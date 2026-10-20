"""Robustez: fuentes difíciles, archivos inválidos, errores en bucle e interfaz sin bloqueos."""
from __future__ import annotations

import shutil
import time

import pymupdf
import pytest

from pdf_studio.core.documento import Documento, ErrorDocumento
from pdf_studio.core.fuentes import Embebido, IndiceFuentesSistema, analizar_fuentes, letras_con_glifo
from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en, lineas_de_pagina, planificar
from pdf_studio.dependencias.chequeo import chequear_fuentes
from tests.fuentes_sinteticas import DESTINO, GENERADORES, generar

if not all((DESTINO / n).is_file() for n in GENERADORES):
    generar()

ESPERADO = {
    "cff_type1c.pdf": {Embebido.SUBCONJUNTO},
    "type1.pdf": {Embebido.SUBCONJUNTO, Embebido.NO_ANALIZADA},
    "type3.pdf": {Embebido.TYPE3},
    "truetype_sin_cmap.pdf": {Embebido.SUBCONJUNTO},
    "truetype_simbolica.pdf": {Embebido.SUBCONJUNTO, Embebido.NO_ANALIZADA},
    "cid_cidtogid.pdf": {Embebido.SUBCONJUNTO, Embebido.NO_ANALIZADA},
}


@pytest.fixture(scope="module")
def incluidas(tmp_path_factory):
    from pdf_studio import config
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path_factory.mktemp("c") / "i.json")


# ---------------------------------------------------------------------- fuentes difíciles
@pytest.mark.parametrize("nombre", list(GENERADORES))
def test_analisis_nunca_falla(nombre, incluidas):
    with pymupdf.open(DESTINO / nombre) as doc:
        fuentes = analizar_fuentes(doc, None, incluidas)
        especial = [f for f in fuentes if f.nombre != "Helvetica"]
        assert len(especial) == 1
        assert especial[0].embebido in ESPERADO[nombre], especial[0].embebido
        fuentes2, faltan = chequear_fuentes(doc, None, incluidas)
        assert len(fuentes2) == 2


def test_truetype_sin_cmap_como_la_prefactura():
    """El caso del bug: TrueType CID sin tabla cmap. Se clasifica por ToUnicode → glifo."""
    with pymupdf.open(DESTINO / "truetype_sin_cmap.pdf") as doc:
        f = next(f for f in analizar_fuentes(doc) if f.tipo == "Type0")
        assert f.embebido == Embebido.SUBCONJUNTO and f.error == ""
        datos = doc.extract_font(f.xrefs[0])[3]
        assert letras_con_glifo(datos) == (None, letras_con_glifo(datos)[1])     # sin cmap no se sabe...
        from pdf_studio.core.fuentes import _mapas_del_pdf
        inverso, a_gid = _mapas_del_pdf(doc, f.xrefs[0], "Type0")
        letras, _total = letras_con_glifo(datos, inverso, a_gid)                  # ...pero por el PDF sí
        assert {"T", "x", "1"} <= letras and "Z" not in letras


def test_fuente_ilegible_no_frena_a_las_demas(monkeypatch):
    from pdf_studio.core import fuentes as mod

    def explota(*a, **k):
        raise KeyError("cmap")
    monkeypatch.setattr(mod, "es_subconjunto_por_cobertura", explota)
    with pymupdf.open(DESTINO / "truetype_sin_cmap.pdf") as doc:
        resultado = {f.nombre: f.embebido for f in analizar_fuentes(doc)}
    assert resultado["Helvetica"] == Embebido.NO_EMBEBIDA
    assert resultado["FacturaSans-Regular"] == Embebido.NO_ANALIZADA


