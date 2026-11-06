"""Instancia única: si PDF Studio ya está abierto, un doble clic en otro PDF lo abre como
pestaña nueva en la misma ventana, en vez de lanzar otro proceso.

- El primer proceso toma un mutex con nombre de Windows (que el sistema libera solo si el
  proceso termina, aunque sea de golpe) y escucha en un canal local (QLocalServer,
  en Windows un named pipe con el nombre del usuario).
- Los siguientes le mandan las rutas por ese canal, esperan el «ok» y terminan enseguida,
  sin cargar pymupdf ni armar la ventana.
- Si el Explorador lanza varios procesos a la vez (al abrir varios PDFs seleccionados), el
  mutex decide cuál queda como principal; los demás esperan a que escuche y le mandan
  sus archivos.

Solo usa QtCore y QtNetwork: importarlo es rápido.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QByteArray, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

ESPERA_CONEXION_MS = 500
ESPERA_RESPUESTA_MS = 15_000      # la principal puede estar terminando de arrancar
ESPERA_PRINCIPAL_S = 20.0         # cuánto esperar a que la principal empiece a escuchar


def nombre_canal(sufijo: str = "") -> str:
    """Nombre del canal, distinto por usuario, por sesión de Windows y por instalación (el .exe
    instalado y la app corrida desde el código no se pasan archivos entre sí)."""
    instalacion = os.path.normcase(str(Path(sys.executable).resolve().parent))
    if not getattr(sys, "frozen", False):
        instalacion += "|" + os.path.normcase(str(Path(__file__).resolve().parent))
    clave = f"{getpass.getuser()}|{os.environ.get('SESSIONNAME', '')}|{instalacion}|{sufijo}"
    return "PDFStudio-" + hashlib.sha1(clave.encode("utf-8")).hexdigest()[:16]


class _Mutex:
    """Mutex con nombre de Windows. Lo tiene el primer proceso que lo crea; el sistema lo libera
    cuando ese proceso termina. En otros sistemas no hay candado (alcanza con el canal)."""

    ERROR_ALREADY_EXISTS = 183

    def __init__(self, nombre: str):
        self.nombre = "Local\\" + nombre
        self._handle = None

    def tomar(self) -> bool:
        if self._handle is not None:
            return True
        if sys.platform != "win32":
            return True
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel32.CreateMutexW(None, False, self.nombre)
        ya_existia = ctypes.get_last_error() == self.ERROR_ALREADY_EXISTS
        if not handle:
            return True          # sin mutex no se puede coordinar: este proceso sigue solo
        if ya_existia:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def soltar(self) -> None:
        if self._handle is not None:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            self._handle = None


def _permitir_primer_plano() -> None:
    """Windows solo deja pasar al frente una ventana si la pide el proceso que recibió el doble
    clic: este le cede ese permiso a la instancia principal."""
    try:
        import ctypes
        ASFW_ANY = -1
        ctypes.windll.user32.AllowSetForegroundWindow(ASFW_ANY)
    except (AttributeError, OSError):
        pass


def enviar(rutas: list[str], canal: str, espera_respuesta_ms: int = ESPERA_RESPUESTA_MS) -> bool:
    """Manda las rutas a la instancia que ya está abierta. True si las recibió."""
    socket = QLocalSocket()
    socket.connectToServer(canal)
    if not socket.waitForConnected(ESPERA_CONEXION_MS):
        return False
    _permitir_primer_plano()
    mensaje = json.dumps({"abrir": [str(Path(r).resolve()) for r in rutas]}, ensure_ascii=False)
    socket.write(QByteArray((mensaje + "\n").encode("utf-8")))
    socket.flush()
    respuesta = b""
    limite = time.monotonic() + espera_respuesta_ms / 1000
    while b"\n" not in respuesta and time.monotonic() < limite:
        if socket.waitForReadyRead(200):
            respuesta += bytes(socket.readAll().data())
        elif socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            break
    socket.disconnectFromServer()
    return respuesta.startswith(b"ok")


class ServidorInstancia(QObject):
    """Escucha a los procesos nuevos y emite las rutas que piden abrir."""

    archivos_recibidos = Signal(list)

    def __init__(self, canal: str, parent: QObject | None = None):
        super().__init__(parent)
        self.canal = canal
        self._servidor = QLocalServer(self)
        self._servidor.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._servidor.newConnection.connect(self._nueva_conexion)
        self._buffers: dict[QLocalSocket, bytes] = {}

    def escuchar(self) -> bool:
        if self._servidor.listen(self.canal):
            return True
        # Un canal que quedó de un cierre abrupto (en Windows no pasa, pero en otros sistemas sí)
        QLocalServer.removeServer(self.canal)
        return self._servidor.listen(self.canal)

    def cerrar(self) -> None:
        """Deja de escuchar y corta las conexiones que queden abiertas."""
        for socket in list(self._buffers):
            socket.blockSignals(True)
            socket.abort()
            socket.deleteLater()
        self._buffers.clear()
        self._servidor.close()

    def _nueva_conexion(self) -> None:
        while self._servidor.hasPendingConnections():
            socket = self._servidor.nextPendingConnection()
            self._buffers[socket] = b""
            # Slots que son métodos (no lambdas que guardan el socket): se desconectan solos
            # cuando se destruye cualquiera de los dos
            socket.readyRead.connect(self._hay_datos)
            socket.disconnected.connect(self._desconectado)
            if socket.bytesAvailable():
                self._leer(socket)

    def _hay_datos(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket) and socket in self._buffers:
            self._leer(socket)

    def _desconectado(self) -> None:
        socket = self.sender()
        if isinstance(socket, QLocalSocket) and socket in self._buffers:
            self._buffers.pop(socket, None)
            socket.deleteLater()

    def _leer(self, socket: QLocalSocket) -> None:
        datos = self._buffers.get(socket, b"") + bytes(socket.readAll().data())
        if b"\n" not in datos:
            self._buffers[socket] = datos
            return
        linea = datos.split(b"\n", 1)[0]
        self._buffers[socket] = b""
        try:
            rutas = json.loads(linea.decode("utf-8")).get("abrir", [])
            rutas = [str(r) for r in rutas if isinstance(r, str)]
        except (ValueError, AttributeError):
            socket.write(QByteArray(b"error\n"))
            socket.flush()
            return
        socket.write(QByteArray(b"ok\n"))
        socket.flush()
        self.archivos_recibidos.emit(rutas)


class Instancia:
    """Decide si este proceso es la instancia principal.

    Uso:
        inst = Instancia()
        if inst.entregar_a_otra(rutas):      # ya había una: le pasó los archivos
            return 0
        ...crear QApplication...
        servidor = inst.servir()             # este proceso es la principal
    """

    def __init__(self, canal: str | None = None):
        # PDFSTUDIO_CANAL: otro canal, para probar sin tocar la instancia que esté abierta
        self.canal = canal or os.environ.get("PDFSTUDIO_CANAL") or nombre_canal()
        self._mutex = _Mutex(self.canal)
        self.principal = False

    def entregar_a_otra(self, rutas: list[str], espera_s: float = ESPERA_PRINCIPAL_S) -> bool:
        """True si otra instancia recibió las rutas (y este proceso puede terminar). False si
        este proceso queda como principal (tomó el mutex)."""
        limite = time.monotonic() + espera_s
        while True:
            if enviar(rutas, self.canal):
                return True
            if self._mutex.tomar():
                self.principal = True
                return False
            if time.monotonic() > limite:
                # La principal no responde: mejor abrir aparte que no abrir nada
                return False
            time.sleep(0.1)    # la principal está arrancando y todavía no escucha

    def servir(self, parent: QObject | None = None) -> ServidorInstancia | None:
        """Empieza a escuchar. None si no se pudo (la app sigue andando, sin instancia única)."""
        servidor = ServidorInstancia(self.canal, parent)
        if not servidor.escuchar():
            return None
        return servidor

    def liberar(self) -> None:
        if self.principal:
            self._mutex.soltar()
            self.principal = False
