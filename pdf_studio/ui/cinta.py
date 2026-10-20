"""Encabezado de la ventana: logo, acceso rápido, pestañas y cinta de herramientas.

Fila 1: [logo] PDF Studio | Abrir Guardar Más | Deshacer Rehacer | pestañas … | Ctrl+K Tema
Fila 2: la cinta de la pestaña elegida (íconos con texto debajo).
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QStackedWidget, QTabBar, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from pdf_studio.config import DIR_RECURSOS
from pdf_studio.ui.tema import gestor


def hacer_enfocable(barra: QToolBar) -> None:
    """Los botones de las barras de Qt no toman foco con Tab: se habilita para usar todo con teclado."""
    for accion in barra.actions():
        boton = barra.widgetForAction(accion)
        if boton is not None:
            boton.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            boton.setAccessibleName(accion.text().replace("&", ""))


class Encabezado(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("encabezado")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.logo = QLabel(self)
        self.logo.setAccessibleName("PDF Studio")
        self.titulo = QLabel("PDF Studio", self)
        self.titulo.setObjectName("titulo_app")
        self.rapida = self._barra("acceso_rapido", solo_iconos=True)
        self.pestanas = QTabBar(self)
        self.pestanas.setObjectName("pestanas_cinta")
        self.pestanas.setDrawBase(False)
        self.pestanas.setExpanding(False)
        self.pestanas.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.pestanas.setAccessibleName("Pestañas de herramientas")
        self.derecha = self._barra("acceso_derecha", solo_iconos=True)
        self.cinta = QStackedWidget(self)
        self.cinta.setObjectName("cinta")
        self.barras: list[QToolBar] = []
        self.pestanas.currentChanged.connect(self.cinta.setCurrentIndex)

        fila = QHBoxLayout()
        fila.setContentsMargins(10, 6, 8, 0)
        fila.setSpacing(6)
        fila.addWidget(self.logo)
        fila.addWidget(self.titulo)
        fila.addSpacing(10)
        fila.addWidget(self.rapida)
        fila.addSpacing(10)
        fila.addWidget(self.pestanas, 0, Qt.AlignmentFlag.AlignBottom)
        fila.addStretch(1)
        fila.addWidget(self.derecha)
        capa = QVBoxLayout(self)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.setSpacing(0)
        capa.addLayout(fila)
        capa.addWidget(self.cinta)
        gestor().cambio.connect(self.actualizar_tamanos)
        self.actualizar_tamanos()

    def _barra(self, nombre: str, solo_iconos: bool = False) -> QToolBar:
        barra = QToolBar(self)
        barra.setObjectName(nombre)
        barra.setMovable(False)
        barra.setFloatable(False)
        barra.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly if solo_iconos
                                 else Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        return barra

    def agregar_pestana(self, nombre: str) -> QToolBar:
        barra = self._barra(f"cinta_{nombre.lower()}")
        barra.setAccessibleName(f"Herramientas de {nombre}")
        self.barras.append(barra)
        self.cinta.addWidget(barra)
        self.pestanas.addTab(nombre)
        return barra

    def agregar_menu(self, barra: QToolBar, accion: QAction, menu) -> QToolButton:
        """Botón con menú desplegable (por ejemplo, Tema o Más)."""
        accion.setMenu(menu)
        barra.addAction(accion)
        boton = barra.widgetForAction(accion)
        boton.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return boton

    def finalizar(self) -> None:
        for barra in [self.rapida, self.derecha] + self.barras:
            hacer_enfocable(barra)

    def actualizar_tamanos(self) -> None:
        g = gestor()
        lado = g.tamano_icono(20)
        for barra in [self.rapida, self.derecha] + self.barras:
            barra.setIconSize(QSize(lado, lado))
        lado_logo = g.tamano_icono(24)
        dpr = max(1.0, self.devicePixelRatioF())
        logo = QPixmap(str(DIR_RECURSOS / "logo_256.png")).scaled(
            round(lado_logo * dpr), round(lado_logo * dpr), Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation)
        logo.setDevicePixelRatio(dpr)
        self.logo.setPixmap(logo)
