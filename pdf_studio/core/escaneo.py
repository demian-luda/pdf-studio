"""Importar fotos de documentos como un PDF escaneado.

Para cada foto: se detecta el papel (el cuadrilátero claro más grande), se recorta el
fondo y se corrige la perspectiva, se endereza la inclinación que quede y se mejora el
contraste (fondo blanco parejo, sin sombras). Después se arma un PDF con una página por
foto, del tamaño de papel que corresponda. Las fotos originales nunca se modifican.

Todo el procesamiento de imagen usa OpenCV (opencv-python-headless) y numpy.
"""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

import numpy as np

from pdf_studio.core.documento import ErrorDocumento, Limitacion

EXTENSIONES = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp")
LADO_DETECCION = 1000          # la detección del papel se hace sobre una copia de este tamaño
ANGULO_MAXIMO = 15.0           # inclinación máxima que se corrige sola (grados)
TOLERANCIA_PAPEL = 0.08        # diferencia de proporción aceptada para reconocer A4, Carta u Oficio
MM_POR_PULGADA = 25.4


class Mejora(str, Enum):
    COLOR = "color"
    GRISES = "grises"
    BYN = "byn"
    NINGUNA = "ninguna"

    @property
    def texto(self) -> str:
        return {"color": "Color mejorado", "grises": "Escala de grises", "byn": "Blanco y negro",
                "ninguna": "Sin cambios"}[self.value]


class TamanoPapel(str, Enum):
    AUTO = "auto"
    A4 = "a4"
    CARTA = "carta"
    OFICIO = "oficio"

    @property
    def texto(self) -> str:
        return {"auto": "Automático (según la forma del papel)", "a4": "A4 (210 × 297 mm)",
                "carta": "Carta (216 × 279 mm)", "oficio": "Oficio (216 × 356 mm)"}[self.value]


MEDIDAS_MM = {TamanoPapel.A4: (210.0, 297.0), TamanoPapel.CARTA: (215.9, 279.4),
              TamanoPapel.OFICIO: (215.9, 355.6)}


def cv2():
    """OpenCV, con un mensaje claro si falta (es un paquete opcional)."""
    try:
        import cv2 as modulo
    except ImportError as e:
        raise Limitacion("Para importar fotos como PDF escaneado falta el paquete «opencv-python-headless». "
                         "Instalalo desde el panel Faltantes.") from e
    return modulo


# ---------------------------------------------------------------------- modelo
@dataclass
class AjusteFoto:
    """Lo que el usuario ajustó en una foto. Las esquinas van normalizadas (0 a 1) sobre la
    foto ya girada; None = detectarlas solas."""
    ruta: str
    rotacion: int = 0                                     # 0, 90, 180 o 270, sentido horario
    esquinas: list[tuple[float, float]] | None = None

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "AjusteFoto":
        esquinas = [tuple(p) for p in d["esquinas"]] if d.get("esquinas") else None
        return cls(d["ruta"], int(d.get("rotacion", 0)), esquinas)


@dataclass
class OpcionesEscaneo:
    recortar: bool = True          # recortar el papel y corregir la perspectiva
    enderezar: bool = True         # corregir la inclinación que quede
    mejora: Mejora = Mejora.COLOR
    tamano: TamanoPapel = TamanoPapel.AUTO
    dpi: int = 200

    def a_dict(self) -> dict:
        d = asdict(self)
        d["mejora"], d["tamano"] = self.mejora.value, self.tamano.value
        return d

    @classmethod
    def desde_dict(cls, d: dict) -> "OpcionesEscaneo":
        return cls(bool(d.get("recortar", True)), bool(d.get("enderezar", True)), Mejora(d.get("mejora", "color")),
                   TamanoPapel(d.get("tamano", "auto")), int(d.get("dpi", 200)))


@dataclass
class Procesada:
    imagen: np.ndarray                                   # BGR, gris o binaria (0/255)
    esquinas: list[tuple[float, float]] | None           # las usadas, normalizadas (None = sin recorte)
    detectado: bool                                      # se encontró el borde del papel
    angulo: float = 0.0                                  # inclinación corregida (grados)


@dataclass
class InformeEscaneo:
    paginas: int
    avisos: list[str] = field(default_factory=list)
    ocr: bool = False              # se agregó la capa de texto reconocido

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "InformeEscaneo":
        return cls(int(d["paginas"]), list(d.get("avisos", [])), bool(d.get("ocr", False)))


