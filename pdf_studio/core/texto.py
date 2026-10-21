"""Edición y movimiento de texto.

Técnica (principio 2: tocar solo lo editado):
1. Se borra SOLO el texto original con una redacción que no toca imágenes ni
   gráficos (images=NONE, graphics=NONE) y usa una franja interior de cada línea
   para no rozar las líneas vecinas.
2. Se verifica que ninguna otra palabra de la página haya desaparecido.
3. Se reescribe el texto en la misma línea base, tamaño, color y modo de render:
   - si la fuente del PDF tiene todos los glifos, se REUTILIZA el mismo objeto de
     fuente (mismos glifos, nada nuevo embebido), con las posiciones exactas;
   - si no, se usa la fuente completa instalada, la incluida con la app o una
     sustituta (en ese orden), embebiendo solo los glifos necesarios.
4. Se verifica que el texto nuevo se pueda extraer tal cual.
Además, la operación declara las zonas que tocó: la verificación de fidelidad
compara el resto de la página píxel por píxel.
"""
from __future__ import annotations

import io
import statistics
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pymupdf

from pdf_studio.core.codificacion import CodificadorFuente, construir_codificador
from pdf_studio.core.documento import ErrorDocumento, Limitacion, Operacion
from pdf_studio.core.fuentes import (Embebido, IndiceFuentesSistema, clase_tipografica, clave_fuente, estilo_de,
                                     familia_de, separar_nombre, sin_prefijo)
from pdf_studio.dependencias.catalogo_fuentes import FuenteLibre
from pdf_studio.core.pdfutil import (Ref, emparejar_objetos, independizar_recursos, parsear_objeto,
                                     serializar_objeto, trasplantar, valor_de_clave)

FLAGS_EXTRACCION = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_LIGATURES

AVISO_INVISIBLE = ("Este texto es la capa de reconocimiento invisible; lo que ves es la imagen. "
                   "Editarlo no cambia el aspecto de la página.")

# Bits de "flags" de los spans de MuPDF
F_ITALICA, F_SERIF, F_MONO, F_NEGRITA = 2, 4, 8, 16


class Nivel:
    FRAGMENTO = "fragmento"   # un tramo del mismo estilo dentro de una línea
    LINEA = "linea"
    PARRAFO = "parrafo"


# ---------------------------------------------------------------------- modelo
@dataclass
class Glifo:
    c: str
    x: float          # origen (línea base), coordenadas sin rotar
    y: float
    bbox: tuple[float, float, float, float]


@dataclass
class Tramo:
    """Secuencia de caracteres con el mismo estilo dentro de una línea."""
    fuente: str
    tamano: float
    color: tuple[float, float, float]
    flags: int
    modo: int = 0             # modo de render: 0 relleno, 1 trazo, 2 ambos, 3 invisible
    opacidad: float = 1.0
    ascender: float = 0.9
    descender: float = -0.2
    glifos: list[Glifo] = field(default_factory=list)

    @property
    def texto(self) -> str:
        return "".join(g.c for g in self.glifos)

    @property
    def bbox(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.glifos[0].bbox)
        for g in self.glifos[1:]:
            r |= g.bbox
        return r

    def mismo_estilo(self, otro: "Tramo") -> bool:
        return (clave_fuente(self.fuente) == clave_fuente(otro.fuente) and abs(self.tamano - otro.tamano) < 0.01
                and self.color == otro.color and self.modo == otro.modo and self.flags == otro.flags)


@dataclass
class Linea:
    tramos: list[Tramo]

    @property
    def texto(self) -> str:
        return "".join(t.texto for t in self.tramos)

    @property
    def glifos(self) -> list[Glifo]:
        return [g for t in self.tramos for g in t.glifos]

    @property
    def bbox(self) -> pymupdf.Rect:
        r = self.tramos[0].bbox
        for t in self.tramos[1:]:
            r |= t.bbox
        return r

    @property
    def base_y(self) -> float:
        return self.tramos[0].glifos[0].y


@dataclass
class BloqueTexto:
    pagina: int
    nivel: str
    lineas: list[Linea]            # para FRAGMENTO, una línea con un solo tramo
    lineas_del_parrafo: list[Linea] = field(default_factory=list)  # contexto (alineación)

    @property
    def texto(self) -> str:
        if self.nivel == Nivel.PARRAFO:
            partes = [ln.texto.strip() for ln in self.lineas]
            texto = ""
            for p in partes:
                if texto.endswith("-") and p[:1].islower():
                    texto = texto[:-1] + p        # palabra cortada con guion al final de línea
                elif texto:
                    texto += " " + p
                else:
                    texto = p
            return texto
        return self.lineas[0].texto

    @property
    def bbox(self) -> pymupdf.Rect:
        r = self.lineas[0].bbox
        for ln in self.lineas[1:]:
            r |= ln.bbox
        return r

    @property
    def estilo(self) -> Tramo:
        return self.lineas[0].tramos[0]

    @property
    def es_horizontal(self) -> bool:
        return all(abs(g.y - ln.base_y) < 0.01 for ln in self.lineas for g in ln.glifos)

    @property
    def invisible(self) -> bool:
        """Todo el bloque es texto invisible (modo de render 3, p. ej. la capa de un OCR)."""
        return all(t.modo == 3 for ln in self.lineas for t in ln.tramos)

    @property
    def varios_estilos(self) -> bool:
        tramos = [t for ln in self.lineas for t in ln.tramos]
        return any(not tramos[0].mismo_estilo(t) for t in tramos[1:])


# ---------------------------------------------------------------------- extracción
def _rgb(entero: int) -> tuple[float, float, float]:
    return tuple(round(v, 4) for v in pymupdf.sRGB_to_pdf(entero))


