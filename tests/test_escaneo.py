"""Importar fotos de documentos como PDF escaneado: detección del papel, perspectiva,
inclinación, contraste, tamaño de página, armado del PDF, OCR y la interfaz."""
from __future__ import annotations

import numpy as np
import pymupdf
import pytest

pytest.importorskip("cv2")

from pdf_studio.core import escaneo  # noqa: E402
from pdf_studio.core.documento import Limitacion  # noqa: E402
from pdf_studio.core.escaneo import AjusteFoto, Mejora, OpcionesEscaneo, TamanoPapel  # noqa: E402
from tests import fotos_sinteticas as fs  # noqa: E402
from tests.conftest import hash_archivo  # noqa: E402

A4 = 297 / 210


def _hay_ocr() -> bool:
    from pdf_studio.core import ocr
    try:
        ocr.entorno_ocr()
        return True
    except ocr.ErrorOCR:
        return False


requiere_tesseract = pytest.mark.skipif(not _hay_ocr(), reason="Tesseract con español no está instalado")


@pytest.fixture
def fotos(tmp_path):
    """Tres fotos: dos inclinadas y en perspectiva, y un escaneo plano torcido."""
    rutas = []
    for i, (angulo, perspectiva) in enumerate([(8, 0.08), (-12, 0.05)]):
        img, _ = fs.foto(angulo, perspectiva, semilla=i)
        rutas.append(tmp_path / f"foto{i}.jpg")
        fs.guardar(img, rutas[-1])
    rutas.append(tmp_path / "plano.png")
    fs.guardar(fs.escaneo_plano(4.0), rutas[-1])
    return rutas


# ---------------------------------------------------------------------- papel y perspectiva
@pytest.mark.parametrize("angulo,perspectiva,sombra,lienzo", [
    (8, 0.08, True, None), (-12, 0.05, True, None), (0, 0, False, None), (20, 0.1, True, None),
    (-4, 0.15, True, None), (30, 0.0, True, (2600, 2800)),       # muy girada: la hoja entra en la foto
])
def test_detecta_el_papel_inclinado_y_en_perspectiva(angulo, perspectiva, sombra, lienzo):
    img, reales = fs.foto(angulo, perspectiva, sombra=sombra, **({"lienzo": lienzo} if lienzo else {}))
    esquinas = escaneo.detectar_papel(img)
    assert esquinas is not None
    diagonal = float(np.linalg.norm(reales[2] - reales[0]))
    error = float(np.abs(esquinas - escaneo.ordenar_esquinas(reales)).max())
    assert error < 0.01 * diagonal, f"esquinas corridas {error:.1f} px"
    derecha = escaneo.corregir_perspectiva(img, esquinas)
    assert derecha.shape[0] / derecha.shape[1] == pytest.approx(A4, rel=0.02)     # vuelve a ser A4
    assert abs(escaneo.angulo_inclinacion(derecha)) < 0.5                          # y queda derecha


def test_escaneo_plano_no_confunde_un_recuadro_con_el_papel():
    """La hoja ocupa toda la imagen: el recuadro dibujado adentro no es el papel."""
    assert escaneo.detectar_papel(fs.escaneo_plano(3.0)) is None


def test_papel_sobre_fondo_claro_no_se_inventa():
    img, _ = fs.foto(5, 0.05)
    claro = np.full_like(img, 245)
    assert escaneo.detectar_papel(claro) is None


@pytest.mark.parametrize("angulo", [-10.0, -6.0, -2.5, 3.0, 7.5])
def test_corrige_la_inclinacion(angulo):
    plano = fs.escaneo_plano(angulo)
    medido = escaneo.angulo_inclinacion(plano)
    assert medido == pytest.approx(angulo, abs=0.3)
    derecho = escaneo.enderezar(plano, medido)
    assert derecho.shape == plano.shape                    # conserva el tamaño (y la proporción)
    assert abs(escaneo.angulo_inclinacion(derecho)) < 0.3


def test_hoja_derecha_no_se_toca():
    hoja = fs.hoja()
    assert escaneo.angulo_inclinacion(hoja) == 0.0
    assert escaneo.enderezar(hoja, 0.0) is hoja


