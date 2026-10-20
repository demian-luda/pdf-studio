"""Rediseño de la interfaz: temas, contraste, íconos, paleta de comandos, accesibilidad y logo."""
from __future__ import annotations

import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from pdf_studio.config import DIR_RECURSOS
from pdf_studio.core.anotaciones import AgregarNota
from pdf_studio.core.imagenes import BibliotecaFirmas
from pdf_studio.ui import iconos
from pdf_studio.ui.tema import CLARO, OSCURO, PALETAS, contraste, gestor
from tests.conftest import DIR_PDFS


@pytest.fixture(autouse=True)
def ajustes_aislados(tmp_path, monkeypatch):
    """Los tests no tocan las preferencias reales del usuario."""
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    from pdf_studio.ui import tema
    monkeypatch.setattr(tema, "_gestor", None)
    yield


@pytest.fixture
def ventana(qtbot, tmp_path, monkeypatch):
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    gestor().aplicar()
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "firmas"))
    qtbot.addWidget(v)
    v.resize(1200, 800)
    v.show()
    qtbot.waitExposed(v)
    monkeypatch.setattr(v, "_informar", lambda *a, **k: None)
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    yield v
    v.cerrar_documento()


# ---------------------------------------------------------------------- contraste WCAG AA
@pytest.mark.parametrize("paleta", [CLARO, OSCURO], ids=["claro", "oscuro"])
def test_contraste_texto_aa(paleta):
    for fondo in (paleta.fondo, paleta.superficie, paleta.superficie_alta):
        assert contraste(paleta.texto, fondo) >= 4.5
        assert contraste(paleta.texto_suave, fondo) >= 4.5
    assert contraste(paleta.sobre_acento, paleta.acento) >= 4.5
    assert contraste(paleta.texto, paleta.acento_suave) >= 4.5
    for color in (paleta.acento, paleta.peligro, paleta.aviso, paleta.exito):
        assert contraste(color, paleta.superficie) >= 4.5


@pytest.mark.parametrize("paleta", [CLARO, OSCURO], ids=["claro", "oscuro"])
def test_contraste_controles_aa(paleta):
    """Contorno de campos/botones y anillo de foco: al menos 3:1 (WCAG 1.4.11)."""
    for fondo in (paleta.fondo, paleta.superficie, paleta.superficie_alta):
        assert contraste(paleta.borde_control, fondo) >= 3.0
        assert contraste(paleta.acento, fondo) >= 3.0


# ---------------------------------------------------------------------- temas y escala
def test_cambiar_tema_y_persistir(qapp):
    g = gestor()
    g.set_tema("oscuro")
    assert g.paleta is OSCURO
    assert OSCURO.fondo.lower() in qapp.styleSheet().lower()
    g.set_tema("claro")
    assert g.paleta is CLARO
    from pdf_studio.ui import tema
    tema._gestor = None
    assert gestor().preferencia == "claro"         # quedó guardado
    gestor().set_tema("sistema")
    assert gestor().paleta in PALETAS.values()


def test_escala_de_interfaz(qapp):
    g = gestor()
    g.set_escala(1.0)
    base = g.tamano_icono(20)
    g.agrandar()
    assert g.escala > 1.0 and g.tamano_icono(20) > base
    assert f"font-size: {round(10 * g.escala, 1)}pt" in qapp.styleSheet()
    g.achicar()
    g.achicar()
    assert g.escala < 1.0
    g.set_escala(1.0)


def test_iconos_cambian_de_color_con_el_tema(qapp):
    gestor().set_tema("claro")
    claro = iconos.crear_icono("save").pixmap(24, 24).toImage()
    gestor().set_tema("oscuro")
    oscuro = iconos.crear_icono("save").pixmap(24, 24).toImage()
    assert claro != oscuro


# ---------------------------------------------------------------------- íconos y tooltips
def test_todas_las_acciones_tienen_icono_y_tooltip_con_atajo(ventana):
    for accion, grupo in ventana._acciones:
        nombre = accion.text().replace("&", "")
        assert not accion.icon().isNull(), f"«{nombre}» no tiene ícono"
        assert iconos.existe(iconos.nombre_de(accion)), nombre
        atajo = accion.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        if atajo:
            assert atajo in accion.toolTip(), f"El tooltip de «{nombre}» no muestra el atajo"


def test_deshacer_describe_la_accion_en_el_tooltip(ventana, copia_pdf):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.acc_rotar_der.trigger()
    tip = ventana.acc_deshacer.toolTip()
    assert "Rotar página 1" in tip and "Ctrl+Z" in tip


