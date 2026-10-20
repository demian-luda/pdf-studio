"""Catálogo de fuentes: cuáles son libres (y cómo instalarlas) y cuáles comerciales.

Las fuentes libres se instalan para el usuario actual, sin permisos de
administrador: el archivo va a %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts y se
registra en HKCU.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pdf_studio.core.fuentes import familia_de, sin_prefijo

_DATOS = Path(__file__).resolve().parent / "datos" / "google_fonts.json"

URL_LIBERATION = ("https://github.com/liberationfonts/liberation-fonts/files/7261482/"
                  "liberation-fonts-ttf-2.1.5.tar.gz")


@dataclass(frozen=True)
class FuenteComercial:
    nombre: str
    alternativa: str          # sustituto libre recomendado
    metrica_compatible: bool  # True si el sustituto tiene los mismos anchos por letra


# Fuentes comerciales frecuentes y su sustituto libre (modo armónico, Fase 4)
COMERCIALES: dict[str, FuenteComercial] = {
    "arial": FuenteComercial("Arial", "Liberation Sans", True),
    "helvetica": FuenteComercial("Helvetica", "Liberation Sans", True),
    "helveticaneue": FuenteComercial("Helvetica Neue", "Liberation Sans", False),
    "timesnewroman": FuenteComercial("Times New Roman", "Liberation Serif", True),
    "times": FuenteComercial("Times", "Liberation Serif", True),
    "couriernew": FuenteComercial("Courier New", "Liberation Mono", True),
    "courier": FuenteComercial("Courier", "Liberation Mono", True),
    "calibri": FuenteComercial("Calibri", "Carlito", True),
    "cambria": FuenteComercial("Cambria", "Caladea", True),
    "verdana": FuenteComercial("Verdana", "DejaVu Sans", False),
    "tahoma": FuenteComercial("Tahoma", "DejaVu Sans", False),
    "segoeui": FuenteComercial("Segoe UI", "Open Sans", False),
    "georgia": FuenteComercial("Georgia", "Gelasio", True),
    "garamond": FuenteComercial("Garamond", "EB Garamond", False),
    "bookantiqua": FuenteComercial("Book Antiqua", "TeX Gyre Pagella", False),
    "palatinolinotype": FuenteComercial("Palatino Linotype", "TeX Gyre Pagella", False),
    "centurygothic": FuenteComercial("Century Gothic", "URW Gothic", False),
    "trebuchetms": FuenteComercial("Trebuchet MS", "Fira Sans", False),
    "frutiger": FuenteComercial("Frutiger", "Liberation Sans", False),
    "futura": FuenteComercial("Futura", "Jost", False),
    "myriadpro": FuenteComercial("Myriad Pro", "PT Sans", False),
    "minionpro": FuenteComercial("Minion Pro", "Crimson Pro", False),
}


@dataclass(frozen=True)
class FuenteLibre:
    nombre: str
    licencia: str
    comando: str              # PowerShell listo para copiar


@lru_cache(maxsize=1)
def _google_fonts() -> dict[str, str]:
    """{carpeta: licencia} de github.com/google/fonts."""
    try:
        return json.loads(_DATOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_REGISTRO = r"HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"


def _comando_desde_lista(titulo: str, obtener_archivos: str) -> str:
    """Arma el script PowerShell. `obtener_archivos` debe dejar en $archivos una
    lista de objetos con .name y .download_url."""
    return "\n".join([
        f"# Instala {titulo} para tu usuario (no requiere administrador)",
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'",
        '$destino = "$env:LOCALAPPDATA\\Microsoft\\Windows\\Fonts"',
        "New-Item -ItemType Directory -Force -Path $destino | Out-Null",
        obtener_archivos,
        "foreach ($a in $archivos) {",
        # Saca '[wght]' de los nombres de fuentes variables (los corchetes rompen -OutFile)
        "  $nombre = $a.name -replace '\\[[^\\]]*\\]', ''",
        "  $ruta = Join-Path $destino $nombre",
        "  Invoke-WebRequest -Uri $a.download_url -OutFile $ruta",
        f"  New-ItemProperty -Path '{_REGISTRO}' -Name \"$([IO.Path]::GetFileNameWithoutExtension($nombre)) (TrueType)\" "
        "-Value $ruta -PropertyType String -Force | Out-Null",
        "}",
        f"Write-Host 'Listo: {titulo} quedó instalada. Reiniciá PDF Studio para usarla.'",
    ])


def comando_google_fonts(carpeta: str, licencia: str, titulo: str) -> str:
    api = f"https://api.github.com/repos/google/fonts/contents/{licencia}/{carpeta}"
    # Entre paréntesis: PowerShell 5.1 no enumera el arreglo JSON en el pipeline
    obtener = (f"$archivos = (Invoke-RestMethod -Uri '{api}') | "
               "Where-Object { $_.name -like '*.ttf' -or $_.name -like '*.otf' }")
    return _comando_desde_lista(titulo, obtener)


def comando_liberation(familia: str) -> str:
    """familia: 'Sans', 'Serif' o 'Mono'."""
    titulo = f"Liberation {familia}"
    obtener = "\n".join([
        '$tmp = Join-Path $env:TEMP "pdfstudio_liberation"',
        "New-Item -ItemType Directory -Force -Path $tmp | Out-Null",
        f"Invoke-WebRequest -Uri '{URL_LIBERATION}' -OutFile \"$tmp\\liberation.tar.gz\"",
        'tar -xzf "$tmp\\liberation.tar.gz" -C $tmp',
        f"$archivos = Get-ChildItem \"$tmp\\liberation-fonts-ttf-*\\Liberation{familia}-*.ttf\" | "
        "ForEach-Object { [pscustomobject]@{ name = $_.Name; download_url = $_.FullName } }",
    ])
    # Para archivos locales, Invoke-WebRequest no sirve: se reemplaza por Copy-Item
    return _comando_desde_lista(titulo, obtener).replace(
        "Invoke-WebRequest -Uri $a.download_url -OutFile $ruta",
        "Copy-Item -LiteralPath $a.download_url -Destination $ruta -Force")


_LIBERATION = {"liberationsans": "Sans", "liberationserif": "Serif", "liberationmono": "Mono"}


def buscar_libre(nombre_fuente: str) -> FuenteLibre | None:
    """Si la fuente es libre y sabemos de dónde bajarla, devuelve el comando."""
    familia = familia_de(nombre_fuente)
    if familia in _LIBERATION:
        return FuenteLibre(f"Liberation {_LIBERATION[familia]}", "SIL OFL 1.1",
                           comando_liberation(_LIBERATION[familia]))
    gf = _google_fonts()
    if familia in gf:
        licencia = gf[familia]
        nombre_lic = {"ofl": "SIL OFL 1.1", "apache": "Apache 2.0", "ufl": "Ubuntu Font Licence"}[licencia]
        return FuenteLibre(nombre_fuente, nombre_lic, comando_google_fonts(familia, licencia, familia_de_visible(nombre_fuente)))
    return None


def familia_de_visible(nombre_fuente: str) -> str:
    """Nombre legible de la familia para mensajes: 'ABCDEF+OpenSans-Bold' -> 'OpenSans'."""
    base = sin_prefijo(nombre_fuente)
    for sep in (",", "-"):
        if sep in base:
            return base.split(sep, 1)[0]
    return base


def buscar_comercial(nombre_fuente: str) -> FuenteComercial | None:
    return COMERCIALES.get(familia_de(nombre_fuente))
