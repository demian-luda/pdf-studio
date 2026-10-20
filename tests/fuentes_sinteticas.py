"""PDFs sintéticos con fuentes "difíciles" (para probar que el análisis nunca se rompa).

Cada PDF tiene una línea con la fuente especial y otra en Helvetica:
- cff_type1c.pdf        Type1 con programa CFF "pelado" (FontFile3 /Type1C)
- type1.pdf             Type1 con programa PostScript (FontFile, cifrado eexec)
- type3.pdf             Type3: glifos dibujados con operadores del PDF
- truetype_sin_cmap.pdf TrueType CID (Identity-H) cuyo programa no tiene tabla cmap
- truetype_simbolica.pdf TrueType simple simbólica: solo cmap (3,0) en U+F0xx, sin /Encoding
- cid_cidtogid.pdf      CIDFontType2 con /CIDToGIDMap en stream y sin /ToUnicode

Las fuentes salen de Liberation Sans (OFL) o se construyen acá; los textos son ficticios.
"""
from __future__ import annotations

import io
import random
from pathlib import Path

import pymupdf
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

RAIZ = Path(__file__).resolve().parent
FUENTE_BASE = RAIZ.parent / "pdf_studio" / "recursos" / "fuentes" / "LiberationSans-Regular.ttf"
DESTINO = RAIZ / "pdfs_prueba" / "fuentes"


# ---------------------------------------------------------------------- utilidades PDF
def _objeto(doc: pymupdf.Document, texto: str) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, texto)
    return xref


def _stream(doc: pymupdf.Document, datos: bytes, dic: str = "<<>>", comprimir: bool = True) -> int:
    xref = _objeto(doc, dic)
    doc.update_stream(xref, datos, compress=comprimir)
    return xref


def _guardar(doc: pymupdf.Document, pag: pymupdf.Page, xref_fuente: int, contenido: bytes, salida: Path) -> None:
    t, v = doc.xref_get_key(pag.xref, "Resources")
    contenedor, prefijo = (int(v.split()[0]), "") if t == "xref" else (pag.xref, "Resources/")
    doc.xref_set_key(contenedor, f"{prefijo}Font/FX", f"{xref_fuente} 0 R")
    nuevo = _stream(doc, b"q\n" + contenido + b"\nQ\n")
    actuales = pag.get_contents()
    doc.xref_set_key(pag.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in actuales + [nuevo]) + "]")
    salida.parent.mkdir(parents=True, exist_ok=True)
    doc.save(salida, garbage=3, deflate=True)


def _documento() -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open()
    pag = doc.new_page(width=420, height=200)
    pag.insert_text((40, 160), "Linea de referencia en Helvetica", fontname="helv", fontsize=11)
    return doc, pag


def _widths(anchos: dict[int, int]) -> tuple[int, int, str]:
    primero, ultimo = min(anchos), max(anchos)
    return primero, ultimo, "[" + " ".join(str(anchos.get(c, 0)) for c in range(primero, ultimo + 1)) + "]"


def _cadena(texto: str) -> bytes:
    return b"(" + texto.encode("latin-1").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


