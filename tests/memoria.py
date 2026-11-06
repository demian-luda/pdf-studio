"""Registro de memoria de la suite (plugin de pytest; se activa con PDFSTUDIO_MEMORIA=<archivo.tsv>).

Después de cada test anota la memoria del proceso (RSS y memoria privada), la de sus
subprocesos vivos y el pico de todo el árbol (tests + subprocesos: OCR, Tesseract, tareas)
durante el test, muestreado cada 0,25 s. Al terminar cada archivo de tests hace un gc.collect() y cuenta lo que
sigue vivo: documentos de MuPDF, Documento de la app, ventanas principales, widgets de Qt
e hilos. Si algo crece de forma sostenida, se ve en la columna correspondiente.

    PDFSTUDIO_MEMORIA=memoria.tsv python -m pytest -p tests.memoria
"""
from __future__ import annotations

import gc
import os
import threading
import time
from pathlib import Path

import pytest

_ARCHIVO = os.environ.get("PDFSTUDIO_MEMORIA")
_proceso = None
_filas: list[str] = []
_ultimo_modulo: str | None = None
_inicio = time.monotonic()
_pico_arbol = 0
_candado = threading.Lock()


def _muestrear() -> None:
    """Hilo de fondo: el pico de memoria de todo el árbol de procesos desde el último test."""
    global _pico_arbol
    while True:
        try:
            total = _proceso.memory_info().rss
            for h in _proceso.children(recursive=True):
                try:
                    total += h.memory_info().rss
                except Exception:
                    pass
            with _candado:
                _pico_arbol = max(_pico_arbol, total)
        except Exception:
            pass
        time.sleep(0.25)


def _mb(n: int) -> str:
    return f"{n / 2**20:.0f}"


def _memoria() -> tuple[int, int, int]:
    """(RSS, privada, RSS de los subprocesos vivos)."""
    info = _proceso.memory_full_info() if os.name == "nt" else _proceso.memory_info()
    privada = getattr(info, "private", getattr(info, "uss", info.rss))
    hijos = 0
    for h in _proceso.children(recursive=True):
        try:
            hijos += h.memory_info().rss
        except Exception:
            pass
    return info.rss, privada, hijos


def _vivos() -> dict[str, int]:
    gc.collect()
    cuenta = {"pymupdf.Document": 0, "Documento": 0, "VentanaPrincipal": 0}
    for o in gc.get_objects():
        nombre = type(o).__name__
        modulo = type(o).__module__ or ""
        if nombre == "Document" and modulo.startswith("pymupdf"):
            cuenta["pymupdf.Document"] += 1
        elif nombre == "Documento" and modulo == "pdf_studio.core.documento":
            cuenta["Documento"] += 1
        elif nombre == "VentanaPrincipal":
            cuenta["VentanaPrincipal"] += 1
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        cuenta["widgets"] = len(app.allWidgets()) if app is not None else 0
        cuenta["ventanas"] = len(app.topLevelWidgets()) if app is not None else 0
    except Exception:
        cuenta["widgets"] = cuenta["ventanas"] = -1
    cuenta["hilos"] = threading.active_count()
    return cuenta


def pytest_configure(config):
    global _proceso
    if not _ARCHIVO:
        return
    import psutil
    _proceso = psutil.Process()
    _filas.append("seg\ttest\trss_mb\tprivada_mb\tsubprocesos_mb\tvivos\tpico_arbol_mb")
    threading.Thread(target=_muestrear, name="memoria", daemon=True).start()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _ultimo_modulo, _pico_arbol
    yield
    if _proceso is None:
        return
    rss, privada, hijos = _memoria()
    vivos = ""
    modulo = item.nodeid.split("::")[0]
    siguiente = nextitem.nodeid.split("::")[0] if nextitem is not None else None
    if siguiente != modulo:                     # último test del archivo: inventario completo
        vivos = " ".join(f"{k}={v}" for k, v in _vivos().items())
        rss, privada, hijos = _memoria()
    with _candado:
        pico, _pico_arbol = max(_pico_arbol, rss + hijos), 0
    _filas.append(f"{time.monotonic() - _inicio:.0f}\t{item.nodeid}\t{_mb(rss)}\t{_mb(privada)}\t{_mb(hijos)}"
                  f"\t{vivos}\t{_mb(pico)}")


def pytest_unconfigure(config):
    if _proceso is None or not _filas:
        return
    Path(_ARCHIVO).write_text("\n".join(_filas) + "\n", encoding="utf-8")
