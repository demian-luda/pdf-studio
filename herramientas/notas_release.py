"""Arma las notas de un release: la sección de CHANGELOG.md de esa versión, más los pasos
de instalación, la nota sobre la firma del .exe y cómo comprobar la descarga.

Uso (lo llama el workflow de release):
    python herramientas/notas_release.py v0.2.0 --salida notas.md [--firmado]

Si el changelog no tiene una sección para la versión, o está vacía, termina con error:
así no se publica un release sin notas.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
CHANGELOG = RAIZ / "CHANGELOG.md"

# Encabezado de versión: «## [0.2.0] - 2026-11-03» (también acepta «## 0.2.0»).
_ENCABEZADO = re.compile(r"^##\s+\[?v?(?P<version>[^\]\s]+)\]?(?P<resto>.*)$")
# Definición de enlace al pie del changelog: «[0.2.0]: https://...».
_DEF_ENLACE = re.compile(r"^\[[^\]]+\]:\s")


class SinSeccion(Exception):
    """El changelog no tiene notas para la versión pedida."""


def normalizar_version(tag: str) -> str:
    """«v0.2.0» y «refs/tags/v0.2.0» → «0.2.0»."""
    tag = tag.strip().removeprefix("refs/tags/")
    return tag[1:] if tag[:1] in ("v", "V") else tag


def seccion_changelog(texto: str, version: str) -> str:
    """Devuelve el cuerpo de la sección «## [version]» (sin el encabezado), hasta la sección
    siguiente. Quita las definiciones de enlace del pie y las líneas en blanco de los bordes."""
    version = normalizar_version(version)
    lineas = texto.splitlines()
    inicio = None
    for i, linea in enumerate(lineas):
        m = _ENCABEZADO.match(linea)
        if m and m.group("version") == version:
            inicio = i + 1
            break
    if inicio is None:
        raise SinSeccion(f"CHANGELOG.md no tiene una sección «## [{version}]».")
    cuerpo = []
    for linea in lineas[inicio:]:
        if linea.startswith("## "):
            break
        if _DEF_ENLACE.match(linea):
            continue
        cuerpo.append(linea)
    resultado = "\n".join(unir_renglones(cuerpo)).strip()
    if not resultado:
        raise SinSeccion(f"La sección «## [{version}]» de CHANGELOG.md está vacía.")
    return resultado


# Líneas que empiezan un bloque nuevo de Markdown (no son continuación del renglón anterior).
_INICIO_BLOQUE = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|#|>|\||```|~~~)")


def unir_renglones(lineas: list[str]) -> list[str]:
    """Une los renglones cortados a mano del changelog. En las notas de un release, GitHub
    muestra cada salto de línea como un salto real, y los párrafos quedarían partidos.
    No toca los bloques de código."""
    resultado: list[str] = []
    en_codigo = False
    for linea in lineas:
        es_cerco = linea.lstrip().startswith(("```", "~~~"))
        continua = (not en_codigo and not es_cerco and linea.strip()
                    and resultado and resultado[-1].strip()
                    and not resultado[-1].lstrip().startswith(("#", "```", "~~~", "|"))
                    and not _INICIO_BLOQUE.match(linea))
        if continua:
            resultado[-1] = resultado[-1].rstrip() + " " + linea.strip()
        else:
            resultado.append(linea)
        if es_cerco:
            en_codigo = not en_codigo
    return resultado


def pasos_instalacion(version: str, firmado: bool) -> str:
    """Pasos de instalación y verificación, iguales a los del README."""
    zip_ = f"PDFStudio-{version}-windows.zip"
    if firmado:
        firma = ("El `.exe` está firmado. Free code signing provided by "
                 "[SignPath.io](https://about.signpath.io/), certificate by "
                 "[SignPath Foundation](https://signpath.org/).")
    else:
        firma = ("El `.exe` todavía no está firmado: Windows puede mostrar «Windows protegió su PC». "
                 "Hacé clic en *Más información → Ejecutar de todas formas*.")
    return f"""## Instalación

1. Descargá `{zip_}` y descomprimilo completo.
2. Hacé doble clic en **`Instalar.cmd`**. No pide permisos de administrador: copia la app a \
`%LOCALAPPDATA%\\Programs\\PDF Studio`, crea los accesos directos y agrega PDF Studio a «Abrir con» \
para los PDF, sin cambiar tu programa predeterminado.
3. Abrí **PDF Studio** desde el menú Inicio.

**Para actualizar:** cerrá PDF Studio y hacé lo mismo con el ZIP nuevo. Reemplaza la versión \
anterior, y tus firmas y tu configuración en `%APPDATA%\\PDFStudio` se conservan.

{firma}

**Comprobá la descarga** comparando su SHA-256 con el de `SHA256SUMS.txt`:

```powershell
Get-FileHash .\\{zip_} -Algorithm SHA256
```"""


def armar_notas(tag: str, firmado: bool, changelog: str | None = None) -> str:
    version = normalizar_version(tag)
    if changelog is None:
        changelog = CHANGELOG.read_text(encoding="utf-8")
    cambios = seccion_changelog(changelog, version)
    return f"{cambios}\n\n{pasos_instalacion(version, firmado)}\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tag", help="versión o tag, por ejemplo v0.2.0")
    p.add_argument("--salida", type=Path, help="archivo Markdown de salida (si no, se imprime)")
    p.add_argument("--firmado", action="store_true", help="el .exe está firmado con SignPath")
    args = p.parse_args(argv)
    try:
        notas = armar_notas(args.tag, args.firmado)
    except SinSeccion as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if args.salida:
        args.salida.write_text(notas, encoding="utf-8")
    else:
        # El changelog tiene caracteres fuera de cp1252 (por ejemplo «⟦»).
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(notas)
    return 0


if __name__ == "__main__":
    sys.exit(main())