def lineas_de_pagina(pag: pymupdf.Page) -> list[list[Linea]]:
    """Bloques de líneas (solo texto horizontal), con modo de render y opacidad."""
    trazas = {}
    for span in pag.get_texttrace():
        if span["chars"]:
            x, y = span["chars"][0][2]
            trazas[(round(x, 1), round(y, 1))] = span
    bloques = []
    for b in pag.get_text("rawdict", flags=FLAGS_EXTRACCION)["blocks"]:
        if b["type"] != 0:
            continue
        lineas = []
        for ln in b["lines"]:
            if abs(ln["dir"][1]) > 1e-3 or ln["dir"][0] <= 0:
                continue  # texto vertical o invertido: no se edita
            tramos: list[Tramo] = []
            for s in ln["spans"]:
                if not s["chars"]:
                    continue
                gl = [Glifo(c["c"], c["origin"][0], c["origin"][1], tuple(c["bbox"])) for c in s["chars"]]
                traza = trazas.get((round(gl[0].x, 1), round(gl[0].y, 1)), {})
                t = Tramo(s["font"], s["size"], _rgb(s["color"]), s["flags"], traza.get("type", 0),
                          traza.get("opacity", 1.0), s.get("ascender", 0.9), s.get("descender", -0.2), gl)
                if tramos and tramos[-1].mismo_estilo(t) and abs(tramos[-1].glifos[0].y - gl[0].y) < 0.01:
                    tramos[-1].glifos.extend(gl)
                else:
                    tramos.append(t)
            if tramos:
                lineas.append(Linea(tramos))
        if lineas:
            bloques.append(lineas)
    return bloques


def bloque_en(pag: pymupdf.Page, pagina: int, punto: pymupdf.Point, nivel: str) -> BloqueTexto | None:
    """Bloque de texto bajo el punto (coordenadas sin rotar)."""
    for lineas in lineas_de_pagina(pag):
        for ln in lineas:
            caja = ln.bbox
            if not pymupdf.Rect(caja.x0 - 1, caja.y0 - 1, caja.x1 + 1, caja.y1 + 1).contains(punto):
                continue
            if nivel == Nivel.PARRAFO:
                return BloqueTexto(pagina, nivel, lineas, lineas)
            if nivel == Nivel.LINEA:
                return BloqueTexto(pagina, nivel, [ln], lineas)
            # Fragmento: el tramo más cercano al punto
            tramo = min(ln.tramos, key=lambda t: 0 if t.bbox.contains(punto) else
                        min(abs(t.bbox.x0 - punto.x), abs(t.bbox.x1 - punto.x)))
            return BloqueTexto(pagina, nivel, [Linea([tramo])], lineas)
    return None


# ---------------------------------------------------------------------- fuentes
def codificador_de_tramo(doc: pymupdf.Document, pag: pymupdf.Page, tramo: Tramo,
                         cache: dict | None = None) -> CodificadorFuente | None:
    """Encuentra el objeto de fuente del PDF que dibujó el tramo y su codificador."""
    cache = cache if cache is not None else {}
    clave = clave_fuente(tramo.fuente)
    candidatos = [f[0] for f in pag.get_fonts(full=True) if clave_fuente(f[3]) == clave]
    mejor, error_mejor = None, None
    for xref in dict.fromkeys(candidatos):
        if xref not in cache:
            try:
                cache[xref] = construir_codificador(doc, xref)
            except Exception:
                cache[xref] = None
        cod = cache[xref]
        if cod is None or not cod.puede(tramo.texto.replace(" ", "")):
            continue
        # Desempata por los anchos: el avance medido tiene que coincidir con la fuente
        error = 0.0
        g = tramo.glifos
        for a, b in zip(g, g[1:]):
            if a.c != " ":
                error += abs((b.x - a.x) - cod.avance(a.c) / 1000 * tramo.tamano)
        if mejor is None or error < error_mejor:
            mejor, error_mejor = cod, error
    return mejor


@lru_cache(maxsize=32)
def bytes_de_fuente(archivo: str, indice: int = 0) -> bytes:
    """Bytes de una fuente; para colecciones .ttc se extrae la cara pedida."""
    ruta = Path(archivo)
    if ruta.suffix.lower() != ".ttc":
        return ruta.read_bytes()
    from fontTools.ttLib import TTCollection
    col = TTCollection(str(ruta))
    buf = io.BytesIO()
    col.fonts[indice].save(buf)
    return buf.getvalue()


def subconjunto(datos: bytes, texto: str) -> bytes:
    """Recorta la fuente a los caracteres necesarios (para no embeber fuentes enteras)."""
    import logging

    from fontTools import subset
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    opciones = subset.Options()
    opciones.layout_features = ["kern", "liga"]
    opciones.notdef_outline = True
    opciones.name_IDs = ["*"]
    opciones.name_legacy = True
    opciones.name_languages = ["*"]
    fuente = subset.load_font(io.BytesIO(datos), opciones)
    sub = subset.Subsetter(opciones)
    sub.populate(unicodes=sorted({ord(c) for c in texto + " "}))
    sub.subset(fuente)
    buf = io.BytesIO()
    subset.save_font(fuente, buf, opciones)
    return buf.getvalue()


@dataclass
class OpcionFuente:
    tipo: str                       # 'original', 'sistema', 'incluida', 'sustituta'
    nombre: str
    descripcion: str
    faltantes: list[str] = field(default_factory=list)
    codificador: CodificadorFuente | None = None      # para 'original'
    archivo: str | None = None                        # para las demás
    indice: int = 0
    metrica_compatible: bool = False

    @property
    def disponible(self) -> bool:
        return not self.faltantes

    def fuente_pymupdf(self) -> pymupdf.Font:
        return pymupdf.Font(fontbuffer=bytes_de_fuente(self.archivo, self.indice))


@dataclass
class AnalisisFuente:
    nombre: str
    embebido: Embebido | None
    opciones: list[OpcionFuente]          # original, completa instalada/incluida, sustituta (en ese orden)
    instalable: FuenteLibre | None = None   # libre y no instalada: se puede instalar desde la app
    clase: str = "sans"
    estilo: str = ""

    @property
    def recomendada(self) -> OpcionFuente | None:
        return next((o for o in self.opciones if o.disponible), None)

    @property
    def comando_instalacion(self) -> str | None:
        return self.instalable.comando if self.instalable is not None else None

    @property
    def licencia(self) -> str | None:
        return self.instalable.licencia if self.instalable is not None else None


