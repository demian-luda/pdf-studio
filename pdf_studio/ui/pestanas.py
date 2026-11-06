"""Pestañas de documentos: varios PDFs abiertos en la misma ventana.

Hay un solo visor y un solo juego de paneles; cada pestaña guarda el estado de su documento
(dónde quedó la vista, la redacción marcada, el análisis de fuentes, el aviso) y la ventana
lo restaura al activarla.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar, QWidget

from pdf_studio.core.documento import Documento
from pdf_studio.core.redaccion import Redaccion


@dataclass(eq=False)
class Pestana:
    documento: Documento
    vista: dict | None = None                  # visor.vista() al dejar la pestaña
    redaccion: Redaccion = field(default_factory=Redaccion)
    analisis: str = "pendiente"                # 'pendiente', 'analizando', 'ok' o 'error'
    mensaje_analisis: str = ""
    fuentes: list | None = None                # list[InfoFuente] del último análisis
    faltantes: list = field(default_factory=list)
    mostrar_faltantes: bool = True             # mostrar el panel si al terminar falta algo importante
    aviso: tuple | None = None                 # (texto, botón, acción, ícono) visible al dejarla

    @property
    def titulo(self) -> str:
        marca = " •" if self.documento.modificado else ""
        return self.documento.ruta_original.name + marca


class BarraPestanas(QTabBar):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("pestanas_documentos")
        self.setTabsClosable(False)          # el botón de cerrar es propio, con ícono Lucide
        self.setMovable(True)
        self.setDocumentMode(True)
        self.setExpanding(False)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Documentos abiertos")
