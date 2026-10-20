"""Tareas pesadas de MuPDF en un proceso aparte (MuPDF no admite hilos y la interfaz no
se tiene que trabar). Recibe la ruta de un JSON con el pedido y escribe el resultado en
otro JSON. Nunca escribe contenido del documento en la salida.

Uso interno:  python -m pdf_studio.procesos <tarea> <pedido.json> <resultado.json>
              (o PDFStudio.exe --tarea <tarea> <pedido.json> <resultado.json>)
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _comprimir(pedido: dict) -> dict:
    from pdf_studio.core.comprimir import NivelCompresion, comprimir_y_verificar
    resultado = comprimir_y_verificar(Path(pedido["entrada"]), Path(pedido["salida"]),
                                      NivelCompresion(pedido["nivel"]), pedido.get("contrasena"))
    return resultado.a_dict()


def _comparar(pedido: dict) -> dict:
    from pdf_studio.core.comparar import comparar_archivos
    return comparar_archivos(Path(pedido["a"]), Path(pedido["b"]), pedido.get("contrasena_a"),
                             pedido.get("contrasena_b")).a_dict()


TAREAS = {"comprimir": _comprimir, "comparar": _comparar}


def ejecutar(tarea: str, pedido_json: Path, resultado_json: Path) -> int:
    from pdf_studio.core.documento import ErrorDocumento
    try:
        pedido = json.loads(Path(pedido_json).read_text(encoding="utf-8"))
        salida = {"ok": True, "resultado": TAREAS[tarea](pedido)}
    except ErrorDocumento as e:
        salida = {"ok": False, "error": str(e)}
    except Exception as e:
        salida = {"ok": False, "error": f"Error inesperado ({type(e).__name__}).",
                  "detalle": traceback.format_exc(limit=8)}
    Path(resultado_json).write_text(json.dumps(salida), encoding="utf-8")
    return 0


def comando(tarea: str, pedido_json: Path, resultado_json: Path) -> tuple[str, list[str]]:
    argumentos = [tarea, str(pedido_json), str(resultado_json)]
    if getattr(sys, "frozen", False):
        return sys.executable, ["--tarea", *argumentos]
    return sys.executable, ["-m", "pdf_studio.procesos", *argumentos]


def entorno() -> dict[str, str]:
    """Entorno para el proceso: que encuentre el paquete aunque la app se lance desde otra carpeta."""
    import os
    env = dict(os.environ)
    raiz = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = raiz + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


if __name__ == "__main__":
    sys.exit(ejecutar(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])))
