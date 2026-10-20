"""Trabajo en segundo plano sin trabar la interfaz.

- EnHilo: corre una función de Python puro (fontTools, subprocess, archivos) en un QThread.
  NUNCA se usa para PyMuPDF: MuPDF no admite hilos.
- AnalizadorFuentes: analiza las fuentes de un PDF en un proceso aparte (ver
  pdf_studio.analisis_proceso) y avisa con señales al terminar o al fallar.
- ProcesoExterno: corre un programa (ocrmypdf, Ghostscript) sin trabar la interfaz,
  con cancelación y tiempo máximo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QProcess, QThread, QTimer, Signal

from pdf_studio.core.fuentes import InfoFuente

TIEMPO_MAXIMO_MS = 180_000


class EnHilo(QThread):
    """Ejecuta `funcion()` en otro hilo. Emite listo(resultado) o fallo(excepción)."""

    listo = Signal(object)
    fallo = Signal(object)

    def __init__(self, funcion: Callable[[], object], parent: QObject | None = None):
        super().__init__(parent)
        self._funcion = funcion

    def run(self) -> None:
        try:
            resultado = self._funcion()
        except Exception as e:      # se informa en el hilo principal
            self.fallo.emit(e)
            return
        self.listo.emit(resultado)


def comando_analisis() -> tuple[str, list[str]]:
    if getattr(sys, "frozen", False):
        return sys.executable, ["--analizar-fuentes"]
    return sys.executable, ["-m", "pdf_studio.analisis_proceso"]


class AnalizadorFuentes(QObject):
    terminado = Signal(list)      # list[InfoFuente]
    fallo = Signal(str)           # mensaje para el usuario

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proceso: QProcess | None = None
        self._reloj = QTimer(self)
        self._reloj.setSingleShot(True)
        self._reloj.timeout.connect(self._vencido)

    @property
    def ocupado(self) -> bool:
        return self._proceso is not None

    def iniciar(self, ruta: Path, contrasena: str | None) -> None:
        self.cancelar()
        proceso = QProcess(self)
        programa, argumentos = comando_analisis()
        entorno = proceso.processEnvironment()
        if entorno.isEmpty():
            from PySide6.QtCore import QProcessEnvironment
            entorno = QProcessEnvironment.systemEnvironment()
        raiz = str(Path(__file__).resolve().parent.parent.parent)
        entorno.insert("PYTHONPATH", raiz + os.pathsep + entorno.value("PYTHONPATH", ""))
        entorno.insert("PYTHONIOENCODING", "utf-8")
        proceso.setProcessEnvironment(entorno)
        proceso.finished.connect(lambda codigo, estado, p=proceso: self._fin(p, codigo))
        proceso.errorOccurred.connect(lambda error, p=proceso: self._error(p, error))
        self._proceso = proceso
        proceso.start(programa, argumentos)
        proceso.write(json.dumps({"ruta": str(ruta), "contrasena": contrasena}).encode("utf-8"))
        proceso.closeWriteChannel()
        self._reloj.start(TIEMPO_MAXIMO_MS)

    def cancelar(self) -> None:
        proceso, self._proceso = self._proceso, None
        self._reloj.stop()
        if proceso is not None:
            proceso.blockSignals(True)
            proceso.kill()
            proceso.waitForFinished(2000)
            proceso.deleteLater()

    def _fin(self, proceso: QProcess, codigo: int) -> None:
        if proceso is not self._proceso:
            return          # resultado de un análisis viejo, ya reemplazado
        self._proceso = None
        self._reloj.stop()
        salida = bytes(proceso.readAllStandardOutput()).decode("utf-8", errors="replace")
        proceso.deleteLater()
        try:
            datos = json.loads(salida)
        except ValueError:
            self.fallo.emit(f"El análisis terminó sin resultado (código {codigo}).")
            return
        if not datos.get("ok"):
            self.fallo.emit(f"El análisis falló ({datos.get('error', 'error desconocido')}).")
            return
        fuentes = []
        for d in datos.get("fuentes", []):
            try:
                fuentes.append(InfoFuente.desde_dict(d))
            except Exception:
                pass
        self.terminado.emit(fuentes)

    def _error(self, proceso: QProcess, error) -> None:
        if proceso is not self._proceso or error != QProcess.ProcessError.FailedToStart:
            return
        self._proceso = None
        self._reloj.stop()
        self.fallo.emit("No se pudo iniciar el análisis de fuentes.")

    def _vencido(self) -> None:
        if self._proceso is not None:
            self.cancelar()
            self.fallo.emit("El análisis de fuentes tardó demasiado y se canceló.")


class ProcesoExterno(QObject):
    """Corre un programa externo. Emite terminado(código, salida de error) al terminar
    (código -1 si no arrancó, -2 si se canceló o venció el tiempo)."""

    terminado = Signal(int, str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._proceso: QProcess | None = None
        self._reloj = QTimer(self)
        self._reloj.setSingleShot(True)
        self._reloj.timeout.connect(self._vencido)
        self.vencio = False

    @property
    def ocupado(self) -> bool:
        return self._proceso is not None

    def iniciar(self, programa: str, argumentos: list[str], entorno: dict[str, str] | None = None,
                tiempo_maximo_ms: int = 3_600_000) -> None:
        from PySide6.QtCore import QProcessEnvironment

        self.cancelar()
        self.vencio = False
        proceso = QProcess(self)
        if entorno is not None:
            env = QProcessEnvironment()
            for clave, valor in entorno.items():
                env.insert(clave, valor)
            proceso.setProcessEnvironment(env)
        proceso.finished.connect(lambda codigo, _estado, p=proceso: self._fin(p, codigo))
        proceso.errorOccurred.connect(lambda error, p=proceso: self._error(p, error))
        self._proceso = proceso
        proceso.start(programa, argumentos)
        self._reloj.start(tiempo_maximo_ms)

    def esperar(self, ms: int) -> bool:
        """Solo para tests: espera a que termine."""
        return self._proceso is None or self._proceso.waitForFinished(ms)

    def cancelar(self) -> bool:
        """Corta el proceso en marcha. Devuelve True si había uno."""
        proceso, self._proceso = self._proceso, None
        self._reloj.stop()
        if proceso is None:
            return False
        proceso.blockSignals(True)
        proceso.kill()
        proceso.waitForFinished(5000)
        proceso.deleteLater()
        return True

    def _fin(self, proceso: QProcess, codigo: int) -> None:
        if proceso is not self._proceso:
            return
        self._proceso = None
        self._reloj.stop()
        error = bytes(proceso.readAllStandardError()).decode("utf-8", errors="replace")
        proceso.deleteLater()
        self.terminado.emit(int(codigo), error)

    def _error(self, proceso: QProcess, error) -> None:
        if proceso is not self._proceso or error != QProcess.ProcessError.FailedToStart:
            return
        self._proceso = None
        self._reloj.stop()
        self.terminado.emit(-1, "No se pudo iniciar el programa.")

    def _vencido(self) -> None:
        if self.cancelar():
            self.vencio = True
            self.terminado.emit(-2, "El proceso tardó demasiado y se canceló.")