# Sustitutas por clase tipográfica, de la preferida a la menos preferida
SUSTITUTAS_POR_CLASE = {
    "sans": ["Liberation Sans", "DejaVu Sans", "Carlito", "Arial"],
    "serif": ["Liberation Serif", "DejaVu Serif", "Caladea", "Times New Roman"],
    "mono": ["Liberation Mono", "DejaVu Sans Mono", "Courier New"],
}


def _cara_con_estilo(caras, estilo: str):
    """La cara de la familia con exactamente ese estilo, o None (no se cambia peso ni estilo)."""
    for c in caras:
        if separar_nombre(f"{c.familia},{c.estilo}")[1] == estilo:
            return c
    return None


def analizar_fuente(doc: pymupdf.Document, pag: pymupdf.Page, tramo: Tramo, texto: str,
                    indice: IndiceFuentesSistema | None, incluidas: IndiceFuentesSistema | None,
                    cache: dict | None = None) -> AnalisisFuente:
    """Opciones de fuente para escribir `texto` con el estilo del tramo, de mejor a peor:
    la original del PDF, la completa (instalada o incluida), instalarla si es libre y,
    por último, una sustituta de la MISMA clase (sans/serif/mono), peso y estilo."""
    from pdf_studio.dependencias.catalogo_fuentes import buscar_comercial, buscar_libre

    opciones: list[OpcionFuente] = []
    nombre = sin_prefijo(tramo.fuente)
    cod = codificador_de_tramo(doc, pag, tramo, cache)
    embebido = cod.embebido if cod is not None else None
    if cod is not None:
        opciones.append(OpcionFuente("original", nombre, "Fuente original del PDF", cod.faltantes(texto),
                                     codificador=cod))
    datos_programa = None
    if cod is not None and cod.embebido not in (Embebido.NO_EMBEBIDA, Embebido.TYPE3):
        try:
            datos_programa = doc.extract_font(cod.xref)[3] or None
        except Exception:
            datos_programa = None
    clase = clase_tipografica(nombre, tramo.flags, datos_programa)
    estilo = estilo_de(tramo.fuente, tramo.flags)

    def opcion(tipo: str, cara, descripcion: str, compatible: bool = False) -> OpcionFuente | None:
        try:
            fuente = pymupdf.Font(fontbuffer=bytes_de_fuente(cara.archivo, cara.indice))
        except Exception:
            return None
        faltan = [c for c in dict.fromkeys(texto) if c not in "\n\r " and not fuente.has_glyph(ord(c))]
        return OpcionFuente(tipo, cara.nombre_visible, descripcion, faltan, archivo=cara.archivo,
                            indice=cara.indice, metrica_compatible=compatible)

    if indice is not None:
        cara = indice.buscar(nombre)
        if cara is not None:
            o = opcion("sistema", cara, f"Fuente completa instalada en Windows ({Path(cara.archivo).name})", True)
            if o is not None:
                opciones.append(o)
    if incluidas is not None:
        cara = incluidas.buscar(nombre)
        if cara is not None:
            o = opcion("incluida", cara, "Fuente completa incluida con PDF Studio", True)
            if o is not None:
                opciones.append(o)

    libre = buscar_libre(nombre)
    instalada = any(o.tipo in ("sistema", "incluida") for o in opciones)
    instalable = libre if libre is not None and not instalada else None

    # Sustituta: misma clase, peso y estilo; la más parecida en ancho a la original
    comercial = buscar_comercial(nombre)
    familias: list[tuple[str, bool]] = []
    if comercial is not None and clase_tipografica(comercial.alternativa) == clase:
        familias.append((comercial.alternativa, comercial.metrica_compatible))
    familias += [(f, False) for f in SUSTITUTAS_POR_CLASE[clase] if f != (comercial.alternativa if comercial else None)]
    muestra = [c for c in dict.fromkeys(tramo.texto) if c.strip()]
    mejores: list[tuple[float, int, OpcionFuente]] = []
    for orden, (familia, compatible) in enumerate(familias):
        if familia_de(familia) == familia_de(nombre):
            continue
        caras = []
        for idx in (incluidas, indice):
            if idx is not None and not caras:
                caras = idx.familia(familia)
        cara = _cara_con_estilo(caras, estilo)
        if cara is None or any(o.archivo == cara.archivo for o in opciones):
            continue
        detalle = "mismo ancho por letra" if compatible else "misma clase y estilo"
        o = opcion("sustituta", cara, f"Sustituta: {cara.nombre_visible} ({detalle})", compatible)
        if o is None:
            continue
        diferencia = 0.0
        if cod is not None and muestra and not compatible:
            fuente = pymupdf.Font(fontbuffer=bytes_de_fuente(cara.archivo, cara.indice))
            orig = sum(cod.avance(c) for c in muestra)
            nuevo = sum(fuente.glyph_advance(ord(c)) * 1000 for c in muestra)
            diferencia = abs(nuevo / orig - 1) if orig else 0.0
        mejores.append((0.0 if compatible else diferencia, orden, o))
    if mejores:
        opciones.append(min(mejores, key=lambda m: (m[0], m[1]))[2])
    return AnalisisFuente(nombre, embebido, opciones, instalable, clase, estilo)

# ---------------------------------------------------------------------- dibujo
@dataclass
class LineaADibujar:
    tokens: list[tuple[str, float]]      # (texto del glifo, x del origen)
    y: float                             # línea base
    tamano: float
    color: tuple[float, float, float]
    modo: int = 0
    opacidad: float = 1.0
    escala_h: float = 1.0                # escalado horizontal de los glifos (Tz/100)
    fuente_xref: int | None = None       # reutilizar esta fuente del PDF
    fuente_datos: bytes | None = None    # o embeber esta (ya recortada)
    ascender: float = 0.9
    descender: float = -0.2

    def caja(self, avance) -> pymupdf.Rect:
        x0 = self.tokens[0][1]
        ultimo, xu = self.tokens[-1]
        x1 = xu + avance(ultimo) / 1000 * self.tamano * self.escala_h
        # Generosa en vertical: acentos de mayúsculas y descendentes sobresalen de las métricas
        return pymupdf.Rect(x0, self.y - max(self.ascender, 1.2) * self.tamano, max(x1, x0 + 1),
                            self.y + max(-self.descender, 0.4) * self.tamano)


