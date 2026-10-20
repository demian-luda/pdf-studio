"""Visor de páginas: scroll continuo, zoom, resaltado de búsqueda y herramientas de mouse.

Cada página es un ItemPagina en una QGraphicsScene. Las coordenadas de la escena
están en puntos PDF (1/72") del espacio de la página ROTADA; la vista aplica el zoom.
Las páginas se renderizan solo cuando se ven, y se guardan en una caché LRU.
"""
from __future__ import annotations

from collections import OrderedDict
from enum import Enum

import pymupdf
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QGraphicsItem, QGraphicsRectItem, QGraphicsScene, QGraphicsView,
                               QStyleOptionGraphicsItem, QToolTip, QWidget)

from pdf_studio.core.documento import Documento
from pdf_studio.ui.colocacion import ImagenColocable
from pdf_studio.ui.tema import gestor

SEPARACION = 16.0          # espacio entre páginas, en puntos
ZOOM_MIN, ZOOM_MAX = 0.1, 8.0
MAX_PIXELES_COMPLETA = 16_000_000   # por encima de esto se renderiza solo la parte visible
COLOR_RESULTADO = QColor(255, 220, 0, 90)
COLOR_RESULTADO_ACTUAL = QColor(255, 120, 0, 130)


class Modo(Enum):
    MANO = "mano"
    RESALTAR = "resaltar"
    NOTA = "nota"
    TEXTO = "texto"
    REDACTAR = "redactar"


def pixmap_a_qimage(pix: pymupdf.Pixmap) -> QImage:
    formato = QImage.Format.Format_RGB888 if pix.n == 3 else QImage.Format.Format_RGBA8888
    return QImage(pix.samples, pix.width, pix.height, pix.stride, formato).copy()


class SeleccionTexto(QGraphicsRectItem):
    """Marco del texto seleccionado. Se puede arrastrar para mover el texto."""

    def __init__(self, rect: QRectF, pagina: int):
        super().__init__(rect)
        self.pagina = pagina
        acento = QColor(gestor().paleta.acento)
        self.setPen(QPen(acento, 0, Qt.PenStyle.DashLine))
        relleno = QColor(acento)
        relleno.setAlpha(40)
        self.setBrush(QBrush(relleno))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, True)
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.setZValue(40)


class ItemPagina(QGraphicsItem):
    def __init__(self, visor: "Visor", indice: int, ancho: float, alto: float):
        super().__init__()
        self.visor = visor
        self.indice = indice
        self._rect = QRectF(0, 0, ancho, alto)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption, True)

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem, widget: QWidget | None = None):
        painter.fillRect(self._rect, Qt.GlobalColor.white)
        escala = painter.worldTransform().m11()
        dpr = widget.devicePixelRatioF() if widget is not None else 1.0
        self.visor.pintar_pagina(painter, self.indice, self._rect, option.exposedRect, escala * dpr)
        painter.setPen(QPen(QColor(0, 0, 0, 60), 0))
        painter.drawRect(self._rect)