# ---------------------------------------------------------------------- contraste
def test_mejora_deja_el_fondo_blanco_aunque_haya_sombra():
    img, _ = fs.foto(6, 0.05, sombra=True)
    papel = escaneo.corregir_perspectiva(img, escaneo.detectar_papel(img))
    h, w = papel.shape[:2]
    sombreada = papel[int(h * 0.8):, int(w * 0.8):]           # la esquina con menos luz
    assert np.median(sombreada) < 225
    for mejora in (Mejora.COLOR, Mejora.GRISES):
        res = escaneo.mejorar(papel, mejora)
        assert np.median(res[int(h * 0.8):, int(w * 0.8):]) >= 245, mejora
    byn = escaneo.mejorar(papel, Mejora.BYN)
    assert set(np.unique(byn)) <= {0, 255}
    # El texto sigue estando (los trazos negros no se pierden)
    tinta = np.count_nonzero(byn == 0) / byn.size
    assert 0.01 < tinta < 0.2
    assert escaneo.mejorar(papel, Mejora.NINGUNA) is papel


# ---------------------------------------------------------------------- tamaño de página
@pytest.mark.parametrize("ancho,alto,tamano,esperado,llena", [
    (1000, 1414, TamanoPapel.AUTO, (595, 842), True),        # A4
    (1414, 1000, TamanoPapel.AUTO, (842, 595), True),        # A4 apaisado
    (1000, 1294, TamanoPapel.AUTO, (612, 792), True),        # Carta
    (1000, 1650, TamanoPapel.AUTO, (612, 1008), True),       # Oficio
    (1000, 1000, TamanoPapel.AUTO, (842, 842), True),        # cuadrado: su proporción, largo de A4
    (1000, 1000, TamanoPapel.A4, (595, 842), False),         # forzado a A4: sin deformar, con margen
    (1000, 1430, TamanoPapel.A4, (595, 842), True),          # casi A4: se ajusta
])
def test_medida_de_pagina(ancho, alto, tamano, esperado, llena):
    w, h, llenar = escaneo.medida_de_pagina(ancho, alto, tamano)
    assert (round(w), round(h)) == esperado and llenar == llena


# ---------------------------------------------------------------------- PDF
def test_crea_el_pdf_sin_tocar_las_fotos(fotos, tmp_path):
    hashes = [hash_archivo(r) for r in fotos]
    destino = tmp_path / "escaneado.pdf"
    informe = escaneo.crear_pdf([AjusteFoto(str(r)) for r in fotos], OpcionesEscaneo(), destino)
    assert informe.paginas == 3
    assert len(informe.avisos) == 1 and "plano.png" in informe.avisos[0]    # sin borde: foto entera
    assert [hash_archivo(r) for r in fotos] == hashes
    with pymupdf.open(destino) as doc:
        assert doc.page_count == 3
        assert all((round(p.rect.width), round(p.rect.height)) == (595, 842) for p in doc)
        for p in doc:
            (xref, *_), = p.get_images(full=True)
            assert doc.xref_get_key(xref, "Filter")[1] == "/DCTDecode"     # JPEG tal cual
            # 200 ppp como mucho: no se guarda más resolución que la pedida
            assert doc.xref_get_key(xref, "Height")[1] and int(doc.xref_get_key(xref, "Height")[1]) <= 842 / 72 * 200 + 2
    assert not list(tmp_path.glob("*.pdfstudio-tmp"))


def test_blanco_y_negro_queda_liviano(fotos, tmp_path):
    color, byn = tmp_path / "color.pdf", tmp_path / "byn.pdf"
    ajustes = [AjusteFoto(str(r)) for r in fotos]
    escaneo.crear_pdf(ajustes, OpcionesEscaneo(), color)
    escaneo.crear_pdf(ajustes, OpcionesEscaneo(mejora=Mejora.BYN), byn)
    assert byn.stat().st_size < color.stat().st_size / 3
    with pymupdf.open(byn) as doc:
        xref = doc[0].get_images(full=True)[0][0]
        assert doc.xref_get_key(xref, "BitsPerComponent")[1] == "1"


