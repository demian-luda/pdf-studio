"""OCR: reconocer el texto de páginas escaneadas.

Modo "Idéntico" (por defecto): ocrmypdf + Tesseract (spa+eng) generan una capa de texto
invisible. De la salida de ocrmypdf se toma SOLO esa capa (el Form XObject /OCR-… que
ocrmypdf agrega a cada página) y se agrega a la página original como un objeto nuevo.
El contenido original no se toca: el guardado sigue siendo incremental y la página se
ve exactamente igual (la verificación de fidelidad exige diferencia de píxeles = 0).

Modo "Reconstruido" (experimental): con el mismo reconocimiento, la imagen escaneada
de fondo se deja de dibujar y el texto se escribe visible con una fuente real. Es
aproximado: sirve para documentos simples.

ocrmypdf corre siempre en otro proceso (ver `comando_ocrmypdf`): tarda y usa varios
núcleos, y MuPDF no admite hilos.
"""
from __future__ import annotations

import io
import os
import shutil
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from pdf_studio import config
from pdf_studio.core.documento import ErrorDocumento, Operacion
from pdf_studio.core.pdfutil import independizar_recursos

IDIOMAS = "spa+eng"
# Una imagen que cubre al menos esta fracción de la página se considera "el escaneo"
FRACCION_FONDO = 0.5
TIEMPO_TESSERACT_S = 600


class ModoOCR(str, Enum):
    IDENTICO = "identico"
    RECONSTRUIDO = "reconstruido"

    @property
    def texto(self) -> str:
        return {"identico": "Idéntico", "reconstruido": "Reconstruido (experimental)"}[self.value]


class ErrorOCR(ErrorDocumento):
    """Falta algo para el OCR o el reconocimiento falló (mensaje listo para mostrar)."""


# ---------------------------------------------------------------------- detección
def pagina_sin_texto(pag: pymupdf.Page) -> bool:
    """True si la página no tiene texto extraíble pero sí imágenes (típico de un escaneo)."""
    try:
        if pag.get_text("text").strip():
            return False
        return bool(pag.get_images())
    except Exception:
        return False


def paginas_sin_texto(doc: pymupdf.Document) -> list[int]:
    return [i for i in range(doc.page_count) if pagina_sin_texto(doc[i])]


def parece_escaneado(doc: pymupdf.Document) -> bool:
    """Para sugerir el OCR al abrir: la mayoría de las páginas son imágenes sin texto."""
    sin_texto = paginas_sin_texto(doc)
    return bool(sin_texto) and len(sin_texto) * 2 >= doc.page_count


# ---------------------------------------------------------------------- entorno de Tesseract
def carpeta_tessdata(exe: Path) -> Path:
    """Carpeta de idiomas a usar. Si los idiomas están en la carpeta del usuario
    (%LOCALAPPDATA%\\PDFStudio\\tessdata), se le copian las configuraciones de Tesseract
    (archivos de texto chicos, p. ej. «hocr») que ocrmypdf necesita."""
    instalada = exe.parent / "tessdata"
    if all((instalada / f"{i}.traineddata").is_file() for i in IDIOMAS.split("+")):
        return instalada
    usuario = config.DIR_TESSDATA_USUARIO
    if not all((usuario / f"{i}.traineddata").is_file() for i in IDIOMAS.split("+")):
        faltan = [i for i in IDIOMAS.split("+")
                  if not (usuario / f"{i}.traineddata").is_file() and not (instalada / f"{i}.traineddata").is_file()]
        raise ErrorOCR("Faltan los idiomas del OCR (" + ", ".join(faltan) + "). Instalalos desde el panel "
                       "Faltantes (Ver → Ver faltantes).")
    for nombre in ("configs", "tessconfigs"):
        origen, destino = instalada / nombre, usuario / nombre
        if origen.is_dir() and not destino.is_dir():
            shutil.copytree(origen, destino)
    for nombre in ("pdf.ttf", "osd.traineddata"):
        origen, destino = instalada / nombre, usuario / nombre
        if origen.is_file() and not destino.is_file():
            shutil.copy2(origen, destino)
    return usuario


