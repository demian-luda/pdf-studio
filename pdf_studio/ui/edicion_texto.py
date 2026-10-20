"""Diálogo para editar un bloque de texto, con análisis de fuente en vivo."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido
from pdf_studio.core.texto import AnalisisFuente, BloqueTexto, Nivel, OpcionFuente

TEXTO_NIVEL = {Nivel.FRAGMENTO: "fragmento", Nivel.LINEA: "línea", Nivel.PARRAFO: "párrafo"}


def _caracteres(lista: list[str]) -> str:
    return ", ".join(f"«{c}»" for c in lista[:12]) + ("…" if len(lista) > 12 else "")


class DialogoEditarTexto(QDialog):
    def __init__(self, bloque: BloqueTexto, analizar: Callable[[str], AnalisisFuente],
                 reescanear: Callable[[], None] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar {TEXTO_NIVEL[bloque.nivel]}")
        self.resize(640, 520 if bloque.nivel == Nivel.PARRAFO else 380)
        self.bloque = bloque
        self._analizar = analizar
        self._reescanear = reescanear
        self._eleccion_manual: str | None = None
        self.analisis: AnalisisFuente | None = None
        estilo = bloque.estilo

        self.info = QLabel(self)
        self.info.setWordWrap(True)
        self.editor = QPlainTextEdit(bloque.texto, self)
        if bloque.nivel != Nivel.PARRAFO:
            self.editor.setMaximumHeight(90)
        self.editor.textChanged.connect(lambda: self._temporizador.start())

        self.grupo = QGroupBox("Fuente para el texto nuevo", self)
        self.capa_opciones = QVBoxLayout(self.grupo)
        self.botones_opciones = QButtonGroup(self)
        self.botones_opciones.buttonClicked.connect(self._eligio)

        # Fuente libre no instalada: comando para instalarla
        self.caja_instalar = QGroupBox("Instalar la fuente completa (gratis)", self)
        capa_inst = QVBoxLayout(self.caja_instalar)
        self.texto_instalar = QLabel(self.caja_instalar)
        self.texto_instalar.setWordWrap(True)
        self.comando = QPlainTextEdit(self.caja_instalar)
        self.comando.setReadOnly(True)
        self.comando.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.comando.setMaximumHeight(90)
        fila = QHBoxLayout()
        self.boton_copiar = QPushButton("Copiar comando", self.caja_instalar)
        self.boton_copiar.clicked.connect(self._copiar)
        self.boton_buscar = QPushButton("Ya la instalé: buscar de nuevo", self.caja_instalar)
        self.boton_buscar.clicked.connect(self._buscar_de_nuevo)
        fila.addWidget(self.boton_copiar)
        fila.addWidget(self.boton_buscar)
        fila.addStretch(1)
        capa_inst.addWidget(self.texto_instalar)
        capa_inst.addWidget(self.comando)
        capa_inst.addLayout(fila)

        self.botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
        self.botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.botones.accepted.connect(self.accept)
        self.botones.rejected.connect(self.reject)

        capa = QVBoxLayout(self)
        capa.addWidget(self.info)
        capa.addWidget(QLabel("Texto:", self))
        capa.addWidget(self.editor, 1 if bloque.nivel == Nivel.PARRAFO else 0)
        capa.addWidget(self.grupo)
        capa.addWidget(self.caja_instalar)
        if bloque.nivel != Nivel.PARRAFO:
            capa.addStretch(1)
        capa.addWidget(self.botones)

        self._temporizador = QTimer(self)
        self._temporizador.setSingleShot(True)
        self._temporizador.setInterval(250)
        self._temporizador.timeout.connect(self.actualizar)
        r, g, b = estilo.color
        self._color = f"rgb({int(r * 255)}, {int(g * 255)}, {int(b * 255)})"
        self.actualizar()

    # ------------------------------------------------------------------ datos
    @property
    def texto(self) -> str:
        return self.editor.toPlainText()

    def opcion_elegida(self) -> OpcionFuente | None:
        boton = self.botones_opciones.checkedButton()
        if boton is None or self.analisis is None:
            return None
        return self.analisis.opciones[self.botones_opciones.id(boton)]

    # ------------------------------------------------------------------ actualización
    def actualizar(self) -> None:
        self.analisis = self._analizar(self.texto)
        a = self.analisis
        estilo = self.bloque.estilo
        embebido = a.embebido.texto if a.embebido is not None else "desconocido"
        instalada = next((o for o in a.opciones if o.tipo == "sistema"), None)
        partes = [f"<b>Fuente:</b> {a.nombre} — {embebido}",
                  f"<b>Tamaño:</b> {estilo.tamano:.1f} pt · <b>Color:</b> "
                  f"<span style='background:{self._color}'>&nbsp;&nbsp;&nbsp;&nbsp;</span>",
                  "<b>En Windows:</b> " + (f"instalada ({instalada.descripcion.split('(')[-1].rstrip(')')})"
                                           if instalada else "no instalada")]
        if estilo.modo == 3:
            partes.append("<i>Es texto invisible (capa de OCR): seguirá siendo invisible.</i>")
        self.info.setText("<br>".join(partes))

        # Opciones de fuente
        for boton in self.botones_opciones.buttons():
            self.botones_opciones.removeButton(boton)
            boton.deleteLater()
        while self.capa_opciones.count():
            item = self.capa_opciones.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        elegida = None
        for i, op in enumerate(a.opciones):
            texto = op.descripcion
            if op.tipo == "original" and a.embebido == Embebido.SUBCONJUNTO and op.disponible:
                texto += " (recortada, pero tiene todas las letras necesarias)"
            if not op.disponible:
                texto += f" — faltan: {_caracteres(op.faltantes)}"
            boton = QRadioButton(texto, self.grupo)
            boton.setEnabled(op.disponible)
            self.botones_opciones.addButton(boton, i)
            self.capa_opciones.addWidget(boton)
            if op.disponible and (elegida is None or op.tipo == self._eleccion_manual):
                elegida = boton
        if elegida is not None:
            elegida.setChecked(True)
        else:
            aviso = QLabel("<b>Ninguna fuente disponible tiene todas las letras del texto.</b> "
                           "Cambiá el texto o instalá la fuente completa.", self.grupo)
            aviso.setWordWrap(True)
            self.capa_opciones.addWidget(aviso)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setEnabled(elegida is not None)

        if a.comando_instalacion:
            original = next((o for o in a.opciones if o.tipo == "original"), None)
            motivo = (f"A la fuente del PDF le faltan {_caracteres(original.faltantes)}. "
                      if original is not None and original.faltantes else "")
            self.texto_instalar.setText(
                f"{motivo}«{a.nombre}» es una fuente libre ({a.licencia}). Copiá el comando, "
                "pegalo en PowerShell y después tocá «Ya la instalé».")
            self.comando.setPlainText(a.comando_instalacion)
            self.caja_instalar.show()
        else:
            self.caja_instalar.hide()

    def _eligio(self, boton) -> None:
        if self.analisis is not None:
            self._eleccion_manual = self.analisis.opciones[self.botones_opciones.id(boton)].tipo

    def _copiar(self) -> None:
        QGuiApplication.clipboard().setText(self.comando.toPlainText())
        self.boton_copiar.setText("¡Copiado!")

    def _buscar_de_nuevo(self) -> None:
        if self._reescanear is not None:
            self._reescanear()
        self.actualizar()
