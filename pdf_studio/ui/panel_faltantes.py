"""Paneles de la barra lateral: "Faltantes" (qué instalar) y "Fuentes del documento"."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
                               QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido, InfoFuente
from pdf_studio.dependencias.chequeo import Faltante, Importancia
from pdf_studio.ui import iconos
from pdf_studio.ui.tema import gestor, marcar_primario


def _color_importancia(imp: Importancia) -> str:
    p = gestor().paleta
    return {Importancia.NECESARIO: p.peligro, Importancia.RECOMENDADO: p.aviso,
            Importancia.OPCIONAL: p.texto_suave}[imp]


def encabezado_panel(titulo: str, parent: QWidget) -> QLabel:
    etiqueta = QLabel(titulo, parent)
    etiqueta.setObjectName("titulo_panel")
    return etiqueta


class TarjetaFaltante(QFrame):
    instalar = Signal(object)        # Faltante

    def __init__(self, faltante: Faltante, parent: QWidget | None = None):
        super().__init__(parent)
        self.faltante = faltante
        self.setObjectName("tarjeta")
        capa = QVBoxLayout(self)
        capa.setContentsMargins(10, 8, 10, 8)
        capa.setSpacing(6)

        titulo = QLabel(f"<b>{faltante.titulo}</b>", self)
        titulo.setWordWrap(True)
        capa.addWidget(titulo)
        self.etiqueta = QLabel(faltante.importancia.texto, self)
        color = _color_importancia(faltante.importancia)
        self.etiqueta.setStyleSheet(f"color: {color}; border: 1px solid {color}; border-radius: 8px;"
                                    "padding: 0 6px;")
        fila_etiqueta = QHBoxLayout()
        fila_etiqueta.addWidget(self.etiqueta)
        fila_etiqueta.addStretch(1)
        capa.addLayout(fila_etiqueta)

        para_que = QLabel(faltante.para_que, self)
        para_que.setWordWrap(True)
        capa.addWidget(para_que)
        if faltante.detalle:
            detalle = QLabel(faltante.detalle, self)
            detalle.setObjectName("texto_suave")
            detalle.setWordWrap(True)
            capa.addWidget(detalle)

        self.boton_instalar: QPushButton | None = None
        self.boton_copiar: QPushButton | None = None
        self.texto_comando: QPlainTextEdit | None = None
        fila = QHBoxLayout()
        if faltante.instalacion is not None:
            self.boton_instalar = QPushButton("Instalar", self)
            marcar_primario(self.boton_instalar)
            iconos.asignar(self.boton_instalar, "download")
            self.boton_instalar.setToolTip("Muestra qué se descarga y pide confirmación antes de instalar")
            self.boton_instalar.clicked.connect(lambda: self.instalar.emit(self.faltante))
            fila.addWidget(self.boton_instalar)
        if faltante.comando:
            self.boton_copiar = QPushButton("Copiar comando", self)
            iconos.asignar(self.boton_copiar, "copy")
            self.boton_copiar.clicked.connect(self.copiar)
            fila.addWidget(self.boton_copiar)
            self.boton_ver = QToolButton(self)
            self.boton_ver.setToolTip("Ver el comando")
            self.boton_ver.setAccessibleName("Ver el comando")
            self.boton_ver.setCheckable(True)
            iconos.asignar(self.boton_ver, "chevron-down")
            fila.addWidget(self.boton_ver)
        fila.addStretch(1)
        if fila.count() > 1:
            capa.addLayout(fila)
        if faltante.comando:
            self.texto_comando = QPlainTextEdit(faltante.comando, self)
            self.texto_comando.setReadOnly(True)
            self.texto_comando.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            self.texto_comando.setFont(mono)
            lineas = faltante.comando.count("\n") + 1
            self.texto_comando.setFixedHeight(self.texto_comando.fontMetrics().lineSpacing() * min(lineas, 9) + 18)
            self.texto_comando.setVisible(False)
            self.boton_ver.toggled.connect(self.texto_comando.setVisible)
            capa.addWidget(self.texto_comando)
        self.aviso = QLabel("", self)
        self.aviso.setObjectName("texto_suave")
        capa.addWidget(self.aviso)
        self.aviso.hide()

    def copiar(self) -> None:
        QGuiApplication.clipboard().setText(self.faltante.comando or "")
        self.aviso.setText("Copiado. Pegalo en PowerShell y apretá Enter.")
        self.aviso.show()


class PanelFaltantes(QWidget):
    rechequear = Signal()
    instalar = Signal(object)            # Faltante

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._generales: list[Faltante] = []
        self._de_documento: list[Faltante] = []
        # Estado de cada análisis: None (no corresponde), 'analizando', 'ok' o 'error'
        self._estados: dict[str, str | None] = {"inicio": None, "documento": None}
        self._detalles: dict[str, str] = {}
        encabezado = QHBoxLayout()
        encabezado.addWidget(encabezado_panel("Faltantes", self), 1)
        boton = QToolButton(self)
        boton.setToolTip("Volver a chequear")
        boton.setAccessibleName("Volver a chequear")
        iconos.asignar(boton, "refresh-cw")
        boton.clicked.connect(self.rechequear.emit)
        encabezado.addWidget(boton)
        self.resumen = QLabel("", self)
        self.resumen.setObjectName("texto_suave")
        self.resumen.setWordWrap(True)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.contenedor = QWidget()
        self.capa_tarjetas = QVBoxLayout(self.contenedor)
        self.capa_tarjetas.setContentsMargins(0, 0, 4, 0)
        self.capa_tarjetas.setSpacing(8)
        self.capa_tarjetas.addStretch(1)
        self.scroll.setWidget(self.contenedor)
        capa = QVBoxLayout(self)
        capa.setContentsMargins(10, 10, 6, 10)
        capa.addLayout(encabezado)
        capa.addWidget(self.resumen)
        capa.addWidget(self.scroll, 1)
        gestor().cambio.connect(self._redibujar)

    def set_generales(self, faltantes: list[Faltante]) -> None:
        self._generales = faltantes
        self._redibujar()

    def set_documento(self, faltantes: list[Faltante]) -> None:
        self._de_documento = faltantes
        self._redibujar()

    def set_estado(self, clave: str, estado: str | None, detalle: str = "") -> None:
        self._estados[clave] = estado
        self._detalles[clave] = detalle
        if clave == "documento" and estado in (None, "analizando"):
            self._de_documento = []
        self._redibujar()

    def estado(self, clave: str) -> str | None:
        return self._estados.get(clave)

    @property
    def hay_error(self) -> bool:
        return "error" in self._estados.values()

    @property
    def faltantes(self) -> list[Faltante]:
        return self._de_documento + self._generales

    def tarjetas(self) -> list[TarjetaFaltante]:
        return self.contenedor.findChildren(TarjetaFaltante)

    def _redibujar(self) -> None:
        while self.capa_tarjetas.count() > 1:
            item = self.capa_tarjetas.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        orden = {Importancia.NECESARIO: 0, Importancia.RECOMENDADO: 1, Importancia.OPCIONAL: 2}
        todos = sorted(self.faltantes, key=lambda f: orden[f.importancia])
        for f in todos:
            tarjeta = TarjetaFaltante(f, self.contenedor)
            tarjeta.instalar.connect(self.instalar.emit)
            self.capa_tarjetas.insertWidget(self.capa_tarjetas.count() - 1, tarjeta)
        nombres = {"inicio": "las dependencias", "documento": "las fuentes del documento"}
        analizando = [nombres[k] for k, e in self._estados.items() if e == "analizando"]
        errores = [f"{nombres[k]}: {self._detalles.get(k) or 'error'}" for k, e in self._estados.items()
                   if e == "error"]
        pendiente = self._estados["inicio"] is None and not todos
        partes = []
        if todos:
            necesarios = sum(1 for f in todos if f.importancia == Importancia.NECESARIO)
            texto = f"{len(todos)} elemento(s) faltante(s)"
            if necesarios:
                texto += f", {necesarios} necesario(s)"
            partes.append(texto + ". Podés instalarlos desde acá o copiar el comando.")
        if analizando:
            partes.append("Analizando " + " y ".join(analizando) + "… los resultados aparecen solos.")
        if errores:
            partes.append("No se pudo completar el análisis de " + "; ".join(errores)
                          + ". La lista puede estar incompleta: tocá «Volver a chequear».")
        if not partes:
            # "No falta nada" solo si todos los análisis terminaron bien
            partes.append("Todavía no se chequeó nada." if pendiente else "No falta nada. Todo listo.")
        self.resumen.setText(" ".join(partes))


class PanelFuentes(QWidget):
    reemplazar_fuente = Signal(str)     # nombre de la fuente del PDF (vacío = elegir en el diálogo)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.tabla = QTableWidget(0, 3, self)
        self.tabla.setHorizontalHeaderLabels(["Fuente", "Embebido", "En Windows"])
        self.tabla.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2):
            self.tabla.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.setShowGrid(False)
        self.tabla.setWordWrap(True)
        self.boton_reemplazar = QPushButton("Reemplazar en todo el documento…", self)
        iconos.asignar(self.boton_reemplazar, "replace")
        self.boton_reemplazar.setToolTip("Para PDFs con fuentes privadas o recortadas: deja el documento uniforme "
                                         "y editable con una fuente libre equivalente.")
        self.boton_reemplazar.clicked.connect(self._pedir_reemplazo)
        self.titulo = encabezado_panel("Fuentes del documento", self)
        capa = QVBoxLayout(self)
        capa.setContentsMargins(10, 10, 6, 10)
        capa.addWidget(self.titulo)
        capa.addWidget(self.tabla, 1)
        capa.addWidget(self.boton_reemplazar)

    def _pedir_reemplazo(self) -> None:
        fila = self.tabla.currentRow()
        item = self.tabla.item(fila, 0) if fila >= 0 else None
        self.reemplazar_fuente.emit(item.text() if item is not None else "")

    def set_estado(self, estado: str, detalle: str = "") -> None:
        """'analizando' o 'error' (las fuentes llegan después con set_fuentes)."""
        self.tabla.setRowCount(0)
        if estado == "analizando":
            self.titulo.setText("Fuentes del documento (analizando…)")
        else:
            self.titulo.setText("Fuentes del documento (no se pudo analizar)")
            self.tabla.setRowCount(1)
            item = QTableWidgetItem(detalle or "El análisis falló.")
            item.setForeground(QColor(gestor().paleta.peligro))
            self.tabla.setItem(0, 0, item)
        self.boton_reemplazar.setEnabled(estado != "analizando")

    def set_fuentes(self, fuentes: list[InfoFuente] | None) -> None:
        fuentes = fuentes or []
        self.boton_reemplazar.setEnabled(True)
        p = gestor().paleta
        self.tabla.setRowCount(len(fuentes))
        for fila, f in enumerate(fuentes):
            if f.instalada:
                en_windows = "Instalada"
            elif f.incluida:
                en_windows = "Incluida con PDF Studio"
            elif f.es_estandar:
                en_windows = "No hace falta (estándar)"
            elif f.embebido == Embebido.TYPE3:
                en_windows = "—"
            else:
                en_windows = "No instalada"
            embebido = {Embebido.COMPLETA: "Completa", Embebido.SUBCONJUNTO: "Recortada",
                        Embebido.NO_EMBEBIDA: "No embebida", Embebido.TYPE3: "Type 3",
                        Embebido.NO_ANALIZADA: "No se pudo analizar"}[f.embebido]
            paginas = ", ".join(str(n + 1) for n in f.paginas[:8]) + ("…" if len(f.paginas) > 8 else "")
            for col, valor in enumerate((f.nombre, embebido, en_windows)):
                item = QTableWidgetItem(valor)
                item.setToolTip(f"{f.nombre_pdf}\n{f.embebido.texto}\nTipo: {f.tipo}\nPáginas: {paginas}"
                                + (f"\nArchivo: {f.instalada.archivo}" if f.instalada else ""))
                if col == 1 and f.embebido == Embebido.SUBCONJUNTO and not (f.instalada or f.incluida):
                    item.setForeground(QColor(p.aviso))
                if col == 2 and en_windows == "No instalada":
                    item.setForeground(QColor(p.peligro))
                self.tabla.setItem(fila, col, item)
        self.tabla.resizeRowsToContents()
        self.titulo.setText(f"Fuentes del documento ({len(fuentes)})" if fuentes else "Fuentes del documento")

