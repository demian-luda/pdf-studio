"""Regenera pdf_studio/dependencias/datos/google_fonts.json.

Lista las carpetas de familias del repositorio github.com/google/fonts
(ofl/, apache/, ufl/). El detector de dependencias usa esa lista para saber
si una fuente del PDF es libre y armar el comando que la descarga.

Uso:  .\\.venv\\Scripts\\python.exe herramientas\\actualizar_catalogo_google_fonts.py
"""
import json
import urllib.request
from pathlib import Path

API = "https://api.github.com/repos/google/fonts/git/trees/main"
DESTINO = Path(__file__).resolve().parent.parent / "pdf_studio" / "dependencias" / "datos" / "google_fonts.json"


def _leer(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def main() -> None:
    raiz = _leer(API)
    familias: dict[str, str] = {}
    for nodo in raiz["tree"]:
        if nodo["path"] in ("ofl", "apache", "ufl") and nodo["type"] == "tree":
            sub = _leer(nodo["url"])
            for hijo in sub["tree"]:
                if hijo["type"] == "tree":
                    familias[hijo["path"]] = nodo["path"]
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(json.dumps(dict(sorted(familias.items())), indent=0), encoding="utf-8")
    print(f"{len(familias)} familias guardadas en {DESTINO}")


if __name__ == "__main__":
    main()
