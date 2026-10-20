"""Marca de agua (texto o imagen) y numeración de páginas.

Se agregan como contenido nuevo al final (o al principio, «detrás») de cada página, sin
tocar lo que ya había. Cada operación declara la zona que ocupa en cada página: fuera de
esa zona la página tiene que quedar idéntica al original.

Las posiciones se piensan en la página TAL COMO SE VE (con su rotación): «abajo a la
derecha» es abajo a la derecha aunque la página tenga /Rotate.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from enum import Enum

import pymupdf
from PIL import Image

from pdf_studio.core.documento import ErrorDocumento, Operacion
from pdf_studio.core.pdfutil import independizar_recursos
from pdf_studio.core.rangos import describir_rango

FUENTES = {"helv": "Helvetica", "hebo": "Helvetica negrita", "tiro": "Times", "tibo": "Times negrita",
           "cour": "Courier"}


class Posicion(str, Enum):
    ARRIBA_IZQ = "arriba_izq"
    ARRIBA = "arriba"
    ARRIBA_DER = "arriba_der"
    CENTRO = "centro"
    ABAJO_IZQ = "abajo_izq"
    ABAJO = "abajo"
    ABAJO_DER = "abajo_der"

    @property
    def texto(self) -> str:
        return {"arriba_izq": "Arriba a la izquierda", "arriba": "Arriba al centro",
                "arriba_der": "Arriba a la derecha", "centro": "Centro",
                "abajo_izq": "Abajo a la izquierda", "abajo": "Abajo al centro",
                "abajo_der": "Abajo a la derecha"}[self.value]

    def ancla(self, ancho: float, alto: float, margen: float, w: float, h: float) -> tuple[float, float]:
        """Centro del elemento (w × h) en una página vista de ancho × alto."""
        col = {"izq": margen + w / 2, "der": ancho - margen - w / 2}
        fila = {"arriba": margen + h / 2, "abajo": alto - margen - h / 2}
        if self == Posicion.CENTRO:
            return ancho / 2, alto / 2
        partes = self.value.split("_")
        y = fila[partes[0]]
        x = col[partes[1]] if len(partes) > 1 else ancho / 2
        return x, y


def _color(hexa: str) -> tuple[float, float, float]:
    hexa = hexa.lstrip("#")
    if len(hexa) != 6:
        raise ValueError(f"Color inválido: #{hexa}")
    return tuple(int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _a_pagina(pag: pymupdf.Page, x: float, y: float) -> pymupdf.Point:
    """De coordenadas de la página vista a coordenadas sin rotar."""
    return pymupdf.Point(x, y) * pag.derotation_matrix


def _rotar(px: float, py: float, cx: float, cy: float, grados: float) -> tuple[float, float]:
    """Rota (px, py) alrededor de (cx, cy), en sentido antihorario tal como se ve."""
    a = math.radians(grados)
    dx, dy = px - cx, py - cy
    return cx + dx * math.cos(a) + dy * math.sin(a), cy - dx * math.sin(a) + dy * math.cos(a)


def escribir_texto(pag: pymupdf.Page, texto: str, centro: tuple[float, float], tamano: float, fuente: str,
                   color: tuple[float, float, float], opacidad: float, angulo: float,
                   detras: bool = False) -> pymupdf.Rect:
    """Escribe `texto` centrado en `centro` (coordenadas de la página vista), girado `angulo`
    grados (antihorario, como se ve). Devuelve la zona ocupada, sin rotar."""
    f = pymupdf.Font(fuente)
    ancho = f.text_length(texto, fontsize=tamano)
    cx, cy = centro
    # Línea de base centrada verticalmente con respecto a las mayúsculas
    inicio = (cx - ancho / 2, cy + tamano * 0.35)
    esquinas = [(cx - ancho / 2, cy - tamano * 0.75), (cx + ancho / 2, cy - tamano * 0.75),
                (cx + ancho / 2, cy + tamano * 0.4), (cx - ancho / 2, cy + tamano * 0.4)]
    inicio = _rotar(*inicio, cx, cy, angulo)
    esquinas = [_rotar(x, y, cx, cy, angulo) for x, y in esquinas]
    origen = _a_pagina(pag, *inicio)
    # Dirección del texto en la página sin rotar
    a = math.radians(angulo)
    fin = _a_pagina(pag, inicio[0] + math.cos(a), inicio[1] - math.sin(a))
    d = fin - origen
    # morph se aplica en el espacio PDF (y hacia arriba): el giro va con el signo invertido
    matriz = pymupdf.Matrix(d.x, -d.y, d.y, d.x, 0, 0)
    forma = pag.new_shape()
    forma.insert_text(origen, texto, fontsize=tamano, fontname=fuente, color=color, fill_opacity=opacidad,
                      morph=(origen, matriz))
    forma.commit(overlay=not detras)
    puntos = [_a_pagina(pag, x, y) for x, y in esquinas]
    return pymupdf.Rect(min(p.x for p in puntos), min(p.y for p in puntos),
                        max(p.x for p in puntos), max(p.y for p in puntos))


def imagen_con_opacidad(datos: bytes, opacidad: float) -> bytes:
    """PNG con la transparencia multiplicada por `opacidad` (la imagen original no se toca)."""
    with Image.open(io.BytesIO(datos)) as img:
        rgba = img.convert("RGBA")
    if opacidad < 1:
        alfa = rgba.getchannel("A").point(lambda v: round(v * opacidad))
        rgba.putalpha(alfa)
    salida = io.BytesIO()
    rgba.save(salida, "PNG", optimize=True)
    return salida.getvalue()


# ---------------------------------------------------------------------- marca de agua
@dataclass
class OpcionesMarca:
    texto: str = "CONFIDENCIAL"
    imagen: bytes | None = None          # si hay imagen, se usa en lugar del texto
    fuente: str = "hebo"
    tamano: float = 60.0
    color: str = "#C0392B"
    opacidad: float = 0.25
    angulo: float = 45.0
    posicion: Posicion = Posicion.CENTRO
    escala_imagen: float = 0.5           # ancho de la imagen respecto del ancho de la página
    detras: bool = False                 # detrás del contenido (puede quedar tapada)
    margen: float = 36.0


class MarcaDeAgua(Operacion):
    nombre = "Marca de agua"

    def __init__(self, paginas: list[int], opciones: OpcionesMarca):
        if not paginas:
            raise ValueError("No hay páginas para la marca de agua.")
        if opciones.imagen is None and not opciones.texto.strip():
            raise ValueError("Escribí el texto de la marca de agua o elegí una imagen.")
        if not 0.05 <= opciones.opacidad <= 1:
            raise ValueError("La opacidad tiene que estar entre 5 % y 100 %.")
        self.paginas = sorted(set(paginas))
        self.opciones = opciones
        self._png = imagen_con_opacidad(opciones.imagen, opciones.opacidad) if opciones.imagen else None
        self._zonas: dict[int, list[tuple]] = {}

    def aplicar(self, doc: pymupdf.Document) -> None:
        o = self.opciones
        self._zonas = {}
        for p in self.paginas:
            independizar_recursos(doc, p)
            pag = doc[p]
            ancho, alto = pag.rect.width, pag.rect.height
            if self._png is not None:
                zona = self._imagen(pag, ancho, alto)
            else:
                f = pymupdf.Font(o.fuente)
                w = f.text_length(o.texto, fontsize=o.tamano)
                # El tamaño que ocupa girado, para que no se salga de la página
                a = math.radians(o.angulo)
                ew = abs(w * math.cos(a)) + abs(o.tamano * math.sin(a))
                eh = abs(w * math.sin(a)) + abs(o.tamano * math.cos(a))
                centro = o.posicion.ancla(ancho, alto, o.margen, ew, eh)
                zona = escribir_texto(pag, o.texto, centro, o.tamano, o.fuente, _color(o.color), o.opacidad,
                                      o.angulo, o.detras)
            self._zonas[p] = [tuple(zona + (-2, -2, 2, 2))]

    def _imagen(self, pag: pymupdf.Page, ancho: float, alto: float) -> pymupdf.Rect:
        o = self.opciones
        with Image.open(io.BytesIO(self._png)) as img:
            proporcion = img.height / img.width
        w = ancho * o.escala_imagen
        h = w * proporcion
        cx, cy = o.posicion.ancla(ancho, alto, o.margen, w, h)
        visto = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        caja = visto * pag.derotation_matrix
        caja.normalize()
        # rotate: la imagen queda derecha tal como se ve la página
        pag.insert_image(caja, stream=self._png, keep_proportion=True, overlay=not o.detras,
                         rotate=(-pag.rotation) % 360)
        return caja

    def paginas_afectadas(self) -> set[int]:
        return set(self.paginas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return dict(self._zonas)

    def descripcion(self) -> str:
        que = "imagen" if self.opciones.imagen else f"«{self.opciones.texto}»"
        return f"Marca de agua {que} en {len(self.paginas)} página{'s' if len(self.paginas) != 1 else ''}"


# ---------------------------------------------------------------------- numeración
FORMATOS_NUMERACION = {
    "{n}": "1, 2, 3…",
    "Página {n}": "Página 1",
    "Página {n} de {total}": "Página 1 de 10",
    "{n} / {total}": "1 / 10",
    "- {n} -": "- 1 -",
}


@dataclass
class OpcionesNumeracion:
    formato: str = "Página {n} de {total}"
    inicio: int = 1
    posicion: Posicion = Posicion.ABAJO
    fuente: str = "helv"
    tamano: float = 10.0
    color: str = "#333333"
    margen: float = 28.0
    total: int | None = None             # None = el último número de la numeración


class NumerarPaginas(Operacion):
    nombre = "Numerar páginas"

    def __init__(self, paginas: list[int], opciones: OpcionesNumeracion):
        if not paginas:
            raise ValueError("No hay páginas para numerar.")
        if "{n}" not in opciones.formato:
            raise ValueError("El formato tiene que incluir {n} (el número de página).")
        try:
            opciones.formato.format(n=1, total=1)
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError("El formato solo puede usar {n} y {total}.") from e
        self.paginas = sorted(set(paginas))
        self.opciones = opciones
        self._zonas: dict[int, list[tuple]] = {}

    def texto_de(self, indice: int) -> str:
        o = self.opciones
        total = o.total if o.total is not None else o.inicio + len(self.paginas) - 1
        return o.formato.format(n=o.inicio + indice, total=total)

    def aplicar(self, doc: pymupdf.Document) -> None:
        o = self.opciones
        f = pymupdf.Font(o.fuente)
        self._zonas = {}
        for i, p in enumerate(self.paginas):
            if p >= doc.page_count:
                raise ErrorDocumento(f"La página {p + 1} no existe.")
            independizar_recursos(doc, p)
            pag = doc[p]
            texto = self.texto_de(i)
            w = f.text_length(texto, fontsize=o.tamano)
            centro = o.posicion.ancla(pag.rect.width, pag.rect.height, o.margen, w, o.tamano)
            zona = escribir_texto(pag, texto, centro, o.tamano, o.fuente, _color(o.color), 1.0, 0.0)
            self._zonas[p] = [tuple(zona + (-2, -2, 2, 2))]

    def paginas_afectadas(self) -> set[int]:
        return set(self.paginas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return dict(self._zonas)

    def descripcion(self) -> str:
        return f"Numerar páginas {describir_rango(self.paginas)}"


def vista_previa(doc: pymupdf.Document, pagina: int, operacion: Operacion, dpi: int = 60) -> pymupdf.Pixmap:
    """Render de una página con la operación aplicada, sobre una copia (no toca `doc`)."""
    copia = pymupdf.open()
    try:
        copia.insert_pdf(doc, from_page=pagina, to_page=pagina)
        op = _para_una_pagina(operacion, pagina)
        op.aplicar(copia)
        return copia[0].get_pixmap(dpi=dpi, annots=True)
    finally:
        copia.close()


def _para_una_pagina(operacion: Operacion, pagina: int) -> Operacion:
    """La misma operación, para la página `pagina` copiada como página 0."""
    if isinstance(operacion, MarcaDeAgua):
        return MarcaDeAgua([0], operacion.opciones)
    if isinstance(operacion, NumerarPaginas):
        indice = operacion.paginas.index(pagina) if pagina in operacion.paginas else 0
        o = operacion.opciones
        total = o.total if o.total is not None else o.inicio + len(operacion.paginas) - 1
        return NumerarPaginas([0], OpcionesNumeracion(o.formato, o.inicio + indice, o.posicion, o.fuente,
                                                      o.tamano, o.color, o.margen, total))
    raise TypeError(type(operacion).__name__)


