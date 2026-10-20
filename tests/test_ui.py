"""Tests de la interfaz con pytest-qt (sin diálogos modales: se reemplazan)."""
from __future__ import annotations

import pymupdf
import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QGuiApplication

from pdf_studio.dependencias.chequeo import Faltante, Importancia
from pdf_studio.ui.ventana_principal import VentanaPrincipal
from pdf_studio.ui.visor import Modo


@pytest.fixture
def ventana(qtbot, monkeypatch, tmp_path):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "firmas"))
    qtbot.addWidget(v)
    v.resize(1100, 800)
    v.show()
    qtbot.waitExposed(v)
    mensajes = []
    monkeypatch.setattr(v, "_informar", lambda t, x, error=False: mensajes.append((t, x, error)))
    monkeypatch.setattr(v, "_mostrar_guardado", lambda g, d: mensajes.append(("guardado", g, d)))
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    v.mensajes = mensajes
    yield v
    v.cerrar_documento()


def test_abrir_muestra_paginas_y_miniaturas(ventana, copia_pdf, qtbot):
    assert ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    assert ventana.visor.cantidad_paginas == 6
    assert ventana.miniaturas.count() == 6
    qtbot.waitUntil(lambda: ventana.miniaturas.renderizado_completo, timeout=5000)
    assert "texto_varias_paginas.pdf" in ventana.windowTitle()
    assert ventana.etiqueta_pagina.text() == "Página 1 de 6"


