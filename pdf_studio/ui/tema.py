"""Temas claro y oscuro, escala de la interfaz y hoja de estilos global.

Reglas (ver "Diseño de interfaz" en CLAUDE.md):
- Todos los colores salen de las paletas de acá; ningún widget hardcodea colores.
- Contraste mínimo WCAG AA: texto >= 4.5:1 y elementos de interfaz >= 3:1
  (lo verifican los tests).
- El tema "sistema" sigue a Windows y cambia en vivo si Windows cambia.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication, QStyleFactory

TEMAS = ("sistema", "claro", "oscuro")
ESCALAS = (0.85, 1.0, 1.15, 1.3, 1.5, 1.75)
TAMANO_BASE = 10          # pt, con escala 1.0


@dataclass(frozen=True)
class Paleta:
    nombre: str
    fondo: str             # fondo de la ventana
    superficie: str        # paneles, barras
    superficie_alta: str   # hover, campos
    borde: str             # separadores decorativos
    borde_control: str     # contorno de campos y botones (>= 3:1)
    texto: str
    texto_suave: str       # texto secundario
    acento: str            # selección, foco, botón principal
    sobre_acento: str      # texto sobre el acento
    acento_suave: str      # fondo de elementos activos (con texto normal encima)
    lienzo: str            # fondo detrás de las páginas
    peligro: str
    aviso: str
    exito: str
    nota: str              # color de las notas en la página

    @property
    def oscuro(self) -> bool:
        return self.nombre == "oscuro"


OSCURO = Paleta(
    nombre="oscuro", fondo="#17181B", superficie="#1F2125", superficie_alta="#2A2D33", borde="#3A3E46", borde_control="#737A86",
    texto="#ECEDEF", texto_suave="#A8ADB7", acento="#6AA8FF", sobre_acento="#0B1626", acento_suave="#24364F",
    lienzo="#2B2E34", peligro="#FF7A7A", aviso="#F2C14E", exito="#5BD49B", nota="#F2C14E",
)
CLARO = Paleta(
    nombre="claro", fondo="#F4F5F7", superficie="#FFFFFF", superficie_alta="#ECEEF2", borde="#CDD2DA", borde_control="#7D8695",
    texto="#16181D", texto_suave="#535A66", acento="#1559C9", sobre_acento="#FFFFFF", acento_suave="#DCE7FA",
    lienzo="#DDE1E7", peligro="#B42318", aviso="#8A5300", exito="#16794A", nota="#E3A008",
)
PALETAS = {"claro": CLARO, "oscuro": OSCURO}


# ---------------------------------------------------------------------- contraste (WCAG 2.1)
def _canal(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminancia(color: str) -> float:
    q = QColor(color)
    return 0.2126 * _canal(q.red()) + 0.7152 * _canal(q.green()) + 0.0722 * _canal(q.blue())


def contraste(a: str, b: str) -> float:
    la, lb = sorted((luminancia(a), luminancia(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ---------------------------------------------------------------------- hoja de estilos
def hoja_de_estilos(p: Paleta, escala: float) -> str:
    t = round(TAMANO_BASE * escala, 1)
    chico = round(TAMANO_BASE * escala * 0.9, 1)
    r = round(6 * escala)
    pad = round(4 * escala)
    return f"""
* {{ font-size: {t}pt; }}
QWidget {{ color: {p.texto}; background: {p.fondo}; selection-background-color: {p.acento};
           selection-color: {p.sobre_acento}; }}
QMainWindow::separator {{ background: {p.borde}; width: 1px; height: 1px; }}
QToolTip {{ color: {p.texto}; background: {p.superficie_alta}; border: 1px solid {p.borde};
            padding: {pad}px {pad * 2}px; border-radius: {r}px; }}

/* Encabezado y cinta */
#encabezado, #cinta {{ background: {p.superficie}; border: none; }}
#encabezado QToolBar, #cinta QToolBar {{ background: transparent; border: none; }}
#encabezado {{ border-bottom: 1px solid {p.borde}; }}
#selector_lateral {{ border-right: 1px solid {p.borde}; }}
#titulo_app {{ font-weight: 600; background: transparent; }}
QTabBar#pestanas_cinta {{ background: transparent; }}
QTabBar#pestanas_cinta::tab {{ background: transparent; color: {p.texto_suave}; padding: {pad + 2}px {pad * 3}px;
    border: none; border-bottom: 2px solid transparent; margin-right: 2px; }}
QTabBar#pestanas_cinta::tab:selected {{ color: {p.texto}; border-bottom: 2px solid {p.acento}; }}
QTabBar#pestanas_cinta::tab:hover {{ color: {p.texto}; }}
QTabBar#pestanas_cinta::tab:focus {{ border: 2px solid {p.acento}; border-radius: {r}px; }}

