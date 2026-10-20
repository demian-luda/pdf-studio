"""Genera el ícono de Windows (.ico multi-resolución) y PNGs a partir de recursos/logo.svg.

Uso:  .\\.venv\\Scripts\\python.exe herramientas\\generar_iconos_app.py
"""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QBuffer, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

RECURSOS = Path(__file__).resolve().parent.parent / "pdf_studio" / "recursos"
TAMANOS = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def renderizar(svg: bytes, lado: int) -> Image.Image:
    render = QSvgRenderer(QByteArray(svg))
    img = QImage(lado, lado, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(img)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    render.render(pintor, QRectF(0, 0, lado, lado))
    pintor.end()
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main() -> None:
    _app = QGuiApplication(sys.argv)
    svg = (RECURSOS / "logo.svg").read_bytes()
    imagenes = {lado: renderizar(svg, lado) for lado in TAMANOS}
    # Cada tamaño se renderiza por separado (más nítido que achicar el de 256)
    grande = imagenes[256]
    grande.save(RECURSOS / "logo.ico", format="ICO", sizes=[(l, l) for l in TAMANOS],
                append_images=[imagenes[l] for l in TAMANOS if l != 256])
    imagenes[256].save(RECURSOS / "logo_256.png")
    imagenes[64].save(RECURSOS / "logo_64.png")
    print("Generados logo.ico, logo_256.png y logo_64.png en", RECURSOS)


if __name__ == "__main__":
    main()
