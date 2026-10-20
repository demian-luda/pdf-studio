"""Barra de búsqueda de texto (Ctrl+F)."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from pdf_studio.core.documento import Documento
from pdf_studio.ui import iconos


def buscar_en_documento(doc: pymupdf.Document, texto: str) -> list[tuple[int, pymupdf.Rect]]:
    """Todas las apariciones (sin distinguir mayúsculas), en orden de lectura."""
    resultados = []
    if not texto.strip():
        return resultados
    for i, pag in enumerate(doc):
        for r in pag.search_for(texto):
            resultados.append((i, r))
    return resultados


class BarraBusqueda(QWidget):
    resultados_cambiaron = Signal(list, int)   # (resultados, índice actual)
    cerrada = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.documento: Documento | None = None
        self.resultados: list[tuple[int, pymupdf.Rect]] = []
        self.actual = -1
        self._texto_buscado = ""

        self.campo = QLineEdit(self)
        self.campo.setPlaceholderText("Buscar en el documento…")
        self.campo.setClearButtonEnabled(True)
        self.campo.returnPressed.connect(self.siguiente)
        self.campo.textChanged.connect(self._texto_cambiado)
        self.etiqueta = QLabel("", self)
        self.etiqueta.setMinimumWidth(90)
        b_ant = QToolButton(self, toolTip="Anterior (Shift+Enter)")
        b_sig = QToolButton(self, toolTip="Siguiente (Enter)")
        b_cerrar = QToolButton(self, toolTip="Cerrar búsqueda (Esc)")
        for boton, icono, nombre in ((b_ant, "chevron-up", "Resultado anterior"),
                                     (b_sig, "chevron-down", "Resultado siguiente"),
                                     (b_cerrar, "x", "Cerrar búsqueda")):
            iconos.asignar(boton, icono)
            boton.setAccessibleName(nombre)
        self.campo.setAccessibleName("Texto a buscar")
        b_ant.clicked.connect(self.anterior)
        b_sig.clicked.connect(self.siguiente)
        b_cerrar.clicked.connect(self.cerrar)
        QShortcut(QKeySequence("Shift+Return"), self.campo, self.anterior)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.cerrar)

        capa = QHBoxLayout(self)
        capa.setContentsMargins(10, 6, 10, 6)
        lupa = QLabel(self)
        lupa.setPixmap(iconos.crear_icono("search").pixmap(18, 18))
        capa.addWidget(lupa)
        capa.addWidget(self.campo, 1)
        capa.addWidget(b_ant)
        capa.addWidget(b_sig)
        capa.addWidget(self.etiqueta)
        capa.addWidget(b_cerrar)

    def set_documento(self, documento: Documento | None) -> None:
        self.documento = documento
        self.limpiar()

    def limpiar(self) -> None:
        self.resultados = []
        self.actual = -1
        self._texto_buscado = ""
        self.etiqueta.setText("")
        self.resultados_cambiaron.emit([], -1)

    def abrir(self) -> None:
        self.show()
        self.campo.setFocus()
        self.campo.selectAll()

    def cerrar(self) -> None:
        self.hide()
        self.limpiar()
        self.cerrada.emit()

    def _texto_cambiado(self, _texto: str) -> None:
        # La búsqueda se hace al apretar Enter; acá solo se invalida la anterior
        if self._texto_buscado:
            self.limpiar()

    def buscar(self, texto: str | None = None) -> None:
        if texto is not None:
            self.campo.blockSignals(True)
            self.campo.setText(texto)
            self.campo.blockSignals(False)
        texto = self.campo.text()
        self._texto_buscado = texto
        self.resultados = buscar_en_documento(self.documento.doc, texto) if self.documento else []
        self.actual = 0 if self.resultados else -1
        self._actualizar()

    def siguiente(self) -> None:
        if self.campo.text() != self._texto_buscado or not self.resultados:
            self.buscar()
            return
        self.actual = (self.actual + 1) % len(self.resultados)
        self._actualizar()

    def anterior(self) -> None:
        if self.campo.text() != self._texto_buscado or not self.resultados:
            self.buscar()
            return
        self.actual = (self.actual - 1) % len(self.resultados)
        self._actualizar()

    def rehacer_busqueda(self) -> None:
        """Repite la búsqueda tras un cambio del documento (deshacer, rotar…)."""
        if self._texto_buscado and self.documento is not None:
            previo = self.actual
            self.resultados = buscar_en_documento(self.documento.doc, self._texto_buscado)
            self.actual = min(previo, len(self.resultados) - 1) if self.resultados else -1
            self._actualizar()

    def _actualizar(self) -> None:
        if not self._texto_buscado:
            self.etiqueta.setText("")
        elif not self.resultados:
            self.etiqueta.setText("Sin resultados")
        else:
            self.etiqueta.setText(f"{self.actual + 1} de {len(self.resultados)}")
        self.resultados_cambiaron.emit(self.resultados, self.actual)