def tokenizar(texto: str, cod: CodificadorFuente | None) -> list[str]:
    """Separa en glifos; usa ligaduras (p. ej. 'fi') si la fuente original las tiene."""
    if cod is None:
        return list(texto)
    secuencias = {s for s in cod.a_codigo if len(s) > 1}
    tokens, i = [], 0
    while i < len(texto):
        for largo in (3, 2):
            if texto[i:i + largo] in secuencias:
                tokens.append(texto[i:i + largo])
                i += largo
                break
        else:
            tokens.append(texto[i])
            i += 1
    return tokens


def _nombre_recurso(doc: pymupdf.Document, pag: pymupdf.Page, xref: int) -> str:
    """Nombre con el que la página conoce a la fuente; si no la tiene (estaba en un
    XObject), se agrega a sus recursos."""
    for f in pag.get_fonts(full=True):
        if f[0] == xref and f[6] == 0:
            return f[4]
    nombre = f"PDFS{xref}"
    t, v = doc.xref_get_key(pag.xref, "Resources")
    contenedor, prefijo = (int(v.split()[0]), "") if t == "xref" else (pag.xref, "Resources/")
    doc.xref_set_key(contenedor, f"{prefijo}Font/{nombre}", f"{xref} 0 R")
    return nombre


def _num(v: float) -> str:
    texto = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if texto in ("-0", "", "-") else texto


def _agregar_contenido(doc: pymupdf.Document, pag: pymupdf.Page, contenido: bytes) -> None:
    if not pag.is_wrapped:
        pag.wrap_contents()
    nuevo = doc.get_new_xref()
    doc.update_object(nuevo, "<<>>")
    doc.update_stream(nuevo, contenido)
    actuales = pag.get_contents()
    doc.xref_set_key(pag.xref, "Contents", "[" + " ".join(f"{x} 0 R" for x in actuales + [nuevo]) + "]")


def _gs_opacidad(doc: pymupdf.Document, pag: pymupdf.Page, opacidad: float) -> str:
    nombre = f"PDFSgs{int(round(opacidad * 1000))}"
    t, v = doc.xref_get_key(pag.xref, "Resources")
    contenedor, prefijo = (int(v.split()[0]), "") if t == "xref" else (pag.xref, "Resources/")
    doc.xref_set_key(contenedor, f"{prefijo}ExtGState/{nombre}", f"<</ca {_num(opacidad)} /CA {_num(opacidad)}>>")
    return nombre


def dibujar_con_fuente_original(doc: pymupdf.Document, pag: pymupdf.Page, linea: LineaADibujar) -> None:
    cod = construir_codificador(doc, linea.fuente_xref, usados=set())
    if cod is None:
        raise Limitacion("No se pudo reutilizar la fuente original.")
    a_pdf = ~pag.transformation_matrix
    nombre = _nombre_recurso(doc, pag, linea.fuente_xref)
    x0, _ = linea.tokens[0][1], linea.y
    origen = pymupdf.Point(x0, linea.y) * a_pdf
    partes = []
    for i, (tok, x) in enumerate(linea.tokens):
        if tok not in cod.a_codigo:
            raise Limitacion(f"La fuente original no tiene el carácter «{tok}».")
        partes.append(f"<{cod.codigo(tok).hex()}>")
        if i + 1 < len(linea.tokens):
            natural = cod.avance(tok) / 1000 * linea.tamano * linea.escala_h
            delta = linea.tokens[i + 1][1] - x - natural
            if abs(delta) > 1e-4:
                partes.append(_num(-delta * 1000 / (linea.tamano * linea.escala_h)))
    r, g, b = linea.color
    color = f"{_num(r)} {_num(g)} {_num(b)}"
    lineas_cs = ["q"]
    if linea.opacidad < 0.999:
        lineas_cs.append(f"/{_gs_opacidad(doc, pag, linea.opacidad)} gs")
    lineas_cs += [
        "BT",
        f"/{nombre} {_num(linea.tamano)} Tf",
        f"{_num(linea.escala_h * 100)} Tz",
        f"{linea.modo} Tr",
        f"{color} rg {color} RG",
        f"1 0 0 1 {_num(origen.x)} {_num(origen.y)} Tm",
        "[" + " ".join(partes) + "] TJ",
        "ET",
        "Q",
    ]
    _agregar_contenido(doc, pag, ("\n".join(lineas_cs) + "\n").encode("latin-1"))


def dibujar_con_fuente_nueva(pag: pymupdf.Page, lineas: list[LineaADibujar]) -> None:
    for ln in lineas:
        fuente = pymupdf.Font(fontbuffer=ln.fuente_datos)
        escritor = pymupdf.TextWriter(pag.rect)
        x0 = ln.tokens[0][1]
        for tok, x in ln.tokens:
            if tok.strip():
                # El escalado horizontal (morph) se aplica alrededor de x0: se compensa la posición
                escritor.append((x0 + (x - x0) / ln.escala_h, ln.y), tok, font=fuente, fontsize=ln.tamano)
        morph = (pymupdf.Point(x0, ln.y), pymupdf.Matrix(ln.escala_h, 0, 0, 1, 0, 0)) if abs(ln.escala_h - 1) > 1e-4 else None
        escritor.write_text(pag, color=ln.color, opacity=ln.opacidad if ln.opacidad < 0.999 else -1,
                            morph=morph, render_mode=ln.modo)


def _clave_glifo(c: str | int, x: float, y: float) -> tuple:
    return (ord(c) if isinstance(c, str) else c, round(x, 1), round(y, 1))


def glifos_de_pagina(pag: pymupdf.Page) -> set[tuple]:
    """(unicode, x, y) de cada glifo visible o invisible de la página (sin espacios)."""
    return {_clave_glifo(c[0], c[2][0], c[2][1]) for s in pag.get_texttrace() for c in s["chars"]
            if c[0] > 32}


