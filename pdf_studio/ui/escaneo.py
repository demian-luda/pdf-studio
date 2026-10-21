"""Diálogo «Importar imágenes como PDF escaneado»: lista de fotos, esquinas del papel
ajustables, vista previa del resultado y opciones (recorte, enderezado, mejora, tamaño de
papel, resolución y OCR)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QSizePolicy, QSplitter, QToolButton, QVBoxLayout, QWidget)

from pdf_studio.core import escaneo
from pdf_studio.core.documento import ErrorDocumento
from pdf_studio.core.escaneo import AjusteFoto, Mejora, OpcionesEscaneo, TamanoPapel
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import botonera, tarjeta_informativa
from pdf_studio.ui.tema import gestor

LADO_PREVIA = 1100              # la vista previa se calcula sobre una copia de este tamaño
FILTRO_IMAGENES = "Imágenes (" + " ".join(f"*{e}" for e in escaneo.EXTENSIONES) + ")"
RESOLUCIONES = (150, 200, 300)


def a_qimage(img: np.ndarray) -> QImage:
    img = np.ascontiguousarray(img)
    h, w = img.shape[:2]
    if img.ndim == 2:
        return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_Grayscale8).copy()
    return QImage(img.data, w, h, img.strides[0], QImage.Format.Format_BGR888).copy()


class EditorEsquinas(QWidget):
    """La foto con el contorno del papel. Las cuatro esquinas se arrastran con el mouse o se
    mueven con el teclado (1-4 o Espacio elige la esquina; flechas la mueven, Mayús = más)."""

    cambiaron = Signal(list)                       # esquinas normalizadas

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Esquinas del papel")
        self.setAccessibleDescription("Elegí una esquina con 1 a 4 o Espacio y movela con las flechas.")
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._imagen: QPixmap | None = None
        self.esquinas: list[tuple[float, float]] = []
        self.activa = 0
        self._arrastrando: int | None = None

    def mostrar(self, imagen: QPixmap | None, esquinas: list[tuple[float, float]] | None) -> None:
        self._imagen = imagen
        self.esquinas = list(esquinas) if esquinas else [(0, 0), (1, 0), (1, 1), (0, 1)]
        self.update()

    def _caja(self) -> QRectF:
        if self._imagen is None or self._imagen.isNull():
            return QRectF()
        tam = QSize(self._imagen.size())
        tam.scale(self.size() - QSize(20, 20), Qt.AspectRatioMode.KeepAspectRatio)
        return QRectF((self.width() - tam.width()) / 2, (self.height() - tam.height()) / 2, tam.width(), tam.height())

    def _a_pantalla(self, p: tuple[float, float]) -> QPointF:
        caja = self._caja()
        return QPointF(caja.x() + p[0] * caja.width(), caja.y() + p[1] * caja.height())

    def paintEvent(self, _evento) -> None:
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.Antialiasing)
        paleta = gestor().paleta
        pintor.fillRect(self.rect(), QColor(paleta.lienzo))
        caja = self._caja()
        if caja.isNull():
            pintor.setPen(QColor(paleta.texto_suave))
            pintor.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Agregá fotos para empezar")
            return
        pintor.drawPixmap(caja.toRect(), self._imagen)
        acento = QColor(paleta.acento)
        puntos = [self._a_pantalla(p) for p in self.esquinas]
        pintor.setPen(QPen(acento, 2))
        relleno = QColor(acento)
        relleno.setAlpha(40)
        pintor.setBrush(relleno)
        pintor.drawPolygon(QPolygonF(puntos))
        for i, p in enumerate(puntos):
            radio = 9 if i == self.activa and self.hasFocus() else 7
            pintor.setPen(QPen(QColor(paleta.sobre_acento), 2))
            pintor.setBrush(acento)
            pintor.drawEllipse(p, radio, radio)

    def _esquina_cercana(self, pos: QPointF) -> int | None:
        distancias = [((self._a_pantalla(p) - pos).manhattanLength(), i) for i, p in enumerate(self.esquinas)]
        d, i = min(distancias) if distancias else (1e9, None)
        return i if d < 28 else None

    def _mover_a(self, i: int, pos: QPointF) -> None:
        caja = self._caja()
        if caja.isNull():
            return
        x = min(1.0, max(0.0, (pos.x() - caja.x()) / caja.width()))
        y = min(1.0, max(0.0, (pos.y() - caja.y()) / caja.height()))
        self.esquinas[i] = (x, y)
        self.update()

    def mousePressEvent(self, evento) -> None:
        i = self._esquina_cercana(evento.position())
        if i is not None:
            self._arrastrando = self.activa = i
            self.update()

    def mouseMoveEvent(self, evento) -> None:
        if self._arrastrando is not None:
            self._mover_a(self._arrastrando, evento.position())

    def mouseReleaseEvent(self, _evento) -> None:
        if self._arrastrando is not None:
            self._arrastrando = None
            self.cambiaron.emit(list(self.esquinas))

    def keyPressEvent(self, evento) -> None:
        tecla = evento.key()
        if Qt.Key.Key_1 <= tecla <= Qt.Key.Key_4:
            self.activa = tecla - Qt.Key.Key_1
            self.update()
            return
        if tecla == Qt.Key.Key_Space:
            self.activa = (self.activa + 1) % 4
            self.update()
            return
        pasos = {Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0), Qt.Key.Key_Up: (0, -1), Qt.Key.Key_Down: (0, 1)}
        if tecla in pasos and self.esquinas:
            paso = 0.02 if evento.modifiers() & Qt.KeyboardModifier.ShiftModifier else 0.004
            x, y = self.esquinas[self.activa]
            dx, dy = pasos[tecla]
            self.esquinas[self.activa] = (min(1.0, max(0.0, x + dx * paso)), min(1.0, max(0.0, y + dy * paso)))
            self.update()
            self.cambiaron.emit(list(self.esquinas))
            return
        super().keyPressEvent(evento)

    def focusInEvent(self, evento) -> None:
        super().focusInEvent(evento)
        self.update()

    def focusOutEvent(self, evento) -> None:
        super().focusOutEvent(evento)
        self.update()


class VistaResultado(QLabel):
    """El resultado procesado, ajustado al espacio disponible."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Vista previa del resultado")
        self._original: QPixmap | None = None

    def mostrar(self, imagen: QPixmap | None) -> None:
        self._original = imagen
        self._ajustar()

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        self._ajustar()

    def _ajustar(self) -> None:
        if self._original is None or self._original.isNull():
            self.setPixmap(QPixmap())
            return
        self.setPixmap(self._original.scaled(self.size() - QSize(20, 20), Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))


