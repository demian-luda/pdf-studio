"""SOPs sintéticos para probar «Convertir escaneado a documento editable».

`crear_sop()` arma un PDF tipo procedimiento operativo estándar (encabezado con logo,
código y versión en una tabla, títulos numerados, párrafos de varias líneas, listas con
viñetas y numeradas, una tabla de materiales, una tabla de aprobaciones con firmas y un
sello) y devuelve también lo que el conversor debería recuperar. `escanear()` lo convierte
en un PDF de imágenes como si hubiera pasado por un escáner: rasterizado, apenas girado,
con ruido y compresión JPEG. No hay datos reales: todo el texto es inventado.
"""
from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

CODIGO = "SOP-QC-014"
VERSION = "Versión: 03"
TITULO_ENCABEZADO = "PROCEDIMIENTO OPERATIVO ESTÁNDAR"
SUBTITULO_ENCABEZADO = "Limpieza de equipos de laboratorio"
TITULO = "LIMPIEZA DE EQUIPOS DE LABORATORIO"

SECCIONES_1 = [
    ("1. OBJETIVO", "Establecer el método de limpieza y desinfección de los equipos del laboratorio de control "
                    "de calidad, para evitar la contaminación cruzada entre muestras y asegurar resultados "
                    "confiables en todos los ensayos que se realizan durante la jornada."),
    ("2. ALCANCE", "Este procedimiento se aplica a todos los equipos de uso compartido del sector, incluidas "
                   "las balanzas analíticas, los agitadores magnéticos y las campanas de flujo laminar."),
]
RESPONSABILIDADES = ["El analista limpia el equipo después de cada uso.",
                     "El supervisor verifica el registro de limpieza al final de cada turno y firma la planilla "
                     "correspondiente.",
                     "El responsable de calidad revisa este procedimiento una vez por año."]
PASOS = ["Apagar y desconectar el equipo antes de comenzar.",
         "Retirar los restos de muestra con un paño seco y descartarlo en el recipiente indicado para residuos.",
         "Aplicar la solución de limpieza y dejar actuar durante cinco minutos.",
         "Registrar la fecha, la hora y el nombre del analista en la planilla."]
MATERIALES = [("Material", "Cantidad", "Observaciones"),
              ("Paños descartables", "10", "Sin pelusa"),
              ("Alcohol al 70 %", "500 ml", "Renovar cada semana"),
              ("Guantes de nitrilo", "2 pares", "Talle según usuario")]
APROBACIONES = [("Elaboró", "Revisó", "Aprobó"), ("", "", ""),
                ("Ana Gómez", "Luis Pérez", "Marta Díaz")]

ANCHO, ALTO = pymupdf.paper_size("a4")
MARGEN = 62.0
TAMANO_CUERPO = 10.5
TAMANO_SECCION = 12.0
TAMANO_TITULO = 16.0


@dataclass
class Esperado:
    """Lo que tiene que recuperar la conversión."""
    paginas: int
    titulos: list[str] = field(default_factory=list)
    parrafos: list[str] = field(default_factory=list)
    vinetas: list[str] = field(default_factory=list)
    numerados: list[str] = field(default_factory=list)
    tablas: list[list[tuple[str, ...]]] = field(default_factory=list)
    encabezado: list[str] = field(default_factory=list)
    pie: str = "Página # de #"
    firmas: int = 3
    sellos: int = 1

    def palabras(self) -> list[str]:
        """Las palabras del cuerpo (sin encabezado ni pie)."""
        textos = self.titulos + self.parrafos + self.vinetas + self.numerados
        textos += [c for t in self.tablas for fila in t for c in fila]
        return [p for t in textos for p in t.split()]


