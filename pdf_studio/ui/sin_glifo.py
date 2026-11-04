"""Diálogo "Caracteres sin glifo": qué hacer con cada uno en todo el documento."""
from __future__ import annotations

import pymupdf
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QScrollArea, QVBoxLayout, QWidget)

from pdf_studio.core.fuentes import IndiceFuentesSistema
from pdf_studio.core.sin_glifo import (Accion, PatronSinGlifo, PlanResolucion, TEXTO_ACCION, VistaPrevia,
                                       planificar_resolucion, vista_previa)
from pdf_studio.ui.edicion_texto import marcar
from pdf_studio.ui.tema import marcar_primario

ORDEN_ACCIONES = (Accion.CONSERVAR, Accion.ESPACIO, Accion.TABULACION, Accion.VINETA, Accion.QUITAR)
ANCHO_VISTA = 560


def _imagen(pix: pymupdf.Pixmap | None) -> QPixmap:
    if pix is None:
        return QPixmap()
    imagen = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
    mapa = QPixmap.fromImage(imagen)
    return mapa.scaledToWidth(ANCHO_VISTA, Qt.TransformationMode.SmoothTransformation) if mapa.width() > ANCHO_VISTA else mapa


class DialogoSinGlifo(QDialog):
    """Por defecto todo se conserva. Para aplicar otra cosa, la vista previa tiene que verificar
    que el resto de cada página no cambie."""

    def __init__(self, doc: pymupdf.Document, patrones: list[PatronSinGlifo],
                 incluidas: IndiceFuentesSistema | None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Caracteres sin glifo")
        self.resize(640, 560)
        self.doc = doc
        self.patrones = patrones
        self.incluidas = incluidas
        self.plan: PlanResolucion | None = None
        self.vista: VistaPrevia | None = None
        self.combos: dict[tuple, QComboBox] = {}
        self._ultimo: PatronSinGlifo | None = patrones[0] if patrones else None

        explicacion = QLabel("Estos caracteres no tienen glifo en su fuente: el PDF los muestra como un rectángulo. "
                             "Si los conservás no cambia nada. Con cualquier otra opción cambia solo cada carácter: "
                             "lo que lo rodea queda exactamente donde está.", self)
        explicacion.setWordWrap(True)
        lista = QWidget(self)
        capa_lista = QVBoxLayout(lista)
        capa_lista.setContentsMargins(0, 0, 0, 0)
        for p in patrones:
            capa_lista.addWidget(self._tarjeta(p, lista))
        capa_lista.addStretch(1)
        desplazable = QScrollArea(self)
        desplazable.setWidgetResizable(True)
        desplazable.setFrameShape(QFrame.Shape.NoFrame)
        desplazable.setWidget(lista)

        self.titulo_vista = QLabel("Vista previa", self)
        self.titulo_vista.setObjectName("titulo_panel")
        self.antes = QLabel(self)
        self.despues = QLabel(self)
        for imagen, nombre in ((self.antes, "Antes"), (self.despues, "Después")):
            imagen.setAccessibleName(f"Vista previa: {nombre.lower()}")
        self.etiqueta_antes = QLabel("Antes", self)
        self.etiqueta_despues = QLabel("Después", self)
        self.estado = QLabel("", self)
        self.estado.setWordWrap(True)

        self.botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.boton_aplicar = self.botones.button(QDialogButtonBox.StandardButton.Ok)
        self.boton_aplicar.setText("Aplicar")
        marcar_primario(self.boton_aplicar)
        self.botones.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancelar")
        self.botones.accepted.connect(self.accept)
        self.botones.rejected.connect(self.reject)

        capa = QVBoxLayout(self)
        capa.addWidget(explicacion)
        capa.addWidget(desplazable, 1)
        capa.addWidget(self.titulo_vista)
        for widget in (self.etiqueta_antes, self.antes, self.etiqueta_despues, self.despues, self.estado):
            capa.addWidget(widget)
        capa.addWidget(self.botones)

        self._temporizador = QTimer(self)
        self._temporizador.setSingleShot(True)
        self._temporizador.setInterval(150)
        self._temporizador.timeout.connect(self.actualizar)
        self.actualizar()

    def _tarjeta(self, patron: PatronSinGlifo, parent: QWidget) -> QFrame:
        tarjeta = QFrame(parent)
        tarjeta.setObjectName("tarjeta")
        capa = QVBoxLayout(tarjeta)
        capa.setContentsMargins(10, 8, 10, 8)
        resumen = QLabel(f"<b>{patron.resumen()}</b>", tarjeta)
        resumen.setWordWrap(True)
        muestra = marcar(patron.apariciones[0].linea.strip()[:70], {patron.caracter})
        ejemplo = QLabel(f"Por ejemplo: «{muestra}»", tarjeta)
        ejemplo.setObjectName("texto_suave")
        ejemplo.setTextFormat(Qt.TextFormat.PlainText)
        ejemplo.setWordWrap(True)
        combo = QComboBox(tarjeta)
        combo.setAccessibleName(f"Qué hacer con {patron.codigo}")
        for accion in ORDEN_ACCIONES:
            combo.addItem(TEXTO_ACCION[accion], accion)
        combo.currentIndexChanged.connect(lambda _i, p=patron: self._cambio(p))
        self.combos[patron.clave] = combo
        capa.addWidget(resumen)
        capa.addWidget(ejemplo)
        fila = QHBoxLayout()
        fila.addWidget(combo)
        fila.addStretch(1)
        capa.addLayout(fila)
        return tarjeta

    def acciones(self) -> dict[tuple, str]:
        return {clave: combo.currentData() for clave, combo in self.combos.items()}

    def _cambio(self, patron: PatronSinGlifo) -> None:
        self._ultimo = patron
        self._temporizador.start()

    def actualizar(self) -> None:
        self.plan, self.vista = None, None
        acciones = self.acciones()
        activos = [p for p in self.patrones if acciones[p.clave] != Accion.CONSERVAR]
        hay_cambios = bool(activos)
        for widget in (self.titulo_vista, self.etiqueta_antes, self.antes, self.etiqueta_despues, self.despues):
            widget.setVisible(hay_cambios)
        if not hay_cambios:
            self.estado.setText("Todo se conserva tal como está.")
            self.boton_aplicar.setEnabled(False)
            return
        ejemplo_de = self._ultimo if self._ultimo in activos else activos[0]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.plan = planificar_resolucion(self.doc, self.patrones, acciones, self.incluidas)
            self.vista = vista_previa(self.doc, self.plan, ejemplo_de.apariciones[0])
        except Exception as e:
            self.plan = None
            self.estado.setText(f"No se pudo preparar el cambio: {e}")
            self.boton_aplicar.setEnabled(False)
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.antes.setPixmap(_imagen(self.vista.antes))
        self.despues.setPixmap(_imagen(self.vista.despues))
        partes = []
        n = self.plan.cambiados
        if self.vista.pixeles_distintos:
            partes.append(f"<b>Hay {self.vista.pixeles_distintos} píxeles distintos fuera de los caracteres cambiados: "
                          "no se puede aplicar sin dañar el documento.</b>")
        elif n:
            paginas = len(self.vista.paginas)
            partes.append(f"Cambia {n} carácter{'es' if n != 1 else ''} en {paginas} página{'s' if paginas != 1 else ''}. "
                          "Verificado: el resto de esas páginas es idéntico (píxeles distintos = 0).")
        if self.plan.omitidos:
            partes.append(f"{self.plan.omitidos} no se pueden cambiar (están en una parte del PDF que no se edita "
                          "directamente) y quedan como están.")
        if self.plan.superpuestos:
            partes.append(f"En {self.plan.superpuestos} lugar{'es' if self.plan.superpuestos != 1 else ''} la viñeta es "
                          "más ancha que el carácter original: revisá la vista previa.")
        if Accion.TABULACION in acciones.values():
            partes.append("Un PDF no tiene tabulaciones: queda el hueco y lo que sigue no se mueve.")
        self.estado.setText("<br>".join(partes))
        self.boton_aplicar.setEnabled(self.plan.operacion is not None and not self.vista.pixeles_distintos)
