"""Fase 4: sustitución global de fuentes (modo armónico)."""
from __future__ import annotations

import re

import pymupdf
import pytest

from pdf_studio import config
from pdf_studio.core.documento import Documento
from pdf_studio.core.fuentes import IndiceFuentesSistema
from pdf_studio.core.sustitucion import (UMBRAL_AJUSTE, candidatas, familias_del_documento,
                                         planificar_sustitucion)
from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en, planificar
from tests.conftest import DIR_PDFS, hash_archivo


@pytest.fixture(scope="module")
def incluidas(tmp_path_factory):
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path_factory.mktemp("c") / "i.json")


def familia(doc, clave):
    return next(f for f in familias_del_documento(doc) if f.clave == clave)


def extremos_de_lineas(pag) -> list[tuple[float, float, float]]:
    """(y, x del primer glifo, fin del último glifo) de cada línea base de texto."""
    lineas: dict[float, list] = {}
    for s in pag.get_texttrace():
        for c in s["chars"]:
            if c[0] > 32:
                lineas.setdefault(round(c[2][1], 1), []).append(c)
    return sorted((y, min(c[2][0] for c in cs), max(c[3][2] for c in cs)) for y, cs in lineas.items())


def test_inventario_de_familias():
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as doc:
        fams = {f.clave: f for f in familias_del_documento(doc)}
    assert set(fams) == {"helvetica"}
    assert fams["helvetica"].nombres_pdf == {"Helvetica", "Helvetica-Bold"}
    assert fams["helvetica"].paginas == set(range(6))
    assert fams["helvetica"].clase == "sans"


@pytest.mark.parametrize("pdf,clave,esperada", [
    ("texto_varias_paginas.pdf", "helvetica", "Liberation Sans"),
    ("imagen_fondo.pdf", "times", "Liberation Serif"),
    ("fuentes_recortadas.pdf", "arial", "Liberation Sans"),
])
def test_recomienda_metricamente_compatible(incluidas, pdf, clave, esperada):
    with pymupdf.open(DIR_PDFS / pdf) as doc:
        cands = candidatas(doc, familia(doc, clave), incluidas, None)
    assert cands[0].familia == esperada and cands[0].compatible


def test_compatibilidad_exige_igualdad_letra_por_letra(incluidas):
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as doc:
        cands = {c.familia: c for c in candidatas(doc, familia(doc, "helvetica"), incluidas, None)}
    assert cands["Liberation Sans"].compatible
    assert not cands["Carlito"].compatible
    # Nunca se ofrece cambiar de clase: Helvetica (sans) no se reemplaza por una serif o mono
    assert "Liberation Serif" not in cands and "Liberation Mono" not in cands and "Caladea" not in cands


def test_compatible_no_mueve_nada(copia_pdf, tmp_path, incluidas):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    antes_hash = hash_archivo(ruta)
    d = Documento(ruta)
    try:
        texto_antes = [p.get_text() for p in d.doc]
        extremos_antes = [extremos_de_lineas(p) for p in d.doc]
        cand = candidatas(d.doc, familia(d.doc, "helvetica"), incluidas, None)[0]
        plan = planificar_sustitucion(d.doc, {"helvetica": cand})
        assert plan.ajustes == [] and plan.omitidos == []
        assert plan.reemplazados == 108 and plan.paginas == set(range(6))
        d.ejecutar(plan.operacion)
        for i, p in enumerate(d.doc):
            assert p.get_text() == texto_antes[i]
            fuentes = {s["font"] for s in p.get_texttrace()}
            assert fuentes == {"LiberationSans", "LiberationSans-Bold"}
            for (y0, a0, b0), (y1, a1, b1) in zip(extremos_antes[i], extremos_de_lineas(p)):
                assert y0 == y1 and a0 == pytest.approx(a1, abs=0.02) and b0 == pytest.approx(b1, abs=0.3)
        g = d.guardar(tmp_path / "armonico.pdf")
        assert g.es_fiel and g.incremental and g.bytes_originales_intactos
        assert sorted(g.fidelidad.parciales) == list(range(6))
        with pymupdf.open(tmp_path / "armonico.pdf") as res:
            nombres = {f[3] for f in res.get_page_fonts(0)}
            assert all(re.fullmatch(r"[A-Z]{6}\+LiberationSans(-Bold)?", n) for n in nombres), nombres
    finally:
        d.cerrar()
    assert hash_archivo(ruta) == antes_hash


def test_no_compatible_ajusta_ancho_y_lo_informa(copia_pdf, tmp_path, incluidas):
    d = Documento(copia_pdf("texto_varias_paginas.pdf"))
    try:
        extremos_antes = extremos_de_lineas(d.doc[0])
        carlito = next(c for c in candidatas(d.doc, familia(d.doc, "helvetica"), incluidas, None)
                       if c.familia == "Carlito")
        plan = planificar_sustitucion(d.doc, {"helvetica": carlito})
        assert plan.ajustes, "Carlito es más angosta: tiene que haber ajustes"
        assert all(abs(a.escala - 1) > UMBRAL_AJUSTE for a in plan.ajustes)
        assert "ajuste de ancho mayor" in plan.resumen()
        d.ejecutar(plan.operacion)
        # Cada tramo empieza y termina exactamente donde estaba
        for (y0, a0, b0), (y1, a1, b1) in zip(extremos_antes, extremos_de_lineas(d.doc[0])):
            assert y0 == y1 and a0 == pytest.approx(a1, abs=0.02) and b0 == pytest.approx(b1, abs=0.05)
        assert d.guardar(tmp_path / "serif.pdf").es_fiel
    finally:
        d.cerrar()


