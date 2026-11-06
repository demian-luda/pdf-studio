"""Mide la memoria de la app al abrir muchos PDFs en pestañas y cerrarlos, varias veces.

Abre N PDFs de una carpeta (sobre copias temporales) en la ventana real, los recorre para que
se dibujen y se analicen, cierra todas las pestañas y repite. Muestra la memoria privada del
proceso (y la de sus subprocesos) en cada etapa y lo que queda vivo al cerrar todo. Si la
memoria no vuelve aproximadamente a la del principio, algo queda retenido.

Uso:
    python herramientas/medir_memoria_pestanas.py C:\\Proyectos\\pdfs-reales [--cantidad 20] [--rondas 3]

El informe muestra solo números (nunca nombres ni contenido de los archivos).
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("carpeta", type=Path)
    p.add_argument("--cantidad", type=int, default=20)
    p.add_argument("--rondas", type=int, default=3)
    args = p.parse_args(argv)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["PDFSTUDIO_SIN_INSTANCIA_UNICA"] = "1"

    import psutil
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop
    from PySide6.QtWidgets import QApplication

    from pdf_studio import posiciones
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.tema import gestor
    from pdf_studio.ui.ventana_principal import VentanaPrincipal

    proceso = psutil.Process()

    def procesar(segundos: float) -> None:
        limite = time.monotonic() + segundos
        while time.monotonic() < limite:
            QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)

    def memoria() -> str:
        gc.collect()
        procesar(0.3)
        # Como el bucle de eventos de la app: procesa los deleteLater pendientes
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        privada = proceso.memory_full_info().private if os.name == "nt" else proceso.memory_info().rss
        hijos = sum(h.memory_info().rss for h in proceso.children(recursive=True) if h.is_running())
        return f"{privada / 2**20:6.0f} MB (subprocesos {hijos / 2**20:4.0f} MB)"

    def vivos() -> str:
        gc.collect()
        n = {"Documento": 0, "pymupdf.Document": 0, "Pestana": 0}
        for o in gc.get_objects():
            t = type(o)
            if t.__name__ == "Documento" and t.__module__ == "pdf_studio.core.documento":
                n["Documento"] += 1
            elif t.__name__ == "Document" and (t.__module__ or "").startswith("pymupdf"):
                n["pymupdf.Document"] += 1
            elif t.__name__ == "Pestana":
                n["Pestana"] += 1
        n["widgets"] = len(QApplication.allWidgets())
        return " ".join(f"{k}={v}" for k, v in n.items())

    def clases_de_widgets() -> dict[str, int]:
        cuenta: dict[str, int] = {}
        for w in QApplication.allWidgets():
            clave = type(w).__name__ + (f"({w.parent().__class__.__name__})" if w.parent() else "")
            cuenta[clave] = cuenta.get(clave, 0) + 1
        return cuenta

    archivos = sorted(x for x in args.carpeta.glob("*") if x.suffix.lower() == ".pdf")[:args.cantidad]
    if not archivos:
        print("No hay PDFs en la carpeta.")
        return 1
    app = QApplication.instance() or QApplication([])
    gestor().aplicar()
    with tempfile.TemporaryDirectory(prefix="pdfstudio_memoria_") as tmp:
        tmp = Path(tmp)
        posiciones._archivo = lambda: tmp / "posiciones.json"     # no tocar las del usuario
        copias = []
        for i, a in enumerate(archivos):
            destino = tmp / f"{i:03d}.pdf"
            shutil.copy2(a, destino)
            copias.append(destino)
        v = VentanaPrincipal(biblioteca=BibliotecaFirmas(tmp / "firmas"))
        v._pedir_contrasena = lambda *a, **k: None
        v._informar = lambda *a, **k: None
        v._preguntar_guardar_cambios = lambda: "descartar"
        v.resize(1200, 850)
        v.show()
        procesar(3)                          # índice de fuentes y chequeo de dependencias
        print(f"inicio                 {memoria()}   {vivos()}", flush=True)
        for ronda in range(1, args.rondas + 1):
            for c in copias:
                v.abrir(c)
                procesar(0.2)
            # Recorrer las pestañas: se dibujan, se arman las miniaturas y se analizan
            for _ in range(len(v.pestanas)):
                v.pestana_vecina(1)
                procesar(0.4)
            print(f"ronda {ronda}: {len(v.pestanas):2d} abiertas  {memoria()}   {vivos()}", flush=True)
            v.cerrar_todas()
            procesar(1)
            if os.environ.get("VACIAR_STORE"):
                import pymupdf
                pymupdf.TOOLS.store_shrink(100)
            print(f"ronda {ronda}: cerradas     {memoria()}   {vivos()}", flush=True)
            if os.environ.get("DETALLE_WIDGETS"):
                ahora = clases_de_widgets()
                if ronda > 1:
                    crecen = {k: ahora[k] - antes.get(k, 0) for k in ahora if ahora[k] > antes.get(k, 0)}
                    print("         widgets que crecieron:", crecen, flush=True)
                antes = ahora
        v.close()
        procesar(0.5)
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
