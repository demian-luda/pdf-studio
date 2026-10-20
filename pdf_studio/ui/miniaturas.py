"""Panel lateral de miniaturas. Se renderizan de a poco para no trabar la interfaz.

Se pueden elegir varias (Ctrl/Mayús + clic) y arrastrarlas para reordenar: el panel no
mueve nada por su cuenta, avisa con `paginas_movidas` y la ventana ejecuta la operación
(así se puede deshacer y el documento sigue cada página para verificar la fidelidad)."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QListWidget, QListWidgetItem, QWidget

from pdf_studio.core.documento import Documento
from pdf_studio.ui.visor import pixmap_a_qimage

ANCHO_MINIATURA = 120
POR_TANDA = 4


class PanelMiniaturas(QListWidget):
    pagina_elegida = Signal(int)
    paginas_movidas = Signal(list, int)       # (páginas, destino: índice antes del cual quedan)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.TopToBottom)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setIconSize(QSize(ANCHO_MINIATURA, int(ANCHO_MINIATURA * 1.42)))
        self.setSpacing(6)
        self.setUniformItemSizes(False)
        self.setMinimumWidth(ANCHO_MINIATURA + 40)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.ActionsContextMenu)
        self.setAccessibleName("Miniaturas de las páginas")
        self.documento: Documento | None = None
        self._pendientes: list[int] = []
        self._temporizador = QTimer(self)
        self._temporizador.setInterval(0)
        self._temporizador.timeout.connect(self._renderizar_tanda)
        self.currentRowChanged.connect(self._fila_cambiada)
        self._sin_senal = False

    def set_documento(self, documento: Documento | None) -> None:
        self.documento = documento
        self.recargar()

    def recargar(self) -> None:
        self._temporizador.stop()
        actual = max(0, self.currentRow())
        self._sin_senal = True
        self.clear()
        self._pendientes = []
        if self.documento is not None:
            vacio = QPixmap(self.iconSize())
            vacio.fill(QColor("white"))
            for i, pag in enumerate(self.documento.doc):
                item = QListWidgetItem(QIcon(vacio), str(i + 1))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
                self.addItem(item)
                self._pendientes.append(i)
            if self.count():
                self.setCurrentRow(min(actual, self.count() - 1))
            self._temporizador.start()
        self._sin_senal = False

    def _renderizar_tanda(self) -> None:
        if self.documento is None or not self._pendientes:
            self._temporizador.stop()
            return
        for _ in range(POR_TANDA):
            if not self._pendientes:
                break
            i = self._pendientes.pop(0)
            if i >= self.count():
                continue
            try:
                pag = self.documento.doc[i]
                tam = self.iconSize()
                escala = min(tam.width() / pag.rect.width, tam.height() / pag.rect.height)
                pix = pag.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=False, annots=True)
                img = QPixmap.fromImage(pixmap_a_qimage(pix))
            except Exception:
                continue          # página dañada: queda la miniatura en blanco
            # Borde fino para que la miniatura se distinga del fondo
            pintor = QPainter(img)
            pintor.setPen(QColor(0, 0, 0, 80))
            pintor.drawRect(0, 0, img.width() - 1, img.height() - 1)
            pintor.end()
            icono = QIcon()
            # El mismo dibujo seleccionada o no: Qt teñiría la página con el color de selección
            for modo in (QIcon.Mode.Normal, QIcon.Mode.Selected, QIcon.Mode.Active):
                icono.addPixmap(img, modo)
            self.item(i).setIcon(icono)

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        # Celdas del ancho del panel: las miniaturas quedan centradas
        alto = self.iconSize().height() + self.fontMetrics().height() + 16
        self.setGridSize(QSize(max(self.iconSize().width() + 16, self.viewport().width() - 4), alto))

    @property
    def renderizado_completo(self) -> bool:
        return not self._pendientes

    def marcar_pagina(self, indice: int) -> None:
        if 0 <= indice < self.count() and indice != self.currentRow():
            self._sin_senal = True
            self.setCurrentRow(indice)
            self.scrollToItem(self.item(indice))
            self._sin_senal = False

    # ------------------------------------------------------------------ selección y arrastre
    def paginas_seleccionadas(self) -> list[int]:
        return sorted({i.row() for i in self.selectedIndexes()})

    def fila_destino(self, pos) -> int:
        """Índice antes del cual se suelta (mitad superior de una miniatura = antes de ella)."""
        item = self.itemAt(pos)
        if item is None:
            return self.count()
        fila = self.row(item)
        return fila + 1 if pos.y() > self.visualItemRect(item).center().y() else fila

    def dragMoveEvent(self, evento) -> None:
        if evento.source() is self:
            evento.setDropAction(Qt.DropAction.MoveAction)
            evento.accept()
        else:
            evento.ignore()

    def dropEvent(self, evento) -> None:
        if evento.source() is not self:
            evento.ignore()
            return
        destino = self.fila_destino(evento.position().toPoint())
        paginas = self.paginas_seleccionadas()
        # Qt no mueve nada: la ventana aplica la operación y el panel se recarga
        evento.setDropAction(Qt.DropAction.IgnoreAction)
        evento.accept()
        if paginas:
            self.paginas_movidas.emit(paginas, destino)

    def _fila_cambiada(self, fila: int) -> None:
        if not self._sin_senal and fila >= 0:
            self.pagina_elegida.emit(fila)
