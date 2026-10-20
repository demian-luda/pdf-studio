"""Ventana «Comparar versiones»: las dos versiones lado a lado, con las diferencias marcadas."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from pdf_studio.core.comparar import ResultadoComparacion, abrir
from pdf_studio.ui import iconos
from pdf_studio.ui.tema import gestor, marcar_primario
from pdf_studio.ui.visor import pixmap_a_qimage

ESCALA_MAXIMA = 2.0


class VistaVersion(QScrollArea):
    """Una versión: la página renderizada con las marcas encima."""

    def __init__(self, titulo: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.etiqueta = QLabel(self)
        self.etiqueta.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.etiqueta.setAccessibleName(titulo)
        self.setWidget(self.etiqueta)

    def mostrar(self, pag: pymupdf.Page | None, marcas: list[tuple[list[tuple], str]], escala: float) -> None:
        if pag is None:
            self.etiqueta.setPixmap(QPixmap())
            self.etiqueta.setText("(esta versión no tiene esta página)")
            return
        pix = pag.get_pixmap(matrix=pymupdf.Matrix(escala, escala), alpha=False, annots=True)
        imagen = QPixmap.fromImage(pixmap_a_qimage(pix))
        pintor = QPainter(imagen)
        matriz = pag.rotation_matrix * pymupdf.Matrix(escala, escala)
        for cajas, color in marcas:
            borde = QColor(color)
            relleno = QColor(color)
            relleno.setAlpha(55)
            pintor.setPen(QPen(borde, 1.5))
            pintor.setBrush(QBrush(relleno))
            for r in cajas:
                visto = pymupdf.Rect(r) * matriz
                visto.normalize()
                pintor.drawRect(QRectF(visto.x0 - 1, visto.y0 - 1, visto.width + 2, visto.height + 2))
        pintor.end()
        self.etiqueta.setText("")
        self.etiqueta.setPixmap(imagen)


class DialogoComparar(QDialog):
    exportar = Signal()

    def __init__(self, resultado: ResultadoComparacion, ruta_a: Path, ruta_b: Path,
                 contrasena_a: str | None = None, contrasena_b: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"Comparar: {resultado.nombre_a} ↔ {resultado.nombre_b}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1280, 860)
        self.resultado = resultado
        self.doc_a = abrir(ruta_a, contrasena_a)
        self.doc_b = abrir(ruta_b, contrasena_b)
        self.total = max(self.doc_a.page_count, self.doc_b.page_count)
        self.pagina = 0
        self._reloj = QTimer(self)            # al cambiar el tamaño, se vuelve a ajustar al ancho
        self._reloj.setSingleShot(True)
        self._reloj.setInterval(120)
        self._reloj.timeout.connect(lambda: self.mostrar_pagina(self.pagina))

        self.resumen = QLabel(resultado.resumen(), self)
        self.resumen.setWordWrap(True)
        self.combo = QComboBox(self)
        self.combo.setAccessibleName("Página")
        self.solo_cambios = QCheckBox("Solo páginas con cambios", self)
        self.ver_texto = QCheckBox("Marcar texto", self)
        self.ver_texto.setChecked(True)
        self.ver_visual = QCheckBox("Marcar diferencias visuales", self)
        self.ver_visual.setChecked(True)
        self.anterior = QPushButton("Cambio anterior", self)
        iconos.asignar(self.anterior, "chevron-up")
        self.siguiente = QPushButton("Cambio siguiente", self)
        iconos.asignar(self.siguiente, "chevron-down")
        self.boton_exportar = QPushButton("Exportar informe…", self)
        marcar_primario(self.boton_exportar)
        iconos.asignar(self.boton_exportar, "file-output")
        self.boton_exportar.clicked.connect(self.exportar.emit)
        for b in (self.anterior, self.siguiente, self.boton_exportar):
            b.setAutoDefault(False)
        self.anterior.clicked.connect(lambda: self._saltar(-1))
        self.siguiente.clicked.connect(lambda: self._saltar(1))
        self.combo.currentIndexChanged.connect(self._combo_cambio)
        self.solo_cambios.toggled.connect(self._llenar_combo)
        self.ver_texto.toggled.connect(lambda _v: self.mostrar_pagina(self.pagina))
        self.ver_visual.toggled.connect(lambda _v: self.mostrar_pagina(self.pagina))

        self.vista_a = VistaVersion(f"Versión A: {resultado.nombre_a}", self)
        self.vista_b = VistaVersion(f"Versión B: {resultado.nombre_b}", self)
        # Desplazamiento sincronizado entre las dos versiones
        self.vista_a.verticalScrollBar().valueChanged.connect(self.vista_b.verticalScrollBar().setValue)
        self.vista_b.verticalScrollBar().valueChanged.connect(self.vista_a.verticalScrollBar().setValue)
        lado_a = self._columna(f"A (antes): {resultado.nombre_a}", self.vista_a)
        lado_b = self._columna(f"B (después): {resultado.nombre_b}", self.vista_b)
        divisor = QSplitter(Qt.Orientation.Horizontal, self)
        divisor.addWidget(lado_a)
        divisor.addWidget(lado_b)

        self.lista = QListWidget(self)
        self.lista.setAccessibleName("Cambios de texto")
        self.lista.setMaximumHeight(160)
        self.lista.itemActivated.connect(self._item_elegido)
        self.lista.itemClicked.connect(self._item_elegido)
        self._llenar_lista()

        barra = QHBoxLayout()
        for w in (self.combo, self.anterior, self.siguiente, self.solo_cambios, self.ver_texto, self.ver_visual):
            barra.addWidget(w)
        barra.addStretch(1)
        barra.addWidget(self.boton_exportar)
        capa = QVBoxLayout(self)
        capa.addWidget(self.resumen)
        capa.addLayout(barra)
        capa.addWidget(divisor, 1)
        capa.addWidget(self.lista)
        self.solo_cambios.setChecked(bool(resultado.paginas_con_cambios()))
        self._llenar_combo()

    @staticmethod
    def _columna(titulo: str, vista: QWidget) -> QWidget:
        caja = QWidget()
        capa = QVBoxLayout(caja)
        capa.setContentsMargins(0, 0, 0, 0)
        etiqueta = QLabel(titulo, caja)
        etiqueta.setObjectName("texto_suave")
        capa.addWidget(etiqueta)
        capa.addWidget(vista, 1)
        return caja

    # ------------------------------------------------------------------ navegación
    def paginas_visibles(self) -> list[int]:
        cambios = self.resultado.paginas_con_cambios()
        return cambios if self.solo_cambios.isChecked() and cambios else list(range(self.total))

    def _llenar_combo(self, *_a) -> None:
        con_cambios = set(self.resultado.paginas_con_cambios())
        self.combo.blockSignals(True)
        self.combo.clear()
        for p in self.paginas_visibles():
            self.combo.addItem(f"Página {p + 1}" + ("  •" if p in con_cambios else ""), p)
        self.combo.blockSignals(False)
        visibles = self.paginas_visibles()
        self.mostrar_pagina(self.pagina if self.pagina in visibles else (visibles[0] if visibles else 0))

    def _combo_cambio(self, indice: int) -> None:
        if indice >= 0:
            self.mostrar_pagina(self.combo.itemData(indice))

    def _saltar(self, delta: int) -> None:
        cambios = self.resultado.paginas_con_cambios()
        if not cambios:
            return
        candidatas = [p for p in cambios if (p > self.pagina if delta > 0 else p < self.pagina)]
        if candidatas:
            self.mostrar_pagina(candidatas[0] if delta > 0 else candidatas[-1])

    def _llenar_lista(self) -> None:
        paleta = gestor().paleta
        for c in self.resultado.cambios:
            signo = "−" if c.tipo == "quitado" else "+"
            texto = c.texto if len(c.texto) < 120 else c.texto[:117] + "…"
            item = QListWidgetItem(f"Página {c.pagina + 1}  {signo} {texto}")
            item.setForeground(QColor(paleta.peligro if c.tipo == "quitado" else paleta.exito))
            item.setData(Qt.ItemDataRole.UserRole, c.pagina)
            self.lista.addItem(item)
        if not self.resultado.cambios:
            self.lista.addItem("No hay cambios de texto.")

    def _item_elegido(self, item: QListWidgetItem) -> None:
        pagina = item.data(Qt.ItemDataRole.UserRole)
        if pagina is not None:
            self.mostrar_pagina(int(pagina))

    def mostrar_pagina(self, pagina: int) -> None:
        if getattr(self, "_cerrado", False):
            return              # ya se cerró: los documentos no están abiertos
        self.pagina = max(0, min(pagina, self.total - 1))
        indice = self.combo.findData(self.pagina)
        if indice >= 0 and indice != self.combo.currentIndex():
            self.combo.blockSignals(True)
            self.combo.setCurrentIndex(indice)
            self.combo.blockSignals(False)
        p = self.pagina
        paleta = gestor().paleta
        visual = self.resultado.visual_de(p) if self.ver_visual.isChecked() else None
        zonas = visual.zonas if visual else []
        marcas_a = [(zonas, paleta.aviso)]
        marcas_b = [(zonas, paleta.aviso)]
        if self.ver_texto.isChecked():
            marcas_a.append(([r for c in self.resultado.cambios_de(p, "quitado") for r in c.rects], paleta.peligro))
            marcas_b.append(([r for c in self.resultado.cambios_de(p, "agregado") for r in c.rects], paleta.exito))
        escala = self._escala(p)
        self.vista_a.mostrar(self.doc_a[p] if p < self.doc_a.page_count else None, marcas_a, escala)
        self.vista_b.mostrar(self.doc_b[p] if p < self.doc_b.page_count else None, marcas_b, escala)
        cambios = self.resultado.paginas_con_cambios()
        self.anterior.setEnabled(any(c < p for c in cambios))
        self.siguiente.setEnabled(any(c > p for c in cambios))

    def _escala(self, p: int) -> float:
        """La misma escala para las dos versiones: que la página más ancha entre a lo ancho."""
        anchos = [d[p].rect.width for d in (self.doc_a, self.doc_b) if p < d.page_count]
        disponible = min(self.vista_a.viewport().width(), self.vista_b.viewport().width()) - 16
        return max(0.3, min(ESCALA_MAXIMA, disponible / max(anchos))) if anchos and disponible > 50 else 1.0

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        self._reloj.start()

    def done(self, resultado: int) -> None:
        self._reloj.stop()
        self._cerrado = True
        for doc in (self.doc_a, self.doc_b):
            try:
                doc.close()
            except Exception:
                pass
        super().done(resultado)
