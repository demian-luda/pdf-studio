"""Fase 5: OCR (modo Idéntico y Reconstruido), rangos de páginas y la interfaz del OCR."""
from __future__ import annotations

import numpy as np
import pymupdf
import pytest

from pdf_studio.core import ocr
from pdf_studio.core.documento import Documento
from pdf_studio.core.fidelidad import comparar_paginas
from pdf_studio.core.rangos import describir_rango, parsear_rango
from tests.conftest import DIR_PDFS, hash_archivo

LETRAS = ("Acta de reunión", "presupuesto anual")


def _hay_ocr() -> bool:
    try:
        ocr.entorno_ocr()
        return True
    except ocr.ErrorOCR:
        return False


requiere_tesseract = pytest.mark.skipif(not _hay_ocr(), reason="Tesseract con español no está instalado")


# ---------------------------------------------------------------------- rangos
@pytest.mark.parametrize("texto,esperado", [
    ("", [0, 1, 2, 3, 4]), ("todas", [0, 1, 2, 3, 4]), ("1-3", [0, 1, 2]), ("2, 4", [1, 3]),
    ("4-fin", [3, 4]), ("3,1-2,3", [0, 1, 2]), ("-2", [0, 1]), ("5-", [4]),
])
def test_parsear_rango(texto, esperado):
    assert parsear_rango(texto, 5) == esperado


@pytest.mark.parametrize("texto,mensaje", [("7", "no existe"), ("3-1", "al revés"), ("a", "no es un número"),
                                           ("0", "no existe")])
def test_rango_invalido_da_mensaje_claro(texto, mensaje):
    with pytest.raises(ValueError, match=mensaje):
        parsear_rango(texto, 5)


def test_describir_rango():
    assert describir_rango([0, 1, 2, 4, 6, 7]) == "1-3, 5, 7-8"


# ---------------------------------------------------------------------- detección
def test_detecta_pdf_escaneado():
    with pymupdf.open(DIR_PDFS / "escaneado.pdf") as d:
        assert ocr.paginas_sin_texto(d) == [0, 1]
        assert ocr.parece_escaneado(d)
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as d:
        assert ocr.paginas_sin_texto(d) == []
        assert not ocr.parece_escaneado(d)


# ---------------------------------------------------------------------- capa invisible
def _capa_sintetica(pag: pymupdf.Page, lineas: list[tuple[str, float, float, float]]) -> bytes:
    """Capa como la que arma capas_de_texto (mismo tamaño de página, texto invisible).
    lineas: (texto, x, y_base, tamaño). Las páginas de prueba tienen MediaBox en el origen."""
    d = pymupdf.open()
    p = d.new_page(width=pag.mediabox.width, height=pag.mediabox.height)
    for texto, x, y, t in lineas:
        p.insert_text((x, y), texto, fontsize=t, fontname="helv", render_mode=3)
    datos = d.tobytes()
    d.close()
    return datos


def _pixeles_iguales(a: pymupdf.Page, b: pymupdf.Page) -> bool:
    return comparar_paginas(a, b, 100)[0] == 0


def test_capa_invisible_no_cambia_el_aspecto_y_se_puede_buscar(copia_pdf, tmp_path):
    ruta = copia_pdf("escaneado.pdf")
    antes = hash_archivo(ruta)
    documento = Documento(ruta)
    capa = _capa_sintetica(documento.doc[0], [("Acta de reunión escaneada", 72, 90, 18)])
    documento.ejecutar(ocr.AgregarCapaOCR({0: capa}))
    assert documento.doc[0].search_for("reunión")
    with pymupdf.open(ruta) as original:
        assert _pixeles_iguales(original[0], documento.doc[0])
    g = documento.guardar(tmp_path / "salida.pdf")
    assert g.incremental and g.bytes_originales_intactos and g.es_fiel
    assert 0 in g.fidelidad.enteras            # la página con OCR se comparó entera: idéntica
    assert "idénticas al original" in g.resumen()
    documento.deshacer()
    assert not documento.doc[0].search_for("reunión")
    documento.cerrar()
    assert hash_archivo(ruta) == antes


