"""Arma PDF Studio para compartir: PyInstaller en modo carpeta («onedir»), la autoprueba del
.exe y un ZIP con el instalador para el usuario actual (sin permisos de administrador).

Uso (con el .venv):
    .\\.venv\\Scripts\\python.exe -m pip install -r requirements-empaquetado.txt
    .\\.venv\\Scripts\\python.exe herramientas\\empaquetar.py            # arma, prueba y comprime
    .\\.venv\\Scripts\\python.exe herramientas\\empaquetar.py --sin-zip  # solo la carpeta

Resultado:
    dist\\PDF Studio\\PDFStudio\\PDFStudio.exe   (la aplicación)
    dist\\PDF Studio\\Instalar.cmd               (copia a %LOCALAPPDATA%\\Programs y crea accesos directos)
    dist\\PDFStudio-<versión>-windows.zip        (todo lo anterior, para compartir)
"""
from __future__ import annotations

import importlib.metadata as metadata
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from pdf_studio import NOMBRE_APP, __version__  # noqa: E402

DIST = RAIZ / "dist"
BUILD = RAIZ / "build"
SALIDA = DIST / NOMBRE_APP                 # carpeta que va en el ZIP
NOMBRE_EXE = "PDFStudio"

# Lo que la app no usa: achica el paquete (QtWebEngine solo pesa más de 100 MB)
EXCLUIR = ["tkinter", "pytest", "_pytest", "pytestqt", "pymupdf4llm", "pymupdf_layout", "onnxruntime",
           "PyInstaller", "setuptools", "pip",
           "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
           "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQml",
           "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
           "PySide6.QtMultimediaWidgets", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
           "PySide6.QtPdfWidgets", "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtTest",
           "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
           "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets"]

# Paquetes que se importan tarde (dentro de funciones) o cargan módulos por nombre
SUBMODULOS = ["pdf_studio", "pyhanko", "pyhanko_certvalidator", "spylls", "ocrmypdf", "pikepdf"]
DATOS = ["docx", "ocrmypdf", "pyhanko", "pypdfium2_raw", "pypdfium2"]
METADATOS = ["ocrmypdf", "pikepdf", "pyhanko", "pymupdf", "pypdfium2", "img2pdf", "pdfminer.six"]


# Lo que PyInstaller trae arrastrado por plugins de Qt que la app no usa (teclado virtual →
# Quick/Qml/OpenGL; imágenes PDF de Qt → QtPdf/QtNetwork), el video de OpenCV y el OpenGL por
# software. Se borra después de armar; la autoprueba y la prueba de la ventana lo validan.
PODAR = ["cv2/opencv_videoio_ffmpeg*.dll", "PySide6/opengl32sw.dll",
         "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll", "PySide6/Qt6VirtualKeyboard.dll",
         "PySide6/Qt6Quick.dll", "PySide6/Qt6Qml*.dll", "PySide6/Qt6OpenGL.dll",
         "PySide6/plugins/imageformats/qpdf.dll", "PySide6/Qt6Pdf.dll",
         "PySide6/plugins/generic/qtuiotouchplugin.dll", "PySide6/Qt6Network.dll"]
TRADUCCIONES = ("_es.qm", "_en.qm")       # de las de Qt, solo español e inglés


def podar(carpeta: Path) -> int:
    interna = carpeta / "_internal"
    borrados = 0
    for patron in PODAR:
        for ruta in interna.glob(patron):
            borrados += ruta.stat().st_size
            ruta.unlink()
    for ruta in (interna / "PySide6" / "translations").glob("*.qm"):
        if not ruta.name.endswith(TRADUCCIONES):
            borrados += ruta.stat().st_size
            ruta.unlink()
    return borrados


def _argumentos() -> list[str]:
    sep = ";"
    args = ["--noconfirm", "--clean", "--name", NOMBRE_EXE, "--windowed",
            "--icon", str(RAIZ / "pdf_studio" / "recursos" / "logo.ico"),
            "--distpath", str(SALIDA), "--workpath", str(BUILD), "--specpath", str(BUILD),
            "--paths", str(RAIZ),
            "--add-data", f"{RAIZ / 'pdf_studio' / 'recursos'}{sep}pdf_studio/recursos",
            "--add-data", f"{RAIZ / 'pdf_studio' / 'dependencias' / 'datos'}{sep}pdf_studio/dependencias/datos"]
    for m in SUBMODULOS:
        args += ["--collect-submodules", m]
    for d in DATOS:
        args += ["--collect-data", d]
    for m in METADATOS:
        try:
            metadata.version(m)
            args += ["--copy-metadata", m]
        except metadata.PackageNotFoundError:
            pass
    for m in EXCLUIR:
        args += ["--exclude-module", m]
    args.append(str(RAIZ / "pdf_studio" / "app.py"))
    return args


