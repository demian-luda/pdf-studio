"""Comparar dos versiones de un PDF.

- Texto: diferencia palabra por palabra en todo el documento (las palabras agregadas
  y quitadas, con su lugar en cada página).
- Visual: diferencia de píxeles página por página, agrupada en zonas rectangulares.
- Informe: un PDF nuevo con un resumen y, por cada página con cambios, las dos versiones
  lado a lado (quitado en rojo, agregado en verde, cambio visual en naranja) y la lista
  de cambios de texto.

Ninguno de los dos archivos se modifica.
"""
from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pymupdf

from pdf_studio.config import DIR_FUENTES_INCLUIDAS
from pdf_studio.core.documento import ErrorDocumento

DPI_VISUAL = 72
CELDA = 6               # px: las diferencias se agrupan en celdas de este lado
TOLERANCIA = 8          # diferencia mínima por canal para contar un píxel (ignora el suavizado)
MAX_PALABRAS_GLOBAL = 60_000

VERDE = (0.09, 0.55, 0.30)
ROJO = (0.80, 0.15, 0.12)
NARANJA = (0.95, 0.55, 0.05)


@dataclass
class CambioTexto:
    tipo: str                       # "agregado" o "quitado"
    texto: str
    pagina: int                     # página del documento donde está (A si quitado, B si agregado)
    rects: list[tuple] = field(default_factory=list)      # una caja por línea, sin rotar


@dataclass
class DiferenciaVisual:
    pagina: int
    zonas: list[tuple]              # cajas sin rotar, en puntos
    porcentaje: float


@dataclass
class ResultadoComparacion:
    nombre_a: str
    nombre_b: str
    paginas_a: int
    paginas_b: int
    cambios: list[CambioTexto] = field(default_factory=list)
    visuales: list[DiferenciaVisual] = field(default_factory=list)
    tamanos_distintos: list[int] = field(default_factory=list)

    @property
    def agregadas(self) -> int:
        return sum(len(c.texto.split()) for c in self.cambios if c.tipo == "agregado")

    @property
    def quitadas(self) -> int:
        return sum(len(c.texto.split()) for c in self.cambios if c.tipo == "quitado")

    @property
    def iguales(self) -> bool:
        return (not self.cambios and not self.visuales and not self.tamanos_distintos
                and self.paginas_a == self.paginas_b)

    def paginas_con_cambios(self) -> list[int]:
        paginas = {c.pagina for c in self.cambios} | {v.pagina for v in self.visuales}
        paginas |= set(self.tamanos_distintos)
        paginas |= set(range(min(self.paginas_a, self.paginas_b), max(self.paginas_a, self.paginas_b)))
        return sorted(paginas)

    def cambios_de(self, pagina: int, tipo: str | None = None) -> list[CambioTexto]:
        return [c for c in self.cambios if c.pagina == pagina and (tipo is None or c.tipo == tipo)]

    def visual_de(self, pagina: int) -> DiferenciaVisual | None:
        return next((v for v in self.visuales if v.pagina == pagina), None)

    def resumen(self) -> str:
        if self.iguales:
            return "Los dos documentos son iguales: mismo texto y mismo aspecto en todas las páginas."
        partes = []
        if self.paginas_a != self.paginas_b:
            partes.append(f"Cantidad de páginas: {self.paginas_a} → {self.paginas_b}.")
        partes.append(f"Texto: {self.agregadas} palabra{'s' if self.agregadas != 1 else ''} "
                      f"agregada{'s' if self.agregadas != 1 else ''} y {self.quitadas} "
                      f"quitada{'s' if self.quitadas != 1 else ''}.")
        if self.visuales:
            partes.append(f"Aspecto: cambia en {len(self.visuales)} página{'s' if len(self.visuales) != 1 else ''}.")
        else:
            partes.append("Aspecto: sin diferencias de píxeles en las páginas comunes.")
        if self.tamanos_distintos:
            partes.append("Páginas con otro tamaño: " + ", ".join(str(p + 1) for p in self.tamanos_distintos) + ".")
        return " ".join(partes)

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "ResultadoComparacion":
        return cls(d["nombre_a"], d["nombre_b"], d["paginas_a"], d["paginas_b"],
                   [CambioTexto(c["tipo"], c["texto"], c["pagina"], [tuple(r) for r in c["rects"]])
                    for c in d["cambios"]],
                   [DiferenciaVisual(v["pagina"], [tuple(z) for z in v["zonas"]], v["porcentaje"])
                    for v in d["visuales"]],
                   list(d["tamanos_distintos"]))


