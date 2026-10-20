"""Barra lateral: un selector vertical de íconos (Páginas, Fuentes, Faltantes) y el panel elegido."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QHBoxLayout, QStackedWidget, QToolBar, QWidget

from pdf_studio.ui import iconos
from pdf_studio.ui.cinta import hacer_enfocable
from pdf_studio.ui.tema import gestor


class BarraLateral(QWidget):
    cambio = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("barra_lateral")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.selector = QToolBar(self)
        self.selector.setObjectName("selector_lateral")
        self.selector.setOrientation(Qt.Orientation.Vertical)
        self.selector.setMovable(False)
        self.selector.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.pila = QStackedWidget(self)
        self.grupo = QActionGroup(self)
        self.grupo.setExclusive(True)
        self._paneles: dict[str, tuple[QAction, QWidget, str]] = {}
        capa = QHBoxLayout(self)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.setSpacing(0)
        capa.addWidget(self.selector)
        capa.addWidget(self.pila, 1)
        gestor().cambio.connect(self._tamanos)
        self._tamanos()

    def agregar(self, clave: str, titulo: str, icono: str, widget: QWidget, atajo: str = "") -> QAction:
        accion = QAction(titulo, self)
        accion.setCheckable(True)
        accion.setToolTip(f"{titulo} ({atajo})" if atajo else titulo)
        iconos.asignar(accion, icono)
        accion.triggered.connect(lambda: self.mostrar(clave))
        self.grupo.addAction(accion)
        self.selector.addAction(accion)
        self.pila.addWidget(widget)
        self._paneles[clave] = (accion, widget, titulo)
        if len(self._paneles) == 1:
            accion.setChecked(True)
        hacer_enfocable(self.selector)
        return accion

    def mostrar(self, clave: str) -> None:
        accion, widget, _ = self._paneles[clave]
        accion.setChecked(True)
        self.pila.setCurrentWidget(widget)
        self.cambio.emit(clave)

    @property
    def actual(self) -> str:
        for clave, (_a, w, _t) in self._paneles.items():
            if w is self.pila.currentWidget():
                return clave
        return ""

    def set_insignia(self, clave: str, cantidad: int) -> None:
        """Muestra la cantidad en el tooltip (por ejemplo, 'Faltantes: 3')."""
        accion, _w, titulo = self._paneles[clave]
        accion.setToolTip(f"{titulo}: {cantidad}" if cantidad else titulo)
        accion.setText(f"{titulo} ({cantidad})" if cantidad else titulo)

    def _tamanos(self) -> None:
        lado = gestor().tamano_icono(20)
        self.selector.setIconSize(QSize(lado, lado))
