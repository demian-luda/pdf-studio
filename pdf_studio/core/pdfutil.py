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


def valor_de_clave(doc: pymupdf.Document, xref: int, clave: str):
    """Valor de una clave ya parseado (Ref si es una referencia); None si no está."""
    tipo, valor = doc.xref_get_key(xref, clave)
    if tipo == "null":
        return None
    if tipo == "xref":
        return Ref(int(valor.split()[0]))
    return parsear_objeto(valor)


def _objeto(doc: pymupdf.Document, xref: int):
    try:
        return parsear_objeto(doc.xref_object(xref, compressed=True))
    except Exception:
        return None


def emparejar_objetos(doc_a: pymupdf.Document, a, doc_b: pymupdf.Document, b, mapa: dict[int, int]) -> None:
    """Recorre en paralelo dos estructuras iguales (una página copiada a otro documento y la
    original) y anota en `mapa` qué objeto de doc_a corresponde a cuál de doc_b."""
    if isinstance(a, Ref) and isinstance(b, Ref):
        if int(a) in mapa:
            return
        mapa[int(a)] = int(b)
        a, b = _objeto(doc_a, int(a)), _objeto(doc_b, int(b))
    # Un lado directo y el otro indirecto (p. ej. una copia con los recursos en línea)
    elif isinstance(a, Ref) and isinstance(b, (dict, list)):
        a = _objeto(doc_a, int(a))
    elif isinstance(b, Ref) and isinstance(a, (dict, list)):
        b = _objeto(doc_b, int(b))
    if isinstance(a, dict) and isinstance(b, dict):
        for clave, va in a.items():
            if clave != "Parent" and clave in b:
                emparejar_objetos(doc_a, va, doc_b, b[clave], mapa)
    elif isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for va, vb in zip(a, b):
            emparejar_objetos(doc_a, va, doc_b, vb, mapa)


def trasplantar(origen: pymupdf.Document, v, destino: pymupdf.Document, mapa: dict[int, int]):
    """Traduce las referencias de `v` según `mapa`; los objetos de `origen` que no están en
    el mapa (nuevos) se copian a `destino` como objetos nuevos, con su contenido. Solo se
    copian formularios y diccionarios: si apareciera una imagen nueva, KeyError (no se
    re-codifica una imagen)."""
    if isinstance(v, Ref):
        x = int(v)
        if x in mapa:
            return Ref(mapa[x])
        obj = _objeto(origen, x)
        if obj is None or (isinstance(obj, dict) and obj.get("Subtype") == "Image"):
            raise KeyError(x)
        nuevo = destino.get_new_xref()
        mapa[x] = nuevo
        if isinstance(obj, dict):
            obj = {k: val for k, val in obj.items() if k not in ("Length", "Filter", "DecodeParms")}
        destino.update_object(nuevo, serializar_objeto(trasplantar(origen, obj, destino, mapa)))
        if origen.xref_is_stream(x):
            destino.update_stream(nuevo, origen.xref_stream(x) or b"")
        return Ref(nuevo)
    if isinstance(v, dict):
        return {k: trasplantar(origen, x, destino, mapa) for k, x in v.items()}
    if isinstance(v, list):
        return [trasplantar(origen, x, destino, mapa) for x in v]
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