def entorno_ocr() -> dict[str, str]:
    """Variables de entorno para ocrmypdf: Tesseract en el PATH y la carpeta de idiomas.
    Lanza ErrorOCR con un mensaje claro si falta algo."""
    from pdf_studio.dependencias.chequeo import buscar_tesseract, modulo_disponible
    if not modulo_disponible("ocrmypdf"):
        raise ErrorOCR("Falta el paquete «ocrmypdf». Instalalo desde el panel Faltantes.")
    exe = buscar_tesseract()
    if exe is None:
        raise ErrorOCR("No se encontró Tesseract OCR. Instalalo desde el panel Faltantes "
                       "(ahí está el comando de PowerShell listo para copiar).")
    tessdata = carpeta_tessdata(exe)
    entorno = dict(os.environ)
    entorno["PATH"] = str(exe.parent) + os.pathsep + entorno.get("PATH", "")
    entorno["TESSDATA_PREFIX"] = str(tessdata)
    entorno["PYTHONIOENCODING"] = "utf-8"
    return entorno


def comando_ocrmypdf(entrada: Path, salida: Path, paginas: list[int]) -> tuple[str, list[str]]:
    """(programa, argumentos) para correr ocrmypdf en otro proceso.

    --output-type pdf: sin conversión a PDF/A (que reescribiría el archivo con Ghostscript).
    -O 0: sin optimizar imágenes. --mode skip: las páginas que ya tienen texto no se tocan.
    Igual, de la salida solo se usa la capa de texto."""
    argumentos = ["--output-type", "pdf", "-O", "0", "--mode", "skip", "-l", IDIOMAS,
                  "--pages", ",".join(str(p + 1) for p in sorted(paginas)),
                  "--rasterizer", "pypdfium", "--tesseract-timeout", str(TIEMPO_TESSERACT_S),
                  "--jobs", str(max(1, min(4, (os.cpu_count() or 2) - 1))),
                  str(entrada), str(salida)]
    if getattr(sys, "frozen", False):
        return sys.executable, ["--ocrmypdf", *argumentos]
    return sys.executable, ["-m", "ocrmypdf", *argumentos]


def mensaje_de_error_ocrmypdf(codigo: int, salida: str) -> str:
    """Traduce los códigos de salida de ocrmypdf a un mensaje claro."""
    conocidos = {
        1: "ocrmypdf recibió opciones inválidas.",
        2: "El archivo no es un PDF válido para ocrmypdf.",
        3: "Falta un programa que necesita el OCR (Tesseract).",
        4: "ocrmypdf generó un archivo inválido.",
        5: "No se pudo escribir el archivo temporal del OCR.",
        6: "Las páginas elegidas ya tienen texto: no hacía falta OCR.",
        7: "Tesseract falló al reconocer el texto.",
        8: "El PDF está protegido con contraseña: ocrmypdf no puede leerlo.",
        9: "La configuración de Tesseract es inválida (faltan archivos de idioma o de configuración).",
        15: "Error interno de ocrmypdf.",
        130: "El OCR se canceló.",
    }
    texto = conocidos.get(codigo, f"ocrmypdf terminó con error (código {codigo}).")
    # La última línea de la salida suele explicar el motivo (no contiene texto del documento)
    ultima = next((l.strip() for l in reversed(salida.splitlines()) if l.strip()), "")
    if ultima and len(ultima) < 300:
        texto += f"\nDetalle: {ultima}"
    return texto


