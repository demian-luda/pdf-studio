"""Cómo escribir texto nuevo con una fuente que YA está en el PDF.

Para reutilizar el mismo objeto de fuente (máxima fidelidad: mismos glifos, sin
embeber nada nuevo) hace falta saber, para cada carácter unicode, qué código
mandar en el operador Tj/TJ y cuánto avanza. Eso sale de:
- el /ToUnicode de la fuente (invertido), o
- su /Encoding (WinAnsi, MacRoman, Standard + /Differences con nombres de glifo).
Los anchos salen de /Widths (fuentes simples), /W y /DW (Type0) o de las
métricas estándar de las 14 fuentes base.

Si la fuente está embebida como subconjunto, solo se consideran disponibles los
caracteres que efectivamente se usan con esa fuente en el documento: el resto
de los glifos no está en el archivo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf
from fontTools import agl
from fontTools.encodings.StandardEncoding import StandardEncoding

from pdf_studio.core.fuentes import ESTANDAR_14, Embebido, clasificar_fuente, clave_fuente, _normalizar

_HEX = re.compile(rb"<([0-9A-Fa-f]*)>")

# Nombre de las 14 fuentes base de PDF -> nombre interno de PyMuPDF (para métricas)
BASE14_PYMUPDF = {
    "courier": "cour", "courierbold": "cobo", "courieroblique": "coit", "courierboldoblique": "cobi",
    "helvetica": "helv", "helveticabold": "hebo", "helveticaoblique": "heit", "helveticaboldoblique": "hebi",
    "timesroman": "tiro", "timesbold": "tibo", "timesitalic": "tiit", "timesbolditalic": "tibi",
    "symbol": "symb", "zapfdingbats": "zadb",
}


def _hex_a_int(b: bytes) -> int:
    return int(b or b"0", 16)


def _hex_a_texto(b: bytes) -> str:
    """Valor de destino de un ToUnicode (UTF-16BE en hexa)."""
    if len(b) % 4:
        b = b.rjust((len(b) + 3) // 4 * 4, b"0")
    try:
        return bytes.fromhex(b.decode()).decode("utf-16-be", errors="ignore")
    except ValueError:
        return ""


def parsear_tounicode(datos: bytes) -> tuple[dict[int, str], int]:
    """{código: texto} y la cantidad de bytes por código (según codespacerange)."""
    mapa: dict[int, str] = {}
    bytes_codigo = 1
    m = re.search(rb"begincodespacerange(.*?)endcodespacerange", datos, re.S)
    if m:
        codigos = _HEX.findall(m.group(1))
        if codigos:
            bytes_codigo = max(1, len(codigos[0]) // 2)
    for bloque in re.findall(rb"beginbfchar(.*?)endbfchar", datos, re.S):
        tokens = _HEX.findall(bloque)
        for i in range(0, len(tokens) - 1, 2):
            mapa[_hex_a_int(tokens[i])] = _hex_a_texto(tokens[i + 1])
    for bloque in re.findall(rb"beginbfrange(.*?)endbfrange", datos, re.S):
        # Cada entrada: <ini> <fin> <destino>  o  <ini> <fin> [<d1> <d2> ...]
        for ini, fin, resto in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)",
                                          bloque):
            a, b = _hex_a_int(ini), _hex_a_int(fin)
            if b - a > 0xFFFF:
                continue
            if resto.startswith(b"["):
                for i, destino in enumerate(_HEX.findall(resto)):
                    if a + i <= b:
                        mapa[a + i] = _hex_a_texto(destino)
            else:
                base = _hex_a_texto(resto.strip(b"<>"))
                if not base:
                    continue
                for i in range(b - a + 1):
                    # Se incrementa el último carácter del destino
                    mapa[a + i] = base[:-1] + chr(ord(base[-1]) + i)
    return mapa, bytes_codigo


def _numeros(texto: str) -> list[float]:
    return [float(x) for x in re.findall(r"-?\d*\.?\d+", texto)]


def _resolver(doc: pymupdf.Document, tipo: str, valor: str) -> str:
    if tipo == "xref":
        return doc.xref_object(int(valor.split()[0]), compressed=True)
    return valor


def _anchos_simple(doc: pymupdf.Document, xref: int) -> tuple[dict[int, float], float]:
    t, v = doc.xref_get_key(xref, "FirstChar")
    primero = int(float(v)) if t in ("int", "float") else 0
    t, v = doc.xref_get_key(xref, "Widths")
    anchos = {}
    if t in ("array", "xref"):
        for i, w in enumerate(_numeros(_resolver(doc, t, v))):
            anchos[primero + i] = w
    t, v = doc.xref_get_key(xref, "FontDescriptor/MissingWidth")
    faltante = float(v) if t in ("int", "float") else 0.0
    return anchos, faltante


def _anchos_cid(doc: pymupdf.Document, xref_desc: int) -> tuple[dict[int, float], float]:
    t, v = doc.xref_get_key(xref_desc, "DW")
    defecto = float(v) if t in ("int", "float") else 1000.0
    t, v = doc.xref_get_key(xref_desc, "W")
    anchos: dict[int, float] = {}
    if t in ("array", "xref"):
        texto = _resolver(doc, t, v).strip()[1:-1]
        # Formato: c [w1 w2 ...]   o   c1 c2 w
        tokens = re.findall(r"\[[^\]]*\]|-?\d*\.?\d+", texto)
        i = 0
        while i < len(tokens):
            if i + 1 < len(tokens) and tokens[i + 1].startswith("["):
                c = int(float(tokens[i]))
                for j, w in enumerate(_numeros(tokens[i + 1])):
                    anchos[c + j] = w
                i += 2
            elif i + 2 < len(tokens):
                c1, c2, w = int(float(tokens[i])), int(float(tokens[i + 1])), float(tokens[i + 2])
                for c in range(c1, min(c2, c1 + 0xFFFF) + 1):
                    anchos[c] = w
                i += 3
            else:
                break
    return anchos, defecto


def _mapa_encoding(doc: pymupdf.Document, xref: int) -> dict[int, str]:
    """{código: unicode} según /Encoding de una fuente simple."""
    t, v = doc.xref_get_key(xref, "Encoding")
    base = "StandardEncoding"
    diferencias = ""
    if t == "name":
        base = v.lstrip("/")
    elif t in ("dict", "xref"):
        objeto = _resolver(doc, t, v)
        m = re.search(r"/BaseEncoding\s*/(\w+)", objeto)
        if m:
            base = m.group(1)
        m = re.search(r"/Differences\s*\[(.*?)\]", objeto, re.S)
        if m:
            diferencias = m.group(1)
    mapa: dict[int, str] = {}
    if base == "WinAnsiEncoding":
        for c in range(32, 256):
            try:
                mapa[c] = bytes([c]).decode("cp1252")
            except UnicodeDecodeError:
                pass
    elif base == "MacRomanEncoding":
        for c in range(32, 256):
            mapa[c] = bytes([c]).decode("mac_roman")
    else:
        for c, nombre in enumerate(StandardEncoding):
            if nombre != ".notdef":
                u = agl.toUnicode(nombre)
                if u:
                    mapa[c] = u
    codigo = 0
    for token in re.findall(r"/[^\s/\[\]]+|\d+", diferencias):
        if token.startswith("/"):
            u = agl.toUnicode(token[1:])
            if u:
                mapa[codigo] = u
            codigo += 1
        else:
            codigo = int(token)
    return mapa


@dataclass
class CodificadorFuente:
    xref: int
    nombre: str
    tipo: str
    embebido: Embebido
    bytes_por_codigo: int
    a_codigo: dict[str, int]
    anchos: dict[int, float]          # código -> ancho en milésimas de em
    ancho_defecto: float
    disponibles: set[str] | None = None   # None = cualquier carácter codificable
    escala_anchos: float = 1.0        # Type3: los anchos vienen en espacio de glifo
    _metricas_base14: pymupdf.Font | None = field(default=None, repr=False)

    def faltantes(self, texto: str) -> list[str]:
        """Caracteres del texto que esta fuente no puede dibujar."""
        faltan = []
        for ch in texto:
            if ch in faltan or ch in "\n\r":
                continue
            if ch not in self.a_codigo or (self.disponibles is not None and ch not in self.disponibles
                                           and ch != " "):
                faltan.append(ch)
        return faltan

    def puede(self, texto: str) -> bool:
        return not self.faltantes(texto)

    def codigo(self, ch: str) -> bytes:
        return self.a_codigo[ch].to_bytes(self.bytes_por_codigo, "big")

    def avance(self, ch: str) -> float:
        """Avance del carácter en milésimas del tamaño de letra."""
        codigo = self.a_codigo.get(ch)
        if codigo is not None and codigo in self.anchos:
            return self.anchos[codigo] * self.escala_anchos
        if self._metricas_base14 is not None:
            return self._metricas_base14.glyph_advance(ord(ch)) * 1000
        return self.ancho_defecto * self.escala_anchos


def caracteres_usados(doc: pymupdf.Document, nombre_fuente: str) -> set[str]:
    """Caracteres dibujados con esa fuente en todo el documento."""
    clave = clave_fuente(nombre_fuente)
    usados: set[str] = set()
    for pag in doc:
        for span in pag.get_texttrace():
            if clave_fuente(span["font"]) == clave:
                usados.update(chr(c[0]) for c in span["chars"] if c[0] > 0)
    return usados


def construir_codificador(doc: pymupdf.Document, xref: int,
                          usados: set[str] | None = None) -> CodificadorFuente | None:
    """Arma el codificador de una fuente del PDF. None si no se puede reutilizar."""
    t, subtipo = doc.xref_get_key(xref, "Subtype")
    subtipo = subtipo.lstrip("/")
    t, basefont = doc.xref_get_key(xref, "BaseFont")
    nombre = basefont.lstrip("/") if t == "name" else f"xref{xref}"
    tounicode: dict[int, str] = {}
    bytes_codigo = 1
    t, v = doc.xref_get_key(xref, "ToUnicode")
    if t == "xref":
        try:
            tounicode, bytes_codigo = parsear_tounicode(doc.xref_stream(int(v.split()[0])) or b"")
        except Exception:
            tounicode = {}

    metricas = None
    escala = 1.0
    if subtipo == "Type0":
        t, v = doc.xref_get_key(xref, "Encoding")
        if t != "name" or v not in ("/Identity-H",):
            return None  # CMaps no Identity (o vertical): no se reutiliza
        t, v = doc.xref_get_key(xref, "DescendantFonts")
        desc = None
        if t == "array":
            m = re.search(r"(\d+)\s+0\s+R", v)
            desc = int(m.group(1)) if m else None
        elif t == "xref":
            m = re.search(r"(\d+)\s+0\s+R", doc.xref_object(int(v.split()[0])))
            desc = int(m.group(1)) if m else None
        if desc is None:
            return None
        anchos, defecto = _anchos_cid(doc, desc)
        bytes_codigo = 2
        mapa = tounicode
        t, _ = doc.xref_get_key(desc, "FontDescriptor/FontFile2")
        t2, _ = doc.xref_get_key(desc, "FontDescriptor/FontFile3")
        ext = "ttf" if t == "xref" else ("cff" if t2 == "xref" else "n/a")
    else:
        anchos, defecto = _anchos_simple(doc, xref)
        mapa = dict(_mapa_encoding(doc, xref))
        mapa.update(tounicode)  # ToUnicode manda sobre el encoding
        archivos = [doc.xref_get_key(xref, f"FontDescriptor/{k}")[0] for k in ("FontFile", "FontFile2", "FontFile3")]
        ext = "n/a" if subtipo != "Type3" and "xref" not in archivos else "embebida"
        if subtipo == "Type3":
            t, v = doc.xref_get_key(xref, "FontMatrix")
            numeros = _numeros(v) if t == "array" else []
            escala = numeros[0] * 1000 if numeros else 1.0
        if _normalizar(nombre) in ESTANDAR_14 and not anchos:
            metricas = pymupdf.Font(BASE14_PYMUPDF[_normalizar(nombre)])
    if not mapa:
        return None
    a_codigo: dict[str, int] = {}
    for codigo, texto in sorted(mapa.items()):
        if len(texto) == 1 and texto not in a_codigo:
            a_codigo[texto] = codigo
    embebido = clasificar_fuente(doc, xref, nombre, subtipo, ext)
    disponibles = None
    if embebido.solo_letras_usadas:
        disponibles = usados if usados is not None else caracteres_usados(doc, nombre)
    return CodificadorFuente(xref, nombre, subtipo, embebido, bytes_codigo, a_codigo, anchos, defecto,
                             disponibles, escala, metricas)
