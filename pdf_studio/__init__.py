"""PDF Studio — editor de PDF universal y gratuito (AGPL-3.0)."""
import os as _os
import sys as _sys

NOMBRE_APP = "PDF Studio"
__version__ = "0.2.0"

# pymupdf 2.0 instala como dependencias pymupdf_layout y pymupdf4llm, y al importarse carga
# los dos (con onnxruntime y modelos de análisis de diagramación): más de un segundo de
# arranque. La app no los usa (sirven para Page.get_layout y to_markdown) y el .exe los
# excluye (herramientas/empaquetar.py), así que desde el código se bloquean igual: arranca
# tan rápido como el .exe y se comporta igual. Tiene que pasar antes del primer `import pymupdf`.
if "pymupdf" not in _sys.modules:
    for _modulo in ("pymupdf.layout", "pymupdf4llm"):
        _sys.modules.setdefault(_modulo, None)
# Sin layout, find_tables() recomendaría instalarlo por consola: no aplica.
_os.environ.setdefault("PYMUPDF_SUGGEST_LAYOUT_ANALYZER", "0")
