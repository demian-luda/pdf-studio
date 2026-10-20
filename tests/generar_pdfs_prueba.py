"""Genera el set de PDFs de prueba en tests/pdfs_prueba/.

Todos los contenidos son ficticios (sin datos reales). Se pueden regenerar con:
    .\\.venv\\Scripts\\python.exe tests\\generar_pdfs_prueba.py

El PDF exportado desde Word lo agrega el usuario a mano (tests/pdfs_prueba/word.pdf).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pymupdf

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from pdf_studio.config import DIR_FUENTES_INCLUIDAS  # noqa: E402

DESTINO = Path(__file__).resolve().parent / "pdfs_prueba"
A4 = pymupdf.paper_rect("a4")

PARRAFO = ("El presupuesto anual de la cooperativa ficticia «Los Álamos» se aprobó en la asamblea "
           "de marzo. Se destinará una parte a la compra de herramientas, otra al mantenimiento "
           "del galpón y el resto a un fondo de reserva. La comisión revisará el presupuesto cada "
           "trimestre y publicará un informe para todos los socios.")


def _fuente(nombre: str) -> str:
    return str(DIR_FUENTES_INCLUIDAS / nombre)


def _escribir_parrafo(pag: pymupdf.Page, rect: pymupdf.Rect, texto: str, **kw) -> None:
    sobrante = pag.insert_textbox(rect, texto, align=pymupdf.TEXT_ALIGN_JUSTIFY, **kw)
    if sobrante < 0:
        raise RuntimeError("El texto no entra en el recuadro")


def texto_varias_paginas() -> None:
    """6 páginas con Helvetica (estándar, no embebida): visor, búsqueda, miniaturas."""
    doc = pymupdf.open()
    for i in range(6):
        pag = doc.new_page(width=A4.width, height=A4.height)
        pag.insert_text((72, 90), f"Capítulo {i + 1}: Informe de la cooperativa",
                        fontname="hebo", fontsize=18)
        for j in range(4):
            _escribir_parrafo(pag, pymupdf.Rect(72, 120 + j * 120, A4.width - 72, 230 + j * 120),
                              PARRAFO, fontname="helv", fontsize=11)
        pag.insert_text((A4.width / 2 - 20, A4.height - 40), f"Página {i + 1}", fontname="helv",
                        fontsize=9, color=(0.4, 0.4, 0.4))
    doc.save(DESTINO / "texto_varias_paginas.pdf", garbage=3, deflate=True)


def tablas() -> None:
    """Tabla con líneas vectoriales y fuente libre embebida completa."""
    doc = pymupdf.open()
    pag = doc.new_page(width=A4.width, height=A4.height)
    pag.insert_font(fontname="LibSans", fontfile=_fuente("LiberationSans-Regular.ttf"))
    pag.insert_font(fontname="LibSansB", fontfile=_fuente("LiberationSans-Bold.ttf"))
    pag.insert_text((72, 80), "Gastos del primer trimestre", fontname="LibSansB", fontsize=16)
    columnas = ["Concepto", "Enero", "Febrero", "Marzo", "Total"]
    filas = [
        ["Herramientas", "12.500", "8.300", "4.100", "24.900"],
        ["Mantenimiento", "3.200", "3.200", "5.750", "12.150"],
        ["Servicios", "7.480", "7.910", "8.020", "23.410"],
        ["Fondo de reserva", "10.000", "10.000", "10.000", "30.000"],
        ["Total", "33.180", "29.410", "27.870", "90.460"],
    ]
    x0, y0, anchos, alto = 72, 110, [150, 80, 80, 80, 80], 26
    forma = pag.new_shape()
    for f in range(len(filas) + 2):
        forma.draw_line((x0, y0 + f * alto), (x0 + sum(anchos), y0 + f * alto))
    x = x0
    for a in anchos + [0]:
        forma.draw_line((x, y0), (x, y0 + (len(filas) + 1) * alto))
        x += a
    forma.finish(color=(0.2, 0.2, 0.2), width=0.8)
    forma.draw_rect(pymupdf.Rect(x0, y0, x0 + sum(anchos), y0 + alto))
    forma.finish(fill=(0.85, 0.9, 0.97), color=None)
    forma.commit(overlay=False)
    for f, fila in enumerate([columnas] + filas):
        x = x0
        for c, celda in enumerate(fila):
            fuente = "LibSansB" if f == 0 or fila[0] == "Total" else "LibSans"
            ancho = pymupdf.get_text_length(celda, fontname="helv", fontsize=10)
            px = x + 6 if c == 0 else x + anchos[c] - 6 - ancho
            pag.insert_text((px, y0 + f * alto + 17), celda, fontname=fuente, fontsize=10)
            x += anchos[c]
    doc.save(DESTINO / "tablas.pdf", garbage=3, deflate=True)


def imagen_fondo() -> None:
    """Imagen de fondo a página completa con texto encima (no se debe rasterizar el texto)."""
    w, h = 600, 848
    yy, xx = np.mgrid[0:h, 0:w]
    r = (200 + 55 * xx / w).astype(np.uint8)
    g = (220 + 35 * yy / h).astype(np.uint8)
    b = (235 + 20 * np.sin(xx / 40.0) * np.cos(yy / 50.0)).clip(0, 255).astype(np.uint8)
    rgb = np.dstack([r, g, b]).astype(np.uint8)
    pix = pymupdf.Pixmap(pymupdf.csRGB, w, h, rgb.tobytes(), False)
    doc = pymupdf.open()
    for i in range(2):
        pag = doc.new_page(width=A4.width, height=A4.height)
        pag.insert_image(pag.rect, pixmap=pix)
        pag.insert_text((72, 100), f"Certificado de participación {i + 1}", fontname="tibo", fontsize=22)
        _escribir_parrafo(pag, pymupdf.Rect(72, 140, A4.width - 72, 300), PARRAFO,
                          fontname="tiro", fontsize=12)
    doc.save(DESTINO / "imagen_fondo.pdf", garbage=3, deflate=True)


def formulario() -> None:
    """Formulario con campo de texto, casilla y lista desplegable."""
    doc = pymupdf.open()
    pag = doc.new_page(width=A4.width, height=A4.height)
    pag.insert_text((72, 80), "Solicitud de inscripción (ejemplo)", fontname="hebo", fontsize=16)
    etiquetas = [("Nombre y apellido:", 120), ("Localidad:", 160), ("¿Acepta el reglamento?", 200),
                 ("Turno:", 240)]
    for texto, y in etiquetas:
        pag.insert_text((72, y), texto, fontname="helv", fontsize=11)
    for nombre, y in (("nombre", 120), ("localidad", 160)):
        wd = pymupdf.Widget()
        wd.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        wd.field_name = nombre
        wd.rect = pymupdf.Rect(220, y - 14, 480, y + 4)
        wd.text_fontsize = 11
        pag.add_widget(wd)
    wd = pymupdf.Widget()
    wd.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    wd.field_name = "acepta"
    wd.rect = pymupdf.Rect(220, 186, 234, 200)
    pag.add_widget(wd)
    wd = pymupdf.Widget()
    wd.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
    wd.field_name = "turno"
    wd.choice_values = ["Mañana", "Tarde", "Noche"]
    wd.field_value = "Mañana"
    wd.rect = pymupdf.Rect(220, 226, 360, 244)
    pag.add_widget(wd)
    doc.save(DESTINO / "formulario.pdf", garbage=3, deflate=True)


def escaneado() -> None:
    """Páginas que son solo una imagen (sin texto extraíble), como un escaneo."""
    origen = pymupdf.open()
    pag = origen.new_page(width=A4.width, height=A4.height)
    pag.insert_text((72, 90), "Acta de reunión (escaneada)", fontname="tibo", fontsize=18)
    _escribir_parrafo(pag, pymupdf.Rect(72, 120, A4.width - 72, 400), PARRAFO + "\n\n" + PARRAFO,
                      fontname="tiro", fontsize=12)
    pix = pag.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).astype(np.int16)
    ruido = np.random.default_rng(1234).integers(-12, 12, arr.shape)
    arr = (arr + ruido).clip(0, 255).astype(np.uint8)
    pix_ruido = pymupdf.Pixmap(pymupdf.csGRAY, pix.width, pix.height, arr.tobytes(), False)
    doc = pymupdf.open()
    for _ in range(2):
        p = doc.new_page(width=A4.width, height=A4.height)
        p.insert_image(p.rect, pixmap=pix_ruido)
    doc.save(DESTINO / "escaneado.pdf", garbage=3, deflate=True)


def fuentes_recortadas() -> None:
    """Mezcla de fuentes: subconjunto, completa, no embebida y una que simula Arial recortada.

    La "ArialMT" es en realidad Liberation Sans (métricamente igual) renombrada, para
    simular el caso típico de un PDF de Word sin incluir la fuente comercial.
    """
    doc = pymupdf.open()
    pag = doc.new_page(width=A4.width, height=A4.height)
    pag.insert_font(fontname="Serif", fontfile=_fuente("LiberationSerif-Regular.ttf"))
    pag.insert_font(fontname="SansFalsa", fontfile=_fuente("LiberationSans-Regular.ttf"))
    pag.insert_text((72, 90), "Texto en Liberation Serif (subconjunto)", fontname="Serif", fontsize=14)
    _escribir_parrafo(pag, pymupdf.Rect(72, 110, A4.width - 72, 220), PARRAFO,
                      fontname="Serif", fontsize=11)
    pag.insert_text((72, 260), "Texto que simula Arial recortada", fontname="SansFalsa", fontsize=12)
    pag.insert_text((72, 300), "Texto en Helvetica (no embebida)", fontname="helv", fontsize=12)
    doc.subset_fonts()
    # Fuente agregada después del recorte: queda embebida completa
    pag.insert_font(fontname="Mono", fontfile=_fuente("LiberationMono-Regular.ttf"))
    pag.insert_text((72, 340), "Texto en Liberation Mono (completa)", fontname="Mono", fontsize=12)
    # Renombrar la Liberation Sans recortada a "ArialMT"
    for xref, _ext, _tipo, basefont, _ref, _enc in doc.get_page_fonts(0):
        if "LiberationSans" in basefont.replace(" ", ""):
            prefijo = basefont.split("+")[0]
            nuevo = f"/{prefijo}+ArialMT"
            doc.xref_set_key(xref, "BaseFont", nuevo)
            for clave in ("DescendantFonts", "FontDescriptor"):
                tipo, valor = doc.xref_get_key(xref, clave)
                if tipo == "xref":
                    doc.xref_set_key(int(valor.split()[0]), "FontName", nuevo)
                elif tipo == "array":
                    desc = int(valor.strip("[]").split()[0])
                    doc.xref_set_key(desc, "BaseFont", nuevo)
                    t2, v2 = doc.xref_get_key(desc, "FontDescriptor")
                    if t2 == "xref":
                        doc.xref_set_key(int(v2.split()[0]), "FontName", nuevo)
    doc.save(DESTINO / "fuentes_recortadas.pdf", garbage=3, deflate=True)


def protegido() -> None:
    """PDF con contraseña de apertura "1234"."""
    doc = pymupdf.open()
    pag = doc.new_page(width=A4.width, height=A4.height)
    pag.insert_text((72, 90), "Documento protegido de ejemplo", fontname="hebo", fontsize=16)
    pag.insert_text((72, 120), "La contraseña de apertura es 1234.", fontname="helv", fontsize=11)
    doc.new_page(width=A4.width, height=A4.height).insert_text(
        (72, 90), "Segunda página protegida", fontname="helv", fontsize=12)
    doc.save(DESTINO / "protegido.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256,
             user_pw="1234", owner_pw="dueno-1234", permissions=-1)


GENERADORES = [texto_varias_paginas, tablas, imagen_fondo, formulario, escaneado,
               fuentes_recortadas, protegido]


def main() -> None:
    DESTINO.mkdir(parents=True, exist_ok=True)
    for g in GENERADORES:
        g()
        print("Generado:", g.__name__)


if __name__ == "__main__":
    main()