# ---------------------------------------------------------------------- CFF (Type1C)
def _cff_desde_truetype(texto: str, nombre: str) -> tuple[bytes, dict[str, int], int]:
    """CFF pelado con los glifos de `texto` (dibujados desde Liberation Sans)."""
    fuente = TTFont(str(FUENTE_BASE))
    cmap = fuente.getBestCmap()
    glifos = fuente.getGlyphSet()
    nombres = [".notdef"] + sorted({cmap[ord(c)] for c in texto})
    charstrings = {}
    for n in nombres:
        pluma = T2CharStringPen(glifos[n].width, glifos)
        glifos[n].draw(pluma)
        charstrings[n] = pluma.getCharString()
    upem = fuente["head"].unitsPerEm
    fb = FontBuilder(upem, isTTF=False)
    fb.setupGlyphOrder(nombres)
    fb.setupCharacterMap({ord(c): cmap[ord(c)] for c in set(texto)})
    fb.setupCFF(nombre, {"FullName": nombre, "FamilyName": nombre}, charstrings, {})
    fb.setupHorizontalMetrics({n: (glifos[n].width, 0) for n in nombres})
    fb.setupHorizontalHeader(ascent=1854, descent=-434)
    fb.setupNameTable({"familyName": nombre, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    buf = io.BytesIO()
    fb.font["CFF "].cff.compile(buf, fb.font)
    anchos = {c: round(glifos[cmap[ord(c)]].width * 1000 / upem) for c in set(texto)}
    return buf.getvalue(), anchos, upem


def cff_type1c(salida: Path) -> None:
    texto = "Texto en CFF pelado"
    datos, anchos, _ = _cff_desde_truetype(texto, "PruebaCFF")
    doc, pag = _documento()
    archivo = _stream(doc, datos, "<</Subtype/Type1C>>")
    desc = _objeto(doc, f"<</Type/FontDescriptor/FontName/PruebaCFF/Flags 32/FontBBox[-200 -250 1100 950]"
                        f"/ItalicAngle 0/Ascent 905/Descent -212/CapHeight 716/StemV 80/FontFile3 {archivo} 0 R>>")
    primero, ultimo, w = _widths({ord(c): a for c, a in anchos.items()})
    fuente = _objeto(doc, f"<</Type/Font/Subtype/Type1/BaseFont/PruebaCFF/FirstChar {primero}/LastChar {ultimo}"
                          f"/Widths {w}/Encoding/WinAnsiEncoding/FontDescriptor {desc} 0 R>>")
    _guardar(doc, pag, fuente, b"BT /FX 18 Tf 40 80 Td " + _cadena(texto) + b" Tj ET", salida)


# ---------------------------------------------------------------------- Type1 (PostScript)
def _numero_t1(v: int) -> bytes:
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    return bytes([255]) + v.to_bytes(4, "big", signed=True)


def _cifrar(datos: bytes, r: int) -> bytes:
    salida = bytearray()
    for p in datos:
        c = p ^ (r >> 8)
        r = ((c + r) * 52845 + 22719) & 0xFFFF
        salida.append(c)
    return bytes(salida)


def _charstring_t1(ancho: int, rect: tuple[int, int, int, int]) -> bytes:
    x0, y0, x1, y1 = rect
    HSBW, RMOVETO, RLINETO, CLOSEPATH, ENDCHAR = 13, 21, 5, 9, 14
    prog = (_numero_t1(0) + _numero_t1(ancho) + bytes([HSBW])
            + _numero_t1(x0) + _numero_t1(y0) + bytes([RMOVETO])
            + _numero_t1(x1 - x0) + _numero_t1(0) + bytes([RLINETO])
            + _numero_t1(0) + _numero_t1(y1 - y0) + bytes([RLINETO])
            + _numero_t1(x0 - x1) + _numero_t1(0) + bytes([RLINETO])
            + bytes([CLOSEPATH, ENDCHAR]))
    semilla = bytes(random.Random(7).randrange(256) for _ in range(4))
    return _cifrar(semilla + prog, 4330)


def _programa_type1(glifos: dict[str, tuple[int, tuple]]) -> tuple[bytes, bytes]:
    """(parte en claro, parte cifrada con eexec) de una fuente Type1 mínima pero válida."""
    codigos = {n: ord(n) for n in glifos if len(n) == 1}
    encoding = "".join(f"dup {c} /{n} put\n" for n, c in codigos.items())
    claro = (
        "%!PS-AdobeFont-1.0: PruebaT1 001.000\n"
        "11 dict begin\n"
        "/FontInfo 2 dict dup begin /FullName (PruebaT1) readonly def end readonly def\n"
        "/FontName /PruebaT1 def\n"
        "/Encoding 256 array 0 1 255 {1 index exch /.notdef put} for\n" + encoding + "readonly def\n"
        "/PaintType 0 def\n/FontType 1 def\n"
        "/FontMatrix [0.001 0 0 0.001 0 0] readonly def\n"
        "/FontBBox {0 0 700 700} readonly def\n"
        "currentdict end\ncurrentfile eexec\n").encode("latin-1")
    privado = bytearray(
        b"dup /Private 8 dict dup begin\n"
        b"/RD{string currentfile exch readstring pop}executeonly def\n"
        b"/ND{noaccess def}executeonly def\n/NP{noaccess put}executeonly def\n"
        b"/BlueValues [] ND\n/MinFeature {16 16} ND\n/password 5839 def\n"
        b"/Subrs 0 array ND\n"
        + f"2 index /CharStrings {len(glifos) + 1} dict dup begin\n".encode())
    for nombre, (ancho, rect) in [(".notdef", (500, (0, 0, 0, 0)))] + list(glifos.items()):
        cs = _charstring_t1(ancho, rect) if nombre != ".notdef" else _cifrar(
            bytes(4) + _numero_t1(0) + _numero_t1(500) + bytes([13, 14]), 4330)
        privado += f"/{nombre} {len(cs)} RD ".encode() + cs + b" ND\n"
    privado += b"end\nend\nreadonly put\nnoaccess put\ndup /FontName get exch definefont pop\n" \
               b"mark currentfile closefile\n"
    cifrado = _cifrar(bytes(random.Random(3).randrange(256) for _ in range(4)) + bytes(privado), 55665)
    return claro, cifrado


def type1(salida: Path) -> None:
    glifos = {"A": (600, (50, 0, 550, 700)), "B": (600, (50, 0, 550, 500)), "C": (400, (50, 0, 350, 600))}
    claro, cifrado = _programa_type1(glifos)
    final = b"0" * 512 + b"\ncleartomark\n"
    doc, pag = _documento()
    archivo = _stream(doc, claro + cifrado + final,
                      f"<</Length1 {len(claro)}/Length2 {len(cifrado)}/Length3 {len(final)}>>")
    desc = _objeto(doc, f"<</Type/FontDescriptor/FontName/PruebaT1/Flags 32/FontBBox[0 0 700 700]/ItalicAngle 0"
                        f"/Ascent 700/Descent 0/CapHeight 700/StemV 80/FontFile {archivo} 0 R>>")
    fuente = _objeto(doc, f"<</Type/Font/Subtype/Type1/BaseFont/PruebaT1/FirstChar 65/LastChar 67"
                          f"/Widths[600 600 400]/FontDescriptor {desc} 0 R>>")
    _guardar(doc, pag, fuente, b"BT /FX 24 Tf 40 80 Td (ABCCBA) Tj ET", salida)


# ---------------------------------------------------------------------- Type3
def type3(salida: Path) -> None:
    doc, pag = _documento()
    cuadrado = _stream(doc, b"750 0 0 0 700 700 d1\n0 0 700 700 re f")
    barra = _stream(doc, b"750 0 0 0 700 700 d1\n0 0 300 700 re f")
    fuente = _objeto(doc, f"<</Type/Font/Subtype/Type3/FontBBox[0 0 750 750]/FontMatrix[0.001 0 0 0.001 0 0]"
                          f"/CharProcs<</a {cuadrado} 0 R/b {barra} 0 R>>"
                          f"/Encoding<</Type/Encoding/Differences[97/a/b]>>/FirstChar 97/LastChar 98"
                          f"/Widths[750 750]/Resources<<>>>>")
    _guardar(doc, pag, fuente, b"BT /FX 20 Tf 40 80 Td (abbaab) Tj ET", salida)


# ---------------------------------------------------------------------- TrueType sin cmap
def truetype_sin_cmap(salida: Path) -> None:
    """Como las fuentes de muchos sistemas de facturación: subconjunto CID sin tabla cmap."""
    from pdf_studio.core.texto import subconjunto
    texto = "Texto TrueType sin cmap 123"
    datos = subconjunto(FUENTE_BASE.read_bytes(), texto)
    doc = pymupdf.open()
    pag = doc.new_page(width=420, height=200)
    pag.insert_text((40, 160), "Linea de referencia en Helvetica", fontname="helv", fontsize=11)
    xref = pag.insert_font(fontname="SinCmap", fontbuffer=datos)
    pag.insert_text((40, 80), texto, fontname="SinCmap", fontsize=16)
    f = TTFont(io.BytesIO(datos))
    del f["cmap"]
    buf = io.BytesIO()
    f.save(buf)
    desc = int(doc.xref_get_key(xref, "DescendantFonts")[1].strip("[]").split()[0])
    descriptor = int(doc.xref_get_key(desc, "FontDescriptor")[1].split()[0])
    archivo = int(doc.xref_get_key(descriptor, "FontFile2")[1].split()[0])
    doc.update_stream(archivo, buf.getvalue())
    for x in (xref, desc):
        doc.xref_set_key(x, "BaseFont", "/FacturaSans-Regular")      # sin prefijo de subconjunto
    doc.xref_set_key(descriptor, "FontName", "/FacturaSans-Regular")
    salida.parent.mkdir(parents=True, exist_ok=True)
    doc.save(salida, garbage=3, deflate=True)


# ---------------------------------------------------------------------- TrueType simbólica
def truetype_simbolica(salida: Path) -> None:
    from pdf_studio.core.texto import subconjunto
    texto = "Simbolica 42"
    f = TTFont(io.BytesIO(subconjunto(FUENTE_BASE.read_bytes(), texto)))
    cmap = f.getBestCmap()
    tabla = CmapSubtable.newSubtable(4)
    tabla.platformID, tabla.platEncID, tabla.language = 3, 0, 0
    tabla.cmap = {0xF000 + ord(c): cmap[ord(c)] for c in set(texto)}
    f["cmap"].tables = [tabla]
    buf = io.BytesIO()
    f.save(buf)
    upem = f["head"].unitsPerEm
    anchos = {ord(c): round(f["hmtx"][cmap[ord(c)]][0] * 1000 / upem) for c in set(texto)}
    doc, pag = _documento()
    archivo = _stream(doc, buf.getvalue(), f"<</Length1 {len(buf.getvalue())}>>")
    desc = _objeto(doc, f"<</Type/FontDescriptor/FontName/PruebaSimbolica/Flags 4/FontBBox[-200 -250 1100 950]"
                        f"/ItalicAngle 0/Ascent 905/Descent -212/CapHeight 716/StemV 80/FontFile2 {archivo} 0 R>>")
    primero, ultimo, w = _widths(anchos)
    fuente = _objeto(doc, f"<</Type/Font/Subtype/TrueType/BaseFont/PruebaSimbolica/FirstChar {primero}"
                          f"/LastChar {ultimo}/Widths {w}/FontDescriptor {desc} 0 R>>")
    _guardar(doc, pag, fuente, b"BT /FX 18 Tf 40 80 Td " + _cadena(texto) + b" Tj ET", salida)


# ---------------------------------------------------------------------- CID con CIDToGIDMap
def cid_cidtogid(salida: Path) -> None:
    from pdf_studio.core.texto import subconjunto
    texto = "CID con mapa propio"
    f = TTFont(io.BytesIO(subconjunto(FUENTE_BASE.read_bytes(), texto)))
    cmap = f.getBestCmap()
    orden = f.getGlyphOrder()
    upem = f["head"].unitsPerEm
    # CIDs propios (100, 101, …) que el /CIDToGIDMap lleva a los glifos reales
    letras = sorted(set(texto))
    cid_de = {c: 100 + i for i, c in enumerate(letras)}
    tabla = bytearray(2 * (max(cid_de.values()) + 1))
    for c, cid in cid_de.items():
        gid = orden.index(cmap[ord(c)])
        tabla[2 * cid], tabla[2 * cid + 1] = gid >> 8, gid & 0xFF
    buf = io.BytesIO()
    f.save(buf)
    doc, pag = _documento()
    archivo = _stream(doc, buf.getvalue(), f"<</Length1 {len(buf.getvalue())}>>")
    mapa = _stream(doc, bytes(tabla))
    desc = _objeto(doc, f"<</Type/FontDescriptor/FontName/PruebaCID/Flags 32/FontBBox[-200 -250 1100 950]"
                        f"/ItalicAngle 0/Ascent 905/Descent -212/CapHeight 716/StemV 80/FontFile2 {archivo} 0 R>>")
    w = " ".join(f"{cid} [{round(f['hmtx'][cmap[ord(c)]][0] * 1000 / upem)}]" for c, cid in cid_de.items())
    hija = _objeto(doc, f"<</Type/Font/Subtype/CIDFontType2/BaseFont/PruebaCID"
                        f"/CIDSystemInfo<</Registry(Adobe)/Ordering(Identity)/Supplement 0>>"
                        f"/FontDescriptor {desc} 0 R/W[{w}]/CIDToGIDMap {mapa} 0 R>>")
    fuente = _objeto(doc, f"<</Type/Font/Subtype/Type0/BaseFont/PruebaCID/Encoding/Identity-H"
                          f"/DescendantFonts[{hija} 0 R]>>")
    codigos = b"".join(cid_de[c].to_bytes(2, "big") for c in texto)
    _guardar(doc, pag, fuente, b"BT /FX 18 Tf 40 80 Td <" + codigos.hex().encode() + b"> Tj ET", salida)


GENERADORES = {
    "cff_type1c.pdf": cff_type1c, "type1.pdf": type1, "type3.pdf": type3,
    "truetype_sin_cmap.pdf": truetype_sin_cmap, "truetype_simbolica.pdf": truetype_simbolica,
    "cid_cidtogid.pdf": cid_cidtogid,
}


def generar(destino: Path = DESTINO) -> list[Path]:
    rutas = []
    for nombre, funcion in GENERADORES.items():
        ruta = destino / nombre
        funcion(ruta)
        rutas.append(ruta)
    return rutas


if __name__ == "__main__":
    for r in generar():
        print("Generado:", r.name)