@pytest.mark.parametrize("rotacion", [0, 90, 180, 270])
def test_capa_alineada_en_paginas_rotadas_y_con_cajas_desplazadas(tmp_path, rotacion):
    d = pymupdf.open()
    p = d.new_page(width=400, height=500)
    p.insert_text((50, 100), "MARCA visible", fontsize=20)
    d.xref_set_key(p.xref, "MediaBox", "[10 20 410 520]")
    d.xref_set_key(p.xref, "CropBox", "[10 20 410 520]")
    p.set_rotation(rotacion)
    ruta = tmp_path / "rotada.pdf"
    d.save(ruta)
    d.close()
    documento = Documento(ruta)
    pag = documento.doc[0]
    esperado = pag.search_for("MARCA")[0]
    # La capa reproduce el mismo texto en el mismo lugar (en coordenadas PDF)
    with pymupdf.open(ruta) as fuente:
        capa = pymupdf.open()
        capa.insert_pdf(fuente)
        cp = capa[0]
        cp.set_rotation(0)
        for xref in cp.get_contents():
            capa.update_stream(xref, capa.xref_stream(xref).replace(b"BT", b"BT 3 Tr", 1))
        datos = capa.tobytes()
    documento.ejecutar(ocr.AgregarCapaOCR({0: datos}))
    encontrados = [pymupdf.Rect(w[:4]) for w in documento.doc[0].get_text("words") if w[4] == "MARCA"]
    assert len(encontrados) == 2
    assert all(abs(r.x0 - esperado.x0) < 0.5 and abs(r.y0 - esperado.y0) < 0.5 for r in encontrados)
    assert documento.doc[0].rotation == rotacion
    with pymupdf.open(ruta) as original:
        assert _pixeles_iguales(original[0], documento.doc[0])
    documento.cerrar()


def test_capa_no_toca_las_otras_paginas_aunque_compartan_recursos(copia_pdf, tmp_path):
    ruta = copia_pdf("escaneado.pdf")
    documento = Documento(ruta)
    capa = _capa_sintetica(documento.doc[1], [("Segunda hoja", 72, 90, 14)])
    documento.ejecutar(ocr.AgregarCapaOCR({1: capa}))
    assert not documento.doc[0].search_for("Segunda")
    g = documento.guardar(tmp_path / "salida.pdf")
    assert g.es_fiel and 0 in g.fidelidad.comparadas
    documento.cerrar()


def test_uso_de_capa_compone_matrices():
    a = [2, 0, 0, 2, 10, 0]
    b = [1, 0, 0, 1, 0, 5]
    assert ocr._multiplicar(a, b) == [2, 0, 0, 2, 10, 5]


def test_capas_de_texto_extrae_solo_la_capa(tmp_path):
    """Simula la salida de ocrmypdf: contenido original + Form XObject /OCR-… dibujado con cm."""
    import pikepdf
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(300, 400))
    pagina = pdf.pages[0]
    capa = pdf.make_stream(b"BT 3 Tr /F1 12 Tf 20 300 Td (Hola mundo) Tj ET")
    capa.Type, capa.Subtype, capa.BBox = pikepdf.Name.XObject, pikepdf.Name.Form, [0, 0, 300, 400]
    capa.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(
        F1=pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)))
    pagina.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary({"/OCR-abc": capa}))
    pagina.obj.Contents = pdf.make_stream(b"0 0 1 rg 0 0 50 50 re f q 1 0 0 1 5 0 cm /OCR-abc Do Q")
    salida = tmp_path / "ocr.pdf"
    pdf.save(salida)
    capas = ocr.capas_de_texto(salida, [0])
    with pymupdf.open("pdf", capas[0]) as d:
        assert "Hola mundo" in d[0].get_text()
        palabra = d[0].search_for("Hola")[0]
        assert palabra.x0 == pytest.approx(25, abs=0.5)     # 20 + desplazamiento del cm
        # Solo la capa: el cuadrado azul del contenido original no está
        pix = d[0].get_pixmap(dpi=20)
        assert set(np.frombuffer(pix.samples, dtype=np.uint8)) == {255}


