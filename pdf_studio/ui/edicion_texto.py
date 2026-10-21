"""Diálogo para editar un bloque de texto, con análisis de fuente en vivo."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QButtonGroup, QDialog, QDialogButtonBox, QGroupBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido
from pdf_studio.core.texto import AVISO_INVISIBLE, AnalisisFuente, BloqueTexto, Nivel, OpcionFuente
from pdf_studio.dependencias.catalogo_fuentes import FuenteLibre
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import tarjeta_informativa
from pdf_studio.ui.tema import marcar_primario

TEXTO_NIVEL = {Nivel.FRAGMENTO: "fragmento", Nivel.LINEA: "línea", Nivel.PARRAFO: "párrafo"}


def _caracteres(lista: list[str]) -> str:
    return ", ".join(f"«{c}»" for c in lista[:12]) + ("…" if len(lista) > 12 else "")


class DialogoEditarTexto(QDialog):
    def __init__(self, bloque: BloqueTexto, analizar: Callable[[str], AnalisisFuente],
                 reescanear: Callable[[], None] | None = None, parent: QWidget | None = None,
                 instalar: Callable[[FuenteLibre], bool] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Editar {TEXTO_NIVEL[bloque.nivel]}")
        self.resize(640, 520 if bloque.nivel == Nivel.PARRAFO else 380)
        self.bloque = bloque
        self._analizar = analizar
        self._reescanear = reescanear
        self._instalar = instalar
        self._eleccion_manual: str | None = None
        self.analisis: AnalisisFuente | None = None
        estilo = bloque.estilo

        self.info = QLabel(self)
        self.info.setWordWrap(True)
        # Capa de OCR invisible: aviso informativo (no es un error; se puede editar igual)
        self.aviso_invisible = tarjeta_informativa(AVISO_INVISIBLE, self) if bloque.invisible else None
        self.editor = QPlainTextEdit(bloque.texto, self)
        if bloque.nivel != Nivel.PARRAFO:
            self.editor.setMaximumHeight(90)
        self.editor.textChanged.connect(lambda: self._temporizador.start())

        self.grupo = QGroupBox("Fuente para el texto nuevo", self)
        self.capa_opciones = QVBoxLayout(self.grupo)
        self.botones_opciones = QButtonGroup(self)
        self.botones_opciones.buttonClicked.connect(self._eligio)

        # Fila "instalar la fuente libre": se crea en actualizar(), entre la completa y la sustituta
        self.fila_instalar: QWidget | None = None
        self.boton_instalar: QPushButton | None = None
        self.boton_copiar: QPushButton | None = None
        self.botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setText("Aplicar")
        marcar_primario(self.botones.button(QDialogButtonBox.StandardButton.Ok))
        self.botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.botones.accepted.connect(self.accept)
        self.botones.rejected.connect(self.reject)

        capa = QVBoxLayout(self)
        if self.aviso_invisible is not None:
            capa.addWidget(self.aviso_invisible)
        capa.addWidget(self.info)
        capa.addWidget(QLabel("Texto:", self))
        capa.addWidget(self.editor, 1 if bloque.nivel == Nivel.PARRAFO else 0)
        capa.addWidget(self.grupo)
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
        if estilo.modo == 3 and not self.bloque.invisible:
            partes.append("<i>El comienzo es texto invisible: el texto nuevo también lo será.</i>")
        self.info.setText("<br>".join(partes))

        # Opciones de fuente, en orden: original → completa instalada/incluida → instalar → sustituta
        for boton in self.botones_opciones.buttons():
            self.botones_opciones.removeButton(boton)
        while self.capa_opciones.count():
            item = self.capa_opciones.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self.fila_instalar = self.boton_instalar = self.boton_copiar = None
        elegida = None
        fila_puesta = False
        for i, op in enumerate(a.opciones):
            if op.tipo == "sustituta" and not fila_puesta:
                self._agregar_fila_instalar(a)
                fila_puesta = True
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
        if not fila_puesta:
            self._agregar_fila_instalar(a)
        if elegida is not None:
            elegida.setChecked(True)
        else:
            aviso = QLabel("<b>Ninguna fuente disponible tiene todas las letras del texto.</b> "
                           "Cambiá el texto o instalá la fuente completa.", self.grupo)
            aviso.setWordWrap(True)
            self.capa_opciones.addWidget(aviso)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setEnabled(elegida is not None)

    def _agregar_fila_instalar(self, a: AnalisisFuente) -> None:
        """Fila para instalar la fuente completa cuando es libre y no está instalada."""
        if a.instalable is None:
            return
        fila = QWidget(self.grupo)
        fila.setObjectName("tarjeta")
        capa = QVBoxLayout(fila)
        capa.setContentsMargins(10, 8, 10, 8)
        original = next((o for o in a.opciones if o.tipo == "original"), None)
        motivo = (f"A la fuente del PDF le faltan {_caracteres(original.faltantes)}. "
                  if original is not None and original.faltantes else "")
        texto = QLabel(f"{motivo}<b>«{a.instalable.nombre}»</b> es libre ({a.instalable.licencia}): "
                       "instalala completa y queda disponible al instante.", fila)
        texto.setWordWrap(True)
        botones = QHBoxLayout()
        self.boton_instalar = QPushButton("Instalar fuente", fila)
        marcar_primario(self.boton_instalar)
        iconos.asignar(self.boton_instalar, "download")
        self.boton_instalar.setEnabled(self._instalar is not None)
        self.boton_instalar.clicked.connect(lambda: self._instalar_fuente(a.instalable))
        self.boton_copiar = QPushButton("Copiar comando", fila)
        iconos.asignar(self.boton_copiar, "copy")
        self.boton_copiar.clicked.connect(lambda: self._copiar(a.instalable.comando))
        ya = QPushButton("Ya la instalé", fila)
        ya.setToolTip("Si la instalaste con el comando, buscarla de nuevo")
        ya.clicked.connect(self._buscar_de_nuevo)
        botones.addWidget(self.boton_instalar)
        botones.addWidget(self.boton_copiar)
        botones.addStretch(1)
        botones.addWidget(ya)
        capa.addWidget(texto)
        capa.addLayout(botones)
        self.capa_opciones.addWidget(fila)
        self.fila_instalar = fila

    def _instalar_fuente(self, fuente: FuenteLibre) -> None:
        if self._instalar is not None and self._instalar(fuente):
            if self._reescanear is not None:
                self._reescanear()
            self._eleccion_manual = "sistema"
            self.actualizar()
    def _eligio(self, boton) -> None:
        if self.analisis is not None:
            self._eleccion_manual = self.analisis.opciones[self.botones_opciones.id(boton)].tipo

    def _copiar(self, comando: str) -> None:
        QGuiApplication.clipboard().setText(comando)
        if self.boton_copiar is not None:
            self.boton_copiar.setText("¡Copiado!")
    def _buscar_de_nuevo(self) -> None:
        if self._reescanear is not None:
            self._reescanear()
        self.actualizar()
