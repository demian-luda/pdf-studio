"""Ventana principal: menús, barra de herramientas, visor, miniaturas, búsqueda y faltantes.

Todos los diálogos pasan por métodos _pedir_* / _informar / _preguntar para que los
tests puedan reemplazarlos sin abrir ventanas modales.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QImage, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMessageBox, QProgressDialog, QVBoxLayout,
                               QWidget)

from pdf_studio import NOMBRE_APP, __version__
from pdf_studio.core.anotaciones import AgregarNota, AgregarResaltado, quads_de_palabras
from pdf_studio.core.documento import Documento, ErrorContrasena, ErrorDocumento, Guardado
from pdf_studio.core.fuentes import indice_incluidas, indice_sistema
from pdf_studio.core.imagenes import (EXTENSIONES_IMAGEN, BibliotecaFirmas, InsertarImagen, a_png,
                                      tiene_transparencia)
from pdf_studio.core.paginas import RotarPagina
from pdf_studio.core.texto import BloqueTexto, Nivel, OpcionFuente, analizar_fuente, bloque_en, planificar
from pdf_studio.dependencias.chequeo import chequear_fuentes, chequear_inicio
from pdf_studio.ui.busqueda import BarraBusqueda
from pdf_studio.ui.colocacion import ImagenColocable
from pdf_studio.ui.dialogo_fidelidad import mostrar_guardado, preguntar_guardar_igual
from pdf_studio.ui.edicion_texto import DialogoEditarTexto
from pdf_studio.ui.firmas import DialogoQuitarFondo, PanelFirmas
from pdf_studio.ui.miniaturas import PanelMiniaturas
from pdf_studio.ui.panel_faltantes import PanelFaltantes
from pdf_studio.ui.visor import Modo, Visor


class VentanaPrincipal(QMainWindow):
    def __init__(self, chequear_al_iniciar: bool = True, biblioteca: BibliotecaFirmas | None = None):
        super().__init__()
        self.documento: Documento | None = None
        self.biblioteca = biblioteca if biblioteca is not None else BibliotecaFirmas()
        self.bloque: BloqueTexto | None = None        # texto seleccionado en modo Editar texto
        self._cache_fuentes: dict = {}
        self.setWindowTitle(NOMBRE_APP)
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        # Centro: barra de búsqueda + visor
        self.visor = Visor(self)
        self.busqueda = BarraBusqueda(self)
        self.busqueda.hide()
        centro = QWidget(self)
        capa = QVBoxLayout(centro)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.setSpacing(0)
        capa.addWidget(self.busqueda)
        capa.addWidget(self.visor, 1)
        self.setCentralWidget(centro)

        # Paneles laterales
        self.miniaturas = PanelMiniaturas(self)
        self.dock_miniaturas = QDockWidget("Páginas", self)
        self.dock_miniaturas.setObjectName("dock_miniaturas")
        self.dock_miniaturas.setWidget(self.miniaturas)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_miniaturas)

        self.faltantes = PanelFaltantes(self)
        self.dock_faltantes = QDockWidget("Faltantes", self)
        self.dock_faltantes.setObjectName("dock_faltantes")
        self.dock_faltantes.setWidget(self.faltantes)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_faltantes)
        self.panel_firmas = PanelFirmas(self.biblioteca, self)
        self.dock_firmas = QDockWidget("Firmas", self)
        self.dock_firmas.setObjectName("dock_firmas")
        self.dock_firmas.setWidget(self.panel_firmas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_firmas)
        self.tabifyDockWidget(self.dock_faltantes, self.dock_firmas)
        self.resizeDocks([self.dock_miniaturas, self.dock_faltantes], [170, 470], Qt.Orientation.Horizontal)
        self.dock_faltantes.hide()
        self.dock_firmas.hide()

        # Barra de estado
        self.etiqueta_pagina = QLabel("", self)
        self.etiqueta_zoom = QLabel("", self)
        self.statusBar().addPermanentWidget(self.etiqueta_pagina)
        self.statusBar().addPermanentWidget(self.etiqueta_zoom)

        self._crear_acciones()
        self._crear_menus()
        self._conectar()
        self._actualizar_estado()

        if chequear_al_iniciar:
            QTimer.singleShot(0, self.chequear_dependencias)

    # ------------------------------------------------------------------ acciones
    def _accion(self, texto: str, atajo=None, slot=None, checkable=False, tip: str = "") -> QAction:
        a = QAction(texto, self)
        if atajo is not None:
            if isinstance(atajo, (list, tuple)):
                a.setShortcuts([QKeySequence(x) for x in atajo])
            else:
                a.setShortcut(QKeySequence(atajo))
        if slot is not None:
            a.triggered.connect(slot)
        a.setCheckable(checkable)
        if tip:
            a.setStatusTip(tip)
        self.addAction(a)
        return a

    def _crear_acciones(self) -> None:
        S = QKeySequence.StandardKey
        self.acc_abrir = self._accion("&Abrir…", S.Open, self.accion_abrir)
        self.acc_guardar = self._accion("&Guardar copia", S.Save, self.guardar_copia,
                                        tip="Guarda una copia editada; el original no se toca.")
        self.acc_guardar_como = self._accion("Guardar copia &como…", "Ctrl+Shift+S", self.guardar_como)
        self.acc_sobrescribir = self._accion("&Sobrescribir original…", None, self.sobrescribir_original,
                                             tip="Reemplaza el archivo original (pide confirmación).")
        self.acc_cerrar = self._accion("&Cerrar documento", S.Close, self.cerrar_documento)
        self.acc_salir = self._accion("Sa&lir", "Ctrl+Q", self.close)

        self.acc_deshacer = self._accion("&Deshacer", S.Undo, self.deshacer)
        self.acc_rehacer = self._accion("&Rehacer", ["Ctrl+Y", "Ctrl+Shift+Z"], self.rehacer)
        self.acc_rotar_der = self._accion("Rotar página a la de&recha", "Ctrl+R", lambda: self.rotar(90))
        self.acc_rotar_izq = self._accion("Rotar página a la i&zquierda", "Ctrl+Shift+R", lambda: self.rotar(-90))

        self.grupo_modo = QActionGroup(self)
        self.acc_modo_mano = self._accion("Mano (desplazar)", "H", lambda: self.set_modo(Modo.MANO), True)
        self.acc_modo_resaltar = self._accion("Resaltar texto", "Ctrl+Shift+H",
                                              lambda: self.set_modo(Modo.RESALTAR), True,
                                              "Arrastrá sobre el texto para resaltarlo.")
        self.acc_modo_nota = self._accion("Agregar nota", "Ctrl+Shift+N", lambda: self.set_modo(Modo.NOTA), True,
                                          "Hacé clic donde quieras dejar una nota.")
        self.acc_insertar_imagen = self._accion("Insertar imagen desde archivo…", "Ctrl+Shift+I",
                                                self.accion_insertar_imagen)
        self.acc_firmas = self._accion("Firmas…", "Ctrl+Shift+F", self.mostrar_firmas,
                                       tip="Biblioteca de firmas: agregar, quitar fondo e insertar.")
        self.acc_modo_texto = self._accion("Editar texto", "Ctrl+Shift+E", lambda: self.set_modo(Modo.TEXTO), True,
                                           "Clic en un texto para seleccionarlo; doble clic o F2 para editarlo; "
                                           "arrastrá el marco para moverlo.")
        self.acc_editar_seleccion = self._accion("Editar texto seleccionado…", "F2", self.editar_seleccion)
        for a in (self.acc_modo_mano, self.acc_modo_resaltar, self.acc_modo_nota, self.acc_modo_texto):
            self.grupo_modo.addAction(a)
        self.acc_modo_mano.setChecked(True)

        self.acc_buscar = self._accion("&Buscar…", S.Find, self.abrir_busqueda)
        self.acc_acercar = self._accion("Acercar", [S.ZoomIn, "Ctrl+="], self.visor.acercar)
        self.acc_alejar = self._accion("Alejar", S.ZoomOut, self.visor.alejar)
        self.acc_zoom_real = self._accion("Tamaño real (100%)", "Ctrl+0", self.visor.zoom_real)
        self.acc_ajustar_ancho = self._accion("Ajustar al ancho", "Ctrl+1", self.visor.ajustar_ancho)
        self.acc_ajustar_pagina = self._accion("Ajustar página", "Ctrl+2", self.visor.ajustar_pagina)
        self.acc_pag_ant = self._accion("Página anterior", "PgUp",
                                        lambda: self.visor.ir_a_pagina(self.visor.pagina_actual - 1))
        self.acc_pag_sig = self._accion("Página siguiente", "PgDown",
                                        lambda: self.visor.ir_a_pagina(self.visor.pagina_actual + 1))
        self.acc_chequear = self._accion("Verificar dependencias", None, self.chequear_dependencias)
        self.acc_ver_faltantes = self.dock_faltantes.toggleViewAction()
        self.acc_ver_faltantes.setText("Panel de faltantes")
        self.acc_ver_miniaturas = self.dock_miniaturas.toggleViewAction()
        self.acc_ver_miniaturas.setText("Panel de páginas")
        self.acc_acerca = self._accion("Acerca de PDF Studio", None, self.acerca_de)

    def _crear_menus(self) -> None:
        m = self.menuBar().addMenu("&Archivo")
        for a in (self.acc_abrir, None, self.acc_guardar, self.acc_guardar_como, self.acc_sobrescribir,
                  None, self.acc_cerrar, self.acc_salir):
            m.addSeparator() if a is None else m.addAction(a)
        m = self.menuBar().addMenu("&Editar")
        for a in (self.acc_deshacer, self.acc_rehacer, None, self.acc_rotar_der, self.acc_rotar_izq,
                  None, self.acc_modo_mano, self.acc_modo_texto, self.acc_editar_seleccion,
                  self.acc_modo_resaltar, self.acc_modo_nota, None,
                  self.acc_firmas, self.acc_insertar_imagen):
            m.addSeparator() if a is None else m.addAction(a)
        m = self.menuBar().addMenu("&Ver")
        for a in (self.acc_acercar, self.acc_alejar, self.acc_zoom_real, self.acc_ajustar_ancho,
                  self.acc_ajustar_pagina, None, self.acc_pag_ant, self.acc_pag_sig, None,
                  self.acc_ver_miniaturas, self.acc_ver_faltantes):
            m.addSeparator() if a is None else m.addAction(a)
        m = self.menuBar().addMenu("&Herramientas")
        m.addAction(self.acc_buscar)
        m.addAction(self.acc_chequear)
        m = self.menuBar().addMenu("A&yuda")
        m.addAction(self.acc_acerca)

        barra = self.addToolBar("Principal")
        barra.setObjectName("barra_principal")
        for a in (self.acc_abrir, self.acc_guardar, None, self.acc_deshacer, self.acc_rehacer, None,
                  self.acc_alejar, self.acc_acercar, self.acc_ajustar_ancho, None,
                  self.acc_rotar_izq, self.acc_rotar_der, None,
                  self.acc_modo_mano, self.acc_modo_texto, None, self.acc_modo_resaltar, self.acc_modo_nota,
                  None, self.acc_firmas, None, self.acc_buscar):
            barra.addSeparator() if a is None else barra.addAction(a)
        self.combo_nivel = QComboBox(self)
        self.combo_nivel.addItem("Fragmento", Nivel.FRAGMENTO)
        self.combo_nivel.addItem("Línea", Nivel.LINEA)
        self.combo_nivel.addItem("Párrafo", Nivel.PARRAFO)
        self.combo_nivel.setCurrentIndex(1)
        self.combo_nivel.setToolTip("Qué se selecciona al hacer clic en un texto")
        barra.insertWidget(self.acc_modo_resaltar, self.combo_nivel)

    def _conectar(self) -> None:
        self.visor.pagina_actual_cambio.connect(self._pagina_cambio)
        self.visor.zoom_cambio.connect(lambda z: self.etiqueta_zoom.setText(f"Zoom {z * 100:.0f}%"))
        self.visor.rect_seleccionado.connect(self._resaltar_en)
        self.visor.punto_clic.connect(self._nota_en)
        self.miniaturas.pagina_elegida.connect(self.visor.ir_a_pagina)
        self.busqueda.resultados_cambiaron.connect(self.visor.mostrar_resultados)
        self.faltantes.rechequear.connect(self.chequear_dependencias)
        self.visor.texto_clic.connect(self._texto_clic)
        self.visor.texto_doble_clic.connect(self._texto_doble_clic)
        self.visor.seleccion_movida.connect(self.mover_seleccion)
        self.visor.colocacion_confirmada.connect(self.aplicar_colocacion)
        self.visor.colocacion_cancelada.connect(self.cancelar_colocacion)
        self.panel_firmas.insertar.connect(self.insertar_firma)
        self.panel_firmas.agregar.connect(self.agregar_firma)
        self.panel_firmas.quitar_fondo_de.connect(self.quitar_fondo_de_firma)
        self.panel_firmas.eliminar.connect(self.eliminar_firma)
        self.panel_firmas.aplicar_colocacion.connect(self.aplicar_colocacion)
        self.panel_firmas.cancelar_colocacion.connect(self.cancelar_colocacion)

    # ------------------------------------------------------------------ diálogos (reemplazables en tests)
    def _pedir_ruta_abrir(self) -> str | None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Abrir PDF", "", "Documentos PDF (*.pdf)")
        return ruta or None

    def _pedir_ruta_guardar(self, sugerida: Path) -> str | None:
        ruta, _ = QFileDialog.getSaveFileName(self, "Guardar copia", str(sugerida), "Documentos PDF (*.pdf)")
        return ruta or None

    def _pedir_contrasena(self, nombre: str, intento: int) -> str | None:
        texto = f"«{nombre}» está protegido. Ingresá la contraseña:"
        if intento:
            texto = "Contraseña incorrecta. Probá de nuevo:\n" + texto
        valor, ok = QInputDialog.getText(self, "Contraseña", texto, QLineEdit.EchoMode.Password)
        return valor if ok else None

    def _pedir_ruta_imagen(self, titulo: str) -> str | None:
        filtro = "Imágenes (" + " ".join(f"*{e}" for e in EXTENSIONES_IMAGEN) + ")"
        ruta, _ = QFileDialog.getOpenFileName(self, titulo, "", filtro)
        return ruta or None

    def _editar_fondo(self, imagen: Image.Image, nombre: str) -> tuple[Image.Image, str] | None:
        """Abre el diálogo Quitar fondo. Devuelve (imagen, nombre) o None si se canceló."""
        dialogo = DialogoQuitarFondo(imagen, nombre, self)
        if dialogo.exec() and dialogo.resultado is not None:
            return dialogo.resultado, dialogo.nombre.text()
        return None

    def _dialogo_editar_texto(self, bloque: BloqueTexto) -> tuple[str, OpcionFuente] | None:
        dialogo = DialogoEditarTexto(bloque, lambda texto: self._analizar(bloque, texto),
                                     lambda: indice_sistema().cargar(), self)
        if dialogo.exec() and dialogo.opcion_elegida() is not None:
            return dialogo.texto, dialogo.opcion_elegida()
        return None

    def _confirmar_avisos(self, avisos: list[str]) -> bool:
        return self._preguntar("Editar texto", "Antes de aplicar:\n\n• " + "\n• ".join(avisos)
                               + "\n\n¿Aplicar igual?")

    def _pedir_texto_nota(self) -> str | None:
        valor, ok = QInputDialog.getMultiLineText(self, "Nueva nota", "Texto de la nota:")
        return valor if ok and valor.strip() else None

    def _informar(self, titulo: str, texto: str, error: bool = False) -> None:
        (QMessageBox.critical if error else QMessageBox.information)(self, titulo, texto)

    def _preguntar(self, titulo: str, texto: str) -> bool:
        r = QMessageBox.question(self, titulo, texto, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        return r == QMessageBox.StandardButton.Yes

    def _preguntar_guardar_cambios(self) -> str:
        """'guardar', 'descartar' o 'cancelar'."""
        r = QMessageBox.question(
            self, "Cambios sin guardar", "Hay cambios sin guardar. ¿Querés guardar una copia antes de cerrar?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save)
        return {QMessageBox.StandardButton.Save: "guardar",
                QMessageBox.StandardButton.Discard: "descartar"}.get(r, "cancelar")

    def _preguntar_guardar_igual(self, guardado: Guardado) -> bool:
        return preguntar_guardar_igual(self, guardado)

    def _mostrar_guardado(self, guardado: Guardado, destino: Path) -> None:
        mostrar_guardado(self, guardado, destino)

    # ------------------------------------------------------------------ abrir / cerrar
    def accion_abrir(self) -> None:
        ruta = self._pedir_ruta_abrir()
        if ruta:
            self.abrir(ruta)

    def abrir(self, ruta: str | Path) -> bool:
        if not self.cerrar_documento():
            return False
        ruta = Path(ruta)
        contrasena = None
        intento = 0
        while True:
            try:
                QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
                try:
                    documento = Documento(ruta, contrasena)
                finally:
                    QApplication.restoreOverrideCursor()
                break
            except ErrorContrasena:
                contrasena = self._pedir_contrasena(ruta.name, intento)
                intento += 1
                if contrasena is None:
                    return False
            except ErrorDocumento as e:
                self._informar("No se pudo abrir", str(e), error=True)
                return False
        self.documento = documento
        documento.al_cambiar(self._documento_cambio)
        self.visor.set_documento(documento)
        self.miniaturas.set_documento(documento)
        self.busqueda.set_documento(documento)
        self._chequear_fuentes_documento()
        self._actualizar_estado()
        self.statusBar().showMessage(f"Abierto: {ruta.name} ({documento.doc.page_count} páginas)", 5000)
        return True

    def cerrar_documento(self) -> bool:
        """Cierra el documento actual. Devuelve False si el usuario canceló."""
        if self.documento is None:
            return True
        if self.documento.modificado:
            r = self._preguntar_guardar_cambios()
            if r == "cancelar":
                return False
            if r == "guardar" and not self.guardar_copia():
                return False
        doc = self.documento
        self.documento = None
        self.visor.set_documento(None)
        self.miniaturas.set_documento(None)
        self.busqueda.set_documento(None)
        self.busqueda.hide()
        self.faltantes.set_documento(None, [])
        self.panel_firmas.set_colocando(False)
        doc.cerrar()
        self._actualizar_estado()
        return True

    def closeEvent(self, evento: QCloseEvent) -> None:
        if self.cerrar_documento():
            evento.accept()
        else:
            evento.ignore()

    def dragEnterEvent(self, evento) -> None:
        if any(u.toLocalFile().lower().endswith(".pdf") for u in evento.mimeData().urls()):
            evento.acceptProposedAction()

    def dropEvent(self, evento) -> None:
        for u in evento.mimeData().urls():
            if u.toLocalFile().lower().endswith(".pdf"):
                self.abrir(u.toLocalFile())
                break

    # ------------------------------------------------------------------ guardar
    def guardar_copia(self) -> bool:
        if self.documento is None:
            return False
        destino = self.documento.ultimo_guardado
        if destino is None or destino == self.documento.ruta_original:
            return self.guardar_como()
        return self._guardar_en(destino)

    def guardar_como(self) -> bool:
        if self.documento is None:
            return False
        ruta = self._pedir_ruta_guardar(self.documento.ruta_sugerida())
        if not ruta:
            return False
        destino = Path(ruta)
        if destino.suffix.lower() != ".pdf":
            destino = destino.with_suffix(".pdf")
        if destino.resolve() == self.documento.ruta_original:
            self._informar("Guardar copia", "Elegiste el archivo original. Para no perderlo, PDF Studio "
                                            "guarda siempre una copia nueva.\nSi realmente querés "
                                            "reemplazarlo, usá Archivo → Sobrescribir original.")
            return False
        return self._guardar_en(destino)

    def sobrescribir_original(self) -> bool:
        if self.documento is None:
            return False
        if not self._preguntar("Sobrescribir original",
                               f"Se va a reemplazar el archivo original:\n{self.documento.ruta_original}\n\n"
                               "Esta acción no se puede deshacer fuera de PDF Studio. ¿Continuar?"):
            return False
        return self._guardar_en(self.documento.ruta_original, sobrescribir=True)

    def _guardar_en(self, destino: Path, sobrescribir: bool = False) -> bool:
        if self.visor.colocacion is not None:
            # Se guarda lo que se ve: la imagen que se estaba ubicando se aplica primero
            self.aplicar_colocacion()
        doc = self.documento
        dialogo = QProgressDialog("Verificando fidelidad visual…", "Cancelar", 0, 100, self)
        dialogo.setWindowTitle("Guardando")
        dialogo.setWindowModality(Qt.WindowModality.WindowModal)
        dialogo.setMinimumDuration(400)

        def progreso(hechas: int, total: int) -> bool:
            dialogo.setMaximum(max(1, total))
            dialogo.setValue(hechas)
            QApplication.processEvents()
            return not dialogo.wasCanceled()

        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                guardado = doc.preparar_guardado(progreso)
            finally:
                QApplication.restoreOverrideCursor()
                dialogo.close()
            if not guardado.es_fiel and not self._preguntar_guardar_igual(guardado):
                self.statusBar().showMessage("No se guardó: la verificación de fidelidad falló.", 8000)
                return False
            final = doc.confirmar_guardado(guardado, destino, sobrescribir_original=sobrescribir)
        except ErrorDocumento as e:
            self._informar("No se pudo guardar", str(e), error=True)
            return False
        self.statusBar().showMessage(f"Guardado: {final}", 8000)
        self._mostrar_guardado(guardado, final)
        return True

    # ------------------------------------------------------------------ edición
    def deshacer(self) -> None:
        if self.documento is not None:
            self.documento.deshacer()

    def rehacer(self) -> None:
        if self.documento is not None:
            self.documento.rehacer()

    def rotar(self, grados: int) -> None:
        if self.documento is not None:
            self.documento.ejecutar(RotarPagina(self.visor.pagina_actual, grados))

    def set_modo(self, modo: Modo) -> None:
        self.visor.set_modo(modo)
        {Modo.MANO: self.acc_modo_mano, Modo.RESALTAR: self.acc_modo_resaltar,
         Modo.NOTA: self.acc_modo_nota, Modo.TEXTO: self.acc_modo_texto}[modo].setChecked(True)
        if modo != Modo.TEXTO:
            self.bloque = None

    def _resaltar_en(self, pagina: int, rect) -> None:
        if self.documento is None:
            return
        quads = quads_de_palabras(self.documento.doc[pagina], rect)
        if not quads:
            self.statusBar().showMessage("No hay texto en esa zona para resaltar.", 4000)
            return
        self.documento.ejecutar(AgregarResaltado(pagina, quads))

    def _nota_en(self, pagina: int, punto) -> None:
        if self.documento is None:
            return
        texto = self._pedir_texto_nota()
        if texto:
            self.documento.ejecutar(AgregarNota(pagina, (punto.x, punto.y), texto))

    # ------------------------------------------------------------------ firmas e imágenes
    def mostrar_firmas(self) -> None:
        self.dock_firmas.show()
        self.dock_firmas.raise_()

    def agregar_firma(self) -> None:
        ruta = self._pedir_ruta_imagen("Agregar firma a la biblioteca")
        if not ruta:
            return
        try:
            imagen = Image.open(ruta)
            imagen.load()
        except (OSError, Image.DecompressionBombError) as e:
            self._informar("Agregar firma", f"No se pudo leer la imagen: {e}", error=True)
            return
        nombre = Path(ruta).stem
        if tiene_transparencia(imagen) and not self._preguntar(
                "Agregar firma", "La imagen ya tiene fondo transparente. ¿Querés ajustar el fondo igual?"):
            final = self.biblioteca.agregar(imagen, nombre)
        else:
            editado = self._editar_fondo(imagen, nombre)
            if editado is None:
                return
            final = self.biblioteca.agregar(*editado)
        self.panel_firmas.recargar()
        self.panel_firmas.seleccionar(final)
        self.statusBar().showMessage(f"Firma guardada en la biblioteca: {final.name}", 5000)

    def quitar_fondo_de_firma(self, ruta: Path) -> None:
        with Image.open(ruta) as imagen:
            imagen.load()
            editado = self._editar_fondo(imagen.copy(), f"{ruta.stem} (sin fondo)")
        if editado is not None:
            final = self.biblioteca.agregar(*editado)
            self.panel_firmas.recargar()
            self.panel_firmas.seleccionar(final)

    def eliminar_firma(self, ruta: Path) -> None:
        if self._preguntar("Eliminar firma", f"¿Eliminar «{ruta.stem}» de la biblioteca?"):
            self.biblioteca.eliminar(ruta)
            self.panel_firmas.recargar()

    def insertar_firma(self, ruta: Path) -> None:
        self.iniciar_colocacion(Path(ruta).read_bytes(), "firma")

    def accion_insertar_imagen(self) -> None:
        if self.documento is None:
            return
        ruta = self._pedir_ruta_imagen("Insertar imagen")
        if not ruta:
            return
        ruta = Path(ruta)
        datos = ruta.read_bytes()
        if ruta.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            # Otros formatos se pasan a PNG (sin pérdida); PNG y JPG se insertan tal cual
            with Image.open(ruta) as imagen:
                datos = a_png(imagen.convert("RGBA"))
        self.iniciar_colocacion(datos, "imagen")

    def iniciar_colocacion(self, datos: bytes, descripcion: str) -> bool:
        if self.documento is None:
            self._informar("Insertar", "Primero abrí un PDF.")
            return False
        imagen = QImage.fromData(datos)
        if imagen.isNull():
            self._informar("Insertar", "No se pudo leer la imagen.", error=True)
            return False
        pag = self.documento.doc[self.visor.pagina_actual]
        # Tamaño inicial: el de la imagen a 150 dpi, sin pasar de la mitad del ancho de la página
        ancho = min(imagen.width() * 72 / 150, pag.rect.width * 0.5)
        ancho = max(ancho, 40.0)
        self.set_modo(Modo.MANO)
        self.visor.iniciar_colocacion(ImagenColocable(imagen, datos, ancho, descripcion))
        self.panel_firmas.set_colocando(True)
        self.mostrar_firmas()   # ahí están los botones Aplicar / Cancelar
        self.visor.setFocus()
        self.statusBar().showMessage("Ubicá la imagen y apretá Enter para aplicarla (Esc cancela).")
        return True

    def aplicar_colocacion(self) -> None:
        item = self.visor.colocacion
        resultado = self.visor.terminar_colocacion()
        self.panel_firmas.set_colocando(False)
        if resultado is None or self.documento is None:
            return
        pagina, esquinas = resultado
        try:
            self.documento.ejecutar(InsertarImagen(pagina, esquinas, item.datos, item.descripcion))
        except ErrorDocumento as e:
            self._informar("Insertar", str(e), error=True)
            return
        self.statusBar().showMessage(f"Se insertó la {item.descripcion} en la página {pagina + 1}.", 5000)

    def cancelar_colocacion(self) -> None:
        self.visor.cancelar_colocacion()
        self.panel_firmas.set_colocando(False)
        self.statusBar().clearMessage()

    # ------------------------------------------------------------------ edición de texto
    @property
    def nivel_texto(self) -> str:
        return self.combo_nivel.currentData()

    def _analizar(self, bloque: BloqueTexto, texto: str):
        doc = self.documento.doc
        return analizar_fuente(doc, doc[bloque.pagina], bloque.estilo, texto, indice_sistema(),
                               indice_incluidas(), self._cache_fuentes)

    def seleccionar_texto(self, pagina: int, punto) -> BloqueTexto | None:
        if self.documento is None:
            return None
        bloque = bloque_en(self.documento.doc[pagina], pagina, punto, self.nivel_texto)
        self.bloque = bloque
        if bloque is None:
            self.visor.limpiar_seleccion()
            self.statusBar().showMessage("No hay texto editable en ese lugar.", 4000)
            return None
        self.visor.mostrar_seleccion(pagina, bloque.bbox)
        resumen = bloque.texto if len(bloque.texto) < 60 else bloque.texto[:57] + "…"
        self.statusBar().showMessage(f"Seleccionado: «{resumen}». Doble clic o F2 para editar; "
                                     "arrastrá el marco para moverlo.")
        return bloque

    def _texto_clic(self, pagina: int, punto) -> None:
        self.seleccionar_texto(pagina, punto)

    def _texto_doble_clic(self, pagina: int, punto) -> None:
        if self.seleccionar_texto(pagina, punto) is not None:
            self.editar_seleccion()

    def editar_seleccion(self) -> None:
        bloque = self.bloque
        if self.documento is None or bloque is None:
            return
        resultado = self._dialogo_editar_texto(bloque)
        if resultado is None:
            return
        texto, opcion = resultado
        self._aplicar_plan(bloque, texto, opcion, (0.0, 0.0))

    def mover_seleccion(self, pagina: int, dx: float, dy: float) -> None:
        bloque = self.bloque
        if self.documento is None or bloque is None or bloque.pagina != pagina:
            return
        opcion = self._analizar(bloque, bloque.texto).recomendada
        self._aplicar_plan(bloque, bloque.texto, opcion, (dx, dy))

    def _aplicar_plan(self, bloque: BloqueTexto, texto: str, opcion: OpcionFuente | None,
                      desplazamiento: tuple[float, float]) -> bool:
        try:
            plan = planificar(self.documento.doc, bloque, texto, opcion, desplazamiento, self._cache_fuentes)
            if plan.avisos and not self._confirmar_avisos(plan.avisos):
                self.visor.recargar()
                return False
            self.documento.ejecutar(plan.operacion)
        except ErrorDocumento as e:
            self._informar("Editar texto", str(e), error=True)
            self.visor.recargar()
            return False
        self.bloque = None
        self.statusBar().showMessage(plan.operacion.descripcion() + ": listo.", 5000)
        return True

    def _documento_cambio(self) -> None:
        self._cache_fuentes = {}
        self.bloque = None
        self.visor.recargar()
        self.miniaturas.recargar()
        self.busqueda.rehacer_busqueda()
        self._actualizar_estado()

    # ------------------------------------------------------------------ búsqueda / navegación
    def abrir_busqueda(self) -> None:
        if self.documento is not None:
            self.busqueda.abrir()

    def _pagina_cambio(self, indice: int) -> None:
        self.miniaturas.marcar_pagina(indice)
        self._actualizar_estado()

    # ------------------------------------------------------------------ dependencias
    def chequear_dependencias(self) -> None:
        faltan = chequear_inicio()
        self.faltantes.set_generales(faltan)
        if self.documento is not None:
            self._chequear_fuentes_documento()
        if self.faltantes.faltantes:
            self.dock_faltantes.show()
            self.statusBar().showMessage(f"Faltan {len(self.faltantes.faltantes)} elemento(s): "
                                         "mirá el panel Faltantes.", 8000)

    def _chequear_fuentes_documento(self) -> None:
        if self.documento is None:
            return
        indice = indice_sistema()
        if not indice._cargado:
            self.statusBar().showMessage("Analizando las fuentes instaladas (solo tarda la primera vez)…")
            QApplication.processEvents()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            fuentes, faltan = chequear_fuentes(self.documento.doc, indice, indice_incluidas())
        finally:
            QApplication.restoreOverrideCursor()
        self.faltantes.set_documento(fuentes, faltan)
        if faltan:
            self.dock_faltantes.show()

    # ------------------------------------------------------------------ estado
    def _actualizar_estado(self) -> None:
        hay = self.documento is not None
        for a in (self.acc_guardar, self.acc_guardar_como, self.acc_sobrescribir, self.acc_cerrar,
                  self.acc_rotar_der, self.acc_rotar_izq, self.acc_buscar, self.acc_acercar,
                  self.acc_alejar, self.acc_zoom_real, self.acc_ajustar_ancho, self.acc_ajustar_pagina,
                  self.acc_modo_resaltar, self.acc_modo_nota, self.acc_pag_ant, self.acc_pag_sig,
                  self.acc_insertar_imagen, self.acc_modo_texto, self.acc_editar_seleccion):
            a.setEnabled(hay)
        self.acc_deshacer.setEnabled(hay and self.documento.puede_deshacer)
        self.acc_rehacer.setEnabled(hay and self.documento.puede_rehacer)
        self.acc_deshacer.setText(f"&Deshacer: {self.documento.texto_deshacer}"
                                  if hay and self.documento.puede_deshacer else "&Deshacer")
        self.acc_rehacer.setText(f"&Rehacer: {self.documento.texto_rehacer}"
                                 if hay and self.documento.puede_rehacer else "&Rehacer")
        if hay:
            marca = "*" if self.documento.modificado else ""
            self.setWindowTitle(f"{self.documento.ruta_original.name}{marca} — {NOMBRE_APP}")
            self.etiqueta_pagina.setText(f"Página {self.visor.pagina_actual + 1} de {self.visor.cantidad_paginas}")
        else:
            self.setWindowTitle(NOMBRE_APP)
            self.etiqueta_pagina.setText("")
            self.etiqueta_zoom.setText("")

    def acerca_de(self) -> None:
        self._informar("Acerca de PDF Studio",
                       f"{NOMBRE_APP} {__version__}\nEditor de PDF gratuito y de código abierto (AGPL-3.0).\n\n"
                       "El archivo original nunca se modifica: siempre se guarda una copia.")
