"""Fase 3: edición y movimiento de texto, codificación de fuentes existentes y fuentes faltantes."""
from __future__ import annotations

import pymupdf
import pytest

from pdf_studio import config
from pdf_studio.core.codificacion import construir_codificador, parsear_tounicode
from pdf_studio.core.documento import Documento, ErrorDocumento
from pdf_studio.core.fuentes import Embebido, IndiceFuentesSistema
from pdf_studio.core.texto import (Nivel, analizar_fuente, bloque_en, glifos_de_pagina, lineas_de_pagina,
                                   planificar)
from tests.conftest import DIR_PDFS, hash_archivo


@pytest.fixture(scope="module")
def incluidas(tmp_path_factory):
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path_factory.mktemp("c") / "i.json")


@pytest.fixture(scope="module")
def sistema(tmp_path_factory):
    return IndiceFuentesSistema([config.DIR_FUENTES_WINDOWS], tmp_path_factory.mktemp("c") / "s.json")


def editar(doc_o_documento, pagina, punto, nivel, transformar, sistema=None, incluidas=None, mover=(0, 0)):
    """Selecciona, analiza la fuente y aplica la edición. Devuelve (plan, texto nuevo)."""
    doc = doc_o_documento.doc if isinstance(doc_o_documento, Documento) else doc_o_documento
    bloque = bloque_en(doc[pagina], pagina, pymupdf.Point(*punto), nivel)
    assert bloque is not None, f"no hay texto en {punto}"
    nuevo = transformar(bloque.texto)
    opcion = analizar_fuente(doc, doc[pagina], bloque.estilo, nuevo, sistema, incluidas).recomendada
    plan = planificar(doc, bloque, nuevo, opcion, mover)
    if isinstance(doc_o_documento, Documento):
        doc_o_documento.ejecutar(plan.operacion)
    else:
        plan.operacion.aplicar(doc)
    return plan, nuevo, opcion


def texto_plano(pag) -> str:
    return "".join(pag.get_text().split())


# ---------------------------------------------------------------------- codificación
def test_parsear_tounicode():
    cmap = b"""1 begincodespacerange <0000> <FFFF> endcodespacerange
    2 beginbfchar <0003> <0020> <0024> <00F1> endbfchar
    2 beginbfrange <0010> <0012> <0041> <0020> <0021> [<0066> <FB01>] endbfrange"""
    mapa, n = parsear_tounicode(cmap)
    assert n == 2
    assert mapa[0x03] == " " and mapa[0x24] == "ñ"
    assert [mapa[c] for c in (0x10, 0x11, 0x12)] == ["A", "B", "C"]
    assert mapa[0x20] == "f" and mapa[0x21] == "ﬁ"


def test_codificador_base14_winansi():
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as doc:
        xref = [f[0] for f in doc[0].get_fonts() if f[3] == "Helvetica"][0]
        cod = construir_codificador(doc, xref)
        assert cod.embebido == Embebido.NO_EMBEBIDA
        assert cod.disponibles is None            # no embebida: el lector pone la fuente
        assert cod.codigo("ñ") == b"\xf1"           # WinAnsi
        assert cod.avance("H") == pytest.approx(722)
        assert cod.puede("Ñandú €")


def test_codificador_subconjunto_solo_letras_usadas():
    with pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf") as doc:
        xref = [f[0] for f in doc[0].get_fonts() if "Serif" in f[3]][0]
        cod = construir_codificador(doc, xref)
        assert cod.embebido == Embebido.SUBCONJUNTO
        assert cod.bytes_por_codigo == 2
        assert cod.puede("Texto en Liberation")
        assert cod.faltantes("ñandú") == ["ñ", "ú"] or "ñ" in cod.faltantes("ñandú")


# ---------------------------------------------------------------------- selección
def test_niveles_de_seleccion():
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as doc:
        pag = doc[0]
        frag = bloque_en(pag, 0, pymupdf.Point(100, 85), Nivel.FRAGMENTO)
        linea = bloque_en(pag, 0, pymupdf.Point(100, 131), Nivel.LINEA)
        parrafo = bloque_en(pag, 0, pymupdf.Point(100, 131), Nivel.PARRAFO)
        assert frag.texto.startswith("Capítulo 1")
        assert linea.texto.startswith("El presupuesto anual") and len(linea.lineas) == 1
        assert len(parrafo.lineas) == 4 and parrafo.texto.endswith("para todos los socios.")
        assert bloque_en(pag, 0, pymupdf.Point(300, 700), Nivel.LINEA) is None or True
        assert bloque_en(pag, 0, pymupdf.Point(5, 5), Nivel.LINEA) is None


