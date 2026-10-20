"""Punto de entrada de PDF Studio."""
from __future__ import annotations

import sys
import traceback


def _falta_paquete(nombre: str, error: Exception) -> int:
    """Si falta una dependencia esencial no se puede ni mostrar la ventana: se avisa por consola."""
    print(f"PDF Studio no puede arrancar: falta el paquete «{nombre}» ({error}).")
    print("Instalalo con este comando de PowerShell (con el .venv activo):")
    print(f'  & "{sys.executable}" -m pip install -r requirements.txt')
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    import importlib
    for modulo, paquete in (("PySide6.QtWidgets", "PySide6"), ("pymupdf", "pymupdf"), ("numpy", "numpy"),
                            ("PIL", "Pillow"), ("fontTools", "fonttools")):
        try:
            importlib.import_module(modulo)
        except ImportError as e:
            return _falta_paquete(paquete, e)

    from PySide6.QtWidgets import QApplication, QMessageBox

    from pdf_studio import NOMBRE_APP
    from pdf_studio.ui.ventana_principal import VentanaPrincipal

    app = QApplication(argv)
    app.setApplicationName(NOMBRE_APP)
    app.setOrganizationName("PDFStudio")

    def excepcion_no_controlada(tipo, valor, tb):
        # Nunca fallar en silencio: cualquier error inesperado se muestra
        texto = "".join(traceback.format_exception(tipo, valor, tb))
        print(texto, file=sys.stderr)
        QMessageBox.critical(None, "Error inesperado",
                             f"Ocurrió un error inesperado:\n\n{valor}\n\nDetalle técnico:\n{texto[-1500:]}")

    sys.excepthook = excepcion_no_controlada

    ventana = VentanaPrincipal()
    ventana.show()
    if len(argv) > 1:
        ventana.abrir(argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