def _objetivo(bloque: "BloqueTexto") -> list[tuple]:
    return [_clave_glifo(g.c, g.x, g.y) for ln in bloque.lineas for g in ln.glifos if ord(g.c) > 32]


def _sin_espacios(texto: str) -> str:
    return "".join(texto.split())


def _recursos(doc: pymupdf.Document, pag: pymupdf.Page) -> dict:
    """Diccionario /Resources de la página, con cada categoría (Font, XObject...) en línea."""
    t, v = doc.xref_get_key(pag.xref, "Resources")
    if t == "xref":
        v = doc.xref_object(int(v.split()[0]), compressed=True)
    elif t != "dict":
        return {}
    recursos = parsear_objeto(v)
    # Solo la categoría se resuelve; sus entradas (cada fuente, imagen...) siguen siendo refs
    return {k: parsear_objeto(doc.xref_object(int(x), compressed=True)) if isinstance(x, Ref) else x
            for k, x in recursos.items()}


def _analizar_contenido(datos: bytes) -> tuple[int, list[str]]:
    """(bytes de texto que dibuja, nombres de los XObject que usa con Do, en orden)."""
    import pikepdf

    texto, usados = 0, []
    with pikepdf.Pdf.new() as auxiliar:
        for ins in pikepdf.parse_content_stream(pikepdf.Stream(auxiliar, datos)):
            if not isinstance(ins, pikepdf.ContentStreamInstruction):
                continue
            op, operandos = str(ins.operator), list(ins.operands)
            if op in ("Tj", "'", '"') and operandos and isinstance(operandos[-1], pikepdf.String):
                texto += len(bytes(operandos[-1]))
            elif op == "TJ" and operandos and isinstance(operandos[0], pikepdf.Array):
                texto += sum(len(bytes(e)) for e in operandos[0] if isinstance(e, pikepdf.String))
            elif op == "Do" and operandos:
                usados.append(str(operandos[0]).lstrip("/"))
    return texto, usados


def _xobjects_de(doc: pymupdf.Document, recursos) -> dict[str, int]:
    if isinstance(recursos, Ref):
        recursos = parsear_objeto(doc.xref_object(int(recursos), compressed=True))
    xobj = recursos.get("XObject") if isinstance(recursos, dict) else None
    if isinstance(xobj, Ref):
        xobj = parsear_objeto(doc.xref_object(int(xobj), compressed=True))
    return {k: int(v) for k, v in xobj.items() if isinstance(v, Ref)} if isinstance(xobj, dict) else {}


def _formas_sin_cambios(doc: pymupdf.Document, viejo: tuple[bytes, dict], nuevo: tuple[bytes, dict],
                        iguales: dict[int, int], vistos: set) -> bool:
    """Empareja los Form XObject que dibujaba el contenido viejo con los que dibuja el nuevo
    (el filtro de MuPDF los reescribe a todos, en el mismo orden) y anota en `iguales`
    {nuevo: viejo} los que no perdieron texto. Devuelve True si este contenido no cambió."""
    texto_v, usados_v = _analizar_contenido(viejo[0])
    texto_n, usados_n = _analizar_contenido(nuevo[0])
    igual = texto_v == texto_n
    if len(usados_v) != len(usados_n):
        return False
    for nombre_v, nombre_n in zip(usados_v, usados_n):
        xv, xn = viejo[1].get(nombre_v), nuevo[1].get(nombre_n)
        if xv is None or xn is None:
            igual = False
            continue
        if xv == xn or (xv, xn) in vistos:
            continue
        vistos.add((xv, xn))
        formas = all(doc.xref_get_key(x, "Subtype")[1] == "/Form" for x in (xv, xn))
        if not formas:
            igual = False      # una imagen reemplazada: no debería pasar (images=NONE)
            continue
        def contenido(x: int) -> tuple[bytes, dict]:
            return doc.xref_stream(x) or b"", _xobjects_de(doc, valor_de_clave(doc, x, "Resources"))
        if _formas_sin_cambios(doc, contenido(xv), contenido(xn), iguales, vistos):
            iguales[xn] = xv
        else:
            igual = False
    return igual


def borrar_texto(doc: pymupdf.Document, pagina: int, rects: list[pymupdf.Rect]) -> None:
    """Borra el texto de las zonas sin tocar imágenes ni gráficos.

    La redacción se hace sobre una copia de la página en un documento aparte y el
    resultado se trasplanta: así el documento real no queda marcado como
    "redactado" por MuPDF y se puede seguir guardando en modo incremental (lo que
    exige el principio 2). El filtro de MuPDF reescribe los recursos de la página
    (renombra imágenes, quita fuentes que ya no se usan): cada referencia se
    traduce al objeto original equivalente.

    Texto dentro de un Form XObject (p. ej. la capa de un OCR): copia al escribir. El
    XObject que perdió texto se copia como objeto NUEVO que usa solo esta página; el
    original queda intacto para las demás páginas que lo dibujan. Los XObject que no
    cambiaron siguen apuntando al objeto original (no se duplican).
    """
    from pdf_studio.core.redaccion import separar_tj

    pag = doc[pagina]
    temporal = pymupdf.open()
    try:
        temporal.insert_pdf(doc, from_page=pagina, to_page=pagina)
        # Sin TJ, para esquivar el error de MuPDF que corre las letras que siguen a lo borrado
        separar_tj(temporal, 0)
        pag_t = temporal[0]
        mapa: dict[int, int] = {}
        emparejar_objetos(temporal, valor_de_clave(temporal, pag_t.xref, "Resources"),
                          doc, valor_de_clave(doc, pag.xref, "Resources"), mapa)
        viejo = (pag_t.read_contents(), _xobjects_de(temporal, valor_de_clave(temporal, pag_t.xref, "Resources")))
        for r in rects:
            pag_t.add_redact_annot(r, fill=False, cross_out=False)
        pag_t.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                               graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                               text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        recursos_t = valor_de_clave(temporal, pag_t.xref, "Resources")
        contenido = pag_t.read_contents()
        iguales: dict[int, int] = {}
        _formas_sin_cambios(temporal, viejo, (contenido, _xobjects_de(temporal, recursos_t)), iguales, set())
        for nuevo, original in iguales.items():
            if original in mapa:
                mapa[nuevo] = mapa[original]
        try:
            recursos_nuevos = trasplantar(temporal, recursos_t if recursos_t is not None else {}, doc, mapa)
        except KeyError:
            raise Limitacion("Este texto está en una parte del PDF que no se puede copiar sin "
                                 "re-codificar una imagen; no se editó para no alterar el documento.")
    finally:
        temporal.close()
    xref_contenido = doc.get_new_xref()
    doc.update_object(xref_contenido, "<<>>")
    doc.update_stream(xref_contenido, contenido)
    if isinstance(recursos_nuevos, Ref):
        xref_recursos = int(recursos_nuevos)
    else:
        xref_recursos = doc.get_new_xref()
        doc.update_object(xref_recursos, serializar_objeto(recursos_nuevos))
    doc.xref_set_key(pag.xref, "Contents", f"{xref_contenido} 0 R")
    doc.xref_set_key(pag.xref, "Resources", f"{xref_recursos} 0 R")

