"""Última posición de lectura de cada PDF (página, zoom y desplazamiento), para retomar
donde se dejó al volver a abrirlo.

Se guarda en %APPDATA%\\PDFStudio\\posiciones.json, solo en esta PC: la ruta de cada archivo
y dónde quedó, nada de su contenido. Se recuerdan los últimos MAX_ARCHIVOS.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from pdf_studio import config

MAX_ARCHIVOS = 300
_registro = logging.getLogger("pdf_studio")


def _archivo() -> Path:
    return config.DIR_APPDATA / "posiciones.json"


def _clave(ruta: str | Path) -> str:
    # Windows no distingue mayúsculas en las rutas
    return os.path.normcase(str(Path(ruta).resolve()))


def _leer_todo() -> dict[str, dict]:
    try:
        datos = json.loads(_archivo().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        _registro.warning("No se pudieron leer las posiciones guardadas: %r", e)
        return {}
    return datos if isinstance(datos, dict) else {}


def leer(ruta: str | Path) -> dict | None:
    """{'pagina', 'zoom', 'ajuste', 'desplazamiento'} de la última vez, o None."""
    vista = _leer_todo().get(_clave(ruta))
    if not isinstance(vista, dict) or not isinstance(vista.get("pagina"), int):
        return None
    return vista


def guardar(ruta: str | Path, vista: dict) -> None:
    """Guarda la vista de un archivo. Nunca falla: si no se puede escribir, se registra y sigue."""
    try:
        todo = _leer_todo()
        clave = _clave(ruta)
        todo.pop(clave, None)
        todo[clave] = vista                        # al final: el más reciente
        while len(todo) > MAX_ARCHIVOS:
            todo.pop(next(iter(todo)))
        destino = _archivo()
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atómica: un corte a mitad de camino no deja el archivo roto
        fd, tmp = tempfile.mkstemp(dir=destino.parent, prefix=".posiciones-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(todo, f, ensure_ascii=False)
        os.replace(tmp, destino)
    except OSError as e:
        _registro.warning("No se pudo guardar la posición de lectura: %r", e)