@pytest.mark.parametrize("nombre", list(GENERADORES))
def test_abrir_renderizar_y_editar(nombre, tmp_path, incluidas):
    """Abrir, dibujar, y editar (o rechazar con un mensaje claro) con cada fuente difícil."""
    ruta = tmp_path / nombre
    shutil.copy2(DESTINO / nombre, ruta)
    d = Documento(ruta)
    try:
        for pag in d.doc:
            pix = pag.get_pixmap(dpi=50)
            assert pix.width > 0
        bloques = lineas_de_pagina(d.doc[0])
        especial = next(ln for b in bloques for ln in b if "Helvetica" not in ln.texto)
        punto = especial.bbox.tl + (2, (especial.bbox.height) / 2)
        bloque = bloque_en(d.doc[0], 0, punto, Nivel.LINEA)
        assert bloque is not None
        # Mover sin cambiar el texto
        try:
            d.ejecutar(planificar(d.doc, bloque, bloque.texto, None, (0, 20)).operacion)
        except ErrorDocumento as e:
            assert str(e)                       # se rechaza con un mensaje, nunca con un crash
        else:
            assert d.guardar(tmp_path / "movido.pdf").es_fiel
            d.deshacer()
        # Editar con la mejor fuente disponible
        nuevo = bloque.texto + " ok"
        analisis = analizar_fuente(d.doc, d.doc[0], bloque.estilo, nuevo, None, incluidas)
        try:
            d.ejecutar(planificar(d.doc, bloque, nuevo, analisis.recomendada).operacion)
        except ErrorDocumento as e:
            assert str(e)
        else:
            assert d.guardar(tmp_path / "editado.pdf").es_fiel
    finally:
        d.cerrar()


# ---------------------------------------------------------------------- archivos inválidos
@pytest.mark.parametrize("contenido,mensaje", [
    (b"", "vacío"),
    (b"Hola, esto es un archivo de texto renombrado.", "no es un PDF"),
    (b"\x89PNG\r\n\x1a\n" + bytes(200), "no es un PDF"),
    (b"%PDF-1.7\n1 0 obj << /Type /Catalog >> basura sin terminar", "dañado"),
])
def test_archivos_invalidos_dan_mensaje_claro(tmp_path, contenido, mensaje):
    ruta = tmp_path / "invalido.pdf"
    ruta.write_bytes(contenido)
    with pytest.raises(ErrorDocumento) as info:
        Documento(ruta)
    texto = str(info.value)
    assert mensaje in texto and "invalido.pdf" in texto