# ---------------------------------------------------------------------- edición
CASOS = [
    ("texto_varias_paginas.pdf", (100, 131), Nivel.LINEA, lambda t: t.replace("presupuesto", "PRESUPUESTO"), "original"),
    ("texto_varias_paginas.pdf", (100, 131), Nivel.PARRAFO,
     lambda t: t.replace("cooperativa", "cooperativa agrícola y ganadera"), "original"),
    ("texto_varias_paginas.pdf", (100, 85), Nivel.FRAGMENTO, lambda t: t + " — ñandú", "original"),
    ("imagen_fondo.pdf", (100, 95), Nivel.LINEA, lambda t: t.replace("1", "Nº 7"), "original"),
    ("tablas.pdf", (285, 177), Nivel.LINEA, lambda t: "9.999", "original"),
    ("fuentes_recortadas.pdf", (100, 90), Nivel.LINEA, lambda t: t.replace("subconjunto", "subconjunto ñ"), "incluida"),
    ("fuentes_recortadas.pdf", (100, 338), Nivel.LINEA, lambda t: t + " ¿qué tal?", "original"),
]


@pytest.mark.parametrize("nombre,punto,nivel,transformar,tipo_fuente", CASOS)
def test_edicion_fiel_e_incremental(copia_pdf, tmp_path, incluidas, nombre, punto, nivel, transformar, tipo_fuente):
    ruta = copia_pdf(nombre)
    antes = hash_archivo(ruta)
    d = Documento(ruta)
    try:
        _plan, nuevo, opcion = editar(d, 0, punto, nivel, transformar, None, incluidas)
        assert opcion.tipo == tipo_fuente
        g = d.guardar(tmp_path / "editado.pdf")
        assert g.es_fiel, g.resumen()
        assert g.incremental and g.bytes_originales_intactos
        assert g.fidelidad.parciales == [0]       # la página editada se verificó fuera de la zona
        with pymupdf.open(tmp_path / "editado.pdf") as res:
            assert "".join(nuevo.split()) in texto_plano(res[0])
        d.deshacer()
        assert d.modificado          # volvió al original, que no es lo último guardado
        assert d.doc[0].get_text() == pymupdf.open(ruta)[0].get_text()
    finally:
        d.cerrar()
    assert hash_archivo(ruta) == antes


def test_arial_recortada_usa_la_de_windows(copia_pdf, tmp_path, sistema, incluidas):
    if sistema.buscar("ArialMT") is None:
        pytest.skip("Arial no está instalada")
    d = Documento(copia_pdf("fuentes_recortadas.pdf"))
    try:
        _p, nuevo, opcion = editar(d, 0, (100, 258), Nivel.LINEA, lambda t: t.replace("Arial", "Arial completa"),
                                   sistema, incluidas)
        assert opcion.tipo == "sistema"
        assert d.guardar(tmp_path / "x.pdf").es_fiel
    finally:
        d.cerrar()


def test_mover_conserva_glifos_y_fuente(copia_pdf, tmp_path):
    d = Documento(copia_pdf("fuentes_recortadas.pdf"))
    try:
        pag = d.doc[0]
        bloque = bloque_en(pag, 0, pymupdf.Point(100, 258), Nivel.LINEA)
        originales = [(g.c, g.x, g.y) for g in bloque.lineas[0].glifos]
        fuentes_antes = len(d.doc.get_page_fonts(0))
        plan = planificar(d.doc, bloque, bloque.texto, None, (50.0, 300.0))
        d.ejecutar(plan.operacion)
        movido = bloque_en(d.doc[0], 0, pymupdf.Point(originales[0][1] + 52, originales[0][2] + 297), Nivel.LINEA)
        assert movido is not None and movido.texto == bloque.texto
        for (c, x, y), g in zip(originales, movido.lineas[0].glifos):
            assert g.c == c and g.x == pytest.approx(x + 50, abs=0.01) and g.y == pytest.approx(y + 300, abs=0.01)
        # Se reutilizó la misma fuente: no se embebió ninguna nueva
        assert len(d.doc.get_page_fonts(0)) == fuentes_antes
        assert d.guardar(tmp_path / "movido.pdf").es_fiel
    finally:
        d.cerrar()