# ---------------------------------------------------------------------- capa de texto
def capas_de_texto(salida_ocr: Path, paginas: list[int]) -> dict[int, bytes]:
    """De la salida de ocrmypdf, arma para cada página un PDF de una página que contiene
    SOLO la capa de texto invisible (con las mismas cajas MediaBox/CropBox que la página
    original). Las páginas sin capa (sin texto reconocido) no aparecen en el resultado."""
    import pikepdf

    capas: dict[int, bytes] = {}
    with pikepdf.open(salida_ocr) as pdf:
        for p in paginas:
            if p >= len(pdf.pages):
                continue
            pagina = pdf.pages[p]
            uso = _uso_de_capa(pagina)
            if uso is None:
                continue
            nombre, matriz = uso
            xobj = pagina.Resources.XObject[nombre]
            nuevo = pikepdf.Pdf.new()
            caja = [float(v) for v in pagina.mediabox]
            nuevo.add_blank_page(page_size=(caja[2] - caja[0], caja[3] - caja[1]))
            destino = nuevo.pages[0]
            destino.obj.MediaBox = pikepdf.Array(caja)
            destino.obj.CropBox = pikepdf.Array([float(v) for v in pagina.cropbox])
            destino.obj.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary({"/OCR": nuevo.copy_foreign(xobj)}))
            cm = (" ".join(_num(v) for v in matriz) + " cm ") if matriz else ""
            destino.obj.Contents = nuevo.make_stream(f"q {cm}/OCR Do Q".encode("ascii"))
            datos = io.BytesIO()
            nuevo.save(datos)
            capas[p] = datos.getvalue()
    return capas


def _uso_de_capa(pagina) -> tuple[str, list[float] | None] | None:
    """(nombre del XObject /OCR-…, matriz 'cm' con que se dibuja) o None si no hay capa."""
    import pikepdf

    recursos = pagina.obj.get("/Resources")
    xobjs = recursos.get("/XObject") if recursos is not None else None
    if xobjs is None:
        return None
    candidatos = {str(k) for k in xobjs.keys() if str(k).startswith("/OCR-")}
    if not candidatos:
        return None
    pila: list[list[float] | None] = []
    matriz: list[float] | None = None
    for operandos, operador in pikepdf.parse_content_stream(pagina):
        op = str(operador)
        if op == "q":
            pila.append(matriz)
        elif op == "Q":
            matriz = pila.pop() if pila else None
        elif op == "cm":
            nueva = [float(v) for v in operandos]
            matriz = nueva if matriz is None else _multiplicar(nueva, matriz)
        elif op == "Do" and str(operandos[0]) in candidatos:
            return str(operandos[0]), matriz
    return None


