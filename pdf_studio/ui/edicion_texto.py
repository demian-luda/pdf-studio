"""Diálogo para editar un bloque de texto, con análisis de fuente en vivo."""
from __future__ import annotations

import unicodedata
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QTextCursor, QTextFormat
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QDialogButtonBox, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QRadioButton, QTextEdit, QToolButton,
                               QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido
from pdf_studio.core.texto import (AVISO_INVISIBLE, AnalisisFuente, BloqueTexto, CaracterSinGlifo, Nivel, OpcionFuente,
                                   localizar_sin_glifo)
from pdf_studio.dependencias.catalogo_fuentes import FuenteLibre
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import tarjeta_informativa
from pdf_studio.ui.tema import gestor, marcar_primario

TEXTO_NIVEL = {Nivel.FRAGMENTO: "fragmento", Nivel.LINEA: "línea", Nivel.PARRAFO: "párrafo"}


def _caracter(c: str) -> str:
    """El carácter a la vista; los que no se pueden ver (control, uso privado), por su código."""
    return f"U+{ord(c):04X}" if unicodedata.category(c) in ("Cc", "Cf", "Cn", "Co", "Cs") or c == "\ufffd" else f"«{c}»"


def _caracteres(lista: list[str]) -> str:
    return ", ".join(_caracter(c) for c in lista[:12]) + ("…" if len(lista) > 12 else "")