class EditarTexto(Operacion):
    nombre = "Editar texto"

    def __init__(self, pagina: int, rects_borrar: list[tuple], lineas: list[LineaADibujar],
                 texto_esperado: str, cajas_nuevas: list[tuple], descripcion: str = "Editar texto",
                 zonas_borradas: list[tuple] | None = None, glifos_objetivo: list[tuple] | None = None):
        self.pagina = pagina
        # Glifos que el usuario quiere borrar: (unicode, x, y) redondeados
        self.glifos_objetivo = {tuple(g) for g in (glifos_objetivo or [])}
        self.rects_borrar = [tuple(r) for r in rects_borrar]
        self.zonas_borradas = [tuple(r) for r in (zonas_borradas or rects_borrar)]
        self.lineas = lineas
        self.texto_esperado = texto_esperado
        self.cajas_nuevas = [tuple(r) for r in cajas_nuevas]
        self._descripcion = descripcion

    def aplicar(self, doc: pymupdf.Document) -> None:
        pag = doc[self.pagina]
        independizar_recursos(doc, self.pagina)
        borrar = [pymupdf.Rect(r) for r in self.rects_borrar]
        antes = glifos_de_pagina(pag)
        borrar_texto(doc, self.pagina, borrar)
        pag = doc[self.pagina]
        # Todo glifo que no era parte del bloque tiene que seguir ahí, en el mismo lugar
        despues = glifos_de_pagina(pag)
        perdidos = (antes - self.glifos_objetivo) - despues
        if perdidos:
            ejemplo = "".join(sorted(chr(g[0]) for g in perdidos))[:20]
            raise Limitacion(f"La edición habría borrado texto vecino («{ejemplo}»). "
                                 "No se aplicó para no dañar el documento.")
        if self.glifos_objetivo & despues:
            raise Limitacion("No se pudo borrar por completo el texto original. No se aplicó.")
        nuevas = [ln for ln in self.lineas if ln.fuente_datos is not None]
        for ln in self.lineas:
            if ln.fuente_xref is not None:
                dibujar_con_fuente_original(doc, pag, ln)
        if nuevas:
            dibujar_con_fuente_nueva(pag, nuevas)
        # El texto nuevo tiene que poder extraerse igual que como se escribió
        if self.texto_esperado.strip() and not any(ln.modo == 3 for ln in self.lineas):
            zona = pymupdf.Rect(self.cajas_nuevas[0])
            for c in self.cajas_nuevas[1:]:
                zona |= c
            extraido = pag.get_text("text", clip=zona + (-2, -2, 2, 2), flags=FLAGS_EXTRACCION)
            if _sin_espacios(self.texto_esperado) not in _sin_espacios(extraido):
                raise Limitacion("El texto escrito no se puede leer correctamente en el PDF "
                                     "(problema de codificación de la fuente). No se aplicó.")

    def paginas_afectadas(self) -> set[int]:
        return {self.pagina}

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return {self.pagina: self.zonas_borradas + self.cajas_nuevas}

    def descripcion(self) -> str:
        return f"{self._descripcion} en página {self.pagina + 1}"


# ---------------------------------------------------------------------- planificación
def _zona_borrada(linea: Linea) -> tuple:
    """Zona que puede cambiar al borrar la línea: glifos completos, con acentos."""
    tam = linea.tramos[0].tamano
    caja = linea.bbox
    return tuple(pymupdf.Rect(caja.x0 - 1, linea.base_y - 1.2 * tam, caja.x1 + 1, linea.base_y + 0.45 * tam))


def _franja_borrado(linea: Linea, glifos: list[Glifo] | None = None) -> pymupdf.Rect:
    """Franja interior de la línea: toca los glifos a borrar pero no las líneas vecinas."""
    glifos = glifos or linea.glifos
    tam = linea.tramos[0].tamano
    x0 = glifos[0].bbox[0] + min(0.5, (glifos[0].bbox[2] - glifos[0].bbox[0]) / 4)
    x1 = glifos[-1].bbox[2] - min(0.5, (glifos[-1].bbox[2] - glifos[-1].bbox[0]) / 4)
    y = glifos[0].y
    return pymupdf.Rect(x0, y - 0.62 * tam, max(x1, x0 + 0.1), y - 0.18 * tam)


