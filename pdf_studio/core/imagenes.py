"""Firmas e imágenes: quitar fondo, biblioteca de firmas e inserción en la página.

La inserción no rasteriza nada: la imagen se agrega como un objeto nuevo y se
dibuja con su propia matriz de transformación (posición, escala y rotación), sin
re-muestrear sus píxeles ni tocar el resto del contenido de la página.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageOps

from pdf_studio import config
from pdf_studio.core.documento import ErrorDocumento, Operacion
from pdf_studio.core.pdfutil import independizar_recursos

LADO_MAXIMO = 2000      # px; las firmas más grandes se reducen al guardarlas en la biblioteca
EXTENSIONES_IMAGEN = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")


# ---------------------------------------------------------------------- quitar fondo
def luminancia(img: Image.Image) -> np.ndarray:
    rgb = np.asarray(img.convert("RGB"), dtype=np.float32)
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def umbral_automatico(lum: np.ndarray) -> int:
    """Umbral de Otsu entre la tinta y el papel, acotado a un rango razonable."""
    hist, _ = np.histogram(lum, bins=256, range=(0, 256))
    total = lum.size
    suma_total = np.dot(np.arange(256), hist)
    peso_fondo = 0.0
    suma_fondo = 0.0
    mejor, umbral = -1.0, 200
    for t in range(256):
        peso_fondo += hist[t]
        if peso_fondo == 0:
            continue
        peso_frente = total - peso_fondo
        if peso_frente == 0:
            break
        suma_fondo += t * hist[t]
        media_f = suma_fondo / peso_fondo
        media_p = (suma_total - suma_fondo) / peso_frente
        varianza = peso_fondo * peso_frente * (media_f - media_p) ** 2
        if varianza > mejor:
            mejor, umbral = varianza, t
    return int(min(245, max(60, umbral)))


@dataclass
class OpcionesFondo:
    umbral: int | None = None          # None = automático
    suavidad: int = 25                 # ancho de la transición (bordes suaves)
    color_tinta: tuple[int, int, int] | None = None   # None = conservar el color original
    recortar: bool = True              # recortar márgenes vacíos


def quitar_fondo(img: Image.Image, opciones: OpcionesFondo | None = None) -> Image.Image:
    """Convierte una firma escaneada (fondo claro) en un PNG con transparencia."""
    op = opciones or OpcionesFondo()
    img = ImageOps.exif_transpose(img)
    rgba = img.convert("RGBA")
    lum = luminancia(rgba)
    umbral = op.umbral if op.umbral is not None else umbral_automatico(lum)
    suavidad = max(1, op.suavidad)
    # Opaco por debajo de (umbral - suavidad), transparente por encima del umbral
    alfa = np.clip((umbral - lum) / suavidad, 0.0, 1.0)
    arr = np.asarray(rgba, dtype=np.float32)
    alfa = np.minimum(alfa, arr[..., 3] / 255.0)
    if op.color_tinta is not None:
        rgb = np.empty_like(arr[..., :3])
        rgb[...] = np.array(op.color_tinta, dtype=np.float32)
    else:
        # Quita el "halo" blanco de los bordes: des-mezcla el color contra el papel
        a = np.maximum(alfa, 1e-3)[..., None]
        rgb = np.clip((arr[..., :3] - (1.0 - a) * 255.0) / a, 0, 255)
    salida = np.dstack([rgb, alfa * 255.0]).round().astype(np.uint8)
    resultado = Image.fromarray(salida, "RGBA")
    if op.recortar:
        resultado = recortar_transparente(resultado)
    return resultado


def recortar_transparente(img: Image.Image, margen: int = 4) -> Image.Image:
    alfa = np.asarray(img.getchannel("A"))
    filas = np.where(alfa.max(axis=1) > 8)[0]
    cols = np.where(alfa.max(axis=0) > 8)[0]
    if len(filas) == 0 or len(cols) == 0:
        return img
    caja = (max(0, cols[0] - margen), max(0, filas[0] - margen),
            min(img.width, cols[-1] + 1 + margen), min(img.height, filas[-1] + 1 + margen))
    return img.crop(caja)


def tiene_transparencia(img: Image.Image) -> bool:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        return int(np.asarray(img.convert("RGBA").getchannel("A")).min()) < 250
    return False


def limitar_tamano(img: Image.Image, lado: int = LADO_MAXIMO) -> Image.Image:
    if max(img.size) <= lado:
        return img
    copia = img.copy()
    copia.thumbnail((lado, lado), Image.Resampling.LANCZOS)
    return copia


def a_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------- biblioteca
class BibliotecaFirmas:
    """Firmas guardadas como PNG en %APPDATA%\\PDFStudio\\firmas."""

    def __init__(self, carpeta: Path | None = None):
        self.carpeta = Path(carpeta) if carpeta is not None else config.DIR_FIRMAS

    def listar(self) -> list[Path]:
        if not self.carpeta.is_dir():
            return []
        return sorted(self.carpeta.glob("*.png"), key=lambda p: p.stat().st_mtime)

    @staticmethod
    def _limpiar_nombre(nombre: str) -> str:
        limpio = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", nombre).strip().strip(".")
        return limpio[:80] or "firma"

    def _ruta_libre(self, nombre: str) -> Path:
        base = self._limpiar_nombre(nombre)
        ruta = self.carpeta / f"{base}.png"
        n = 2
        while ruta.exists():
            ruta = self.carpeta / f"{base} ({n}).png"
            n += 1
        return ruta

    def agregar(self, img: Image.Image, nombre: str) -> Path:
        self.carpeta.mkdir(parents=True, exist_ok=True)
        ruta = self._ruta_libre(nombre)
        limitar_tamano(img.convert("RGBA")).save(ruta, "PNG", optimize=True)
        return ruta

    def eliminar(self, ruta: Path) -> None:
        ruta = Path(ruta)
        if ruta.parent.resolve() != self.carpeta.resolve():
            raise ValueError("La firma no pertenece a la biblioteca.")
        ruta.unlink(missing_ok=True)

    def renombrar(self, ruta: Path, nombre: str) -> Path:
        ruta = Path(ruta)
        nueva = self._ruta_libre(nombre)
        ruta.rename(nueva)
        return nueva


# ---------------------------------------------------------------------- inserción
Punto = tuple[float, float]


def _num(v: float) -> str:
    texto = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if texto in ("-0", "") else texto


_MATRIZ_CM = re.compile(rb"(?:[-+]?\d*\.?\d+\s+){6}cm")


class InsertarImagen(Operacion):
    """Inserta una imagen (PNG/JPG) en una página.

    `esquinas` son las esquinas superior-izquierda, superior-derecha e
    inferior-izquierda de la imagen TAL COMO SE VE, expresadas en coordenadas
    de la página sin rotar. Con eso se arma la matriz exacta: cualquier
    posición, escala o rotación, y la imagen queda derecha aun si la página
    tiene /Rotate.
    """

    nombre = "Insertar imagen"

    def __init__(self, pagina: int, esquinas: tuple[Punto, Punto, Punto], datos: bytes,
                 descripcion: str = "firma"):
        self.pagina = pagina
        self.esquinas = tuple((float(x), float(y)) for x, y in esquinas)
        self.datos = datos
        self._descripcion = descripcion

    def matriz_pdf(self, pag: pymupdf.Page) -> tuple[float, ...]:
        """Matriz 'cm' que lleva el cuadrado unitario de la imagen a su lugar."""
        a_pdf = ~pag.transformation_matrix
        sup_izq, sup_der, inf_izq = (pymupdf.Point(p) * a_pdf for p in self.esquinas)
        # En el espacio de imagen PDF, (0,0) es la esquina inferior izquierda
        ux = sup_der - sup_izq          # borde superior = borde inferior
        uy = sup_izq - inf_izq
        return (ux.x, ux.y, uy.x, uy.y, inf_izq.x, inf_izq.y)

    def caja(self) -> pymupdf.Rect:
        (x0, y0), (x1, y1), (x2, y2) = self.esquinas
        x3, y3 = x1 + x2 - x0, y1 + y2 - y0
        return pymupdf.Rect(min(x0, x1, x2, x3), min(y0, y1, y2, y3),
                            max(x0, x1, x2, x3), max(y0, y1, y2, y3))

    def aplicar(self, doc: pymupdf.Document) -> None:
        pag = doc[self.pagina]
        caja = self.caja()
        area = pag.rect * pag.derotation_matrix      # la página en coordenadas sin rotar
        if caja.is_empty or not caja.intersects(area):
            raise ErrorDocumento("La imagen quedó fuera de la página.")
        independizar_recursos(doc, self.pagina)   # no tocar recursos de otras páginas
        antes = set(pag.get_contents())
        # La caja solo sirve para registrar la imagen; la matriz real se escribe abajo
        pag.insert_image(pymupdf.Rect(caja) & area, stream=self.datos, keep_proportion=False,
                         overlay=True)
        nuevos = [x for x in pag.get_contents() if x not in antes]
        if len(nuevos) != 1:
            raise ErrorDocumento("No se pudo ubicar la imagen insertada en la página.")
        xref = nuevos[0]
        contenido = doc.xref_stream(xref)
        if len(_MATRIZ_CM.findall(contenido)) != 1:
            raise ErrorDocumento("Contenido inesperado al insertar la imagen.")
        matriz = " ".join(_num(v) for v in self.matriz_pdf(pag)).encode() + b" cm"
        doc.update_stream(xref, _MATRIZ_CM.sub(matriz, contenido, count=1))

    def paginas_afectadas(self) -> set[int]:
        return {self.pagina}

    def descripcion(self) -> str:
        return f"Insertar {self._descripcion} en página {self.pagina + 1}"


def esquinas_rectangulo(centro: Punto, ancho: float, alto: float, angulo: float = 0.0,
                        rotacion_pagina: int = 0) -> tuple[Punto, Punto, Punto]:
    """Esquinas (sup-izq, sup-der, inf-izq) de una imagen centrada en `centro`, girada
    `angulo` grados en sentido horario TAL COMO SE VE, en coordenadas sin rotar de una
    página con /Rotate = rotacion_pagina. `centro` también va en coordenadas sin rotar."""
    import math
    t = math.radians(angulo - rotacion_pagina)
    c, s = math.cos(t), math.sin(t)

    def girar(dx: float, dy: float) -> Punto:
        return (centro[0] + dx * c - dy * s, centro[1] + dx * s + dy * c)

    return (girar(-ancho / 2, -alto / 2), girar(ancho / 2, -alto / 2), girar(-ancho / 2, alto / 2))
