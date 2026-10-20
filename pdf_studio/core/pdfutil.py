"""Utilidades de bajo nivel sobre objetos PDF.

independizar_recursos(): muchos PDFs comparten un mismo diccionario /Resources
entre varias páginas. Si al editar una página se agrega algo a ese diccionario
(una imagen, una fuente), se estarían modificando también las demás páginas:
eso viola el principio "se toca solo lo que el usuario editó" y además puede
cambiar sutilmente su render (por ejemplo, MuPDF compone distinto una página
cuyos recursos incluyen una imagen con transparencia). Por eso, antes de editar,
la página recibe su propia copia de los diccionarios compartidos.
"""
from __future__ import annotations

import re
from collections import Counter

import pymupdf

# Subdiccionarios de /Resources donde las ediciones agregan entradas
SUBDICCIONARIOS = ("XObject", "Font", "ExtGState", "ColorSpace", "Pattern", "Shading", "Properties")


def _ref(doc: pymupdf.Document, xref: int, clave: str) -> int | None:
    tipo, valor = doc.xref_get_key(xref, clave)
    return int(valor.split()[0]) if tipo == "xref" else None


def _recursos_heredados(doc: pymupdf.Document, xref_pagina: int) -> str | None:
    """Texto del diccionario /Resources heredado del árbol de páginas, si lo hay."""
    actual = xref_pagina
    for _ in range(64):  # protección contra árboles circulares
        padre = _ref(doc, actual, "Parent")
        if padre is None:
            return None
        tipo, valor = doc.xref_get_key(padre, "Resources")
        if tipo == "xref":
            return doc.xref_object(int(valor.split()[0]), compressed=True)
        if tipo == "dict":
            return valor
        actual = padre
    return None


def _contenedor_recursos(doc: pymupdf.Document, xref_pagina: int) -> tuple[int, str] | None:
    """(xref que contiene el diccionario, prefijo de clave) para leer subdiccionarios."""
    tipo, valor = doc.xref_get_key(xref_pagina, "Resources")
    if tipo == "xref":
        return int(valor.split()[0]), ""
    if tipo == "dict":
        return xref_pagina, "Resources/"
    return None


# ---------------------------------------------------------------------- objetos PDF como texto
class Ref(int):
    """Referencia indirecta 'N 0 R'."""


class Nombre(str):
    """Nombre PDF, sin la barra inicial."""


_TOKEN = re.compile(rb"""
    (?P<ref>\d+\s+\d+\s+R)
  | (?P<dict_ini><<) | (?P<dict_fin>>>)
  | (?P<arr_ini>\[) | (?P<arr_fin>\])
  | (?P<nombre>/[^\s/<>\[\]()%{}]*)
  | (?P<hex><[0-9A-Fa-f\s]*>)
  | (?P<cadena>\((?:\\.|[^\\()])*\))
  | (?P<otro>[^\s/<>\[\]()%{}]+)
""", re.X | re.S)


def parsear_objeto(texto: str):
    """Convierte el texto de un objeto PDF (diccionarios, arreglos, nombres, refs) en
    estructuras de Python. Las cadenas y números quedan como texto crudo."""
    tokens = [m for m in _TOKEN.finditer(texto.encode("latin-1"))]
    pos = 0

    def valor():
        nonlocal pos
        m = tokens[pos]
        pos += 1
        tipo = m.lastgroup
        crudo = m.group(0).decode("latin-1")
        if tipo == "ref":
            return Ref(int(crudo.split()[0]))
        if tipo == "nombre":
            return Nombre(crudo[1:])
        if tipo == "dict_ini":
            d = {}
            while tokens[pos].lastgroup != "dict_fin":
                clave = valor()
                d[str(clave)] = valor()
            pos += 1
            return d
        if tipo == "arr_ini":
            lista = []
            while tokens[pos].lastgroup != "arr_fin":
                lista.append(valor())
            pos += 1
            return lista
        return crudo

    return valor()


def serializar_objeto(v) -> str:
    if isinstance(v, Ref):
        return f"{int(v)} 0 R"
    if isinstance(v, Nombre):
        return "/" + v
    if isinstance(v, dict):
        return "<<" + "".join(f"/{k} {serializar_objeto(x)}" for k, x in v.items()) + ">>"
    if isinstance(v, list):
        return "[" + " ".join(serializar_objeto(x) for x in v) + "]"
    return str(v)


def traducir_refs(v, mapa: dict[int, int]):
    """Cambia cada referencia según `mapa`. Lanza KeyError si aparece una desconocida."""
    if isinstance(v, Ref):
        return Ref(mapa[int(v)])
    if isinstance(v, dict):
        return {k: traducir_refs(x, mapa) for k, x in v.items()}
    if isinstance(v, list):
        return [traducir_refs(x, mapa) for x in v]
    return v


def _copiar_objeto(doc: pymupdf.Document, xref: int) -> int:
    nuevo = doc.get_new_xref()
    doc.update_object(nuevo, doc.xref_object(xref, compressed=True))
    return nuevo


def independizar_recursos(doc: pymupdf.Document, pagina: int) -> int:
    """Le da a la página copias propias de los recursos que comparte con otras páginas.
    Devuelve cuántos objetos se copiaron (0 si ya eran propios)."""
    xref_pagina = doc.page_xref(pagina)
    copiados = 0

    tipo, _ = doc.xref_get_key(xref_pagina, "Resources")
    if tipo == "null":
        heredado = _recursos_heredados(doc, xref_pagina)
        if heredado is None:
            return 0
        nuevo = doc.get_new_xref()
        doc.update_object(nuevo, heredado)
        doc.xref_set_key(xref_pagina, "Resources", f"{nuevo} 0 R")
        copiados += 1

    # Cuántas páginas usan cada diccionario de recursos y cada subdiccionario
    usos_recursos: Counter[int] = Counter()
    usos_sub: Counter[int] = Counter()
    for i in range(doc.page_count):
        x = doc.page_xref(i)
        r = _ref(doc, x, "Resources")
        if r is not None:
            usos_recursos[r] += 1
            continue
        # /Resources directo en la página: sus subdiccionarios pueden ser indirectos
        for clave in SUBDICCIONARIOS:
            s = _ref(doc, x, "Resources/" + clave)
            if s is not None:
                usos_sub[s] += 1
    # Un diccionario de recursos compartido aporta sus subdiccionarios a varias páginas
    for r, n in usos_recursos.items():
        for clave in SUBDICCIONARIOS:
            s = _ref(doc, r, clave)
            if s is not None:
                usos_sub[s] += n

    r = _ref(doc, xref_pagina, "Resources")
    if r is not None and usos_recursos[r] > 1:
        nuevo = _copiar_objeto(doc, r)
        doc.xref_set_key(xref_pagina, "Resources", f"{nuevo} 0 R")
        copiados += 1

    cont = _contenedor_recursos(doc, xref_pagina)
    if cont is not None:
        for clave in SUBDICCIONARIOS:
            s = _ref(doc, cont[0], cont[1] + clave)
            if s is not None and usos_sub[s] > 1:
                nuevo = _copiar_objeto(doc, s)
                doc.xref_set_key(cont[0], cont[1] + clave, f"{nuevo} 0 R")
                copiados += 1
    return copiados
