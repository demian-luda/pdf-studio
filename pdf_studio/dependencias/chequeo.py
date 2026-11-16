"""Detector de dependencias: qué falta, para qué sirve y cómo instalarlo.

Se ejecuta al iniciar la app (paquetes y programas) y al abrir cada PDF (fuentes).
Cada faltante trae el comando de PowerShell listo para copiar y, cuando se puede,
una instalación asistida que la app ejecuta SOLO tras la confirmación del usuario.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from pdf_studio.core.fuentes import Embebido, IndiceFuentesSistema, InfoFuente, analizar_fuentes
from pdf_studio.dependencias import instalador, programas
from pdf_studio.dependencias.catalogo_fuentes import buscar_comercial, buscar_libre
from pdf_studio.dependencias.instalador import Instalacion

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
    PaquetePython("spylls", "spylls", "marcar palabras mal reconocidas al convertir escaneados (corrector ortográfico)",
                  Importancia.OPCIONAL, "MPL-2.0"),
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


# ---------------------------------------------------------------------- Tesseract y Ghostscript
# Vienen incluidos con la app (ver dependencias/programas.py): no se buscan en el sistema.
def buscar_tesseract() -> Path | None:
    return programas.ejecutable(programas.TESSERACT)


def buscar_ghostscript() -> Path | None:
    return programas.ejecutable(programas.GHOSTSCRIPT)


def _faltante_programa(programa: programas.Programa, importancia: Importancia) -> Faltante:
    if getattr(sys, "frozen", False):
        detalle = (f"Viene incluido con PDF Studio, pero no está en la carpeta de la app "
                   f"(¿se borró «{programa.clave}»?). «Instalar» lo baja de nuevo, o reinstalá PDF Studio.")
    else:
        detalle = ("Desde el código no viene armado: «Instalar» lo baja (versión fija, verificada con SHA-256) "
                   "sin administrador.")
    return Faltante(f"programa:{programa.clave}", programa.titulo, programa.para_que, importancia,
                    programas.comando_preparar(), detalle, programas.instalacion(programa))


def chequear_tesseract() -> list[Faltante]:
    if buscar_tesseract() is not None:
        return []
    return [_faltante_programa(programas.TESSERACT, Importancia.RECOMENDADO)]


def chequear_ghostscript() -> list[Faltante]:
    if buscar_ghostscript() is not None:
        return []
    return [_faltante_programa(programas.GHOSTSCRIPT, Importancia.OPCIONAL)]


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