def _espaciado(tramo: Tramo, cod: CodificadorFuente | None) -> tuple[float, float, float]:
    """(escala horizontal, espaciado entre letras, espacio extra entre palabras) medidos."""
    if cod is None:
        return 1.0, 0.0, 0.0
    pares, espacios = [], []
    g = tramo.glifos
    for a, b in zip(g, g[1:]):
        natural = cod.avance(a.c) / 1000 * tramo.tamano
        medido = b.x - a.x
        (espacios if a.c == " " else pares).append((natural, medido))
    escala, letras = 1.0, 0.0
    if len(pares) >= 3:
        xs = [p[0] for p in pares]
        ys = [p[1] for p in pares]
        if max(xs) - min(xs) > 1e-3:
            media_x, media_y = statistics.fmean(xs), statistics.fmean(ys)
            cov = sum((x - media_x) * (y - media_y) for x, y in pares)
            var = sum((x - media_x) ** 2 for x in xs)
            escala = cov / var if var else 1.0
            letras = media_y - escala * media_x
        if abs(escala - 1) < 0.005:
            escala = 1.0
            letras = statistics.fmean(y - x for x, y in pares)
        if abs(letras) < 0.01:
            letras = 0.0
    palabras = 0.0
    if espacios:
        palabras = statistics.fmean(m - (n * escala + letras) for n, m in espacios)
        if abs(palabras) < 0.05:
            palabras = 0.0
    return escala, letras, palabras


def _alineacion(lineas: list[Linea]) -> str:
    if len(lineas) < 2:
        return "izquierda"
    x0s = [ln.bbox.x0 for ln in lineas]
    x1s = [ln.bbox.x1 for ln in lineas]
    centros = [(a + b) / 2 for a, b in zip(x0s, x1s)]
    tol = 1.5
    izq = max(x0s) - min(x0s) < tol
    der = max(x1s[:-1]) - min(x1s[:-1]) < tol if len(lineas) > 2 else abs(x1s[0] - x1s[1]) < tol
    if izq and der:
        return "justificado"
    if izq:
        return "izquierda"
    if max(x1s) - min(x1s) < tol:
        return "derecha"
    if max(centros) - min(centros) < tol:
        return "centro"
    return "izquierda"


class _Metrica:
    """Avance de glifos según la opción de fuente elegida, en milésimas de em."""

    def __init__(self, opcion: OpcionFuente):
        self.opcion = opcion
        self.cod = opcion.codificador if opcion.tipo == "original" else None
        self.fuente = None if self.cod is not None else opcion.fuente_pymupdf()

    def __call__(self, token: str) -> float:
        if self.cod is not None:
            codigo = self.cod.a_codigo.get(token)
            if codigo is not None and codigo in self.cod.anchos:
                return self.cod.anchos[codigo] * self.cod.escala_anchos
            return sum(self.cod.avance(c) for c in token)
        return sum(self.fuente.glyph_advance(ord(c)) * 1000 for c in token)

    def tokens(self, texto: str) -> list[str]:
        return tokenizar(texto, self.cod)


def _posiciones(tokens: list[str], x0: float, metrica: _Metrica, tamano: float,
                escala: float, letras: float, palabras: float) -> list[tuple[str, float]]:
    res, x = [], x0
    for t in tokens:
        res.append((t, x))
        x += (metrica(t) / 1000 * tamano + letras + (palabras if t == " " else 0.0)) * escala
    return res


def _ancho(tokens, metrica, tamano, escala, letras, palabras) -> float:
    if not tokens:
        return 0.0
    pos = _posiciones(tokens, 0.0, metrica, tamano, escala, letras, palabras)
    ultimo, x = pos[-1]
    return x + metrica(ultimo) / 1000 * tamano * escala


def _cortar_en_lineas(texto: str, ancho: float, medir) -> list[tuple[str, bool]]:
    """Divide el texto en líneas que entran en `ancho`. Devuelve (línea, es_fin_de_párrafo)."""
    resultado = []
    for parrafo in texto.split("\n"):
        palabras = parrafo.split(" ")
        actual = ""
        for p in palabras:
            prueba = p if not actual else actual + " " + p
            if actual and medir(prueba) > ancho + 0.01:
                resultado.append((actual, False))
                actual = p
            else:
                actual = prueba
        resultado.append((actual, True))
    return resultado


@dataclass
class PlanEdicion:
    operacion: EditarTexto
    avisos: list[str]


