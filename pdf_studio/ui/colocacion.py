"""Imagen flotante sobre el visor para ubicar una firma: arrastrar, escalar y rotar.

- Arrastrar la imagen la mueve.
- La manija de la esquina inferior derecha escala manteniendo la proporción.
- La manija circular de arriba la rota (con Shift, de a 15°).
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject

TAM_MANIJA = 10        # px en pantalla
DIST_ROTACION = 28     # px en pantalla entre el borde superior y la manija de rotación
ANCHO_MINIMO = 8.0     # puntos


class Manija(QGraphicsItem):
    """Manija de tamaño constante en pantalla (ignora el zoom)."""

    def __init__(self, padre: "ImagenColocable", tipo: str):
        super().__init__(padre)
        self.tipo = tipo  # 'escala' o 'rotacion'
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor if tipo == "escala" else Qt.CursorShape.CrossCursor)
        self.setZValue(2)

    def boundingRect(self) -> QRectF:
        m = TAM_MANIJA / 2 + 1
        return QRectF(-m, -m, 2 * m, 2 * m)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(0, 120, 215), 1.5))
        painter.setBrush(QBrush(Qt.GlobalColor.white))
        m = TAM_MANIJA / 2
        if self.tipo == "escala":
            painter.drawRect(QRectF(-m, -m, 2 * m, 2 * m))
        else:
            painter.drawEllipse(QRectF(-m, -m, 2 * m, 2 * m))

    def mousePressEvent(self, evento) -> None:
        evento.accept()

    def mouseMoveEvent(self, evento) -> None:
        padre: ImagenColocable = self.parentItem()
        if self.tipo == "escala":
            padre.escalar_hacia(evento.scenePos())
        else:
            padre.rotar_hacia(evento.scenePos(), bool(evento.modifiers() & Qt.KeyboardModifier.ShiftModifier))


class ImagenColocable(QGraphicsObject):
    def __init__(self, imagen: QImage, datos: bytes, ancho: float, descripcion: str = "firma"):
        super().__init__()
        self.imagen = imagen
        self.datos = datos                   # bytes PNG/JPG originales que se insertan
        self.descripcion = descripcion
        self.proporcion = imagen.height() / max(1, imagen.width())
        self.ancho = ancho
        self._escala_vista = 1.0
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable
                      | QGraphicsItem.GraphicsItemFlag.ItemIsFocusable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(50)
        self.manija_escala = Manija(self, "escala")
        self.manija_rotacion = Manija(self, "rotacion")
        self._ubicar_manijas()

    # ------------------------------------------------------------------ geometría
    @property
    def alto(self) -> float:
        return self.ancho * self.proporcion

    @property
    def angulo(self) -> float:
        return self.rotation()

    def rect_local(self) -> QRectF:
        return QRectF(-self.ancho / 2, -self.alto / 2, self.ancho, self.alto)

    def boundingRect(self) -> QRectF:
        extra = (DIST_ROTACION + TAM_MANIJA) / max(self._escala_vista, 1e-6)
        return self.rect_local().adjusted(-2, -extra, 2, 2)

    def shape(self) -> QPainterPath:
        camino = QPainterPath()
        camino.addRect(self.rect_local())
        return camino

    def set_escala_vista(self, escala: float) -> None:
        """La vista avisa el zoom actual para mantener las manijas a distancia fija en pantalla."""
        self.prepareGeometryChange()
        self._escala_vista = escala
        self._ubicar_manijas()

    def _ubicar_manijas(self) -> None:
        r = self.rect_local()
        self.manija_escala.setPos(r.bottomRight())
        self.manija_rotacion.setPos(QPointF(0, r.top() - DIST_ROTACION / max(self._escala_vista, 1e-6)))

    def set_ancho(self, ancho: float) -> None:
        self.prepareGeometryChange()
        self.ancho = max(ANCHO_MINIMO, ancho)
        self._ubicar_manijas()
        self.update()

    def escalar_hacia(self, punto_escena: QPointF) -> None:
        """Escala manteniendo la proporción: la esquina sigue al mouse (desde el centro)."""
        local = self.mapFromScene(punto_escena)
        mitad_diagonal = math.hypot(self.ancho / 2, self.alto / 2)
        distancia = math.hypot(local.x(), local.y())
        if mitad_diagonal > 0:
            self.set_ancho(self.ancho * distancia / mitad_diagonal)

    def rotar_hacia(self, punto_escena: QPointF, en_pasos: bool = False) -> None:
        centro = self.scenePos()
        dx, dy = punto_escena.x() - centro.x(), punto_escena.y() - centro.y()
        angulo = math.degrees(math.atan2(dx, -dy))   # 0° = manija arriba, horario positivo
        if en_pasos:
            angulo = round(angulo / 15) * 15
        self.setRotation(angulo % 360)

    def esquinas_escena(self) -> tuple[QPointF, QPointF, QPointF]:
        r = self.rect_local()
        return (self.mapToScene(r.topLeft()), self.mapToScene(r.topRight()), self.mapToScene(r.bottomLeft()))

    # ------------------------------------------------------------------ dibujo
    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self.rect_local()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(r, self.imagen)
        pluma = QPen(QColor(0, 120, 215), 0, Qt.PenStyle.DashLine)
        painter.setPen(pluma)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(r)
        painter.drawLine(QPointF(0, r.top()), self.manija_rotacion.pos())

    # ------------------------------------------------------------------ estado (para reconstruir la escena)
    def estado(self) -> dict:
        return {"pos": self.pos(), "ancho": self.ancho, "angulo": self.rotation()}

    def restaurar(self, estado: dict) -> None:
        self.setPos(estado["pos"])
        self.set_ancho(estado["ancho"])
        self.setRotation(estado["angulo"])
