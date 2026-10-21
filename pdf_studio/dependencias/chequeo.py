"""Detector de dependencias: qué falta, para qué sirve y cómo instalarlo.

Se ejecuta al iniciar la app (paquetes y programas) y al abrir cada PDF (fuentes).
Cada faltante trae el comando de PowerShell listo para copiar y, cuando se puede,
una instalación asistida que la app ejecuta SOLO tras la confirmación del usuario.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from pdf_studio import config
from pdf_studio.core.fuentes import Embebido, IndiceFuentesSistema, InfoFuente, analizar_fuentes
from pdf_studio.dependencias import instalador
from pdf_studio.dependencias.catalogo_fuentes import buscar_comercial, buscar_libre
from pdf_studio.dependencias.instalador import Instalacion

# IDs verificados con `winget search` / GitHub (octubre 2026)
WINGET_TESSERACT = "tesseract-ocr.tesseract"
URL_TESSDATA = "https://github.com/tesseract-ocr/tessdata/raw/main/{idioma}.traineddata"
API_GHOSTSCRIPT = "https://api.github.com/repos/ArtifexSoftware/ghostpdl-downloads/releases/latest"
IDIOMAS_OCR = ("spa", "eng")


class Importancia(str, Enum):
    NECESARIO = "necesario"      # la app no funciona bien sin esto
    RECOMENDADO = "recomendado"  # una función importante queda limitada
    OPCIONAL = "opcional"        # solo para una función puntual

    @property
    def texto(self) -> str:
        return {"necesario": "Necesario", "recomendado": "Recomendado", "opcional": "Opcional"}[self.value]


@dataclass
class Faltante:
    clave: str
    titulo: str
    para_que: str
    importancia: Importancia
    comando: str | None = None   # PowerShell listo para copiar; None si no hay instalación posible
    detalle: str = ""
    instalacion: Instalacion | None = None   # instalación asistida (con confirmación)


# ---------------------------------------------------------------------- Python
@dataclass(frozen=True)
class PaquetePython:
    modulo: str
    paquete: str
    para_que: str
    importancia: Importancia
    licencia: str = ""


PAQUETES = [
    PaquetePython("PySide6", "PySide6", "la interfaz gráfica", Importancia.NECESARIO, "LGPL-3.0"),
    PaquetePython("pymupdf", "pymupdf", "abrir, mostrar y editar PDFs", Importancia.NECESARIO, "AGPL-3.0"),
    PaquetePython("numpy", "numpy", "la verificación de fidelidad visual", Importancia.NECESARIO, "BSD"),
    PaquetePython("fontTools", "fonttools", "analizar e instalar fuentes", Importancia.NECESARIO, "MIT"),
    PaquetePython("pikepdf", "pikepdf", "verificar la estructura de los PDFs guardados", Importancia.RECOMENDADO,
                  "MPL-2.0"),
    PaquetePython("PIL", "Pillow", "las firmas en imagen (quitar fondo)", Importancia.NECESARIO, "MIT-CMU"),
    PaquetePython("ocrmypdf", "ocrmypdf", "el OCR (reconocer texto en PDFs escaneados)", Importancia.OPCIONAL,
                  "MPL-2.0"),
    PaquetePython("pyhanko", "pyHanko", "la firma digital con certificado", Importancia.OPCIONAL, "MIT"),
    PaquetePython("cv2", "opencv-python-headless", "importar fotos como PDF escaneado (recorte y perspectiva)",
                  Importancia.OPCIONAL, "Apache-2.0"),
    PaquetePython("docx", "python-docx", "convertir escaneados a documentos de Word editables (.docx)",
                  Importancia.OPCIONAL, "MIT"),
]


def comando_pip(paquete: str) -> str:
    if getattr(sys, "frozen", False):
        # En el .exe empaquetado no hay pip: hay que usar una versión que lo incluya
        return f"# Esta versión empaquetada no incluye {paquete}. Descargá la versión completa de PDF Studio."
    return f'& "{sys.executable}" -m pip install {paquete}'


def modulo_disponible(modulo: str) -> bool:
    try:
        return importlib.util.find_spec(modulo) is not None
    except (ImportError, ValueError):
        return False


def chequear_python(paquetes: list[PaquetePython] = PAQUETES) -> list[Faltante]:
    faltan = []
    for p in paquetes:
        if not modulo_disponible(p.modulo):
            faltan.append(Faltante(
                clave=f"python:{p.paquete}",
                titulo=f"Paquete de Python «{p.paquete}»",
                para_que=f"Se usa para {p.para_que}.",
                importancia=p.importancia,
                comando=comando_pip(p.paquete),
                instalacion=instalador.instalacion_pip(p.paquete, p.licencia, comando_pip(p.paquete)),
            ))
    return faltan


# ---------------------------------------------------------------------- Tesseract
def buscar_tesseract() -> Path | None:
    encontrado = shutil.which("tesseract")
    if encontrado:
        return Path(encontrado)
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        if base:
            candidato = Path(base) / "Tesseract-OCR" / "tesseract.exe"
            if candidato.is_file():
                return candidato
            candidato = Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe"
            if candidato.is_file():
                return candidato
    return None


def idiomas_tesseract(exe: Path, tessdata: Path | None = None) -> set[str]:
    entorno = dict(os.environ)
    if tessdata is not None:
        entorno["TESSDATA_PREFIX"] = str(tessdata)
    try:
        r = subprocess.run([str(exe), "--list-langs"], capture_output=True, text=True,
                           timeout=20, env=entorno,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return set()
    lineas = (r.stdout + "\n" + r.stderr).splitlines()
    return {l.strip() for l in lineas if l.strip() and " " not in l.strip() and not l.endswith(":")}


def tessdata_a_usar(exe: Path) -> Path | None:
    """La carpeta de idiomas del usuario tiene prioridad si tiene español."""
    if (config.DIR_TESSDATA_USUARIO / "spa.traineddata").is_file():
        return config.DIR_TESSDATA_USUARIO
    return None


def comando_idiomas_ocr() -> str:
    destino = config.DIR_TESSDATA_USUARIO
    return "\n".join([
        "# Descarga los idiomas español e inglés para el OCR (no requiere administrador)",
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'",
        f'$td = "{destino}"',
        "New-Item -ItemType Directory -Force -Path $td | Out-Null",
        "foreach ($l in 'spa', 'eng', 'osd') {",
        f"  Invoke-WebRequest -Uri \"{URL_TESSDATA.format(idioma='$l')}\" -OutFile \"$td\\$l.traineddata\"",
        "}",
        "Write-Host 'Listo: idiomas instalados.'",
    ])


def comando_tesseract() -> str:
    return (f"winget install --id {WINGET_TESSERACT} -e "
            "--accept-source-agreements --accept-package-agreements")


def chequear_tesseract() -> list[Faltante]:
    exe = buscar_tesseract()
    idiomas_inst = instalador.instalacion_idiomas(URL_TESSDATA, ("spa", "eng", "osd"), comando_idiomas_ocr())
    if exe is None:
        return [
            Faltante("programa:tesseract", "Tesseract OCR",
                     "Motor de OCR: reconoce el texto de PDFs escaneados.",
                     Importancia.OPCIONAL, comando_tesseract(),
                     "Después de instalarlo, instalá también los idiomas (ítem siguiente).",
                     instalador.instalacion_tesseract(WINGET_TESSERACT, comando_tesseract())),
            Faltante("idioma:ocr", "Idiomas español e inglés para OCR",
                     "Tesseract necesita estos archivos para reconocer texto en español.",
                     Importancia.OPCIONAL, comando_idiomas_ocr(), instalacion=idiomas_inst),
        ]
    idiomas = idiomas_tesseract(exe, tessdata_a_usar(exe))
    faltan = [i for i in IDIOMAS_OCR if i not in idiomas]
    if faltan:
        return [Faltante("idioma:ocr", "Idiomas para OCR: " + ", ".join(faltan),
                         "Tesseract está instalado pero le faltan idiomas para reconocer texto.",
                         Importancia.OPCIONAL, comando_idiomas_ocr(), instalacion=idiomas_inst)]
    return []


# ---------------------------------------------------------------------- Ghostscript
def buscar_ghostscript() -> Path | None:
    for nombre in ("gswin64c", "gswin32c", "gs"):
        encontrado = shutil.which(nombre)
        if encontrado:
            return Path(encontrado)
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidatos = sorted(glob.glob(str(Path(base) / "gs" / "gs*" / "bin" / "gswin*c.exe")))
        if candidatos:
            return Path(candidatos[-1])
    return None


def comando_ghostscript() -> str:
    # Ghostscript no está en winget: se baja el instalador oficial de la última versión
    return "\n".join([
        "# Descarga e instala Ghostscript (versión oficial más reciente de Artifex).",
        "# Windows va a pedir permiso de administrador durante la instalación.",
        "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'",
        f"$r = Invoke-RestMethod -Uri '{API_GHOSTSCRIPT}'",
        "$a = $r.assets | Where-Object { $_.name -like 'gs*w64.exe' } | Select-Object -First 1",
        '$exe = Join-Path $env:TEMP $a.name',
        "Invoke-WebRequest -Uri $a.browser_download_url -OutFile $exe",
        "Start-Process -FilePath $exe -Wait",
    ])


def chequear_ghostscript() -> list[Faltante]:
    if buscar_ghostscript() is not None:
        return []
    return [Faltante("programa:ghostscript", "Ghostscript",
                     "Permite la compresión agresiva de PDFs (opcional; sin él se usa la compresión normal).",
                     Importancia.OPCIONAL, comando_ghostscript(),
                     instalacion=instalador.instalacion_ghostscript(API_GHOSTSCRIPT, comando_ghostscript()))]


# ---------------------------------------------------------------------- fuentes
def faltante_de_fuente(f: InfoFuente) -> Faltante | None:
    """Decide si una fuente del PDF necesita atención y qué se puede hacer."""
    if f.embebido in (Embebido.TYPE3, Embebido.COMPLETA) or f.es_estandar or f.instalada or f.incluida:
        return None
    if f.embebido == Embebido.SUBCONJUNTO:
        problema = ("Está embebida como subconjunto: el PDF solo trae las letras que usa. "
                    "Para escribir texto nuevo con esta fuente hace falta la fuente completa.")
        importancia = Importancia.OPCIONAL
    elif f.embebido == Embebido.NO_ANALIZADA:
        problema = ("Está embebida, pero no se pudo analizar qué letras trae. El documento se ve bien; "
                    "para escribir texto nuevo conviene tener la fuente completa.")
        importancia = Importancia.OPCIONAL
    else:
        problema = ("No está embebida ni instalada: el PDF se muestra con una fuente de reemplazo "
                    "y puede verse distinto al original.")
        importancia = Importancia.RECOMENDADO
    libre = buscar_libre(f.nombre)
    if libre is not None:
        return Faltante(f"fuente:{f.familia}", f"Fuente «{f.nombre}»",
                        problema, importancia, libre.comando,
                        f"Es una fuente libre ({libre.licencia}): se puede instalar gratis.",
                        instalador.instalacion_fuente(libre))
    comercial = buscar_comercial(f.nombre)
    if comercial is not None:
        compat = "con el mismo ancho por letra" if comercial.metrica_compatible else "parecida"
        detalle = (f"«{comercial.nombre}» es una fuente comercial: no se puede descargar gratis. "
                   f"Al editar se puede sustituir por «{comercial.alternativa}» ({compat}).")
    else:
        detalle = ("Fuente comercial o desconocida: no se ofrece descarga. "
                   "Al editar se puede sustituir por una fuente libre parecida.")
    return Faltante(f"fuente:{f.familia}", f"Fuente «{f.nombre}»", problema, importancia, None, detalle)


def chequear_fuentes(doc: pymupdf.Document, indice: IndiceFuentesSistema | None,
                     incluidas: IndiceFuentesSistema | None = None,
                     ) -> tuple[list[InfoFuente], list[Faltante]]:
    fuentes = analizar_fuentes(doc, indice, incluidas)
    return fuentes, faltantes_de_fuentes(fuentes)


def faltantes_de_fuentes(fuentes: list[InfoFuente]) -> list[Faltante]:
    faltantes: dict[str, Faltante] = {}
    for f in fuentes:
        try:
            falta = faltante_de_fuente(f)
        except Exception:
            falta = None      # una fuente rara nunca frena el resto del informe
        if falta is not None and falta.clave not in faltantes:
            faltantes[falta.clave] = falta
    return list(faltantes.values())


# ---------------------------------------------------------------------- general
def chequear_inicio() -> list[Faltante]:
    """Chequeo completo de paquetes y programas al iniciar la app."""
    return chequear_python() + chequear_tesseract() + chequear_ghostscript()
