"""Clasificación por cobertura real de glifos y sustitutas de la misma clase (caso DejaVuSans-Bold)."""
from __future__ import annotations

import pymupdf
import pytest

from pdf_studio import config
from pdf_studio.core.fuentes import (Clase, Embebido, IndiceFuentesSistema, analizar_fuentes, clase_tipografica,
                                     estilo_de, letras_con_glifo)
from pdf_studio.core.sustitucion import candidatas, familias_del_documento
from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en, subconjunto

TEXTO = "Texto de prueba"


@pytest.fixture(scope="module")
def incluidas(tmp_path_factory):
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path_factory.mktemp("c") / "i.json")


@pytest.fixture(scope="module")
def pdf_dejavu(tmp_path_factory):
    """Subconjunto SIN prefijo ABCDEF+, llamado DejaVuSans-Bold y marcado como serif en /Flags
    (como hacen algunos generadores). Solo tiene las letras de TEXTO: faltan 'H' y 'O'."""
    datos = subconjunto((config.DIR_FUENTES_INCLUIDAS / "LiberationSans-Bold.ttf").read_bytes(), TEXTO)
    doc = pymupdf.open()
    pag = doc.new_page()
    xref = pag.insert_font(fontname="DV", fontbuffer=datos)
    pag.insert_text((72, 100), TEXTO, fontname="DV", fontsize=14)
    desc = int(doc.xref_get_key(xref, "DescendantFonts")[1].strip("[]").split()[0])
    descriptor = int(doc.xref_get_key(desc, "FontDescriptor")[1].split()[0])
    for x in (xref, desc):
        doc.xref_set_key(x, "BaseFont", "/DejaVuSans-Bold")
    doc.xref_set_key(descriptor, "FontName", "/DejaVuSans-Bold")
    doc.xref_set_key(descriptor, "Flags", str(2 | 32 | 262144))   # Serif + Nonsymbolic + ForceBold
    ruta = tmp_path_factory.mktemp("pdf") / "dejavu.pdf"
    doc.save(ruta)
    return ruta


def test_subconjunto_sin_prefijo_se_detecta(pdf_dejavu):
    with pymupdf.open(pdf_dejavu) as doc:
        fuentes = analizar_fuentes(doc)
        assert [f.nombre_pdf for f in fuentes] == ["DejaVuSans-Bold"]       # sin prefijo
        assert fuentes[0].embebido == Embebido.SUBCONJUNTO
        letras, total = letras_con_glifo(doc.extract_font(fuentes[0].xrefs[0])[3])
        assert "H" not in letras and "O" not in letras and "T" in letras


def test_fuente_completa_sigue_completa():
    from tests.conftest import DIR_PDFS
    with pymupdf.open(DIR_PDFS / "tablas.pdf") as doc:
        assert {f.embebido for f in analizar_fuentes(doc)} == {Embebido.COMPLETA}


def test_dialogo_ya_no_dice_completa_y_faltan(pdf_dejavu, incluidas):
    with pymupdf.open(pdf_dejavu) as doc:
        pag = doc[0]
        bloque = bloque_en(pag, 0, pymupdf.Point(100, 96), Nivel.LINEA)
        analisis = analizar_fuente(doc, pag, bloque.estilo, "HOLA " + TEXTO, None, incluidas)
    assert analisis.embebido == Embebido.SUBCONJUNTO
    original = analisis.opciones[0]
    assert original.tipo == "original" and set(original.faltantes) >= {"H", "O"}
    assert analisis.clase == Clase.SANS and analisis.estilo == "bold"
    sustituta = analisis.recomendada
    assert sustituta.tipo == "sustituta"
    assert "Sans" in sustituta.nombre and "Bold" in sustituta.nombre and "Serif" not in sustituta.nombre
    # DejaVu es libre: se ofrece instalarla
    assert analisis.instalable is not None and analisis.instalable.nombre == "DejaVu Sans"
    assert "dejavu-fonts-ttf-2.37.zip" in analisis.instalable.url
    assert "DejaVuSans-*.ttf" in analisis.comando_instalacion


def test_ninguna_opcion_cambia_de_clase(pdf_dejavu, incluidas):
    with pymupdf.open(pdf_dejavu) as doc:
        bloque = bloque_en(doc[0], 0, pymupdf.Point(100, 96), Nivel.LINEA)
        analisis = analizar_fuente(doc, doc[0], bloque.estilo, "¿HOLA?", None, incluidas)
        for op in analisis.opciones:
            assert "Serif" not in op.nombre and "Mono" not in op.nombre
        fam = familias_del_documento(doc)[0]
        assert fam.clase == Clase.SANS
        assert all(clase_tipografica(c.familia) == Clase.SANS for c in candidatas(doc, fam, incluidas, None))


@pytest.mark.parametrize("nombre,flags,clase", [
    ("DejaVuSans-Bold", 2 | 4, Clase.SANS),          # flags mienten: manda el nombre
    ("ABCDEF+DejaVuSerif-Italic", 0, Clase.SERIF),
    ("DejaVuSansMono", 0, Clase.MONO),
    ("PTSans-Regular", 4, Clase.SANS),
    ("Liberation Serif Regular", 0, Clase.SERIF),
    ("CourierNewPSMT", 0, Clase.MONO),
    ("Garamond", 0, Clase.SERIF),
    ("FuenteRara", 4, Clase.SERIF),                   # sin otra pista, usa los flags
    ("FuenteRara", 0, Clase.SANS),
])
def test_clase_tipografica(nombre, flags, clase):
    assert clase_tipografica(nombre, flags) == clase


def test_clase_por_panose_del_programa():
    datos = (config.DIR_FUENTES_INCLUIDAS / "LiberationSerif-Regular.ttf").read_bytes()
    assert clase_tipografica("XYZ123", 0, datos) == Clase.SERIF
    datos = (config.DIR_FUENTES_INCLUIDAS / "LiberationMono-Regular.ttf").read_bytes()
    assert clase_tipografica("XYZ123", 0, datos) == Clase.MONO


@pytest.mark.parametrize("nombre,flags,estilo", [
    ("DejaVuSans-Bold", 0, "bold"), ("Arial,BoldItalic", 0, "bolditalic"), ("OpenSans-SemiBold", 0, "bold"),
    ("Times-Italic", 0, "italic"), ("Calibri", 16, "bold"), ("Calibri", 0, ""),
])
def test_estilo(nombre, flags, estilo):
    assert estilo_de(nombre, flags) == estilo
