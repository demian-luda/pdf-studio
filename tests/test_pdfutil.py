"""Copia de recursos compartidos antes de editar una página (principio 2)."""
from __future__ import annotations

import pymupdf

from pdf_studio.core.imagenes import InsertarImagen, esquinas_rectangulo
from pdf_studio.core.pdfutil import independizar_recursos
from tests.conftest import DIR_PDFS
from tests.test_firmas import imagen_dos_colores


def test_recursos_compartidos_se_copian():
    doc = pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf")
    compartido = doc.xref_get_key(doc.page_xref(0), "Resources")
    assert all(doc.xref_get_key(doc.page_xref(i), "Resources") == compartido for i in range(6))
    objeto_antes = doc.xref_object(int(compartido[1].split()[0]))
    InsertarImagen(2, esquinas_rectangulo((300, 600), 150, 60), imagen_dos_colores()).aplicar(doc)
    # La página 3 tiene recursos propios; el diccionario compartido no cambió
    assert doc.xref_get_key(doc.page_xref(2), "Resources") != compartido
    assert doc.xref_object(int(compartido[1].split()[0])) == objeto_antes
    for i in (0, 1, 3, 4, 5):
        assert doc.xref_get_key(doc.page_xref(i), "Resources") == compartido


def test_no_copia_si_ya_es_propio():
    doc = pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf")
    assert independizar_recursos(doc, 1) == 1
    assert independizar_recursos(doc, 1) == 0


def test_recursos_heredados_del_arbol():
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "Hola", fontname="helv")
    # Mueve /Resources de las páginas al nodo /Pages (herencia)
    raiz_pages = int(doc.xref_get_key(doc.pdf_catalog(), "Pages")[1].split()[0])
    recursos = doc.xref_get_key(doc.page_xref(0), "Resources")[1]
    doc.xref_set_key(raiz_pages, "Resources", recursos)
    for i in range(2):
        doc.xref_set_key(doc.page_xref(i), "Resources", "null")
    assert doc.xref_get_key(doc.page_xref(0), "Resources")[0] == "null"
    assert independizar_recursos(doc, 0) >= 1
    assert doc.xref_get_key(doc.page_xref(0), "Resources")[0] == "xref"
    assert doc.xref_get_key(doc.page_xref(1), "Resources")[0] == "null"
    assert "Hola" in doc[0].get_text()


def test_subdiccionario_compartido():
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page().insert_text((72, 72), "Hola", fontname="helv")
    fuentes = doc.xref_get_key(doc.page_xref(0), "Resources/Font")[1]
    xref_fuentes = doc.get_new_xref()
    doc.update_object(xref_fuentes, fuentes)
    for i in range(2):
        doc.xref_set_key(doc.page_xref(i), "Resources", f"<</Font {xref_fuentes} 0 R>>")
    independizar_recursos(doc, 1)
    assert doc.xref_get_key(doc.page_xref(0), "Resources/Font") == ("xref", f"{xref_fuentes} 0 R")
    assert doc.xref_get_key(doc.page_xref(1), "Resources/Font") != ("xref", f"{xref_fuentes} 0 R")