# ---------------------------------------------------------------------- apertura
def abrir(ruta: Path, contrasena: str | None = None) -> pymupdf.Document:
    from pdf_studio.core.documento import validar_archivo
    ruta = Path(ruta)
    validar_archivo(ruta)
    try:
        doc = pymupdf.open(ruta, filetype="pdf")
    except Exception as e:
        raise ErrorDocumento(f"«{ruta.name}» está dañado y no se puede abrir ({type(e).__name__}).") from e
    if doc.needs_pass and not doc.authenticate(contrasena or ""):
        doc.close()
        raise ErrorDocumento(f"«{ruta.name}» está protegido con contraseña.")
    return doc


# ---------------------------------------------------------------------- texto
Palabra = tuple[str, int, tuple]          # (texto, página, caja)


def _palabras(doc: pymupdf.Document, paginas: range | None = None) -> list[Palabra]:
    lista = []
    for p in paginas if paginas is not None else range(doc.page_count):
        try:
            palabras = doc[p].get_text("words", sort=True)
        except Exception:
            continue
        lista.extend((w[4], p, tuple(w[:4])) for w in palabras if w[4].strip())
    return lista


def _agrupar(palabras: list[Palabra], tipo: str) -> list[CambioTexto]:
    """Palabras consecutivas → un cambio por página, con una caja por línea."""
    cambios: list[CambioTexto] = []
    for texto, pagina, caja in palabras:
        actual = cambios[-1] if cambios and cambios[-1].pagina == pagina else None
        if actual is None:
            actual = CambioTexto(tipo, texto, pagina, [caja])
            cambios.append(actual)
            continue
        actual.texto += " " + texto
        ultima = pymupdf.Rect(actual.rects[-1])
        r = pymupdf.Rect(caja)
        if abs(r.y0 - ultima.y0) < max(2.0, ultima.height * 0.5) and r.x0 >= ultima.x0 - 1:
            actual.rects[-1] = tuple(ultima | r)          # misma línea
        else:
            actual.rects.append(caja)
    return cambios


def _diferencias(a: list[Palabra], b: list[Palabra]) -> list[CambioTexto]:
    comparador = difflib.SequenceMatcher(None, [w[0] for w in a], [w[0] for w in b], autojunk=False)
    cambios = []
    for operacion, i1, i2, j1, j2 in comparador.get_opcodes():
        if operacion in ("replace", "delete"):
            cambios.extend(_agrupar(a[i1:i2], "quitado"))
        if operacion in ("replace", "insert"):
            cambios.extend(_agrupar(b[j1:j2], "agregado"))
    return cambios


def comparar_texto(doc_a: pymupdf.Document, doc_b: pymupdf.Document) -> list[CambioTexto]:
    a, b = _palabras(doc_a), _palabras(doc_b)
    if len(a) + len(b) <= MAX_PALABRAS_GLOBAL:
        return _diferencias(a, b)
    # Documentos enormes: página contra página (más rápido; no detecta texto que se movió de página)
    cambios = []
    for p in range(max(doc_a.page_count, doc_b.page_count)):
        pa = _palabras(doc_a, range(p, p + 1)) if p < doc_a.page_count else []
        pb = _palabras(doc_b, range(p, p + 1)) if p < doc_b.page_count else []
        cambios.extend(_diferencias(pa, pb))
    return cambios