def _partir(texto: str, fuente: str, tamano: float, ancho: float) -> list[str]:
    lineas, actual = [], ""
    for palabra in texto.split():
        prueba = f"{actual} {palabra}".strip()
        if pymupdf.get_text_length(prueba, fontname=fuente, fontsize=tamano) <= ancho or not actual:
            actual = prueba
        else:
            lineas.append(actual)
            actual = palabra
    if actual:
        lineas.append(actual)
    return lineas


class _Pagina:
    def __init__(self, pag: pymupdf.Page, regular: str, negrita: str):
        self.pag, self.regular, self.negrita = pag, regular, negrita
        self.y = 0.0

    def texto(self, x: float, y: float, texto: str, tamano: float = TAMANO_CUERPO, negrita: bool = False,
              color=(0, 0, 0)) -> None:
        self.pag.insert_text((x, y), texto, fontname=self.negrita if negrita else self.regular, fontsize=tamano,
                             color=color)

    def ancho(self, texto: str, tamano: float, negrita: bool = False) -> float:
        return pymupdf.get_text_length(texto, fontname=self.negrita if negrita else self.regular, fontsize=tamano)

    def parrafo(self, texto: str, x: float = MARGEN, ancho: float = ANCHO - 2 * MARGEN,
                tamano: float = TAMANO_CUERPO, interlineado: float = 1.35) -> None:
        for linea in _partir(texto, self.regular, tamano, ancho):
            self.y += tamano * interlineado
            self.texto(x, self.y, linea, tamano)

    def seccion(self, texto: str) -> None:
        self.y += TAMANO_SECCION * 2.0
        self.texto(MARGEN, self.y, texto, TAMANO_SECCION, negrita=True)
        self.y += 4

    def lista(self, items: list[str], numerada: bool) -> None:
        sangria = MARGEN + 16
        for n, item in enumerate(items, 1):
            primera = True
            for linea in _partir(item, self.regular, TAMANO_CUERPO, ANCHO - MARGEN - sangria - 14):
                self.y += TAMANO_CUERPO * 1.35
                if primera:
                    if numerada:
                        self.texto(sangria - 2, self.y, f"{n}.")
                    else:
                        self.pag.draw_circle((sangria + 3, self.y - TAMANO_CUERPO * 0.33), 1.9, color=(0, 0, 0),
                                             fill=(0, 0, 0))
                    primera = False
                self.texto(sangria + 14, self.y, linea)
            self.y += 2

    def tabla(self, filas: list[tuple[str, ...]], anchos: list[float], alto_fila: float = 22.0,
              altos: list[float] | None = None, negrita_primera: bool = True, x0: float = MARGEN) -> list[pymupdf.Rect]:
        """Dibuja la grilla y el texto; devuelve las celdas (por filas)."""
        celdas = []
        y = self.y
        for i, fila in enumerate(filas):
            alto = altos[i] if altos else alto_fila
            x = x0
            for j, texto in enumerate(fila):
                r = pymupdf.Rect(x, y, x + anchos[j], y + alto)
                celdas.append(r)
                if texto:
                    self.texto(r.x0 + 6, r.y1 - (alto - TAMANO_CUERPO) / 2 - 2.5, texto,
                               negrita=negrita_primera and i == 0)
                x += anchos[j]
            y += alto
        forma = self.pag.new_shape()
        for r in celdas:
            forma.draw_rect(r)
        forma.finish(color=(0, 0, 0), width=0.8)
        forma.commit()
        self.y = y
        return celdas


def _firma(pag: pymupdf.Page, caja: pymupdf.Rect, semilla: int) -> None:
    """Un garabato a mano alzada (curvas) dentro de la caja."""
    rng = np.random.default_rng(semilla)
    forma = pag.new_shape()
    cx, cy = caja.x0 + caja.width * 0.15, caja.y0 + caja.height * 0.6
    puntos = []
    for t in np.linspace(0, 1, 40):
        x = cx + t * caja.width * 0.7
        y = cy - math.sin(t * math.pi * (5 + semilla)) * caja.height * 0.28 - t * caja.height * 0.1
        puntos.append(pymupdf.Point(x + rng.normal(0, 0.6), y + rng.normal(0, 0.6)))
    for a, b in zip(puntos, puntos[1:]):
        forma.draw_line(a, b)
    forma.draw_bezier(puntos[-1], puntos[-1] + (8, 6), puntos[0] + (-4, 10), puntos[0] + (20, 4))
    forma.finish(color=(0.05, 0.1, 0.35), width=1.3, closePath=False)
    forma.commit()


