"""Manejo global de errores inesperados.

- El detalle técnico va a un log en %APPDATA%\\PDFStudio\\logs (rota a los 2 MB).
- Al usuario se le muestra un aviso amigable UNA sola vez por error distinto (mismo
  tipo en el mismo lugar del código); las repeticiones solo se registran.
- Nunca se abren dos avisos a la vez y la app queda usable.
"""
from __future__ import annotations

import logging
import logging.handlers
import traceback

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from pdf_studio import config
from pdf_studio.ui.tema import marcar_primario

DIR_LOGS = config.DIR_APPDATA / "logs"
ARCHIVO_LOG = DIR_LOGS / "pdfstudio.log"

_registro: logging.Logger | None = None


def registro() -> logging.Logger:
    global _registro
    if _registro is None:
        _registro = logging.getLogger("pdf_studio")
        _registro.setLevel(logging.INFO)
        try:
            DIR_LOGS.mkdir(parents=True, exist_ok=True)
            manejador = logging.handlers.RotatingFileHandler(ARCHIVO_LOG, maxBytes=2_000_000, backupCount=3,
                                                             encoding="utf-8")
            manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _registro.addHandler(manejador)
        except OSError:
            _registro.addHandler(logging.NullHandler())
    return _registro


def firma(tipo, tb) -> tuple:
    """Identifica un error por su tipo y el último lugar de NUESTRO código donde ocurrió."""
    lugar = ("", 0)
    for marco in traceback.extract_tb(tb):
        if "pdf_studio" in marco.filename:
            lugar = (marco.filename, marco.lineno)
    return (tipo.__name__, *lugar)


class DialogoError(QDialog):
    def __init__(self, resumen: str, detalle: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Algo salió mal")
        self.setMinimumWidth(520)
        texto = QLabel(f"<b>Ocurrió un error inesperado.</b><br>{resumen}<br><br>"
                       "Podés seguir usando PDF Studio. Si se repite, no se te va a volver a mostrar este aviso; "
                       f"queda registrado en:<br><code>{ARCHIVO_LOG}</code>", self)
        texto.setWordWrap(True)
        self.detalle = QPlainTextEdit(detalle, self)
        self.detalle.setReadOnly(True)
        self.detalle.setVisible(False)
        ver = QPushButton("Ver detalle", self)
        ver.setCheckable(True)
        ver.toggled.connect(self.detalle.setVisible)
        self.copiar = QPushButton("Copiar detalle", self)
        self.copiar.clicked.connect(lambda: (QGuiApplication.clipboard().setText(detalle),
                                             self.copiar.setText("Detalle copiado")))
        cerrar = QPushButton("Seguir", self)
        marcar_primario(cerrar)
        cerrar.clicked.connect(self.accept)
        fila = QHBoxLayout()
        fila.addWidget(ver)
        fila.addWidget(self.copiar)
        fila.addStretch(1)
        fila.addWidget(cerrar)
        capa = QVBoxLayout(self)
        capa.addWidget(texto)
        capa.addWidget(self.detalle, 1)
        capa.addLayout(fila)


class ManejadorErrores:
    def __init__(self, mostrar=None):
        self._vistos: set[tuple] = set()
        self._mostrando = False
        self.repeticiones = 0
        # `mostrar(resumen, detalle)`: reemplazable en los tests
        self._mostrar = mostrar or self._mostrar_dialogo

    def __call__(self, tipo, valor, tb) -> None:
        detalle = "".join(traceback.format_exception(tipo, valor, tb))
        clave = firma(tipo, tb)
        registro().error("Error inesperado %s\n%s", clave, detalle)
        if clave in self._vistos or self._mostrando:
            self.repeticiones += 1
            return
        self._vistos.add(clave)
        self._mostrando = True
        try:
            self._mostrar(f"{tipo.__name__}: {valor}", detalle)
        finally:
            self._mostrando = False

    @staticmethod
    def _mostrar_dialogo(resumen: str, detalle: str) -> None:
        if QApplication.instance() is None:
            return
        padre = QApplication.activeWindow()
        DialogoError(resumen, detalle, padre).exec()
