"""Pantalla "Acerca de PDF Studio"."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from pdf_studio import NOMBRE_APP, __version__
from pdf_studio.config import DIR_RECURSOS


class DialogoAcerca(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Acerca de {NOMBRE_APP}")
        logo = QLabel(self)
        pixmap = QPixmap(str(DIR_RECURSOS / "logo_256.png"))
        logo.setPixmap(pixmap.scaled(96, 96, Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation))
        logo.setAccessibleName("Logo de PDF Studio")
        nombre = QLabel(f"<span style='font-size:18pt; font-weight:600'>{NOMBRE_APP}</span><br>"
                        f"Versión {__version__}", self)
        descripcion = QLabel(
            "Editor de PDF gratuito y de código abierto (AGPL-3.0).<br>"
            "El archivo original nunca se modifica: siempre se guarda una copia y se verifica "
            "que lo que no editaste quede idéntico.", self)
        descripcion.setWordWrap(True)
        creditos = QLabel(
            "Motor PDF: PyMuPDF (AGPL-3.0) · Interfaz: Qt / PySide6 (LGPL-3.0)<br>"
            "Íconos: Lucide (ISC) · Fuentes incluidas: Liberation, Carlito y Caladea (SIL OFL 1.1)", self)
        creditos.setObjectName("texto_suave")
        creditos.setWordWrap(True)
        cerrar = QPushButton("Cerrar", self)
        cerrar.setProperty("primario", True)
        cerrar.clicked.connect(self.accept)
        arriba = QHBoxLayout()
        arriba.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        texto = QVBoxLayout()
        texto.addWidget(nombre)
        texto.addWidget(descripcion)
        arriba.addLayout(texto, 1)
        capa = QVBoxLayout(self)
        capa.addLayout(arriba)
        capa.addWidget(creditos)
        fila = QHBoxLayout()
        fila.addStretch(1)
        fila.addWidget(cerrar)
        capa.addLayout(fila)
        self.setMinimumWidth(520)
