"""Detector de dependencias y comandos de PowerShell generados."""
from __future__ import annotations

import shutil
import subprocess
import sys

import pymupdf
import pytest

from pdf_studio import config
from pdf_studio.core.fuentes import Embebido, FuenteSistema, IndiceFuentesSistema, InfoFuente
from pdf_studio.dependencias import chequeo
from pdf_studio.dependencias.catalogo_fuentes import buscar_comercial, buscar_libre, comando_liberation
from pdf_studio.dependencias.chequeo import (Importancia, PaquetePython, chequear_fuentes, chequear_python,
                                             comando_ghostscript, comando_idiomas_ocr, comando_tesseract,
                                             faltante_de_fuente)
from tests.conftest import DIR_PDFS


def test_paquete_faltante_da_comando_pip_del_venv():
    faltan = chequear_python([PaquetePython("modulo_que_no_existe_xyz", "paquete-xyz", "probar",
                                            Importancia.OPCIONAL),
                              PaquetePython("pymupdf", "pymupdf", "pdf", Importancia.NECESARIO)])
    assert len(faltan) == 1
    assert faltan[0].comando == f'& "{sys.executable}" -m pip install paquete-xyz'


def test_paquetes_esenciales_instalados():
    esenciales = [p for p in chequeo.PAQUETES if p.importancia == Importancia.NECESARIO]
    assert chequear_python(esenciales) == []


def test_comando_tesseract_usa_id_verificado():
    assert "winget install --id tesseract-ocr.tesseract -e" in comando_tesseract()


def test_comando_idiomas():
    c = comando_idiomas_ocr()
    assert "spa" in c and "eng" in c
    assert str(config.DIR_TESSDATA_USUARIO) in c
    assert "https://github.com/tesseract-ocr/tessdata/raw/main/$l.traineddata" in c


def test_tesseract_ausente(monkeypatch):
    monkeypatch.setattr(chequeo, "buscar_tesseract", lambda: None)
    faltan = chequeo.chequear_tesseract()
    assert [f.clave for f in faltan] == ["programa:tesseract", "idioma:ocr"]


def test_tesseract_sin_espanol(monkeypatch, tmp_path):
    monkeypatch.setattr(chequeo, "buscar_tesseract", lambda: tmp_path / "tesseract.exe")
    monkeypatch.setattr(chequeo, "idiomas_tesseract", lambda exe, td=None: {"eng", "osd"})
    faltan = chequeo.chequear_tesseract()
    assert len(faltan) == 1 and "spa" in faltan[0].titulo


def test_comando_ghostscript():
    c = comando_ghostscript()
    assert "ArtifexSoftware/ghostpdl-downloads/releases/latest" in c
    assert "gs*w64.exe" in c


def _fuente(nombre, embebido, instalada=False, incluida=False):
    sistema = FuenteSistema("x.ttf", 0, nombre, nombre, "Regular") if instalada else None
    incl = FuenteSistema("y.ttf", 0, nombre, nombre, "Regular") if incluida else None
    return InfoFuente(nombre, nombre, "TrueType", "ttf" if embebido != Embebido.NO_EMBEBIDA else "n/a",
                      embebido, [1], [0], sistema, incl)


def test_fuente_libre_genera_comando_google_fonts():
    f = faltante_de_fuente(_fuente("OpenSans-Regular", Embebido.SUBCONJUNTO))
    assert f is not None and f.comando
    assert "api.github.com/repos/google/fonts/contents/ofl/opensans" in f.comando
    assert "HKCU:" in f.comando and "LOCALAPPDATA" in f.comando
    assert "libre" in f.detalle


def test_fuente_comercial_no_inventa_descarga():
    f = faltante_de_fuente(_fuente("Calibri-Bold", Embebido.SUBCONJUNTO))
    assert f is not None and f.comando is None
    assert "Carlito" in f.detalle
    assert buscar_comercial("TimesNewRomanPSMT").alternativa == "Liberation Serif"


def test_fuente_desconocida():
    f = faltante_de_fuente(_fuente("MiFuentePrivadaXYZ", Embebido.NO_EMBEBIDA))
    assert f is not None and f.comando is None
    assert f.importancia == Importancia.RECOMENDADO


