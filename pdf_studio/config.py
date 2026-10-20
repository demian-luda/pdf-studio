"""Rutas y constantes globales de la aplicación."""
import os
from pathlib import Path

# Carpeta del paquete y recursos incluidos con la app (fuentes libres, íconos)
DIR_PAQUETE = Path(__file__).resolve().parent
DIR_RECURSOS = DIR_PAQUETE / "recursos"
DIR_FUENTES_INCLUIDAS = DIR_RECURSOS / "fuentes"


def _carpeta_entorno(variable: str, respaldo: str) -> Path:
    base = os.environ.get(variable)
    return Path(base) if base else Path.home() / respaldo


# Datos del usuario que viajan con el perfil (firmas)
DIR_APPDATA = _carpeta_entorno("APPDATA", "AppData/Roaming") / "PDFStudio"
DIR_FIRMAS = DIR_APPDATA / "firmas"

# Datos locales de la máquina (caché, idiomas de Tesseract)
DIR_LOCAL = _carpeta_entorno("LOCALAPPDATA", "AppData/Local") / "PDFStudio"
DIR_CACHE = DIR_LOCAL / "cache"
DIR_TESSDATA_USUARIO = DIR_LOCAL / "tessdata"

# Carpetas de fuentes de Windows: del sistema y del usuario actual
DIR_FUENTES_WINDOWS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
DIR_FUENTES_USUARIO = _carpeta_entorno("LOCALAPPDATA", "AppData/Local") / "Microsoft" / "Windows" / "Fonts"

# Resolución usada para comparar renders en la verificación de fidelidad
DPI_FIDELIDAD = 150
