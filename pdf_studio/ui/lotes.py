"""Procesamiento por lotes: elegir la carpeta y las tareas, y ver el informe al terminar."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from pdf_studio.core.comprimir import NivelCompresion
from pdf_studio.core.lotes import (AVISO, CARPETA_RESULTADOS, ERROR, OK, OMITIDO, TEXTO_ESTADO, InformeLote,
                                   OpcionesLote, listar_pdfs)
from pdf_studio.core.marcas import FORMATOS_NUMERACION, OpcionesMarca, OpcionesNumeracion, Posicion
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import botonera, tarjeta_informativa
from pdf_studio.ui.tema import marcar_primario

ICONOS_ESTADO = {OK: "circle-check", AVISO: "circle-alert", ERROR: "square-x", OMITIDO: "x"}


def _combo_posicion(padre: QWidget, actual: Posicion) -> QComboBox:
    combo = QComboBox(padre)
    for p in Posicion:
        combo.addItem(p.texto, p.value)
    combo.setCurrentIndex(list(Posicion).index(actual))
    return combo


class DialogoLotes(QDialog):
    def __init__(self, carpeta: str = "", hay_ocr: bool = True, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Procesar por lotes")
        self.setMinimumWidth(600)
        self._cantidad = 0

        self.carpeta = QLineEdit(carpeta, self)
        self.carpeta.setPlaceholderText("Carpeta con los PDFs")
        self.carpeta.setAccessibleName("Carpeta con los PDFs")
        elegir = QPushButton("Examinar…", self)
        iconos.asignar(elegir, "folder-open")
        elegir.setAutoDefault(False)
        elegir.clicked.connect(lambda: self._elegir(self.carpeta, "Elegir la carpeta con los PDFs"))
        self.subcarpetas = QCheckBox("Incluir las subcarpetas", self)
        self.cantidad = QLabel("", self)
        self.cantidad.setObjectName("texto_suave")
        self.destino = QLineEdit(self)
        self.destino.setAccessibleName("Carpeta de destino")
        elegir_destino = QPushButton("Examinar…", self)
        iconos.asignar(elegir_destino, "folder-open")
        elegir_destino.setAutoDefault(False)
        elegir_destino.clicked.connect(lambda: self._elegir(self.destino, "Elegir dónde guardar los resultados"))

        tareas = QGroupBox("Tareas (se aplican en este orden)", self)
        grilla = QGridLayout(tareas)
        motivo_ocr = "" if hay_ocr else " (falta Tesseract: ver Faltantes)"
        self.ocr = QCheckBox("Reconocer texto (OCR) en las páginas escaneadas" + motivo_ocr, tareas)
        self.ocr.setToolTip("Modo «Idéntico»: capa de texto invisible, el aspecto no cambia.")
        self.ocr.setEnabled(hay_ocr)

        marca = OpcionesMarca()
        self.marca = QCheckBox("Marca de agua:", tareas)
        self.marca_texto = QLineEdit(marca.texto, tareas)
        self.marca_texto.setAccessibleName("Texto de la marca de agua")
        self.marca_opacidad = QSpinBox(tareas)
        self.marca_opacidad.setRange(5, 100)
        self.marca_opacidad.setValue(int(marca.opacidad * 100))
        self.marca_opacidad.setSuffix(" % opacidad")
        self.marca_opacidad.setAccessibleName("Opacidad de la marca de agua")
        self.marca_posicion = _combo_posicion(tareas, marca.posicion)
        self.marca_posicion.setAccessibleName("Posición de la marca de agua")

        numeracion = OpcionesNumeracion()
        self.numerar = QCheckBox("Numerar páginas:", tareas)
        self.formato = QComboBox(tareas)
        for formato, ejemplo in FORMATOS_NUMERACION.items():
            self.formato.addItem(ejemplo, formato)
        self.formato.setCurrentIndex(max(0, self.formato.findData(numeracion.formato)))
        self.formato.setAccessibleName("Formato de la numeración")
        self.numero_posicion = _combo_posicion(tareas, numeracion.posicion)
        self.numero_posicion.setAccessibleName("Posición del número de página")

        self.comprimir = QCheckBox("Comprimir:", tareas)
        self.nivel = QComboBox(tareas)
        for n in NivelCompresion:
            self.nivel.addItem(n.texto, n.value)
        self.nivel.setCurrentIndex(list(NivelCompresion).index(NivelCompresion.MEDIO))
        self.nivel.setAccessibleName("Nivel de compresión")

        self.editable = QCheckBox("Además, convertir las páginas escaneadas a Word editable (.docx)" + motivo_ocr,
                                  tareas)
        self.editable.setToolTip("Sin la pantalla de revisión: las palabras dudosas quedan para revisar en Word.")
        self.editable.setEnabled(hay_ocr)

        grilla.addWidget(self.ocr, 0, 0, 1, 4)
        grilla.addWidget(self.marca, 1, 0)
        grilla.addWidget(self.marca_texto, 1, 1)
        grilla.addWidget(self.marca_opacidad, 1, 2)
        grilla.addWidget(self.marca_posicion, 1, 3)
        grilla.addWidget(self.numerar, 2, 0)
        grilla.addWidget(self.formato, 2, 1)
        grilla.addWidget(self.numero_posicion, 2, 3)
        grilla.addWidget(self.comprimir, 3, 0)
        grilla.addWidget(self.nivel, 3, 1, 1, 3)
        grilla.addWidget(self.editable, 4, 0, 1, 4)
        grilla.setColumnStretch(1, 1)

        forma = QFormLayout()
        fila = QHBoxLayout()
        fila.addWidget(self.carpeta, 1)
        fila.addWidget(elegir)
        forma.addRow("Carpeta:", fila)
        fila_sub = QHBoxLayout()
        fila_sub.addWidget(self.subcarpetas)
        fila_sub.addStretch(1)
        fila_sub.addWidget(self.cantidad)
        forma.addRow("", fila_sub)
        fila = QHBoxLayout()
        fila.addWidget(self.destino, 1)
        fila.addWidget(elegir_destino)
        forma.addRow("Resultados en:", fila)

        self.botones = botonera(self, "Procesar", "folder-cog")
        self.boton_procesar = self.botones.button(QDialogButtonBox.StandardButton.Ok)
        capa = QVBoxLayout(self)
        capa.addLayout(forma)
        capa.addWidget(tareas)
        capa.addWidget(tarjeta_informativa(
            "Los originales no se modifican: cada resultado va a la carpeta de destino con el mismo nombre (sin "
            "pisar nada). Cada guardado se verifica; si un archivo falla, queda anotado en el registro y se sigue "
            "con el siguiente. Los PDFs protegidos con contraseña se saltean.", self))
        capa.addWidget(self.botones)

        for control in (self.marca, self.numerar, self.comprimir):
            control.toggled.connect(self._actualizar)
        for control in (self.ocr, self.editable):
            control.toggled.connect(self._actualizar)
        self.carpeta.textChanged.connect(self._carpeta_cambio)
        self.subcarpetas.toggled.connect(self._carpeta_cambio)
        self._carpeta_cambio()

    def _elegir(self, campo: QLineEdit, titulo: str) -> None:
        carpeta = QFileDialog.getExistingDirectory(self, titulo, campo.text() or self.carpeta.text())
        if carpeta:
            campo.setText(str(Path(carpeta)))

    def _carpeta_cambio(self) -> None:
        texto = self.carpeta.text().strip()
        self.destino.setPlaceholderText(str(Path(texto) / CARPETA_RESULTADOS) if texto else "")
        self._cantidad = 0
        if texto and Path(texto).is_dir():
            try:
                destino = Path(self.destino.text().strip() or Path(texto) / CARPETA_RESULTADOS)
                self._cantidad = len(listar_pdfs(texto, self.subcarpetas.isChecked(), excluir=destino))
            except Exception:
                self._cantidad = 0
            self.cantidad.setText(f"{self._cantidad} PDF{'s' if self._cantidad != 1 else ''}")
        else:
            self.cantidad.setText("La carpeta no existe" if texto else "")
        self._actualizar()

    def _actualizar(self) -> None:
        self.marca_texto.setEnabled(self.marca.isChecked())
        self.marca_opacidad.setEnabled(self.marca.isChecked())
        self.marca_posicion.setEnabled(self.marca.isChecked())
        self.formato.setEnabled(self.numerar.isChecked())
        self.numero_posicion.setEnabled(self.numerar.isChecked())
        self.nivel.setEnabled(self.comprimir.isChecked())
        alguna = any(c.isChecked() for c in (self.ocr, self.marca, self.numerar, self.comprimir, self.editable))
        self.boton_procesar.setEnabled(alguna and self._cantidad > 0)

    def resultado(self) -> OpcionesLote:
        marca = numeracion = None
        if self.marca.isChecked():
            o = OpcionesMarca(texto=self.marca_texto.text().strip() or "COPIA",
                              opacidad=self.marca_opacidad.value() / 100,
                              posicion=Posicion(self.marca_posicion.currentData()))
            marca = {k: (v.value if isinstance(v, Posicion) else v) for k, v in asdict(o).items() if k != "imagen"}
        if self.numerar.isChecked():
            o = OpcionesNumeracion(formato=self.formato.currentData(),
                                   posicion=Posicion(self.numero_posicion.currentData()))
            numeracion = {k: (v.value if isinstance(v, Posicion) else v) for k, v in asdict(o).items()}
        return OpcionesLote(self.carpeta.text().strip(), self.destino.text().strip(), self.subcarpetas.isChecked(),
                            self.ocr.isChecked(), self.nivel.currentData() if self.comprimir.isChecked() else None,
                            marca, numeracion, self.editable.isChecked())


class DialogoInformeLote(QDialog):
    def __init__(self, informe: InformeLote, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Procesar por lotes")
        self.resize(680, 480)
        self.informe = informe
        capa = QVBoxLayout(self)
        titulo = QLabel(f"<b>{informe.resumen()}</b>", self)
        titulo.setWordWrap(True)
        capa.addWidget(titulo)
        self.lista = QListWidget(self)
        self.lista.setAccessibleName("Resultado de cada archivo")
        for r in informe.resultados:
            texto = f"{r.archivo} — {TEXTO_ESTADO[r.estado]}"
            if r.mensajes:
                texto += "\n" + "; ".join(r.mensajes)
            item = QListWidgetItem(iconos.crear_icono(ICONOS_ESTADO[r.estado]), texto)
            item.setToolTip("\n".join(r.salidas) if r.salidas else "Sin archivos nuevos")
            self.lista.addItem(item)
        capa.addWidget(self.lista, 1)
        registro = QLabel(f"Registro: {informe.registro}", self)
        registro.setObjectName("texto_suave")
        registro.setWordWrap(True)
        registro.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        capa.addWidget(registro)
        botones = QDialogButtonBox(self)
        carpeta = botones.addButton("Abrir la carpeta de resultados", QDialogButtonBox.ButtonRole.ActionRole)
        iconos.asignar(carpeta, "folder-open")
        carpeta.clicked.connect(lambda: self._abrir(informe.destino))
        ver = botones.addButton("Ver el registro", QDialogButtonBox.ButtonRole.ActionRole)
        iconos.asignar(ver, "clipboard-list")
        ver.clicked.connect(lambda: self._abrir(informe.registro))
        cerrar = botones.addButton("Cerrar", QDialogButtonBox.ButtonRole.RejectRole)
        marcar_primario(cerrar)
        botones.rejected.connect(self.reject)
        for b in (carpeta, ver):
            b.setAutoDefault(False)
        capa.addWidget(botones)

    @staticmethod
    def _abrir(ruta: str) -> None:
        if ruta and Path(ruta).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))
