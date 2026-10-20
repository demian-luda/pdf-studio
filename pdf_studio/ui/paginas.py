"""Diálogos de la pestaña Páginas: insertar desde otro PDF, separar en archivos, marca de
agua, numeración y compresión."""
from __future__ import annotations

from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QPushButton, QRadioButton, QSlider, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from pdf_studio.core.comprimir import NivelCompresion, ResultadoCompresion, formato_tamano
from pdf_studio.core.documento import ErrorDocumento
from pdf_studio.core.marcas import (FORMATOS_NUMERACION, FUENTES, MarcaDeAgua, NumerarPaginas, OpcionesMarca,
                                    OpcionesNumeracion, Posicion, vista_previa)
from pdf_studio.core.paginas import FuentePDF, grupos_cada
from pdf_studio.core.rangos import parsear_rango
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import SelectorPaginas, botonera
from pdf_studio.ui.visor import pixmap_a_qimage


def _etiqueta_error(padre: QWidget) -> QLabel:
    error = QLabel("", padre)
    error.setObjectName("texto_aviso")
    error.setWordWrap(True)
    error.hide()
    return error


# ---------------------------------------------------------------------- insertar desde PDF
class DialogoInsertarPDF(QDialog):
    """Elegir uno o varios PDFs y dónde insertarlos (unir documentos)."""

    def __init__(self, total: int, actual: int, parent: QWidget | None = None, pedir_archivos=None):
        super().__init__(parent)
        self.setWindowTitle("Insertar páginas de otro PDF")
        self.resize(560, 420)
        self.total, self.actual = total, actual
        self.fuentes: list[FuentePDF] = []
        self.posicion = total
        self._pedir_archivos = pedir_archivos or self._pedir_archivos_dialogo

        self.lista = QListWidget(self)
        self.lista.setAccessibleName("Archivos a insertar")
        agregar = QPushButton("Agregar archivos…", self)
        iconos.asignar(agregar, "file-plus")
        agregar.clicked.connect(self.agregar)
        quitar = QPushButton("Quitar", self)
        iconos.asignar(quitar, "x")
        quitar.clicked.connect(self.quitar)
        arriba = QPushButton("Subir", self)
        iconos.asignar(arriba, "arrow-up")
        arriba.clicked.connect(lambda: self._mover(-1))
        abajo = QPushButton("Bajar", self)
        iconos.asignar(abajo, "arrow-down")
        abajo.clicked.connect(lambda: self._mover(1))
        fila = QHBoxLayout()
        for b in (agregar, quitar, arriba, abajo):
            fila.addWidget(b)
        fila.addStretch(1)

        grupo = QGroupBox("Dónde insertarlas", self)
        capa_grupo = QVBoxLayout(grupo)
        self.radio_final = QRadioButton("Al final del documento (unir)", grupo)
        self.radio_despues = QRadioButton(f"Después de la página actual ({actual + 1})", grupo)
        self.radio_antes = QRadioButton(f"Antes de la página actual ({actual + 1})", grupo)
        self.radio_final.setChecked(True)
        for r in (self.radio_final, self.radio_despues, self.radio_antes):
            capa_grupo.addWidget(r)
        self.error = _etiqueta_error(self)
        self.botones = botonera(self, "Insertar", "file-input")

        capa = QVBoxLayout(self)
        capa.addWidget(QLabel("Las páginas se copian tal cual, sin re-renderizar nada. El orden de la lista "
                              "es el orden en que se insertan.", self))
        capa.addWidget(self.lista, 1)
        capa.addLayout(fila)
        capa.addWidget(grupo)
        capa.addWidget(self.error)
        capa.addWidget(self.botones)

    def _pedir_archivos_dialogo(self) -> list[str]:
        rutas, _ = QFileDialog.getOpenFileNames(self, "Elegir PDFs", "", "Documentos PDF (*.pdf)")
        return rutas

    def agregar(self) -> None:
        for ruta in self._pedir_archivos():
            try:
                fuente = FuentePDF.desde_archivo(ruta)
                n = fuente.cantidad()
            except ErrorDocumento as e:
                self.error.setText(str(e))
                self.error.show()
                continue
            self.fuentes.append(fuente)
            self.lista.addItem(f"{fuente.nombre} — {n} página{'s' if n != 1 else ''}")

    def quitar(self) -> None:
        fila = self.lista.currentRow()
        if fila >= 0:
            self.lista.takeItem(fila)
            del self.fuentes[fila]

    def _mover(self, delta: int) -> None:
        fila = self.lista.currentRow()
        nueva = fila + delta
        if fila < 0 or not 0 <= nueva < self.lista.count():
            return
        item = self.lista.takeItem(fila)
        self.lista.insertItem(nueva, item)
        self.fuentes.insert(nueva, self.fuentes.pop(fila))
        self.lista.setCurrentRow(nueva)

    def accept(self) -> None:
        if not self.fuentes:
            self.error.setText("Agregá al menos un archivo.")
            self.error.show()
            return
        if self.radio_despues.isChecked():
            self.posicion = self.actual + 1
        elif self.radio_antes.isChecked():
            self.posicion = self.actual
        else:
            self.posicion = self.total
        super().accept()


