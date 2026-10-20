"""Diálogo "Reemplazar fuente en todo el documento" con vista previa lado a lado."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea, QSpinBox,
                               QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import Embebido, IndiceFuentesSistema
from pdf_studio.core.sustitucion import (UMBRAL_AJUSTE, Candidata, FamiliaEnUso, PlanSustitucion, candidatas,
                                         familias_del_documento, planificar_sustitucion, vista_previa)
from pdf_studio.ui.visor import pixmap_a_qimage

DPI_PREVIA = 80


class DialogoSustitucion(QDialog):
    def __init__(self, doc: pymupdf.Document, incluidas: IndiceFuentesSistema | None,
                 sistema: IndiceFuentesSistema | None, preseleccion: str | None = None,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Reemplazar fuente en todo el documento")
        self.resize(1150, 780)
        self.doc = doc
        self.plan: PlanSustitucion | None = None
        self._previa: pymupdf.Document | None = None
        self.familias: list[FamiliaEnUso] = familias_del_documento(doc)
        self.opciones: list[list[Candidata]] = [candidatas(doc, f, incluidas, sistema) for f in self.familias]

        ayuda = QLabel("Elegí qué fuentes reemplazar. Se priorizan las que tienen <b>el mismo ancho por letra</b>; "
                       "si no hay, se ajusta el ancho de cada línea para que no se corran los saltos de línea, "
                       "la alineación ni las tablas. Revisá la vista previa antes de aplicar.", self)
        ayuda.setWordWrap(True)

        self.tabla = QTableWidget(len(self.familias), 5, self)
        self.tabla.setHorizontalHeaderLabels(["Reemplazar", "Fuente", "Embebido", "Fragmentos", "Reemplazar por"])
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tabla.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.checks: list[QCheckBox] = []
        self.combos: list[QComboBox] = []
        for fila, (fam, cands) in enumerate(zip(self.familias, self.opciones)):
            check = QCheckBox(self.tabla)
            privada = bool(fam.embebidos & {Embebido.SUBCONJUNTO, Embebido.NO_EMBEBIDA})
            # Por defecto se marcan las privadas o recortadas (salvo las 14 estándar de PDF)
            estandar = fam.clave in ("helvetica", "times", "courier", "symbol", "zapfdingbats")
            check.setChecked(fam.clave == preseleccion if preseleccion else (privada and not estandar))
            check.setEnabled(bool(cands))
            check.toggled.connect(self._invalidar)
            self.checks.append(check)
            contenedor = QWidget(self.tabla)
            capa = QHBoxLayout(contenedor)
            capa.setContentsMargins(8, 0, 0, 0)
            capa.addWidget(check)
            self.tabla.setCellWidget(fila, 0, contenedor)
            nombre = QTableWidgetItem(fam.nombre + ("  (privada o recortada)" if privada and not estandar else ""))
            nombre.setToolTip("\n".join(sorted(fam.nombres_pdf)))
            self.tabla.setItem(fila, 1, nombre)
            self.tabla.setItem(fila, 2, QTableWidgetItem(fam.descripcion_embebido))
            self.tabla.setItem(fila, 3, QTableWidgetItem(f"{fam.fragmentos} en {len(fam.paginas)} pág."))
            combo = QComboBox(self.tabla)
            for c in cands:
                combo.addItem(("★ " if c.compatible else "") + c.descripcion)
            combo.currentIndexChanged.connect(self._invalidar)
            self.combos.append(combo)
            self.tabla.setCellWidget(fila, 4, combo)
        self.tabla.resizeColumnsToContents()
        self.tabla.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tabla.setMaximumHeight(36 + 30 * min(6, len(self.familias)))

        self.ajustar = QCheckBox("Ajustar el ancho de cada línea (mantiene saltos de línea, alineación y tablas)", self)
        self.ajustar.setChecked(True)
        self.ajustar.toggled.connect(self._invalidar)
        self.boton_previa = QPushButton("Generar vista previa", self)
        self.boton_previa.clicked.connect(self.generar_previa)

        # Vista previa lado a lado
        self.vista_original = QLabel(self)
        self.vista_nueva = QLabel(self)
        paneles = QSplitter(Qt.Orientation.Horizontal, self)
        for titulo, vista in (("Original", self.vista_original), ("Con la fuente nueva", self.vista_nueva)):
            caja = QWidget()
            c = QVBoxLayout(caja)
            c.setContentsMargins(0, 0, 0, 0)
            c.addWidget(QLabel(f"<b>{titulo}</b>"))
            desplazable = QScrollArea()
            desplazable.setWidgetResizable(True)
            vista.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            desplazable.setWidget(vista)
            c.addWidget(desplazable, 1)
            paneles.addWidget(caja)
        paneles.setStretchFactor(0, 1)
        paneles.setStretchFactor(1, 1)
        paneles.setSizes([1, 1])
        paneles.setChildrenCollapsible(False)
        self.pagina = QSpinBox(self)
        self.pagina.setPrefix("Página ")
        self.pagina.setRange(1, max(1, doc.page_count))
        self.pagina.valueChanged.connect(self._mostrar_pagina)

        self.resumen = QLabel("Generá la vista previa para ver el resultado.", self)
        self.resumen.setWordWrap(True)
        self.lista_ajustes = QListWidget(self)
        self.lista_ajustes.setMaximumHeight(120)
        self.lista_ajustes.itemClicked.connect(lambda it: self.pagina.setValue(it.data(Qt.ItemDataRole.UserRole) + 1))

        self.boton_aplicar = QPushButton("Aplicar", self)
        self.boton_aplicar.setEnabled(False)
        self.boton_aplicar.clicked.connect(self.accept)
        cancelar = QPushButton("Cancelar", self)
        cancelar.clicked.connect(self.reject)

        fila_previa = QHBoxLayout()
        fila_previa.addWidget(self.ajustar, 1)
        fila_previa.addWidget(self.boton_previa)
        fila_pagina = QHBoxLayout()
        fila_pagina.addWidget(self.pagina)
        fila_pagina.addWidget(self.resumen, 1)
        botones = QHBoxLayout()
        botones.addStretch(1)
        botones.addWidget(self.boton_aplicar)
        botones.addWidget(cancelar)

        capa = QVBoxLayout(self)
        capa.addWidget(ayuda)
        capa.addWidget(self.tabla)
        capa.addLayout(fila_previa)
        capa.addLayout(fila_pagina)
        capa.addWidget(paneles, 1)
        capa.addWidget(QLabel(f"Líneas con un ajuste de ancho mayor a ±{UMBRAL_AJUSTE * 100:.0f}% "
                              "(clic para ir a la página):", self))
        capa.addWidget(self.lista_ajustes)
        capa.addLayout(botones)
        self._mostrar_pagina()

    # ------------------------------------------------------------------ datos
    def elecciones(self) -> dict[str, Candidata]:
        res = {}
        for fam, check, combo, cands in zip(self.familias, self.checks, self.combos, self.opciones):
            if check.isChecked() and cands:
                res[fam.clave] = cands[combo.currentIndex()]
        return res

    def _invalidar(self, *_args) -> None:
        self.plan = None
        self.boton_aplicar.setEnabled(False)
        self.resumen.setText("Cambiaste las opciones: generá la vista previa de nuevo.")

    def generar_previa(self) -> None:
        elecciones = self.elecciones()
        if not elecciones:
            self.resumen.setText("Marcá al menos una fuente para reemplazar.")
            return
        self.plan = planificar_sustitucion(self.doc, elecciones, self.ajustar.isChecked())
        if self._previa is not None:
            self._previa.close()
        self._previa = vista_previa(self.doc, self.plan)
        texto = self.plan.resumen()
        if self.plan.omitidos:
            texto += "\nNo se reemplazan: " + "; ".join(self.plan.omitidos[:3])
        self.resumen.setText(texto)
        self.lista_ajustes.clear()
        for a in self.plan.ajustes:
            item = QListWidgetItem(f"Página {a.pagina + 1}: {a.porcentaje:+.1f}%  «{a.texto}»")
            item.setData(Qt.ItemDataRole.UserRole, a.pagina)
            self.lista_ajustes.addItem(item)
        self.boton_aplicar.setEnabled(self.plan.operacion is not None)
        if self.plan.paginas:
            self.pagina.setValue(min(self.plan.paginas) + 1)
        self._mostrar_pagina()

    def _render(self, doc: pymupdf.Document, indice: int) -> QPixmap:
        return QPixmap.fromImage(pixmap_a_qimage(doc[indice].get_pixmap(dpi=DPI_PREVIA)))

    def _mostrar_pagina(self) -> None:
        i = self.pagina.value() - 1
        self.vista_original.setPixmap(self._render(self.doc, i))
        if self._previa is not None:
            self.vista_nueva.setPixmap(self._render(self._previa, i))
        else:
            self.vista_nueva.setText("(sin vista previa)")

    def done(self, resultado: int) -> None:
        if self._previa is not None:
            self._previa.close()
            self._previa = None
        super().done(resultado)
