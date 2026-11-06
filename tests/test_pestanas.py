"""Varios PDFs en pestañas de la misma ventana y retomar la lectura donde se dejó."""
from __future__ import annotations

import json

import pymupdf
import pytest

from pdf_studio import posiciones


def _ir(ventana, pagina: int, zoom: float | None = None) -> None:
    if zoom is not None:
        ventana.visor.set_zoom(zoom)
    ventana.visor.ir_a_pagina(pagina)


# ---------------------------------------------------------------------- pestañas
def test_cada_pdf_abre_su_pestana_y_no_se_repite(ventana, copia_pdf):
    a, b = copia_pdf("texto_varias_paginas.pdf"), copia_pdf("tablas.pdf")
    assert ventana.abrir(a) and ventana.abrir(b)
    assert len(ventana.pestanas) == 2 and ventana.barra_pestanas.count() == 2
    assert ventana.barra_pestanas.isVisible()
    assert ventana.documento.ruta_original == b.resolve()
    assert ventana.barra_pestanas.tabText(1) == "tablas.pdf"
    assert ventana.barra_pestanas.tabToolTip(1) == str(b.resolve())
    # Abrir uno que ya está abierto pasa a su pestaña, sin duplicarlo
    assert ventana.abrir(a)
    assert len(ventana.pestanas) == 2
    assert ventana.documento.ruta_original == a.resolve()
    assert ventana.barra_pestanas.currentIndex() == 0


