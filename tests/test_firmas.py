"""Fase 2: firmas en imagen, quitar fondo, biblioteca e inserción sin rasterizar."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pymupdf
import pytest
from PIL import Image
from PySide6.QtCore import QPointF

from pdf_studio.core.documento import Documento, ErrorDocumento
from pdf_studio.core.imagenes import (BibliotecaFirmas, InsertarImagen, OpcionesFondo, a_png,
                                      esquinas_rectangulo, luminancia, quitar_fondo,
                                      tiene_transparencia, umbral_automatico)
from pdf_studio.core.paginas import RotarPagina
from tests.conftest import hash_archivo

FIRMA = Path(__file__).resolve().parent / "imagenes_prueba" / "firma_escaneada.jpg"


def imagen_dos_colores() -> bytes:
    """200x80: mitad izquierda roja, derecha azul; las 20 filas de arriba transparentes."""
    a = np.zeros((80, 200, 4), np.uint8)
    a[20:, :100] = (255, 0, 0, 255)
    a[20:, 100:] = (0, 0, 255, 255)
    return a_png(Image.fromarray(a, "RGBA"))


def caja_color(pix: pymupdf.Pixmap, canal: str):
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).astype(int)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    m = (r > 200) & (g < 60) & (b < 60) if canal == "rojo" else (b > 200) & (r < 60) & (g < 60)
    ys, xs = np.where(m)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())) if len(xs) else None


# ---------------------------------------------------------------------- quitar fondo
def test_quitar_fondo_firma_escaneada():
    img = Image.open(FIRMA)
    assert not tiene_transparencia(img)
    res = quitar_fondo(img)
    assert res.mode == "RGBA"
    assert tiene_transparencia(res)
    alfa = np.asarray(res.getchannel("A"))
    # El papel queda transparente (casi todo) y el trazo opaco
    assert (alfa == 0).mean() > 0.6
    assert (alfa == 255).sum() > 1000
    # Se recortaron los márgenes vacíos
    assert res.width < img.width and res.height < img.height
    # Los píxeles opacos conservan el azul de la tinta
    rgb = np.asarray(res)[alfa == 255][:, :3].mean(axis=0)
    assert rgb[2] > rgb[0] + 30


def test_umbral_automatico_entre_tinta_y_papel():
    img = Image.open(FIRMA)
    u = umbral_automatico(luminancia(img))
    assert 60 < u < 220


def test_umbral_ajustable():
    img = Image.open(FIRMA)
    poco = quitar_fondo(img, OpcionesFondo(umbral=60, recortar=False))
    mucho = quitar_fondo(img, OpcionesFondo(umbral=245, recortar=False))
    opacos_poco = (np.asarray(poco.getchannel("A")) > 128).sum()
    opacos_mucho = (np.asarray(mucho.getchannel("A")) > 128).sum()
    assert opacos_poco < opacos_mucho


def test_color_de_tinta_forzado():
    res = quitar_fondo(Image.open(FIRMA), OpcionesFondo(color_tinta=(0, 0, 0)))
    arr = np.asarray(res)
    assert (arr[arr[..., 3] > 0][:, :3] == 0).all()


# ---------------------------------------------------------------------- biblioteca
def test_biblioteca(tmp_path):
    bib = BibliotecaFirmas(tmp_path / "firmas")
    assert bib.listar() == []
    img = quitar_fondo(Image.open(FIRMA))
    a = bib.agregar(img, "Mi firma")
    b = bib.agregar(img, "Mi firma")
    c = bib.agregar(img, 'raro:/\\*?"<>|')
    assert a.name == "Mi firma.png" and b.name == "Mi firma (2).png"
    assert c.parent == bib.carpeta and c.suffix == ".png"
    assert len(bib.listar()) == 3
    with Image.open(a) as guardada:
        assert guardada.mode == "RGBA"
    r = bib.renombrar(b, "Firma corta")
    assert r.name == "Firma corta.png"
    bib.eliminar(a)
    assert [p.name for p in bib.listar()] == [r.name, c.name]
    with pytest.raises(ValueError):
        bib.eliminar(tmp_path / "otra.png")


def test_biblioteca_limita_tamano(tmp_path):
    bib = BibliotecaFirmas(tmp_path)
    enorme = Image.new("RGBA", (5000, 1000), (0, 0, 0, 255))
    with Image.open(bib.agregar(enorme, "grande")) as img:
        assert max(img.size) == 2000


# ---------------------------------------------------------------------- inserción
@pytest.mark.parametrize("rot_pagina", [0, 90, 180, 270])
@pytest.mark.parametrize("angulo", [0, 90, 30])
def test_insercion_queda_donde_se_ve(rot_pagina, angulo):
    """La imagen aparece en el lugar, tamaño y orientación visual pedidos, con cualquier
    rotación de página."""
    doc = pymupdf.open()
    pag = doc.new_page(width=595, height=842)
    pag.set_rotation(rot_pagina)
    centro = pymupdf.Point(300, 200) * pag.derotation_matrix
    esq = esquinas_rectangulo((centro.x, centro.y), 200, 80, angulo, rot_pagina)
    InsertarImagen(0, esq, imagen_dos_colores()).aplicar(doc)
    pix = doc[0].get_pixmap(dpi=72)
    rojo, azul = caja_color(pix, "rojo"), caja_color(pix, "azul")
    if angulo == 0:
        assert rojo == pytest.approx((200, 180, 299, 239), abs=1)
        assert azul == pytest.approx((300, 180, 399, 239), abs=1)
    elif angulo == 90:   # horario: lo que estaba a la izquierda queda arriba
        assert rojo == pytest.approx((260, 100, 319, 199), abs=1)
        assert azul == pytest.approx((260, 200, 319, 299), abs=1)
    else:
        assert rojo[0] < azul[0] and rojo[1] < azul[1]   # inclinada hacia abajo a la derecha


def test_insercion_en_pagina_con_mediabox_desplazado():
    doc = pymupdf.open()
    pag = doc.new_page(width=595, height=842)
    pag.set_mediabox(pymupdf.Rect(50, 100, 645, 942))
    esq = esquinas_rectangulo((300, 200), 200, 80)
    InsertarImagen(0, esq, imagen_dos_colores()).aplicar(doc)
    pix = doc[0].get_pixmap(dpi=72)
    assert caja_color(pix, "rojo") == pytest.approx((200, 180, 299, 239), abs=1)


def test_insercion_no_rasteriza_ni_toca_el_resto(copia_pdf, tmp_path):
    ruta = copia_pdf("imagen_fondo.pdf")
    antes = hash_archivo(ruta)
    d = Documento(ruta)
    try:
        texto_antes = d.doc[0].get_text()
        imagenes_antes = len(d.doc[0].get_images())
        firma = a_png(quitar_fondo(Image.open(FIRMA)))
        d.ejecutar(InsertarImagen(0, esquinas_rectangulo((400, 700), 150, 55, 10), firma))
        assert d.doc[0].get_text() == texto_antes          # el texto sigue siendo texto
        assert len(d.doc[0].get_images()) == imagenes_antes + 1
        g = d.guardar(tmp_path / "firmado.pdf")
        assert g.es_fiel and g.incremental and g.bytes_originales_intactos
        assert g.fidelidad.comparadas == [1] and g.fidelidad.excluidas == [0]
        with pymupdf.open(tmp_path / "firmado.pdf") as res:
            assert len(res[0].get_images()) == imagenes_antes + 1
            # La imagen insertada conserva su transparencia (máscara SMask)
            xref = [i for i in res[0].get_images() if i[1]][0]
            assert xref[1] > 0
        d.deshacer()
        assert len(d.doc[0].get_images()) == imagenes_antes
    finally:
        d.cerrar()
    assert hash_archivo(ruta) == antes


def test_jpg_se_inserta_sin_recomprimir():
    datos = FIRMA.read_bytes()
    doc = pymupdf.open()
    doc.new_page()
    InsertarImagen(0, esquinas_rectangulo((200, 200), 150, 53), datos).aplicar(doc)
    xref = doc[0].get_images()[0][0]
    assert doc.extract_image(xref)["ext"] == "jpeg"
    assert doc.xref_stream_raw(xref) == datos


def test_insercion_fuera_de_pagina_falla():
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ErrorDocumento):
        InsertarImagen(0, esquinas_rectangulo((5000, 5000), 100, 40), imagen_dos_colores()).aplicar(doc)


def test_rehacer_y_otras_operaciones(copia_pdf, tmp_path):
    d = Documento(copia_pdf("texto_varias_paginas.pdf"))
    try:
        d.ejecutar(InsertarImagen(2, esquinas_rectangulo((300, 600), 150, 60), imagen_dos_colores()))
        d.ejecutar(RotarPagina(2, 90))
        d.deshacer()
        d.deshacer()
        d.rehacer()
        assert len(d.doc[2].get_images()) == 1
        g = d.guardar(tmp_path / "x.pdf")
        assert g.es_fiel and g.fidelidad.excluidas == [2]
    finally:
        d.cerrar()


# ---------------------------------------------------------------------- interfaz
@pytest.fixture
def ventana(qtbot, monkeypatch, tmp_path):
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "firmas"))
    qtbot.addWidget(v)
    v.resize(1100, 800)
    v.show()
    qtbot.waitExposed(v)
    monkeypatch.setattr(v, "_informar", lambda *a, **k: None)
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    yield v
    v.cerrar_documento()


def test_agregar_firma_con_quitar_fondo(ventana, monkeypatch):
    monkeypatch.setattr(ventana, "_pedir_ruta_imagen", lambda t: str(FIRMA))
    usados = []

    def editar(img, nombre):
        usados.append(nombre)
        return quitar_fondo(img), "Firma Lucía"
    monkeypatch.setattr(ventana, "_editar_fondo", editar)
    ventana.agregar_firma()
    assert usados == ["firma_escaneada"]
    assert [p.name for p in ventana.biblioteca.listar()] == ["Firma Lucía.png"]
    assert ventana.panel_firmas.lista.count() == 1
    assert ventana.panel_firmas.seleccionada().name == "Firma Lucía.png"


def test_dialogo_quitar_fondo(qtbot):
    from pdf_studio.ui.firmas import DialogoQuitarFondo
    d = DialogoQuitarFondo(Image.open(FIRMA), "firma")
    qtbot.addWidget(d)
    d.umbral.setValue(200)
    d.color.setCurrentIndex(1)  # negro
    d.actualizar_vista()
    assert not d.vista_resultado.pixmap().isNull()
    d.aceptar()
    assert d.resultado is not None and tiene_transparencia(d.resultado)


def test_colocar_mover_escalar_rotar_y_aplicar(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ruta = ventana.biblioteca.agregar(quitar_fondo(Image.open(FIRMA)), "firma")
    ventana.panel_firmas.recargar()
    ventana.insertar_firma(ruta)
    item = ventana.visor.colocacion
    assert item is not None
    assert ventana.panel_firmas.caja_colocacion.isVisible()
    pagina_item = ventana.visor.items[0]
    destino = pagina_item.mapToScene(QPointF(300, 650))
    item.setPos(destino)
    item.set_ancho(160)
    item.rotar_hacia(destino + QPointF(100, -100))     # 45°
    assert item.angulo == pytest.approx(45)
    item.rotar_hacia(destino + QPointF(100, -90), en_pasos=True)
    assert item.angulo == pytest.approx(45)
    item.escalar_hacia(item.mapToScene(item.rect_local().bottomRight() * 1.5))
    assert item.ancho == pytest.approx(240, rel=0.01)
    qtbot.keyClick(ventana.visor, "\r")    # Enter aplica
    assert ventana.visor.colocacion is None
    imgs = ventana.documento.doc[0].get_images()
    assert len(imgs) == 1
    assert "Insertar firma en página 1" in ventana.acc_deshacer.text()
    ventana.acc_deshacer.trigger()
    assert ventana.documento.doc[0].get_images() == []


def test_escape_cancela_y_recargar_conserva(ventana, copia_pdf, qtbot):
    from PySide6.QtCore import Qt
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.iniciar_colocacion(imagen_dos_colores(), "imagen")
    item = ventana.visor.colocacion
    item.setPos(item.pos() + QPointF(30, 0))
    pos = item.pos()
    ventana.acc_rotar_der.trigger()          # reconstruye la escena
    assert ventana.visor.colocacion is item
    assert item.scene() is ventana.visor.scene()
    assert item.pos() == pos
    qtbot.keyClick(ventana.visor, Qt.Key.Key_Right)
    assert item.pos().x() == pytest.approx(pos.x() + 1)
    qtbot.keyClick(ventana.visor, Qt.Key.Key_Escape)
    assert ventana.visor.colocacion is None
    assert not ventana.panel_firmas.caja_colocacion.isVisible()


def test_firma_aplicada_coincide_con_lo_visto(ventana, copia_pdf):
    """Lo que se ve en el visor es lo que queda en el PDF (posición y tamaño)."""
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.iniciar_colocacion(imagen_dos_colores(), "imagen")
    item = ventana.visor.colocacion
    item.setPos(ventana.visor.items[0].mapToScene(QPointF(300, 200)))
    item.set_ancho(200)
    ventana.aplicar_colocacion()
    pix = ventana.documento.doc[0].get_pixmap(dpi=72)
    assert caja_color(pix, "rojo") == pytest.approx((200, 180, 299, 239), abs=1)


def test_guardar_durante_colocacion_aplica_primero(ventana, copia_pdf, tmp_path, monkeypatch):
    ventana.abrir(copia_pdf("tablas.pdf"))
    ventana.iniciar_colocacion(imagen_dos_colores(), "imagen")
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "g.pdf"))
    monkeypatch.setattr(ventana, "_mostrar_guardado", lambda g, d: None)
    assert ventana.guardar_copia()
    with pymupdf.open(tmp_path / "g.pdf") as res:
        assert len(res[0].get_images()) == 1