def test_pdf_truncado(tmp_path):
    from tests.conftest import DIR_PDFS
    datos = (DIR_PDFS / "tablas.pdf").read_bytes()
    ruta = tmp_path / "truncado.pdf"
    ruta.write_bytes(datos[: len(datos) // 3])
    try:
        d = Documento(ruta)           # MuPDF puede repararlo: entonces se abre normalmente
    except ErrorDocumento as e:
        assert "truncado.pdf" in str(e)
    else:
        d.cerrar()


def test_carpeta_en_vez_de_archivo(tmp_path):
    with pytest.raises(ErrorDocumento, match="no es un archivo"):
        Documento(tmp_path)


def test_interfaz_avisa_sin_crash(qtbot, tmp_path, monkeypatch):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    avisos = []
    monkeypatch.setattr(v, "_informar", lambda t, x, error=False: avisos.append((x, error)))
    vacio = tmp_path / "vacio.pdf"
    vacio.write_bytes(b"")
    assert not v.abrir(vacio)
    assert avisos and "vacío" in avisos[-1][0] and avisos[-1][1]
    assert v.documento is None


# ---------------------------------------------------------------------- errores globales
def test_error_repetido_se_muestra_una_sola_vez(tmp_path, monkeypatch):
    from pdf_studio.ui import errores
    monkeypatch.setattr(errores, "ARCHIVO_LOG", tmp_path / "log.txt")
    mostrados = []
    manejador = errores.ManejadorErrores(mostrar=lambda resumen, detalle: mostrados.append(resumen))

    def falla():
        raise KeyError("cmap")
    for _ in range(5):
        try:
            falla()
        except KeyError as e:
            manejador(type(e), e, e.__traceback__)
    assert len(mostrados) == 1 and "KeyError" in mostrados[0]
    assert manejador.repeticiones == 4


def test_dialogo_de_error_copia_detalle(qtbot):
    from PySide6.QtGui import QGuiApplication
    from pdf_studio.ui.errores import DialogoError
    d = DialogoError("KeyError: 'cmap'", "Traceback… detalle técnico")
    qtbot.addWidget(d)
    d.copiar.click()
    assert QGuiApplication.clipboard().text() == "Traceback… detalle técnico"


def test_log_en_appdata():
    from pdf_studio import config
    from pdf_studio.ui.errores import ARCHIVO_LOG
    assert ARCHIVO_LOG.parent == config.DIR_APPDATA / "logs"


# ---------------------------------------------------------------------- sin bloqueos
def test_interfaz_no_se_bloquea_durante_el_analisis(qtbot, tmp_path, monkeypatch, copia_pdf):
    """Con un análisis lento (simulado), la interfaz sigue procesando eventos y se puede usar."""
    from PySide6.QtCore import QTimer
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui import segundo_plano
    from pdf_studio.ui.ventana_principal import VentanaPrincipal

    # Proceso de análisis "lento": duerme 3 s antes de contestar
    lento = tmp_path / "lento.py"
    lento.write_text("import time, sys, json\nsys.stdin.read()\ntime.sleep(3)\n"
                     "print(json.dumps({'ok': True, 'fuentes': []}))\n", encoding="utf-8")
    import sys as _sys
    monkeypatch.setattr(segundo_plano, "comando_analisis", lambda: (_sys.executable, [str(lento)]))

    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    monkeypatch.setattr(v, "_informar", lambda *a, **k: None)
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    v.show()
    latidos = []
    reloj = QTimer()
    reloj.timeout.connect(lambda: latidos.append(time.monotonic()))
    reloj.start(50)
    inicio = time.monotonic()
    v.abrir(copia_pdf("texto_varias_paginas.pdf"))
    assert time.monotonic() - inicio < 2.5                  # abrir no espera al análisis
    assert v.analisis_en_curso and "Analizando" in v.etiqueta_analisis.text()
    assert "Analizando" in v.faltantes.resumen.text()
    # Mientras analiza, la app responde: se puede rotar y hacer zoom
    v.acc_rotar_der.trigger()
    v.acc_acercar.trigger()
    assert v.documento.doc[0].rotation == 90
    qtbot.waitUntil(lambda: not v.analisis_en_curso, timeout=20_000)
    reloj.stop()
    # El reloj siguió latiendo durante el análisis (sin huecos de más de 1 s)
    huecos = [b - a for a, b in zip(latidos, latidos[1:])]
    assert latidos and max(huecos) < 1.0
    assert v.etiqueta_analisis.text() == ""                  # el estado se limpió
    v.cerrar_documento()


def test_fallo_del_analisis_se_informa(qtbot, tmp_path, monkeypatch, copia_pdf):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui import segundo_plano
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    roto = tmp_path / "roto.py"
    roto.write_text("import sys\nsys.stdin.read()\nraise SystemExit(3)\n", encoding="utf-8")
    import sys as _sys
    monkeypatch.setattr(segundo_plano, "comando_analisis", lambda: (_sys.executable, [str(roto)]))
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    v.abrir(copia_pdf("tablas.pdf"))
    qtbot.waitUntil(lambda: not v.analisis_en_curso, timeout=20_000)
    assert v.faltantes.estado("documento") == "error"
    assert "No falta nada" not in v.faltantes.resumen.text()
    assert "No se pudo completar" in v.faltantes.resumen.text()
    assert v.boton_faltantes.text() == "Análisis incompleto"
    assert v.etiqueta_analisis.text() == ""
    v.cerrar_documento()


def test_analisis_real_de_la_prefactura_sintetica(qtbot, tmp_path):
    """El proceso de análisis real (sin simular) con la fuente sin cmap."""
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    ruta = tmp_path / "f.pdf"
    shutil.copy2(DESTINO / "truetype_sin_cmap.pdf", ruta)
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    v.abrir(ruta)
    qtbot.waitUntil(lambda: not v.analisis_en_curso, timeout=60_000)
    assert v.faltantes.estado("documento") == "ok"
    tabla = v.panel_fuentes.tabla
    assert {tabla.item(i, 1).text() for i in range(tabla.rowCount())} == {"No embebida", "Recortada"}
    v.cerrar_documento()