def test_cinta_por_pestanas_y_teclado(ventana, qtbot):
    pestanas = ventana.encabezado.pestanas
    assert [pestanas.tabText(i) for i in range(pestanas.count())] == ["Ver", "Editar", "Firmar", "Páginas",
                                                                       "Herramientas"]
    ventana.ir_a_pestana(3)
    assert ventana.encabezado.cinta.currentIndex() == 3
    # Los botones de la cinta se pueden enfocar con Tab
    barra = ventana.encabezado.barras[3]
    boton = barra.widgetForAction(ventana.acc_rotar_der)
    assert boton.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert boton.accessibleName() == "Rotar a la derecha"
    # F6 pasa el foco entre regiones
    ventana.encabezado.pestanas.setFocus()
    ventana.siguiente_region()
    assert ventana.visor.hasFocus()


def test_paleta_de_comandos(ventana, qtbot, copia_pdf):
    from pdf_studio.ui.paleta_comandos import PaletaComandos
    ventana.abrir(copia_pdf("tablas.pdf"))
    paleta = PaletaComandos(ventana.acciones_de_paleta(), ventana)
    qtbot.addWidget(paleta)
    paleta.campo.setText("rotar derecha")
    assert paleta.resultados() == ["Rotar a la derecha"]
    paleta.campo.setText("TEMA OSCURO")                      # sin distinguir mayúsculas
    assert "Tema oscuro" in paleta.resultados()
    paleta.campo.setText("pagina")                             # sin acentos
    assert "Página siguiente" in paleta.resultados()
    paleta.campo.setText("rotar derecha")
    paleta.ejecutar()
    assert ventana.documento.doc[0].rotation == 90


def test_barra_lateral_y_faltantes(ventana):
    from pdf_studio.dependencias.chequeo import Faltante, Importancia
    ventana.faltantes.set_generales([Faltante("x", "Programa X", "Para probar.", Importancia.OPCIONAL, "cmd")])
    ventana._actualizar_insignia()
    assert ventana.boton_faltantes.isVisible() and "1 faltante" in ventana.boton_faltantes.text()
    ventana.boton_faltantes.click()
    assert ventana.lateral.actual == "faltantes"
    ventana.acc_ver_paginas.trigger()
    assert ventana.lateral.actual == "paginas"
    ventana.acc_lateral.trigger()
    assert not ventana.dock_lateral.isVisible()


def test_acerca_de_muestra_el_logo(ventana, qtbot):
    from pdf_studio.ui.acerca import DialogoAcerca
    d = DialogoAcerca(ventana)
    qtbot.addWidget(d)
    logos = [w for w in d.findChildren(type(d.findChild(type(ventana.etiqueta_pagina)))) if w.pixmap() and
             not w.pixmap().isNull()]
    assert logos


# ---------------------------------------------------------------------- logo
def test_logo_ico_multiresolucion():
    with Image.open(DIR_RECURSOS / "logo.ico") as ico:
        tamanos = set(ico.info["sizes"])
    assert {(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)} <= tamanos
    assert (DIR_RECURSOS / "logo.svg").read_text(encoding="utf-8").startswith("<svg")


def test_ventana_usa_el_icono(ventana):
    assert not ventana.windowIcon().isNull()


# ---------------------------------------------------------------------- notas discretas
def test_notas_chicas_y_discretas():
    doc = pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf")
    AgregarNota(0, (300, 140), "Comentario").aplicar(doc)
    pag = doc[0]
    annot = next(pag.annots())
    assert annot.info["content"] == "Comentario"
    pix = doc[0].get_pixmap(dpi=72, clip=pymupdf.Rect(296, 136, 320, 160))
    # Píxeles amarillos del ícono: tiene que ocupar mucho menos que los 16x16 pt del área
    amarillos = sum(1 for x in range(pix.width) for y in range(pix.height)
                    if (lambda r, g, b: r > 200 and g > 150 and b < 140)(*pix.pixel(x, y)[:3]))
    assert 0 < amarillos < 0.5 * 16 * 16


def test_tooltip_de_nota_en_el_visor(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.documento.ejecutar(AgregarNota(0, (300, 140), "Revisar este dato"))
    pag = ventana.documento.doc[0]
    rect = next(pag.annots()).rect
    centro = ventana.visor.a_escena(0, rect).center()
    assert ventana.visor.nota_en(centro) == "Revisar este dato"