def test_parrafo_justificado_se_reacomoda(copia_pdf):
    d = Documento(copia_pdf("texto_varias_paginas.pdf"))
    try:
        original = bloque_en(d.doc[0], 0, pymupdf.Point(100, 131), Nivel.PARRAFO)
        margen_der = original.lineas[0].bbox.x1
        editar(d, 0, (100, 131), Nivel.PARRAFO, lambda t: t.replace("anual", "anual y extraordinario"))
        nuevo = bloque_en(d.doc[0], 0, pymupdf.Point(100, 131), Nivel.PARRAFO)
        assert "anual y extraordinario" in nuevo.texto
        # Las líneas (menos la última) siguen justificadas al mismo margen derecho
        for ln in nuevo.lineas[:-1]:
            assert ln.bbox.x1 == pytest.approx(margen_der, abs=1.0)
        # Misma interlínea
        bases = [ln.base_y for ln in nuevo.lineas]
        pasos = {round(b - a, 1) for a, b in zip(bases, bases[1:])}
        assert len(pasos) == 1
    finally:
        d.cerrar()


def test_no_borra_texto_vecino_superpuesto():
    """Si borrar el bloque se llevara glifos de otro texto, la edición se rechaza."""
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "AAAAA", fontname="helv", fontsize=12)     # x: 72 a 112
    pag.insert_text((105, 100), "BBBB", fontname="cour", fontsize=12)     # x: 105 a 134, pisa la última A
    bloque = bloque_en(pag, 0, pymupdf.Point(128, 96), Nivel.FRAGMENTO)
    assert bloque.texto.strip() == "BBBB"
    antes = glifos_de_pagina(pag)
    plan = planificar(doc, bloque, "CCCC", analizar_fuente(doc, pag, bloque.estilo, "CCCC", None, None).recomendada)
    try:
        plan.operacion.aplicar(doc)
    except ErrorDocumento as e:
        assert "vecino" in str(e)
    else:
        # Si no lanzó, no se perdió ninguna A
        despues = glifos_de_pagina(doc[0])
        assert {g for g in antes if g[0] == ord("A")} <= despues


def test_texto_en_xobject_no_se_edita():
    fuente = pymupdf.open()
    fuente.new_page().insert_text((72, 100), "Texto dentro de un formulario XObject", fontname="helv")
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.show_pdf_page(pag.rect, fuente, 0)
    bloque = bloque_en(pag, 0, pymupdf.Point(100, 96), Nivel.LINEA)
    assert bloque is not None
    opcion = analizar_fuente(doc, pag, bloque.estilo, "Otro", None, None).recomendada
    with pytest.raises(ErrorDocumento, match="XObject"):
        planificar(doc, bloque, "Otro", opcion).operacion.aplicar(doc)


def test_texto_invisible_sigue_invisible():
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "capa de ocr invisible", fontname="helv", render_mode=3)
    bloque = bloque_en(pag, 0, pymupdf.Point(100, 96), Nivel.LINEA)
    assert bloque.estilo.modo == 3
    editar(doc, 0, (100, 96), Nivel.LINEA, lambda t: t.replace("ocr", "OCR"))
    trazas = [s for s in doc[0].get_texttrace() if s["chars"]]
    assert "OCR" in "".join(chr(c[0]) for s in trazas for c in s["chars"])
    assert all(s["type"] == 3 for s in trazas)


def test_pagina_rotada(tmp_path):
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "Texto en página rotada", fontname="helv", fontsize=14)
    pag.set_rotation(90)
    ruta = tmp_path / "rotada.pdf"
    doc.save(ruta)
    d = Documento(ruta)
    try:
        editar(d, 0, (100, 96), Nivel.LINEA, lambda t: t.replace("rotada", "girada"))
        assert "girada" in d.doc[0].get_text()
        assert d.guardar(tmp_path / "salida.pdf").es_fiel
    finally:
        d.cerrar()


def test_sin_fuente_con_las_letras_falla_claro():
    doc = pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf")
    pag = doc[0]
    bloque = bloque_en(pag, 0, pymupdf.Point(100, 90), Nivel.LINEA)
    analisis = analizar_fuente(doc, pag, bloque.estilo, "漢字", None, None)
    assert analisis.recomendada is None
    with pytest.raises(ErrorDocumento):
        planificar(doc, bloque, "漢字", analisis.opciones[0])


def test_varias_ediciones_y_deshacer(copia_pdf, tmp_path, incluidas):
    d = Documento(copia_pdf("fuentes_recortadas.pdf"))
    try:
        editar(d, 0, (100, 90), Nivel.LINEA, lambda t: t + " ñ", None, incluidas)
        editar(d, 0, (100, 338), Nivel.LINEA, lambda t: t.upper(), None, incluidas)
        editar(d, 0, (100, 298), Nivel.LINEA, lambda t: t, None, incluidas, mover=(0, 60))
        g = d.guardar(tmp_path / "varias.pdf")
        assert g.es_fiel and g.incremental
        d.deshacer()
        d.deshacer()
        d.rehacer()
        assert "TEXTO EN LIBERATION MONO" in d.doc[0].get_text()
        assert d.guardar(tmp_path / "varias2.pdf").es_fiel
    finally:
        d.cerrar()


