"""Confirmación y ejecución de instalaciones asistidas (en segundo plano, con progreso)."""
from __future__ import annotations

from PySide6.QtCore import QEventLoop, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QApplication, QDialog, QFormLayout, QHBoxLayout, QLabel, QProgressDialog,
                               QPushButton, QVBoxLayout, QWidget)

from pdf_studio.dependencias.instalador import ErrorInstalacion, Instalacion, formato_tamano
from pdf_studio.ui import iconos


class DialogoConfirmarInstalacion(QDialog):
    """Muestra qué se va a instalar antes de hacerlo. Nada se instala sin este OK."""

    def __init__(self, inst: Instalacion, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Confirmar instalación")
        self.setMinimumWidth(520)
        titulo = QLabel(f"Instalar: {inst.titulo}", self)
        titulo.setObjectName("titulo_panel")
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            tamano = formato_tamano(inst.tamano())
        finally:
            QApplication.restoreOverrideCursor()
        datos = QFormLayout()
        for etiqueta, valor in (("Qué se instala:", inst.titulo), ("Se descarga de:", inst.origen),
                                ("Licencia:", inst.licencia), ("Tamaño de la descarga:", tamano),
                                ("Dónde se instala:", inst.destino)):
            texto = QLabel(valor, self)
            texto.setWordWrap(True)
            texto.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            datos.addRow(etiqueta, texto)
        aviso = QLabel(self)
        aviso.setWordWrap(True)
        aviso.setObjectName("texto_suave")
        if inst.requiere_admin:
            aviso.setText("Windows va a mostrar su aviso de permisos de administrador: aceptalo para continuar.")
        else:
            aviso.setText("No hace falta ser administrador. Queda disponible sin reiniciar la app.")

        self.boton_instalar = QPushButton("Instalar", self)
        self.boton_instalar.setProperty("primario", True)
        iconos.asignar(self.boton_instalar, "download")
        self.boton_instalar.clicked.connect(self.accept)
        copiar = QPushButton("Copiar comando", self)
        iconos.asignar(copiar, "copy")
        copiar.clicked.connect(lambda: (QGuiApplication.clipboard().setText(inst.comando),
                                        copiar.setText("Comando copiado")))
        cancelar = QPushButton("Cancelar", self)
        cancelar.clicked.connect(self.reject)
        fila = QHBoxLayout()
        fila.addWidget(copiar)
        fila.addStretch(1)
        fila.addWidget(cancelar)
        fila.addWidget(self.boton_instalar)

        capa = QVBoxLayout(self)
        capa.addWidget(titulo)
        capa.addLayout(datos)
        capa.addWidget(aviso)
        capa.addLayout(fila)
        cancelar.setFocus()      # lo seguro por defecto: no instalar


class _Trabajo(QThread):
    progreso = Signal(int, int, str)
    terminado = Signal(bool, str)

    def __init__(self, inst: Instalacion):
        super().__init__()
        self.inst = inst

    def run(self) -> None:
        try:
            mensaje = self.inst.ejecutar(lambda h, t, m: self.progreso.emit(h, t, m))
            self.terminado.emit(True, mensaje)
        except ErrorInstalacion as e:
            self.terminado.emit(False, str(e))
        except Exception as e:  # nunca fallar en silencio
            self.terminado.emit(False, f"Error inesperado: {e}")


def ejecutar_con_progreso(inst: Instalacion, parent: QWidget | None = None) -> tuple[bool, str]:
    """Corre la instalación en otro hilo mostrando el progreso; espera a que termine."""
    dialogo = QProgressDialog(f"Instalando {inst.titulo}…", None, 0, 0, parent)
    dialogo.setWindowTitle("Instalando")
    dialogo.setWindowModality(Qt.WindowModality.WindowModal)
    dialogo.setMinimumDuration(0)
    dialogo.setMinimumWidth(460)
    trabajo = _Trabajo(inst)
    resultado: list = [False, ""]
    bucle = QEventLoop()

    def avance(hecho: int, total: int, mensaje: str) -> None:
        if total > 0:
            dialogo.setMaximum(1000)
            dialogo.setValue(min(1000, int(hecho * 1000 / total)))
            dialogo.setLabelText(f"{mensaje}  ({formato_tamano(hecho)} de {formato_tamano(total)})")
        else:
            dialogo.setMaximum(0)
            dialogo.setLabelText(mensaje)

    def fin(ok: bool, mensaje: str) -> None:
        resultado[0], resultado[1] = ok, mensaje
        bucle.quit()

    trabajo.progreso.connect(avance)
    trabajo.terminado.connect(fin)
    dialogo.show()
    trabajo.start()
    bucle.exec()
    trabajo.wait()
    dialogo.close()
    return resultado[0], resultado[1]