def test_esquinas_y_rotacion_manuales(tmp_path):
    img, reales = fs.foto(0, 0, sombra=False)
    ruta = tmp_path / "girada.jpg"
    fs.guardar(np.ascontiguousarray(np.rot90(img)), ruta)       # foto acostada
    leida = escaneo.leer_imagen(ruta, rotacion=90)
    assert leida.shape[:2] == img.shape[:2]                      # girada 90° queda derecha
    # Esquinas a mano: solo la mitad de arriba de la hoja
    tl, tr, br, bl = escaneo.ordenar_esquinas(reales)
    medio_izq, medio_der = (tl + bl) / 2, (tr + br) / 2
    manual = escaneo.normalizar_esquinas(np.array([tl, tr, medio_der, medio_izq]), leida)
    hecha = escaneo.procesar(leida, AjusteFoto(str(ruta), 90, manual), OpcionesEscaneo(enderezar=False))
    h, w = hecha.imagen.shape[:2]
    assert h / w == pytest.approx(A4 / 2, rel=0.03)


def test_respeta_la_orientacion_exif(tmp_path):
    from PIL import Image
    img, _ = fs.foto(0, 0, sombra=False)
    acostada = Image.fromarray(np.ascontiguousarray(np.rot90(img))[:, :, ::-1])
    exif = Image.Exif()
    exif[0x0112] = 6                      # «girar 90° a la derecha para ver derecho»
    ruta = tmp_path / "celular.jpg"
    acostada.save(ruta, exif=exif)
    assert escaneo.leer_imagen(ruta).shape[:2] == img.shape[:2]


def test_errores_claros(tmp_path, fotos):
    with pytest.raises(Limitacion):
        escaneo.crear_pdf([], OpcionesEscaneo(), tmp_path / "x.pdf")
    roto = tmp_path / "roto.jpg"
    roto.write_bytes(b"no es una imagen")
    with pytest.raises(escaneo.ErrorDocumento, match="roto.jpg"):
        escaneo.crear_pdf([AjusteFoto(str(roto))], OpcionesEscaneo(), tmp_path / "x.pdf")
    assert not (tmp_path / "x.pdf").exists()


def test_tarea_en_otro_proceso(fotos, tmp_path):
    import json

    from pdf_studio import procesos
    pedido, resultado = tmp_path / "pedido.json", tmp_path / "resultado.json"
    pedido.write_text(json.dumps({"fotos": [AjusteFoto(str(r)).a_dict() for r in fotos],
                                  "opciones": OpcionesEscaneo(mejora=Mejora.GRISES).a_dict(),
                                  "destino": str(tmp_path / "salida.pdf")}), encoding="utf-8")
    procesos.ejecutar("escaneo", pedido, resultado)
    datos = json.loads(resultado.read_text(encoding="utf-8"))
    assert datos["ok"] and datos["resultado"]["paginas"] == 3


@requiere_tesseract
def test_ocr_reconoce_el_texto_de_las_fotos(fotos, tmp_path):
    from pdf_studio.core import ocr
    from pdf_studio.core.fidelidad import comparar_paginas
    destino = tmp_path / "escaneado.pdf"
    escaneo.crear_pdf([AjusteFoto(str(r)) for r in fotos], OpcionesEscaneo(), destino)
    with pymupdf.open(destino) as doc, pymupdf.open(destino) as antes:
        capas = ocr.reconocer(doc, list(range(doc.page_count)), tmp_path, timeout=300)
        ocr.AgregarCapaOCR(capas).aplicar(doc)
        for p in range(doc.page_count):
            texto = doc[p].get_text()
            assert fs.TITULO in texto and "presupuesto anual" in texto
            assert comparar_paginas(antes[p], doc[p], 72)[0] == 0          # mismo aspecto


