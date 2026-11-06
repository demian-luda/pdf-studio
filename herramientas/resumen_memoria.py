"""Resume el registro de memoria de la suite (tests/memoria.py) en Markdown.

Uso:  python herramientas/resumen_memoria.py memoria.tsv [--titulo "..."]

Muestra el pico, el final, la memoria al terminar cada archivo de tests (para ver si crece de
forma sostenida), los tests que más la hicieron subir y lo que seguía vivo al final.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def resumir(texto: str, titulo: str = "Memoria de la suite") -> str:
    filas = []
    for linea in texto.splitlines()[1:]:
        partes = linea.split("\t")
        if len(partes) < 6:
            continue
        seg, test, rss, privada, hijos, vivos = partes[:6]
        arbol = int(partes[6]) if len(partes) > 6 and partes[6] else int(rss) + int(hijos)
        filas.append((int(seg), test, int(rss), int(privada), int(hijos), vivos, arbol))
    if not filas:
        return f"## {titulo}\n\nSin datos.\n"
    pico = max(filas, key=lambda f: f[3])
    pico_hijos = max(filas, key=lambda f: f[4])
    pico_arbol = max(filas, key=lambda f: f[6])
    lineas = [f"## {titulo}", "",
              f"- Tests: {len(filas)} · duración: {filas[-1][0] // 60} min {filas[-1][0] % 60} s",
              f"- Memoria privada del proceso de tests: primera {filas[0][3]} MB · pico **{pico[3]} MB** "
              f"(en `{pico[1]}`) · final **{filas[-1][3]} MB**",
              f"- RSS: primera {filas[0][2]} MB · final {filas[-1][2]} MB",
              f"- Subprocesos vivos al terminar un test: hasta {pico_hijos[4]} MB (en `{pico_hijos[1]}`)",
              f"- Pico de todo el árbol durante un test (tests + OCR, Tesseract, tareas): "
              f"**{pico_arbol[6]} MB** (en `{pico_arbol[1]}`)",
              "", "### Al terminar cada archivo", "",
              "| Archivo | Privada (MB) | Δ en el archivo | Vivos después de gc |", "|---|---:|---:|---|"]
    anterior = filas[0][3]
    for f in filas:
        if f[5]:
            archivo = f[1].split("::")[0].replace("tests/", "")
            lineas.append(f"| {archivo} | {f[3]} | {f[3] - anterior:+d} | {f[5]} |")
            anterior = f[3]
    subidas = sorted(((filas[i][3] - filas[i - 1][3], filas[i][1]) for i in range(1, len(filas))), reverse=True)
    lineas += ["", "### Tests que más subieron la memoria", "", "| Δ (MB) | Test |", "|---:|---|"]
    lineas += [f"| {d:+d} | `{t}` |" for d, t in subidas[:12]]
    arboles = sorted(((f[6], f[1]) for f in filas), reverse=True)
    lineas += ["", "### Picos de todo el árbol de procesos", "", "| Pico (MB) | Test |", "|---:|---|"]
    lineas += [f"| {m} | `{t}` |" for m, t in arboles[:12]]
    return "\n".join(lineas) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("archivo", type=Path)
    p.add_argument("--titulo", default="Memoria de la suite")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(resumir(args.archivo.read_text(encoding="utf-8"), args.titulo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