# ---------------------------------------------------------------------- reconstruido
def _escaneado_con_logo(tmp_path):
    """Escaneo (imagen de toda la página) + un logo chico, en dos páginas."""
    with pymupdf.open(DIR_PDFS / "escaneado.pdf") as fuente:
        pix = fuente[0].get_pixmap(dpi=40)
    logo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 20), False)
    logo.clear_with(90)
    d = pymupdf.open()
    for _ in range(2):
        p = d.new_page(width=595, height=842)
        p.insert_image(p.rect, pixmap=pix)
        p.insert_image(pymupdf.Rect(500, 30, 560, 90), pixmap=logo)
    ruta = tmp_path / "con_logo.pdf"
    d.save(ruta)
    d.close()
    return ruta


def test_reconstruido_reemplaza_el_fondo_y_conserva_el_resto(tmp_path):
    ruta = _escaneado_con_logo(tmp_path)
    documento = Documento(ruta)
    capa = _capa_sintetica(documento.doc[0], [("Acta de reunión", 72, 90, 18),
                                             ("El presupuesto anual de la cooperativa", 72, 140, 12)])
    documento.ejecutar(ocr.ReconstruirOCR({0: capa}))
    pag = documento.doc[0]
    assert "presupuesto anual" in pag.get_text()
    # El escaneo ya no se dibuja, el logo sí
    cajas = [pymupdf.Rect(i["bbox"]) for i in pag.get_image_info()]
    assert len(cajas) == 1 and cajas[0].x0 == pytest.approx(500)
    # El texto es visible (hay píxeles oscuros donde está la línea)
    pix = pag.get_pixmap(dpi=72, clip=pymupdf.Rect(72, 125, 300, 145))
    assert min(pix.samples) < 128
    g = documento.guardar(tmp_path / "salida.pdf")
    assert g.es_fiel and g.fidelidad.comparadas == [1]     # la otra página quedó idéntica
    documento.deshacer()
    assert len(documento.doc[0].get_image_info()) == 2
    documento.cerrar()


def test_reconstruido_sin_imagen_de_fondo_avisa(copia_pdf):
    documento = Documento(copia_pdf("texto_varias_paginas.pdf"))
    capa = _capa_sintetica(documento.doc[0], [("Hola", 72, 90, 12)])
    with pytest.raises(ocr.ErrorOCR, match="imagen de fondo"):
        documento.ejecutar(ocr.ReconstruirOCR({0: capa}))
    assert documento.operaciones_activas == []
    documento.cerrar()


def test_sin_capas_da_error_claro():
    with pytest.raises(ocr.ErrorOCR, match="No se reconoció texto"):
        ocr.AgregarCapaOCR({})


# ---------------------------------------------------------------------- dependencias
def test_sin_tesseract_mensaje_claro(monkeypatch):
    from pdf_studio.dependencias import chequeo
    monkeypatch.setattr(chequeo, "buscar_tesseract", lambda: None)
    with pytest.raises(ocr.ErrorOCR, match="Tesseract"):
        ocr.entorno_ocr()


def test_idiomas_del_usuario_reciben_las_configuraciones(tmp_path, monkeypatch):
    from pdf_studio import config
    instalado = tmp_path / "Tesseract-OCR"
    (instalado / "tessdata" / "configs").mkdir(parents=True)
    (instalado / "tessdata" / "configs" / "hocr").write_text("tessedit_create_hocr 1")
    (instalado / "tessdata" / "eng.traineddata").write_bytes(b"x")
    usuario = tmp_path / "usuario"
    usuario.mkdir()
    for i in ("spa", "eng"):
        (usuario / f"{i}.traineddata").write_bytes(b"x")
    monkeypatch.setattr(config, "DIR_TESSDATA_USUARIO", usuario)
    assert ocr.carpeta_tessdata(instalado / "tesseract.exe") == usuario
    assert (usuario / "configs" / "hocr").is_file()


