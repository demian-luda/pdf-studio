"""Punto de entrada de PDF Studio."""
from __future__ import annotations

import sys


def _falta_paquete(nombre: str, error: Exception) -> int:
    """Si falta una dependencia esencial no se puede ni mostrar la ventana: se avisa por consola."""
    print(f"PDF Studio no puede arrancar: falta el paquete «{nombre}» ({error}).")
    print("Instalalo con este comando de PowerShell (con el .venv activo):")
    print(f'  & "{sys.executable}" -m pip install -r requirements.txt')
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    if "--analizar-fuentes" in argv:
        # Proceso auxiliar (versión empaquetada): análisis de fuentes sin interfaz
        from pdf_studio.analisis_proceso import main as analizar
        return analizar()
    import importlib
    for modulo, paquete in (("PySide6.QtWidgets", "PySide6"), ("pymupdf", "pymupdf"), ("numpy", "numpy"),
                            ("PIL", "Pillow"), ("fontTools", "fonttools")):
        try:
            importlib.import_module(modulo)
        except ImportError as e:
            return _falta_paquete(paquete, e)

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    from pdf_studio import NOMBRE_APP
    from pdf_studio.config import DIR_RECURSOS
    from pdf_studio.ui.tema import gestor
    from pdf_studio.ui.ventana_principal import VentanaPrincipal

    # Windows agrupa la ventana con su propio ícono en la barra de tareas (y no con el de Python)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFStudio.EditorPDF")
    except (AttributeError, OSError):
        pass

    app = QApplication(argv)
    app.setApplicationName(NOMBRE_APP)
    app.setOrganizationName("PDFStudio")
    app.setWindowIcon(QIcon(str(DIR_RECURSOS / "logo.ico")))
    gestor().aplicar()

    # Nunca fallar en silencio, pero tampoco en bucle: aviso una vez, detalle en el log
    from pdf_studio.ui.errores import ManejadorErrores, registro
    registro().info("Inicio de PDF Studio")
    sys.excepthook = ManejadorErrores()

    ventana = VentanaPrincipal()
    ventana.show()
    if len(argv) > 1:
        ventana.abrir(argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
