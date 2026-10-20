"""Diálogos de la redacción real: buscar datos para redactar y confirmar la redacción."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout, QWidget)

from pdf_studio.core.redaccion import (NEGRO, PATRONES, Coincidencia, OpcionesRedaccion, Redaccion, buscar,
                                       compilar)
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import botonera

TEXTO_EXACTO = "Texto exacto"
EXPRESION = "Expresión regular"
ORIGENES = {"texto": "", "anotación": " (en una anotación)", "campo": " (en un campo del formulario)"}


class DialogoBuscarRedactar(QDialog):
    """Busca datos (DNI, CUIT, correos…) o un texto y marca las coincidencias elegidas."""

    def __init__(self, doc: pymupdf.Document, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Buscar datos para redactar")
        self.resize(640, 560)
        self.doc = doc
        self.coincidencias: list[Coincidencia] = []
        self.elegidas: list[Coincidencia] = []

        self.tipo = QComboBox(self)
        for patron in PATRONES:
            self.tipo.addItem(f"{patron.nombre} — {patron.descripcion}", patron.expresion)
        self.tipo.addItem(TEXTO_EXACTO, None)
        self.tipo.addItem(EXPRESION, None)
        self.tipo.setAccessibleName("Qué buscar")
        self.campo = QLineEdit(self)
        self.campo.setPlaceholderText("Texto o expresión a buscar")
        self.campo.setAccessibleName("Texto a buscar")
        self.mayusculas = QCheckBox("Distinguir mayúsculas", self)
        self.boton_buscar = QPushButton("Buscar", self)
        iconos.asignar(self.boton_buscar, "search")
        self.boton_buscar.clicked.connect(self.buscar)
        self.boton_buscar.setAutoDefault(False)
        self.tipo.currentIndexChanged.connect(self._tipo_cambio)
        self.campo.returnPressed.connect(self.buscar)

        self.lista = QListWidget(self)
        self.lista.setAccessibleName("Coincidencias")
        self.lista.itemChanged.connect(lambda _i: self._actualizar_resumen())
        todas = QPushButton("Marcar todas", self)
        todas.clicked.connect(lambda: self._marcar_todas(True))
        ninguna = QPushButton("Ninguna", self)
        ninguna.clicked.connect(lambda: self._marcar_todas(False))
        for b in (todas, ninguna):
            b.setAutoDefault(False)
        self.resumen = QLabel("", self)
        self.resumen.setObjectName("texto_suave")
        self.error = QLabel("", self)
        self.error.setObjectName("texto_aviso")
        self.error.setWordWrap(True)
        self.error.hide()

        forma = QFormLayout()
        forma.addRow("Buscar:", self.tipo)
        fila = QHBoxLayout()
        fila.addWidget(self.campo, 1)
        fila.addWidget(self.mayusculas)
        fila.addWidget(self.boton_buscar)
        forma.addRow("", fila)
        botones_lista = QHBoxLayout()
        botones_lista.addWidget(todas)
        botones_lista.addWidget(ninguna)
        botones_lista.addStretch(1)
        botones_lista.addWidget(self.resumen)
        capa = QVBoxLayout(self)
        ayuda = QLabel("Elegí qué buscar. Las coincidencias marcadas se agregan a la redacción pendiente: "
                       "todavía no se borra nada hasta que apliques la redacción.", self)
        ayuda.setWordWrap(True)
        capa.addWidget(ayuda)
        capa.addLayout(forma)
        capa.addWidget(self.lista, 1)
        capa.addLayout(botones_lista)
        capa.addWidget(self.error)
        self.botones = botonera(self, "Marcar para redactar", "square-dashed")
        capa.addWidget(self.botones)
        self._tipo_cambio()

    def _tipo_cambio(self, *_a) -> None:
        libre = self.tipo.currentData() is None
        self.campo.setEnabled(libre)
        self.mayusculas.setEnabled(libre)
        if libre:
            self.campo.setFocus()

    def _patron(self):
        expresion = self.tipo.currentData()
        if expresion is not None:
            return compilar(expresion, True)
        return compilar(self.campo.text(), self.tipo.currentText() == EXPRESION, self.mayusculas.isChecked())

    def buscar(self) -> None:
        try:
            patron = self._patron()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        self.error.hide()
        self.coincidencias = buscar(self.doc, patron)
        self.lista.blockSignals(True)
        self.lista.clear()
        for c in self.coincidencias:
            item = QListWidgetItem(f"Página {c.pagina + 1}: {c.texto}{ORIGENES.get(c.origen, '')}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.lista.addItem(item)
        self.lista.blockSignals(False)
        if not self.coincidencias:
            self.resumen.setText("No se encontró nada.")
        else:
            self._actualizar_resumen()

    def _marcar_todas(self, marcar: bool) -> None:
        estado = Qt.CheckState.Checked if marcar else Qt.CheckState.Unchecked
        for i in range(self.lista.count()):
            self.lista.item(i).setCheckState(estado)

    def _elegidas(self) -> list[Coincidencia]:
        return [c for i, c in enumerate(self.coincidencias)
                if self.lista.item(i).checkState() == Qt.CheckState.Checked]

    def _actualizar_resumen(self) -> None:
        n = len(self._elegidas())
        self.resumen.setText(f"{n} de {len(self.coincidencias)} marcadas")

    def accept(self) -> None:
        self.elegidas = self._elegidas()
        if not self.elegidas:
            self.error.setText("No hay coincidencias marcadas." if self.coincidencias else "Primero buscá algo.")
            self.error.show()
            return
        super().accept()


class DialogoAplicarRedaccion(QDialog):
    """Confirmación con opciones. Aplicar NO es el botón por defecto: borra contenido."""

    def __init__(self, redaccion: Redaccion, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Aplicar redacción")
        self.resize(560, 0)
        self.opciones = OpcionesRedaccion()
        self.borrar_metadatos = False
        zonas = sum(len(v) for v in redaccion.zonas.values())
        otros = sum(len(v) for v in redaccion.anotaciones.values()) + sum(len(v) for v in redaccion.campos.values())
        paginas = len(redaccion.paginas)
        detalle = f"{zonas} zona{'s' if zonas != 1 else ''}"
        if otros:
            detalle += f" y {otros} anotación(es) o campo(s)"
        texto = QLabel(f"Se va a <b>eliminar del archivo</b> el contenido de {detalle} en {paginas} "
                       f"página{'s' if paginas != 1 else ''}: el texto deja de existir (no solo se tapa). "
                       "Al guardar, el PDF se reescribe completo para que no quede ninguna copia, y se "
                       "verifica que lo redactado ya no se pueda extraer.<br><br>Mientras no cierres el "
                       "documento podés deshacerlo con Ctrl+Z.", self)
        texto.setWordWrap(True)
        texto.setTextFormat(Qt.TextFormat.RichText)
        grupo = QGroupBox("Opciones", self)
        forma = QFormLayout(grupo)
        self.relleno = QComboBox(grupo)
        self.relleno.addItem("Recuadro negro", "negro")
        self.relleno.addItem("Recuadro blanco", "blanco")
        self.relleno.addItem("Sin recuadro (queda el espacio vacío)", "ninguno")
        self.imagenes = QCheckBox("Borrar también la parte de las imágenes que cae en la zona", grupo)
        self.imagenes.setChecked(True)
        self.dibujos = QCheckBox("Borrar líneas y formas que quedan dentro de la zona", grupo)
        self.dibujos.setChecked(True)
        self.metadatos = QCheckBox("Borrar los metadatos (autor, título, programas, fechas)", grupo)
        forma.addRow("Relleno:", self.relleno)
        forma.addRow(self.imagenes)
        forma.addRow(self.dibujos)
        forma.addRow(self.metadatos)
        capa = QVBoxLayout(self)
        capa.addWidget(texto)
        capa.addWidget(grupo)
        capa.addWidget(botonera(self, "Aplicar redacción", "ban", primario=False))

    def accept(self) -> None:
        relleno = {"negro": NEGRO, "blanco": (1.0, 1.0, 1.0), "ninguno": None}[self.relleno.currentData()]
        self.opciones = OpcionesRedaccion(relleno, self.imagenes.isChecked(), self.dibujos.isChecked())
        self.borrar_metadatos = self.metadatos.isChecked()
        super().accept()
