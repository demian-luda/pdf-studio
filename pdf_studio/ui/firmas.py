"""Panel de la biblioteca de firmas y diálogo "Quitar fondo"."""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QPushButton, QSlider, QVBoxLayout, QWidget)

from pdf_studio.core.imagenes import (BibliotecaFirmas, OpcionesFondo, luminancia, quitar_fondo,
                                      umbral_automatico)
from pdf_studio.ui import iconos
from pdf_studio.ui.tema import gestor

COLORES_TINTA = [("Color original", None), ("Negro", (20, 20, 20)), ("Azul lapicera", (18, 45, 140))]
LADO_VISTA_PREVIA = 700


def pil_a_qimage(img: Image.Image) -> QImage:
    rgba = img.convert("RGBA")
    datos = rgba.tobytes("raw", "RGBA")
    return QImage(datos, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888).copy()


def con_cuadricula(img: QImage, tam: QSize) -> QPixmap:
    """Dibuja la imagen sobre un damero (para ver la transparencia)."""
    escalada = img.scaled(tam, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pix = QPixmap(escalada.size())
    pintor = QPainter(pix)
    lado = 8
    for y in range(0, pix.height(), lado):
        for x in range(0, pix.width(), lado):
            claro = (x // lado + y // lado) % 2 == 0
            pintor.fillRect(x, y, lado, lado, QColor(255, 255, 255) if claro else QColor(220, 220, 220))
    pintor.drawImage(0, 0, escalada)
    pintor.end()
    return pix


class DialogoQuitarFondo(QDialog):
    """Convierte una imagen escaneada en PNG transparente, con vista previa en vivo."""

    def __init__(self, imagen: Image.Image, nombre: str = "firma", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Quitar fondo")
        self.original = imagen.convert("RGBA")
        # La vista previa se calcula sobre una copia chica para que sea fluida
        self._previa = self.original.copy()
        self._previa.thumbnail((LADO_VISTA_PREVIA, LADO_VISTA_PREVIA))
        self.umbral_auto = umbral_automatico(luminancia(self._previa))
        self.resultado: Image.Image | None = None

        self.vista_original = QLabel(self)
        self.vista_resultado = QLabel(self)
        for v in (self.vista_original, self.vista_resultado):
            v.setMinimumSize(320, 200)
            v.setAlignment(Qt.AlignmentFlag.AlignCenter)
            v.setStyleSheet(f"border: 1px solid {gestor().paleta.borde}; border-radius: 6px;")

        self.umbral = QSlider(Qt.Orientation.Horizontal, self)
        self.umbral.setRange(30, 250)
        self.umbral.setValue(self.umbral_auto)
        self.etiqueta_umbral = QLabel(self)
        boton_auto = QPushButton("Automático", self)
        boton_auto.clicked.connect(lambda: self.umbral.setValue(self.umbral_auto))
        self.suavidad = QSlider(Qt.Orientation.Horizontal, self)
        self.suavidad.setRange(1, 80)
        self.suavidad.setValue(25)
        self.color = QComboBox(self)
        for texto, _ in COLORES_TINTA:
            self.color.addItem(texto)
        self.recortar = QCheckBox("Recortar márgenes vacíos", self)
        self.recortar.setChecked(True)
        self.nombre = QLineEdit(nombre, self)

        fila_umbral = QHBoxLayout()
        fila_umbral.addWidget(self.umbral, 1)
        fila_umbral.addWidget(self.etiqueta_umbral)
        fila_umbral.addWidget(boton_auto)
        form = QFormLayout()
        form.addRow("Umbral (más alto = quita menos):", fila_umbral)
        form.addRow("Suavidad de bordes:", self.suavidad)
        form.addRow("Color de la tinta:", self.color)
        form.addRow("", self.recortar)
        form.addRow("Nombre:", self.nombre)

        grilla = QGridLayout()
        grilla.addWidget(QLabel("Original"), 0, 0)
        grilla.addWidget(QLabel("Resultado (lo cuadriculado es transparente)"), 0, 1)
        grilla.addWidget(self.vista_original, 1, 0)
        grilla.addWidget(self.vista_resultado, 1, 1)

        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        botones.button(QDialogButtonBox.StandardButton.Ok).setText("Guardar en la biblioteca")
        botones.button(QDialogButtonBox.StandardButton.Ok).setProperty("primario", True)
        botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        botones.accepted.connect(self.aceptar)
        botones.rejected.connect(self.reject)

        capa = QVBoxLayout(self)
        capa.addLayout(grilla, 1)
        capa.addLayout(form)
        capa.addWidget(botones)

        self._temporizador = QTimer(self)
        self._temporizador.setSingleShot(True)
        self._temporizador.setInterval(120)
        self._temporizador.timeout.connect(self.actualizar_vista)
        for control in (self.umbral, self.suavidad):
            control.valueChanged.connect(lambda _v: self._temporizador.start())
        self.color.currentIndexChanged.connect(lambda _v: self._temporizador.start())
        self.recortar.toggled.connect(lambda _v: self._temporizador.start())
        self.vista_original.setPixmap(con_cuadricula(pil_a_qimage(self._previa), QSize(320, 260)))
        self.actualizar_vista()

    def opciones(self) -> OpcionesFondo:
        return OpcionesFondo(umbral=self.umbral.value(), suavidad=self.suavidad.value(),
                             color_tinta=COLORES_TINTA[self.color.currentIndex()][1],
                             recortar=self.recortar.isChecked())

    def actualizar_vista(self) -> None:
        self.etiqueta_umbral.setText(str(self.umbral.value()))
        previa = quitar_fondo(self._previa, self.opciones())
        self.vista_resultado.setPixmap(con_cuadricula(pil_a_qimage(previa), QSize(320, 260)))

    def aceptar(self) -> None:
        self.resultado = quitar_fondo(self.original, self.opciones())
        self.accept()


class PanelFirmas(QWidget):
    """Biblioteca de firmas: agregar, quitar fondo, eliminar e insertar."""

    insertar = Signal(Path)
    agregar = Signal()
    quitar_fondo_de = Signal(Path)
    eliminar = Signal(Path)
    aplicar_colocacion = Signal()
    cancelar_colocacion = Signal()

    def __init__(self, biblioteca: BibliotecaFirmas, parent: QWidget | None = None):
        super().__init__(parent)
        self.biblioteca = biblioteca
        self.lista = QListWidget(self)
        self.lista.setViewMode(QListView.ViewMode.IconMode)
        self.lista.setIconSize(QSize(150, 70))
        self.lista.setResizeMode(QListView.ResizeMode.Adjust)
        self.lista.setMovement(QListView.Movement.Static)
        self.lista.setSpacing(6)
        self.lista.setWordWrap(True)
        self.lista.itemDoubleClicked.connect(lambda it: self.insertar.emit(Path(it.data(Qt.ItemDataRole.UserRole))))
        self.lista.currentItemChanged.connect(lambda *_: self._actualizar_botones())

        self.b_insertar = QPushButton("Insertar en la página", self)
        self.b_insertar.setProperty("primario", True)
        self.b_agregar = QPushButton("Agregar firma…", self)
        self.b_fondo = QPushButton("Quitar fondo…", self)
        self.b_eliminar = QPushButton("Eliminar", self)
        for boton, icono in ((self.b_insertar, "signature"), (self.b_agregar, "plus"), (self.b_fondo, "eraser"),
                             (self.b_eliminar, "trash")):
            iconos.asignar(boton, icono)
        self.b_insertar.clicked.connect(lambda: self._con_seleccion(self.insertar))
        self.b_agregar.clicked.connect(self.agregar.emit)
        self.b_fondo.clicked.connect(lambda: self._con_seleccion(self.quitar_fondo_de))
        self.b_eliminar.clicked.connect(lambda: self._con_seleccion(self.eliminar))

        # Controles visibles mientras se ubica una firma
        self.caja_colocacion = QWidget(self)
        capa_col = QVBoxLayout(self.caja_colocacion)
        capa_col.setContentsMargins(0, 0, 0, 0)
        ayuda = QLabel("Arrastrá la firma para moverla. Esquina: tamaño. Círculo: rotar "
                       "(Shift = de a 15°). Flechas: ajuste fino.", self.caja_colocacion)
        ayuda.setWordWrap(True)
        fila_col = QHBoxLayout()
        self.b_aplicar = QPushButton("Aplicar (Enter)", self.caja_colocacion)
        self.b_aplicar.setProperty("primario", True)
        self.b_cancelar = QPushButton("Cancelar (Esc)", self.caja_colocacion)
        iconos.asignar(self.b_aplicar, "check")
        iconos.asignar(self.b_cancelar, "x")
        self.b_aplicar.clicked.connect(self.aplicar_colocacion.emit)
        self.b_cancelar.clicked.connect(self.cancelar_colocacion.emit)
        fila_col.addWidget(self.b_aplicar)
        fila_col.addWidget(self.b_cancelar)
        capa_col.addWidget(ayuda)
        capa_col.addLayout(fila_col)
        self.caja_colocacion.hide()

        self.etiqueta_carpeta = QLabel(f"Carpeta: {biblioteca.carpeta}", self)
        self.etiqueta_carpeta.setObjectName("texto_suave")
        self.etiqueta_carpeta.setWordWrap(True)
        self.etiqueta_carpeta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        grilla = QGridLayout()
        grilla.addWidget(self.b_insertar, 0, 0, 1, 2)
        grilla.addWidget(self.b_agregar, 1, 0)
        grilla.addWidget(self.b_fondo, 1, 1)
        grilla.addWidget(self.b_eliminar, 2, 0)
        capa = QVBoxLayout(self)
        capa.addWidget(self.caja_colocacion)
        capa.addWidget(self.lista, 1)
        capa.addLayout(grilla)
        capa.addWidget(self.etiqueta_carpeta)
        self.recargar()

    def recargar(self) -> None:
        seleccion = self.seleccionada()
        self.lista.clear()
        for ruta in self.biblioteca.listar():
            img = QImage(str(ruta))
            item = QListWidgetItem(QIcon(con_cuadricula(img, self.lista.iconSize())), ruta.stem)
            item.setData(Qt.ItemDataRole.UserRole, str(ruta))
            item.setToolTip(str(ruta))
            self.lista.addItem(item)
            if seleccion == ruta:
                self.lista.setCurrentItem(item)
        if self.lista.currentItem() is None and self.lista.count():
            self.lista.setCurrentRow(self.lista.count() - 1)
        self._actualizar_botones()

    def seleccionada(self) -> Path | None:
        item = self.lista.currentItem()
        return Path(item.data(Qt.ItemDataRole.UserRole)) if item is not None else None

    def seleccionar(self, ruta: Path) -> None:
        for i in range(self.lista.count()):
            if Path(self.lista.item(i).data(Qt.ItemDataRole.UserRole)) == ruta:
                self.lista.setCurrentRow(i)

    def _con_seleccion(self, senal) -> None:
        ruta = self.seleccionada()
        if ruta is not None:
            senal.emit(ruta)

    def _actualizar_botones(self) -> None:
        hay = self.lista.currentItem() is not None
        for b in (self.b_insertar, self.b_fondo, self.b_eliminar):
            b.setEnabled(hay)

    def set_colocando(self, colocando: bool) -> None:
        self.caja_colocacion.setVisible(colocando)
