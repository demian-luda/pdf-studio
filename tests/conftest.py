"""Fixtures compartidas por todos los tests."""
from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

DIR_PDFS = Path(__file__).resolve().parent / "pdfs_prueba"


def _asegurar_pdfs() -> None:
    esperados = ["texto_varias_paginas.pdf", "tablas.pdf", "imagen_fondo.pdf", "formulario.pdf",
                 "escaneado.pdf", "fuentes_recortadas.pdf", "protegido.pdf"]
    imagen = DIR_PDFS.parent / "imagenes_prueba" / "firma_escaneada.jpg"
    if not all((DIR_PDFS / n).is_file() for n in esperados) or not imagen.is_file():
        from tests import generar_pdfs_prueba
        generar_pdfs_prueba.main()


_asegurar_pdfs()


def hash_archivo(ruta: Path) -> str:
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


@pytest.fixture
def copia_pdf(tmp_path):
    """Copia un PDF de prueba a una carpeta temporal (para verificar que el original no cambie)."""
    def _copiar(nombre: str) -> Path:
        destino = tmp_path / nombre
        shutil.copy2(DIR_PDFS / nombre, destino)
        return destino
    return _copiar


@pytest.fixture(autouse=True)
def cache_aislada(tmp_path, monkeypatch):
    """Los índices de fuentes de los tests usan una caché temporal."""
    from pdf_studio import config
    from pdf_studio.core import fuentes
    monkeypatch.setattr(config, "DIR_CACHE", tmp_path / "cache")
    monkeypatch.setattr(fuentes, "_indice_incluidas", None)
    yield


@pytest.fixture(autouse=True)
def sin_dialogos_modales(monkeypatch):
    """Si un test olvida reemplazar un diálogo, falla en vez de quedar colgado esperando."""
    from PySide6.QtWidgets import QMessageBox

    def prohibido(*a, **k):
        raise AssertionError("Un test abrió un diálogo modal sin reemplazarlo")
    for nombre in ("information", "critical", "question", "warning"):
        monkeypatch.setattr(QMessageBox, nombre, staticmethod(prohibido))
    yield


@pytest.fixture
def ventana(qtbot, monkeypatch, tmp_path):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "firmas"))
    qtbot.addWidget(v)
    v.resize(1100, 800)
    v.show()
    qtbot.waitExposed(v)
    mensajes = []
    monkeypatch.setattr(v, "_informar", lambda t, x, error=False: mensajes.append((t, x, error)))
    monkeypatch.setattr(v, "_mostrar_guardado", lambda g, d: mensajes.append(("guardado", g, d)))
    monkeypatch.setattr(v, "_preguntar_guardar_cambios", lambda: "descartar")
    v.mensajes = mensajes
    yield v
    v.cerrar_documento()
