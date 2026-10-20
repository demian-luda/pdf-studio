"""Instalación asistida de dependencias desde la app.

Regla: NUNCA se instala nada sin confirmación explícita del usuario. La interfaz
muestra antes nombre, origen de la descarga, licencia, tamaño y dónde se instala.

- Fuentes libres: solo para el usuario actual (sin administrador), en
  %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts + registro en HKCU. Se avisa a Windows
  (AddFontResource + WM_FONTCHANGE), así quedan disponibles sin reiniciar.
- Idiomas de OCR: a la carpeta de datos de la app, sin administrador.
- Tesseract (winget) y Ghostscript (instalador oficial): Windows muestra su propio
  aviso de permisos de administrador.
- Paquetes de Python: con el pip del .venv.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pdf_studio import __version__, config

# progreso(hecho, total, mensaje): total = 0 si no se sabe (barra indeterminada)
Progreso = Callable[[int, int, str], None]
AGENTE = f"PDFStudio/{__version__}"
SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ErrorInstalacion(Exception):
    """Error con mensaje listo para mostrar."""


def _sin_progreso(_h: int, _t: int, _m: str) -> None:
    pass


# ---------------------------------------------------------------------- descargas
def _abrir(url: str, metodo: str = "GET"):
    pedido = urllib.request.Request(url, method=metodo, headers={"User-Agent": AGENTE})
    return urllib.request.urlopen(pedido, timeout=60)


def leer_json(url: str):
    with _abrir(url) as r:
        return json.load(r)


def tamano_remoto(url: str) -> int | None:
    try:
        with _abrir(url, "HEAD") as r:
            largo = r.headers.get("Content-Length")
            return int(largo) if largo else None
    except Exception:
        return None


def descargar(url: str, destino: Path, progreso: Progreso = _sin_progreso, mensaje: str = "Descargando") -> Path:
    try:
        with _abrir(url) as r, open(destino, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            hecho = 0
            while True:
                bloque = r.read(1 << 16)
                if not bloque:
                    break
                f.write(bloque)
                hecho += len(bloque)
                progreso(hecho, total, mensaje)
    except OSError as e:
        destino.unlink(missing_ok=True)
        raise ErrorInstalacion(f"No se pudo descargar {url}: {e}") from e
    return destino


def formato_tamano(n: int | None) -> str:
    if n is None:
        return "desconocido"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


# ---------------------------------------------------------------------- modelo
@dataclass
class Instalacion:
    clave: str
    titulo: str
    origen: str
    licencia: str
    destino: str                       # dónde queda instalado (texto para el usuario)
    requiere_admin: bool
    comando: str                       # equivalente en PowerShell, para copiar
    calcular_tamano: Callable[[], int | None] = field(repr=False, default=lambda: None)
    accion: Callable[[Progreso], str] = field(repr=False, default=lambda p: "")
    _tamano: int | None = field(default=None, repr=False)
    _tamano_calculado: bool = field(default=False, repr=False)

    def tamano(self) -> int | None:
        if not self._tamano_calculado:
            try:
                self._tamano = self.calcular_tamano()
            except Exception:
                self._tamano = None
            self._tamano_calculado = True
        return self._tamano

    def ejecutar(self, progreso: Progreso = _sin_progreso) -> str:
        """Instala y devuelve un mensaje de éxito. Lanza ErrorInstalacion si falla."""
        return self.accion(progreso)


# ---------------------------------------------------------------------- fuentes
def _nombre_limpio(nombre: str) -> str:
    import re
    return re.sub(r"\[[^\]]*\]", "", Path(nombre).name)


def archivos_google(api: str) -> list[tuple[str, str, int | None]]:
    """(nombre, url, tamaño) de las fuentes de una carpeta de google/fonts."""
    try:
        datos = leer_json(api)
    except Exception as e:
        raise ErrorInstalacion(f"No se pudo consultar Google Fonts: {e}") from e
    return [(d["name"], d["download_url"], d.get("size")) for d in datos
            if d.get("name", "").lower().endswith((".ttf", ".otf"))]


def tamano_fuente(fuente) -> int | None:
    if fuente.tipo == "google":
        archivos = archivos_google(fuente.url)
        return sum(t or 0 for _n, _u, t in archivos) or None
    return fuente.tamano or tamano_remoto(fuente.url)


def _extraer(archivo: Path, patrones: tuple[str, ...], destino: Path) -> list[Path]:
    """Extrae del zip/tar solo las fuentes que coinciden; nunca escribe fuera de `destino`."""
    def sirve(nombre: str) -> bool:
        base = Path(nombre).name
        return base.lower().endswith((".ttf", ".otf")) and any(fnmatch.fnmatch(base, p) for p in patrones)

    escritos = []
    if zipfile.is_zipfile(archivo):
        with zipfile.ZipFile(archivo) as z:
            for info in z.infolist():
                if not info.is_dir() and sirve(info.filename):
                    ruta = destino / _nombre_limpio(info.filename)
                    ruta.write_bytes(z.read(info))
                    escritos.append(ruta)
    else:
        with tarfile.open(archivo, "r:*") as t:
            for m in t.getmembers():
                if m.isfile() and sirve(m.name):
                    ruta = destino / _nombre_limpio(m.name)
                    ruta.write_bytes(t.extractfile(m).read())
                    escritos.append(ruta)
    return escritos


def instalar_fuente(fuente, progreso: Progreso = _sin_progreso, destino: Path | None = None,
                    registrar: bool = True) -> list[Path]:
    """Descarga e instala una FuenteLibre para el usuario actual. Devuelve los archivos."""
    destino = Path(destino) if destino is not None else config.DIR_FUENTES_USUARIO
    destino.mkdir(parents=True, exist_ok=True)
    instalados: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="pdfstudio_") as tmp:
        tmp = Path(tmp)
        if fuente.tipo == "google":
            archivos = archivos_google(fuente.url)
            if not archivos:
                raise ErrorInstalacion(f"No se encontraron archivos de «{fuente.nombre}».")
            total = sum(t or 0 for _n, _u, t in archivos)
            acumulado = 0
            for nombre, url, tam in archivos:
                temporal = tmp / _nombre_limpio(nombre)
                descargar(url, temporal, lambda h, _t, m, base=acumulado: progreso(base + h, total, m),
                          f"Descargando {nombre}")
                acumulado += tam or temporal.stat().st_size
                final = destino / temporal.name
                shutil.copy2(temporal, final)
                instalados.append(final)
        else:
            archivo = tmp / fuente.url.rsplit("/", 1)[-1]
            descargar(fuente.url, archivo, progreso, f"Descargando {fuente.nombre}")
            progreso(0, 0, "Extrayendo fuentes…")
            extraidos = _extraer(archivo, fuente.patrones, tmp)
            if not extraidos:
                raise ErrorInstalacion(f"El archivo descargado no contiene «{fuente.nombre}».")
            for e in extraidos:
                final = destino / e.name
                shutil.copy2(e, final)
                instalados.append(final)
    if registrar:
        for ruta in instalados:
            registrar_fuente(ruta)
        avisar_cambio_de_fuentes()
    return instalados


def registrar_fuente(ruta: Path) -> None:
    """Registra la fuente para el usuario (HKCU) y la carga en la sesión actual."""
    import winreg
    clave = r"Software\Microsoft\Windows NT\CurrentVersion\Fonts"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, clave, 0, winreg.KEY_SET_VALUE) as k:
        tipo = "OpenType" if ruta.suffix.lower() == ".otf" else "TrueType"
        winreg.SetValueEx(k, f"{ruta.stem} ({tipo})", 0, winreg.REG_SZ, str(ruta))
    try:
        import ctypes
        ctypes.windll.gdi32.AddFontResourceW(str(ruta))
    except (AttributeError, OSError):
        pass


def avisar_cambio_de_fuentes() -> None:
    """Avisa a las aplicaciones abiertas que cambió la lista de fuentes (WM_FONTCHANGE)."""
    try:
        import ctypes
        resultado = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001D, 0, 0, 0x0002, 1000, ctypes.byref(resultado))
    except (AttributeError, OSError):
        pass


def instalacion_fuente(fuente) -> Instalacion:
    def accion(progreso: Progreso) -> str:
        archivos = instalar_fuente(fuente, progreso)
        from pdf_studio.core.fuentes import indice_sistema
        indice_sistema().cargar()
        return f"«{fuente.nombre}» quedó instalada ({len(archivos)} archivo(s)). Ya se puede usar."

    return Instalacion(f"fuente:{fuente.nombre}", f"Fuente «{fuente.nombre}»", fuente.origen, fuente.licencia,
                       f"Solo para tu usuario, sin permisos de administrador ({config.DIR_FUENTES_USUARIO})",
                       False, fuente.comando, lambda: tamano_fuente(fuente), accion)


# ---------------------------------------------------------------------- Tesseract e idiomas
API_TESSERACT = "https://api.github.com/repos/tesseract-ocr/tesseract/releases/latest"


def _tamano_asset(api: str, patron: str) -> int | None:
    datos = leer_json(api)
    for a in datos.get("assets", []):
        if fnmatch.fnmatch(a["name"], patron):
            return a.get("size")
    return None


def instalacion_tesseract(id_winget: str, comando: str) -> Instalacion | None:
    if shutil.which("winget") is None:
        return None

    def accion(progreso: Progreso) -> str:
        progreso(0, 0, "Instalando Tesseract con winget (Windows puede pedir permiso)…")
        r = subprocess.run(["winget", "install", "--id", id_winget, "-e", "--accept-source-agreements",
                            "--accept-package-agreements"], capture_output=True, text=True,
                           creationflags=SIN_VENTANA)
        if r.returncode != 0:
            raise ErrorInstalacion("winget no pudo instalar Tesseract:\n" + (r.stdout + r.stderr)[-800:])
        return "Tesseract quedó instalado."

    return Instalacion("programa:tesseract", "Tesseract OCR", f"winget ({id_winget}), desde github.com/tesseract-ocr",
                       "Apache 2.0", "Para todo el equipo (Archivos de programa)", True, comando,
                       lambda: _tamano_asset(API_TESSERACT, "*w64-setup*.exe"), accion)


def instalacion_idiomas(url_plantilla: str, idiomas: tuple[str, ...], comando: str) -> Instalacion:
    destino = config.DIR_TESSDATA_USUARIO
    urls = [url_plantilla.format(idioma=i) for i in idiomas]

    def tamano() -> int | None:
        tamanos = [tamano_remoto(u) for u in urls]
        return sum(tamanos) if all(t is not None for t in tamanos) else None

    def accion(progreso: Progreso) -> str:
        destino.mkdir(parents=True, exist_ok=True)
        for i, url in zip(idiomas, urls):
            temporal = destino / f"{i}.traineddata.descarga"
            descargar(url, temporal, progreso, f"Descargando idioma «{i}»")
            os.replace(temporal, destino / f"{i}.traineddata")
        return "Los idiomas de OCR quedaron instalados."

    return Instalacion("idioma:ocr", "Idiomas de OCR (" + ", ".join(idiomas) + ")", "github.com/tesseract-ocr/tessdata",
                       "Apache 2.0", f"Solo para PDF Studio, sin administrador ({destino})", False, comando,
                       tamano, accion)


# ---------------------------------------------------------------------- Ghostscript
def instalacion_ghostscript(api: str, comando: str) -> Instalacion:
    def accion(progreso: Progreso) -> str:
        datos = leer_json(api)
        asset = next((a for a in datos.get("assets", []) if fnmatch.fnmatch(a["name"], "gs*w64.exe")), None)
        if asset is None:
            raise ErrorInstalacion("No se encontró el instalador de Ghostscript para Windows de 64 bits.")
        with tempfile.TemporaryDirectory(prefix="pdfstudio_") as tmp:
            exe = Path(tmp) / asset["name"]
            descargar(asset["browser_download_url"], exe, progreso, "Descargando Ghostscript")
            progreso(0, 0, "Instalando Ghostscript (Windows va a pedir permiso de administrador)…")
            # -Verb RunAs: aparece el aviso de Windows para permisos de administrador
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Start-Process -FilePath '{exe}' -Verb RunAs -Wait"],
                               capture_output=True, text=True, creationflags=SIN_VENTANA)
            if r.returncode != 0:
                raise ErrorInstalacion("No se completó la instalación de Ghostscript:\n" + r.stderr[-600:])
        return "Ghostscript quedó instalado."

    return Instalacion("programa:ghostscript", "Ghostscript", "github.com/ArtifexSoftware/ghostpdl-downloads",
                       "AGPL-3.0", "Para todo el equipo (Archivos de programa)", True, comando,
                       lambda: _tamano_asset(api, "gs*w64.exe"), accion)


# ---------------------------------------------------------------------- Python
def instalacion_pip(paquete: str, licencia: str, comando: str) -> Instalacion | None:
    if getattr(sys, "frozen", False):
        return None

    def accion(progreso: Progreso) -> str:
        progreso(0, 0, f"Instalando {paquete} con pip…")
        r = subprocess.run([sys.executable, "-m", "pip", "install", paquete], capture_output=True, text=True,
                           creationflags=SIN_VENTANA)
        if r.returncode != 0:
            raise ErrorInstalacion(f"pip no pudo instalar {paquete}:\n" + r.stderr[-800:])
        return f"«{paquete}» quedó instalado."

    return Instalacion(f"python:{paquete}", f"Paquete de Python «{paquete}»", f"PyPI (pypi.org/project/{paquete})",
                       licencia, f"En el entorno virtual del proyecto ({Path(sys.prefix)})", False, comando,
                       lambda: None, accion)