def _sello(pag: pymupdf.Page, centro: pymupdf.Point, radio: float, fuente: str) -> None:
    color = (0.15, 0.25, 0.75)
    pag.draw_circle(centro, radio, color=color, width=2.2)
    pag.draw_circle(centro, radio - 5, color=color, width=0.9)
    texto = "APROBADO"
    tamano = 9.0
    ancho = pymupdf.get_text_length(texto, fontname=fuente, fontsize=tamano)
    pag.insert_text((centro.x - ancho / 2, centro.y + 3), texto, fontname=fuente, fontsize=tamano, color=color)
    pag.draw_line(centro + (-radio + 9, -9), centro + (radio - 9, -9), color=color, width=0.9)
    pag.draw_line(centro + (-radio + 9, 9), centro + (radio - 9, 9), color=color, width=0.9)


def _logo(pag: pymupdf.Page, caja: pymupdf.Rect) -> None:
    c = (caja.tl + caja.br) / 2
    pag.draw_circle(c, min(caja.width, caja.height) * 0.36, color=(0.1, 0.45, 0.3), fill=(0.1, 0.55, 0.35))
    tri = [c + (-10, 8), c + (10, 8), c + (0, -11)]
    pag.draw_polyline(tri + [tri[0]], color=(1, 1, 1), fill=(1, 1, 1), width=1)


def _encabezado(p: _Pagina, numero: int, total: int) -> None:
    anchos = [90.0, 255.0, ANCHO - 2 * MARGEN - 345.0]
    x = [MARGEN, MARGEN + anchos[0], MARGEN + anchos[0] + anchos[1], ANCHO - MARGEN]
    y0, y1, y2 = 36.0, 62.0, 88.0
    forma = p.pag.new_shape()
    forma.draw_rect(pymupdf.Rect(x[0], y0, x[3], y2))
    forma.draw_line((x[1], y0), (x[1], y2))
    forma.draw_line((x[2], y0), (x[2], y2))
    forma.draw_line((x[1], y1), (x[3], y1))
    forma.finish(color=(0, 0, 0), width=0.8)
    forma.commit()
    _logo(p.pag, pymupdf.Rect(x[0], y0, x[1], y2))
    p.texto(x[1] + 6, y1 - 8, TITULO_ENCABEZADO, 10, negrita=True)
    p.texto(x[1] + 6, y2 - 8, SUBTITULO_ENCABEZADO, 10)
    p.texto(x[2] + 6, y1 - 8, f"Código: {CODIGO}", 9.5)
    p.texto(x[2] + 6, y2 - 8, VERSION, 9.5)
    pie = f"Página {numero} de {total}"
    p.texto((ANCHO - p.ancho(pie, 9)) / 2, ALTO - 34, pie, 9)