def _multiplicar(a: list[float], b: list[float]) -> list[float]:
    """a × b con la convención de PDF (vector fila)."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
            a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _num(v: float) -> str:
    return f"{v:.6f}".rstrip("0").rstrip(".") or "0"


def _superponer(doc: pymupdf.Document, pagina: int, capa: bytes) -> None:
    """Dibuja la página de `capa` encima de la página, en sus mismas coordenadas.
    show_pdf_page interpreta el rectángulo en coordenadas rotadas: se trabaja sin
    rotación y se restaura al final."""
    pag = doc[pagina]
    rotacion = pag.rotation
    with pymupdf.open("pdf", capa) as fuente:
        if rotacion:
            pag.set_rotation(0)
        try:
            pag.show_pdf_page(pag.rect, fuente, 0, overlay=True, keep_proportion=False)
        finally:
            if rotacion:
                pag.set_rotation(rotacion)


class AgregarCapaOCR(Operacion):
    """Modo Idéntico: agrega la capa de texto invisible sin tocar el contenido original."""

    nombre = "Reconocer texto (OCR)"

    def __init__(self, capas: dict[int, bytes]):
        if not capas:
            raise ErrorOCR("No se reconoció texto nuevo en las páginas elegidas (puede que ya tuvieran "
                           "texto o que la imagen no tenga letras legibles).")
        self.capas = dict(capas)

    def aplicar(self, doc: pymupdf.Document) -> None:
        for p, capa in sorted(self.capas.items()):
            independizar_recursos(doc, p)
            _superponer(doc, p, capa)

    def paginas_afectadas(self) -> set[int]:
        return set(self.capas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        # Sin zonas: la página entera tiene que verse idéntica (el texto es invisible)
        return {p: [] for p in self.capas}

    def descripcion(self) -> str:
        n = len(self.capas)
        return f"OCR en {n} página{'s' if n != 1 else ''}"


# ---------------------------------------------------------------------- modo reconstruido
@dataclass
class LineaOCR:
    texto: str
    caja: tuple[float, float, float, float]     # en coordenadas de página sin rotar
    tamano: float
    bloque: int = 0


def lineas_de_capa(capa: bytes) -> list[LineaOCR]:
    """Las líneas reconocidas de una capa (texto, caja y tamaño de letra estimado)."""
    lineas = []
    with pymupdf.open("pdf", capa) as d:
        for n, bloque in enumerate(d[0].get_text("dict")["blocks"]):
            for linea in bloque.get("lines", []):
                if abs(linea["dir"][1]) > 0.1:
                    continue        # solo texto horizontal
                texto = " ".join(s["text"].strip() for s in linea["spans"] if s["text"].strip())
                if texto:
                    caja = pymupdf.Rect(linea["bbox"])
                    lineas.append(LineaOCR(texto, tuple(caja), max(s["size"] for s in linea["spans"]), n))
    return lineas


class ReconstruirOCR(Operacion):
    """Modo Reconstruido (experimental): deja de dibujar la imagen escaneada y escribe
    el texto reconocido, visible y editable. El resto del contenido de la página
    (otras imágenes chicas, dibujos) se conserva. Es aproximado."""

    nombre = "Reconstruir texto (OCR)"

    def __init__(self, capas: dict[int, bytes]):
        if not capas:
            raise ErrorOCR("No se reconoció texto nuevo en las páginas elegidas (puede que ya tuvieran "
                           "texto o que la imagen no tenga letras legibles).")
        self.capas = dict(capas)

    def aplicar(self, doc: pymupdf.Document) -> None:
        for p, capa in sorted(self.capas.items()):
            independizar_recursos(doc, p)
            self._ocultar_fondo(doc, p)
            pag = doc[p]
            lineas = lineas_de_capa(capa)
            nombre, fuente = _fuente_mas_parecida(lineas)
            rotacion = pag.rotation
            if rotacion:
                pag.set_rotation(0)
            try:
                forma = pag.new_shape()
                for linea in lineas:
                    caja = pymupdf.Rect(linea.caja)
                    tamano = _tamano_de_bloque(lineas, linea.bloque)
                    escala = _escala(fuente, linea, tamano)
                    origen = pymupdf.Point(caja.x0, caja.y1 - caja.height * 0.2)
                    forma.insert_text(origen, linea.texto, fontsize=tamano, fontname=nombre,
                                      morph=(origen, pymupdf.Matrix(escala, 1)))
                forma.commit(overlay=True)
            finally:
                if rotacion:
                    pag.set_rotation(rotacion)

    @staticmethod
    def _ocultar_fondo(doc: pymupdf.Document, p: int) -> None:
        """La imagen del escaneo se reemplaza, SOLO en los recursos de esta página, por un
        dibujo vacío: el contenido original no se reescribe y las otras páginas no cambian."""
        pag = doc[p]
        area = abs(pag.rect)
        fondos = set()
        for info in pag.get_image_info(xrefs=True):
            if info.get("xref") and abs(pymupdf.Rect(info["bbox"]) & pag.rect) >= area * FRACCION_FONDO:
                fondos.add(info["xref"])
        if not fondos:
            raise ErrorOCR(f"La página {p + 1} no tiene una imagen de fondo para reemplazar.")
        directas = {img[0]: img[7] for img in pag.get_images(full=True) if img[9] == 0}
        if not fondos <= set(directas):
            raise ErrorOCR(f"En la página {p + 1} la imagen escaneada está dentro de un objeto "
                           "compartido: no se puede reconstruir.")
        vacio = doc.get_new_xref()
        doc.update_object(vacio, "<</Type/XObject/Subtype/Form/BBox[0 0 1 1]>>")
        doc.update_stream(vacio, b"")
        cont = doc.xref_get_key(pag.xref, "Resources")
        for xref in fondos:
            nombre = directas[xref]
            if cont[0] == "xref":
                doc.xref_set_key(int(cont[1].split()[0]), f"XObject/{nombre}", f"{vacio} 0 R")
            else:
                doc.xref_set_key(pag.xref, f"Resources/XObject/{nombre}", f"{vacio} 0 R")

    def paginas_afectadas(self) -> set[int]:
        return set(self.capas)

    def descripcion(self) -> str:
        n = len(self.capas)
        return f"Reconstruir texto en {n} página{'s' if n != 1 else ''}"


# Fuentes estándar del PDF (no se embeben): sin serifa, con serifa y monoespaciada
FUENTES_RECONSTRUIDO = ("helv", "tiro", "cour")


def _escala(fuente: pymupdf.Font, linea: LineaOCR, tamano: float) -> float:
    """Escalado horizontal para que la línea ocupe el mismo ancho que en el escaneo."""
    ancho = fuente.text_length(linea.texto, fontsize=tamano)
    escala = pymupdf.Rect(linea.caja).width / ancho if ancho > 0 else 1.0
    return min(2.0, max(0.5, escala))


def _fuente_mas_parecida(lineas: list[LineaOCR]) -> tuple[str, pymupdf.Font]:
    """La fuente estándar que necesita menos escalado horizontal en promedio."""
    import math

    mejor = None
    for nombre in FUENTES_RECONSTRUIDO:
        fuente = pymupdf.Font(nombre)
        desvio = sum(abs(math.log(_escala(fuente, l, _tamano_de_bloque(lineas, l.bloque)))) for l in lineas)
        if mejor is None or desvio < mejor[0]:
            mejor = (desvio, nombre, fuente)
    return (mejor[1], mejor[2]) if mejor else ("helv", pymupdf.Font("helv"))


def _tamano_de_bloque(lineas: list[LineaOCR], bloque: int) -> float:
    """Tamaño de letra parejo: la altura de cada línea varía según tenga letras con o sin
    «patas», así que los tamaños cercanos a la mediana de la página se igualan a ella
    (los títulos, bastante más grandes, conservan el suyo)."""
    alturas = sorted(pymupdf.Rect(l.caja).height * 0.95 for l in lineas)
    if not alturas:
        return 10.0
    mediana = alturas[len(alturas) // 2]
    propias = sorted(pymupdf.Rect(l.caja).height * 0.95 for l in lineas if l.bloque == bloque)
    propia = propias[len(propias) // 2] if propias else mediana
    return max(4.0, mediana if abs(propia - mediana) <= mediana * 0.3 else propia)


def crear_operacion(modo: ModoOCR, capas: dict[int, bytes]) -> Operacion:
    return AgregarCapaOCR(capas) if modo == ModoOCR.IDENTICO else ReconstruirOCR(capas)


# ---------------------------------------------------------------------- uso directo (tests, lotes)
def reconocer(doc: pymupdf.Document, paginas: list[int], carpeta: Path,
              timeout: float | None = None) -> dict[int, bytes]:
    """Corre ocrmypdf de forma sincrónica (para tests y procesamiento por lotes; la interfaz
    usa un QProcess). Devuelve las capas de texto por página."""
    import subprocess

    entrada, salida = carpeta / "ocr_entrada.pdf", carpeta / "ocr_salida.pdf"
    doc.save(entrada, garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
    programa, argumentos = comando_ocrmypdf(entrada, salida, paginas)
    r = subprocess.run([programa, *argumentos], env=entorno_ocr(), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise ErrorOCR(mensaje_de_error_ocrmypdf(r.returncode, r.stderr))
    return capas_de_texto(salida, paginas)