QToolBar {{ background: {p.superficie}; border: none; spacing: {pad}px; padding: {pad}px; }}
QToolBar::separator {{ background: {p.borde}; width: 1px; margin: {pad}px {pad * 2}px; }}
QToolButton {{ background: transparent; color: {p.texto}; border: 1px solid transparent; border-radius: {r}px;
    padding: {pad}px {pad + 2}px; }}
QToolButton:hover {{ background: {p.superficie_alta}; }}
QToolButton:pressed {{ background: {p.borde}; }}
QToolButton:checked {{ background: {p.acento_suave}; color: {p.texto}; border-color: {p.acento}; }}
QToolButton:focus {{ border: 2px solid {p.acento}; }}
QToolButton:disabled {{ color: {p.texto_suave}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* Controles */
QPushButton {{ background: {p.superficie_alta}; color: {p.texto}; border: 1px solid {p.borde_control};
    border-radius: {r}px; padding: {pad + 1}px {pad * 3}px; }}
QPushButton:hover {{ border-color: {p.texto_suave}; }}
QPushButton:focus {{ border: 2px solid {p.acento}; }}
QPushButton:disabled {{ color: {p.texto_suave}; background: {p.superficie}; }}
QPushButton[primario="true"] {{ background: {p.acento}; color: {p.sobre_acento}; border-color: {p.acento};
    font-weight: 600; }}
QPushButton[primario="true"]:focus {{ border: 2px solid {p.texto}; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{ background: {p.superficie}; color: {p.texto};
    border: 1px solid {p.borde_control}; border-radius: {r}px; padding: {pad}px {pad + 2}px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus {{
    border: 2px solid {p.acento}; }}
QComboBox QAbstractItemView {{ background: {p.superficie}; border: 1px solid {p.borde}; }}
QCheckBox, QRadioButton, QLabel {{ background: transparent; }}
QCheckBox:focus, QRadioButton:focus {{ color: {p.acento}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.texto_suave}; }}
QGroupBox {{ border: 1px solid {p.borde}; border-radius: {r}px; margin-top: {pad * 3}px; padding: {pad * 2}px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: {pad * 2}px; padding: 0 {pad}px; color: {p.texto_suave}; }}
QSlider::groove:horizontal {{ height: 4px; background: {p.borde}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {p.acento}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider:focus::handle:horizontal {{ border: 2px solid {p.texto}; }}
QProgressBar {{ background: {p.superficie_alta}; border: none; border-radius: {r}px; text-align: center; }}
QProgressBar::chunk {{ background: {p.acento}; border-radius: {r}px; }}

/* Listas y tablas */
QListWidget, QTableWidget, QTreeView, QListView {{ background: {p.superficie}; border: 1px solid {p.borde};
    border-radius: {r}px; outline: none; }}
QListWidget:focus, QTableWidget:focus {{ border: 2px solid {p.acento}; }}
QListWidget::item {{ border-radius: {r}px; padding: {pad}px; }}
QListWidget::item:hover {{ background: {p.superficie_alta}; }}
QListWidget::item:selected, QTableWidget::item:selected {{ background: {p.acento_suave}; color: {p.texto}; }}
QHeaderView::section {{ background: {p.superficie_alta}; color: {p.texto_suave}; border: none;
    border-bottom: 1px solid {p.borde}; padding: {pad}px; }}
QTableWidget {{ gridline-color: {p.borde}; }}

/* Barra lateral, paneles y estado */
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {p.superficie}; padding: {pad + 2}px; text-align: left; }}
#barra_lateral, #barra_lateral QWidget#selector_lateral {{ background: {p.superficie}; }}
#selector_lateral QToolButton {{ padding: {pad + 2}px; }}
#tarjeta {{ background: {p.superficie}; border: 1px solid {p.borde}; border-radius: {r + 2}px; }}
#tarjeta QLabel {{ background: transparent; }}
#titulo_panel {{ font-weight: 600; font-size: {round(t * 1.05, 1)}pt; }}
#texto_suave {{ color: {p.texto_suave}; }}
QStatusBar {{ background: {p.superficie}; color: {p.texto_suave}; border-top: 1px solid {p.borde}; }}
QStatusBar QLabel {{ color: {p.texto_suave}; font-size: {chico}pt; padding: 0 {pad * 2}px; }}
QStatusBar::item {{ border: none; }}

QMenu {{ background: {p.superficie}; border: 1px solid {p.borde}; border-radius: {r}px; padding: {pad}px; }}
QMenu::item {{ padding: {pad + 1}px {pad * 6}px {pad + 1}px {pad * 3}px; border-radius: {r - 2}px; }}
QMenu::item:selected {{ background: {p.acento_suave}; color: {p.texto}; }}
QMenu::item:disabled {{ color: {p.texto_suave}; }}
QMenu::separator {{ height: 1px; background: {p.borde}; margin: {pad}px {pad * 2}px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle {{ background: {p.borde}; border-radius: 5px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:hover {{ background: {p.texto_suave}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {p.borde}; }}

/* Paleta de comandos */
#paleta_comandos {{ background: {p.superficie}; border: 1px solid {p.borde}; border-radius: {r + 4}px; }}
#paleta_comandos QLineEdit {{ font-size: {round(t * 1.2, 1)}pt; padding: {pad * 2}px; }}
#paleta_comandos QTreeWidget {{ border: none; background: {p.superficie}; }}
#paleta_comandos QTreeWidget::item {{ padding: {pad + 2}px {pad}px; }}
#paleta_comandos QTreeWidget::item:selected {{ background: {p.acento_suave}; color: {p.texto}; }}
QTreeView::item:selected {{ background: {p.acento_suave}; color: {p.texto}; }}
"""


def _qpalette(p: Paleta) -> QPalette:
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.fondo, QPalette.ColorRole.WindowText: p.texto,
        QPalette.ColorRole.Base: p.superficie, QPalette.ColorRole.AlternateBase: p.superficie_alta,
        QPalette.ColorRole.Text: p.texto, QPalette.ColorRole.Button: p.superficie_alta,
        QPalette.ColorRole.ButtonText: p.texto, QPalette.ColorRole.Highlight: p.acento,
        QPalette.ColorRole.HighlightedText: p.sobre_acento, QPalette.ColorRole.ToolTipBase: p.superficie_alta,
        QPalette.ColorRole.ToolTipText: p.texto, QPalette.ColorRole.PlaceholderText: p.texto_suave,
        QPalette.ColorRole.Link: p.acento, QPalette.ColorRole.Mid: p.borde,
    }
    for rol, color in roles.items():
        pal.setColor(rol, QColor(color))
    for rol in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, rol, QColor(p.texto_suave))
    return pal


class GestorTema(QObject):
    """Tema y escala actuales; avisa cuando cambian para que se redibujen íconos y vistas."""

    cambio = Signal()

    def __init__(self):
        super().__init__()
        self._ajustes = QSettings("PDFStudio", "PDF Studio")
        self.preferencia: str = str(self._ajustes.value("tema", "sistema"))
        if self.preferencia not in TEMAS:
            self.preferencia = "sistema"
        try:
            self.escala: float = float(self._ajustes.value("escala", 1.0))
        except (TypeError, ValueError):
            self.escala = 1.0
        self._conectado = False

    @property
    def paleta(self) -> Paleta:
        if self.preferencia == "sistema":
            return OSCURO if self.windows_oscuro() else CLARO
        return PALETAS[self.preferencia]

    @staticmethod
    def windows_oscuro() -> bool:
        app = QGuiApplication.instance()
        if app is None:
            return False
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def aplicar(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if not self._conectado:
            app.styleHints().colorSchemeChanged.connect(self._windows_cambio)
            self._conectado = True
        app.setStyle(QStyleFactory.create("Fusion"))
        p = self.paleta
        app.setPalette(_qpalette(p))
        app.setStyleSheet(hoja_de_estilos(p, self.escala))
        self.cambio.emit()

    def _windows_cambio(self, *_):
        if self.preferencia == "sistema":
            self.aplicar()

    def set_tema(self, nombre: str) -> None:
        if nombre not in TEMAS:
            raise ValueError(nombre)
        self.preferencia = nombre
        self._ajustes.setValue("tema", nombre)
        self.aplicar()

    def set_escala(self, escala: float) -> None:
        self.escala = min(ESCALAS[-1], max(ESCALAS[0], round(escala, 2)))
        self._ajustes.setValue("escala", self.escala)
        self.aplicar()

    def agrandar(self) -> None:
        mayores = [e for e in ESCALAS if e > self.escala + 1e-6]
        if mayores:
            self.set_escala(mayores[0])

    def achicar(self) -> None:
        menores = [e for e in ESCALAS if e < self.escala - 1e-6]
        if menores:
            self.set_escala(menores[-1])

    def tamano_icono(self, base: int = 20) -> int:
        return round(base * self.escala)


_gestor: GestorTema | None = None


def gestor() -> GestorTema:
    global _gestor
    if _gestor is None:
        _gestor = GestorTema()
    return _gestor
