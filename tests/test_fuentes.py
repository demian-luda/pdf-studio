"""Análisis de fuentes: embebido completo / subconjunto / no embebido, e índice del sistema."""
from __future__ import annotations

import pymupdf
import pytest

from pdf_studio import config
from pdf_studio.core.fuentes import (Embebido, IndiceFuentesSistema, analizar_fuentes, clave_fuente,
                                     familia_de)
from tests.conftest import DIR_PDFS


@pytest.mark.parametrize("nombre,clave", [
    ("ABCDEF+Arial-BoldMT", "arialbold"),
    ("Arial,Bold", "arialbold"),
    ("Arial Bold", "arialbold"),
    ("ArialMT", "arial"),
    ("TimesNewRomanPSMT", "timesnewroman"),
    ("TimesNewRomanPS-BoldItalicMT", "timesnewromanbolditalic"),
    ("Times New Roman Bold Italic", "timesnewromanbolditalic"),
    ("Liberation Sans Regular", "liberationsans"),
    ("LiberationSans-Regular", "liberationsans"),
    ("Helvetica-BoldOblique", "helveticabolditalic"),
    ("Calibri", "calibri"),
])
def test_clave_fuente(nombre, clave):
    assert clave_fuente(nombre) == clave


@pytest.mark.parametrize("nombre,familia", [
    ("ABCDEF+OpenSans-SemiBold", "opensans"),
    ("Open Sans SemiBold", "opensans"),
    ("TimesNewRomanPS-BoldMT", "timesnewroman"),
    ("Calibri,Italic", "calibri"),
])
def test_familia_de(nombre, familia):
    assert familia_de(nombre) == familia


def test_clasificacion_de_fuentes():
    with pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf") as doc:
        fuentes = {f.nombre: f for f in analizar_fuentes(doc)}
    assert fuentes["ArialMT"].embebido == Embebido.SUBCONJUNTO
    assert fuentes["Liberation Serif Regular"].embebido == Embebido.SUBCONJUNTO
    assert fuentes["Liberation Mono Regular"].embebido == Embebido.COMPLETA
    assert fuentes["Helvetica"].embebido == Embebido.NO_EMBEBIDA
    assert fuentes["Helvetica"].es_estandar
    assert fuentes["ArialMT"].nombre_pdf.endswith("+ArialMT")
    assert fuentes["ArialMT"].paginas == [0]


def test_indice_con_fuentes_incluidas(tmp_path):
    cache = tmp_path / "idx.json"
    idx = IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], cache)
    f = idx.buscar("ABCDEF+LiberationSans-Bold")
    assert f is not None and f.archivo.endswith("LiberationSans-Bold.ttf")
    assert idx.buscar("Liberation Serif Italic").archivo.endswith("LiberationSerif-Italic.ttf")
    assert idx.buscar("Carlito,Bold").archivo.endswith("Carlito-Bold.ttf")
    assert idx.buscar("FuenteQueNoExiste") is None
    assert len(idx.familia("Liberation Sans")) == 4
    assert cache.is_file()
    # Segunda carga: sale de la caché y da lo mismo
    idx2 = IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], cache)
    assert idx2.buscar("LiberationMono-Regular").archivo == idx.buscar("LiberationMono-Regular").archivo


def test_analisis_marca_instaladas_e_incluidas(tmp_path):
    vacio = IndiceFuentesSistema([tmp_path / "sin_fuentes"], tmp_path / "a.json")
    incluidas = IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path / "b.json")
    with pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf") as doc:
        fuentes = {f.nombre: f for f in analizar_fuentes(doc, vacio, incluidas)}
    assert fuentes["ArialMT"].instalada is None
    assert fuentes["ArialMT"].incluida is None
    assert fuentes["Liberation Serif Regular"].incluida is not None


@pytest.mark.skipif(not (config.DIR_FUENTES_WINDOWS / "arial.ttf").is_file(), reason="Sin Arial de Windows")
def test_indice_windows_encuentra_arial(tmp_path):
    idx = IndiceFuentesSistema([config.DIR_FUENTES_WINDOWS], tmp_path / "win.json")
    assert idx.buscar("ABCDEF+ArialMT").archivo.lower().endswith("arial.ttf")
    assert idx.buscar("Arial,Bold").archivo.lower().endswith("arialbd.ttf")
