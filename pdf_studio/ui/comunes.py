"""Piezas de interfaz compartidas por varios diálogos: selector de páginas, botonera
estándar y la barra de aviso que aparece arriba del visor."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QRadioButton, QToolButton, QVBoxLayout, QWidget)

from pdf_studio.core.rangos import describir_rango, parsear_rango
from pdf_studio.ui import iconos
from pdf_studio.ui.tema import marcar_primario


def botonera(dialogo: QDialog, texto_ok: str, icono_ok: str | None = None) -> QDialogButtonBox:
    """Aceptar (botón principal, con el texto de la acción) y Cancelar."""
    botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, dialogo)
    ok = botones.button(QDialogButtonBox.StandardButton.Ok)
    ok.setText(texto_ok)
    marcar_primario(ok)
    if icono_ok:
        iconos.asignar(ok, icono_ok)
    botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
    botones.accepted.connect(dialogo.accept)
    botones.rejected.connect(dialogo.reject)
    return botones


class SelectorPaginas(QGroupBox):
    """Todas / página actual / rango («1-3, 5, 8-fin»), y opciones extra al principio
    (p. ej. «Páginas sin texto»)."""

    def __init__(self, total: int, actual: int, parent: QWidget | None = None,
                 extras: list[tuple[str, list[int]]] | None = None, titulo: str = "Páginas"):
        super().__init__(titulo, parent)
        self.total = total
        self.actual = actual
        self._grupo = QButtonGroup(self)
        self._opciones: list[tuple[QRadioButton, Callable[[], list[int]]]] = []
        capa = QVBoxLayout(self)
        for texto, paginas in extras or []:
            self._agregar(capa, QRadioButton(texto, self), lambda p=paginas: list(p))
        self._agregar(capa, QRadioButton(f"Todas ({total})", self), lambda: list(range(total)))
        self._agregar(capa, QRadioButton(f"Página actual ({actual + 1})", self), lambda: [actual])
        fila = QHBoxLayout()
        self.radio_rango = QRadioButton("Rango:", self)
        self.campo_rango = QLineEdit(self)
        self.campo_rango.setPlaceholderText("p. ej. 1-3, 5, 8-fin")
        self.campo_rango.setAccessibleName("Rango de páginas")
        self.campo_rango.textEdited.connect(lambda _t: self.radio_rango.setChecked(True))
        fila.addWidget(self.radio_rango)
        fila.addWidget(self.campo_rango, 1)
        capa.addLayout(fila)
        self._grupo.addButton(self.radio_rango)
        self._opciones.append((self.radio_rango, lambda: parsear_rango(self.campo_rango.text(), total)))
        self._opciones[0][0].setChecked(True)

    def _agregar(self, capa: QVBoxLayout, radio: QRadioButton, funcion: Callable[[], list[int]]) -> None:
        capa.addWidget(radio)
        self._grupo.addButton(radio)
        self._opciones.append((radio, funcion))

    def elegir(self, indice: int) -> None:
        self._opciones[indice][0].setChecked(True)

    def set_rango(self, texto: str) -> None:
        self.campo_rango.setText(texto)
        self.radio_rango.setChecked(True)

    def paginas(self) -> list[int]:
        """Las páginas elegidas (índices desde 0). Lanza ValueError si el rango es inválido."""
        for radio, funcion in self._opciones:
            if radio.isChecked():
                paginas = funcion()
                if not paginas:
                    raise ValueError("No hay páginas en esa opción.")
                return paginas
        return list(range(self.total))

    def descripcion(self) -> str:
        try:
            return describir_rango(self.paginas())
        except ValueError:
            return ""


class BarraAviso(QFrame):
    """Aviso discreto arriba del visor, con una acción y un botón para cerrarlo."""

    accion = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("barra_aviso")
        capa = QHBoxLayout(self)
        capa.setContentsMargins(12, 6, 6, 6)
        self.icono = QLabel(self)
        self.texto = QLabel(self)
        self.texto.setWordWrap(True)
        self.boton = QPushButton(self)
        self.boton.clicked.connect(self.accion.emit)
        cerrar = QToolButton(self)
        cerrar.setAutoRaise(True)
        cerrar.setToolTip("Cerrar aviso")
        cerrar.setAccessibleName("Cerrar aviso")
        iconos.asignar(cerrar, "x")
        cerrar.clicked.connect(self.hide)
        capa.addWidget(self.icono)
        capa.addWidget(self.texto, 1)
        capa.addWidget(self.boton)
        capa.addWidget(cerrar)
        self.hide()

    def mostrar(self, texto: str, boton: str, icono: str = "info") -> None:
        self.texto.setText(texto)
        self.boton.setText(boton)
        lado = 18
        self.icono.setPixmap(iconos.crear_icono(icono).pixmap(lado, lado))
        self.icono.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.show()