class DialogoImportarFotos(QDialog):
    def __init__(self, rutas: list[str | Path] | None = None, ocr_disponible: bool = True,
                 motivo_sin_ocr: str = "", parent: QWidget | None = None, para_convertir: bool = False):
        """`para_convertir`: las fotos van a «Convertir escaneado a documento editable» (el texto
        se reconoce igual, así que no se ofrece el OCR idéntico)."""
        super().__init__(parent)
        self.para_convertir = para_convertir
        self.setWindowTitle("Fotos para convertir a documento editable" if para_convertir
                            else "Importar imágenes como PDF escaneado")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1200, 820)
        self.fotos: list[AjusteFoto] = []
        self._chicas: dict[tuple[str, int], np.ndarray] = {}      # (ruta, rotación) -> copia chica
        self._detectadas: dict[tuple[str, int], bool] = {}
        self._actual = -1

        # Lista de fotos (el orden es el de las páginas)
        self.lista = QListWidget(self)
        self.lista.setAccessibleName("Fotos, en el orden de las páginas")
        self.lista.setIconSize(QSize(56, 56))
        self.lista.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.lista.model().rowsMoved.connect(self._reordenadas)
        self.lista.currentRowChanged.connect(self._elegir)
        agregar = self._boton("Agregar fotos…", "image-plus", self.pedir_fotos)
        self.quitar = self._boton("Quitar", "trash-2", self.quitar_actual, solo_icono=True)
        self.subir = self._boton("Subir", "arrow-up", lambda: self.mover_actual(-1), solo_icono=True)
        self.bajar = self._boton("Bajar", "arrow-down", lambda: self.mover_actual(1), solo_icono=True)
        botones_lista = QHBoxLayout()
        botones_lista.addWidget(agregar, 1)
        for b in (self.quitar, self.subir, self.bajar):
            botones_lista.addWidget(b)
        izquierda = QWidget(self)
        capa_izq = QVBoxLayout(izquierda)
        capa_izq.setContentsMargins(0, 0, 0, 0)
        capa_izq.addWidget(QLabel("<b>Fotos</b> (arrastrá para ordenar)", izquierda))
        capa_izq.addWidget(self.lista, 1)
        capa_izq.addLayout(botones_lista)

        # Foto con las esquinas y resultado
        self.editor = EditorEsquinas(self)
        self.editor.cambiaron.connect(self._esquinas_movidas)
        self.resultado = VistaResultado(self)
        self.estado = QLabel(self)
        self.estado.setObjectName("texto_suave")
        self.estado.setWordWrap(True)
        self.girar_izq = self._boton("Girar a la izquierda", "rotate-ccw", lambda: self.girar(-90), solo_icono=True)
        self.girar_der = self._boton("Girar a la derecha", "rotate-cw", lambda: self.girar(90), solo_icono=True)
        self.redetectar = self._boton("Detectar el papel de nuevo", "refresh-cw", self.detectar_de_nuevo)
        fila_foto = QHBoxLayout()
        for b in (self.girar_izq, self.girar_der, self.redetectar):
            fila_foto.addWidget(b)
        fila_foto.addWidget(self.estado, 1)
        paneles = QSplitter(Qt.Orientation.Horizontal, self)
        for titulo, vista in (("Foto (mové las esquinas si hace falta)", self.editor),
                              ("Resultado", self.resultado)):
            caja = QWidget()
            c = QVBoxLayout(caja)
            c.setContentsMargins(0, 0, 0, 0)
            c.addWidget(QLabel(f"<b>{titulo}</b>", caja))
            c.addWidget(vista, 1)
            paneles.addWidget(caja)
        paneles.setChildrenCollapsible(False)
        centro = QWidget(self)
        capa_centro = QVBoxLayout(centro)
        capa_centro.setContentsMargins(0, 0, 0, 0)
        capa_centro.addWidget(paneles, 1)
        capa_centro.addLayout(fila_foto)

        # Opciones
        self.recortar = QCheckBox("Recortar el fondo y corregir la perspectiva", self)
        self.recortar.setChecked(True)
        self.enderezar = QCheckBox("Corregir la inclinación", self)
        self.enderezar.setChecked(True)
        self.mejora = QComboBox(self)
        for m in Mejora:
            self.mejora.addItem(m.texto, m.value)
        self.tamano = QComboBox(self)
        for t in TamanoPapel:
            self.tamano.addItem(t.texto, t.value)
        self.resolucion = QComboBox(self)
        for dpi in RESOLUCIONES:
            self.resolucion.addItem(f"{dpi} ppp" + ("  (recomendada)" if dpi == 200 else ""), dpi)
        self.resolucion.setCurrentIndex(RESOLUCIONES.index(200))
        self.ocr = QCheckBox("Reconocer texto (OCR idéntico: se ve igual, pero se puede buscar y copiar)", self)
        self.ocr.setChecked(ocr_disponible and not para_convertir)
        self.ocr.setEnabled(ocr_disponible and not para_convertir)
        grupo = QGroupBox("Opciones", self)
        formulario = QFormLayout(grupo)
        formulario.addRow(self.recortar)
        formulario.addRow(self.enderezar)
        formulario.addRow("Mejora del contraste:", self.mejora)
        formulario.addRow("Tamaño de página:", self.tamano)
        formulario.addRow("Resolución:", self.resolucion)
        if para_convertir:
            self.ocr.hide()
            self.resolucion.setEnabled(False)
            self.resolucion.setToolTip("Para reconocer el texto se usa siempre 300 ppp.")
        else:
            formulario.addRow(self.ocr)
        if not ocr_disponible and not para_convertir:
            formulario.addRow(tarjeta_informativa(motivo_sin_ocr or "El OCR no está disponible: revisá el panel "
                                                  "Faltantes.", grupo))
        for control in (self.recortar, self.enderezar):
            control.toggled.connect(self._programar)
        self.mejora.currentIndexChanged.connect(self._programar)

        self.botones = botonera(self, "Convertir" if para_convertir else "Crear PDF…",
                                "scan-text" if para_convertir else "file-scan")
        self.boton_crear = self.botones.button(QDialogButtonBox.StandardButton.Ok)

        divisor = QSplitter(Qt.Orientation.Horizontal, self)
        divisor.addWidget(izquierda)
        divisor.addWidget(centro)
        divisor.setStretchFactor(1, 1)
        divisor.setSizes([260, 940])
        divisor.setChildrenCollapsible(False)
        capa = QVBoxLayout(self)
        capa.addWidget(divisor, 1)
        capa.addWidget(grupo)
        capa.addWidget(self.botones)

        self._reloj = QTimer(self)
        self._reloj.setSingleShot(True)
        self._reloj.setInterval(150)
        self._reloj.timeout.connect(self.actualizar_previa)
        self.agregar(rutas or [])
        self._estado_botones()

    def _boton(self, texto: str, icono: str, accion, solo_icono: bool = False) -> QWidget:
        if solo_icono:
            b = QToolButton(self)
            b.setToolTip(texto)
            b.setAccessibleName(texto)
        else:
            b = QPushButton(texto, self)
        iconos.asignar(b, icono)
        b.clicked.connect(accion)
        if isinstance(b, QPushButton):
            b.setAutoDefault(False)
        return b

    # ------------------------------------------------------------------ datos
    def opciones(self) -> OpcionesEscaneo:
        return OpcionesEscaneo(self.recortar.isChecked(), self.enderezar.isChecked(), Mejora(self.mejora.currentData()),
                               TamanoPapel(self.tamano.currentData()), int(self.resolucion.currentData()))

    @property
    def con_ocr(self) -> bool:
        return self.ocr.isEnabled() and self.ocr.isChecked()

    def _chica(self, foto: AjusteFoto) -> np.ndarray:
        clave = (foto.ruta, foto.rotacion)
        if clave not in self._chicas:
            img, _ = escaneo.reducir(escaneo.leer_imagen(foto.ruta, foto.rotacion), LADO_PREVIA)
            self._chicas[clave] = img
        return self._chicas[clave]

    def _detectar(self, foto: AjusteFoto) -> None:
        """Fija las esquinas detectadas en la copia chica: así la vista previa y el PDF usan
        exactamente las mismas."""
        img = self._chica(foto)
        puntos = escaneo.detectar_papel(img)
        self._detectadas[(foto.ruta, foto.rotacion)] = puntos is not None
        foto.esquinas = escaneo.normalizar_esquinas(puntos, img) if puntos is not None else None

    # ------------------------------------------------------------------ lista
    def pedir_fotos(self) -> None:
        rutas, _ = QFileDialog.getOpenFileNames(self, "Elegir fotos de documentos", "", FILTRO_IMAGENES)
        self.agregar(rutas)

    def agregar(self, rutas: list[str | Path]) -> list[str]:
        """Agrega fotos; devuelve los mensajes de las que no se pudieron leer."""
        problemas = []
        for ruta in rutas:
            ruta = str(ruta)
            foto = AjusteFoto(ruta)
            try:
                self._detectar(foto)
            except ErrorDocumento as e:
                problemas.append(str(e))
                continue
            self.fotos.append(foto)
            item = QListWidgetItem(Path(ruta).name)
            item.setToolTip(ruta)
            item.setData(Qt.ItemDataRole.UserRole, foto)
            item.setIcon(QIcon(QPixmap.fromImage(a_qimage(escaneo.reducir(self._chica(foto), 112)[0]))))
            self.lista.addItem(item)
        if problemas:
            self.estado.setText(" ".join(problemas))
        if self.lista.currentRow() < 0 and self.lista.count():
            self.lista.setCurrentRow(0)
        self._estado_botones()
        return problemas

    def _reordenadas(self, *_args) -> None:
        self.fotos = [self.lista.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.lista.count())]
        self._actual = self.lista.currentRow()

    def quitar_actual(self) -> None:
        fila = self.lista.currentRow()
        if fila < 0:
            return
        self.lista.takeItem(fila)
        self._reordenadas()
        self._elegir(self.lista.currentRow())
        self._estado_botones()

    def mover_actual(self, delta: int) -> None:
        fila = self.lista.currentRow()
        destino = fila + delta
        if fila < 0 or not 0 <= destino < self.lista.count():
            return
        item = self.lista.takeItem(fila)
        self.lista.insertItem(destino, item)
        self.lista.setCurrentRow(destino)
        self._reordenadas()

    def _estado_botones(self) -> None:
        hay = bool(self.fotos)
        for b in (self.quitar, self.subir, self.bajar, self.girar_izq, self.girar_der, self.redetectar):
            b.setEnabled(hay)
        self.boton_crear.setEnabled(hay)

    # ------------------------------------------------------------------ foto actual
    @property
    def foto_actual(self) -> AjusteFoto | None:
        fila = self.lista.currentRow()
        return self.lista.item(fila).data(Qt.ItemDataRole.UserRole) if fila >= 0 else None

    def _elegir(self, fila: int) -> None:
        self._actual = fila
        foto = self.foto_actual
        if foto is None:
            self.editor.mostrar(None, None)
            self.resultado.mostrar(None)
            self.estado.setText("")
            return
        self.editor.mostrar(QPixmap.fromImage(a_qimage(self._chica(foto))), foto.esquinas)
        self.actualizar_previa()

    def girar(self, grados: int) -> None:
        foto = self.foto_actual
        if foto is None:
            return
        foto.rotacion = (foto.rotacion + grados) % 360
        self._detectar(foto)
        self._actualizar_icono()
        self._elegir(self.lista.currentRow())

    def _actualizar_icono(self) -> None:
        item = self.lista.currentItem()
        foto = self.foto_actual
        if item is not None and foto is not None:
            item.setIcon(QIcon(QPixmap.fromImage(a_qimage(escaneo.reducir(self._chica(foto), 112)[0]))))

    def detectar_de_nuevo(self) -> None:
        foto = self.foto_actual
        if foto is not None:
            self._detectar(foto)
            self._elegir(self.lista.currentRow())

    def _esquinas_movidas(self, esquinas: list) -> None:
        foto = self.foto_actual
        if foto is not None:
            foto.esquinas = [tuple(p) for p in esquinas]
            self._detectadas[(foto.ruta, foto.rotacion)] = True
            self._programar()

    def _programar(self, *_args) -> None:
        self._reloj.start()

    def actualizar_previa(self) -> None:
        foto = self.foto_actual
        if foto is None:
            return
        opciones = self.opciones()
        hecha = escaneo.procesar(self._chica(foto), foto, opciones)
        self.resultado.mostrar(QPixmap.fromImage(a_qimage(hecha.imagen)))
        partes = []
        if opciones.recortar:
            if self._detectadas.get((foto.ruta, foto.rotacion)) or foto.esquinas is not None:
                partes.append("Papel detectado.")
            else:
                partes.append("No se encontró el borde del papel: se usa la foto entera (podés mover las esquinas).")
        if opciones.enderezar and abs(hecha.angulo) >= 0.15:
            partes.append(f"Inclinación corregida: {abs(hecha.angulo):.1f}°.")
        self.estado.setText(" ".join(partes))

    def done(self, resultado: int) -> None:
        self._reloj.stop()
        super().done(resultado)