class Visor(QGraphicsView):
    pagina_actual_cambio = Signal(int)
    zoom_cambio = Signal(float)
    rect_seleccionado = Signal(int, object)   # (página, pymupdf.Rect sin rotar)
    punto_clic = Signal(int, object)          # (página, pymupdf.Point sin rotar)
    colocacion_confirmada = Signal()          # Enter mientras se ubica una imagen
    colocacion_cancelada = Signal()           # Esc mientras se ubica una imagen
    texto_clic = Signal(int, object)          # (página, punto sin rotar) en modo Editar texto
    texto_doble_clic = Signal(int, object)
    seleccion_movida = Signal(int, float, float)   # (página, dx, dy) en coordenadas sin rotar

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setAccessibleName("Página del documento")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.aplicar_tema()
        self.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.documento: Documento | None = None
        self.items: list[ItemPagina] = []
        self._zoom = 1.0
        self._ajuste: str | None = "ancho"   # 'ancho', 'pagina' o None (zoom manual)
        self._cache: OrderedDict[tuple[int, int], QImage] = OrderedDict()
        self._paginas_rotas: set[int] = set()
        self._cache_max = 24
        self._resultados: list[tuple[int, pymupdf.Rect]] = []
        self._resultado_actual = -1
        self._marcas: list[QGraphicsRectItem] = []
        self._redaccion: list[tuple[int, pymupdf.Rect]] = []        # zonas marcadas, aún sin aplicar
        self._marcas_redaccion: list[QGraphicsRectItem] = []
        self._pagina_actual = 0
        self._modo = Modo.MANO
        self._inicio_seleccion: QPointF | None = None
        self._rect_seleccion: QGraphicsRectItem | None = None
        self.colocacion: ImagenColocable | None = None
        self.seleccion: SeleccionTexto | None = None
        self._pos_seleccion: QPointF | None = None
        self.verticalScrollBar().valueChanged.connect(self._actualizar_pagina_actual)
        self.set_modo(Modo.MANO)

    def aplicar_tema(self) -> None:
        self.setBackgroundBrush(QColor(gestor().paleta.lienzo))
        if getattr(self, "seleccion", None) is not None:
            acento = QColor(gestor().paleta.acento)
            self.seleccion.setPen(QPen(acento, 0, Qt.PenStyle.DashLine))
        self.viewport().update()

    # ------------------------------------------------------------------ documento
    def set_documento(self, documento: Documento | None) -> None:
        self.cancelar_colocacion()
        self.documento = documento
        self._resultados = []
        self._resultado_actual = -1
        self._pagina_actual = 0
        self.recargar()
        self.ajustar_ancho()
        self.verticalScrollBar().setValue(self.verticalScrollBar().minimum())

    def recargar(self) -> None:
        """Rearma la escena (después de abrir, deshacer, rotar...). Mantiene la página visible."""
        pagina = self._pagina_actual
        self._cache.clear()
        self._paginas_rotas = set()
        escena = self.scene()
        # La imagen que se está ubicando sobrevive a la reconstrucción de la escena
        colocacion = self.colocacion
        estado = colocacion.estado() if colocacion is not None else None
        if colocacion is not None:
            escena.removeItem(colocacion)
        escena.clear()
        self.items = []
        self._marcas = []
        self._marcas_redaccion = []       # escena.clear() ya las borró
        self._rect_seleccion = None
        self.seleccion = None      # el documento cambió: la selección de texto ya no vale
        if self.documento is None:
            escena.setSceneRect(QRectF())
            return
        doc = self.documento.doc
        ancho_max = max((p.rect.width for p in doc), default=0)
        y = SEPARACION
        for i, pag in enumerate(doc):
            r = pag.rect
            item = ItemPagina(self, i, r.width, r.height)
            item.setPos((ancho_max - r.width) / 2 + SEPARACION, y)
            escena.addItem(item)
            self.items.append(item)
            y += r.height + SEPARACION
        escena.setSceneRect(QRectF(0, 0, ancho_max + 2 * SEPARACION, y))
        self._dibujar_resultados()
        self._dibujar_redaccion()
        if colocacion is not None:
            escena.addItem(colocacion)
            colocacion.restaurar(estado)
        if self.items:
            self.ir_a_pagina(min(pagina, len(self.items) - 1))

    @property
    def cantidad_paginas(self) -> int:
        return len(self.items)

    # ------------------------------------------------------------------ render
    def pintar_pagina(self, painter: QPainter, indice: int, rect: QRectF, expuesto: QRectF,
                      escala: float) -> None:
        if self.documento is None:
            return
        if indice in self._paginas_rotas:
            self._pintar_aviso(painter, rect)
            return
        try:
            self._pintar_pagina(painter, indice, rect, expuesto, escala)
        except Exception as e:
            # Una página dañada no puede tirar un error en cada repintado: se avisa una vez
            from pdf_studio.ui.errores import registro
            registro().error("No se pudo dibujar la página %d: %r", indice + 1, e)
            self._paginas_rotas.add(indice)
            self._pintar_aviso(painter, rect)

    @staticmethod
    def _pintar_aviso(painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QColor(gestor().paleta.peligro))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "No se pudo mostrar esta página\n(está dañada)")

    def _pintar_pagina(self, painter: QPainter, indice: int, rect: QRectF, expuesto: QRectF,
                       escala: float) -> None:
        pag = self.documento.doc[indice]
        ancho_px = rect.width() * escala
        alto_px = rect.height() * escala
        if ancho_px * alto_px <= MAX_PIXELES_COMPLETA or pag.rotation:
            img = self._imagen_completa(indice, escala, ancho_px * alto_px)
            painter.drawImage(rect, img)
        else:
            # Zoom muy grande: se renderiza solo la zona visible, sin caché
            zona = expuesto.intersected(rect)
            if zona.isEmpty():
                return
            clip = pymupdf.Rect(zona.left(), zona.top(), zona.right(), zona.bottom())
            pix = pag.get_pixmap(matrix=pymupdf.Matrix(escala, escala), clip=clip, alpha=False, annots=True)
            painter.drawImage(zona, pixmap_a_qimage(pix))

    def _imagen_completa(self, indice: int, escala: float, pixeles: float) -> QImage:
        if pixeles > MAX_PIXELES_COMPLETA:
            escala *= (MAX_PIXELES_COMPLETA / pixeles) ** 0.5
        clave = (indice, int(escala * 100))
        img = self._cache.get(clave)
        if img is not None:
            self._cache.move_to_end(clave)
            return img
        pag = self.documento.doc[indice]
        pix = pag.get_pixmap(matrix=pymupdf.Matrix(clave[1] / 100, clave[1] / 100), alpha=False, annots=True)
        img = pixmap_a_qimage(pix)
        self._cache[clave] = img
        while len(self._cache) > self._cache_max:
            self._cache.popitem(last=False)
        return img

    def imagen_pagina(self, indice: int, escala: float) -> QImage:
        """Render de una página a la escala dada (también lo usan las miniaturas)."""
        pag = self.documento.doc[indice]
        return pixmap_a_qimage(pag.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=False))

    # ------------------------------------------------------------------ zoom
    @property
    def zoom(self) -> float:
        """Zoom relativo al tamaño real (1.0 = 100%)."""
        return self._zoom

    def _escala_pantalla(self) -> float:
        return self.logicalDpiX() / 72.0

    def set_zoom(self, zoom: float, ancla: QPointF | None = None, _ajuste: str | None = None) -> None:
        self._ajuste = _ajuste
        zoom = max(ZOOM_MIN, min(ZOOM_MAX, zoom))
        if ancla is None:
            ancla = QPointF(self.viewport().width() / 2, self.viewport().height() / 2)
        punto_escena = self.mapToScene(ancla.toPoint())
        self._zoom = zoom
        escala = zoom * self._escala_pantalla()
        self.resetTransform()
        self.scale(escala, escala)
        if self.colocacion is not None:
            self.colocacion.set_escala_vista(escala)
        # Mantiene el punto bajo el ancla en el mismo lugar de la pantalla
        nuevo = self.mapToScene(ancla.toPoint())
        delta = (nuevo - punto_escena) * escala
        self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - round(delta.x()))
        self.verticalScrollBar().setValue(self.verticalScrollBar().value() - round(delta.y()))
        self.zoom_cambio.emit(zoom)

    def acercar(self) -> None:
        self.set_zoom(self._zoom * 1.25)

    def alejar(self) -> None:
        self.set_zoom(self._zoom / 1.25)

    def zoom_real(self) -> None:
        self.set_zoom(1.0)

    def ajustar_ancho(self) -> None:
        ancho = self.scene().sceneRect().width()
        if ancho > 0:
            disponible = self.viewport().width() - 4
            self.set_zoom(disponible / (ancho * self._escala_pantalla()), _ajuste="ancho")

    def ajustar_pagina(self) -> None:
        if not self.items:
            return
        r = self.items[self._pagina_actual].boundingRect()
        vp = self.viewport()
        z = min((vp.width() - 4) / ((r.width() + 2 * SEPARACION) * self._escala_pantalla()),
                (vp.height() - 4) / ((r.height() + 2 * SEPARACION) * self._escala_pantalla()))
        self.set_zoom(z, _ajuste="pagina")
        self.ir_a_pagina(self._pagina_actual)

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        # Si el usuario eligió "ajustar", se mantiene al cambiar el tamaño de la ventana o los paneles
        if self.items and self._ajuste == "ancho":
            self.ajustar_ancho()
        elif self.items and self._ajuste == "pagina":
            self.ajustar_pagina()

    def wheelEvent(self, evento) -> None:
        if evento.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if evento.angleDelta().y() > 0 else 1 / 1.15
            self.set_zoom(self._zoom * factor, evento.position())
            evento.accept()
        else:
            super().wheelEvent(evento)

    # ------------------------------------------------------------------ navegación
    @property
    def pagina_actual(self) -> int:
        return self._pagina_actual

    def ir_a_pagina(self, indice: int) -> None:
        if not 0 <= indice < len(self.items):
            return
        item = self.items[indice]
        arriba = item.mapToScene(QPointF(0, 0))
        centro_x = self.mapToScene(self.viewport().rect().center()).x()
        self.centerOn(centro_x, arriba.y() + self.viewport().height() / 2 / self.transform().m11()
                      - SEPARACION / 2)
        self._set_pagina_actual(indice)

    def _set_pagina_actual(self, indice: int) -> None:
        if indice != self._pagina_actual:
            self._pagina_actual = indice
            self.pagina_actual_cambio.emit(indice)

    def _actualizar_pagina_actual(self) -> None:
        if not self.items:
            return
        # La página actual es la que ocupa el centro superior de la vista
        punto = self.mapToScene(self.viewport().width() // 2, self.viewport().height() // 3)
        for item in self.items:
            r = item.sceneBoundingRect()
            if r.top() - SEPARACION <= punto.y() <= r.bottom() + SEPARACION / 2:
                self._set_pagina_actual(item.indice)
                return

    # ------------------------------------------------------------------ coordenadas
    def a_escena(self, indice: int, rect: pymupdf.Rect) -> QRectF:
        """Rect de PDF sin rotar → rect en la escena."""
        pag = self.documento.doc[indice]
        r = rect * pag.rotation_matrix
        return self.items[indice].mapRectToScene(QRectF(r.x0, r.y0, r.width, r.height))

    def escena_a_pagina(self, indice: int, punto: QPointF) -> pymupdf.Point:
        """Punto de la escena → punto PDF sin rotar de la página dada (aunque caiga afuera)."""
        local = self.items[indice].mapFromScene(punto)
        return pymupdf.Point(local.x(), local.y()) * self.documento.doc[indice].derotation_matrix

    def pagina_mas_cercana(self, punto: QPointF) -> int:
        return min(range(len(self.items)), key=lambda i: abs(self.items[i].sceneBoundingRect().center().y()
                                                              - punto.y()))

    def desde_escena(self, punto: QPointF) -> tuple[int, pymupdf.Point] | None:
        """Punto de la escena → (página, punto PDF sin rotar)."""
        for item in self.items:
            if item.sceneBoundingRect().contains(punto):
                local = item.mapFromScene(punto)
                pag = self.documento.doc[item.indice]
                return item.indice, pymupdf.Point(local.x(), local.y()) * pag.derotation_matrix
        return None

    # ------------------------------------------------------------------ búsqueda
    def mostrar_resultados(self, resultados: list[tuple[int, pymupdf.Rect]], actual: int = -1) -> None:
        self._resultados = resultados
        self._resultado_actual = actual
        self._dibujar_resultados()
        self._dibujar_redaccion()
        if 0 <= actual < len(resultados):
            pagina, rect = resultados[actual]
            self.ensureVisible(self.a_escena(pagina, rect), 60, 120)
            self._set_pagina_actual(pagina)

    def _dibujar_resultados(self) -> None:
        for m in self._marcas:
            self.scene().removeItem(m)
        self._marcas = []
        if self.documento is None:
            return
        for n, (pagina, rect) in enumerate(self._resultados):
            if pagina >= len(self.items):
                continue
            color = COLOR_RESULTADO_ACTUAL if n == self._resultado_actual else COLOR_RESULTADO
            marca = QGraphicsRectItem(self.a_escena(pagina, rect))
            marca.setBrush(QBrush(color))
            marca.setPen(QPen(Qt.PenStyle.NoPen))
            marca.setZValue(10)
            self.scene().addItem(marca)
            self._marcas.append(marca)

    # ------------------------------------------------------------------ redacción pendiente
    def mostrar_redaccion(self, zonas: list[tuple[int, pymupdf.Rect]]) -> None:
        """Zonas marcadas para redactar (todavía no aplicadas): contorno y rayado en rojo."""
        self._redaccion = list(zonas)
        self._dibujar_redaccion()

    def _dibujar_redaccion(self) -> None:
        for m in self._marcas_redaccion:
            if m.scene() is not None:
                self.scene().removeItem(m)
        self._marcas_redaccion = []
        if self.documento is None:
            return
        rojo = QColor(gestor().paleta.peligro)
        for pagina, rect in self._redaccion:
            if pagina >= len(self.items):
                continue
            marca = QGraphicsRectItem(self.a_escena(pagina, rect))
            marca.setPen(QPen(rojo, 0, Qt.PenStyle.DashLine))
            relleno = QColor(rojo)
            relleno.setAlpha(60)
            marca.setBrush(QBrush(relleno, Qt.BrushStyle.BDiagPattern))
            marca.setZValue(15)
            marca.setToolTip("Marcado para redactar (todavía no se aplicó)")
            self.scene().addItem(marca)
            self._marcas_redaccion.append(marca)

    # ------------------------------------------------------------------ selección de texto
    def mostrar_seleccion(self, pagina: int, rect: pymupdf.Rect) -> None:
        self.limpiar_seleccion()
        self.seleccion = SeleccionTexto(self.a_escena(pagina, rect).adjusted(-1, -1, 1, 1), pagina)
        self.scene().addItem(self.seleccion)

    def limpiar_seleccion(self) -> None:
        if self.seleccion is not None and self.seleccion.scene() is not None:
            self.scene().removeItem(self.seleccion)
        self.seleccion = None

    # ------------------------------------------------------------------ colocación de imágenes
    def iniciar_colocacion(self, item: ImagenColocable, pagina: int | None = None) -> None:
        """Muestra la imagen flotante centrada en la parte visible de la página."""
        self.cancelar_colocacion()
        if not self.items:
            return
        pagina = self._pagina_actual if pagina is None else pagina
        caja_pagina = self.items[pagina].sceneBoundingRect()
        visible = self.mapToScene(self.viewport().rect()).boundingRect().intersected(caja_pagina)
        centro = visible.center() if not visible.isEmpty() else caja_pagina.center()
        self.colocacion = item
        self.scene().addItem(item)
        item.setPos(centro)
        item.set_escala_vista(self.transform().m11())
        item.setFocus()
        self.set_modo(Modo.MANO)

    def terminar_colocacion(self) -> tuple[int, tuple] | None:
        """Saca la imagen flotante y devuelve (página, esquinas sin rotar)."""
        item = self.colocacion
        if item is None or not self.items:
            return None
        indice = self.pagina_mas_cercana(item.scenePos())
        esquinas = tuple(tuple(self.escena_a_pagina(indice, p)) for p in item.esquinas_escena())
        self.scene().removeItem(item)
        self.colocacion = None
        return indice, esquinas

    def cancelar_colocacion(self) -> None:
        if self.colocacion is not None:
            self.scene().removeItem(self.colocacion)
            self.colocacion = None

    def keyPressEvent(self, evento) -> None:
        if self.colocacion is not None:
            tecla = evento.key()
            if tecla in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.colocacion_confirmada.emit()
                return
            if tecla == Qt.Key.Key_Escape:
                self.colocacion_cancelada.emit()
                return
            paso = 10.0 if evento.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
            desplazamientos = {Qt.Key.Key_Left: (-paso, 0), Qt.Key.Key_Right: (paso, 0),
                               Qt.Key.Key_Up: (0, -paso), Qt.Key.Key_Down: (0, paso)}
            if tecla in desplazamientos:
                dx, dy = desplazamientos[tecla]
                self.colocacion.moveBy(dx, dy)
                return
        super().keyPressEvent(evento)

    # ------------------------------------------------------------------ herramientas de mouse
    @property
    def modo(self) -> Modo:
        return self._modo

    def set_modo(self, modo: Modo) -> None:
        self._modo = modo
        if modo == Modo.MANO:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            cursor = (Qt.CursorShape.IBeamCursor if modo in (Modo.RESALTAR, Modo.TEXTO)
                      else Qt.CursorShape.CrossCursor if modo == Modo.REDACTAR
                      else Qt.CursorShape.PointingHandCursor)
            self.viewport().setCursor(cursor)
        if modo != Modo.TEXTO:
            self.limpiar_seleccion()

    def mousePressEvent(self, evento) -> None:
        if self._modo in (Modo.RESALTAR, Modo.REDACTAR) and evento.button() == Qt.MouseButton.LeftButton:
            self._inicio_seleccion = self.mapToScene(evento.position().toPoint())
            self._rect_seleccion = QGraphicsRectItem(QRectF(self._inicio_seleccion, self._inicio_seleccion))
            paleta = gestor().paleta
            acento = QColor(paleta.peligro if self._modo == Modo.REDACTAR else paleta.acento)
            self._rect_seleccion.setPen(QPen(acento, 0))
            relleno = QColor(acento)
            relleno.setAlpha(40)
            self._rect_seleccion.setBrush(QBrush(relleno))
            self._rect_seleccion.setZValue(20)
            self.scene().addItem(self._rect_seleccion)
            return
        if self._modo == Modo.TEXTO and evento.button() == Qt.MouseButton.LeftButton:
            escena = self.mapToScene(evento.position().toPoint())
            if self.seleccion is not None and self.seleccion.sceneBoundingRect().contains(escena):
                self._pos_seleccion = self.seleccion.pos()
                super().mousePressEvent(evento)   # arrastre del marco
                return
            destino = self.desde_escena(escena)
            if destino is not None:
                self.texto_clic.emit(*destino)
            else:
                self.limpiar_seleccion()
            return
        if self._modo == Modo.NOTA and evento.button() == Qt.MouseButton.LeftButton:
            destino = self.desde_escena(self.mapToScene(evento.position().toPoint()))
            if destino is not None:
                self.punto_clic.emit(*destino)
            return
        super().mousePressEvent(evento)

    def mouseMoveEvent(self, evento) -> None:
        if self._rect_seleccion is not None and self._inicio_seleccion is not None:
            actual = self.mapToScene(evento.position().toPoint())
            self._rect_seleccion.setRect(QRectF(self._inicio_seleccion, actual).normalized())
            return
        if evento.buttons() == Qt.MouseButton.NoButton:
            self._mostrar_nota(evento)
        super().mouseMoveEvent(evento)

    def nota_en(self, punto_escena: QPointF) -> str | None:
        """Texto de la nota que está bajo el punto, si hay una."""
        destino = self.desde_escena(punto_escena)
        if destino is None or self.documento is None:
            return None
        pagina, punto = destino
        pag = self.documento.doc[pagina]       # la página tiene que vivir mientras se usan sus anotaciones
        for annot in pag.annots(types=[pymupdf.PDF_ANNOT_TEXT]):
            if annot.rect.contains(punto):
                return annot.info.get("content") or ""
        return None

    def _mostrar_nota(self, evento) -> None:
        texto = self.nota_en(self.mapToScene(evento.position().toPoint()))
        if texto:
            QToolTip.showText(evento.globalPosition().toPoint(), texto, self)

    def mouseDoubleClickEvent(self, evento) -> None:
        if self._modo == Modo.TEXTO:
            destino = self.desde_escena(self.mapToScene(evento.position().toPoint()))
            if destino is not None:
                self.texto_doble_clic.emit(*destino)
            return
        super().mouseDoubleClickEvent(evento)

    def mouseReleaseEvent(self, evento) -> None:
        if self._pos_seleccion is not None and self.seleccion is not None:
            super().mouseReleaseEvent(evento)
            inicio, fin = self._pos_seleccion, self.seleccion.pos()
            self._pos_seleccion = None
            if (fin - inicio).manhattanLength() > 0.5:
                pagina = self.seleccion.pagina
                a = self.escena_a_pagina(pagina, self.items[pagina].mapToScene(QPointF(0, 0)) + inicio)
                b = self.escena_a_pagina(pagina, self.items[pagina].mapToScene(QPointF(0, 0)) + fin)
                self.seleccion_movida.emit(pagina, b.x - a.x, b.y - a.y)
            return
        if self._rect_seleccion is not None and self._inicio_seleccion is not None:
            rect = self._rect_seleccion.rect()
            self.scene().removeItem(self._rect_seleccion)
            self._rect_seleccion = None
            self._inicio_seleccion = None
            self._emitir_seleccion(rect)
            return
        super().mouseReleaseEvent(evento)

    def _emitir_seleccion(self, rect_escena: QRectF) -> None:
        origen = self.desde_escena(rect_escena.center())
        if origen is None:
            return
        indice = origen[0]
        item = self.items[indice]
        local = item.mapRectFromScene(rect_escena).intersected(item.boundingRect())
        pag = self.documento.doc[indice]
        r = pymupdf.Rect(local.left(), local.top(), local.right(), local.bottom()) * pag.derotation_matrix
        self.rect_seleccionado.emit(indice, r)
