"""Corrector ortográfico para el OCR: marca como dudosas las palabras que no existen.

Tesseract a veces lee mal una palabra con confianza alta («Fima» por «Firma», «por-» por
«por:»). Toda palabra que no está en el diccionario (español de la Argentina e inglés) y no
es un número, un código o un nombre propio se marca para revisar.

Usa spylls (Hunspell en Python puro, MPL-2.0) con los diccionarios de
`recursos/diccionarios` (RLA-ES y SCOWL, ver LEEME.txt).
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache
from pathlib import Path

from pdf_studio.core.reconstruccion import Bloque, Linea, Reconstruccion, Tipo

CARPETA = Path(__file__).resolve().parent.parent / "recursos" / "diccionarios"
DICCIONARIOS = ("es_AR", "en_US")
_ABREN = "¿¡([{\"'«“‘"
_CIERRAN = ".,;:!?)]}\"'»”’…"
_BASURA = set("»«|~^¬©®¨`´¦§¤")
_LETRAS_ES = "abcdefghijklmnñopqrstuvwxyzáéíóúü"


class SinCorrector(Exception):
    """No se pudo cargar el corrector (falta spylls o los diccionarios)."""


@lru_cache(maxsize=1)
def _diccionarios() -> tuple:
    try:
        from spylls.hunspell import Dictionary
    except ImportError as e:
        raise SinCorrector("Falta el paquete spylls (corrector ortográfico).") from e
    cargados = []
    for nombre in DICCIONARIOS:
        base = CARPETA / nombre
        if not base.with_suffix(".dic").exists() or not base.with_suffix(".aff").exists():
            raise SinCorrector(f"Falta el diccionario {nombre} en {CARPETA}.")
        try:
            cargados.append(Dictionary.from_files(str(base)))
        except Exception as e:
            raise SinCorrector(f"No se pudo leer el diccionario {nombre} ({type(e).__name__}).") from e
    return tuple(cargados)


@lru_cache(maxsize=20000)
def existe(palabra: str) -> bool:
    return any(d.lookup(palabra) for d in _diccionarios())


@lru_cache(maxsize=50000)
def _existe_en_espanol(palabra: str) -> bool:
    return _diccionarios()[0].lookup(palabra)


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if not unicodedata.combining(c))


def _hay_parecida(palabra: str) -> bool:
    """Hay una palabra del diccionario de español a una letra de distancia (sin contar las
    tildes, que en los nombres propios suelen faltar: «Gutierrez»)."""
    p = palabra.lower()
    base = _sin_tildes(p)
    candidatas = set()
    for i in range(len(p) + 1):
        izq, der = p[:i], p[i:]
        if der:
            candidatas.add(izq + der[1:])
        if len(der) > 1:
            candidatas.add(izq + der[1] + der[0] + der[2:])
        for letra in _LETRAS_ES:
            if der:
                candidatas.add(izq + letra + der[1:])
            candidatas.add(izq + letra + der)
    candidatas.discard(p)
    return any(len(c) >= 3 and _sin_tildes(c) != base and _existe_en_espanol(c) for c in candidatas)


def _limpia(texto: str) -> str:
    return texto.lstrip(_ABREN).rstrip(_CIERRAN)


def sospechosa(texto: str, corte_de_linea: str | None = None) -> bool:
    """¿La palabra (tal como la leyó el OCR) parece mal leída?

    `corte_de_linea`: si la palabra termina con guion al final de un renglón, la que sigue en
    el renglón de abajo (para reconocer una palabra partida)."""
    t = _limpia(texto.strip())
    if not t or not any(c.isalpha() for c in t):
        return False
    if any(c.isdigit() for c in t):
        return False                                   # número, fecha, código
    if t.endswith("-") and len(t) > 1:
        if corte_de_linea is not None:
            unida = t[:-1] + _limpia(corte_de_linea)
            if existe(unida) or existe(unida.lower()):
                return False                           # una palabra partida al final del renglón
        return True                                    # «por-»: el OCR leyó mal los dos puntos
    if any(c in _BASURA for c in t):
        return True                                    # «Vicro»io»: restos de una mala lectura
    if not all(c.isalpha() or c in "-'’" for c in t):
        return False                                   # «N°», «N/A», «e-mail@...»: códigos
    partes = [x for x in t.replace("’", "'").split("-") if x]
    if not partes:
        return False
    for parte in partes:
        if parte.isupper() and len(parte) <= 5:
            continue                                   # sigla: POE, SOP, ISO
        if existe(parte) or existe(parte.lower()):
            continue
        if parte[0].isupper():
            # Un nombre propio no está en el diccionario; solo es sospechoso si se parece mucho
            # a una palabra que sí está («Fima» → «Firma»)
            if _hay_parecida(parte):
                return True
            continue
        return True
    return False


def marcar_sospechosas(rec: Reconstruccion) -> int:
    """Marca las palabras sospechosas de todo el documento; devuelve cuántas."""
    _diccionarios()                                    # que falle acá, antes de recorrer
    cuenta = 0

    def lineas_de(b: Bloque) -> list[list[Linea]]:
        if b.tipo == Tipo.TABLA:
            return [ls for c in b.celdas for x in c.bloques for ls in lineas_de(x)]
        return [b.lineas] if b.lineas else []

    for pagina in rec.paginas:
        for b in pagina.encabezado + pagina.bloques + pagina.pie:
            for lineas in lineas_de(b):
                for n, linea in enumerate(lineas):
                    for i, p in enumerate(linea.palabras):
                        siguiente = None
                        if i == len(linea.palabras) - 1 and n + 1 < len(lineas) and lineas[n + 1].palabras:
                            siguiente = lineas[n + 1].palabras[0].texto
                        p.sospechosa = sospechosa(p.texto, siguiente)
                        cuenta += p.sospechosa
    return cuenta

