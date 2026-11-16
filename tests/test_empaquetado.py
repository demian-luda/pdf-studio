"""Fase 11: empaquetado. La autoprueba (la misma que corre el .exe), los argumentos de
PyInstaller, la lista de licencias y los scripts del instalador (sintaxis de PowerShell,
codificación, que no pidan administrador ni pisen el programa predeterminado)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# Usa Tesseract, OpenCV, ocrmypdf o arma procesos aparte: en la PC, de a un archivo
pytestmark = pytest.mark.pesado

RAIZ = Path(__file__).resolve().parent.parent
INSTALADOR = RAIZ / "instalador"
sys.path.insert(0, str(RAIZ / "herramientas"))


def test_autoprueba_sin_empaquetar(tmp_path):
    from pdf_studio import autoprueba
    destino = tmp_path / "resultado.json"
    codigo = autoprueba.main(str(destino))
    resultados = json.loads(destino.read_text(encoding="utf-8"))
    assert set(resultados) == set(autoprueba.PRUEBAS)
    assert codigo == 0, resultados


def test_app_acepta_autoprueba(monkeypatch, tmp_path):
    from pdf_studio import app, autoprueba
    llamado = []
    monkeypatch.setattr(autoprueba, "main", lambda destino: llamado.append(destino) or 0)
    assert app.main(["PDFStudio.exe", "--autoprueba", str(tmp_path / "r.json")]) == 0
    assert llamado == [str(tmp_path / "r.json")]


def test_argumentos_de_pyinstaller():
    empaquetar = pytest.importorskip("empaquetar")
    args = empaquetar._argumentos()
    assert "--windowed" in args and "--noconfirm" in args and args[-1].endswith("app.py")
    datos = [args[i + 1] for i, a in enumerate(args) if a == "--add-data"]
    assert any(d.endswith("pdf_studio/recursos") for d in datos)
    assert any(d.endswith("pdf_studio/dependencias/datos") for d in datos)
    sub = [args[i + 1] for i, a in enumerate(args) if a == "--collect-submodules"]
    assert {"pdf_studio", "pyhanko", "spylls", "ocrmypdf"} <= set(sub)
    fuera = [args[i + 1] for i, a in enumerate(args) if a == "--exclude-module"]
    assert "PySide6.QtWebEngineCore" in fuera and "pytest" in fuera
    # Lo que la app sí usa no se excluye
    usados = {"PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets", "PySide6.QtSvg"}
    assert not usados & set(fuera)


def test_qt_que_usa_la_app_no_se_excluye():
    """Si algún módulo empieza a usar otro módulo de Qt, hay que sacarlo de la lista de excluidos."""
    import re
    empaquetar = pytest.importorskip("empaquetar")
    usados = set()
    for ruta in (RAIZ / "pdf_studio").rglob("*.py"):
        usados |= set(re.findall(r"PySide6\.(Qt[A-Za-z0-9]+)", ruta.read_text(encoding="utf-8")))
    excluidos = {m.split(".", 1)[1] for m in empaquetar.EXCLUIR if m.startswith("PySide6.")}
    assert not usados & excluidos


def test_licencias_de_terceros():
    empaquetar = pytest.importorskip("empaquetar")
    texto = empaquetar.licencias_de_terceros()
    paquetes = texto.lower()
    assert "AGPL-3.0" in texto and all(f"- {p} " in paquetes for p in ("pymupdf", "pyside6", "spylls", "pyhanko"))
    assert "pyinstaller" not in texto.lower().split("recursos incluidos")[0]
    assert "Lucide" in texto and "RLA-ES" in texto
    assert "Tesseract OCR 5." in texto and "tessdata_best" in texto and "Ghostscript 10." in texto
    assert "LICENSE-tesseract.txt" in texto and "COPYING-ghostscript.txt" in texto
    assert "TEXTOS COMPLETOS" in texto and "GNU AFFERO GENERAL PUBLIC LICENSE" in texto
    assert "winget" not in texto.lower()


def test_cada_dll_incluida_tiene_el_texto_completo_de_su_licencia():
    """Cada DLL de Tesseract y Ghostscript tiene el texto completo de su licencia (verificado por
    SHA-256) en su carpeta, y ese texto está entero en LICENCIAS-TERCEROS.txt. Si una versión nueva
    trae otra biblioteca, falla hasta registrar su licencia."""
    import hashlib

    from pdf_studio.dependencias import programas
    empaquetar = pytest.importorskip("empaquetar")
    base = programas.carpeta_del_repo()
    if not all(programas.esta_completo(base / p.clave, p) for p in programas.PROGRAMAS):
        pytest.skip("Tesseract y Ghostscript no están preparados (python -m pdf_studio.dependencias.programas)")
    lista = empaquetar.licencias_de_terceros()
    for p in programas.PROGRAMAS:
        carpeta = base / p.clave
        dlls = sorted(d.name for d in carpeta.glob("*.dll"))
        cubiertas = {dll for dll, _lic in programas.licencias_de(carpeta)}     # KeyError: DLL sin registrar
        sin_texto = set(dlls) - cubiertas - {"gsdll64.dll", "libtesseract-5.dll"}   # esas: su licencia propia
        assert not sin_texto, sin_texto
        for dll, lic in programas.licencias_de(carpeta):
            assert dll in lista and lic.spdx in lista
            for t in lic.textos:
                ruta = carpeta / "licencias" / lic.carpeta / t.destino
                datos = ruta.read_bytes()
                assert hashlib.sha256(datos).hexdigest() == (t.sha256_miembro or t.archivo.sha256), ruta
                assert len(datos) > 500, ruta
                assert datos.decode("utf-8", "replace").rstrip() in lista, f"falta el texto completo de {ruta}"
    for ruta in (base / "tesseract" / "LICENSE-tesseract.txt", base / "tesseract" / "LICENSE-tessdata_best.txt",
                 base / "ghostscript" / "COPYING-ghostscript.txt"):
        assert ruta.read_text(encoding="utf-8", errors="replace").rstrip() in lista, ruta


def test_copiar_programas_al_lado_del_exe(tmp_path):
    from pdf_studio.dependencias import programas
    empaquetar = pytest.importorskip("empaquetar")
    if any(programas.carpeta(p) != programas.carpeta_del_repo() / p.clave for p in programas.PROGRAMAS):
        pytest.skip("Tesseract y Ghostscript no están preparados (python -m pdf_studio.dependencias.programas)")
    empaquetar.copiar_programas(tmp_path)
    assert (tmp_path / "tesseract" / "tesseract.exe").is_file()
    assert (tmp_path / "tesseract" / "tessdata" / "spa.traineddata").is_file()
    assert (tmp_path / "ghostscript" / "gswin64c.exe").is_file()


@pytest.mark.parametrize("nombre", ["instalar.ps1", "desinstalar.ps1"])
def test_scripts_powershell_con_bom_y_sin_errores_de_sintaxis(nombre):
    ruta = INSTALADOR / nombre
    datos = ruta.read_bytes()
    assert datos.startswith(b"\xef\xbb\xbf")             # Windows PowerShell 5.1 necesita el BOM para las tildes
    texto = datos.decode("utf-8-sig")
    assert "RunAs" not in texto and "HKLM:" not in texto     # nunca pide administrador
    if shutil.which("powershell") is None:
        pytest.skip("sin PowerShell")
    comando = ("$e = $null; [System.Management.Automation.Language.Parser]::ParseFile("
               f"'{ruta}', [ref]$null, [ref]$e) | Out-Null; $e.Count")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", comando], capture_output=True, text=True,
                       timeout=60)
    assert r.stdout.strip() == "0", r.stdout + r.stderr


def test_instalador_no_cambia_el_programa_predeterminado():
    texto = (INSTALADOR / "instalar.ps1").read_text(encoding="utf-8-sig")
    assert "OpenWithList" in texto and "UserChoice" not in texto
    assert r"Classes\.pdf\shell" not in texto and "(default)" in texto
    desinstalar = (INSTALADOR / "desinstalar.ps1").read_text(encoding="utf-8-sig")
    for clave in ("Uninstall\\PDFStudio", "Applications\\PDFStudio.exe", "OpenWithList\\PDFStudio.exe"):
        assert clave in texto and clave in desinstalar
    assert "APPDATA" in desinstalar and "Remove-Item -Recurse -Force $destino" in desinstalar


@pytest.mark.skipif(shutil.which("powershell") is None, reason="sin PowerShell")
def test_instalar_y_desinstalar_en_modo_de_prueba(tmp_path):
    """Con -Raiz todo va a una carpeta y a HKCU:\\Software\\PDFStudioPrueba (nada real)."""
    paquete = tmp_path / "paquete"
    (paquete / "PDFStudio" / "_internal").mkdir(parents=True)
    (paquete / "PDFStudio" / "PDFStudio.exe").write_bytes(b"MZ prueba")
    (paquete / "PDFStudio" / "_internal" / "dato.txt").write_text("x", encoding="utf-8")
    (paquete / "VERSION.txt").write_text("9.9.9\n", encoding="utf-8")
    for nombre in ("instalar.ps1", "desinstalar.ps1"):
        shutil.copy2(INSTALADOR / nombre, paquete / nombre)
    raiz = tmp_path / "raiz"

    def ps(script: Path, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                               "-Raiz", str(raiz), *extra], capture_output=True, text=True, timeout=120)

    def registro(clave: str, valor: str) -> str:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"(Get-ItemProperty 'HKCU:\\Software\\PDFStudioPrueba\\{clave}').'{valor}'"],
                           capture_output=True, text=True, timeout=60)
        return r.stdout.strip()

    try:
        r = ps(paquete / "instalar.ps1")
        assert r.returncode == 0, r.stdout + r.stderr
        instalada = raiz / "Programs" / "PDF Studio"
        assert (instalada / "PDFStudio.exe").is_file() and (instalada / "_internal" / "dato.txt").is_file()
        assert (instalada / "desinstalar.ps1").is_file()
        assert (raiz / "Menu" / "PDF Studio.lnk").is_file() and (raiz / "Escritorio" / "PDF Studio.lnk").is_file()
        assert registro(r"Microsoft\Windows\CurrentVersion\Uninstall\PDFStudio", "DisplayVersion") == "9.9.9"
        assert registro(r"Classes\Applications\PDFStudio.exe\shell\open\command", "(default)").endswith('"%1"')
        r = ps(instalada / "desinstalar.ps1")
        assert r.returncode == 0, r.stdout + r.stderr
        assert not instalada.exists() and not (raiz / "Menu" / "PDF Studio.lnk").exists()
        assert registro(r"Microsoft\Windows\CurrentVersion\Uninstall\PDFStudio", "DisplayVersion") == ""
    finally:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Remove-Item -Recurse -Force 'HKCU:\\Software\\PDFStudioPrueba' -ErrorAction SilentlyContinue"],
                       timeout=60)


def test_zip_reproducible_y_sumas(tmp_path, monkeypatch):
    """Con SOURCE_DATE_EPOCH, el mismo contenido da exactamente el mismo ZIP (aunque cambien las
    fechas de los archivos), y SHA256SUMS.txt lo describe."""
    import hashlib
    import os
    import time
    empaquetar = pytest.importorskip("empaquetar")
    carpeta = tmp_path / "PDF Studio"
    (carpeta / "PDFStudio" / "_internal").mkdir(parents=True)
    (carpeta / "PDFStudio" / "PDFStudio.exe").write_bytes(b"MZ" + bytes(range(256)) * 10)
    (carpeta / "PDFStudio" / "_internal" / "b.txt").write_text("b", encoding="utf-8")
    (carpeta / "instalar.ps1").write_text("# instalar", encoding="utf-8")
    monkeypatch.setattr(empaquetar, "DIST", tmp_path)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1761000000")
    primero = hashlib.sha256(empaquetar.comprimir(carpeta).read_bytes()).hexdigest()
    ahora = time.time() + 3600
    for ruta in carpeta.rglob("*"):
        os.utime(ruta, (ahora, ahora))
    zip_ = empaquetar.comprimir(carpeta)
    assert hashlib.sha256(zip_.read_bytes()).hexdigest() == primero
    import zipfile
    with zipfile.ZipFile(zip_) as z:
        assert z.namelist() == ["PDF Studio/PDFStudio/PDFStudio.exe", "PDF Studio/PDFStudio/_internal/b.txt",
                                "PDF Studio/instalar.ps1"]
        assert {i.date_time for i in z.infolist()} == {time.gmtime(1761000000)[:6]}
    sumas = empaquetar.sumas_sha256(zip_).read_text(encoding="utf-8")
    assert sumas == f"{primero}  {zip_.name}\n"


def test_workflow_de_release():
    """El workflow arma con versiones fijas y la firma de SignPath está desactivada hasta configurarla."""
    import re
    texto = (RAIZ / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    usos = re.findall(r"uses:\s*([^\s]+)", texto)
    assert usos and all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u) for u in usos), usos
    assert 'tags: ["v*"]' in texto and "windows-latest" in texto
    # Tesseract y Ghostscript se arman (verificados) antes de las pruebas, que no los saltean
    assert texto.index("python -m pdf_studio.dependencias.programas") < texto.index("python -m pytest")
    assert 'PDFSTUDIO_EXIGIR_PROGRAMAS: "1"' in texto
    assert re.search(r'PYTHON_VERSION: "\d+\.\d+\.\d+"', texto) and "requirements-empaquetado.txt" in texto
    firma = texto[texto.index("Firmar con SignPath"):texto.index("Comprobar la firma")]
    assert "if: ${{ vars.SIGNPATH_HABILITADO == 'true' }}" in firma
    assert "secrets.SIGNPATH_API_TOKEN" in firma
    # Todas las variables que usa el workflow están documentadas en CODE_SIGNING.md
    politica = (RAIZ / "CODE_SIGNING.md").read_text(encoding="utf-8")
    for nombre in set(re.findall(r"(?:vars|secrets)\.(SIGNPATH_\w+)", texto)):
        assert nombre in politica, nombre
    assert "SignPath Foundation" in (RAIZ / "README.md").read_text(encoding="utf-8")
    # Los paquetes del armado tienen versión fija
    for linea in (RAIZ / "requirements-empaquetado.txt").read_text(encoding="utf-8").splitlines():
        if linea and not linea.startswith(("#", "-r")):
            assert "==" in linea, linea


def test_instalar_cmd_es_ascii():
    datos = (INSTALADOR / "Instalar.cmd").read_bytes()
    datos.decode("ascii")
    assert b"-ExecutionPolicy Bypass" in datos and b"instalar.ps1" in datos