def test_faltan_idiomas(tmp_path, monkeypatch):
    from pdf_studio import config
    (tmp_path / "Tesseract-OCR" / "tessdata").mkdir(parents=True)
    monkeypatch.setattr(config, "DIR_TESSDATA_USUARIO", tmp_path / "vacia")
    with pytest.raises(ocr.ErrorOCR, match="spa"):
        ocr.carpeta_tessdata(tmp_path / "Tesseract-OCR" / "tesseract.exe")


def test_codigos_de_error_de_ocrmypdf():
    assert "contraseña" in ocr.mensaje_de_error_ocrmypdf(8, "")
    assert "Detalle: algo" in ocr.mensaje_de_error_ocrmypdf(15, "linea 1\nalgo\n")


# ---------------------------------------------------------------------- OCR real
@requiere_tesseract
def test_ocr_real_identico(copia_pdf, tmp_path):
    ruta = copia_pdf("escaneado.pdf")
    antes = hash_archivo(ruta)
    documento = Documento(ruta)
    capas = ocr.reconocer(documento.doc, [0, 1], tmp_path, timeout=300)
    assert set(capas) == {0, 1}
    documento.ejecutar(ocr.AgregarCapaOCR(capas))
    texto = documento.doc[0].get_text()
    assert all(l in texto for l in LETRAS)
    g = documento.guardar(tmp_path / "salida.pdf")
    assert g.es_fiel and g.incremental and g.bytes_originales_intactos
    assert sorted(g.fidelidad.enteras) == [0, 1]
    documento.cerrar()
    assert hash_archivo(ruta) == antes


@requiere_tesseract
def test_ocr_real_reconstruido(copia_pdf, tmp_path):
    documento = Documento(copia_pdf("escaneado.pdf"))
    capas = ocr.reconocer(documento.doc, [0], tmp_path, timeout=300)
    documento.ejecutar(ocr.ReconstruirOCR(capas))
    assert not documento.doc[0].get_image_info()
    assert "presupuesto" in documento.doc[0].get_text()
    g = documento.guardar(tmp_path / "salida.pdf")
    assert g.es_fiel and g.fidelidad.comparadas == [1]
    documento.cerrar()


# ---------------------------------------------------------------------- interfaz
def test_aviso_de_escaneado_al_abrir(ventana, copia_pdf):
    ventana.abrir(copia_pdf("escaneado.pdf"))
    assert ventana.aviso.isVisible()
    assert "escaneado" in ventana.aviso.texto.text()
    ventana.cerrar_documento()
    assert not ventana.aviso.isVisible()
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    assert not ventana.aviso.isVisible()


def test_ocr_sin_tesseract_avisa_en_la_interfaz(ventana, copia_pdf, monkeypatch):
    from pdf_studio.dependencias import chequeo
    ventana.abrir(copia_pdf("escaneado.pdf"))
    monkeypatch.setattr(chequeo, "buscar_tesseract", lambda: None)
    monkeypatch.setattr(ventana, "_dialogo_ocr", lambda sin_texto: (ocr.ModoOCR.IDENTICO, sin_texto))
    monkeypatch.setattr(ventana, "chequear_dependencias", lambda mostrar=True: None)
    assert not ventana.reconocer_texto()
    assert ventana.mensajes[-1][2] and "Tesseract" in ventana.mensajes[-1][1]


def test_ocr_en_segundo_plano_con_proceso_simulado(ventana, copia_pdf, monkeypatch, qtbot):
    """La interfaz sigue respondiendo mientras corre el OCR, y al terminar se aplica la capa."""
    import sys
    import pdf_studio.ui.ventana_principal as vp
    ventana.abrir(copia_pdf("escaneado.pdf"))
    capa = _capa_sintetica(ventana.documento.doc[0], [("Texto reconocido", 72, 90, 14)])
    monkeypatch.setattr(ventana, "_dialogo_ocr", lambda sin_texto: (ocr.ModoOCR.IDENTICO, [0]))
    monkeypatch.setattr(vp, "entorno_ocr", lambda: None)
    monkeypatch.setattr(vp, "comando_ocrmypdf",
                        lambda e, s, p: (sys.executable, ["-c", "import time; time.sleep(1.5)"]))
    monkeypatch.setattr(vp, "capas_de_texto", lambda salida, paginas: {0: capa})
    assert ventana.reconocer_texto()
    assert ventana.proceso_ocr.ocupado
    assert "Reconociendo" in ventana.etiqueta_analisis.text()
    ventana.visor.acercar()                        # la interfaz responde mientras tanto
    qtbot.waitUntil(lambda: not ventana.proceso_ocr.ocupado, timeout=20_000)
    assert ventana.documento.doc[0].search_for("reconocido")
    assert not ventana.aviso.isVisible()
    assert "Reconociendo" not in ventana.etiqueta_analisis.text()
    assert ventana.acc_deshacer.isEnabled()


