"""Paleta de comandos (Ctrl+K): buscar y ejecutar cualquier acción por su nombre."""
from __future__ import annotations

import unicodedata

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QDialog, QHeaderView, QLineEdit, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget


def normalizar(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto.replace("&", ""))
    return "".join(c for c in sin_acentos if not unicodedata.combining(c)).lower()


def nombre_visible(accion: QAction) -> str:
    return accion.text().replace("&", "").split(":")[0].strip()


class PaletaComandos(QDialog):
    def __init__(self, acciones: list[tuple[QAction, str]], parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("paleta_comandos")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.acciones = acciones
        self.campo = QLineEdit(self)
        self.campo.setPlaceholderText("Buscá una acción… (por ejemplo: rotar, guardar, tema)")
        self.campo.setAccessibleName("Buscar acción")
        self.lista = QTreeWidget(self)
        self.lista.setColumnCount(3)
        self.lista.setHeaderHidden(True)
        self.lista.setRootIsDecorated(False)
        self.lista.setAccessibleName("Acciones")
        self.lista.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.lista.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.lista.header().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.lista.itemActivated.connect(lambda *_: self.ejecutar())
        self.campo.textChanged.connect(self.filtrar)
        self.campo.returnPressed.connect(self.ejecutar)
        capa = QVBoxLayout(self)
        capa.setContentsMargins(8, 8, 8, 8)
        capa.addWidget(self.campo)
        capa.addWidget(self.lista)
        self.resize(640, 420)
        self.filtrar("")

    def filtrar(self, texto: str) -> None:
        palabras = normalizar(texto).split()
        self.lista.clear()
        vistos = set()
        for accion, grupo in self.acciones:
            nombre = nombre_visible(accion)
            if not nombre or id(accion) in vistos:
                continue
            pajar = normalizar(f"{nombre} {grupo} {accion.toolTip()} {accion.statusTip()}")
            if all(p in pajar for p in palabras):
                vistos.add(id(accion))
                atajo = accion.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                item = QTreeWidgetItem([nombre, grupo, atajo])
                item.setIcon(0, accion.icon())
                item.setData(0, Qt.ItemDataRole.UserRole, accion)
                if not accion.isEnabled():
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                self.lista.addTopLevelItem(item)
        # Primer resultado habilitado seleccionado
        for i in range(self.lista.topLevelItemCount()):
            item = self.lista.topLevelItem(i)
            if item.flags() & Qt.ItemFlag.ItemIsEnabled:
                self.lista.setCurrentItem(item)
                break

    def resultados(self) -> list[str]:
        return [self.lista.topLevelItem(i).text(0) for i in range(self.lista.topLevelItemCount())]

    def ejecutar(self) -> None:
        item = self.lista.currentItem()
        if item is None or not (item.flags() & Qt.ItemFlag.ItemIsEnabled):
            return
        accion: QAction = item.data(0, Qt.ItemDataRole.UserRole)
        self.accept()
        accion.trigger()

    def keyPressEvent(self, evento) -> None:
        if evento.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
            self.lista.setFocus()
            self.lista.keyPressEvent(evento)
            self.campo.setFocus()
            return
        super().keyPressEvent(evento)
