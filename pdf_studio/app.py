"""Punto de entrada de PDF Studio."""
from __future__ import annotations

import os
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
    if len(argv) > 4 and argv[1] == "--tarea":
        # Proceso auxiliar (versión empaquetada): compresión, comparación
        from pathlib import Path

        from pdf_studio.procesos import ejecutar
        return ejecutar(argv[2], Path(argv[3]), Path(argv[4]))
    if len(argv) > 2 and argv[1] == "--autoprueba":
        # Comprueba que la versión empaquetada tenga todo (ver pdf_studio/autoprueba.py)
        from pdf_studio.autoprueba import main as autoprueba
        return autoprueba(argv[2])
    if len(argv) > 1 and argv[1] == "--ocrmypdf":
        # Proceso auxiliar (versión empaquetada): ocrmypdf con los argumentos que siguen
        import multiprocessing
        multiprocessing.freeze_support()
        from ocrmypdf.__main__ import run as ocrmypdf
        return ocrmypdf(argv[2:])
    # Se verifica que estén sin importarlos: importarlos acá demoraría cada arranque
    import importlib.util
    for modulo, paquete in (("PySide6.QtWidgets", "PySide6"), ("pymupdf", "pymupdf"), ("numpy", "numpy"),
                            ("PIL", "Pillow"), ("fontTools", "fonttools")):
        try:
            encontrado = importlib.util.find_spec(modulo) is not None
        except (ImportError, ValueError) as e:
            return _falta_paquete(paquete, e)
        if not encontrado:
            return _falta_paquete(paquete, ImportError(f"No se encontró el módulo {modulo}"))

    rutas = [a for a in argv[1:] if not a.startswith("--")]

    # Instancia única: si PDF Studio ya está abierto, le pasa los archivos y termina enseguida
    # (antes de cargar pymupdf o armar la ventana)
    instancia = None
    if not os.environ.get("PDFSTUDIO_SIN_INSTANCIA_UNICA"):
        from pdf_studio.instancia import Instancia
        instancia = Instancia()
        if instancia.entregar_a_otra(rutas):
            return 0

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication

    # Windows agrupa la ventana con su propio ícono en la barra de tareas (y no con el de Python)
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PDFStudio.EditorPDF")
    except (AttributeError, OSError):
        pass

    app = QApplication(argv)
    # Escucha cuanto antes: los procesos que se lancen mientras esta arranca esperan su «ok»
    servidor = instancia.servir(app) if instancia is not None and instancia.principal else None
    recibidos: list[str] = []
    if servidor is not None:
        servidor.archivos_recibidos.connect(recibidos.extend)

    from pdf_studio import NOMBRE_APP
    from pdf_studio.config import DIR_RECURSOS
    from pdf_studio.ui.tema import gestor
    from pdf_studio.ui.ventana_principal import VentanaPrincipal

    app.setApplicationName(NOMBRE_APP)
    app.setOrganizationName("PDFStudio")
    app.setWindowIcon(QIcon(str(DIR_RECURSOS / "logo.ico")))
    gestor().aplicar()

    # Nunca fallar en silencio, pero tampoco en bucle: aviso una vez, detalle en el log
    from pdf_studio.ui.errores import ManejadorErrores, registro
    registro().info("Inicio de PDF Studio")
    sys.excepthook = ManejadorErrores()

    ventana = VentanaPrincipal()
    if servidor is not None:
        servidor.archivos_recibidos.disconnect()
        servidor.archivos_recibidos.connect(ventana.abrir_varios)
    ventana.show()
    if rutas:
        ventana.abrir(rutas[0])
    if rutas[1:] or recibidos:
        ventana.abrir_varios(rutas[1:] + recibidos)
    codigo = app.exec()
    if servidor is not None:
        servidor.cerrar()
    if instancia is not None:
        instancia.liberar()
    return codigo


if __name__ == "__main__":
    sys.exit(main())
