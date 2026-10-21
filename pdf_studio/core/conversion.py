"""Convertir un escaneado (PDF de imágenes o fotos) en un documento editable.

Para cada página: se endereza, se emparejan el fondo y el contraste, se detectan las
tablas por sus líneas (OpenCV), se reconoce el texto con Tesseract (TSV: caja y confianza
por palabra) y se recortan como imágenes los logos, firmas, sellos y el texto manuscrito
(la tinta que no es texto reconocido con confianza). Después `core.maquetado` arma la
estructura y se elige una fuente parecida a la del escaneo.

Corre en otro proceso (ver `pdf_studio.procesos`, tarea «convertir»): tarda y MuPDF no
admite hilos. El documento original y las fotos nunca se modifican.
"""
from __future__ import annotations

import csv
import io
import math
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pdf_studio.core import escaneo, ortografia
from pdf_studio.core.documento import ErrorDocumento, Limitacion
from pdf_studio.core.maquetado import (FACTORES, AnalisisPagina, Grilla, PalabraOCR, RegionImagen, maquetar,
                                       tamano_palabra)
from pdf_studio.core.reconstruccion import Fuente, Palabra, Reconstruccion

DPI_ANALISIS = 300
LADO_MAXIMO = 4400              # px: una página más grande se analiza con menos resolución
DPI_REVISION = 150              # resolución de la imagen que se muestra en la revisión
IDIOMAS = "spa+eng"
TIEMPO_TESSERACT_S = 300
CONF_TEXTO = 65.0               # una palabra con al menos esta confianza es texto seguro
MAXIMO_PAGINAS = 200


class ErrorConversion(ErrorDocumento):
    """La conversión falló (mensaje listo para mostrar)."""


# ---------------------------------------------------------------------- dependencias
def verificar_dependencias() -> None:
    """Lanza Limitacion con un mensaje claro si falta algo para convertir."""
    escaneo.cv2()
    try:
        import docx  # noqa: F401
    except ImportError as e:
        raise Limitacion("Para crear el documento de Word falta el paquete «python-docx». Instalalo desde el "
                         "panel Faltantes.") from e


def herramientas_ocr() -> tuple[Path, Path]:
    """(tesseract.exe, carpeta de idiomas) o Limitacion si falta algo."""
    from pdf_studio.core.ocr import LimitacionOCR, carpeta_tessdata
    from pdf_studio.dependencias.chequeo import buscar_tesseract
    exe = buscar_tesseract()
    if exe is None:
        raise LimitacionOCR("No se encontró Tesseract OCR, que hace falta para reconocer el texto. Instalalo "
                            "desde el panel Faltantes (ahí está el comando de PowerShell listo para copiar).")
    return exe, carpeta_tessdata(exe)


# ---------------------------------------------------------------------- OCR
@dataclass
class PalabraTSV:
    texto: str
    caja: tuple[int, int, int, int]     # px
    conf: float
    linea: tuple[int, int, int]


