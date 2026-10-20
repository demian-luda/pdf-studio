"""Panel lateral «Formulario»: completar los campos del PDF y aplanarlo."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from pdf_studio.core.formularios import Campo
from pdf_studio.ui import iconos
from pdf_studio.ui.panel_faltantes import encabezado_panel
from pdf_studio.ui.tema import marcar_primario

NINGUNA = "(ninguna)"


class PanelFormulario(QWidget):
    aplicar = Signal(dict)              # {nombre del campo: valor nuevo}
    aplanar = Signal()
    ir_a = Signal(int, tuple)           # (página, recuadro del campo) al entrar a un campo

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.campos: list[Campo] = []
        self.editores: dict[str, QWidget] = {}
        self.ayuda = QLabel("", self)
        self.ayuda.setWordWrap(True)
        self.ayuda.setObjectName("texto_suave")
        self.contenido = QWidget(self)
        self.forma = QFormLayout(self.contenido)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.contenido)
        self.boton_aplicar = QPushButton("Aplicar cambios", self)
        marcar_primario(self.boton_aplicar)
        iconos.asignar(self.boton_aplicar, "check")
        self.boton_aplicar.clicked.connect(self._aplicar)
        self.boton_descartar = QPushButton("Descartar", self)
        iconos.asignar(self.boton_descartar, "undo-2")
        self.boton_descartar.clicked.connect(lambda: self.set_campos(self.campos))
        self.boton_aplanar = QPushButton("Aplanar…", self)
        iconos.asignar(self.boton_aplanar, "layers")
        self.boton_aplanar.setToolTip("Convierte los campos en contenido fijo: se ven igual pero ya no se editan")
        self.boton_aplanar.clicked.connect(self.aplanar.emit)
        fila = QHBoxLayout()
        fila.addWidget(self.boton_aplicar)
        fila.addWidget(self.boton_descartar)
        fila.addStretch(1)
        fila.addWidget(self.boton_aplanar)
        capa = QVBoxLayout(self)
        capa.addWidget(encabezado_panel("Formulario", self))
        capa.addWidget(self.ayuda)
        capa.addWidget(scroll, 1)
        capa.addLayout(fila)
        self.set_campos([])

    def set_campos(self, campos: list[Campo]) -> None:
        self.campos = list(campos)
        while self.forma.rowCount():
            self.forma.removeRow(0)
        self.editores = {}
        editables = [c for c in campos if c.editable]
        if not campos:
            self.ayuda.setText("Este documento no tiene campos de formulario.")
        else:
            self.ayuda.setText(f"{len(campos)} campo{'s' if len(campos) != 1 else ''}"
                               f" ({len(editables)} editable{'s' if len(editables) != 1 else ''}). "
                               "Al entrar a un campo, el visor lo muestra en la página.")
        for c in campos:
            editor = self._editor(c)
            editor.setAccessibleName(c.nombre)
            editor.setEnabled(c.editable)
            editor.installEventFilter(self)
            self.editores[c.nombre] = editor
            self.forma.addRow(QLabel(f"{c.nombre}:" if c.editable else f"{c.nombre} (no editable):",
                                     self.contenido), editor)
        hay = bool(editables)
        self.boton_aplicar.setEnabled(hay)
        self.boton_descartar.setEnabled(hay)
        self.boton_aplanar.setEnabled(bool(campos))

    def _editor(self, c: Campo) -> QWidget:
        if c.tipo == "casilla":
            editor = QCheckBox(self.contenido)
            editor.setChecked(bool(c.valor))
            return editor
        if c.tipo in ("opciones", "lista", "radio"):
            editor = QComboBox(self.contenido)
            if c.tipo == "radio":
                editor.addItem(NINGUNA)
            editor.addItems(c.opciones)
            actual = str(c.valor) if c.valor not in (None, "") else NINGUNA
            if editor.findText(actual) >= 0:
                editor.setCurrentText(actual)
            return editor
        if c.tipo == "texto":
            if c.multilinea:
                editor = QPlainTextEdit(str(c.valor or ""), self.contenido)
                editor.setMaximumHeight(90)
                return editor
            return QLineEdit(str(c.valor or ""), self.contenido)
        return QLabel({"firma": "(firma digital)", "boton": "(botón)"}.get(c.tipo, "(no editable)"), self.contenido)

    def valor_de(self, c: Campo):
        editor = self.editores[c.nombre]
        if isinstance(editor, QCheckBox):
            return editor.isChecked()
        if isinstance(editor, QComboBox):
            texto = editor.currentText()
            return "" if texto == NINGUNA else texto
        if isinstance(editor, QPlainTextEdit):
            return editor.toPlainText()
        if isinstance(editor, QLineEdit):
            return editor.text()
        return c.valor

    def cambios(self) -> dict:
        """Solo los campos editables cuyo valor cambió."""
        resultado = {}
        for c in self.campos:
            if not c.editable:
                continue
            nuevo = self.valor_de(c)
            viejo = bool(c.valor) if c.tipo == "casilla" else str(c.valor or "")
            if nuevo != viejo:
                resultado[c.nombre] = nuevo
        return resultado

    def _aplicar(self) -> None:
        cambios = self.cambios()
        if cambios:
            self.aplicar.emit(cambios)

    def eventFilter(self, objeto: QObject, evento: QEvent) -> bool:
        if evento.type() == QEvent.Type.FocusIn:
            for c in self.campos:
                if self.editores.get(c.nombre) is objeto and c.rects:
                    self.ir_a.emit(c.pagina, c.rects[0])
                    break
        return super().eventFilter(objeto, evento)