# ---------------------------------------------------------------------- lectura
def leer_imagen(ruta: str | Path, rotacion: int = 0) -> np.ndarray:
    """La foto como BGR, derecha según su EXIF (los celulares guardan la rotación aparte) y
    girada `rotacion` grados en sentido horario."""
    from PIL import Image, ImageOps

    ruta = Path(ruta)
    try:
        with Image.open(ruta) as foto:
            foto = ImageOps.exif_transpose(foto)
            if foto.mode not in ("RGB", "L"):
                fondo = Image.new("RGB", foto.size, (255, 255, 255))
                foto = foto.convert("RGBA")
                fondo.paste(foto, mask=foto.split()[-1])
                foto = fondo
            matriz = np.asarray(foto.convert("RGB"))[:, :, ::-1].copy()
    except Image.DecompressionBombError as e:
        raise Limitacion(f"«{ruta.name}» es demasiado grande para procesarla.") from e
    except (OSError, ValueError) as e:
        raise ErrorDocumento(f"No se pudo leer la imagen «{ruta.name}».") from e
    return girar(matriz, rotacion)


def girar(img: np.ndarray, rotacion: int) -> np.ndarray:
    vueltas = (rotacion // 90) % 4
    return np.ascontiguousarray(np.rot90(img, -vueltas)) if vueltas else img


def reducir(img: np.ndarray, lado: int) -> tuple[np.ndarray, float]:
    """Copia cuyo lado mayor mide como mucho `lado`, y el factor usado."""
    h, w = img.shape[:2]
    factor = min(1.0, lado / max(h, w))
    if factor >= 1.0:
        return img, 1.0
    return cv2().resize(img, (max(1, round(w * factor)), max(1, round(h * factor))),
                        interpolation=cv2().INTER_AREA), factor


# ---------------------------------------------------------------------- papel
def ordenar_esquinas(puntos) -> np.ndarray:
    """Arriba-izquierda, arriba-derecha, abajo-derecha, abajo-izquierda."""
    p = np.asarray(puntos, dtype=np.float32).reshape(4, 2)
    suma, resta = p.sum(axis=1), np.diff(p, axis=1).ravel()
    return np.array([p[np.argmin(suma)], p[np.argmin(resta)], p[np.argmax(suma)], p[np.argmax(resta)]],
                    dtype=np.float32)


def _cuadrilatero(contorno, area_minima: float):
    c = cv2()
    area = c.contourArea(contorno)
    if area < area_minima:
        return None
    perimetro = c.arcLength(contorno, True)
    for eps in (0.02, 0.03, 0.05):
        aprox = c.approxPolyDP(contorno, eps * perimetro, True)
        if len(aprox) == 4 and c.isContourConvex(aprox):
            return area, aprox.reshape(4, 2).astype(np.float32)
    # Esquinas redondeadas o un borde tapado: si el contorno llena su rectángulo mínimo, sirve
    rect = c.minAreaRect(contorno)
    if rect[1][0] * rect[1][1] > 0 and area / (rect[1][0] * rect[1][1]) > 0.9:
        return area, c.boxPoints(rect).astype(np.float32)
    return None


def detectar_papel(img: np.ndarray) -> np.ndarray | None:
    """Esquinas del papel (en píxeles de `img`, ordenadas) o None si no se ve el borde
    (por ejemplo, si el papel ocupa toda la foto)."""
    c = cv2()
    chica, factor = reducir(img, LADO_DETECCION)
    h, w = chica.shape[:2]
    gris = c.cvtColor(chica, c.COLOR_BGR2GRAY) if chica.ndim == 3 else chica
    gris = c.GaussianBlur(gris, (5, 5), 0)
    nucleo = c.getStructuringElement(c.MORPH_RECT, (5, 5))
    # Dos máscaras: el papel más claro que el fondo (Otsu) y los bordes (Canny)
    _, claro = c.threshold(gris, 0, 255, c.THRESH_BINARY + c.THRESH_OTSU)
    claro = c.morphologyEx(claro, c.MORPH_CLOSE, c.getStructuringElement(c.MORPH_RECT, (15, 15)))
    claro = c.morphologyEx(claro, c.MORPH_OPEN, nucleo)
    bordes = c.dilate(c.Canny(gris, 40, 120), nucleo, iterations=2)
    area_minima = 0.15 * h * w
    margen = 0.01 * max(h, w)
    # Primero la máscara por brillo (bordes exactos); la de bordes, solo si esa no alcanza
    # (sus contornos quedan un poco afuera por la dilatación)
    for mascara, modo in ((claro, c.RETR_EXTERNAL), (bordes, c.RETR_LIST)):
        contornos, _ = c.findContours(mascara, modo, c.CHAIN_APPROX_SIMPLE)
        utiles = []
        for contorno in sorted(contornos, key=c.contourArea, reverse=True)[:6]:
            encontrado = _cuadrilatero(contorno, area_minima)
            if encontrado is None:
                continue
            area, puntos = encontrado
            # Si coincide con el borde de la foto entera, no es el papel: es el encuadre
            en_el_borde = all(min(x, y, w - 1 - x, h - 1 - y) <= margen for x, y in puntos)
            if en_el_borde and area > 0.9 * h * w:
                continue
            if _resalta_del_fondo(gris, puntos):
                utiles.append((area, puntos))
        if utiles:
            _, puntos = max(utiles, key=lambda u: u[0])
            return ordenar_esquinas(puntos / factor)
    return None


def _resalta_del_fondo(gris: np.ndarray, puntos: np.ndarray, diferencia: float = 18.0) -> bool:
    """El borde del papel es más claro que lo que lo rodea. Descarta, por ejemplo, un
    recuadro dibujado dentro de una hoja que ocupa toda la imagen."""
    c = cv2()
    mascara = np.zeros(gris.shape, np.uint8)
    c.fillConvexPoly(mascara, ordenar_esquinas(puntos).astype(np.int32), 255)
    ancho = max(3, min(gris.shape) // 50)
    nucleo = c.getStructuringElement(c.MORPH_RECT, (ancho, ancho))
    adentro = mascara & ~c.erode(mascara, nucleo, iterations=2)
    afuera = c.dilate(mascara, nucleo, iterations=2) & ~mascara
    if not np.any(afuera):
        return True                 # el papel llega al borde de la foto por todos lados
    # La mediana: una sombra o el texto del borde no la mueven
    return float(np.median(gris[adentro > 0])) - float(np.median(gris[afuera > 0])) >= diferencia


def normalizar_esquinas(esquinas: np.ndarray, img: np.ndarray) -> list[tuple[float, float]]:
    h, w = img.shape[:2]
    return [(float(x) / w, float(y) / h) for x, y in esquinas]


def a_pixeles(esquinas, img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    return np.array([(x * w, y * h) for x, y in esquinas], dtype=np.float32)


def corregir_perspectiva(img: np.ndarray, esquinas: np.ndarray) -> np.ndarray:
    """La zona del papel, vista de frente."""
    c = cv2()
    tl, tr, br, bl = ordenar_esquinas(esquinas)
    ancho = int(round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl))))
    alto = int(round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr))))
    ancho, alto = max(ancho, 1), max(alto, 1)
    destino = np.array([(0, 0), (ancho - 1, 0), (ancho - 1, alto - 1), (0, alto - 1)], dtype=np.float32)
    matriz = c.getPerspectiveTransform(np.array([tl, tr, br, bl], dtype=np.float32), destino)
    return c.warpPerspective(img, matriz, (ancho, alto), flags=c.INTER_CUBIC, borderMode=c.BORDER_REPLICATE)