def test_cancelar_ocr(ventana, copia_pdf, monkeypatch):
    import sys
    import pdf_studio.ui.ventana_principal as vp
    ventana.abrir(copia_pdf("escaneado.pdf"))
    monkeypatch.setattr(ventana, "_dialogo_ocr", lambda sin_texto: (ocr.ModoOCR.IDENTICO, [0]))
    monkeypatch.setattr(vp, "entorno_ocr", lambda: None)
    monkeypatch.setattr(vp, "comando_ocrmypdf", lambda e, s, p: (sys.executable, ["-c", "import time; time.sleep(30)"]))
    assert ventana.reconocer_texto()
    ventana.cancelar_ocr()
    assert not ventana.proceso_ocr.ocupado
    assert ventana.documento.operaciones_activas == []
    assert "Reconociendo" not in ventana.etiqueta_analisis.text()


def test_ocr_que_falla_se_informa(ventana, copia_pdf, monkeypatch, qtbot):
    import sys
    import pdf_studio.ui.ventana_principal as vp
    ventana.abrir(copia_pdf("escaneado.pdf"))
    monkeypatch.setattr(ventana, "_dialogo_ocr", lambda sin_texto: (ocr.ModoOCR.IDENTICO, [0]))
    monkeypatch.setattr(vp, "entorno_ocr", lambda: None)
    monkeypatch.setattr(vp, "comando_ocrmypdf", lambda e, s, p: (sys.executable, ["-c", "raise SystemExit(7)"]))
    ventana.reconocer_texto()
    qtbot.waitUntil(lambda: not ventana.proceso_ocr.ocupado, timeout=20_000)
    assert ventana.mensajes and ventana.mensajes[-1][2]
    assert "Tesseract falló" in ventana.mensajes[-1][1]


@requiere_tesseract
def test_ocr_real_desde_la_interfaz(ventana, copia_pdf, monkeypatch, qtbot, tmp_path):
    ventana.abrir(copia_pdf("escaneado.pdf"))
    monkeypatch.setattr(ventana, "_dialogo_ocr", lambda sin_texto: (ocr.ModoOCR.IDENTICO, sin_texto))
    assert ventana.reconocer_texto()
    qtbot.waitUntil(lambda: not ventana.proceso_ocr.ocupado, timeout=300_000)
    assert ventana.documento.doc[0].search_for("presupuesto")
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda sugerida: str(tmp_path / "con_ocr.pdf"))
    assert ventana.guardar_como()
    guardado = ventana.mensajes[-1][1]
    assert guardado.es_fiel


def test_dialogo_ocr(qtbot):
    from pdf_studio.ui.ocr import DialogoOCR
    d = DialogoOCR(5, 2, [0, 1])
    qtbot.addWidget(d)
    assert "Páginas sin texto (2)" in d.selector._opciones[0][0].text()
    d.accept()
    assert d.paginas == [0, 1] and d.modo == ocr.ModoOCR.IDENTICO
    d2 = DialogoOCR(5, 2, [])
    qtbot.addWidget(d2)
    d2.radio_reconstruido.setChecked(True)
    d2.selector.set_rango("9")
    d2.accept()
    assert d2.error.isVisibleTo(d2) and "no existe" in d2.error.text()
    d2.selector.set_rango("2-3")
    d2.accept()
    assert d2.paginas == [1, 2] and d2.modo == ocr.ModoOCR.RECONSTRUIDO
