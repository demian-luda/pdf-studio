"""Diálogo «Reconocer texto (OCR)»: modo Idéntico o Reconstruido y páginas a procesar."""
from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QDialog, QGroupBox, QLabel, QRadioButton, QVBoxLayout, QWidget

from pdf_studio.core.ocr import ModoOCR
from pdf_studio.ui.comunes import SelectorPaginas, botonera


class DialogoOCR(QDialog):
    def __init__(self, total: int, actual: int, sin_texto: list[int], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Reconocer texto (OCR)")
        self.resize(560, 0)
        self.modo: ModoOCR = ModoOCR.IDENTICO
        self.paginas: list[int] = []

        ayuda = QLabel("Reconoce el texto de páginas escaneadas (imágenes) con Tesseract, en español e "
                       "inglés. Las páginas que ya tienen texto no se tocan.", self)
        ayuda.setWordWrap(True)

        grupo_modo = QGroupBox("Modo", self)
        capa_modo = QVBoxLayout(grupo_modo)
        self.radio_identico = QRadioButton("Idéntico (recomendado)", grupo_modo)
        self.radio_identico.setChecked(True)
        nota_identico = QLabel("La página se ve exactamente igual que el original; se agrega una capa de "
                               "texto invisible para buscar, seleccionar y copiar.", grupo_modo)
        nota_identico.setObjectName("texto_suave")
        nota_identico.setWordWrap(True)
        self.radio_reconstruido = QRadioButton("Reconstruido (experimental)", grupo_modo)
        self.aviso_reconstruido = QLabel("Reemplaza la imagen escaneada por texto real editable. Es aproximado: "
                                         "la página cambia de aspecto (fuente y posiciones) y funciona mejor en "
                                         "documentos simples.", grupo_modo)
        self.aviso_reconstruido.setObjectName("texto_aviso")
        self.aviso_reconstruido.setWordWrap(True)
        grupo = QButtonGroup(self)
        grupo.addButton(self.radio_identico)
        grupo.addButton(self.radio_reconstruido)
        for w in (self.radio_identico, nota_identico, self.radio_reconstruido, self.aviso_reconstruido):
            capa_modo.addWidget(w)

        extras = [(f"Páginas sin texto ({len(sin_texto)})", sin_texto)] if sin_texto else []
        self.selector = SelectorPaginas(total, actual, self, extras=extras)
        self.error = QLabel("", self)
        self.error.setObjectName("texto_aviso")
        self.error.setWordWrap(True)
        self.error.hide()

        capa = QVBoxLayout(self)
        capa.addWidget(ayuda)
        capa.addWidget(grupo_modo)
        capa.addWidget(self.selector)
        capa.addWidget(self.error)
        capa.addWidget(botonera(self, "Reconocer texto", "scan-text"))

    def accept(self) -> None:
        try:
            self.paginas = self.selector.paginas()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        self.modo = ModoOCR.RECONSTRUIDO if self.radio_reconstruido.isChecked() else ModoOCR.IDENTICO
        super().accept()