def crear_sop(destino: Path, serif: bool = False) -> Esperado:
    """Escribe el SOP de dos páginas en `destino` y devuelve lo esperado."""
    regular, negrita = ("tiro", "tibo") if serif else ("helv", "hebo")
    doc = pymupdf.open()
    esperado = Esperado(2, encabezado=[TITULO_ENCABEZADO, SUBTITULO_ENCABEZADO, f"Código: {CODIGO}", VERSION])

    # ---------------------------------------------------------- página 1
    p = _Pagina(doc.new_page(width=ANCHO, height=ALTO), regular, negrita)
    _encabezado(p, 1, 2)
    p.y = 128
    p.texto((ANCHO - p.ancho(TITULO, TAMANO_TITULO, True)) / 2, p.y, TITULO, TAMANO_TITULO, negrita=True)
    esperado.titulos.append(TITULO)
    p.y += 6
    for titulo, texto in SECCIONES_1:
        p.seccion(titulo)
        p.parrafo(texto)
        esperado.titulos.append(titulo)
        esperado.parrafos.append(texto)
    p.seccion("3. RESPONSABILIDADES")
    esperado.titulos.append("3. RESPONSABILIDADES")
    p.lista(RESPONSABILIDADES, numerada=False)
    esperado.vinetas += RESPONSABILIDADES
    p.seccion("4. PROCEDIMIENTO")
    esperado.titulos.append("4. PROCEDIMIENTO")
    p.lista(PASOS, numerada=True)
    esperado.numerados += PASOS

    # ---------------------------------------------------------- página 2
    p = _Pagina(doc.new_page(width=ANCHO, height=ALTO), regular, negrita)
    _encabezado(p, 2, 2)
    p.y = 110
    p.seccion("5. MATERIALES")
    esperado.titulos.append("5. MATERIALES")
    p.y += 8
    p.tabla(MATERIALES, [160.0, 100.0, ANCHO - 2 * MARGEN - 260.0])
    esperado.tablas.append(MATERIALES)
    p.seccion("6. APROBACIONES")
    esperado.titulos.append("6. APROBACIONES")
    p.y += 8
    ancho_col = (ANCHO - 2 * MARGEN) / 3
    celdas = p.tabla(APROBACIONES, [ancho_col] * 3, altos=[22.0, 58.0, 22.0])
    esperado.tablas.append(APROBACIONES)
    for i, caja in enumerate(celdas[3:6]):
        _firma(p.pag, caja + (10, 6, -10, -6), i)
    p.y += 30
    p.parrafo("Este documento es una copia controlada. Toda modificación debe pasar por el circuito de "
              "aprobación del área de calidad antes de su distribución.")
    esperado.parrafos.append("Este documento es una copia controlada. Toda modificación debe pasar por el circuito "
                             "de aprobación del área de calidad antes de su distribución.")
    _sello(p.pag, pymupdf.Point(ANCHO - MARGEN - 60, p.y + 70), 42, negrita)
    doc.set_metadata({"producer": "pruebas PDF Studio"})
    doc.save(destino)
    doc.close()
    return esperado


def escanear(origen: Path, destino: Path, dpi: int = 200, angulo: float = 0.6, ruido: float = 7.0,
             semilla: int = 3, calidad: int = 80) -> None:
    """Simula un escaneo: cada página se rasteriza, se gira un poco, se le agrega ruido y
    se guarda como JPEG dentro de un PDF de solo imágenes."""
    import cv2
    from PIL import Image

    rng = np.random.default_rng(semilla)
    with pymupdf.open(origen) as doc, pymupdf.open() as salida:
        for n, pag in enumerate(doc):
            pix = pag.get_pixmap(dpi=dpi)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3].copy()
            h, w = img.shape[:2]
            giro = angulo if n % 2 == 0 else -angulo * 0.7
            m = cv2.getRotationMatrix2D((w / 2, h / 2), giro, 1.0)
            img = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))
            img = cv2.GaussianBlur(img, (3, 3), 0.6)
            # Papel no del todo blanco y ruido del sensor
            img = img.astype(np.float32) * 0.94 + 6
            img += rng.normal(0, ruido, (h, w, 1)).astype(np.float32)
            img = np.clip(img, 0, 255).astype(np.uint8)
            buf = io.BytesIO()
            Image.fromarray(img).save(buf, "JPEG", quality=calidad)
            nueva = salida.new_page(width=pag.rect.width, height=pag.rect.height)
            nueva.insert_image(nueva.rect, stream=buf.getvalue())
        salida.save(destino, garbage=3, deflate=True)
