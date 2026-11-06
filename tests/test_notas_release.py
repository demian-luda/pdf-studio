"""Notas del release: salen de la sección del CHANGELOG.md de la versión del tag, más los
pasos de instalación."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "herramientas"))

import notas_release as nr  # noqa: E402

CHANGELOG = """# Cambios

## [0.3.0] - 2026-12-01

### Agregado

- Algo nuevo.

## [0.2.0] - 2026-11-03

### Corregido

- Un arreglo.

## [0.1.0] - 2026-10-21

Primera versión.

[0.2.0]: https://example.com/compare/v0.1.0...v0.2.0
"""


@pytest.mark.parametrize("tag", ["v0.2.0", "0.2.0", "refs/tags/v0.2.0"])
def test_toma_solo_la_seccion_del_tag(tag):
    cuerpo = nr.seccion_changelog(CHANGELOG, tag)
    assert cuerpo == "### Corregido\n\n- Un arreglo."


def test_ultima_seccion_sin_definiciones_de_enlace():
    assert nr.seccion_changelog(CHANGELOG, "v0.1.0") == "Primera versión."


def test_version_inexistente_da_error():
    with pytest.raises(nr.SinSeccion):
        nr.seccion_changelog(CHANGELOG, "v9.9.9")


def test_seccion_vacia_da_error():
    with pytest.raises(nr.SinSeccion):
        nr.seccion_changelog("## [1.0.0]\n\n## [0.9.0]\n- x\n", "v1.0.0")


def test_no_confunde_versiones_con_prefijo_comun():
    texto = "## [0.2.10]\n- diez\n\n## [0.2.1]\n- uno\n"
    assert nr.seccion_changelog(texto, "v0.2.1") == "- uno"


def test_une_renglones_cortados_pero_no_bloques():
    texto = ("## [1.0.0]\n\n### Agregado\n\n- **Algo.** Primera parte\n  y segunda parte.\n"
             "- Otro punto.\n\nUn párrafo\ncortado.\n\n```\nlinea 1\nlinea 2\n```\n")
    cuerpo = nr.seccion_changelog(texto, "1.0.0")
    assert cuerpo == ("### Agregado\n\n- **Algo.** Primera parte y segunda parte.\n"
                      "- Otro punto.\n\nUn párrafo cortado.\n\n```\nlinea 1\nlinea 2\n```")


@pytest.mark.parametrize("firmado", [False, True])
def test_notas_completas(firmado):
    notas = nr.armar_notas("v0.3.0", firmado, CHANGELOG)
    assert notas.startswith("### Agregado\n\n- Algo nuevo.")
    assert "Un arreglo" not in notas
    assert "## Instalación" in notas
    assert "PDFStudio-0.3.0-windows.zip" in notas
    assert "Instalar.cmd" in notas
    assert "SHA256SUMS.txt" in notas
    assert ("SignPath Foundation" in notas) == firmado
    assert ("Windows protegió su PC" in notas) == (not firmado)


def test_version_actual_tiene_notas():
    """La versión del proyecto tiene su sección en el CHANGELOG real (si no, el release fallaría)."""
    from pdf_studio import __version__
    notas = nr.armar_notas(f"v{__version__}", firmado=False)
    assert "## Instalación" in notas


def test_main_escribe_archivo_y_falla_sin_seccion(tmp_path, capsys):
    salida = tmp_path / "notas.md"
    from pdf_studio import __version__
    assert nr.main([f"v{__version__}", "--salida", str(salida)]) == 0
    assert "## Instalación" in salida.read_text(encoding="utf-8")
    assert nr.main(["v99.0.0"]) == 1
    assert "no tiene una sección" in capsys.readouterr().err