@pytest.mark.parametrize("info", [
    _fuente("Calibri", Embebido.SUBCONJUNTO, instalada=True),
    _fuente("LiberationSans", Embebido.SUBCONJUNTO, incluida=True),
    _fuente("MiFuente", Embebido.COMPLETA),
    _fuente("Helvetica", Embebido.NO_EMBEBIDA),
])
def test_fuentes_sin_problema(info):
    assert faltante_de_fuente(info) is None


def test_liberation_usa_tarball_oficial():
    libre = buscar_libre("LiberationSerif-Bold")
    assert libre is not None and libre.nombre == "Liberation Serif"
    assert "liberation-fonts-ttf-2.1.5.tar.gz" in libre.comando
    assert "LiberationSerif-*.ttf" in libre.comando
    assert "Copy-Item" in libre.comando


def test_chequeo_de_documento(tmp_path):
    vacio = IndiceFuentesSistema([tmp_path / "nada"], tmp_path / "a.json")
    incluidas = IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path / "b.json")
    with pymupdf.open(DIR_PDFS / "fuentes_recortadas.pdf") as doc:
        fuentes, faltan = chequear_fuentes(doc, vacio, incluidas)
    assert len(fuentes) == 4
    # Solo la "Arial" recortada sin instalar necesita atención; las Liberation vienen con la app
    assert [f.titulo for f in faltan] == ["Fuente «ArialMT»"]
    assert "Liberation Sans" in faltan[0].detalle


@pytest.mark.skipif(shutil.which("powershell") is None, reason="Requiere PowerShell")
def test_comandos_son_powershell_valido():
    """Parsea (sin ejecutar) cada comando generado con el parser oficial de PowerShell."""
    comandos = [
        comando_tesseract(), comando_idiomas_ocr(), comando_ghostscript(), comando_liberation("Sans"),
        buscar_libre("Roboto-Bold").comando, chequeo.comando_pip("ocrmypdf"),
    ]
    for c in comandos:
        script = ("$t = $null; $e = $null; [void][System.Management.Automation.Language.Parser]::ParseInput("
                  "[Console]::In.ReadToEnd(), [ref]$t, [ref]$e); "
                  "if ($e.Count) { $e | ForEach-Object { $_.Message }; exit 1 }")
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           input=c, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"Comando inválido:\n{c}\n{r.stdout}{r.stderr}"


def _ejecutar_descarga_sin_instalar(comando: str, destino) -> list[str]:
    """Corre el comando real cambiando el destino y sin tocar el registro."""
    prueba = comando.replace('$destino = "$env:LOCALAPPDATA\\Microsoft\\Windows\\Fonts"', f"$destino = '{destino}'")
    prueba = "\n".join(l for l in prueba.splitlines() if "New-ItemProperty" not in l)
    assert "LOCALAPPDATA" not in prueba and "HKCU" not in prueba
    script = destino.parent / "descarga.ps1"
    script.write_text(prueba, encoding="utf-8-sig")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                        "-File", str(script)], capture_output=True, text=True, timeout=240)
    assert r.returncode == 0, r.stderr
    return sorted(p.name for p in destino.iterdir())


@pytest.mark.red
@pytest.mark.skipif(shutil.which("powershell") is None, reason="Requiere PowerShell")
def test_descarga_real_google_fonts(tmp_path):
    (tmp_path / "f").mkdir()
    archivos = _ejecutar_descarga_sin_instalar(buscar_libre("Carlito-Bold").comando, tmp_path / "f")
    assert archivos == ["Carlito-Bold.ttf", "Carlito-BoldItalic.ttf", "Carlito-Italic.ttf", "Carlito-Regular.ttf"]


@pytest.mark.red
@pytest.mark.skipif(shutil.which("powershell") is None, reason="Requiere PowerShell")
def test_descarga_real_liberation(tmp_path):
    (tmp_path / "f").mkdir()
    archivos = _ejecutar_descarga_sin_instalar(comando_liberation("Serif"), tmp_path / "f")
    assert archivos == ["LiberationSerif-Bold.ttf", "LiberationSerif-BoldItalic.ttf",
                        "LiberationSerif-Italic.ttf", "LiberationSerif-Regular.ttf"]