def test_sin_ajuste_de_ancho(incluidas):
    doc = pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf")
    carlito = next(c for c in candidatas(doc, familia(doc, "helvetica"), incluidas, None)
                   if c.familia == "Carlito")
    plan = planificar_sustitucion(doc, {"helvetica": carlito}, ajustar_ancho=False)
    assert plan.ajustes == []
    plan.operacion.aplicar(doc)
    assert {s["font"] for s in doc[0].get_texttrace()} == {"Carlito-Regular", "Carlito-Bold"}


def test_deshacer_restaura_fuentes(copia_pdf, incluidas):
    d = Documento(copia_pdf("imagen_fondo.pdf"))
    try:
        cand = candidatas(d.doc, familia(d.doc, "times"), incluidas, None)[0]
        d.ejecutar(planificar_sustitucion(d.doc, {"times": cand}).operacion)
        assert {s["font"] for s in d.doc[0].get_texttrace()} == {"LiberationSerif", "LiberationSerif-Bold"}
        d.deshacer()
        assert {s["font"] for s in d.doc[0].get_texttrace()} == {"Times-Roman", "Times-Bold"}
    finally:
        d.cerrar()


def test_despues_de_sustituir_se_puede_editar(copia_pdf, tmp_path, incluidas):
    """El objetivo del modo armónico: después se edita con la misma familia."""
    d = Documento(copia_pdf("fuentes_recortadas.pdf"))
    try:
        cand = candidatas(d.doc, familia(d.doc, "arial"), incluidas, None)[0]
        d.ejecutar(planificar_sustitucion(d.doc, {"arial": cand}).operacion)
        pag = d.doc[0]
        bloque = bloque_en(pag, 0, pymupdf.Point(100, 258), Nivel.LINEA)
        nuevo = bloque.texto + " con eñe"
        analisis = analizar_fuente(d.doc, pag, bloque.estilo, nuevo, None, incluidas)
        opcion = analisis.recomendada
        assert opcion.tipo == "incluida" and "Liberation Sans" in opcion.nombre
        d.ejecutar(planificar(d.doc, bloque, nuevo, opcion).operacion)
        assert "con eñe" in d.doc[0].get_text()
        assert d.guardar(tmp_path / "x.pdf").es_fiel
    finally:
        d.cerrar()


def test_xobject_se_omite_y_se_informa(incluidas):
    fuente = pymupdf.open()
    fuente.new_page().insert_text((72, 100), "Texto en XObject", fontname="helv")
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.show_pdf_page(pag.rect, fuente, 0)
    doc.new_page().insert_text((72, 100), "Texto normal", fontname="helv")
    cand = candidatas(doc, familia(doc, "helvetica"), incluidas, None)[0]
    plan = planificar_sustitucion(doc, {"helvetica": cand})
    assert plan.paginas == {1}
    assert any("XObject" in o for o in plan.omitidos)


# ---------------------------------------------------------------------- interfaz
def test_dialogo_vista_previa(qtbot, incluidas):
    from pdf_studio.ui.sustitucion import DialogoSustitucion
    doc = pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf")
    dialogo = DialogoSustitucion(doc, incluidas, None, preseleccion="arial")
    qtbot.addWidget(dialogo)
    fila = [f.clave for f in dialogo.familias].index("arial")
    assert dialogo.checks[fila].isChecked()
    assert dialogo.combos[fila].currentText().startswith("★ Liberation Sans")
    assert not dialogo.boton_aplicar.isEnabled()
    dialogo.generar_previa()
    assert dialogo.boton_aplicar.isEnabled()
    assert dialogo.vista_nueva.pixmap() is not None and not dialogo.vista_nueva.pixmap().isNull()
    assert "Se reemplazarán 1 fragmento" in dialogo.resumen.text()
    dialogo.ajustar.setChecked(False)          # cambiar opciones invalida la vista previa
    assert not dialogo.boton_aplicar.isEnabled()


def test_ventana_sustituir_y_deshacer(qtbot, monkeypatch, copia_pdf, tmp_path, incluidas):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    mensajes = []
    monkeypatch.setattr(v, "_informar", lambda t, x, error=False: mensajes.append(x))
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    v.abrir(copia_pdf("imagen_fondo.pdf"))

    def dialogo(preseleccion):
        cand = candidatas(v.documento.doc, familia(v.documento.doc, "times"), incluidas, None)[0]
        return planificar_sustitucion(v.documento.doc, {"times": cand})
    monkeypatch.setattr(v, "_dialogo_sustitucion", dialogo)
    assert v.sustituir_fuentes()
    assert "Se reemplazarán 10 fragmento" in mensajes[-1]
    assert {s["font"] for s in v.documento.doc[0].get_texttrace()} == {"LiberationSerif", "LiberationSerif-Bold"}
    assert "Reemplazar fuentes por Liberation Serif" in v.acc_deshacer.text()
    v.acc_deshacer.trigger()
    assert "Times-Roman" in {s["font"] for s in v.documento.doc[0].get_texttrace()}
    v.cerrar_documento()