# ---------------------------------------------------------------------- extraer
class DialogoExtraer(QDialog):
    """Qué páginas copiar a un PDF nuevo."""

    def __init__(self, total: int, actual: int, seleccionadas: list[int], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Extraer páginas")
        self.paginas: list[int] = []
        extras = []
        if len(seleccionadas) > 1:
            from pdf_studio.core.rangos import describir_rango
            extras = [(f"Páginas seleccionadas ({describir_rango(seleccionadas)})", seleccionadas)]
        self.selector = SelectorPaginas(total, actual, self, extras=extras)
        if not extras:
            self.selector.elegir(1)          # la página actual
        self.error = _etiqueta_error(self)
        capa = QVBoxLayout(self)
        capa.addWidget(QLabel("Las páginas elegidas se copian a un PDF nuevo, sin re-renderizar nada. "
                              "El documento abierto no cambia.", self))
        capa.addWidget(self.selector)
        capa.addWidget(self.error)
        capa.addWidget(botonera(self, "Elegir destino y extraer", "file-output"))

    def accept(self) -> None:
        try:
            self.paginas = self.selector.paginas()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        super().accept()


# ---------------------------------------------------------------------- separar
class DialogoSeparar(QDialog):
    """Separar el documento en varios archivos."""

    def __init__(self, total: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Separar en archivos")
        self.total = total
        self.grupos: list[list[int]] = []

        self.radio_cada = QRadioButton("Cada", self)
        self.cada = QSpinBox(self)
        self.cada.setRange(1, max(1, total))
        self.cada.setValue(1)
        self.cada.setSuffix(" página(s) por archivo")
        self.cada.valueChanged.connect(lambda _v: self.radio_cada.setChecked(True))
        self.radio_rangos = QRadioButton("Por rangos:", self)
        self.rangos = QLineEdit(self)
        self.rangos.setPlaceholderText("p. ej. 1-3; 4-6; 7-fin  (un archivo por rango)")
        self.rangos.setAccessibleName("Rangos, separados por punto y coma")
        self.rangos.textEdited.connect(lambda _t: self.radio_rangos.setChecked(True))
        self.radio_cada.setChecked(True)
        grupo = QButtonGroup(self)
        grupo.addButton(self.radio_cada)
        grupo.addButton(self.radio_rangos)
        fila1 = QHBoxLayout()
        fila1.addWidget(self.radio_cada)
        fila1.addWidget(self.cada, 1)
        fila2 = QHBoxLayout()
        fila2.addWidget(self.radio_rangos)
        fila2.addWidget(self.rangos, 1)
        self.resumen = QLabel("", self)
        self.resumen.setObjectName("texto_suave")
        self.error = _etiqueta_error(self)
        for senal in (self.cada.valueChanged, self.rangos.textChanged, self.radio_cada.toggled):
            senal.connect(self._actualizar)

        capa = QVBoxLayout(self)
        capa.addWidget(QLabel(f"El documento tiene {total} páginas. Los archivos nuevos se guardan en la "
                              "carpeta que elijas; el documento abierto no cambia.", self))
        capa.addLayout(fila1)
        capa.addLayout(fila2)
        capa.addWidget(self.resumen)
        capa.addWidget(self.error)
        capa.addWidget(botonera(self, "Elegir carpeta y separar", "scissors"))
        self._actualizar()

    def calcular(self) -> list[list[int]]:
        if self.radio_cada.isChecked():
            return grupos_cada(self.total, self.cada.value())
        partes = [p for p in self.rangos.text().split(";") if p.strip()]
        if not partes:
            raise ValueError("Escribí al menos un rango.")
        return [parsear_rango(p, self.total) for p in partes]

    def _actualizar(self, *_a) -> None:
        try:
            n = len(self.calcular())
            self.resumen.setText(f"Se van a crear {n} archivo{'s' if n != 1 else ''}.")
            self.error.hide()
        except ValueError as e:
            self.resumen.setText("")
            self.error.setText(str(e))
            self.error.show()

    def accept(self) -> None:
        try:
            self.grupos = self.calcular()
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        super().accept()


# ---------------------------------------------------------------------- vista previa
class VistaPrevia(QLabel):
    """Render de una página con la operación que se está configurando."""

    def __init__(self, doc: pymupdf.Document, pagina: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.doc, self.pagina = doc, pagina
        self.setMinimumSize(260, 340)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setObjectName("tarjeta")
        self.setAccessibleName("Vista previa")

    def mostrar(self, operacion) -> None:
        if operacion is None:
            self.setText("(sin vista previa)")
            return
        try:
            pix = vista_previa(self.doc, self.pagina, operacion, dpi=50)
        except Exception as e:
            self.setText(f"No se pudo generar la vista previa ({type(e).__name__}).")
            return
        img = QPixmap.fromImage(pixmap_a_qimage(pix))
        self.setPixmap(img.scaled(self.width() - 8, self.height() - 8, Qt.AspectRatioMode.KeepAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation))


class _DialogoConPrevia(QDialog):
    def __init__(self, doc: pymupdf.Document, actual: int, titulo: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.resize(820, 560)
        self.doc = doc
        self.operacion = None
        self.previa = VistaPrevia(doc, actual, self)
        self.selector = SelectorPaginas(doc.page_count, actual, self)
        self.error = _etiqueta_error(self)
        self._reloj = QTimer(self)
        self._reloj.setSingleShot(True)
        self._reloj.setInterval(150)
        self._reloj.timeout.connect(self._refrescar)
        self.formulario = QFormLayout()

    def _armar(self, texto_ok: str, icono_ok: str) -> None:
        izquierda = QVBoxLayout()
        izquierda.addLayout(self.formulario)
        izquierda.addWidget(self.selector)
        izquierda.addWidget(self.error)
        izquierda.addStretch(1)
        cuerpo = QHBoxLayout()
        cuerpo.addLayout(izquierda, 3)
        cuerpo.addWidget(self.previa, 2)
        capa = QVBoxLayout(self)
        capa.addLayout(cuerpo, 1)
        capa.addWidget(botonera(self, texto_ok, icono_ok))
        QTimer.singleShot(0, self._refrescar)

    def cambio(self, *_a) -> None:
        self._reloj.start()

    def construir(self, paginas: list[int]):
        raise NotImplementedError

    def paginas_previa(self) -> list[int]:
        return [self.previa.pagina]

    def _refrescar(self) -> None:
        try:
            op = self.construir(self.paginas_previa())
            self.error.hide()
        except ValueError as e:
            op = None
            self.error.setText(str(e))
            self.error.show()
        self.previa.mostrar(op)

    def accept(self) -> None:
        try:
            self.operacion = self.construir(self.selector.paginas())
        except ValueError as e:
            self.error.setText(str(e))
            self.error.show()
            return
        super().accept()


class BotonColor(QPushButton):
    def __init__(self, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.color = color
        self.setAccessibleName("Elegir color")
        self.clicked.connect(self._elegir)
        self._pintar()

    def _pintar(self) -> None:
        px = QPixmap(16, 16)
        px.fill(QColor(self.color))
        self.setIcon(px)
        self.setText(self.color.upper())

    def _elegir(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, "Color")
        if c.isValid():
            self.color = c.name()
            self._pintar()
            self.clicked_color()

    def clicked_color(self) -> None:
        pass


def _combo_posiciones(padre: QWidget, actual: Posicion) -> QComboBox:
    combo = QComboBox(padre)
    for p in Posicion:
        combo.addItem(p.texto, p.value)      # Qt convertiría el Enum en str
    combo.setCurrentIndex(list(Posicion).index(actual))
    return combo


def _combo_fuentes(padre: QWidget, actual: str) -> QComboBox:
    combo = QComboBox(padre)
    for clave, nombre in FUENTES.items():
        combo.addItem(nombre, clave)
    combo.setCurrentIndex(list(FUENTES).index(actual))
    return combo


# ---------------------------------------------------------------------- marca de agua
class DialogoMarcaAgua(_DialogoConPrevia):
    def __init__(self, doc: pymupdf.Document, actual: int, parent: QWidget | None = None, pedir_imagen=None):
        super().__init__(doc, actual, "Marca de agua", parent)
        o = OpcionesMarca()
        self.imagen: bytes | None = None
        self._pedir_imagen = pedir_imagen

        self.tipo = QTabWidget(self)
        hoja_texto = QWidget(self.tipo)
        f_texto = QFormLayout(hoja_texto)
        self.texto = QLineEdit(o.texto, hoja_texto)
        self.fuente = _combo_fuentes(hoja_texto, o.fuente)
        self.tamano = QSpinBox(hoja_texto)
        self.tamano.setRange(6, 300)
        self.tamano.setValue(int(o.tamano))
        self.tamano.setSuffix(" pt")
        self.color = BotonColor(o.color, hoja_texto)
        self.color.clicked_color = self.cambio
        self.angulo = QSpinBox(hoja_texto)
        self.angulo.setRange(-180, 180)
        self.angulo.setValue(int(o.angulo))
        self.angulo.setSuffix("°")
        f_texto.addRow("Texto:", self.texto)
        f_texto.addRow("Fuente:", self.fuente)
        f_texto.addRow("Tamaño:", self.tamano)
        f_texto.addRow("Color:", self.color)
        f_texto.addRow("Ángulo:", self.angulo)
        hoja_imagen = QWidget(self.tipo)
        f_imagen = QFormLayout(hoja_imagen)
        self.boton_imagen = QPushButton("Elegir imagen…", hoja_imagen)
        iconos.asignar(self.boton_imagen, "image-plus")
        self.boton_imagen.clicked.connect(self.elegir_imagen)
        self.escala = QSpinBox(hoja_imagen)
        self.escala.setRange(5, 100)
        self.escala.setValue(int(o.escala_imagen * 100))
        self.escala.setSuffix(" % del ancho")
        f_imagen.addRow("Imagen:", self.boton_imagen)
        f_imagen.addRow("Tamaño:", self.escala)
        self.tipo.addTab(hoja_texto, "Texto")
        self.tipo.addTab(hoja_imagen, "Imagen")

        self.opacidad = QSlider(Qt.Orientation.Horizontal, self)
        self.opacidad.setRange(5, 100)
        self.opacidad.setValue(int(o.opacidad * 100))
        self.opacidad.setAccessibleName("Opacidad")
        self.etiqueta_opacidad = QLabel(f"{self.opacidad.value()} %", self)
        fila_op = QHBoxLayout()
        fila_op.addWidget(self.opacidad, 1)
        fila_op.addWidget(self.etiqueta_opacidad)
        self.posicion = _combo_posiciones(self, o.posicion)
        self.detras = QCheckBox("Detrás del contenido (puede quedar tapada por imágenes)", self)

        self.formulario.addRow(self.tipo)
        self.formulario.addRow("Opacidad:", fila_op)
        self.formulario.addRow("Posición:", self.posicion)
        self.formulario.addRow(self.detras)
        for senal in (self.texto.textChanged, self.fuente.currentIndexChanged, self.tamano.valueChanged,
                      self.angulo.valueChanged, self.escala.valueChanged, self.opacidad.valueChanged,
                      self.posicion.currentIndexChanged, self.detras.toggled, self.tipo.currentChanged):
            senal.connect(self.cambio)
        self.opacidad.valueChanged.connect(lambda v: self.etiqueta_opacidad.setText(f"{v} %"))
        self._armar("Aplicar marca de agua", "stamp")

    def elegir_imagen(self) -> None:
        from pdf_studio.core.imagenes import EXTENSIONES_IMAGEN
        if self._pedir_imagen is not None:
            ruta = self._pedir_imagen()
        else:
            filtro = "Imágenes (" + " ".join(f"*{e}" for e in EXTENSIONES_IMAGEN) + ")"
            ruta, _ = QFileDialog.getOpenFileName(self, "Imagen para la marca de agua", "", filtro)
        if ruta:
            self.imagen = Path(ruta).read_bytes()
            self.boton_imagen.setText(Path(ruta).name)
            self.tipo.setCurrentIndex(1)
            self.cambio()

    def construir(self, paginas: list[int]) -> MarcaDeAgua:
        es_imagen = self.tipo.currentIndex() == 1
        if es_imagen and self.imagen is None:
            raise ValueError("Elegí una imagen para la marca de agua.")
        o = OpcionesMarca(texto=self.texto.text(), imagen=self.imagen if es_imagen else None,
                          fuente=self.fuente.currentData(), tamano=float(self.tamano.value()),
                          color=self.color.color, opacidad=self.opacidad.value() / 100,
                          angulo=float(self.angulo.value()), posicion=Posicion(self.posicion.currentData()),
                          escala_imagen=self.escala.value() / 100, detras=self.detras.isChecked())
        return MarcaDeAgua(paginas, o)


# ---------------------------------------------------------------------- numeración
class DialogoNumeracion(_DialogoConPrevia):
    def __init__(self, doc: pymupdf.Document, actual: int, parent: QWidget | None = None):
        super().__init__(doc, actual, "Numerar páginas", parent)
        o = OpcionesNumeracion()
        self.formato = QComboBox(self)
        self.formato.setEditable(True)
        for formato, ejemplo in FORMATOS_NUMERACION.items():
            self.formato.addItem(formato)
            self.formato.setItemData(self.formato.count() - 1, f"Ejemplo: {ejemplo}", Qt.ItemDataRole.ToolTipRole)
        self.formato.setCurrentText(o.formato)
        self.formato.setToolTip("{n} = número de página; {total} = cantidad total")
        self.inicio = QSpinBox(self)
        self.inicio.setRange(0, 99999)
        self.inicio.setValue(o.inicio)
        self.posicion = _combo_posiciones(self, o.posicion)
        self.fuente = _combo_fuentes(self, o.fuente)
        self.tamano = QDoubleSpinBox(self)
        self.tamano.setRange(5, 72)
        self.tamano.setValue(o.tamano)
        self.tamano.setSuffix(" pt")
        self.color = BotonColor(o.color, self)
        self.color.clicked_color = self.cambio
        self.margen = QSpinBox(self)
        self.margen.setRange(4, 200)
        self.margen.setValue(int(o.margen))
        self.margen.setSuffix(" pt")
        self.formulario.addRow("Formato:", self.formato)
        self.formulario.addRow("Empezar en:", self.inicio)
        self.formulario.addRow("Posición:", self.posicion)
        self.formulario.addRow("Fuente:", self.fuente)
        self.formulario.addRow("Tamaño:", self.tamano)
        self.formulario.addRow("Color:", self.color)
        self.formulario.addRow("Margen:", self.margen)
        for senal in (self.formato.currentTextChanged, self.inicio.valueChanged, self.posicion.currentIndexChanged,
                      self.fuente.currentIndexChanged, self.tamano.valueChanged, self.margen.valueChanged):
            senal.connect(self.cambio)
        self._armar("Numerar", "list-ordered")

    def construir(self, paginas: list[int]) -> NumerarPaginas:
        o = OpcionesNumeracion(formato=self.formato.currentText(), inicio=self.inicio.value(),
                               posicion=Posicion(self.posicion.currentData()), fuente=self.fuente.currentData(),
                               tamano=self.tamano.value(), color=self.color.color, margen=float(self.margen.value()))
        return NumerarPaginas(paginas, o)

    def paginas_previa(self) -> list[int]:
        """Con todas las páginas elegidas, la vista previa muestra el número que le toca a la
        página actual."""
        try:
            elegidas = self.selector.paginas()
        except ValueError:
            return [self.previa.pagina]
        return elegidas if self.previa.pagina in elegidas else [self.previa.pagina]


# ---------------------------------------------------------------------- compresión
class DialogoComprimir(QDialog):
    def __init__(self, tamano_actual: int, hay_ghostscript: bool, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Comprimir")
        self.resize(560, 0)
        self.nivel = NivelCompresion.BAJO
        self._radios: dict[NivelCompresion, QRadioButton] = {}
        capa = QVBoxLayout(self)
        capa.addWidget(QLabel(f"Tamaño actual: <b>{formato_tamano(tamano_actual)}</b>. Se crea una copia "
                              "comprimida; el documento abierto y el original no cambian.", self))
        grupo = QGroupBox("Nivel", self)
        capa_grupo = QVBoxLayout(grupo)
        botones = QButtonGroup(self)
        for nivel in NivelCompresion:
            radio = QRadioButton(nivel.texto, grupo)
            nota = QLabel(nivel.descripcion, grupo)
            nota.setWordWrap(True)
            nota.setObjectName("texto_aviso" if nivel == NivelCompresion.MAXIMO else "texto_suave")
            if nivel == NivelCompresion.MAXIMO and not hay_ghostscript:
                radio.setEnabled(False)
                nota.setText(nivel.descripcion + " Requiere Ghostscript (ver Faltantes).")
            botones.addButton(radio)
            self._radios[nivel] = radio
            capa_grupo.addWidget(radio)
            capa_grupo.addWidget(nota)
        self._radios[NivelCompresion.MEDIO].setChecked(True)
        capa.addWidget(grupo)
        capa.addWidget(botonera(self, "Comprimir", "shrink"))

    def accept(self) -> None:
        self.nivel = next(n for n, r in self._radios.items() if r.isChecked())
        super().accept()


class DialogoResultadoCompresion(QDialog):
    """Muestra antes/después y la verificación; el usuario decide si guarda."""

    def __init__(self, resultado: ResultadoCompresion, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Resultado de la compresión")
        self.resize(560, 0)
        texto = QLabel(resultado.resumen().replace("\n", "<br><br>"), self)
        texto.setWordWrap(True)
        texto.setTextFormat(Qt.TextFormat.RichText)
        capa = QVBoxLayout(self)
        capa.addWidget(texto)
        ok = resultado.fidelidad.ok and resultado.reduce
        # Si algo no salió bien, guardar no es el botón principal ni lo que pasa con Enter
        botones = botonera(self, "Guardar copia comprimida…" if ok else "Guardar igual…", "save", primario=ok)
        botones.button(botones.StandardButton.Cancel).setText("Descartar")
        capa.addWidget(botones)
