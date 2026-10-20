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
    """Fuente libre que se puede descargar. La app la puede instalar sola (con
    confirmación) o mostrar el comando de PowerShell equivalente."""
    nombre: str
    licencia: str
    tipo: str                         # 'google' (carpeta de google/fonts) o 'archivo' (zip/tar oficial)
    url: str                          # API de la carpeta, o URL del archivo comprimido
    patrones: tuple[str, ...] = ()    # para 'archivo': qué fuentes extraer
    tamano: int | None = None         # bytes a descargar, si se conocen de antemano

    @property
    def origen(self) -> str:
        if self.tipo == "google":
            return "Google Fonts (github.com/google/fonts)"
        return self.url

    @property
    def comando(self) -> str:
        if self.tipo == "google":
            return comando_google_fonts(self.url, self.nombre)
        return comando_archivo(self.url, self.patrones, self.nombre)


@lru_cache(maxsize=1)
def _google_fonts() -> dict[str, str]:
    """{carpeta: licencia} de github.com/google/fonts."""
    try:
        return json.loads(_DATOS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


_REGISTRO = r"HKCU:\Software\Microsoft\Windows NT\CurrentVersion\Fonts"
LICENCIAS_GOOGLE = {"ofl": "SIL OFL 1.1", "apache": "Apache 2.0", "ufl": "Ubuntu Font Licence"}


def _comando_desde_lista(titulo: str, obtener_archivos: str, copiar_local: bool = False) -> str:
    """Arma el script PowerShell. `obtener_archivos` debe dejar en $archivos una
    lista de objetos con .name y .download_url (URL, o ruta local si copiar_local)."""
    traer = ("  Copy-Item -LiteralPath $a.download_url -Destination $ruta -Force" if copiar_local
             else "  Invoke-WebRequest -Uri $a.download_url -OutFile $ruta")
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
        traer,
        f"  New-ItemProperty -Path '{_REGISTRO}' -Name \"$([IO.Path]::GetFileNameWithoutExtension($nombre)) (TrueType)\" "
        "-Value $ruta -PropertyType String -Force | Out-Null",
        "}",
        f"Write-Host 'Listo: {titulo} quedó instalada. Reiniciá PDF Studio para usarla.'",
    ])


def api_google(carpeta: str, licencia: str) -> str:
    return f"https://api.github.com/repos/google/fonts/contents/{licencia}/{carpeta}"


def comando_google_fonts(api: str, titulo: str) -> str:
    # Entre paréntesis: PowerShell 5.1 no enumera el arreglo JSON en el pipeline
    obtener = (f"$archivos = (Invoke-RestMethod -Uri '{api}') | "
               "Where-Object { $_.name -like '*.ttf' -or $_.name -like '*.otf' }")
    return _comando_desde_lista(titulo, obtener)


def comando_archivo(url: str, patrones: tuple[str, ...], titulo: str) -> str:
    """Descarga un zip/tar oficial y copia solo las fuentes que coinciden con los patrones."""
    nombre_archivo = url.rsplit("/", 1)[-1]
    incluir = ", ".join(f"'{p}'" for p in patrones)
    obtener = "\n".join([
        '$tmp = Join-Path $env:TEMP ("pdfstudio_" + [guid]::NewGuid().ToString("N"))',
        "New-Item -ItemType Directory -Force -Path $tmp | Out-Null",
        f"Invoke-WebRequest -Uri '{url}' -OutFile \"$tmp\\{nombre_archivo}\"",
        # tar.exe de Windows: otro tar en el PATH (como el de Git) confunde «C:» con un servidor
        f'& "$env:SystemRoot\\System32\\tar.exe" -xf "$tmp\\{nombre_archivo}" -C $tmp',
        f"$archivos = Get-ChildItem -Path $tmp -Recurse -Include {incluir} | "
        "ForEach-Object { [pscustomobject]@{ name = $_.Name; download_url = $_.FullName } }",
    ])
    return _comando_desde_lista(titulo, obtener, copiar_local=True)


def comando_liberation(familia: str) -> str:
    """familia: 'Sans', 'Serif' o 'Mono'."""
    return ARCHIVOS[f"liberation{familia.lower()}"].comando


URL_DEJAVU = "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.zip"

# Fuentes libres que no están en Google Fonts: se bajan del archivo oficial del proyecto
ARCHIVOS: dict[str, FuenteLibre] = {
    "liberationsans": FuenteLibre("Liberation Sans", "SIL OFL 1.1", "archivo", URL_LIBERATION,
                                  ("LiberationSans-*.ttf",)),
    "liberationserif": FuenteLibre("Liberation Serif", "SIL OFL 1.1", "archivo", URL_LIBERATION,
                                   ("LiberationSerif-*.ttf",)),
    "liberationmono": FuenteLibre("Liberation Mono", "SIL OFL 1.1", "archivo", URL_LIBERATION,
                                  ("LiberationMono-*.ttf",)),
    "dejavusans": FuenteLibre("DejaVu Sans", "Bitstream Vera / dominio público", "archivo", URL_DEJAVU,
                              ("DejaVuSans.ttf", "DejaVuSans-*.ttf"), 5522795),
    "dejavuserif": FuenteLibre("DejaVu Serif", "Bitstream Vera / dominio público", "archivo", URL_DEJAVU,
                               ("DejaVuSerif.ttf", "DejaVuSerif-*.ttf"), 5522795),
    "dejavusansmono": FuenteLibre("DejaVu Sans Mono", "Bitstream Vera / dominio público", "archivo", URL_DEJAVU,
                                  ("DejaVuSansMono.ttf", "DejaVuSansMono-*.ttf"), 5522795),
}


def buscar_libre(nombre_fuente: str) -> FuenteLibre | None:
    """Si la fuente es libre y sabemos de dónde bajarla, cómo hacerlo."""
    familia = familia_de(nombre_fuente)
    if familia in ARCHIVOS:
        return ARCHIVOS[familia]
    gf = _google_fonts()
    if familia in gf:
        licencia = gf[familia]
        return FuenteLibre(familia_de_visible(nombre_fuente), LICENCIAS_GOOGLE[licencia], "google",
                           api_google(familia, licencia))
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