def test_zoom(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.visor.zoom_real()
    assert ventana.visor.zoom == pytest.approx(1.0)
    ventana.acc_acercar.trigger()
    assert ventana.visor.zoom == pytest.approx(1.25)
    ventana.acc_alejar.trigger()
    ventana.acc_alejar.trigger()
    assert ventana.visor.zoom == pytest.approx(0.8)
    ventana.visor.set_zoom(100)
    assert ventana.visor.zoom == 8.0  # límite máximo
    ventana.acc_ajustar_ancho.trigger()
    assert 0.1 < ventana.visor.zoom < 8.0


def test_render_con_zoom_grande_no_falla(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.visor.set_zoom(6.0)
    ventana.visor.viewport().repaint()
    imagen = ventana.visor.grab().toImage()
    assert not imagen.isNull()


def test_busqueda(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.acc_buscar.trigger()
    assert ventana.busqueda.isVisible()
    ventana.busqueda.buscar("presupuesto")
    total = len(ventana.busqueda.resultados)
    assert total == 6 * 4 * 2  # 2 por párrafo, 4 párrafos, 6 páginas
    assert ventana.busqueda.etiqueta.text() == f"1 de {total}"
    assert len(ventana.visor._marcas) == total
    for _ in range(9):
        ventana.busqueda.siguiente()
    assert ventana.busqueda.actual == 9
    assert ventana.visor.pagina_actual == 1  # el resultado 10 está en la página 2
    ventana.busqueda.anterior()
    assert ventana.busqueda.etiqueta.text() == f"9 de {total}"
    ventana.busqueda.buscar("palabra-que-no-esta")
    assert ventana.busqueda.etiqueta.text() == "Sin resultados"
    ventana.busqueda.cerrar()
    assert ventana.visor._marcas == []


def test_rotar_deshacer_rehacer_desde_acciones(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    assert not ventana.acc_deshacer.isEnabled()
    ventana.acc_rotar_der.trigger()
    assert ventana.documento.doc[0].rotation == 90
    assert ventana.acc_deshacer.isEnabled()
    assert "Rotar página 1" in ventana.acc_deshacer.text()
    assert ventana.windowTitle().startswith("texto_varias_paginas.pdf*")
    r = ventana.visor.items[0].boundingRect()
    assert r.width() > r.height()  # la página quedó apaisada en el visor
    ventana.acc_deshacer.trigger()
    assert ventana.documento.doc[0].rotation == 0
    ventana.acc_rehacer.trigger()
    assert ventana.documento.doc[0].rotation == 90


def test_coordenadas_en_pagina_rotada(ventana, copia_pdf):
    """El resaltado de búsqueda cae sobre el texto también en páginas rotadas."""
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.acc_rotar_der.trigger()
    pag = ventana.documento.doc[0]
    rect = pag.search_for("Capítulo")[0]
    girado = rect * pag.rotation_matrix
    pix = pag.get_pixmap(matrix=pymupdf.Matrix(1, 1), clip=None)
    # Zona del texto en el render rotado: tiene que haber tinta (píxeles oscuros)
    x0, y0, x1, y1 = (int(v) for v in (girado.x0, girado.y0, girado.x1, girado.y1))
    oscuros = sum(1 for x in range(x0, x1) for y in range(y0, y1) if pix.pixel(x, y)[0] < 100)
    assert oscuros > 20
    # Y el mapeo escena -> página vuelve al mismo punto
    escena = ventana.visor.a_escena(0, rect)
    pagina, punto = ventana.visor.desde_escena(escena.center())
    assert pagina == 0
    assert punto.x == pytest.approx((rect.x0 + rect.x1) / 2, abs=0.5)
    assert punto.y == pytest.approx((rect.y0 + rect.y1) / 2, abs=0.5)


def test_resaltar_y_nota(ventana, copia_pdf, monkeypatch):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.acc_modo_resaltar.trigger()
    assert ventana.visor.modo == Modo.RESALTAR
    rect = ventana.documento.doc[0].search_for("Informe")[0]
    ventana.visor.rect_seleccionado.emit(0, rect)
    assert [a.type[1] for a in ventana.documento.doc[0].annots()] == ["Highlight"]
    monkeypatch.setattr(ventana, "_pedir_texto_nota", lambda: "Hola")
    ventana.acc_modo_nota.trigger()
    ventana.visor.punto_clic.emit(1, pymupdf.Point(100, 100))
    assert [a.info["content"] for a in ventana.documento.doc[1].annots()] == ["Hola"]


def test_seleccion_con_mouse_en_modo_resaltar(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.set_modo(Modo.RESALTAR)
    rect = ventana.documento.doc[0].search_for("Capítulo")[0]
    escena = ventana.visor.a_escena(0, rect)
    a = ventana.visor.mapFromScene(escena.topLeft() + QPointF(1, 1))
    b = ventana.visor.mapFromScene(escena.bottomRight() - QPointF(1, 1))
    with qtbot.waitSignal(ventana.visor.rect_seleccionado, timeout=2000):
        qtbot.mousePress(ventana.visor.viewport(), pymupdf_boton(), pos=a)
        qtbot.mouseMove(ventana.visor.viewport(), pos=b)
        qtbot.mouseRelease(ventana.visor.viewport(), pymupdf_boton(), pos=b)
    assert len(list(ventana.documento.doc[0].annots())) == 1


def pymupdf_boton():
    from PySide6.QtCore import Qt
    return Qt.MouseButton.LeftButton


def test_guardar_copia(ventana, copia_pdf, tmp_path, monkeypatch):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    original = ruta.read_bytes()
    ventana.abrir(ruta)
    ventana.acc_rotar_der.trigger()
    destino = tmp_path / "salida.pdf"
    pedidas = []
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: pedidas.append(s) or str(destino))
    assert ventana.guardar_copia()
    assert pedidas[0].name == "texto_varias_paginas_editado.pdf"
    assert destino.is_file()
    assert ruta.read_bytes() == original
    _, guardado, final = ventana.mensajes[-1]
    assert guardado.es_fiel and final == destino.resolve()
    assert not ventana.windowTitle().startswith("texto_varias_paginas.pdf*")
    # Segunda vez: guarda directo en el mismo destino, sin preguntar
    ventana.acc_rotar_der.trigger()
    assert ventana.guardar_copia()
    assert len(pedidas) == 1
    with pymupdf.open(destino) as res:
        assert res[0].rotation == 180


def test_guardar_como_rechaza_el_original(ventana, copia_pdf, monkeypatch):
    ruta = copia_pdf("tablas.pdf")
    ventana.abrir(ruta)
    ventana.acc_rotar_der.trigger()
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(ruta))
    assert not ventana.guardar_como()
    assert "Sobrescribir original" in ventana.mensajes[-1][1]


def test_sobrescribir_pide_confirmacion(ventana, copia_pdf, monkeypatch):
    ruta = copia_pdf("tablas.pdf")
    ventana.abrir(ruta)
    ventana.acc_rotar_der.trigger()
    monkeypatch.setattr(ventana, "_preguntar", lambda t, x: False)
    assert not ventana.sobrescribir_original()
    with pymupdf.open(ruta) as d:
        assert d[0].rotation == 0
    monkeypatch.setattr(ventana, "_preguntar", lambda t, x: True)
    assert ventana.sobrescribir_original()
    with pymupdf.open(ruta) as d:
        assert d[0].rotation == 90


def test_fidelidad_fallida_pregunta_antes_de_guardar(ventana, copia_pdf, tmp_path, monkeypatch):
    from pdf_studio.core.documento import Operacion

    class Tramposa(Operacion):
        def aplicar(self, doc):
            doc[2].draw_rect(pymupdf.Rect(50, 50, 150, 150), fill=(1, 0, 0))

    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.documento.ejecutar(Tramposa())
    destino = tmp_path / "x.pdf"
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(destino))
    preguntas = []
    monkeypatch.setattr(ventana, "_preguntar_guardar_igual", lambda g: preguntas.append(g) or False)
    assert not ventana.guardar_como()
    assert not destino.exists()
    assert [d.pagina for d in preguntas[0].fidelidad.diferencias] == [2]


def test_abrir_protegido(ventana, copia_pdf, monkeypatch):
    intentos = iter(["mala", "1234"])
    monkeypatch.setattr(ventana, "_pedir_contrasena", lambda n, i: next(intentos))
    assert ventana.abrir(copia_pdf("protegido.pdf"))
    assert ventana.visor.cantidad_paginas == 2


def test_abrir_archivo_invalido_avisa(ventana, tmp_path):
    falso = tmp_path / "roto.pdf"
    falso.write_bytes(b"%PDF-1.4 basura")
    assert not ventana.abrir(falso)
    assert ventana.mensajes[-1][2] is True  # se mostró como error
    assert ventana.documento is None


def test_cerrar_con_cambios_y_cancelar(ventana, copia_pdf, monkeypatch):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.acc_rotar_der.trigger()
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", lambda: "cancelar")
    assert not ventana.cerrar_documento()
    assert ventana.documento is not None
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", lambda: "descartar")
    assert ventana.cerrar_documento()
    assert ventana.documento is None


def test_panel_faltantes_y_copiar(ventana):
    f = Faltante("x", "Programa X", "Sirve para probar.", Importancia.OPCIONAL, "winget install X")
    ventana.faltantes.set_generales([f])
    tarjetas = ventana.faltantes.tarjetas()
    assert len(tarjetas) == 1
    tarjetas[0].boton_copiar.click()
    assert QGuiApplication.clipboard().text() == "winget install X"
    assert "1 elemento" in ventana.faltantes.resumen.text()


def test_chequeo_al_abrir_muestra_fuentes(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("fuentes_recortadas.pdf"))
    assert ventana.faltantes.estado("documento") == "analizando"     # no bloquea: corre aparte
    qtbot.waitUntil(lambda: not ventana.analisis_en_curso, timeout=60_000)
    assert ventana.faltantes.estado("documento") == "ok"
    assert ventana.etiqueta_analisis.text() == ""                     # el estado se limpia
    tabla = ventana.panel_fuentes.tabla
    assert tabla.rowCount() == 4
    nombres = {tabla.item(i, 0).text() for i in range(4)}
    assert "ArialMT" in nombres
    textos = [tabla.item(i, 2).text() for i in range(4)]
    assert "No hace falta (estándar)" in textos