# ---------------------------------------------------------------------- interfaz
def test_dialogo_importar(qtbot, fotos):
    from PySide6.QtCore import Qt

    from pdf_studio.ui.escaneo import DialogoImportarFotos
    dialogo = DialogoImportarFotos(fotos, ocr_disponible=True)
    qtbot.addWidget(dialogo)
    dialogo.show()
    qtbot.waitExposed(dialogo)
    assert dialogo.lista.count() == 3 and dialogo.boton_crear.isEnabled()
    assert dialogo.boton_crear.property("primario")
    foto = dialogo.foto_actual
    assert foto.esquinas is not None and dialogo.editor.esquinas == foto.esquinas
    assert "Papel detectado" in dialogo.estado.text()
    assert dialogo.resultado.pixmap() is not None and not dialogo.resultado.pixmap().isNull()
    # Mover una esquina con el teclado
    dialogo.editor.setFocus()
    antes = foto.esquinas[2]
    qtbot.keyClick(dialogo.editor, Qt.Key.Key_3)
    qtbot.keyClick(dialogo.editor, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert foto.esquinas[2][0] == pytest.approx(antes[0] - 0.02) and foto.esquinas[2][1] == antes[1]
    # El escaneo plano avisa que usa la foto entera
    dialogo.lista.setCurrentRow(2)
    assert "No se encontró el borde" in dialogo.estado.text()
    # Girar, reordenar y quitar
    dialogo.girar(90)
    assert dialogo.foto_actual.rotacion == 90
    dialogo.mover_actual(-1)
    assert [f.ruta for f in dialogo.fotos][1].endswith("plano.png")
    dialogo.lista.setCurrentRow(0)
    dialogo.quitar_actual()
    assert len(dialogo.fotos) == 2
    dialogo.mejora.setCurrentIndex(dialogo.mejora.findData("byn"))
    assert dialogo.opciones().mejora == Mejora.BYN and dialogo.con_ocr
    while dialogo.fotos:
        dialogo.quitar_actual()
    assert not dialogo.boton_crear.isEnabled()


def test_dialogo_sin_ocr_lo_explica(qtbot, fotos):
    from PySide6.QtWidgets import QFrame

    from pdf_studio.ui.escaneo import DialogoImportarFotos
    dialogo = DialogoImportarFotos(fotos[:1], ocr_disponible=False, motivo_sin_ocr="No se encontró Tesseract OCR.")
    qtbot.addWidget(dialogo)
    assert not dialogo.ocr.isEnabled() and not dialogo.con_ocr
    textos = [w.accessibleDescription() for w in dialogo.findChildren(QFrame) if w.objectName() == "tarjeta"]
    assert any("Tesseract" in t for t in textos)


def test_ventana_importa_y_abre_el_pdf(ventana, fotos, tmp_path, monkeypatch, qtbot):
    destino = tmp_path / "importado.pdf"
    monkeypatch.setattr(ventana, "_dialogo_importar_fotos", lambda disponible, motivo: (
        [AjusteFoto(str(r)) for r in fotos], OpcionesEscaneo(), False))
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda sugerida: str(destino))
    assert ventana.acc_importar_fotos.isEnabled()            # no hace falta tener un PDF abierto
    assert ventana.importar_fotos()
    qtbot.waitUntil(lambda: ventana.documento is not None, timeout=120_000)
    assert ventana.documento.ruta_original == destino.resolve() and ventana.documento.doc.page_count == 3
    titulo, texto, error = ventana.mensajes[-1]
    assert titulo == "Importar fotos" and "Se creó «importado.pdf» con 3 páginas." in texto and not error


@requiere_tesseract
def test_ventana_importa_con_ocr(ventana, fotos, tmp_path, monkeypatch, qtbot):
    destino = tmp_path / "con_ocr.pdf"
    monkeypatch.setattr(ventana, "_dialogo_importar_fotos", lambda disponible, motivo: (
        [AjusteFoto(str(r)) for r in fotos[:2]], OpcionesEscaneo(mejora=Mejora.GRISES), True))
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda sugerida: str(destino))
    assert ventana.importar_fotos()
    qtbot.waitUntil(lambda: ventana.documento is not None, timeout=300_000)
    assert all(fs.TITULO in p.get_text() for p in ventana.documento.doc)
    assert "texto reconocido" in ventana.mensajes[-1][1]
    assert not ventana.documento.modificado               # el OCR ya está en el archivo


def test_ventana_sin_opencv_avisa(ventana, monkeypatch):
    def falta():
        raise Limitacion("Para importar fotos como PDF escaneado falta el paquete «opencv-python-headless».")
    monkeypatch.setattr(escaneo, "cv2", falta)
    monkeypatch.setattr(ventana, "chequear_dependencias", lambda mostrar=True: None)
    assert not ventana.importar_fotos()
    titulo, texto, error = ventana.mensajes[-1]
    assert "opencv" in texto and not error