# ---------------------------------------------------------------------- visual
def _zonas(mascara: np.ndarray) -> list[tuple[int, int, int, int]]:
    """Agrupa los píxeles distintos en rectángulos (en píxeles): celdas vecinas se unen."""
    alto, ancho = mascara.shape
    filas, columnas = -(-alto // CELDA), -(-ancho // CELDA)
    relleno = np.zeros((filas * CELDA, columnas * CELDA), dtype=bool)
    relleno[:alto, :ancho] = mascara
    celdas = relleno.reshape(filas, CELDA, columnas, CELDA).any(axis=(1, 3))
    zonas = []
    vistas = np.zeros_like(celdas)
    for f, c in zip(*np.nonzero(celdas)):
        if vistas[f, c]:
            continue
        pila = [(f, c)]
        vistas[f, c] = True
        f0 = f1 = f
        c0 = c1 = c
        while pila:
            y, x = pila.pop()
            f0, f1, c0, c1 = min(f0, y), max(f1, y), min(c0, x), max(c1, x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < filas and 0 <= nx < columnas and celdas[ny, nx] and not vistas[ny, nx]:
                        vistas[ny, nx] = True
                        pila.append((ny, nx))
        zonas.append((c0 * CELDA, f0 * CELDA, min(ancho, (c1 + 1) * CELDA), min(alto, (f1 + 1) * CELDA)))
    return zonas


def comparar_pagina_visual(pag_a: pymupdf.Page, pag_b: pymupdf.Page, dpi: int = DPI_VISUAL
                           ) -> DiferenciaVisual | None:
    pix_a = pag_a.get_pixmap(dpi=dpi, alpha=False, annots=True)
    pix_b = pag_b.get_pixmap(dpi=dpi, alpha=False, annots=True)
    if (pix_a.width, pix_a.height) != (pix_b.width, pix_b.height):
        raise ValueError("tamaño distinto")
    if pix_a.samples_mv == pix_b.samples_mv:
        return None
    a = np.frombuffer(pix_a.samples_mv, dtype=np.uint8).reshape(pix_a.height, pix_a.width, pix_a.n)
    b = np.frombuffer(pix_b.samples_mv, dtype=np.uint8).reshape(pix_b.height, pix_b.width, pix_b.n)
    mascara = np.any(np.abs(a.astype(np.int16) - b.astype(np.int16)) > TOLERANCIA, axis=2)
    if not mascara.any():
        return None
    escala = 72 / dpi
    zonas = []
    for x0, y0, x1, y1 in _zonas(mascara):
        visto = pymupdf.Rect(x0 * escala, y0 * escala, x1 * escala, y1 * escala)
        caja = visto * pag_b.derotation_matrix
        caja.normalize()
        zonas.append(tuple(caja))
    return DiferenciaVisual(pag_b.number, zonas, float(100.0 * mascara.sum() / mascara.size))


def comparar(doc_a: pymupdf.Document, doc_b: pymupdf.Document, nombre_a: str = "A", nombre_b: str = "B"
             ) -> ResultadoComparacion:
    resultado = ResultadoComparacion(nombre_a, nombre_b, doc_a.page_count, doc_b.page_count)
    resultado.cambios = comparar_texto(doc_a, doc_b)
    for p in range(min(doc_a.page_count, doc_b.page_count)):
        try:
            diferencia = comparar_pagina_visual(doc_a[p], doc_b[p])
        except ValueError:
            resultado.tamanos_distintos.append(p)
            continue
        if diferencia is not None:
            resultado.visuales.append(diferencia)
    return resultado


def comparar_archivos(ruta_a: Path, ruta_b: Path, contrasena_a: str | None = None,
                      contrasena_b: str | None = None, nombre_a: str | None = None,
                      nombre_b: str | None = None) -> ResultadoComparacion:
    with abrir(ruta_a, contrasena_a) as a, abrir(ruta_b, contrasena_b) as b:
        return comparar(a, b, nombre_a or Path(ruta_a).name, nombre_b or Path(ruta_b).name)


# ---------------------------------------------------------------------- informe
_FUENTE = DIR_FUENTES_INCLUIDAS / "LiberationSans-Regular.ttf"
_FUENTE_NEGRITA = DIR_FUENTES_INCLUIDAS / "LiberationSans-Bold.ttf"


def _escribir(pag: pymupdf.Page, rect: pymupdf.Rect, texto: str, tamano: float = 10, negrita: bool = False,
              color=(0.1, 0.1, 0.1)) -> float:
    nombre = "LSB" if negrita else "LS"
    archivo = _FUENTE_NEGRITA if negrita else _FUENTE
    sobra = pag.insert_textbox(rect, texto, fontsize=tamano, fontname=nombre, fontfile=str(archivo), color=color)
    return sobra


def _pegar_pagina(informe: pymupdf.Page, destino: pymupdf.Rect, pag: pymupdf.Page,
                  marcas: list[tuple[list[tuple], tuple]]) -> None:
    """Dibuja la página (como imagen de referencia) dentro de `destino` y encima las marcas
    [(cajas sin rotar, color)]."""
    pix = pag.get_pixmap(dpi=110, alpha=False, annots=True)
    escala = min(destino.width / pag.rect.width, destino.height / pag.rect.height)
    ancho, alto = pag.rect.width * escala, pag.rect.height * escala
    caja = pymupdf.Rect(destino.x0, destino.y0, destino.x0 + ancho, destino.y0 + alto)
    informe.insert_image(caja, stream=pix.tobytes("jpeg", jpg_quality=85))   # JPEG: informe liviano
    informe.draw_rect(caja, color=(0.6, 0.6, 0.6), width=0.5)
    matriz = pag.rotation_matrix * pymupdf.Matrix(escala, escala) * pymupdf.Matrix(1, 0, 0, 1, caja.x0, caja.y0)
    for cajas, color in marcas:
        for r in cajas:
            visto = pymupdf.Rect(r) * matriz
            visto.normalize()
            informe.draw_rect(visto + (-1, -1, 1, 1), color=color, fill=color, fill_opacity=0.18, width=0.8)


def exportar_informe(resultado: ResultadoComparacion, ruta_a: Path, ruta_b: Path, destino: Path,
                     contrasena_a: str | None = None, contrasena_b: str | None = None) -> int:
    """Escribe el informe en `destino`. Devuelve la cantidad de páginas del informe."""
    informe = pymupdf.open()
    ancho, alto = pymupdf.paper_size("a4-l")
    try:
        with abrir(ruta_a, contrasena_a) as a, abrir(ruta_b, contrasena_b) as b:
            portada = informe.new_page(width=ancho, height=alto)
            margen = 40
            y = margen
            _escribir(portada, pymupdf.Rect(margen, y, ancho - margen, y + 30), "Informe de diferencias", 20, True)
            y += 40
            fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
            texto = (f"Versión A (antes): {resultado.nombre_a} — {resultado.paginas_a} páginas\n"
                     f"Versión B (después): {resultado.nombre_b} — {resultado.paginas_b} páginas\n"
                     f"Generado el {fecha} con PDF Studio.\n\n{resultado.resumen()}")
            _escribir(portada, pymupdf.Rect(margen, y, ancho - margen, y + 140), texto, 11)
            y += 150
            for color, leyenda in ((ROJO, "Texto quitado (en la versión A)"), (VERDE, "Texto agregado (en la versión B)"),
                                   (NARANJA, "Cambio visual (diferencia de píxeles)")):
                portada.draw_rect(pymupdf.Rect(margen, y + 2, margen + 14, y + 14), color=color, fill=color,
                                  fill_opacity=0.25, width=0.8)
                _escribir(portada, pymupdf.Rect(margen + 22, y, ancho - margen, y + 18), leyenda, 10)
                y += 20
            paginas = resultado.paginas_con_cambios()
            if paginas:
                _escribir(portada, pymupdf.Rect(margen, y + 10, ancho - margen, alto - margen),
                          "Páginas con cambios: " + ", ".join(str(p + 1) for p in paginas), 10)
            for p in paginas:
                hoja = informe.new_page(width=ancho, height=alto)
                _escribir(hoja, pymupdf.Rect(margen, 20, ancho - margen, 44), f"Página {p + 1}", 14, True)
                columna = (ancho - 3 * margen) / 2
                arriba = 50
                abajo = alto - 150
                visual = resultado.visual_de(p)
                zonas = visual.zonas if visual else []
                for lado, (doc, tipo, color, x0, etiqueta) in enumerate((
                        (a, "quitado", ROJO, margen, f"A: {resultado.nombre_a}"),
                        (b, "agregado", VERDE, 2 * margen + columna, f"B: {resultado.nombre_b}"))):
                    _escribir(hoja, pymupdf.Rect(x0, arriba - 2, x0 + columna, arriba + 14), etiqueta, 9,
                              color=(0.35, 0.35, 0.35))
                    zona = pymupdf.Rect(x0, arriba + 16, x0 + columna, abajo)
                    if p < doc.page_count:
                        marcas = [([r for c in resultado.cambios_de(p, tipo) for r in c.rects], color),
                                  (zonas, NARANJA)]
                        _pegar_pagina(hoja, zona, doc[p], marcas)
                    else:
                        _escribir(hoja, zona, "(esta versión no tiene esta página)", 11, color=(0.4, 0.4, 0.4))
                # Lista de cambios de texto: quitado en rojo, agregado en verde
                lineas = [(("− " if c.tipo == "quitado" else "+ ") + c.texto, ROJO if c.tipo == "quitado" else VERDE)
                          for c in resultado.cambios_de(p)]
                if p in resultado.tamanos_distintos:
                    lineas.insert(0, ("La página cambió de tamaño.", NARANJA))
                y = abajo + 8
                for n, (linea, color) in enumerate(lineas):
                    if n == 11 and len(lineas) > 12:
                        _escribir(hoja, pymupdf.Rect(margen, y, ancho - margen, y + 14),
                                  f"… y {len(lineas) - 11} cambios más.", 8.5, color=(0.35, 0.35, 0.35))
                        break
                    _escribir(hoja, pymupdf.Rect(margen, y, ancho - margen, y + 14),
                              linea if len(linea) < 170 else linea[:167] + "…", 8.5, color=color)
                    y += 11
        informe.subset_fonts()
        destino = Path(destino)
        temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
        informe.save(temporal, garbage=3, deflate=True)
        temporal.replace(destino)
        return informe.page_count
    finally:
        informe.close()
