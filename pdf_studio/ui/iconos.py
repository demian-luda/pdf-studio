"""Íconos Lucide (SVG incluidos, licencia ISC) coloreados según el tema.

Los SVG usan stroke="currentColor": se reemplaza por el color del tema y se
renderizan en varios tamaños. Al cambiar el tema, `refrescar()` vuelve a
pintar todos los íconos registrados.
"""
from __future__ import annotations

import weakref
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from pdf_studio.config import DIR_RECURSOS
from pdf_studio.ui.tema import Paleta, gestor

DIR_ICONOS = DIR_RECURSOS / "iconos"
TAMANOS = (16, 20, 24, 32, 40, 48)


@lru_cache(maxsize=None)
def _svg(nombre: str) -> str:
    return (DIR_ICONOS / f"{nombre}.svg").read_text(encoding="utf-8")


def existe(nombre: str) -> bool:
    return (DIR_ICONOS / f"{nombre}.svg").is_file()


def _pixmap(nombre: str, color: str, lado: int, dpr: float = 2.0) -> QPixmap:
    datos = _svg(nombre).replace("currentColor", color)
    render = QSvgRenderer(QByteArray(datos.encode("utf-8")))
    px = QPixmap(round(lado * dpr), round(lado * dpr))
    px.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(px)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    render.render(pintor, QRectF(0, 0, px.width(), px.height()))
    pintor.end()
    px.setDevicePixelRatio(dpr)
    return px


def crear_icono(nombre: str, paleta: Paleta | None = None, primario: bool = False) -> QIcon:
    """`primario`: el ícono va sobre el color de acento (botón principal) y usa su color de texto."""
    p = paleta or gestor().paleta
    normal = p.sobre_acento if primario else p.texto
    icono = QIcon()
    for lado in TAMANOS:
        icono.addPixmap(_pixmap(nombre, normal, lado), QIcon.Mode.Normal, QIcon.State.Off)
        icono.addPixmap(_pixmap(nombre, p.acento, lado), QIcon.Mode.Normal, QIcon.State.On)
        icono.addPixmap(_pixmap(nombre, p.texto_suave, lado), QIcon.Mode.Disabled, QIcon.State.Off)
        icono.addPixmap(_pixmap(nombre, p.texto_suave, lado), QIcon.Mode.Disabled, QIcon.State.On)
    return icono


# Registro de objetos con ícono (acciones, botones) para repintarlos al cambiar el tema
_registrados: "weakref.WeakKeyDictionary[object, str]" = weakref.WeakKeyDictionary()


def _es_primario(objeto) -> bool:
    try:
        return bool(objeto.property("primario"))
    except (AttributeError, RuntimeError):
        return False


def asignar(objeto, nombre: str) -> None:
    """objeto: QAction o QAbstractButton. En botones primarios el ícono toma el color del texto
    sobre el acento (si no, quedaría oscuro sobre azul)."""
    _registrados[objeto] = nombre
    objeto.setIcon(crear_icono(nombre, primario=_es_primario(objeto)))


def nombre_de(objeto) -> str | None:
    return _registrados.get(objeto)


def refrescar() -> None:
    for objeto, nombre in list(_registrados.items()):
        try:
            objeto.setIcon(crear_icono(nombre, primario=_es_primario(objeto)))
        except RuntimeError:
            pass  # el objeto de Qt ya se destruyó
