"""Programas incluidos (Tesseract con español e inglés, Ghostscript): versiones fijas,
descargas verificadas, dónde se buscan (nunca en el sistema) y que no quede nada de winget."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

from pdf_studio import config
from pdf_studio.dependencias import programas
from pdf_studio.dependencias.instalador import ErrorInstalacion

RAIZ = Path(__file__).resolve().parent.parent
ORIGENES_OFICIALES = ("https://github.com/tesseract-ocr/", "https://github.com/ArtifexSoftware/",
                      "https://github.com/ip7z/7zip/")


ORIGENES_LICENCIAS = ORIGENES_OFICIALES + (
    "https://raw.githubusercontent.com/", "https://gitlab.com/libidn/", "https://gitlab.com/libtiff/",
    "https://gcc.gnu.org/git/", "https://sourceware.org/git/", "https://sourceforge.net/p/giflib/",
    "https://ftp.gnu.org/gnu/", "https://www.cl.cam.ac.uk/~mgk25/jbigkit/")


def _todas_las_descargas() -> list[programas.Archivo]:
    return [programas.INSTALADOR_TESSERACT, *programas.IDIOMAS.values(), programas.LICENCIA_TESSDATA,
            programas.INSTALADOR_GHOSTSCRIPT, programas.SIETE_ZIP_R, programas.SIETE_ZIP]


def _textos_de_licencia() -> list[programas.Texto]:
    licencias = [l for l in programas.LICENCIAS_DLL.values() if l is not None] + [programas.LICENCIA_MSVC]
    return [t for l in licencias for t in l.textos]


@pytest.mark.parametrize("archivo", _todas_las_descargas(), ids=lambda a: a.nombre)
def test_descargas_fijas_y_oficiales(archivo):
    assert archivo.url.startswith(ORIGENES_OFICIALES), archivo.url
    assert re.fullmatch(r"[0-9a-f]{64}", archivo.sha256) and archivo.tamano > 0
    assert "latest" not in archivo.url and "/main/" not in archivo.url      # versión fija, no «la última»


@pytest.mark.parametrize("texto", _textos_de_licencia(), ids=lambda t: t.archivo.url.rsplit("/", 2)[-2] + "/" + t.destino)
def test_textos_de_licencia_con_version_fija(texto):
    a = texto.archivo
    assert a.url.startswith(ORIGENES_LICENCIAS), a.url
    assert re.fullmatch(r"[0-9a-f]{64}", a.sha256) and a.tamano > 0
    assert "/main/" not in a.url and "/master/" not in a.url and "latest" not in a.url
    if texto.miembro is not None:                       # de un tarball: se verifica también el texto
        assert re.fullmatch(r"[0-9a-f]{64}", texto.sha256_miembro or "")


def test_la_descarga_incluye_las_licencias():
    assert programas.tamano_descarga(programas.TESSERACT) > programas.INSTALADOR_TESSERACT.tamano + 28_000_000
    urls = {a.url for a in programas.TESSERACT.descargas()}
    assert all(t.archivo.url in urls for l in programas.LICENCIAS_DLL.values() if l for t in l.textos)


def test_idiomas_best_espanol_e_ingles():
    assert set(programas.IDIOMAS) == {"spa", "eng"}
    assert all("/tessdata_best/" in a.url for a in programas.IDIOMAS.values())


def test_se_busca_solo_en_las_carpetas_de_la_app(monkeypatch, tmp_path):
    """Con otro Tesseract en el PATH o en Archivos de programa, la app no lo usa."""
    sistema = tmp_path / "Tesseract-OCR"
    sistema.mkdir()
    (sistema / "tesseract.exe").write_bytes(b"x")
    monkeypatch.setenv("PATH", str(sistema))
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.setattr(programas, "carpeta_del_repo", lambda: tmp_path / "repo")
    monkeypatch.setattr(config, "DIR_PROGRAMAS", tmp_path / "local")
    assert programas.ejecutable(programas.TESSERACT) is None
    # Incompleto (sin español) tampoco sirve
    incluido = tmp_path / "repo" / "tesseract"
    (incluido / "tessdata" / "configs").mkdir(parents=True)
    for n in ("tesseract.exe", "tessdata/eng.traineddata", "tessdata/configs/hocr", "tessdata/configs/txt"):
        (incluido / n).write_bytes(b"x")
    assert programas.ejecutable(programas.TESSERACT) is None
    (incluido / "tessdata" / "spa.traineddata").write_bytes(b"x")
    assert programas.ejecutable(programas.TESSERACT) == incluido / "tesseract.exe"


def test_version_empaquetada_busca_al_lado_del_exe(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "PDFStudio.exe"))
    assert programas.carpetas_base() == [tmp_path, config.DIR_PROGRAMAS]
    assert "--instalar-programas" in programas.comando_preparar()


def test_descarga_con_otra_huella_se_descarta(monkeypatch, tmp_path):
    def falsa(url, destino, progreso=None, mensaje=""):
        Path(destino).write_bytes(b"no es el instalador")
        return destino
    monkeypatch.setattr(programas, "descargar", falsa)
    destino = tmp_path / "t.exe"
    with pytest.raises(ErrorInstalacion, match="SHA-256"):
        programas.bajar_verificado(programas.INSTALADOR_TESSERACT, destino)
    assert not destino.exists()


def test_descarga_sin_https_se_rechaza(tmp_path):
    with pytest.raises(ErrorInstalacion, match="HTTPS"):
        programas.bajar_verificado(programas.Archivo("http://ejemplo.com/x.exe", "0" * 64, 1), tmp_path / "x")


def test_cache_verificada_no_vuelve_a_bajar(monkeypatch, tmp_path):
    import hashlib
    datos = b"contenido"
    archivo = programas.Archivo("https://github.com/tesseract-ocr/x/y.bin", hashlib.sha256(datos).hexdigest(), 9)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / archivo.sha256).write_bytes(datos)      # la caché guarda por huella
    monkeypatch.setattr(programas, "descargar", lambda *a, **k: pytest.fail("no debía descargar"))
    programas.bajar_verificado(archivo, tmp_path / "y.bin", cache=tmp_path / "cache")
    assert (tmp_path / "y.bin").read_bytes() == datos


def test_lee_las_dll_que_importa_un_ejecutable():
    """El lector de importaciones (sin dependencias) con el python.exe de la base."""
    importadas = {d.lower() for d in programas.importaciones_pe(Path(sys.base_prefix) / "python.exe")}
    assert f"python{sys.version_info.major}{sys.version_info.minor}.dll" in importadas
    assert "kernel32.dll" in importadas


def test_dependencias_avisa_lo_que_falta(tmp_path):
    import shutil
    base = Path(sys.base_prefix)
    shutil.copy2(base / "python.exe", tmp_path / "python.exe")
    _usados, faltan = programas.dependencias(tmp_path, ["python.exe"])
    assert f"python{sys.version_info.major}{sys.version_info.minor}.dll" in faltan
    shutil.copy2(base / f"python{sys.version_info.major}{sys.version_info.minor}.dll", tmp_path)
    usados, faltan = programas.dependencias(tmp_path, ["python.exe"])
    assert len(usados) == 2


def test_instalacion_asistida_sin_admin_ni_winget():
    for p in programas.PROGRAMAS:
        inst = programas.instalacion(p)
        assert not inst.requiere_admin and str(config.DIR_PROGRAMAS) in inst.destino
        assert inst.tamano() == programas.tamano_descarga(p)
        assert inst.licencia in ("Apache 2.0", "AGPL-3.0")


def test_no_queda_winget_en_la_app():
    """Ni la app, ni el instalador, ni las herramientas, ni el README dependen de winget."""
    rutas = [*(RAIZ / "pdf_studio").rglob("*.py"), *(RAIZ / "instalador").iterdir(),
             *(RAIZ / "herramientas").glob("*.py"), RAIZ / "README.md", *(RAIZ / ".github" / "workflows").iterdir()]
    con_winget = [str(r.relative_to(RAIZ)) for r in rutas
                  if r.is_file() and "winget" in r.read_text(encoding="utf-8", errors="ignore").lower()]
    assert con_winget == []


# ---------------------------------------------------------------------- los armados de verdad
def _exigidos() -> bool:
    return os.environ.get("PDFSTUDIO_EXIGIR_PROGRAMAS") == "1"


@pytest.mark.skipif(not _exigidos() and programas.carpeta(programas.TESSERACT) is None,
                    reason="Tesseract no está preparado (python -m pdf_studio.dependencias.programas)")
def test_tesseract_armado_funciona_y_esta_completo():
    carpeta = programas.carpeta(programas.TESSERACT)
    assert carpeta is not None
    programas.comprobar(carpeta, programas.TESSERACT)
    _usados, faltan = programas.dependencias(carpeta, ["tesseract.exe"])
    assert not faltan
    for idioma, archivo in programas.IDIOMAS.items():
        assert programas.sha256(carpeta / "tessdata" / f"{idioma}.traineddata") == archivo.sha256
    assert (carpeta / "LICENSE-tesseract.txt").is_file() and (carpeta / "LICENSE-tessdata_best.txt").is_file()


@pytest.mark.skipif(not _exigidos() and programas.carpeta(programas.GHOSTSCRIPT) is None,
                    reason="Ghostscript no está preparado (python -m pdf_studio.dependencias.programas)")
def test_ghostscript_armado_funciona_y_trae_su_runtime():
    carpeta = programas.carpeta(programas.GHOSTSCRIPT)
    assert carpeta is not None
    programas.comprobar(carpeta, programas.GHOSTSCRIPT)
    _usados, faltan = programas.dependencias(carpeta, ["gswin64c.exe", "gsdll64.dll"])
    assert not faltan                      # el runtime de Visual C++ va en la carpeta
    assert all((carpeta / n).is_file() for n in programas.RUNTIME_MSVC)
    assert (carpeta / "COPYING-ghostscript.txt").is_file()
