"""Tareas pesadas de MuPDF en un proceso aparte (comprimir, comparar versiones, informe de
diferencias, importar fotos como PDF escaneado, convertir un escaneado a documento
editable, verificar una firma digital recién hecha, procesar por lotes): MuPDF no admite hilos y la interfaz no se tiene que trabar. Recibe la ruta de un
JSON con el pedido y escribe el resultado en otro JSON (y, si el pedido trae "progreso", el
avance en un tercero). Nunca escribe contenido del documento en la salida estándar.

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
                             pedido.get("contrasena_b"), pedido.get("nombre_a"), pedido.get("nombre_b")).a_dict()


def _informe(pedido: dict) -> dict:
    from pdf_studio.core.comparar import ResultadoComparacion, exportar_informe
    paginas = exportar_informe(ResultadoComparacion.desde_dict(pedido["resultado"]), Path(pedido["a"]),
                               Path(pedido["b"]), Path(pedido["destino"]), pedido.get("contrasena_a"),
                               pedido.get("contrasena_b"))
    return {"paginas": paginas}


def _escaneo(pedido: dict) -> dict:
    from pdf_studio.core.escaneo import AjusteFoto, OpcionesEscaneo, crear_pdf
    fotos = [AjusteFoto.desde_dict(f) for f in pedido["fotos"]]
    return crear_pdf(fotos, OpcionesEscaneo.desde_dict(pedido["opciones"]), Path(pedido["destino"])).a_dict()


def _avisar_progreso(archivo: str | None):
    def avisar(hechas: int, total: int, etiqueta: str | None = None) -> None:
        if not archivo:
            return
        destino = Path(archivo)
        temporal = destino.with_name(destino.name + ".tmp")
        datos = {"hechas": hechas, "total": total}
        if etiqueta is not None:
            datos["etiqueta"] = etiqueta
        try:
            temporal.write_text(json.dumps(datos), encoding="utf-8")
            temporal.replace(destino)
        except OSError:
            pass                    # el avance es solo informativo
    return avisar


def _convertir(pedido: dict) -> dict:
    from pdf_studio.core import conversion
    from pdf_studio.core.escaneo import AjusteFoto, OpcionesEscaneo
    progreso = _avisar_progreso(pedido.get("progreso"))
    if pedido["origen"] == "fotos":
        fotos = [AjusteFoto.desde_dict(f) for f in pedido["fotos"]]
        paginas = conversion.paginas_de_fotos(fotos, OpcionesEscaneo.desde_dict(pedido["opciones"]))
        total = len(fotos)
    else:
        total = conversion.contar_paginas(Path(pedido["ruta"]), pedido.get("contrasena"))
        if pedido.get("paginas") is not None:
            total = len(pedido["paginas"])
        paginas = conversion.paginas_de_pdf(Path(pedido["ruta"]), pedido.get("contrasena"), pedido.get("paginas"))
    progreso(0, total)
    rec = conversion.convertir(paginas, Path(pedido["tesseract"]), Path(pedido["tessdata"]), Path(pedido["carpeta"]),
                               total, progreso)
    return rec.a_dict()


def _verificar_firma(pedido: dict) -> dict:
    # La firma ya está hecha (la contraseña del certificado nunca pasa por acá): solo se verifica
    from pdf_studio.core.firma_digital import FirmaPreparada, verificar_firma
    return verificar_firma(Path(pedido["origen"]), FirmaPreparada.desde_dict(pedido["firma"]),
                           Path(pedido["destino"]), pedido.get("contrasena")).a_dict()


def _lotes(pedido: dict) -> dict:
    from pdf_studio.core.lotes import TEXTO_ESTADO, OpcionesLote, procesar_lote
    progreso = _avisar_progreso(pedido.get("progreso"))

    def avance(hechos: int, total: int, actual: str | None, ultimo) -> None:
        etiqueta = f"Archivo {min(hechos + 1, total)} de {total}: {actual}" if actual else "Terminando…"
        if ultimo is not None:
            etiqueta += f"\nAnterior: {ultimo.archivo} — {TEXTO_ESTADO[ultimo.estado]}"
        progreso(hechos, total, etiqueta)

    return procesar_lote(OpcionesLote.desde_dict(pedido["opciones"]), avance).a_dict()


TAREAS = {"comprimir": _comprimir, "comparar": _comparar, "informe": _informe, "escaneo": _escaneo,
          "convertir": _convertir, "verificar_firma": _verificar_firma, "lotes": _lotes}


def ejecutar(tarea: str, pedido_json: Path, resultado_json: Path) -> int:
    from pdf_studio.core.documento import ErrorDocumento, Limitacion
    try:
        pedido = json.loads(Path(pedido_json).read_text(encoding="utf-8"))
        salida = {"ok": True, "resultado": TAREAS[tarea](pedido)}
    except ErrorDocumento as e:
        salida = {"ok": False, "error": str(e), "limitacion": isinstance(e, Limitacion)}
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
