"""Instancia única (un doble clic con la app abierta suma una pestaña) y arranque rápido
(lo pesado se carga recién al usarlo; los íconos se dibujan bajo demanda)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent


def _entorno(**extra) -> dict:
    env = dict(os.environ, PYTHONPATH=str(RAIZ), PYTHONIOENCODING="utf-8", QT_QPA_PLATFORM="offscreen")
    env.update(extra)
    return env


# ---------------------------------------------------------------------- instancia única
def test_un_segundo_proceso_le_pasa_el_pdf_a_la_instancia_abierta(tmp_path):
    """La app real (python -m pdf_studio x.pdf) le entrega el archivo a la abierta y termina."""
    from pdf_studio import autoprueba
    assert autoprueba._instancia_unica(tmp_path) == "ok"


_CANDIDATO = textwrap.dedent("""
    import json, sys, time
    from PySide6.QtCore import QCoreApplication, QEventLoop
    from pdf_studio.instancia import Instancia
    canal, ruta, espera = sys.argv[1], sys.argv[2], float(sys.argv[3])
    inst = Instancia(canal)
    if inst.entregar_a_otra([ruta]):
        print(json.dumps({"rol": "entregó"}), flush=True)
        sys.exit(0)
    app = QCoreApplication([])
    servidor = inst.servir(app)
    recibidos = []
    servidor.archivos_recibidos.connect(recibidos.extend)
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
    servidor.cerrar()
    print(json.dumps({"rol": "principal", "recibidos": recibidos}), flush=True)
""")


def test_varios_procesos_a_la_vez_quedan_en_una_sola_instancia(tmp_path):
    """Como al abrir varios PDFs seleccionados: el Explorador lanza un proceso por archivo,
    todos a la vez. Uno queda como principal y los demás le pasan sus archivos."""
    from pdf_studio.instancia import nombre_canal
    script = tmp_path / "candidato.py"
    script.write_text(_CANDIDATO, encoding="utf-8")
    canal = nombre_canal(f"prueba-{os.getpid()}-{tmp_path.name}")
    rutas = [tmp_path / f"doc{i}.pdf" for i in range(4)]
    procesos = [subprocess.Popen([sys.executable, str(script), canal, str(r), "8"], env=_entorno(),
                                 stdout=subprocess.PIPE, text=True) for r in rutas]
    salidas = [json.loads(p.communicate(timeout=60)[0].strip().splitlines()[-1]) for p in procesos]
    principales = [s for s in salidas if s["rol"] == "principal"]
    assert len(principales) == 1, salidas
    # La principal recibió los archivos de todos los demás
    propia = rutas[[s["rol"] for s in salidas].index("principal")]
    esperados = {str(r.resolve()) for r in rutas} - {str(propia.resolve())}
    assert set(principales[0]["recibidos"]) == esperados


def test_sin_instancia_abierta_queda_como_principal(tmp_path):
    from pdf_studio.instancia import Instancia, nombre_canal
    inst = Instancia(nombre_canal(f"sola-{os.getpid()}-{tmp_path.name}"))
    try:
        assert not inst.entregar_a_otra([str(tmp_path / "x.pdf")], espera_s=2)
        assert inst.principal
        # Un segundo candidato del mismo canal no puede ser principal mientras viva el primero
        otra = Instancia(inst.canal)
        assert not otra._mutex.tomar()
    finally:
        inst.liberar()
    otra = Instancia(inst.canal)
    assert otra._mutex.tomar()                 # liberado: ya se puede tomar
    otra._mutex.soltar()


def test_el_canal_separa_usuario_e_instalacion():
    from pdf_studio.instancia import nombre_canal
    assert nombre_canal() == nombre_canal()
    assert nombre_canal("a") != nombre_canal("b")
    assert nombre_canal().startswith("PDFStudio-")


_CLIENTE_INVALIDO = textwrap.dedent("""
    import sys, time
    from PySide6.QtCore import QByteArray
    from PySide6.QtNetwork import QLocalSocket
    s = QLocalSocket()
    s.connectToServer(sys.argv[1])
    assert s.waitForConnected(5000)
    s.write(QByteArray(b"esto no es json\\n"))
    s.flush()
    datos = b""
    limite = time.monotonic() + 10
    while b"\\n" not in datos and time.monotonic() < limite:
        if s.waitForReadyRead(200):
            datos += bytes(s.readAll().data())
    print(datos.decode().strip(), flush=True)