def ocr_tsv(img: np.ndarray, dpi: float, tesseract: Path, tessdata: Path, carpeta: Path,
            psm: int = 3) -> list[PalabraTSV]:
    """Reconoce el texto de una imagen en escala de grises con Tesseract (salida TSV)."""
    from PIL import Image

    carpeta.mkdir(parents=True, exist_ok=True)
    archivo = carpeta / "ocr_pagina.png"
    Image.fromarray(img).save(archivo, dpi=(dpi, dpi))
    entorno = dict(os.environ)
    entorno["TESSDATA_PREFIX"] = str(tessdata)
    entorno["OMP_THREAD_LIMIT"] = "2"
    try:
        r = subprocess.run([str(tesseract), str(archivo), "stdout", "-l", IDIOMAS, "--psm", str(psm),
                            "--dpi", str(int(round(dpi))), "tsv"],
                           env=entorno, capture_output=True, timeout=TIEMPO_TESSERACT_S,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise ErrorConversion("Tesseract tardó demasiado en reconocer una página.") from e
    except OSError as e:
        raise ErrorConversion(f"No se pudo ejecutar Tesseract: {e}") from e
    finally:
        archivo.unlink(missing_ok=True)
    if r.returncode != 0:
        detalle = r.stderr.decode("utf-8", "replace").strip().splitlines()
        ultima = detalle[-1] if detalle else ""
        raise ErrorConversion("Tesseract falló al reconocer el texto." + (f"\nDetalle: {ultima}" if ultima and
                                                                          len(ultima) < 300 else ""))
    return leer_tsv(r.stdout.decode("utf-8", "replace"))


def leer_tsv(texto: str) -> list[PalabraTSV]:
    palabras = []
    lector = csv.reader(io.StringIO(texto), delimiter="\t", quoting=csv.QUOTE_NONE)
    next(lector, None)
    for fila in lector:
        if len(fila) < 12 or fila[0] != "5":
            continue
        contenido = fila[11].strip()
        try:
            conf = float(fila[10])
            x, y, w, h = (int(v) for v in fila[6:10])
        except ValueError:
            continue
        if not contenido or conf < 0 or w <= 0 or h <= 0:
            continue
        palabras.append(PalabraTSV(contenido, (x, y, x + w, y + h), conf,
                                   (int(fila[2]), int(fila[3]), int(fila[4]))))
    return palabras


# ---------------------------------------------------------------------- tablas
@dataclass
class _Segmento:
    pos: float          # y (horizontal) o x (vertical) del centro
    a: int              # extremos
    b: int
    etiqueta: int = 0   # componente conexa (para borrar solo las líneas aceptadas)


def _segmentos(mascara: np.ndarray, horizontal: bool) -> tuple[list[_Segmento], np.ndarray]:
    c = escaneo.cv2()
    n, etiquetas, stats, _ = c.connectedComponentsWithStats(mascara, connectivity=8)
    salida = []
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if horizontal:
            salida.append(_Segmento(y + h / 2, x, x + w, i))
        else:
            salida.append(_Segmento(x + w / 2, y, y + h, i))
    return salida, etiquetas


def _verticales_cortas(corta: np.ndarray, larga: np.ndarray, horiz: np.ndarray, tolerancia: float) -> np.ndarray:
    """Las verticales cortas (las de una tabla de un solo renglón) que van de una horizontal a
    otra. Sin esa condición, los trazos de las letras grandes pasarían por líneas."""
    c = escaneo.cv2()
    n, etiquetas, stats, _ = c.connectedComponentsWithStats(corta & ~c.dilate(larga, np.ones((3, 3), np.uint8)),
                                                            connectivity=8)
    if n <= 1:
        return larga
    salida = larga.copy()
    t = max(4, int(tolerancia) // 3)          # una línea de verdad toca la otra; una letra, no
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        alto_img = horiz.shape[0]
        # Arriba y abajo, otra línea (o el borde de la hoja, si la tabla quedó cortada)
        arriba = y <= 2 * tolerancia or horiz[max(0, y - t):y + t + 1, x:x + w].any()
        abajo = y + h >= alto_img - 2 * tolerancia or horiz[max(0, y + h - t - 1):y + h + t, x:x + w].any()
        if arriba and abajo:
            salida[etiquetas == i] = 255
    return salida


def _solo_finas(mascara: np.ndarray, horizontal: bool, grosor: int) -> np.ndarray:
    """Quita lo que no es una línea fina (por ejemplo, el relleno de un logo, que también
    tiene tramos largos de tinta)."""
    c = escaneo.cv2()
    n, etiquetas, stats, _ = c.connectedComponentsWithStats(mascara, connectivity=8)
    # Grosor medio = área / largo (no el alto de la caja: una línea larga apenas inclinada
    # tiene una caja alta, pero sigue siendo fina)
    largo = np.maximum(1, stats[:, c.CC_STAT_WIDTH if horizontal else c.CC_STAT_HEIGHT])
    gruesos = stats[:, c.CC_STAT_AREA] / largo > grosor
    gruesos[0] = False
    if not gruesos.any():
        return mascara
    salida = mascara.copy()
    salida[gruesos[etiquetas]] = 0
    return salida


def _agrupar(segmentos: list[_Segmento], tolerancia: float) -> list[list[_Segmento]]:
    grupos: list[list[_Segmento]] = []
    for s in sorted(segmentos, key=lambda s: s.pos):
        if grupos and s.pos - grupos[-1][-1].pos <= tolerancia:
            grupos[-1].append(s)
        else:
            grupos.append([s])
    return grupos


def _de_borde_a_borde(grupo: list[_Segmento], cruces: list[float], tolerancia: float) -> bool:
    """La línea va de una línea que la cruza a otra (puede pasarse o faltarle un pedazo en
    las puntas, si quedó cortada). Un trazo recto de una firma, dentro de una celda, no."""
    a, b = min(s.a for s in grupo), max(s.b for s in grupo)
    tocadas = [c for c in cruces if a - tolerancia <= c <= b + tolerancia]
    if len(tocadas) >= 2:
        return True
    return (any(abs(a - c) <= tolerancia or a < cruces[0] for c in cruces)
            and any(abs(b - c) <= tolerancia or b > cruces[-1] for c in cruces))


def _cobertura(grupo: list[_Segmento], a: float, b: float) -> float:
    """Fracción de [a, b] cubierta por los segmentos del grupo."""
    if b <= a:
        return 0.0
    tramos = sorted((max(a, s.a), min(b, s.b)) for s in grupo if s.b > a and s.a < b)
    cubierto, fin = 0.0, a
    for x0, x1 in tramos:
        if x1 > fin:
            cubierto += x1 - max(x0, fin)
            fin = x1
    return cubierto / (b - a)


def detectar_grillas(tinta: np.ndarray) -> tuple[list[Grilla], np.ndarray]:
    """Tablas con bordes: (grillas en px, máscara de sus líneas para borrarlas antes del OCR)."""
    c = escaneo.cv2()
    h, w = tinta.shape
    largo_h, largo_v = max(30, w // 30), max(30, h // 45)
    tolerancia = max(8.0, 0.004 * max(h, w))
    # Un poco de tolerancia a líneas apenas inclinadas o cortadas por el ruido
    horiz = c.morphologyEx(c.dilate(tinta, c.getStructuringElement(c.MORPH_RECT, (1, 3))), c.MORPH_OPEN,
                           c.getStructuringElement(c.MORPH_RECT, (largo_h, 1)))
    ancha = c.dilate(tinta, c.getStructuringElement(c.MORPH_RECT, (3, 1)))
    vert = c.morphologyEx(ancha, c.MORPH_OPEN, c.getStructuringElement(c.MORPH_RECT, (1, largo_v)))
    grosor = max(12, int(0.006 * max(h, w)))
    horiz, vert = _solo_finas(horiz, True, grosor), _solo_finas(vert, False, grosor)
    # Las verticales de una tabla de un solo renglón son cortas (unos 0,2 pulgadas)
    corta = c.morphologyEx(ancha, c.MORPH_OPEN, c.getStructuringElement(c.MORPH_RECT, (1, max(20, h // 90))))
    vert = _verticales_cortas(_solo_finas(corta, False, grosor), vert, horiz, tolerancia)
    lineas = c.dilate(horiz | vert, c.getStructuringElement(c.MORPH_RECT, (7, 7)))
    n, _, stats, _ = c.connectedComponentsWithStats(lineas, connectivity=8)
    grillas, mascara = [], np.zeros_like(tinta)
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        if bw < 0.08 * w or bh < 0.015 * h:
            continue
        zona = (slice(max(0, y - 4), y + bh + 4), slice(max(0, x - 4), x + bw + 4))
        hs, etiquetas_h = _segmentos(horiz[zona], True)
        vs, etiquetas_v = _segmentos(vert[zona], False)
        filas = [g for g in _agrupar(hs, tolerancia) if max(s.b for s in g) - min(s.a for s in g) >= 0.05 * bw]
        cols = [g for g in _agrupar(vs, tolerancia) if max(s.b for s in g) - min(s.a for s in g) >= 0.3 * bh
                or len(filas) > 2]
        if not filas or not cols or len(filas) + len(cols) < 4:
            continue
        ys = [float(np.mean([s.pos for s in g])) for g in filas]
        xs = [float(np.mean([s.pos for s in g])) for g in cols]
        # Un borde de verdad va de una línea a otra; un trazo recto de una firma, no
        borde = max(tolerancia, 2.0 * grosor)
        filas = [g for g in filas if _de_borde_a_borde(g, xs, borde)]
        cols = [g for g in cols if _de_borde_a_borde(g, ys, borde)]
        # Un borde que no se ve (cortado por el borde de la hoja, muy tenue o tapado por una
        # sombra) se completa donde terminan juntas las líneas que lo cruzan
        filas = _completar_bordes(filas, cols, tolerancia, zona[0].stop - zona[0].start)
        cols = _completar_bordes(cols, filas, tolerancia, zona[1].stop - zona[1].start)
        if len(filas) < 2 or len(cols) < 2:
            continue
        ys = [float(np.mean([s.pos for s in g])) for g in filas]
        xs = [float(np.mean([s.pos for s in g])) for g in cols]
        nf, nc = len(ys) - 1, len(xs) - 1
        padre = list(range(nf * nc))

        def raiz(k: int) -> int:
            while padre[k] != k:
                padre[k] = padre[padre[k]]
                k = padre[k]
            return k

        verticales = [[_cobertura(cols[k], ys[f], ys[f + 1]) for f in range(nf)] for k in range(1, nc)]
        horizontales = [[_cobertura(filas[k], xs[col], xs[col + 1]) for col in range(nc)] for k in range(1, nf)]
        dx, dy = max(0, x - 4), max(0, y - 4)
        banda, corto = max(3, int(tolerancia * 0.5)), max(8, int(tolerancia * 1.6))
        rastro_v = [[_rastro(tinta, False, xs[k] + dx, ys[f] + dy, ys[f + 1] + dy, banda, corto) for f in range(nf)]
                    for k in range(1, nc)]
        rastro_h = [[_rastro(tinta, True, ys[k] + dy, xs[col] + dx, xs[col + 1] + dx, banda, corto)
                     for col in range(nc)] for k in range(1, nf)]
        for f in range(nf):
            for col in range(nc):
                if col + 1 < nc and not _hay_borde(verticales, rastro_v, col, f):
                    padre[raiz(f * nc + col)] = raiz(f * nc + col + 1)
                if f + 1 < nf and not _hay_borde(horizontales, rastro_h, f, col):
                    padre[raiz(f * nc + col)] = raiz((f + 1) * nc + col)
        grupos: dict[int, list[tuple[int, int]]] = {}
        for f in range(nf):
            for col in range(nc):
                grupos.setdefault(raiz(f * nc + col), []).append((f, col))
        celdas = []
        for miembros in grupos.values():
            f0, c0 = min(m[0] for m in miembros), min(m[1] for m in miembros)
            f1, c1 = max(m[0] for m in miembros), max(m[1] for m in miembros)
            celdas.append((f0, c0, f1 - f0 + 1, c1 - c0 + 1))
        celdas = _sin_solapes(celdas)
        ys, xs, celdas = _sin_lineas_sobrantes(ys, xs, celdas)
        dx, dy = max(0, x - 4), max(0, y - 4)
        grilla = Grilla([v + dy for v in ys], [v + dx for v in xs], sorted(celdas))
        # Un marco alrededor de toda la hoja no es una tabla
        caja = grilla.caja
        if len(celdas) == 1 and (caja[2] - caja[0]) * (caja[3] - caja[1]) > 0.6 * w * h:
            continue
        grillas.append(grilla)
        # Se borran solo las líneas de la tabla (no trazos de letras que parezcan líneas)
        aceptadas_h = [s.etiqueta for g in filas for s in g if s.etiqueta]
        aceptadas_v = [s.etiqueta for g in cols for s in g if s.etiqueta]
        mascara[zona][np.isin(etiquetas_h, aceptadas_h) | np.isin(etiquetas_v, aceptadas_v)] = 255
        _restos_de_lineas(tinta, mascara, grilla, tolerancia)
    mascara = c.dilate(mascara, c.getStructuringElement(c.MORPH_RECT, (5, 5)))
    return grillas, mascara


def _restos_de_lineas(tinta: np.ndarray, mascara: np.ndarray, grilla: Grilla, tolerancia: float) -> None:
    """Agrega a la máscara los pedazos cortos de las líneas de la tabla (los que quedan entre
    dos cortes, demasiado cortos para detectarlos solos): si no, parecen manchas al lado del
    texto. Solo los trazos alargados en la dirección de la línea, no las letras que la tocan."""
    c = escaneo.cv2()
    h, w = tinta.shape
    banda = max(3, int(tolerancia * 0.5))
    # Más largo que cualquier trazo de una letra (la barra de una «E», el palo de una «R»)
    largo = max(12, int(tolerancia * 3))
    x0, x1 = max(0, int(grilla.columnas[0]) - banda), min(w, int(grilla.columnas[-1]) + banda)
    y0, y1 = max(0, int(grilla.filas[0]) - banda), min(h, int(grilla.filas[-1]) + banda)
    for y in grilla.filas:
        a, b = max(0, int(y) - banda), min(h, int(y) + banda + 1)
        zona = np.ascontiguousarray(tinta[a:b, x0:x1])
        mascara[a:b, x0:x1] |= c.morphologyEx(zona, c.MORPH_OPEN, c.getStructuringElement(c.MORPH_RECT, (largo, 1)))
    for x in grilla.columnas:
        a, b = max(0, int(x) - banda), min(w, int(x) + banda + 1)
        zona = np.ascontiguousarray(tinta[y0:y1, a:b])
        mascara[y0:y1, a:b] |= c.morphologyEx(zona, c.MORPH_OPEN,
                                              c.getStructuringElement(c.MORPH_RECT, (1, int(largo * 1.5))))


def _hay_borde(lineas: list[list[float]], rastros: list[list[float]], k: int, i: int) -> bool:
    """¿Existe el tramo i de la línea interior k? (`lineas[k][i]`: qué fracción del tramo se
    detectó como línea; `rastros[k][i]`: qué fracción tiene al menos trazos rectos cortos en
    esa dirección, en la tinta original).

    Sí si se ve en al menos la mitad. Si no, las filas (o columnas) que se repiten con la
    misma estructura completan una línea cortada o borrada por un reflejo, siempre que quede
    un rastro de ella: la línea está en casi todos los demás tramos y, en ese mismo lugar,
    todas las otras líneas paralelas están. Sin ningún rastro, es una celda combinada."""
    tramos = lineas[k]
    if tramos[i] >= 0.5:
        return True
    if len(tramos) < 3 or max(tramos[i], rastros[k][i]) < 0.08:
        return False
    enteros = sum(c >= 0.5 for j, c in enumerate(tramos) if j != i)
    if enteros < 0.75 * (len(tramos) - 1):
        return False
    paralelas = [lineas[m][i] for m in range(len(lineas)) if m != k]
    return bool(paralelas) and all(c >= 0.5 for c in paralelas)


def _rastro(tinta: np.ndarray, horizontal: bool, pos: float, a: float, b: float, banda: int, largo: int) -> float:
    """Fracción de [a, b] donde hay trazos rectos de al menos `largo` px en la dirección de la
    línea, cerca de `pos` (los pedazos de una línea cortada; las letras casi no tienen)."""
    c = escaneo.cv2()
    a, b = int(max(0, a)), int(b)
    if b - a < 3:
        return 0.0
    if horizontal:
        zona = tinta[max(0, int(pos) - banda):int(pos) + banda + 1, a:b]
        kernel = (largo, 1)
    else:
        zona = tinta[a:b, max(0, int(pos) - banda):int(pos) + banda + 1]
        kernel = (1, largo)
    if zona.size == 0:
        return 0.0
    abierta = c.morphologyEx(np.ascontiguousarray(zona), c.MORPH_OPEN, c.getStructuringElement(c.MORPH_RECT, kernel))
    presente = abierta.any(axis=0 if horizontal else 1)
    return float(np.count_nonzero(presente)) / max(1, presente.size)


def _completar_bordes(paralelas: list[list[_Segmento]], cruzadas: list[list[_Segmento]], tolerancia: float,
                      zona_h: int) -> list[list[_Segmento]]:
    """Agrega el primer o el último borde si falta: al menos dos líneas cruzadas siguen más allá
    de la última paralela y terminan a la misma altura."""
    if len(cruzadas) < 2 or not paralelas:
        return paralelas
    paralelas = list(paralelas)
    a = min(min(s.a for s in g) for g in cruzadas)
    b = max(max(s.b for s in g) for g in cruzadas)
    primera, ultima = paralelas[0][0].pos, paralelas[-1][0].pos
    largo_minimo = max(4 * tolerancia, 0.02 * zona_h)
    for fin, adelante in ((ultima, True), (primera, False)):
        extremos = [max(s.b for s in g) if adelante else min(s.a for s in g) for g in cruzadas]
        pasan = sorted(e for e in extremos if (e - fin if adelante else fin - e) >= largo_minimo)
        if len(pasan) < 2 or pasan[-1] - pasan[0] > 3 * tolerancia:
            continue
        borde = _Segmento(float(pasan[-1] if adelante else pasan[0]), int(a), int(b))
        if adelante:
            paralelas.append([borde])
        else:
            paralelas.insert(0, [borde])
    return paralelas


def _sin_lineas_sobrantes(ys: list[float], xs: list[float], celdas: list[tuple[int, int, int, int]]):
    """Saca las líneas que no son borde de ninguna celda (un trazo recto de una firma, por
    ejemplo) y renumera las celdas."""
    filas_usadas = sorted({f for c in celdas for f in (c[0], c[0] + c[2])})
    cols_usadas = sorted({k for c in celdas for k in (c[1], c[1] + c[3])})
    nueva_f = {f: i for i, f in enumerate(filas_usadas)}
    nueva_c = {k: i for i, k in enumerate(cols_usadas)}
    celdas = [(nueva_f[c[0]], nueva_c[c[1]], nueva_f[c[0] + c[2]] - nueva_f[c[0]],
               nueva_c[c[1] + c[3]] - nueva_c[c[1]]) for c in celdas]
    return [ys[f] for f in filas_usadas], [xs[k] for k in cols_usadas], celdas


def _sin_solapes(celdas: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Las combinaciones no rectangulares se reemplazan por su rectángulo; si dos se pisan, se unen."""
    resultado = list(dict.fromkeys(celdas))
    cambio = True
    while cambio:
        cambio = False
        for i in range(len(resultado)):
            for j in range(i + 1, len(resultado)):
                a, b = resultado[i], resultado[j]
                if (a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]):
                    f0, c0 = min(a[0], b[0]), min(a[1], b[1])
                    f1, c1 = max(a[0] + a[2], b[0] + b[2]), max(a[1] + a[3], b[1] + b[3])
                    resultado[i] = (f0, c0, f1 - f0, c1 - c0)
                    del resultado[j]
                    cambio = True
                    break
            if cambio:
                break
    return resultado


# ---------------------------------------------------------------------- tinta que no es texto
def _mascara_texto(forma: tuple[int, int], palabras: list[PalabraTSV], alto_texto: float) -> np.ndarray:
    c = escaneo.cv2()
    mascara = np.zeros(forma, np.uint8)
    for p in palabras:
        x0, y0, x1, y1 = p.caja
        if p.conf >= CONF_TEXTO and (y1 - y0) <= 3.0 * alto_texto:
            c.rectangle(mascara, (x0 - 2, y0 - 2), (x1 + 2, y1 + 2), 255, -1)
    return mascara


def _tinta_de_color(color: np.ndarray) -> np.ndarray:
    c = escaneo.cv2()
    hsv = c.cvtColor(color, c.COLOR_BGR2HSV)
    mascara = ((hsv[:, :, 1] > 70) & (hsv[:, :, 2] < 235) & (hsv[:, :, 2] > 25)).astype(np.uint8) * 255
    return c.morphologyEx(mascara, c.MORPH_OPEN, c.getStructuringElement(c.MORPH_RECT, (3, 3)))


@dataclass
class _Region:
    caja: tuple[int, int, int, int]
    clase: str
    de_color: bool


def regiones_no_texto(tinta: np.ndarray, color: np.ndarray, palabras: list[PalabraTSV], alto_texto: float,
                      grillas: list[Grilla]) -> tuple[list[_Region], list[tuple[int, int, int, int]]]:
    """(regiones de imagen, viñetas): la tinta que no está cubierta por texto reconocido con
    confianza se agrupa en regiones (firmas, logos, sellos, manuscrito); las manchas chicas
    y macizas al principio de un renglón son viñetas."""
    c = escaneo.cv2()
    h, w = tinta.shape
    texto = _mascara_texto(tinta.shape, palabras, alto_texto)
    de_color = _tinta_de_color(color)
    resto = tinta & ~texto
    n, etiquetas, stats, _ = c.connectedComponentsWithStats(resto, connectivity=8)
    util = np.zeros_like(resto)
    vinetas = []
    minimo = max(12, int((0.12 * alto_texto) ** 2))
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < minimo:
            continue
        lado = max(bw, bh)
        if (0.15 * alto_texto <= lado <= 0.65 * alto_texto and abs(bw - bh) <= 0.35 * lado
                and area >= 0.55 * bw * bh):
            vinetas.append((int(x), int(y), int(x + bw), int(y + bh)))
            continue
        util[etiquetas == i] = 255
    candidatos = util | (de_color & ~texto) | (de_color & tinta)
    k = max(3, int(alto_texto * 0.8))
    unida = c.dilate(candidatos, c.getStructuringElement(c.MORPH_RECT, (k, max(3, int(alto_texto * 0.6)))))
    n, etiquetas, stats, _ = c.connectedComponentsWithStats(unida, connectivity=8)
    regiones = []
    for i in range(1, n):
        x, y, bw, bh, _ = stats[i]
        dentro = candidatos[y:y + bh, x:x + bw] & (etiquetas[y:y + bh, x:x + bw] == i).astype(np.uint8) * 255
        ys, xs = np.nonzero(dentro)
        if len(xs) == 0:
            continue
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()) + y, int(ys.max()) + y + 1
        x0, x1 = x0 + x, x1 + x
        bw, bh = x1 - x0, y1 - y0
        if bw < 1.5 * alto_texto or bh < 1.0 * alto_texto:
            continue
        tinta_total = int(np.count_nonzero(dentro))
        if tinta_total < 0.02 * bw * bh:
            continue
        color_zona = de_color[y0:y1, x0:x1]
        color_frac = np.count_nonzero(color_zona) / max(1, tinta_total)
        if color_frac > 0.4:
            # Texto de color reconocido con confianza: es texto, no una imagen
            fuera = np.count_nonzero(color_zona & ~texto[y0:y1, x0:x1])
            if fuera <= 0.3 * np.count_nonzero(color_zona):
                continue
        relleno = tinta_total / (bw * bh)
        if color_frac > 0.4 and 0.6 <= bw / bh <= 1.6 and relleno < 0.35:
            clase = "sello"
        elif color_frac > 0.4 and bw / bh >= 1.3 and relleno < 0.25:
            clase = "firma"                    # con birome azul
        elif color_frac > 0.4:
            clase = "logo"
        elif bw / bh >= 1.3 and relleno < 0.25:
            clase = "firma"
        else:
            clase = "imagen"
        caja = _ajustar_a_celda((x0, y0, x1, y1), grillas)
        regiones.append(_Region(caja, clase, color_frac > 0.4))
    return _unir_regiones(regiones, alto_texto * 0.5), vinetas


def _fraccion_dentro(caja, contenedor) -> float:
    """Qué parte del área de `caja` cae dentro de `contenedor`."""
    ancho = min(caja[2], contenedor[2]) - max(caja[0], contenedor[0])
    alto = min(caja[3], contenedor[3]) - max(caja[1], contenedor[1])
    area = (caja[2] - caja[0]) * (caja[3] - caja[1])
    return max(0, ancho) * max(0, alto) / area if area > 0 else 0.0


def _es_texto(region: _Region, tinta: np.ndarray, palabras: list[PalabraTSV], alto_texto: float,
              grillas: list[Grilla] = (), bordes: np.ndarray | None = None) -> bool:
    """Una «imagen» que en realidad es texto legible: conviene el texto, que se puede editar
    (si quedó mal leído, se marca como dudoso para revisarlo). Pasa con las palabras de baja
    confianza (no cubiertas por el texto seguro) y con las sombras o manchas que rodean al
    texto dentro de una tabla."""
    if region.de_color or region.clase in ("sello", "logo"):
        return False
    x0, y0, x1, y1 = region.caja
    zona = tinta[y0:y1, x0:x1] > 0
    if bordes is not None:
        zona &= ~bordes[y0:y1, x0:x1]                  # la sombra del borde no es parte de nada
    total = int(np.count_nonzero(zona))
    if total == 0:
        return True
    cubierta = np.zeros_like(zona)
    legibles = seguras = 0
    for p in palabras:
        letras = sum(ch.isalnum() for ch in p.texto)
        if (p.conf >= 30 and letras >= 2 and _fraccion_dentro(p.caja, region.caja) >= 0.5
                and p.caja[3] - p.caja[1] <= 2.5 * alto_texto):
            legibles += 1
            seguras += p.conf >= 85
            cubierta[max(0, p.caja[1] - y0):max(0, p.caja[3] - y0), max(0, p.caja[0] - x0):max(0, p.caja[2] - x0)] = True
    if not legibles:
        return False
    resto = zona & ~cubierta
    if np.count_nonzero(resto) <= 0.25 * total:
        # Una firma puede tener garabatos que el OCR lee como letras: hace falta texto seguro
        return region.clase != "firma" or seguras >= 1
    if region.clase == "firma":
        return False
    # En una tabla, si lo que rodea al texto legible es una mancha maciza (la sombra del borde
    # de la hoja), es una mancha; trazos finos (manuscrito) siguen siendo imagen
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if not any(g.caja[0] <= cx <= g.caja[2] and g.caja[1] <= cy <= g.caja[3] for g in grillas):
        return False
    ys, xs = np.nonzero(resto)
    caja_resto = (xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1)
    return np.count_nonzero(resto) >= 0.35 * caja_resto


def tinta_en_bordes(tinta: np.ndarray) -> np.ndarray:
    """La tinta conectada al borde de la hoja: sombras y bordes oscuros del escaneo o de la foto."""
    c = escaneo.cv2()
    n, etiquetas = c.connectedComponents(tinta, connectivity=8)
    if n <= 1:
        return np.zeros_like(tinta, dtype=bool)
    tocan = np.unique(np.concatenate([etiquetas[0], etiquetas[-1], etiquetas[:, 0], etiquetas[:, -1]]))
    tocan = tocan[tocan > 0]
    return np.isin(etiquetas, tocan)


def _releer_region(region: _Region, n: int, gris: np.ndarray, tinta: np.ndarray, bordes: np.ndarray,
                   palabras: list[PalabraTSV], alto_texto: float, grillas: list[Grilla], dpi: float,
                   tesseract: Path, tessdata: Path, carpeta: Path) -> list[PalabraTSV] | None:
    if region.de_color or region.clase in ("sello", "logo"):
        return None
    x0, y0, x1, y1 = (int(v) for v in region.caja)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if not any(g.caja[0] <= cx <= g.caja[2] and g.caja[1] <= cy <= g.caja[3] for g in grillas):
        return None
    if not any(p.conf >= 60 and _fraccion_dentro(p.caja, region.caja) >= 0.5 for p in palabras):
        return None
    # Las palabras que la región corta por la mitad (o toca) se leen enteras con ella
    holgura = 0.5 * alto_texto
    alrededor = (x0 - holgura, y0, x1 + holgura, y1)
    for p in palabras:
        if _fraccion_dentro(p.caja, alrededor) > 0.1 and p.caja[3] - p.caja[1] <= 2.5 * alto_texto:
            x0, y0, x1, y1 = min(x0, p.caja[0]), min(y0, p.caja[1]), max(x1, p.caja[2]), max(y1, p.caja[3])
    region.caja = (x0, y0, x1, y1)
    # Margen solo arriba y abajo: a los costados podría entrar un pedazo de la palabra vecina
    m = max(4, int(0.3 * alto_texto))
    h, w = gris.shape
    x0, y0, x1, y1 = max(0, x0 - 2), max(0, y0 - m), min(w, x1 + 2), min(h, y1 + m)
    recorte = gris[y0:y1, x0:x1].copy()
    recorte[bordes[y0:y1, x0:x1]] = 255
    tinta_zona = (tinta[y0:y1, x0:x1] > 0) & ~bordes[y0:y1, x0:x1]
    total = int(np.count_nonzero(tinta_zona))
    if total == 0:
        return None
    leidas = ocr_tsv(recorte, dpi, tesseract, tessdata, carpeta, psm=6)
    if not leidas or min(p.conf for p in leidas) < 70:
        return None
    cubierta = np.zeros_like(tinta_zona)
    for p in leidas:
        cubierta[max(0, p.caja[1] - 2):p.caja[3] + 2, max(0, p.caja[0] - 2):p.caja[2] + 2] = True
    if np.count_nonzero(tinta_zona & ~cubierta) > 0.2 * total:
        return None                                   # hay algo más que texto (una firma, un dibujo)
    return [PalabraTSV(p.texto, (p.caja[0] + x0, p.caja[1] + y0, p.caja[2] + x0, p.caja[3] + y0), p.conf,
                       (-1 - n, p.linea[1], p.linea[2])) for p in leidas]


def _ajustar_a_celda(caja, grillas: list[Grilla]):
    """Una firma dentro de una celda no puede salirse de ella (los bordes no son parte de la firma)."""
    cx, cy = (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2
    for g in grillas:
        for celda in g.celdas:
            x0, y0, x1, y1 = g.caja_celda(celda)
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                return (int(max(caja[0], x0 + 3)), int(max(caja[1], y0 + 3)), int(min(caja[2], x1 - 3)),
                        int(min(caja[3], y1 - 3)))
    return caja


def _unir_regiones(regiones: list[_Region], margen: float) -> list[_Region]:
    regiones = list(regiones)
    cambio = True
    while cambio:
        cambio = False
        for i in range(len(regiones)):
            for j in range(i + 1, len(regiones)):
                a, b = regiones[i].caja, regiones[j].caja
                if (a[0] - margen < b[2] and b[0] - margen < a[2] and a[1] - margen < b[3] and b[1] - margen < a[3]):
                    caja = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))
                    clase = regiones[i].clase if regiones[i].de_color or not regiones[j].de_color else regiones[j].clase
                    regiones[i] = _Region(caja, clase, regiones[i].de_color or regiones[j].de_color)
                    del regiones[j]
                    cambio = True
                    break
            if cambio:
                break
    return regiones


# ---------------------------------------------------------------------- medidas por palabra
def grosor_trazo(tinta: np.ndarray, caja) -> float:
    """Grosor medio del trazo (px) dentro de la caja: en un trazo fino el área es largo ×
    grosor y el perímetro, casi el doble del largo; así que grosor ≈ 2 · área / perímetro."""
    c = escaneo.cv2()
    x0, y0, x1, y1 = caja
    recorte = np.ascontiguousarray(tinta[max(0, y0):y1, max(0, x0):x1])
    if recorte.size == 0 or not np.any(recorte):
        return 0.0
    contornos, _ = c.findContours(recorte, c.RETR_LIST, c.CHAIN_APPROX_NONE)
    perimetro = sum(c.arcLength(k, True) for k in contornos)
    return float(2 * np.count_nonzero(recorte) / perimetro) if perimetro > 0 else 0.0


def caja_de_tinta(tinta: np.ndarray, caja) -> tuple[int, int, int, int]:
    """La caja de la palabra ajustada a su tinta (la del OCR suele quedar un poco grande)."""
    x0, y0, x1, y1 = caja
    zona = tinta[max(0, y0):y1, max(0, x0):x1]
    ys, xs = np.nonzero(zona)
    if len(xs) < 4:
        return caja
    return (x0 + int(xs.min()), y0 + int(ys.min()), x0 + int(xs.max()) + 1, y0 + int(ys.max()) + 1)


def color_palabra(color: np.ndarray, tinta: np.ndarray, caja) -> str:
    x0, y0, x1, y1 = caja
    zona = tinta[max(0, y0):y1, max(0, x0):x1] > 0
    if not np.any(zona):
        return "000000"
    bgr = np.median(color[max(0, y0):y1, max(0, x0):x1][zona], axis=0)
    if float(bgr.max() - bgr.min()) < 60:
        return "000000"                  # negro o gris: se deja negro
    b, g, r = (int(v) for v in bgr)
    return f"{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------- una página
@dataclass
class Muestra:
    """Recorte de una palabra reconocida con confianza, para elegir la fuente."""
    texto: str
    tinta: np.ndarray        # binaria (255 = tinta), recortada a la palabra
    trazo: float             # grosor del trazo / tamaño de letra estimado (para dejar afuera las negritas)
    grosor_px: float = 0.0   # grosor medido del trazo
    px_por_pt: float = 1.0


def analizar_pagina(img: np.ndarray, numero: int, ancho_pt: float, alto_pt: float, tesseract: Path,
                    tessdata: Path, carpeta: Path, enderezar: bool = True) -> tuple[AnalisisPagina, list[Muestra]]:
    c = escaneo.cv2()
    if enderezar:
        img = escaneo.enderezar(img, escaneo.angulo_inclinacion(img))
    h, w = img.shape[:2]
    k = ancho_pt / w                                   # px → pt
    dpi = 72.0 / k
    gris = escaneo.mejorar(img, escaneo.Mejora.GRISES)
    color = escaneo.mejorar(img, escaneo.Mejora.COLOR) if img.ndim == 3 else c.cvtColor(gris, c.COLOR_GRAY2BGR)
    _, tinta = c.threshold(gris, 0, 255, c.THRESH_BINARY_INV + c.THRESH_OTSU)
    grillas_px, lineas = detectar_grillas(tinta)
    para_ocr = gris.copy()
    para_ocr[lineas > 0] = 255
    tinta_sin_lineas = tinta & ~lineas
    # Las sombras pegadas al borde de la hoja confunden al OCR con el texto de al lado
    bordes = tinta_en_bordes(tinta_sin_lineas)
    para_ocr[bordes] = 255
    palabras_tsv = ocr_tsv(para_ocr, dpi, tesseract, tessdata, carpeta)

    seguras = [p.caja[3] - p.caja[1] for p in palabras_tsv if p.conf >= CONF_TEXTO]
    alto_texto = float(np.median(seguras)) if seguras else 0.012 * h
    regiones, vinetas = regiones_no_texto(tinta_sin_lineas, color, palabras_tsv, alto_texto, grillas_px)
    regiones = [r for r in regiones
                if not _es_texto(r, tinta_sin_lineas, palabras_tsv, alto_texto, grillas_px, bordes)]
    # Lo que quedó como imagen dentro de una tabla pero tiene texto: se vuelve a leer solo
    # (sin la sombra ni lo que lo rodea); si sale legible, va como texto y no como imagen
    quedan = []
    for n, r in enumerate(regiones):
        releidas = _releer_region(r, n, para_ocr, tinta_sin_lineas, bordes, palabras_tsv, alto_texto, grillas_px,
                                  dpi, tesseract, tessdata, carpeta)
        if releidas is None:
            quedan.append(r)
        else:
            palabras_tsv = [p for p in palabras_tsv if _fraccion_dentro(p.caja, r.caja) < 0.5] + releidas
    regiones = quedan
    # Para medir las palabras (tamaño, negrita), sin la sombra del borde de la hoja
    tinta_palabras = np.where(bordes, 0, tinta_sin_lineas).astype(np.uint8)

    # Imágenes recortadas del escaneo (con un pequeño margen)
    imagenes = []
    for n, r in enumerate(regiones):
        x0, y0, x1, y1 = r.caja
        m = max(2, int(alto_texto * 0.15))
        x0, y0, x1, y1 = max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)
        archivo = carpeta / f"imagen_{numero + 1}_{n + 1}.png"
        _guardar_png(color[y0:y1, x0:x1], archivo)
        imagenes.append((RegionImagen((x0 * k, y0 * k, x1 * k, y1 * k), str(archivo), r.clase), r))

    palabras, muestras = [], []
    for i, p in enumerate(palabras_tsv):
        alto = p.caja[3] - p.caja[1]
        if any(_fraccion_dentro(p.caja, r.caja) >= 0.5 for _, r in imagenes):
            continue                                   # va en la imagen: no se repite como texto
        if alto > 4 * alto_texto and p.conf < CONF_TEXTO:
            continue                                   # una mancha grande que el OCR leyó como letra
        ajustada = caja_de_tinta(tinta_palabras, p.caja)
        caja_pt = tuple(round(v * k, 2) for v in ajustada)
        palabra = Palabra(f"p{numero}-{i}", p.texto, caja_pt, round(p.conf, 1),
                          color=color_palabra(color, tinta_palabras, ajustada))
        grosor = grosor_trazo(tinta_palabras, ajustada)
        palabra.trazo = round(grosor * k, 4)
        palabras.append(PalabraOCR(palabra, p.linea))
        tamano = tamano_palabra(palabra)
        if (p.conf >= 90 and len(p.texto) >= 4 and p.texto.isalpha() and len(muestras) < 40
                and grosor > 0 and tamano):
            x0, y0, x1, y1 = ajustada
            muestras.append(Muestra(p.texto, tinta_palabras[y0:y1, x0:x1].copy(), palabra.trazo / tamano,
                                    grosor, 1 / k))

    grillas = [Grilla([y * k for y in g.filas], [x * k for x in g.columnas], g.celdas) for g in grillas_px]
    revision = carpeta / f"pagina_{numero + 1}.jpg"
    factor = min(1.0, DPI_REVISION / dpi)
    vista = c.resize(color, (max(1, round(w * factor)), max(1, round(h * factor))),
                     interpolation=c.INTER_AREA) if factor < 1 else color
    _guardar_jpg(vista, revision)
    analisis = AnalisisPagina(numero, ancho_pt, alto_pt, str(revision), palabras, grillas,
                              [i for i, _ in imagenes], [(v[0] * k, v[1] * k, v[2] * k, v[3] * k) for v in vinetas])
    return analisis, muestras


def _guardar_png(img: np.ndarray, ruta: Path) -> None:
    from PIL import Image
    Image.fromarray(img[:, :, ::-1] if img.ndim == 3 else img).save(ruta, optimize=True)


def _guardar_jpg(img: np.ndarray, ruta: Path) -> None:
    from PIL import Image
    Image.fromarray(img[:, :, ::-1] if img.ndim == 3 else img).save(ruta, quality=85)


# ---------------------------------------------------------------------- fuente
CANDIDATAS = {
    "sans": ["Arial", "Liberation Sans", "Calibri", "Carlito", "Verdana", "Tahoma", "Segoe UI"],
    "serif": ["Times New Roman", "Liberation Serif", "Cambria", "Caladea", "Georgia", "Book Antiqua"],
    "mono": ["Courier New", "Liberation Mono", "Consolas"],
}


def fuentes_disponibles() -> list[Fuente]:
    """Las candidatas que están instaladas en Windows o vienen con PDF Studio."""
    from pdf_studio.core.fuentes import indice_incluidas, indice_sistema, separar_nombre

    disponibles = []
    vistas = set()
    for clase, familias in CANDIDATAS.items():
        for familia in familias:
            for origen, indice in (("sistema", indice_sistema()), ("incluida", indice_incluidas())):
                try:
                    caras = indice.familia(familia)
                except Exception:
                    caras = []
                por_estilo = {separar_nombre(f"{c.familia},{c.estilo}")[1]: c for c in caras}
                regular = por_estilo.get("")
                if regular is None or familia.lower() in vistas:
                    continue
                negrita = por_estilo.get("bold", regular)
                disponibles.append(Fuente(familia, clase, regular.archivo, negrita.archivo, regular.indice,
                                          negrita.indice, origen))
                vistas.add(familia.lower())
    return disponibles


def factores_de_fuente(fuente: Fuente) -> dict:
    """Proporciones de la letra (altas, «x», patas, mayúsculas con tilde) de esa fuente."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(fuente.regular, 200, index=fuente.indice_regular)
    except OSError:
        return dict(FACTORES)

    def alto(texto: str) -> float:
        caja = f.getbbox(texto, anchor="ls")
        return -caja[1] / 200

    def pata(texto: str) -> float:
        return f.getbbox(texto, anchor="ls")[3] / 200

    return {"alta": (alto("H") + alto("d")) / 2, "x": alto("x"), "pata": pata("p"), "acento": alto("Á")}


def _render(fuente_pil, texto: str) -> np.ndarray | None:
    from PIL import Image, ImageDraw
    caja = fuente_pil.getbbox(texto)
    w, h = int(caja[2] - caja[0]) + 4, int(caja[3] - caja[1]) + 4
    if w <= 4 or h <= 4:
        return None
    lienzo = Image.new("L", (w, h), 0)
    ImageDraw.Draw(lienzo).text((2 - caja[0], 2 - caja[1]), texto, font=fuente_pil, fill=255)
    m = np.asarray(lienzo)
    ys, xs = np.nonzero(m > 127)
    if len(xs) == 0:
        return None
    return (m[ys.min():ys.max() + 1, xs.min():xs.max() + 1] > 127).astype(np.uint8) * 255


def parecido(fuente: Fuente, muestras: list[Muestra]) -> float:
    """Qué tan bien explica la fuente las palabras del escaneo: forma de las letras
    (coincidencia de la tinta) y proporción ancho/alto de cada palabra."""
    from PIL import ImageFont
    c = escaneo.cv2()
    try:
        f = ImageFont.truetype(fuente.regular, 64, index=fuente.indice_regular)
    except OSError:
        return -math.inf
    puntajes = []
    for m in muestras:
        recorte = m.tinta
        ys, xs = np.nonzero(recorte)
        if len(xs) < 20:
            continue
        recorte = recorte[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
        render = _render(f, m.texto)
        if render is None:
            continue
        hs, ws = recorte.shape
        hr, wr = render.shape
        proporcion = math.log((ws / hs) / (wr / hr))
        ajustado = c.resize(render, (ws, hs), interpolation=c.INTER_AREA) > 127
        escaneada = recorte > 0
        union_ = np.count_nonzero(ajustado | escaneada)
        iou = np.count_nonzero(ajustado & escaneada) / union_ if union_ else 0.0
        puntajes.append(iou - 1.5 * abs(proporcion))
    return float(np.mean(puntajes)) if puntajes else -math.inf


def trazo_de_fuente(fuente: Fuente) -> tuple[float, float] | None:
    """Grosor medio del trazo de la regular y de la negrita, en fracciones del tamaño de letra
    (se mide igual que en el escaneo: 2 · área / perímetro)."""
    import statistics

    from PIL import ImageFont

    def medir(archivo: str, indice: int) -> float | None:
        try:
            f = ImageFont.truetype(archivo, 48, index=indice)
        except OSError:
            return None
        valores = []
        for texto in ("Revisado", "control", "Fecha", "manejo", "Preparado", "temperatura"):
            r = _render(f, texto)
            if r is not None:
                valores.append(grosor_trazo(r, (0, 0, r.shape[1], r.shape[0])) / 48)
        return statistics.median(valores) if valores else None

    regular = medir(fuente.regular, fuente.indice_regular)
    negrita = medir(fuente.negrita, fuente.indice_negrita) if fuente.negrita != fuente.regular else None
    if not regular:
        return None
    return regular, negrita or regular * 1.45


def medidor_de_fuente(fuente: Fuente):
    """Función texto → (alto sobre la línea de base, profundidad bajo la línea de base), en
    fracciones del tamaño de letra, medidas con la fuente elegida."""
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(fuente.regular, 1000, index=fuente.indice_regular)
    except OSError:
        return None
    cache: dict[str, tuple[float, float] | None] = {}

    def medir(texto: str):
        if texto not in cache:
            try:
                x0, y0, x1, y1 = f.getbbox(texto, anchor="ls")
                cache[texto] = (max(0.0, -y0) / 1000, max(0.0, y1) / 1000) if x1 > x0 else None
            except Exception:
                cache[texto] = None
        return cache[texto]

    return medir


def estimar_exceso(analisis: list[AnalisisPagina], medidor) -> float:
    """Cuánto engorda la tinta el escaneo (pt): el desenfoque y el umbral hacen las letras más
    altas de lo que son. En cada renglón, la diferencia de altura entre las palabras con letras
    altas («del», «Este») y las que no tienen («en», «una») depende solo del tamaño de letra:
    de ahí sale el tamaño y, con él, lo que sobra."""
    import statistics

    excesos = []
    for a in analisis:
        renglones: dict[tuple, list[Palabra]] = {}
        for po in a.palabras:
            renglones.setdefault(po.linea, []).append(po.palabra)
        for palabras in renglones.values():
            altas, bajas = [], []
            for p in palabras:
                if len(p.texto) < 2 or not p.texto.isalpha() or p.conf < 85:
                    continue
                medida = medidor(p.texto)
                if medida is None or medida[1] > 0.02:
                    continue                    # sin letras con «patas»
                alto, relativo = p.caja[3] - p.caja[1], sum(medida)
                if medida[0] >= 0.68:
                    altas.append((alto, relativo))
                elif medida[0] < 0.6:
                    bajas.append((alto, relativo))
            if len(altas) >= 2 and bajas:
                ha, Ha = statistics.median(x[0] for x in altas), statistics.median(x[1] for x in altas)
                hb, Hb = statistics.median(x[0] for x in bajas), statistics.median(x[1] for x in bajas)
                if Ha - Hb > 0.1 and ha > hb:
                    tamano = (ha - hb) / (Ha - Hb)
                    excesos.append(ha - tamano * Ha)
    if len(excesos) < 3:
        return 0.0
    # El desenfoque agrega lo mismo a letras grandes y chicas: es una medida fija
    return round(max(0.0, min(2.0, statistics.median(excesos))), 3)


def elegir_fuente(muestras: list[Muestra], disponibles: list[Fuente] | None = None) -> tuple[Fuente | None, list[Fuente]]:
    """(la más parecida, las demás de la misma clase de más a menos parecida). Se prefieren las
    instaladas en Windows (Word las necesita) sobre las incluidas con PDF Studio."""
    disponibles = fuentes_disponibles() if disponibles is None else disponibles
    if not disponibles:
        return None, []
    trazos = sorted(m.trazo for m in muestras)
    if trazos:
        limite = trazos[len(trazos) // 2] * 1.2
        muestras = [m for m in muestras if m.trazo <= limite]       # sin las negritas
    if not muestras:
        sans = [f for f in disponibles if f.clase == "sans"]
        return (sans or disponibles)[0], (sans or disponibles)[1:]
    puntajes = {id(f): parecido(f, muestras) for f in disponibles}
    mejor = max(disponibles, key=lambda f: puntajes[id(f)])
    misma_clase = sorted((f for f in disponibles if f.clase == mejor.clase), key=lambda f: -puntajes[id(f)])
    # Una instalada casi igual de parecida gana a una incluida (Word no tiene las incluidas)
    if mejor.origen == "incluida":
        instaladas = [f for f in misma_clase if f.origen == "sistema"
                      and puntajes[id(f)] >= puntajes[id(mejor)] - 0.03]
        if instaladas:
            mejor = instaladas[0]
    return mejor, [f for f in misma_clase if f is not mejor]


# ---------------------------------------------------------------------- entradas
def imagen_de_pagina(pag, dpi: int = DPI_ANALISIS) -> np.ndarray:
    """La página renderizada (BGR) a `dpi`, con un tope de tamaño."""
    lado = max(pag.rect.width, pag.rect.height) / 72.0 * dpi
    if lado > LADO_MAXIMO:
        dpi = int(dpi * LADO_MAXIMO / lado)
    pix = pag.get_pixmap(dpi=dpi, alpha=False)
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
    return np.ascontiguousarray(img[:, :, :3][:, :, ::-1])


def _a_resolucion(img: np.ndarray, ancho_pt: float, alto_pt: float) -> np.ndarray:
    """Una foto, llevada a la resolución de análisis para el tamaño de página que le toca."""
    c = escaneo.cv2()
    h, w = img.shape[:2]
    objetivo_w = ancho_pt / 72.0 * DPI_ANALISIS
    objetivo_h = alto_pt / 72.0 * DPI_ANALISIS
    if objetivo_w > LADO_MAXIMO or objetivo_h > LADO_MAXIMO:
        f = LADO_MAXIMO / max(objetivo_w, objetivo_h)
        objetivo_w, objetivo_h = objetivo_w * f, objetivo_h * f
    tam = (max(1, round(objetivo_w)), max(1, round(objetivo_h)))
    interpolacion = c.INTER_AREA if objetivo_w < w else c.INTER_CUBIC
    return c.resize(img, tam, interpolation=interpolacion)


def contar_paginas(ruta: Path, contrasena: str | None) -> int:
    import pymupdf
    try:
        with pymupdf.open(ruta) as doc:
            if doc.needs_pass and not doc.authenticate(contrasena or ""):
                raise Limitacion("El PDF está protegido con contraseña.")
            return doc.page_count
    except ErrorDocumento:
        raise
    except Exception as e:
        raise ErrorDocumento(f"No se pudo abrir «{Path(ruta).name}».") from e


def paginas_de_pdf(ruta: Path, contrasena: str | None, paginas: list[int] | None):
    """(imagen, ancho, alto) de cada página pedida."""
    import pymupdf
    try:
        doc = pymupdf.open(ruta)
    except Exception as e:
        raise ErrorDocumento(f"No se pudo abrir «{Path(ruta).name}».") from e
    try:
        if doc.needs_pass and not doc.authenticate(contrasena or ""):
            raise Limitacion("El PDF está protegido con contraseña.")
        elegidas = paginas if paginas is not None else list(range(doc.page_count))
        for n in elegidas:
            pag = doc[n]
            yield imagen_de_pagina(pag), pag.rect.width, pag.rect.height
    finally:
        doc.close()


def paginas_de_fotos(fotos: list[escaneo.AjusteFoto], opciones: escaneo.OpcionesEscaneo):
    """(imagen, ancho, alto) de cada foto, recortada y con la perspectiva corregida."""
    sin_mejora = escaneo.OpcionesEscaneo(opciones.recortar, opciones.enderezar, escaneo.Mejora.NINGUNA,
                                         opciones.tamano, opciones.dpi)
    for foto in fotos:
        hecha = escaneo.procesar(escaneo.leer_imagen(foto.ruta, foto.rotacion), foto, sin_mejora)
        h, w = hecha.imagen.shape[:2]
        ancho, alto, llenar = escaneo.medida_de_pagina(w, h, opciones.tamano)
        if not llenar:
            # Otra proporción: la página toma la de la foto, con el ancho elegido
            alto = ancho * h / w
        yield _a_resolucion(hecha.imagen, ancho, alto), ancho, alto


# ---------------------------------------------------------------------- todo junto
def convertir(paginas, tesseract: Path, tessdata: Path, carpeta: Path, total: int | None = None,
              progreso=None, enderezar: bool = True) -> Reconstruccion:
    """Analiza las páginas (iterable de (imagen, ancho, alto)) y arma la reconstrucción.
    `progreso(hechas, total)` se llama después de cada página."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    analisis, muestras = [], []
    for n, (img, ancho, alto) in enumerate(paginas):
        if n >= MAXIMO_PAGINAS:
            raise Limitacion(f"El documento tiene más de {MAXIMO_PAGINAS} páginas: convertilo por partes.")
        a, m = analizar_pagina(img, n, ancho, alto, tesseract, tessdata, carpeta, enderezar)
        analisis.append(a)
        muestras += m
        if progreso is not None:
            progreso(n + 1, total or n + 1)
    if not analisis:
        raise Limitacion("No hay páginas para convertir.")
    if not any(a.palabras for a in analisis):
        raise Limitacion("No se reconoció texto en las páginas: puede que la imagen no tenga letras legibles "
                         "o que la resolución sea muy baja.")
    fuente, alternativas = elegir_fuente(muestras)
    factores = dict(FACTORES)
    if fuente is not None:
        factores = factores_de_fuente(fuente)
        medidor = medidor_de_fuente(fuente)
        if medidor is not None:
            factores["medidor"] = medidor
            factores["exceso"] = estimar_exceso(analisis, medidor)
        trazo = trazo_de_fuente(fuente)
        if trazo is not None:
            factores["trazo_fuente"] = trazo
    rec = maquetar(analisis, factores)
    rec.fuente, rec.alternativas = fuente, alternativas
    if fuente is None:
        rec.avisos.append("No se encontró ninguna fuente para el documento: se usará la predeterminada de Word.")
    elif fuente.origen == "incluida":
        rec.avisos.append(f"La fuente más parecida, {fuente.familia}, viene con PDF Studio pero no está "
                          "instalada en Windows: el PDF la lleva incluida, pero Word va a mostrar otra hasta "
                          "que la instales.")
    try:
        ortografia.marcar_sospechosas(rec)
    except ortografia.SinCorrector as e:
        rec.avisos.append(f"{e} Solo se marcan como dudosas las palabras con baja confianza del OCR.")
    dudosas = len(rec.dudosas())
    if dudosas:
        rec.avisos.append(f"Hay {dudosas} palabra{'s' if dudosas != 1 else ''} para revisar (baja confianza o fuera del diccionario).")
    return rec