def licencias_de_terceros() -> str:
    """Lista de los paquetes incluidos y sus licencias (para LICENCIAS-TERCEROS.txt)."""
    lineas = [f"{NOMBRE_APP} {__version__} se distribuye bajo la licencia AGPL-3.0 (ver LICENSE.txt).",
              "Incluye estos componentes de terceros, cada uno con su licencia:", ""]
    # Los paquetes cuyos módulos están todos excluidos no van en el .exe (pymupdf trae también
    # pymupdf4llm, que se excluye, pero pymupdf sí va)
    modulos_de: dict[str, set[str]] = {}
    for modulo, dists in metadata.packages_distributions().items():
        if not modulo.startswith("_"):
            for d in dists:
                modulos_de.setdefault(d.lower(), set()).add(modulo)
    excluidos = {m for m in EXCLUIR if "." not in m}
    vistos = {d for d, modulos in modulos_de.items() if modulos and modulos <= excluidos}
    for dist in sorted(metadata.distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        nombre = dist.metadata["Name"]
        if not nombre or nombre.lower() in vistos or nombre.lower() in {
                "pyinstaller", "pyinstaller-hooks-contrib", "pytest", "pytest-qt", "pip", "setuptools", "altgraph",
                "pefile", "pywin32-ctypes", "iniconfig"}:
            continue
        vistos.add(nombre.lower())
        licencia = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        if not licencia or len(licencia) > 80 or licencia.upper() == "UNKNOWN":
            clasificadores = [c.split("::")[-1].strip() for c in (dist.metadata.get_all("Classifier") or [])
                              if c.startswith("License ::")]
            licencia = ", ".join(clasificadores) or licencia[:80] or "ver el paquete"
        lineas.append(f"- {nombre} {dist.version}: {licencia}")
    lineas += ["", "Recursos incluidos:",
               "- Íconos Lucide (ISC): pdf_studio/recursos/iconos/LICENSE-lucide.txt",
               "- Fuentes Liberation (SIL OFL 1.1), Carlito y Caladea (SIL OFL 1.1): pdf_studio/recursos/fuentes",
               "- Diccionarios es_AR (RLA-ES: GPL-3.0 / LGPL-3.0 / MPL-1.1) y en_US (SCOWL: MIT y BSD): "
               "pdf_studio/recursos/diccionarios",
               "", "Tesseract y Ghostscript no vienen incluidos: la app ofrece instalarlos con winget."]
    return "\n".join(lineas) + "\n"


def armar() -> Path:
    import PyInstaller.__main__
    if SALIDA.exists():
        shutil.rmtree(SALIDA)
    PyInstaller.__main__.run(_argumentos())
    carpeta = SALIDA / NOMBRE_EXE
    exe = carpeta / f"{NOMBRE_EXE}.exe"
    if not exe.is_file():
        raise SystemExit("PyInstaller no generó el .exe")
    print(f"Podado: {podar(carpeta) / 1024 / 1024:.0f} MB que la app no usa")
    instalador = RAIZ / "instalador"
    for archivo in ("instalar.ps1", "desinstalar.ps1", "Instalar.cmd"):
        shutil.copy2(instalador / archivo, SALIDA / archivo)
    shutil.copy2(RAIZ / "LICENSE", SALIDA / "LICENSE.txt")
    shutil.copy2(RAIZ / "README.md", SALIDA / "LEEME.md")
    (SALIDA / "LICENCIAS-TERCEROS.txt").write_text(licencias_de_terceros(), encoding="utf-8")
    (SALIDA / "VERSION.txt").write_text(__version__ + "\n", encoding="utf-8")
    return exe


def autoprueba(exe: Path) -> bool:
    resultado = BUILD / "autoprueba.json"
    resultado.unlink(missing_ok=True)
    r = subprocess.run([str(exe), "--autoprueba", str(resultado)], timeout=600)
    datos = json.loads(resultado.read_text(encoding="utf-8")) if resultado.exists() else {}
    for nombre, estado in datos.items():
        print(f"  {nombre:24} {estado}")
    ok = r.returncode == 0 and bool(datos) and all(v == "ok" or v.startswith("omitida") for v in datos.values())
    print("Autoprueba del .exe:", "OK" if ok else f"FALLÓ (código {r.returncode})")
    return ok


def prueba_ventana(exe: Path, segundos: float = 12.0) -> bool:
    """Abre el .exe de verdad (con la ventana y el plugin de Windows de Qt) con un PDF de
    ejemplo: tiene que seguir abierto pasados unos segundos (si le falta algo, se cierra)."""
    import time

    import pymupdf
    pdf = BUILD / "ventana_prueba.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 100), "Prueba de la ventana de PDF Studio", fontsize=14)
        doc.save(pdf)
    proceso = subprocess.Popen([str(exe), str(pdf)])
    try:
        time.sleep(segundos)
        sigue = proceso.poll() is None
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proceso.kill()
    print("Prueba de la ventana:", "OK" if sigue else f"FALLÓ (se cerró con código {proceso.returncode})")
    return sigue


def comprimir() -> Path:
    destino = DIST / f"PDFStudio-{__version__}-windows.zip"
    destino.unlink(missing_ok=True)
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for ruta in sorted(SALIDA.rglob("*")):
            if ruta.is_file():
                z.write(ruta, Path(NOMBRE_APP) / ruta.relative_to(SALIDA))
    return destino


def tamano(carpeta: Path) -> str:
    total = sum(p.stat().st_size for p in carpeta.rglob("*") if p.is_file())
    return f"{total / 1024 / 1024:.0f} MB"


def main(argv: list[str]) -> int:
    exe = armar()
    print(f"Listo: {exe} ({tamano(SALIDA)})")
    if "--sin-prueba" not in argv and not (autoprueba(exe) and prueba_ventana(exe)):
        return 1
    if "--sin-zip" not in argv:
        zip_ = comprimir()
        print(f"ZIP para compartir: {zip_} ({zip_.stat().st_size / 1024 / 1024:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