""")


def test_mensaje_invalido_no_abre_nada(qtbot, tmp_path):
    """Lo que no es un pedido válido se contesta con «error» y no abre nada."""
    from pdf_studio.instancia import ServidorInstancia, nombre_canal
    servidor = ServidorInstancia(nombre_canal(f"invalido-{os.getpid()}-{tmp_path.name}"))
    assert servidor.escuchar()
    recibidos = []
    servidor.archivos_recibidos.connect(recibidos.append)
    script = tmp_path / "cliente.py"
    script.write_text(_CLIENTE_INVALIDO, encoding="utf-8")
    proceso = subprocess.Popen([sys.executable, str(script), servidor.canal], env=_entorno(),
                               stdout=subprocess.PIPE, text=True)
    try:
        qtbot.waitUntil(lambda: proceso.poll() is not None, timeout=20_000)
        assert proceso.stdout.read().strip() == "error"
        assert not recibidos
    finally:
        if proceso.poll() is None:
            proceso.kill()
        servidor.cerrar()
        qtbot.wait(50)


# ---------------------------------------------------------------------- arranque
PESADOS = ["numpy", "pikepdf", "fontTools", "cv2", "docx", "pyhanko", "ocrmypdf", "spylls", "onnxruntime",
           "pymupdf4llm", "pymupdf.layout", "urllib.request", "lxml", "cryptography"]


def test_abrir_la_ventana_no_carga_lo_pesado():
    """Lo que se usa recién al editar, guardar, convertir o firmar no se importa al arrancar."""
    codigo = ("import sys, json\n"
              "import pdf_studio.app, pdf_studio.ui.ventana_principal\n"
              f"print(json.dumps([m for m in {PESADOS!r} if sys.modules.get(m) is not None]))\n")
    salida = subprocess.run([sys.executable, "-c", codigo], env=_entorno(), capture_output=True, text=True,
                            timeout=120, cwd=RAIZ)
    assert salida.returncode == 0, salida.stderr
    assert json.loads(salida.stdout.strip().splitlines()[-1]) == []


def test_lo_diferido_sigue_funcionando():
    """Cada módulo que ahora importa tarde sigue andando la primera vez que se usa."""
    import pymupdf
    from PIL import Image

    from pdf_studio.core import codificacion, fidelidad, imagenes
    img = Image.new("RGBA", (20, 20), (255, 255, 255, 255))
    assert not imagenes.tiene_transparencia(img)
    with pymupdf.open() as a, pymupdf.open() as b:
        a.new_page().insert_text((72, 72), "uno")
        b.new_page().insert_text((72, 72), "dos")
        distintos, total, _ = fidelidad.comparar_paginas(a[0], b[0], 72)
        assert 0 < distintos < total
        # Una fuente simple con /Encoding: usa fontTools (agl), que ahora se importa al leerla
        assert codificacion._mapa_encoding(a, a[0].get_fonts()[0][0])


def test_iconos_se_dibujan_recien_cuando_se_piden(qapp, monkeypatch):
    from PySide6.QtGui import QColor, QIcon

    from pdf_studio.ui import iconos
    from pdf_studio.ui.tema import gestor
    dibujados = []
    original = iconos._pixmap
    monkeypatch.setattr(iconos, "_pixmap", lambda *a, **k: dibujados.append(a) or original(*a, **k))
    icono = iconos.crear_icono("save")
    assert dibujados == [] and not icono.isNull()
    px = icono.pixmap(24, 24)
    assert len(dibujados) == 1 and px.deviceIndependentSize().width() == pytest.approx(24, abs=1)
    icono.pixmap(24, 24)
    assert len(dibujados) == 1                        # el mismo tamaño se reutiliza
    # Deshabilitado: color de texto suave
    img = icono.pixmap(48, 48, QIcon.Mode.Disabled).toImage()
    colores = {img.pixelColor(x, y).name() for x in range(img.width()) for y in range(img.height())
               if img.pixelColor(x, y).alpha() > 200}
    assert QColor(gestor().paleta.texto_suave).name() in colores