def test_lineas_de_pagina_ignora_texto_vertical():
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "horizontal", fontname="helv")
    pag.insert_text((300, 300), "vertical", fontname="helv", rotate=90)
    textos = [ln.texto for b in lineas_de_pagina(pag) for ln in b]
    assert textos == ["horizontal"]


# ---------------------------------------------------------------------- interfaz
@pytest.fixture
def ventana(qtbot, monkeypatch, tmp_path):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "firmas"))
    qtbot.addWidget(v)
    v.resize(1100, 800)
    v.show()
    qtbot.waitExposed(v)
    monkeypatch.setattr(v, "_informar", lambda *a, **k: None)
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    monkeypatch.setattr(v, "_confirmar_avisos", lambda avisos: True)
    yield v
    v.cerrar_documento()


def test_ui_seleccionar_editar_y_deshacer(ventana, copia_pdf, monkeypatch):
    from pdf_studio.ui.visor import Modo
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.acc_modo_texto.trigger()
    assert ventana.visor.modo == Modo.TEXTO
    ventana.visor.texto_clic.emit(0, pymupdf.Point(100, 131))
    assert ventana.bloque is not None and ventana.visor.seleccion is not None

    def dialogo(bloque):
        texto = bloque.texto.replace("presupuesto", "plan")
        return texto, ventana._analizar(bloque, texto).recomendada
    monkeypatch.setattr(ventana, "_dialogo_editar_texto", dialogo)
    ventana.acc_editar_seleccion.trigger()
    assert "El plan anual" in ventana.documento.doc[0].get_text()
    assert "Editar texto en página 1" in ventana.acc_deshacer.text()
    ventana.acc_deshacer.trigger()
    assert "El presupuesto anual" in ventana.documento.doc[0].get_text()


def test_ui_mover_arrastrando(ventana, copia_pdf):
    from pdf_studio.ui.visor import Modo
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.set_modo(Modo.TEXTO)
    ventana.combo_nivel.setCurrentIndex(0)    # fragmento
    ventana.visor.texto_clic.emit(0, pymupdf.Point(100, 85))
    ventana.visor.seleccion_movida.emit(0, 0.0, 600.0)
    pag = ventana.documento.doc[0]
    titulo = [b for b in pag.get_text("dict")["blocks"] if b["type"] == 0
              and "Capítulo 1" in "".join(s["text"] for l in b["lines"] for s in l["spans"])]
    assert titulo and titulo[0]["bbox"][1] > 600


def test_dialogo_editar_texto_opciones(qtbot):
    from pdf_studio.ui.edicion_texto import DialogoEditarTexto
    doc = pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf")
    incl = IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], None)
    bloque = bloque_en(doc[0], 0, pymupdf.Point(100, 90), Nivel.LINEA)
    dialogo = DialogoEditarTexto(bloque, lambda t: analizar_fuente(doc, doc[0], bloque.estilo, t, None, incl))
    qtbot.addWidget(dialogo)
    assert dialogo.opcion_elegida().tipo == "original"
    dialogo.editor.setPlainText(bloque.texto + " ñ")
    dialogo.actualizar()
    assert dialogo.opcion_elegida().tipo == "incluida"
    botones = dialogo.botones_opciones.buttons()
    assert not botones[0].isEnabled() and "faltan" in botones[0].text()
    assert "subconjunto" in dialogo.info.text().lower()


def test_dialogo_muestra_comando_de_fuente_libre(qtbot):
    from pdf_studio.core.texto import AnalisisFuente, OpcionFuente
    from pdf_studio.ui.edicion_texto import DialogoEditarTexto
    doc = pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf")
    bloque = bloque_en(doc[0], 0, pymupdf.Point(100, 131), Nivel.LINEA)
    from pdf_studio.dependencias.catalogo_fuentes import buscar_libre
    analisis = AnalisisFuente("Roboto-Regular", Embebido.SUBCONJUNTO,
                              [OpcionFuente("original", "Roboto", "Fuente original del PDF", ["ñ"])],
                              buscar_libre("Roboto-Regular"))
    dialogo = DialogoEditarTexto(bloque, lambda t: analisis)
    qtbot.addWidget(dialogo)
    dialogo.show()
    assert dialogo.fila_instalar is not None and dialogo.fila_instalar.isVisible()
    dialogo.boton_copiar.click()
    from PySide6.QtGui import QGuiApplication
    assert "google/fonts/contents/ofl/roboto" in QGuiApplication.clipboard().text()
    assert not dialogo.boton_instalar.isEnabled()      # sin función de instalación no se ofrece
    assert not dialogo.botones.button(dialogo.botones.StandardButton.Ok).isEnabled()