def planificar(doc: pymupdf.Document, bloque: BloqueTexto, texto_nuevo: str, opcion: OpcionFuente | None,
               desplazamiento: tuple[float, float] = (0.0, 0.0), cache: dict | None = None) -> PlanEdicion:
    """Arma la operación que edita (y/o mueve) el bloque. No modifica el documento."""
    if not bloque.es_horizontal:
        raise Limitacion("Por ahora solo se puede editar texto horizontal.")
    pag = doc[bloque.pagina]
    dx, dy = desplazamiento
    avisos: list[str] = []
    rects = [_franja_borrado(ln) for ln in bloque.lineas]
    texto_original = bloque.texto
    lineas_dibujo: list[LineaADibujar] = []

    # Caso 1: mismo texto (mover). Cada tramo con su propia fuente y posiciones exactas
    if texto_nuevo == texto_original:
        todos = True
        for ln in bloque.lineas:
            for tr in ln.tramos:
                cod = codificador_de_tramo(doc, pag, tr, cache)
                if cod is None or not cod.puede(tr.texto):
                    todos = False
                    break
                tokens = []
                i = 0
                for tok in tokenizar(tr.texto, cod):
                    tokens.append((tok, tr.glifos[i].x + dx))
                    i += len(tok)
                escala = _espaciado(tr, cod)[0]
                lineas_dibujo.append(LineaADibujar(tokens, tr.glifos[0].y + dy, tr.tamano, tr.color, tr.modo,
                                                   tr.opacidad, escala, fuente_xref=cod.xref,
                                                   ascender=tr.ascender, descender=tr.descender))
            if not todos:
                break
        if todos:
            cajas = [tuple(ln.caja(_avance_cod(construir_codificador(doc, ln.fuente_xref, usados=set()))))
                     for ln in lineas_dibujo]
            op = EditarTexto(bloque.pagina, rects, lineas_dibujo, texto_original, cajas,
                             "Mover texto" if (dx or dy) else "Reescribir texto",
                             [_zona_borrada(ln) for ln in bloque.lineas], _objetivo(bloque))
            return PlanEdicion(op, avisos)
        lineas_dibujo = []
        if opcion is None:
            raise Limitacion("La fuente original no se puede reutilizar: elegí otra fuente.")
        avisos.append("La fuente original no se pudo reutilizar; se usa " + opcion.nombre + ".")

    if opcion is None or not opcion.disponible:
        raise Limitacion("La fuente elegida no tiene todos los caracteres del texto nuevo.")
    estilo = bloque.estilo
    if bloque.varios_estilos:
        avisos.append("El texto tenía varios estilos; el texto nuevo usa el estilo del comienzo.")
    metrica = _Metrica(opcion)
    cod_original = codificador_de_tramo(doc, pag, estilo, cache)
    escala, letras, palabras = _espaciado(estilo, cod_original)
    datos_fuente = None
    if opcion.tipo != "original":
        datos_fuente = subconjunto(bytes_de_fuente(opcion.archivo, opcion.indice), texto_nuevo)
        if opcion.tipo == "sustituta":
            avisos.append(f"Se usó la fuente sustituta «{opcion.nombre}» en lugar de «{sin_prefijo(estilo.fuente)}».")

    def linea(tokens_pos, y) -> LineaADibujar:
        return LineaADibujar(tokens_pos, y + dy, estilo.tamano, estilo.color, estilo.modo, estilo.opacidad, escala,
                             fuente_xref=opcion.codificador.xref if opcion.tipo == "original" else None,
                             fuente_datos=datos_fuente, ascender=estilo.ascender, descender=estilo.descender)

    if bloque.nivel in (Nivel.FRAGMENTO, Nivel.LINEA):
        ln = bloque.lineas[0]
        texto_linea = texto_nuevo.replace("\n", " ")
        tokens = metrica.tokens(texto_linea)
        x0 = ln.glifos[0].x
        ancho_orig = ln.bbox.x1 - ln.bbox.x0
        if bloque.nivel == Nivel.LINEA and abs(palabras) > 0.3:
            # Línea justificada: se reparte el espacio para conservar el ancho
            espacios = sum(1 for t in tokens if t == " ")
            natural = _ancho(tokens, metrica, estilo.tamano, escala, letras, 0.0)
            if espacios and natural < ancho_orig:
                palabras = (ancho_orig - natural) / escala / espacios
            else:
                palabras = 0.0
        if bloque.nivel == Nivel.LINEA:
            contexto = bloque.lineas_del_parrafo or bloque.lineas
            alin = _alineacion(contexto)
            ancho_nuevo = _ancho(tokens, metrica, estilo.tamano, escala, letras, palabras)
            if alin == "centro":
                x0 = (ln.bbox.x0 + ln.bbox.x1) / 2 - ancho_nuevo / 2
            elif alin == "derecha":
                x0 = ln.bbox.x1 - ancho_nuevo
        lineas_dibujo.append(linea(_posiciones(tokens, x0 + dx, metrica, estilo.tamano, escala, letras, palabras),
                                   ln.base_y))
        ancho_final = _ancho(tokens, metrica, estilo.tamano, escala, letras, palabras)
        if ancho_final > ancho_orig + 1 and bloque.lineas_del_parrafo and len(bloque.lineas_del_parrafo) > 1:
            avisos.append(f"El texto nuevo es {ancho_final - ancho_orig:.0f} pt más ancho que la línea original "
                          "y puede salirse del margen. Para que se acomode solo, editá el párrafo entero.")
    else:
        lineas = bloque.lineas
        x_izq = min(ln.bbox.x0 for ln in lineas)
        x_der = max(ln.bbox.x1 for ln in lineas)
        ancho = x_der - x_izq
        bases = [ln.base_y for ln in lineas]
        paso = statistics.median([b - a for a, b in zip(bases, bases[1:])]) if len(bases) > 1 else estilo.tamano * 1.2
        alin = _alineacion(lineas)
        base_palabras = 0.0 if alin == "justificado" else palabras

        def medir(texto: str) -> float:
            return _ancho(metrica.tokens(texto), metrica, estilo.tamano, escala, letras, base_palabras)

        partes = _cortar_en_lineas(texto_nuevo, ancho, medir)
        for i, (texto_linea, fin) in enumerate(partes):
            tokens = metrica.tokens(texto_linea)
            extra = base_palabras
            natural = _ancho(tokens, metrica, estilo.tamano, escala, letras, base_palabras)
            x0 = x_izq
            if alin == "justificado" and not fin:
                espacios = sum(1 for t in tokens if t == " ")
                if espacios and natural < ancho:
                    extra = (ancho - _ancho(tokens, metrica, estilo.tamano, escala, letras, 0.0)) / escala / espacios
            elif alin == "centro":
                x0 = x_izq + (ancho - natural) / 2
            elif alin == "derecha":
                x0 = x_der - natural
            if natural > ancho + 0.5:
                avisos.append(f"Una palabra no entra en el ancho del párrafo: «{texto_linea[:30]}».")
            lineas_dibujo.append(linea(_posiciones(tokens, x0 + dx, metrica, estilo.tamano, escala, letras, extra),
                                       bases[0] + i * paso))
        if len(partes) > len(lineas):
            avisos.append(f"El párrafo ahora ocupa {len(partes)} líneas (antes {len(lineas)}): "
                          "revisá que no se superponga con lo que sigue.")
    lineas_dibujo = [ld for ld in lineas_dibujo if ld.tokens]
    cajas = [tuple(ld.caja(metrica)) for ld in lineas_dibujo]
    op = EditarTexto(bloque.pagina, rects, lineas_dibujo, texto_nuevo, cajas or [tuple(rects[0])],
                     "Mover y editar texto" if (dx or dy) else "Editar texto",
                     [_zona_borrada(ln) for ln in bloque.lineas], _objetivo(bloque))
    return PlanEdicion(op, avisos)


def _avance_cod(cod: CodificadorFuente):
    def avance(token: str) -> float:
        codigo = cod.a_codigo.get(token)
        if codigo is not None and codigo in cod.anchos:
            return cod.anchos[codigo] * cod.escala_anchos
        return sum(cod.avance(c) for c in token)
    return avance