def test_al_volver_a_una_pestana_sigue_donde_estaba(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    _ir(ventana, 3, zoom=1.5)
    ventana.abrir(copia_pdf("tablas.pdf"))
    assert ventana.visor.pagina_actual == 0
    ventana.barra_pestanas.setCurrentIndex(0)          # como un clic en la pestaña
    assert ventana.documento.ruta_original.name == "texto_varias_paginas.pdf"
    assert ventana.visor.pagina_actual == 3
    assert ventana.visor.zoom == pytest.approx(1.5, abs=0.01)
    assert ventana.miniaturas.documento is ventana.documento
    assert ventana.busqueda.isHidden()
    assert "Página 4 de 6" in ventana.etiqueta_pagina.text()


def test_ctrl_tab_recorre_las_pestanas(ventana, copia_pdf):
    for nombre in ("texto_varias_paginas.pdf", "tablas.pdf", "imagen_fondo.pdf"):
        ventana.abrir(copia_pdf(nombre))
    ventana.pestana_vecina(1)                           # da la vuelta: del último al primero
    assert ventana.documento.ruta_original.name == "texto_varias_paginas.pdf"
    ventana.pestana_vecina(-1)
    assert ventana.documento.ruta_original.name == "imagen_fondo.pdf"


def test_f6_pasa_por_las_pestanas_de_documentos(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.activateWindow()
    ventana.encabezado.pestanas.setFocus()
    ventana.siguiente_region()
    assert ventana.barra_pestanas.hasFocus()
    ventana.siguiente_region()
    assert ventana.visor.hasFocus()
    assert ventana.barra_pestanas.accessibleName() == "Documentos abiertos"


def test_mover_una_pestana_reordena_los_documentos(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.barra_pestanas.moveTab(1, 0)
    assert [p.documento.ruta_original.name for p in ventana.pestanas] == ["tablas.pdf", "texto_varias_paginas.pdf"]
    ventana.barra_pestanas.setCurrentIndex(1)
    assert ventana.documento.ruta_original.name == "texto_varias_paginas.pdf"


def test_la_pestana_marca_los_cambios_sin_guardar(ventana, copia_pdf):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.acc_rotar_der.trigger()
    assert ventana.barra_pestanas.tabText(0) == "tablas.pdf •"
    ventana.acc_deshacer.trigger()
    assert ventana.barra_pestanas.tabText(0) == "tablas.pdf"


def test_cerrar_una_pestana_con_cambios_la_muestra_y_pregunta(ventana, copia_pdf, monkeypatch):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.acc_rotar_der.trigger()                      # cambio en tablas.pdf
    con_cambios = ventana._activa
    ventana.barra_pestanas.setCurrentIndex(0)           # se pasa a la otra
    vista_al_preguntar = []

    def preguntar():
        vista_al_preguntar.append(ventana.documento.ruta_original.name)
        return "cancelar"
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", preguntar)
    assert not ventana.cerrar_pestana(con_cambios)
    assert vista_al_preguntar == ["tablas.pdf"]          # se ve el documento por el que pregunta
    assert len(ventana.pestanas) == 2
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", lambda: "descartar")
    assert ventana.cerrar_pestana(con_cambios)
    assert len(ventana.pestanas) == 1
    assert ventana.documento.ruta_original.name == "texto_varias_paginas.pdf"
    assert ventana.visor.documento is ventana.documento


def test_el_boton_de_la_pestana_la_cierra(ventana, copia_pdf):
    from PySide6.QtWidgets import QTabBar
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.abrir(copia_pdf("tablas.pdf"))
    boton = ventana.barra_pestanas.tabButton(0, QTabBar.ButtonPosition.RightSide)
    assert boton.accessibleName() == "Cerrar texto_varias_paginas.pdf"
    assert not boton.icon().isNull()
    boton.click()
    assert [p.documento.ruta_original.name for p in ventana.pestanas] == ["tablas.pdf"]
    ventana.cerrar_documento()
    assert not ventana.pestanas and ventana.documento is None
    assert not ventana.barra_pestanas.isVisible()


def test_cerrar_todo_pregunta_solo_por_las_que_tienen_cambios(ventana, copia_pdf, monkeypatch):
    for nombre in ("texto_varias_paginas.pdf", "tablas.pdf", "imagen_fondo.pdf"):
        ventana.abrir(copia_pdf(nombre))
        if nombre != "tablas.pdf":
            ventana.acc_rotar_der.trigger()
    preguntas = []

    def preguntar():
        preguntas.append(ventana.documento.ruta_original.name)
        return "descartar"
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", preguntar)
    assert ventana.cerrar_todas()
    assert sorted(preguntas) == ["imagen_fondo.pdf", "texto_varias_paginas.pdf"]
    assert not ventana.pestanas and ventana.documento is None


def test_cancelar_al_cerrar_todo_deja_la_ventana_usable(ventana, copia_pdf, monkeypatch):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.acc_rotar_der.trigger()
    ventana.abrir(copia_pdf("tablas.pdf"))
    monkeypatch.setattr(ventana, "_preguntar_guardar_cambios", lambda: "cancelar")
    assert not ventana.cerrar_todas()
    # tablas.pdf (sin cambios) se cerró; queda el que tiene cambios, a la vista
    assert [p.documento.ruta_original.name for p in ventana.pestanas] == ["texto_varias_paginas.pdf"]
    assert ventana.visor.documento is ventana.documento is ventana.pestanas[0].documento


def test_la_redaccion_marcada_es_de_su_pestana(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.marcar_para_redactar(0, pymupdf.Rect(72, 72, 200, 100))
    assert not ventana.aviso.isHidden()
    ventana.abrir(copia_pdf("tablas.pdf"))
    assert ventana.redaccion_pendiente.vacia
    assert not ventana.acc_aplicar_redaccion.isEnabled()
    ventana.barra_pestanas.setCurrentIndex(0)
    assert not ventana.redaccion_pendiente.vacia
    assert not ventana.aviso.isHidden() and ventana.acc_aplicar_redaccion.isEnabled()


def test_el_aviso_de_cada_documento_vuelve_con_su_pestana(ventana, copia_pdf):
    ventana.abrir(copia_pdf("formulario.pdf"))
    assert not ventana.aviso.isHidden() and "formulario" in ventana.aviso.texto.text()
    ventana.abrir(copia_pdf("tablas.pdf"))
    assert ventana.aviso.isHidden()
    ventana.barra_pestanas.setCurrentIndex(0)
    assert not ventana.aviso.isHidden() and "formulario" in ventana.aviso.texto.text()
    ventana.aviso.hide()                                   # el usuario lo cierra
    ventana.barra_pestanas.setCurrentIndex(1)
    ventana.barra_pestanas.setCurrentIndex(0)
    assert ventana.aviso.isHidden()                        # y no vuelve a aparecer


def test_lo_que_llega_con_un_dialogo_abierto_espera(ventana, copia_pdf, monkeypatch, qtbot):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ocupada = [True]
    monkeypatch.setattr(ventana, "_ocupada", lambda: ocupada[0])
    ventana.abrir_varios([str(copia_pdf("texto_varias_paginas.pdf")), str(copia_pdf("imagen_fondo.pdf"))])
    assert len(ventana.pestanas) == 1 and ventana.documento.ruta_original.name == "tablas.pdf"
    assert "cuando termine" in ventana.statusBar().currentMessage()
    ocupada[0] = False
    qtbot.waitUntil(lambda: len(ventana.pestanas) == 3, timeout=5000)
    assert ventana.documento.ruta_original.name == "imagen_fondo.pdf"


def test_un_archivo_que_no_abre_no_deja_pestana(ventana, tmp_path, copia_pdf):
    ventana.abrir(copia_pdf("tablas.pdf"))
    roto = tmp_path / "roto.pdf"
    roto.write_bytes(b"esto no es un PDF")
    assert not ventana.abrir(roto)
    assert len(ventana.pestanas) == 1 and ventana.documento.ruta_original.name == "tablas.pdf"
    assert ventana.visor.documento is ventana.documento


def test_el_analisis_de_fuentes_es_de_cada_pestana(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("fuentes_recortadas.pdf"))
    qtbot.waitUntil(lambda: not ventana.analisis_en_curso, timeout=60_000)
    filas_a = ventana.panel_fuentes.tabla.rowCount()
    assert filas_a > 0
    ventana.abrir(copia_pdf("tablas.pdf"))
    qtbot.waitUntil(lambda: not ventana.analisis_en_curso, timeout=60_000)
    ventana.barra_pestanas.setCurrentIndex(0)
    # Al volver se muestra el resultado guardado, sin analizar de nuevo
    assert not ventana.analisis_en_curso and not ventana.analizador.ocupado
    assert ventana.panel_fuentes.tabla.rowCount() == filas_a


def test_cambiar_de_pestana_durante_el_analisis_lo_retoma_despues(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("fuentes_recortadas.pdf"))
    primera = ventana._activa
    assert primera.analisis == "analizando"
    ventana.abrir(copia_pdf("tablas.pdf"))                 # antes de que termine
    assert primera.analisis == "pendiente"
    ventana.barra_pestanas.setCurrentIndex(0)
    qtbot.waitUntil(lambda: primera.analisis == "ok", timeout=60_000)
    assert ventana.panel_fuentes.tabla.rowCount() > 0


# ---------------------------------------------------------------------- análisis después de la primera página
def test_el_analisis_arranca_despues_de_ver_la_primera_pagina(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("fuentes_recortadas.pdf"))
    # El estado ya dice «analizando», pero el proceso todavía no compite con el dibujo
    assert ventana.analisis_en_curso
    assert not ventana.visor.ya_pinto and not ventana.analizador.ocupado
    qtbot.waitUntil(lambda: ventana.visor.ya_pinto, timeout=10_000)
    qtbot.waitUntil(lambda: ventana.analizador.ocupado or not ventana.analisis_en_curso, timeout=10_000)
    qtbot.waitUntil(lambda: not ventana.analisis_en_curso, timeout=60_000)
    assert ventana.faltantes.estado("documento") == "ok"


def test_sin_dibujar_el_analisis_igual_arranca(qtbot, tmp_path, copia_pdf):
    """Con la ventana oculta nunca se dibuja una página: arranca igual, al rato."""
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    v.abrir(copia_pdf("tablas.pdf"))
    qtbot.waitUntil(lambda: not v.analisis_en_curso, timeout=60_000)
    assert v.faltantes.estado("documento") == "ok"
    v.cerrar_documento()


def test_cerrar_enseguida_no_deja_tareas_diferidas(qtbot, tmp_path):
    """Si la ventana se cierra antes de que arranque lo diferido (índice de fuentes), no arranca
    después: un hilo vivo al destruir la ventana cerraría el proceso de golpe."""
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import ESPERA_MAX_MS, VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=True, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    assert v._tras_pintura                                  # índice y chequeo, esperando
    v.close()
    qtbot.wait(ESPERA_MAX_MS + 300)
    assert not v._hilos and not v._tras_pintura


# ---------------------------------------------------------------------- retomar la lectura
def test_al_reabrir_un_pdf_vuelve_a_la_ultima_pagina_y_zoom(ventana, copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    ventana.abrir(ruta)
    _ir(ventana, 4, zoom=1.3)
    ventana.cerrar_documento()
    ventana.abrir(ruta)
    assert ventana.visor.pagina_actual == 4
    assert ventana.visor.zoom == pytest.approx(1.3, abs=0.01)
    assert "Seguís en la página 5" in ventana.statusBar().currentMessage()


def test_al_reabrir_mantiene_ajustar_al_ancho(ventana, copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    ventana.abrir(ruta)
    ventana.acc_ajustar_ancho.trigger()
    ventana.visor.ir_a_pagina(2)
    ventana.cerrar_documento()
    ventana.resize(900, 700)                               # otra ventana, otro ancho
    ventana.abrir(ruta)
    assert ventana.visor.pagina_actual == 2
    assert ventana.visor._ajuste == "ancho"


def test_la_posicion_se_guarda_al_cerrar_la_ventana(ventana, copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    ventana.abrir(ruta)
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.barra_pestanas.setCurrentIndex(0)
    _ir(ventana, 5)
    ventana.barra_pestanas.setCurrentIndex(1)               # queda en otra pestaña
    assert ventana.cerrar_todas()
    assert posiciones.leer(ruta)["pagina"] == 5


def test_posicion_de_una_pagina_que_ya_no_existe(ventana, copia_pdf):
    """Si el archivo cambió y tiene menos páginas, se va a la última."""
    ruta = copia_pdf("tablas.pdf")
    posiciones.guardar(ruta, {"pagina": 40, "zoom": 1.0, "ajuste": None, "desplazamiento": 0.5})
    ventana.abrir(ruta)
    assert ventana.visor.pagina_actual == 0
    assert "Seguís" not in ventana.statusBar().currentMessage()


def test_posiciones_recuerda_las_ultimas_y_tolera_un_archivo_roto(tmp_path, monkeypatch):
    archivo = tmp_path / "pos.json"
    monkeypatch.setattr(posiciones, "_archivo", lambda: archivo)
    monkeypatch.setattr(posiciones, "MAX_ARCHIVOS", 3)
    assert posiciones.leer(tmp_path / "a.pdf") is None
    for i in range(5):
        posiciones.guardar(tmp_path / f"{i}.pdf", {"pagina": i, "zoom": 1.0, "ajuste": None})
    datos = json.loads(archivo.read_text(encoding="utf-8"))
    assert len(datos) == 3
    assert posiciones.leer(tmp_path / "0.pdf") is None and posiciones.leer(tmp_path / "4.pdf")["pagina"] == 4
    # Volver a guardar uno lo hace el más reciente
    posiciones.guardar(tmp_path / "2.pdf", {"pagina": 9, "zoom": 1.0, "ajuste": None})
    posiciones.guardar(tmp_path / "5.pdf", {"pagina": 5, "zoom": 1.0, "ajuste": None})
    assert posiciones.leer(tmp_path / "2.pdf")["pagina"] == 9
    assert posiciones.leer(tmp_path / "3.pdf") is None
    # Mayúsculas distintas: el mismo archivo en Windows
    assert posiciones.leer(str(tmp_path / "5.pdf").upper())["pagina"] == 5
    archivo.write_text("{ roto", encoding="utf-8")
    assert posiciones.leer(tmp_path / "5.pdf") is None
    posiciones.guardar(tmp_path / "6.pdf", {"pagina": 1, "zoom": 1.0, "ajuste": None})
    assert posiciones.leer(tmp_path / "6.pdf")["pagina"] == 1
    assert not list(tmp_path.glob(".posiciones-*"))      # no quedan temporales


# ---------------------------------------------------------------------- memoria
def test_cerrar_pestanas_libera_sus_documentos(ventana, copia_pdf, qtbot):
    """Una pestaña cerrada no queda retenida (antes la retenía la lambda de su botón de
    cerrar, que Qt no destruye al sacar la pestaña), ni su documento de MuPDF."""
    import gc

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QToolButton

    from pdf_studio.core.documento import Documento
    from pdf_studio.ui.pestanas import Pestana

    def vivos(tipo) -> int:
        gc.collect()
        return sum(1 for o in gc.get_objects() if type(o) is tipo)

    def botones() -> int:
        return len(ventana.barra_pestanas.findChildren(QToolButton))

    sin_pestanas = botones()                 # los de desplazamiento propios de QTabBar
    for _ in range(2):
        for nombre in ("texto_varias_paginas.pdf", "tablas.pdf", "imagen_fondo.pdf"):
            ventana.abrir(copia_pdf(nombre))
        assert vivos(Pestana) == 3 and botones() == sin_pestanas + 3
        ventana.cerrar_pestana(ventana.pestanas[0])      # con su botón (lo mismo que el clic)
        assert ventana.cerrar_todas()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert vivos(Pestana) == 0 and vivos(Documento) == 0
        assert botones() == sin_pestanas


def test_el_boton_de_cerrar_encuentra_su_pestana_despues_de_moverla(ventana, copia_pdf):
    from PySide6.QtWidgets import QTabBar
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.barra_pestanas.moveTab(1, 0)
    ventana.barra_pestanas.tabButton(0, QTabBar.ButtonPosition.RightSide).click()
    assert [p.documento.ruta_original.name for p in ventana.pestanas] == ["texto_varias_paginas.pdf"]