def _descripcion_sin_glifo(c: CaracterSinGlifo) -> str:
    primeras = ", ".join(str(p) for p in c.posiciones[:6]) + ("…" if len(c.posiciones) > 6 else "")
    lugar = "posición" if len(c.posiciones) == 1 else "posiciones"
    return f"<b>{c.codigo}</b> en la {lugar} {primeras}: {c.motivo}."


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
        self.boton_buscar = QToolButton(self)
        self.boton_buscar.setToolTip("Volver a buscar las fuentes instaladas (si recién instalaste una)")
        self.boton_buscar.setAccessibleName("Volver a buscar las fuentes instaladas")
        iconos.asignar(self.boton_buscar, "refresh-cw")
        self.boton_buscar.clicked.connect(self._buscar_de_nuevo)
        self.boton_buscar.setEnabled(reescanear is not None)
        self.resultado_busqueda = QLabel("", self)
        self.resultado_busqueda.setObjectName("texto_suave")
        self.resultado_busqueda.setWordWrap(True)
        self.resultado_busqueda.hide()
        # Capa de OCR invisible: aviso informativo (no es un error; se puede editar igual)
        self.aviso_invisible = tarjeta_informativa(AVISO_INVISIBLE, self) if bloque.invisible else None
        self.editor = QPlainTextEdit(bloque.texto, self)
        if bloque.nivel != Nivel.PARRAFO:
            self.editor.setMaximumHeight(90)
        self.editor.textChanged.connect(lambda: self._temporizador.start())
        self.sin_glifo: list[CaracterSinGlifo] = []
        self._crear_tarjeta_sin_glifo()

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
        encabezado = QHBoxLayout()
        encabezado.addWidget(self.info, 1)
        encabezado.addWidget(self.boton_buscar, 0, Qt.AlignmentFlag.AlignTop)
        capa.addLayout(encabezado)
        capa.addWidget(self.resultado_busqueda)
        capa.addWidget(QLabel("Texto:", self))
        capa.addWidget(self.editor, 1 if bloque.nivel == Nivel.PARRAFO else 0)
        capa.addWidget(self.tarjeta_sin_glifo)
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

    def _crear_tarjeta_sin_glifo(self) -> None:
        self.tarjeta_sin_glifo = QFrame(self)
        self.tarjeta_sin_glifo.setObjectName("tarjeta")
        capa = QVBoxLayout(self.tarjeta_sin_glifo)
        capa.setContentsMargins(10, 8, 10, 8)
        self.etiqueta_sin_glifo = QLabel("", self.tarjeta_sin_glifo)
        self.etiqueta_sin_glifo.setObjectName("texto_tarjeta")
        self.etiqueta_sin_glifo.setWordWrap(True)
        self.boton_reemplazar_espacio = QPushButton("Reemplazar por espacio", self.tarjeta_sin_glifo)
        iconos.asignar(self.boton_reemplazar_espacio, "replace")
        self.boton_reemplazar_espacio.clicked.connect(lambda: self._resolver_sin_glifo(" "))
        self.boton_quitar_caracter = QPushButton("Quitar", self.tarjeta_sin_glifo)
        iconos.asignar(self.boton_quitar_caracter, "eraser")
        self.boton_quitar_caracter.clicked.connect(lambda: self._resolver_sin_glifo(""))
        fila = QHBoxLayout()
        fila.addWidget(self.boton_reemplazar_espacio)
        fila.addWidget(self.boton_quitar_caracter)
        fila.addStretch(1)
        capa.addWidget(self.etiqueta_sin_glifo)
        capa.addLayout(fila)
        self.tarjeta_sin_glifo.hide()

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
        self._actualizar_sin_glifo(a)

        # Opciones de fuente, en orden: original → completa instalada/incluida → instalar → sustituta
        for boton in self.botones_opciones.buttons():
            self.botones_opciones.removeButton(boton)
        while self.capa_opciones.count():
            item = self.capa_opciones.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()         # deleteLater tarda: que no se superponga con las nuevas
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
            detalle = ("Reemplazá o quitá los caracteres sin glifo de arriba." if self.sin_glifo
                       else "Cambiá el texto o instalá la fuente completa.")
            aviso = QLabel(f"<b>Ninguna fuente disponible tiene todas las letras del texto.</b> {detalle}", self.grupo)
            aviso.setWordWrap(True)
            self.capa_opciones.addWidget(aviso)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setEnabled(elegida is not None)

    def _actualizar_sin_glifo(self, a: AnalisisFuente) -> None:
        """Muestra qué caracteres no se pueden dibujar, dónde están y cómo resolverlo."""
        # Del texto original y sin ninguna fuente que lo dibuje: lo que el usuario escribe
        # de más se sigue informando como "faltan" en cada opción
        originales = {g.c for ln in self.bloque.lineas for g in ln.glifos}
        sin_fuente = set.intersection(*(set(o.faltantes) for o in a.opciones)) & originales if a.opciones else set()
        texto = self.texto
        self.sin_glifo = localizar_sin_glifo(texto, self.bloque, sin_fuente)
        self.tarjeta_sin_glifo.setVisible(bool(self.sin_glifo))
        marcas = []
        if self.sin_glifo:
            n = len(self.sin_glifo)
            titulo = "Hay un carácter sin glifo en el texto" if n == 1 else f"Hay {n} caracteres sin glifo en el texto"
            self.etiqueta_sin_glifo.setText(f"<b>{titulo}.</b><br>"
                                            + "<br>".join(_descripcion_sin_glifo(c) for c in self.sin_glifo))
            color = QColor(gestor().paleta.aviso)
            color.setAlpha(90)
            for c in self.sin_glifo:
                for posicion in c.posiciones:
                    marca = QTextEdit.ExtraSelection()
                    marca.format.setBackground(color)
                    marca.format.setProperty(QTextFormat.Property.FullWidthSelection, False)
                    marca.cursor = QTextCursor(self.editor.document())
                    marca.cursor.setPosition(posicion - 1)
                    marca.cursor.setPosition(posicion, QTextCursor.MoveMode.KeepAnchor)
                    marcas.append(marca)
        self.editor.setExtraSelections(marcas)

    def _resolver_sin_glifo(self, reemplazo: str) -> None:
        texto = self.texto
        for c in self.sin_glifo:
            texto = texto.replace(c.caracter, reemplazo)
        # Con el cursor, y no con setPlainText, para que Ctrl+Z del editor lo pueda deshacer
        cursor = QTextCursor(self.editor.document())
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(texto)
        self._temporizador.stop()
        self.actualizar()

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
        ya.setToolTip("Si la instalaste con el comando o por fuera, buscarla de nuevo (sin reiniciar la app)")
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
        """Vuelve a leer las carpetas de fuentes y dice qué encontró: nunca termina en silencio."""
        if self._reescanear is None:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self._reescanear()
        except Exception as e:
            self.resultado_busqueda.setText(f"No se pudieron volver a leer las fuentes instaladas ({type(e).__name__}). "
                                            "Se siguen usando las que ya se conocían.")
            self.resultado_busqueda.show()
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.actualizar()
        encontrada = next((o for o in self.analisis.opciones if o.tipo == "sistema"), None)
        if encontrada is not None:
            mensaje = f"Encontrada: {encontrada.nombre}. Ya se puede usar."
        else:
            mensaje = (f"Se volvió a buscar y «{self.analisis.nombre}» no aparece entre las fuentes instaladas. "
                       "Revisá que el archivo tenga ese peso y estilo.")
        self.resultado_busqueda.setText(mensaje)
        self.resultado_busqueda.show()
