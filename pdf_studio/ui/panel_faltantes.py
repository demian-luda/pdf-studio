"""Panel "Faltantes": qué falta, para qué sirve y el comando de PowerShell para instalarlo."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase, QGuiApplication
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit,
                               QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
                               QTabWidget, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido, InfoFuente
from pdf_studio.dependencias.chequeo import Faltante, Importancia

COLORES = {
    Importancia.NECESARIO: "#c62828",
    Importancia.RECOMENDADO: "#ef6c00",
    Importancia.OPCIONAL: "#546e7a",
}


class TarjetaFaltante(QFrame):
    def __init__(self, faltante: Faltante, parent: QWidget | None = None):
        super().__init__(parent)
        self.faltante = faltante
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("tarjeta")
        capa = QVBoxLayout(self)
        capa.setContentsMargins(10, 8, 10, 8)

        encabezado = QHBoxLayout()
        titulo = QLabel(f"<b>{faltante.titulo}</b>", self)
        titulo.setWordWrap(True)
        etiqueta = QLabel(faltante.importancia.texto, self)
        etiqueta.setStyleSheet(f"color: white; background: {COLORES[faltante.importancia]};"
                               "border-radius: 3px; padding: 1px 6px;")
        encabezado.addWidget(titulo, 1)
        encabezado.addWidget(etiqueta, 0, Qt.AlignmentFlag.AlignTop)
        capa.addLayout(encabezado)

        para_que = QLabel(faltante.para_que, self)
        para_que.setWordWrap(True)
        capa.addWidget(para_que)
        if faltante.detalle:
            detalle = QLabel(f"<i>{faltante.detalle}</i>", self)
            detalle.setWordWrap(True)
            capa.addWidget(detalle)

        self.boton_copiar: QPushButton | None = None
        if faltante.comando:
            capa.addWidget(QLabel("Comando de PowerShell:", self))
            self.texto_comando = QPlainTextEdit(faltante.comando, self)
            self.texto_comando.setReadOnly(True)
            self.texto_comando.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            fuente_mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
            fuente_mono.setPointSize(9)
            self.texto_comando.setFont(fuente_mono)
            lineas = faltante.comando.count("\n") + 1
            alto = self.texto_comando.fontMetrics().lineSpacing() * min(lineas, 10) + 22
            self.texto_comando.setFixedHeight(alto)
            capa.addWidget(self.texto_comando)
            fila = QHBoxLayout()
            self.boton_copiar = QPushButton("Copiar", self)
            self.boton_copiar.clicked.connect(self.copiar)
            self.aviso_copiado = QLabel("", self)
            fila.addWidget(self.boton_copiar)
            fila.addWidget(self.aviso_copiado, 1)
            capa.addLayout(fila)

    def copiar(self) -> None:
        QGuiApplication.clipboard().setText(self.faltante.comando or "")
        self.aviso_copiado.setText("Copiado. Pegalo en PowerShell y apretá Enter.")


class PanelFaltantes(QWidget):
    rechequear = Signal()
    reemplazar_fuente = Signal(str)     # nombre de la fuente del PDF (vacío = elegir en el diálogo)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._generales: list[Faltante] = []
        self._de_documento: list[Faltante] = []

        self.pestanas = QTabWidget(self)

        # Pestaña 1: faltantes
        pagina = QWidget()
        capa_pag = QVBoxLayout(pagina)
        fila = QHBoxLayout()
        self.resumen = QLabel("", pagina)
        self.resumen.setWordWrap(True)
        boton = QPushButton("Volver a chequear", pagina)
        boton.clicked.connect(self.rechequear.emit)
        fila.addWidget(self.resumen, 1)
        fila.addWidget(boton)
        capa_pag.addLayout(fila)
        self.scroll = QScrollArea(pagina)
        self.scroll.setWidgetResizable(True)
        self.contenedor = QWidget()
        self.capa_tarjetas = QVBoxLayout(self.contenedor)
        self.capa_tarjetas.addStretch(1)
        self.scroll.setWidget(self.contenedor)
        capa_pag.addWidget(self.scroll, 1)
        self.pestanas.addTab(pagina, "Faltantes")

        # Pestaña 2: fuentes del documento
        self.tabla = QTableWidget(0, 4, self)
        self.tabla.setHorizontalHeaderLabels(["Fuente", "Embebido", "En Windows", "Páginas"])
        self.tabla.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            self.tabla.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        pagina_fuentes = QWidget()
        capa_fuentes = QVBoxLayout(pagina_fuentes)
        capa_fuentes.setContentsMargins(0, 0, 0, 0)
        capa_fuentes.addWidget(self.tabla, 1)
        self.boton_reemplazar = QPushButton("Reemplazar fuente en todo el documento…", pagina_fuentes)
        self.boton_reemplazar.setToolTip("Para PDFs con fuentes privadas o recortadas: deja el documento uniforme "
                                         "y editable con una fuente libre equivalente.")
        self.boton_reemplazar.clicked.connect(self._pedir_reemplazo)
        capa_fuentes.addWidget(self.boton_reemplazar)
        self.pestanas.addTab(pagina_fuentes, "Fuentes del documento")

        capa = QVBoxLayout(self)
        capa.setContentsMargins(4, 4, 4, 4)
        capa.addWidget(self.pestanas)

    # ------------------------------------------------------------------ datos
    def set_generales(self, faltantes: list[Faltante]) -> None:
        self._generales = faltantes
        self._redibujar()

    def set_documento(self, fuentes: list[InfoFuente] | None, faltantes: list[Faltante]) -> None:
        self._de_documento = faltantes
        self._llenar_tabla(fuentes or [])
        self._redibujar()

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
            self.capa_tarjetas.insertWidget(self.capa_tarjetas.count() - 1, TarjetaFaltante(f, self.contenedor))
        if not todos:
            self.resumen.setText("No falta nada. Todo listo.")
        else:
            necesarios = sum(1 for f in todos if f.importancia == Importancia.NECESARIO)
            texto = f"{len(todos)} elemento(s) faltante(s)"
            if necesarios:
                texto += f", {necesarios} necesario(s)"
            self.resumen.setText(texto + ". Copiá el comando y pegalo en PowerShell.")

    def _pedir_reemplazo(self) -> None:
        fila = self.tabla.currentRow()
        item = self.tabla.item(fila, 0) if fila >= 0 else None
        self.reemplazar_fuente.emit(item.text() if item is not None else "")

    def _llenar_tabla(self, fuentes: list[InfoFuente]) -> None:
        self.tabla.setRowCount(len(fuentes))
        for fila, f in enumerate(fuentes):
            if f.instalada:
                en_windows = "Instalada"
            elif f.incluida:
                en_windows = "No (incluida con PDF Studio)"
            elif f.es_estandar:
                en_windows = "No hace falta (estándar PDF)"
            elif f.embebido == Embebido.TYPE3:
                en_windows = "—"
            else:
                en_windows = "No instalada"
            paginas = ", ".join(str(p + 1) for p in f.paginas[:8]) + ("…" if len(f.paginas) > 8 else "")
            valores = [f.nombre, f.embebido.texto, en_windows, paginas]
            for col, valor in enumerate(valores):
                item = QTableWidgetItem(valor)
                if col == 0:
                    item.setToolTip(f"{f.nombre_pdf}\nTipo: {f.tipo}"
                                    + (f"\nArchivo: {f.instalada.archivo}" if f.instalada else ""))
                if col == 1 and f.embebido == Embebido.SUBCONJUNTO and not (f.instalada or f.incluida):
                    item.setForeground(Qt.GlobalColor.darkYellow)
                if col == 2 and en_windows == "No instalada":
                    item.setForeground(Qt.GlobalColor.red)
                self.tabla.setItem(fila, col, item)
        self.pestanas.setTabText(1, f"Fuentes del documento ({len(fuentes)})")
