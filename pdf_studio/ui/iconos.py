"""Íconos Lucide (SVG incluidos, licencia ISC) coloreados según el tema.

Los SVG usan stroke="currentColor": se reemplaza por el color del tema. Cada tamaño se
dibuja recién cuando Qt lo pide (`_MotorIcono`) y queda guardado: dibujarlos todos de
antemano (6 tamaños × 4 estados × ~90 íconos) demoraba medio segundo el arranque.
Al cambiar el tema, `refrescar()` vuelve a asignar todos los íconos registrados.
"""
from __future__ import annotations

import weakref
from functools import lru_cache

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
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


@lru_cache(maxsize=None)
def _renderizador(nombre: str, color: str) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(_svg(nombre).replace("currentColor", color).encode("utf-8")))


def _pixmap(nombre: str, color: str, lado: int, dpr: float = 2.0) -> QPixmap:
    px = QPixmap(round(lado * dpr), round(lado * dpr))
    px.fill(Qt.GlobalColor.transparent)
    pintor = QPainter(px)
    pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
    _renderizador(nombre, color).render(pintor, QRectF(0, 0, px.width(), px.height()))
    pintor.end()
    px.setDevicePixelRatio(dpr)
    return px


class _MotorIcono(QIconEngine):
    """Dibuja el SVG en el tamaño y el estado que Qt pida, una sola vez por combinación."""

    def __init__(self, nombre: str, normal: str, activo: str, apagado: str):
        super().__init__()
        self.nombre = nombre
        self.colores = (normal, activo, apagado)
        self._cache: dict[tuple, QPixmap] = {}

    def _color(self, modo: QIcon.Mode, estado: QIcon.State) -> str:
        normal, activo, apagado = self.colores
        if modo == QIcon.Mode.Disabled:
            return apagado
        return activo if estado == QIcon.State.On else normal

    def scaledPixmap(self, tamano: QSize, modo: QIcon.Mode, estado: QIcon.State, escala: float) -> QPixmap:
        lado = max(1, min(tamano.width(), tamano.height()))
        escala = max(1.0, float(escala))
        clave = (lado, round(escala * 100), self._color(modo, estado))
        px = self._cache.get(clave)
        if px is None:
            px = self._cache[clave] = _pixmap(self.nombre, clave[2], lado, escala)
        return px

    def pixmap(self, tamano: QSize, modo: QIcon.Mode, estado: QIcon.State) -> QPixmap:
        # Sin escala explícita: se dibuja al doble, como antes, para pantallas de alta densidad
        return self.scaledPixmap(tamano, modo, estado, 2.0)

    def paint(self, pintor: QPainter, rect: QRect, modo: QIcon.Mode, estado: QIcon.State) -> None:
        dpr = pintor.device().devicePixelRatioF() if pintor.device() is not None else 1.0
        pintor.drawPixmap(rect, self.scaledPixmap(rect.size(), modo, estado, dpr))

    def availableSizes(self, modo: QIcon.Mode = QIcon.Mode.Normal,
                       estado: QIcon.State = QIcon.State.Off) -> list[QSize]:
        return [QSize(lado, lado) for lado in TAMANOS]

    def actualSize(self, tamano: QSize, modo: QIcon.Mode, estado: QIcon.State) -> QSize:
        return tamano

    def isNull(self) -> bool:
        return False

    def clone(self) -> "QIconEngine":
        return _MotorIcono(self.nombre, *self.colores)

    def key(self) -> str:
        return "pdfstudio-lucide"


def crear_icono(nombre: str, paleta: Paleta | None = None, primario: bool = False) -> QIcon:
    """`primario`: el ícono va sobre el color de acento (botón principal) y usa su color de texto."""
    p = paleta or gestor().paleta
    normal = p.sobre_acento if primario else p.texto
    return QIcon(_MotorIcono(nombre, normal, p.acento, p.texto_suave))


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