# ---------------------------------------------------------------------- inclinación
def angulo_inclinacion(img: np.ndarray) -> float:
    """Inclinación del texto en grados (positivo = girado en sentido antihorario), buscando
    el ángulo en el que las filas de texto quedan más marcadas (perfil de proyección)."""
    c = cv2()
    chica, _ = reducir(img, 900)
    gris = c.cvtColor(chica, c.COLOR_BGR2GRAY) if chica.ndim == 3 else chica
    _, tinta = c.threshold(gris, 0, 255, c.THRESH_BINARY_INV + c.THRESH_OTSU)
    if np.count_nonzero(tinta) < 0.002 * tinta.size:
        return 0.0                      # casi sin texto: no hay de dónde medir
    h, w = tinta.shape
    centro = (w / 2, h / 2)

    def nitidez(angulo: float) -> float:
        m = c.getRotationMatrix2D(centro, angulo, 1.0)
        girada = c.warpAffine(tinta, m, (w, h), flags=c.INTER_NEAREST, borderValue=0)
        filas = girada.sum(axis=1, dtype=np.float64)
        return float(np.sum(np.diff(filas) ** 2))

    mejor = max(np.arange(-ANGULO_MAXIMO, ANGULO_MAXIMO + 0.01, 1.0), key=nitidez)
    fino = max(np.arange(mejor - 1.0, mejor + 1.01, 0.1), key=nitidez)
    # El ángulo que endereza es el opuesto a la inclinación
    angulo = -float(round(fino, 1))
    return 0.0 if abs(angulo) < 0.15 else angulo


