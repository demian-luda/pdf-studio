"""Mide cuánto tarda PDF Studio desde que se lanza hasta que se ve la primera página de un PDF.

Lanza la app desde el código (como un doble clic: `python -m pdf_studio <pdf>`) en un proceso
nuevo y toma el tiempo hasta que el visor termina de pintar la primera página. Repite varias
veces y muestra la mediana (la primera corrida suele ser más lenta: disco frío).

Uso:
    python herramientas/medir_arranque.py tests/pdfs_prueba/tablas.pdf [--veces 5]

Para que la medición sea comparable, mide lo mismo con cualquier versión del código: no
depende de ganchos propios de la app, sino de envolver Visor._pintar_pagina desde afuera.
"""
from __future__ import annotations

import argparse
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Código del proceso hijo: avisa por stdout al pintar la primera página y cierra la app.
_HIJO = r"""
import os, sys, time
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
import pdf_studio.ui.visor as visor

original = visor.Visor._pintar_pagina
avisado = []

def envuelta(self, painter, indice, *args, **kwargs):
    r = original(self, painter, indice, *args, **kwargs)
    if not avisado:
        avisado.append(1)
        print("PRIMERA_PAGINA", flush=True)
        QTimer.singleShot(0, lambda: os._exit(0))
    return r

visor.Visor._pintar_pagina = envuelta
from pdf_studio.app import main
sys.exit(main(["pdf_studio", sys.argv[1]]))
"""


def medir_una(pdf: Path, limite: float = 120.0) -> float:
    entorno = dict(os.environ, PYTHONIOENCODING="utf-8")
    inicio = time.perf_counter()
    proceso = subprocess.Popen([sys.executable, "-c", _HIJO, str(pdf)], cwd=RAIZ, env=entorno,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        for linea in proceso.stdout:
            if "PRIMERA_PAGINA" in linea:
                return time.perf_counter() - inicio
            if time.perf_counter() - inicio > limite:
                break
        raise RuntimeError("La app terminó o tardó demasiado sin mostrar la primera página.")
    finally:
        proceso.kill()
        proceso.wait()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("pdf", type=Path)
    p.add_argument("--veces", type=int, default=5)
    args = p.parse_args(argv)
    tiempos = []
    for i in range(args.veces):
        t = medir_una(args.pdf.resolve())
        tiempos.append(t)
        print(f"  corrida {i + 1}: {t:.2f} s", flush=True)
    print(f"Mediana: {statistics.median(tiempos):.2f} s  (mín. {min(tiempos):.2f} s, máx. {max(tiempos):.2f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
