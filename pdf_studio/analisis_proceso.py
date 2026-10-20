"""Análisis de fuentes de un PDF en un proceso aparte.

MuPDF no admite usarse desde varios hilos a la vez, así que para no trabar la
interfaz el análisis corre en otro proceso: recibe por stdin {"ruta", "contrasena"}
y devuelve por stdout un JSON con las fuentes. Nunca imprime contenido del documento.

Uso interno:  python -m pdf_studio.analisis_proceso   (o PDFStudio.exe --analizar-fuentes)
"""
from __future__ import annotations

import json
import sys
import traceback


def analizar(ruta: str, contrasena: str | None) -> dict:
    import pymupdf

    from pdf_studio.core.fuentes import analizar_fuentes, indice_incluidas, indice_sistema
    doc = pymupdf.open(ruta)
    try:
        if doc.needs_pass and not doc.authenticate(contrasena or ""):
            return {"ok": False, "error": "contrasena", "detalle": "Contraseña incorrecta."}
        fuentes = analizar_fuentes(doc, indice_sistema(), indice_incluidas())
        return {"ok": True, "fuentes": [f.a_dict() for f in fuentes]}
    finally:
        doc.close()


def main() -> int:
    try:
        pedido = json.loads(sys.stdin.read() or "{}")
        resultado = analizar(pedido["ruta"], pedido.get("contrasena"))
    except Exception as e:
        resultado = {"ok": False, "error": type(e).__name__, "detalle": traceback.format_exc(limit=8)}
    sys.stdout.write(json.dumps(resultado))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