def enderezar(img: np.ndarray, angulo: float) -> np.ndarray:
    """Gira la imagen para deshacer una inclinación de `angulo` grados. Conserva el tamaño
    (y con él la proporción del papel): las puntas que quedan afuera son solo margen."""
    if abs(angulo) < 0.05:
        return img
    c = cv2()
    h, w = img.shape[:2]
    m = c.getRotationMatrix2D((w / 2, h / 2), -angulo, 1.0)
    return c.warpAffine(img, m, (w, h), flags=c.INTER_CUBIC, borderMode=c.BORDER_REPLICATE)


# ---------------------------------------------------------------------- contraste
def _fondo(img: np.ndarray) -> np.ndarray:
    """Estimación del fondo (el papel con sus sombras), sin el texto: se calcula en chico."""
    c = cv2()
    chica, factor = reducir(img, 800)
    k = max(3, (min(chica.shape[:2]) // 60) | 1)
    fondo = c.dilate(chica, c.getStructuringElement(c.MORPH_RECT, (k, k)))
    fondo = c.medianBlur(fondo, max(3, (k * 3) | 1))
    h, w = img.shape[:2]
    return c.resize(fondo, (w, h), interpolation=c.INTER_LINEAR) if factor < 1 else fondo


def _estirar(gris: np.ndarray, bajo: float = 1.0, alto: float = 99.5) -> np.ndarray:
    a, b = np.percentile(gris, (bajo, alto))
    if b - a < 1:
        return gris
    return np.clip((gris.astype(np.float32) - a) * (255.0 / (b - a)), 0, 255).astype(np.uint8)


def mejorar(img: np.ndarray, mejora: Mejora) -> np.ndarray:
    """Fondo blanco parejo (sin sombras) y texto más oscuro."""
    if mejora == Mejora.NINGUNA:
        return img
    c = cv2()
    parejo = c.divide(img, _fondo(img), scale=255)
    if mejora == Mejora.COLOR:
        if parejo.ndim == 2:
            return _estirar(parejo)
        # Contraste en la luminancia, sin cambiar los colores
        lab = c.cvtColor(parejo, c.COLOR_BGR2LAB)
        lab[:, :, 0] = _estirar(lab[:, :, 0])
        return c.cvtColor(lab, c.COLOR_LAB2BGR)
    gris = c.cvtColor(parejo, c.COLOR_BGR2GRAY) if parejo.ndim == 3 else parejo
    if mejora == Mejora.GRISES:
        return _estirar(gris)
    # Blanco y negro: con el fondo ya parejo, un umbral global separa bien el texto
    _, binaria = c.threshold(gris, 0, 255, c.THRESH_BINARY + c.THRESH_OTSU)
    return binaria


# ---------------------------------------------------------------------- foto completa
def procesar(img: np.ndarray, ajuste: AjusteFoto, opciones: OpcionesEscaneo) -> Procesada:
    """Aplica recorte, perspectiva, enderezado y mejora a una foto ya leída y girada."""
    esquinas, detectado = None, False
    resultado = img
    if opciones.recortar:
        if ajuste.esquinas is not None:
            puntos = a_pixeles(ajuste.esquinas, img)
            detectado = True
        else:
            puntos = detectar_papel(img)
            detectado = puntos is not None
        if puntos is not None:
            esquinas = normalizar_esquinas(ordenar_esquinas(puntos), img)
            resultado = corregir_perspectiva(img, puntos)
    angulo = 0.0
    if opciones.enderezar:
        angulo = angulo_inclinacion(resultado)
        resultado = enderezar(resultado, angulo)
    return Procesada(mejorar(resultado, opciones.mejora), esquinas, detectado, angulo)


def medida_de_pagina(ancho_px: int, alto_px: int, tamano: TamanoPapel) -> tuple[float, float, bool]:
    """(ancho, alto) de la página en puntos y si la imagen se estira para llenarla (cuando
    la proporción es casi la del papel: la medida de una foto nunca es exacta)."""
    vertical = alto_px >= ancho_px
    proporcion = max(ancho_px, alto_px) / max(1, min(ancho_px, alto_px))
    elegido = None
    if tamano == TamanoPapel.AUTO:
        cercanos = sorted(MEDIDAS_MM, key=lambda t: abs(MEDIDAS_MM[t][1] / MEDIDAS_MM[t][0] - proporcion))
        t = cercanos[0]
        if abs(MEDIDAS_MM[t][1] / MEDIDAS_MM[t][0] / proporcion - 1) <= TOLERANCIA_PAPEL:
            elegido = t
    else:
        elegido = tamano
    if elegido is None:
        # Papel de otra forma: se respeta su proporción, con el largo de un A4
        largo = MEDIDAS_MM[TamanoPapel.A4][1]
        corto, largo_mm = largo / proporcion, largo
        llenar = True
    else:
        corto, largo_mm = MEDIDAS_MM[elegido]
        llenar = abs(largo_mm / corto / proporcion - 1) <= TOLERANCIA_PAPEL
    a_pt = 72.0 / MM_POR_PULGADA
    ancho, alto = (corto * a_pt, largo_mm * a_pt) if vertical else (largo_mm * a_pt, corto * a_pt)
    return ancho, alto, llenar


def _codificar(img: np.ndarray, mejora: Mejora) -> bytes:
    """JPEG para color y grises; PNG de 1 bit para blanco y negro (queda liviano)."""
    from PIL import Image

    buf = io.BytesIO()
    if mejora == Mejora.BYN:
        Image.fromarray(img).convert("1").save(buf, "PNG", optimize=True)
    elif img.ndim == 2:
        Image.fromarray(img).save(buf, "JPEG", quality=85, optimize=True)
    else:
        Image.fromarray(img[:, :, ::-1]).save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue()


def crear_pdf(fotos: list[AjusteFoto], opciones: OpcionesEscaneo, destino: Path, progreso=None) -> InformeEscaneo:
    """Arma el PDF (una página por foto) y lo escribe en `destino` de forma atómica."""
    import pymupdf

    if not fotos:
        raise Limitacion("No hay fotos para importar.")
    destino = Path(destino)
    if destino.resolve() in {Path(f.ruta).resolve() for f in fotos}:
        raise Limitacion("El PDF no puede reemplazar una de las fotos: elegí otro nombre.")
    avisos: list[str] = []
    doc = pymupdf.open()
    try:
        for i, foto in enumerate(fotos):
            if progreso is not None:
                progreso(i, len(fotos))
            nombre = Path(foto.ruta).name
            img = leer_imagen(foto.ruta, foto.rotacion)
            hecha = procesar(img, foto, opciones)
            if opciones.recortar and not hecha.detectado:
                avisos.append(f"«{nombre}»: no se encontró el borde del papel; se usó la foto entera.")
            h, w = hecha.imagen.shape[:2]
            ancho, alto, llenar = medida_de_pagina(w, h, opciones.tamano)
            # No se guarda más resolución que la pedida (ni se agranda una foto chica)
            factor = min(1.0, (ancho / 72.0 * opciones.dpi) / w, (alto / 72.0 * opciones.dpi) / h)
            imagen = hecha.imagen
            if factor < 0.98:
                imagen = cv2().resize(imagen, (max(1, round(w * factor)), max(1, round(h * factor))),
                                      interpolation=cv2().INTER_AREA)
                if opciones.mejora == Mejora.BYN:
                    imagen = np.where(imagen > 127, 255, 0).astype(np.uint8)
            pag = doc.new_page(width=ancho, height=alto)
            caja = pag.rect
            if not llenar:
                escala = min(ancho / w, alto / h)
                caja = pymupdf.Rect(0, 0, w * escala, h * escala)
                caja += ((ancho - caja.width) / 2, (alto - caja.height) / 2, (ancho - caja.width) / 2,
                         (alto - caja.height) / 2)
            pag.insert_image(caja, stream=_codificar(imagen, opciones.mejora), keep_proportion=not llenar)
        if progreso is not None:
            progreso(len(fotos), len(fotos))
        doc.set_metadata({"producer": "PDF Studio", "creator": "PDF Studio (importar fotos)"})
        temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
        try:
            doc.save(temporal, garbage=3, deflate=True)
            temporal.replace(destino)
        except PermissionError as e:
            temporal.unlink(missing_ok=True)
            raise ErrorDocumento(f"No se pudo escribir {destino}. ¿Está abierto en otro programa?") from e
        except OSError as e:
            temporal.unlink(missing_ok=True)
            raise ErrorDocumento(f"No se pudo escribir {destino}: {e}") from e
        return InformeEscaneo(doc.page_count, avisos)
    finally:
        doc.close()
