"""Fotos sintéticas de documentos para probar la importación como PDF escaneado: una hoja
A4 con texto, girada, en perspectiva y con luz despareja, sobre un fondo con textura."""
from __future__ import annotations

import cv2
import numpy as np

TITULO = "INFORME MENSUAL"
LINEAS = ["El presupuesto anual fue aprobado", "por la comision directiva en la", "reunion del dia quince de marzo.",
          "Se adjuntan los comprobantes de", "pago y el detalle de los gastos."]


def hoja(ancho: int = 1000, proporcion: float = 297 / 210) -> np.ndarray:
    """Hoja blanca con un título y varias líneas de texto (BGR)."""
    alto = int(round(ancho * proporcion))
    img = np.full((alto, ancho, 3), 250, np.uint8)
    escala = ancho / 1000
    cv2.putText(img, TITULO, (int(90 * escala), int(160 * escala)), cv2.FONT_HERSHEY_DUPLEX, 2.0 * escala,
                (20, 20, 20), max(1, int(4 * escala)), cv2.LINE_AA)
    for i, texto in enumerate(LINEAS):
        y = int((300 + i * 90) * escala)
        cv2.putText(img, texto, (int(90 * escala), y), cv2.FONT_HERSHEY_SIMPLEX, 1.25 * escala, (30, 30, 30),
                    max(1, int(3 * escala)), cv2.LINE_AA)
    cv2.rectangle(img, (int(90 * escala), int(900 * escala)), (int(910 * escala), int(1250 * escala)),
                  (60, 60, 60), max(1, int(3 * escala)))
    return img


def foto(angulo: float = 8.0, perspectiva: float = 0.08, lienzo: tuple[int, int] = (1700, 2100),
         sombra: bool = True, semilla: int = 1, papel: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(foto BGR, esquinas reales del papel en la foto: arriba-izq, arriba-der, abajo-der, abajo-izq)."""
    rng = np.random.default_rng(semilla)
    papel = hoja() if papel is None else papel
    ph, pw = papel.shape[:2]
    lw, lh = lienzo
    # Fondo: madera oscura con ruido
    fondo = np.zeros((lh, lw, 3), np.float32)
    fondo[:] = (40, 70, 110)
    fondo += rng.normal(0, 12, (lh, lw, 1)).astype(np.float32)
    vetas = (np.sin(np.linspace(0, 40, lw))[None, :, None] * 10).astype(np.float32)
    fondo = np.clip(fondo + vetas, 0, 255).astype(np.uint8)
    # Esquinas destino: la hoja centrada, más angosta arriba (perspectiva) y girada
    escala = 0.78 * min(lw / pw, lh / ph)
    w, h = pw * escala, ph * escala
    cx, cy = lw / 2, lh / 2
    base = np.array([(-w / 2 * (1 - perspectiva), -h / 2), (w / 2 * (1 - perspectiva), -h / 2),
                     (w / 2, h / 2), (-w / 2, h / 2)], np.float32)
    rad = np.deg2rad(angulo)
    rot = np.array([[np.cos(rad), -np.sin(rad)], [np.sin(rad), np.cos(rad)]], np.float32)
    esquinas = (base @ rot.T) + (cx, cy)
    origen = np.array([(0, 0), (pw - 1, 0), (pw - 1, ph - 1), (0, ph - 1)], np.float32)
    m = cv2.getPerspectiveTransform(origen, esquinas.astype(np.float32))
    proyectada = cv2.warpPerspective(papel, m, (lw, lh), flags=cv2.INTER_LINEAR)
    mascara = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), m, (lw, lh))
    img = np.where(mascara[:, :, None] > 127, proyectada, fondo)
    if sombra:
        # Luz despareja: más oscuro hacia una esquina
        yy, xx = np.mgrid[0:lh, 0:lw].astype(np.float32)
        luz = 1.0 - 0.35 * (xx / lw) * (yy / lh)
        img = np.clip(img.astype(np.float32) * luz[:, :, None], 0, 255).astype(np.uint8)
    return img, esquinas.astype(np.float32)


def escaneo_plano(angulo: float = 3.0) -> np.ndarray:
    """Un escaneo de cama plana: la hoja ocupa toda la imagen, apenas torcida."""
    papel = hoja()
    h, w = papel.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angulo, 1.0)
    return cv2.warpAffine(papel, m, (w, h), borderValue=(250, 250, 250))


def guardar(img: np.ndarray, ruta, calidad: int = 92) -> None:
    from PIL import Image
    Image.fromarray(img[:, :, ::-1]).save(ruta, quality=calidad)
