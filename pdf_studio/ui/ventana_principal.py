"""Ventana principal: encabezado con cinta, barra lateral, visor, búsqueda y paneles.

Todos los diálogos pasan por métodos _pedir_* / _informar / _preguntar / _confirmar_* para
que los tests puedan reemplazarlos sin abrir ventanas modales.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QImage, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressDialog, QToolButton,
                               QVBoxLayout, QWidget)

from pdf_studio import NOMBRE_APP
from pdf_studio.config import DIR_RECURSOS
from pdf_studio.core.anotaciones import AgregarNota, AgregarResaltado, quads_de_palabras
from pdf_studio.core.documento import Documento, ErrorContrasena, ErrorDocumento, Guardado
from pdf_studio.core.fuentes import familia_de, indice_incluidas, indice_sistema
from pdf_studio.core.imagenes import (EXTENSIONES_IMAGEN, BibliotecaFirmas, InsertarImagen, a_png,
                                      tiene_transparencia)
from pdf_studio.core.paginas import RotarPagina
from pdf_studio.core.sustitucion import PlanSustitucion
from pdf_studio.core.texto import BloqueTexto, Nivel, OpcionFuente, analizar_fuente, bloque_en, planificar
from pdf_studio.dependencias.chequeo import Faltante, Importancia, chequear_inicio, faltantes_de_fuentes
from pdf_studio.dependencias.instalador import Instalacion, instalacion_fuente
from pdf_studio.ui import iconos
from pdf_studio.ui.errores import registro
from pdf_studio.ui.acerca import DialogoAcerca
from pdf_studio.ui.barra_lateral import BarraLateral
from pdf_studio.ui.busqueda import BarraBusqueda
from pdf_studio.ui.cinta import Encabezado
from pdf_studio.ui.colocacion import ImagenColocable
from pdf_studio.ui.dialogo_fidelidad import mostrar_guardado, preguntar_guardar_igual
from pdf_studio.ui.edicion_texto import DialogoEditarTexto
from pdf_studio.ui.firmas import DialogoQuitarFondo, PanelFirmas
from pdf_studio.ui.instalacion import DialogoConfirmarInstalacion, ejecutar_con_progreso
from pdf_studio.ui.miniaturas import PanelMiniaturas
from pdf_studio.ui.paleta_comandos import PaletaComandos
from pdf_studio.ui.segundo_plano import AnalizadorFuentes, EnHilo
from pdf_studio.ui.panel_faltantes import PanelFaltantes, PanelFuentes
from pdf_studio.ui.sustitucion import DialogoSustitucion
from pdf_studio.ui.tema import gestor
from pdf_studio.ui.visor import Modo, Visor

PESTANAS = ("Ver", "Editar", "Firmar", "Páginas", "Herramientas")


class VentanaPrincipal(QMainWindow):
    def __init__(self, chequear_al_iniciar: bool = True, biblioteca: BibliotecaFirmas | None = None):
        super().__init__()
        self.documento: Documento | None = None
        self.biblioteca = biblioteca if biblioteca is not None else BibliotecaFirmas()
        self.bloque: BloqueTexto | None = None        # texto seleccionado en modo Editar texto
        self._cache_fuentes: dict = {}
        self._acciones: list[tuple[QAction, str]] = []   # (acción, grupo) para la paleta de comandos
        self._hilos: list[EnHilo] = []
        self.analizador = AnalizadorFuentes(self)
        self.analizador.terminado.connect(self._analisis_terminado)
        self.analizador.fallo.connect(self._analisis_fallo)
        self._mostrar_tras_analisis = False
        self.setWindowTitle(NOMBRE_APP)
        self.setWindowIcon(QIcon(str(DIR_RECURSOS / "logo.ico")))
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

        # Barra lateral (páginas / fuentes / faltantes)
        self.miniaturas = PanelMiniaturas(self)
        self.panel_fuentes = PanelFuentes(self)
        self.faltantes = PanelFaltantes(self)
        self.lateral = BarraLateral(self)
        self.dock_lateral = QDockWidget("Barra lateral", self)
        self.dock_lateral.setObjectName("dock_lateral")
        self.dock_lateral.setTitleBarWidget(QWidget())
        self.dock_lateral.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.dock_lateral.setWidget(self.lateral)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.dock_lateral)

        # Panel de firmas (contextual, a la derecha)
        self.panel_firmas = PanelFirmas(self.biblioteca, self)
        self.dock_firmas = QDockWidget("Firmas", self)
        self.dock_firmas.setObjectName("dock_firmas")
        self.dock_firmas.setWidget(self.panel_firmas)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock_firmas)
        self.dock_firmas.hide()

        # Barra de estado discreta
        self.boton_faltantes = QToolButton(self)
        self.boton_faltantes.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.boton_faltantes.setAutoRaise(True)
        self.boton_faltantes.clicked.connect(self.mostrar_faltantes)
        self.boton_faltantes.hide()
        iconos.asignar(self.boton_faltantes, "triangle-alert")
        self.etiqueta_pagina = QLabel("", self)
        self.etiqueta_zoom = QLabel("", self)
        self.etiqueta_analisis = QLabel("", self)
        self.statusBar().addPermanentWidget(self.etiqueta_analisis)
        self.statusBar().addPermanentWidget(self.boton_faltantes)
        self.statusBar().addPermanentWidget(self.etiqueta_pagina)
        self.statusBar().addPermanentWidget(self.etiqueta_zoom)
        self.statusBar().setSizeGripEnabled(False)

        self._crear_acciones()
        self._crear_encabezado()
        self.lateral.agregar("paginas", "Páginas", "files", self.miniaturas)
        self.lateral.agregar("fuentes", "Fuentes del documento", "type", self.panel_fuentes)
        self.lateral.agregar("faltantes", "Faltantes", "package-search", self.faltantes)
        self._conectar()
        gestor().cambio.connect(self._tema_cambio)
        self._actualizar_estado()
        # El índice de fuentes de Windows se arma en segundo plano (con caché en disco)
        self._en_hilo(lambda: (indice_sistema().cargar(), indice_incluidas().cargar()), lambda _r: None,
                      lambda e: registro().warning("No se pudo indexar las fuentes: %r", e))
        if chequear_al_iniciar:
            QTimer.singleShot(0, self.chequear_dependencias)

    # ------------------------------------------------------------------ acciones
    def _accion(self, texto: str, icono: str | None, atajo=None, slot=None, checkable: bool = False,
                tip: str = "", grupo: str = "") -> QAction:
        a = QAction(texto, self)
        atajos = [] if atajo is None else ([atajo] if not isinstance(atajo, (list, tuple)) else list(atajo))
        if atajos:
            a.setShortcuts([QKeySequence(x) for x in atajos])
        if slot is not None:
            a.triggered.connect(slot)
        a.setCheckable(checkable)
        if tip:
            a.setStatusTip(tip)
        if icono:
            iconos.asignar(a, icono)
        self._tooltip(a)
        self.addAction(a)
        self._acciones.append((a, grupo))
        return a

    @staticmethod
    def _tooltip(a: QAction, texto: str | None = None) -> None:
        """Tooltip = nombre + atajo de teclado (y la descripción, si hay)."""
        nombre = (texto or a.text()).replace("&", "")
        atajo = a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        tip = f"{nombre} ({atajo})" if atajo else nombre
        if a.statusTip():
            tip += f"\n{a.statusTip()}"
        a.setToolTip(tip)

    def _crear_acciones(self) -> None:
        S = QKeySequence.StandardKey
        A, E, V, F, P, H = "Archivo", "Editar", "Ver", "Firmar", "Páginas", "Herramientas"
        self.acc_abrir = self._accion("Abrir…", "folder-open", S.Open, self.accion_abrir, grupo=A)
        self.acc_guardar = self._accion("Guardar copia", "save", S.Save, self.guardar_copia, grupo=A,
                                        tip="Guarda una copia editada; el original no se toca.")
        self.acc_guardar_como = self._accion("Guardar copia como…", "save-all", "Ctrl+Shift+S", self.guardar_como,
                                             grupo=A)
        self.acc_sobrescribir = self._accion("Sobrescribir original…", "file-exclamation-point", None,
                                             self.sobrescribir_original, grupo=A,
                                             tip="Reemplaza el archivo original (pide confirmación).")
        self.acc_cerrar = self._accion("Cerrar documento", "file-x", S.Close, self.cerrar_documento, grupo=A)
        self.acc_salir = self._accion("Salir", "log-out", "Ctrl+Q", self.close, grupo=A)

        self.acc_deshacer = self._accion("Deshacer", "undo-2", S.Undo, self.deshacer, grupo=E)
        self.acc_rehacer = self._accion("Rehacer", "redo-2", ["Ctrl+Y", "Ctrl+Shift+Z"], self.rehacer, grupo=E)
        self.acc_rotar_der = self._accion("Rotar a la derecha", "rotate-cw", "Ctrl+R", lambda: self.rotar(90),
                                          grupo=P)
        self.acc_rotar_izq = self._accion("Rotar a la izquierda", "rotate-ccw", "Ctrl+Shift+R",
                                          lambda: self.rotar(-90), grupo=P)

        self.grupo_modo = QActionGroup(self)
        self.acc_modo_mano = self._accion("Mano", "hand", "H", lambda: self.set_modo(Modo.MANO), True,
                                          "Desplazar la página arrastrando.", E)
        self.acc_modo_texto = self._accion("Editar texto", "type", "Ctrl+Shift+E", lambda: self.set_modo(Modo.TEXTO),
                                           True, "Clic para seleccionar; doble clic o F2 para editar; "
                                                 "arrastrá el marco para mover.", E)
        self.acc_editar_seleccion = self._accion("Editar selección…", "pencil-line", "F2", self.editar_seleccion,
                                                 grupo=E)
        self.acc_modo_resaltar = self._accion("Resaltar", "highlighter", "Ctrl+Shift+H",
                                              lambda: self.set_modo(Modo.RESALTAR), True,
                                              "Arrastrá sobre el texto para resaltarlo.", E)
        self.acc_modo_nota = self._accion("Nota", "sticky-note", "Ctrl+Shift+N", lambda: self.set_modo(Modo.NOTA),
                                          True, "Hacé clic donde quieras dejar una nota.", E)
        for a in (self.acc_modo_mano, self.acc_modo_resaltar, self.acc_modo_nota, self.acc_modo_texto):
            self.grupo_modo.addAction(a)
        self.acc_modo_mano.setChecked(True)
        self.acc_firmas = self._accion("Firmas", "signature", "Ctrl+Shift+F", self.mostrar_firmas, grupo=F,
                                       tip="Biblioteca de firmas: agregar, quitar fondo e insertar.")
        self.acc_insertar_imagen = self._accion("Insertar imagen…", "image-plus", "Ctrl+Shift+I",
                                                self.accion_insertar_imagen, grupo=F)

        self.acc_buscar = self._accion("Buscar", "search", S.Find, self.abrir_busqueda, grupo=V)
        self.acc_acercar = self._accion("Acercar", "zoom-in", [S.ZoomIn, "Ctrl+="], self.visor.acercar, grupo=V)
        self.acc_alejar = self._accion("Alejar", "zoom-out", S.ZoomOut, self.visor.alejar, grupo=V)
        self.acc_zoom_real = self._accion("Tamaño real", "ratio", "Ctrl+0", self.visor.zoom_real, grupo=V)
        self.acc_ajustar_ancho = self._accion("Ajustar al ancho", "move-horizontal", "Ctrl+1",
                                              self.visor.ajustar_ancho, grupo=V)
        self.acc_ajustar_pagina = self._accion("Ajustar página", "maximize-2", "Ctrl+2", self.visor.ajustar_pagina,
                                               grupo=V)
        self.acc_pag_ant = self._accion("Página anterior", "chevron-up", "PgUp",
                                        lambda: self.visor.ir_a_pagina(self.visor.pagina_actual - 1), grupo=V)
        self.acc_pag_sig = self._accion("Página siguiente", "chevron-down", "PgDown",
                                        lambda: self.visor.ir_a_pagina(self.visor.pagina_actual + 1), grupo=V)
        self.acc_lateral = self._accion("Barra lateral", "panel-left", "Ctrl+B", self.alternar_lateral, True,
                                        grupo=V)
        self.acc_lateral.setChecked(True)
        self.acc_ver_paginas = self._accion("Ver páginas", "files", "Ctrl+Alt+1",
                                            lambda: self.mostrar_lateral("paginas"), grupo=V)
        self.acc_ver_fuentes = self._accion("Ver fuentes del documento", "type", "Ctrl+Alt+2",
                                            lambda: self.mostrar_lateral("fuentes"), grupo=V)
        self.acc_ver_faltantes = self._accion("Ver faltantes", "package-search", "Ctrl+Alt+3",
                                              self.mostrar_faltantes, grupo=V)

        self.grupo_tema = QActionGroup(self)
        self.acc_tema_sistema = self._accion("Tema según Windows", "monitor", None,
                                             lambda: gestor().set_tema("sistema"), True, grupo=V)
        self.acc_tema_claro = self._accion("Tema claro", "sun", None, lambda: gestor().set_tema("claro"), True,
                                           grupo=V)
        self.acc_tema_oscuro = self._accion("Tema oscuro", "moon", None, lambda: gestor().set_tema("oscuro"), True,
                                            grupo=V)
        for a in (self.acc_tema_sistema, self.acc_tema_claro, self.acc_tema_oscuro):
            self.grupo_tema.addAction(a)
        self.acc_tema = self._accion("Tema", "sun-moon", None, None, grupo=V, tip="Claro, oscuro o según Windows.")
        self.acc_agrandar = self._accion("Agrandar la interfaz", "a-large-small", "Ctrl+Alt+=", gestor().agrandar,
                                         grupo=V)
        self.acc_achicar = self._accion("Achicar la interfaz", "a-large-small", "Ctrl+Alt+-", gestor().achicar,
                                        grupo=V)
        self.acc_escala_normal = self._accion("Tamaño normal de la interfaz", "a-large-small", "Ctrl+Alt+0",
                                              lambda: gestor().set_escala(1.0), grupo=V)

        self.acc_sustituir = self._accion("Reemplazar fuente…", "replace", None, lambda: self.sustituir_fuentes(),
                                          grupo=H, tip="Modo armónico: reemplaza una fuente en todo el documento.")
        self.acc_chequear = self._accion("Verificar dependencias", "shield-check", None, self.chequear_dependencias,
                                         grupo=H)
        self.acc_paleta = self._accion("Paleta de comandos", "command", "Ctrl+K", self.abrir_paleta, grupo=H,
                                       tip="Buscá cualquier acción por su nombre.")
        self.acc_acerca = self._accion("Acerca de PDF Studio", "info", None, self.acerca_de, grupo=H)
        self.acc_mas = self._accion("Más opciones", "ellipsis", None, None, grupo="")

        # Navegación con teclado (no aparecen en la paleta)
        for i in range(len(PESTANAS)):
            a = QAction(self)
            a.setShortcut(QKeySequence(f"Alt+{i + 1}"))
            a.triggered.connect(lambda _=False, n=i: self.ir_a_pestana(n))
            self.addAction(a)
        foco = QAction(self)
        foco.setShortcut(QKeySequence("F6"))
        foco.triggered.connect(self.siguiente_region)
        self.addAction(foco)

    def _crear_encabezado(self) -> None:
        enc = self.encabezado = Encabezado(self)
        # Acceso rápido: archivo + deshacer/rehacer
        for a in (self.acc_abrir, self.acc_guardar):
            enc.rapida.addAction(a)
        menu_mas = QMenu(self)
        for a in (self.acc_guardar_como, self.acc_sobrescribir, self.acc_cerrar):
            menu_mas.addAction(a)
        menu_mas.addSeparator()
        menu_mas.addAction(self.acc_acerca)
        menu_mas.addAction(self.acc_salir)
        enc.agregar_menu(enc.rapida, self.acc_mas, menu_mas)
        enc.rapida.addSeparator()
        enc.rapida.addAction(self.acc_deshacer)
        enc.rapida.addAction(self.acc_rehacer)
        # Derecha: paleta y tema
        enc.derecha.addAction(self.acc_paleta)
        menu_tema = QMenu(self)
        for a in (self.acc_tema_sistema, self.acc_tema_claro, self.acc_tema_oscuro):
            menu_tema.addAction(a)
        menu_tema.addSeparator()
        for a in (self.acc_agrandar, self.acc_achicar, self.acc_escala_normal):
            menu_tema.addAction(a)
        enc.agregar_menu(enc.derecha, self.acc_tema, menu_tema)

        ver = enc.agregar_pestana("Ver")
        for a in (self.acc_alejar, self.acc_acercar, self.acc_zoom_real, self.acc_ajustar_ancho,
                  self.acc_ajustar_pagina, None, self.acc_pag_ant, self.acc_pag_sig, None, self.acc_buscar,
                  None, self.acc_lateral):
            ver.addSeparator() if a is None else ver.addAction(a)
        editar = enc.agregar_pestana("Editar")
        for a in (self.acc_modo_mano, self.acc_modo_texto):
            editar.addAction(a)
        self.combo_nivel = QComboBox(self)
        self.combo_nivel.addItem("Fragmento", Nivel.FRAGMENTO)
        self.combo_nivel.addItem("Línea", Nivel.LINEA)
        self.combo_nivel.addItem("Párrafo", Nivel.PARRAFO)
        self.combo_nivel.setCurrentIndex(1)
        self.combo_nivel.setToolTip("Qué se selecciona al hacer clic en un texto")
        self.combo_nivel.setAccessibleName("Nivel de selección de texto")
        editar.addWidget(self.combo_nivel)
        for a in (self.acc_editar_seleccion, None, self.acc_modo_resaltar, self.acc_modo_nota, None,
                  self.acc_sustituir):
            editar.addSeparator() if a is None else editar.addAction(a)
        firmar = enc.agregar_pestana("Firmar")
        for a in (self.acc_firmas, self.acc_insertar_imagen):
            firmar.addAction(a)
        paginas = enc.agregar_pestana("Páginas")
        for a in (self.acc_rotar_izq, self.acc_rotar_der):
            paginas.addAction(a)
        herramientas = enc.agregar_pestana("Herramientas")
        for a in (self.acc_sustituir, self.acc_chequear, None, self.acc_paleta, self.acc_acerca):
            herramientas.addSeparator() if a is None else herramientas.addAction(a)
        enc.finalizar()
        enc.pestanas.setCurrentIndex(1)       # arranca en "Editar"
        self.setMenuWidget(enc)
        self._marcar_tema()

    def _conectar(self) -> None:
        self.visor.pagina_actual_cambio.connect(self._pagina_cambio)
        self.visor.zoom_cambio.connect(lambda z: self.etiqueta_zoom.setText(f"{z * 100:.0f}%"))
        self.visor.rect_seleccionado.connect(self._resaltar_en)
        self.visor.punto_clic.connect(self._nota_en)
        self.miniaturas.pagina_elegida.connect(self.visor.ir_a_pagina)
        self.busqueda.resultados_cambiaron.connect(self.visor.mostrar_resultados)
        self.faltantes.rechequear.connect(self.chequear_dependencias)
        self.faltantes.instalar.connect(self.instalar_faltante)
        self.panel_fuentes.reemplazar_fuente.connect(lambda nombre: self.sustituir_fuentes(nombre or None))
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
        self.dock_lateral.visibilityChanged.connect(self.acc_lateral.setChecked)

    # ------------------------------------------------------------------ tema, navegación y paleta
    def _tema_cambio(self) -> None:
        iconos.refrescar()
        self.visor.aplicar_tema()
        self._marcar_tema()

    def _marcar_tema(self) -> None:
        {"sistema": self.acc_tema_sistema, "claro": self.acc_tema_claro,
         "oscuro": self.acc_tema_oscuro}[gestor().preferencia].setChecked(True)

    def ir_a_pestana(self, indice: int) -> None:
        self.encabezado.pestanas.setCurrentIndex(indice)
        self.encabezado.pestanas.setFocus()

    def siguiente_region(self) -> None:
        """F6: pasa el foco entre la cinta, el visor y la barra lateral."""
        regiones = [self.encabezado.pestanas, self.visor]
        if self.dock_lateral.isVisible():
            regiones.append(self.lateral.pila.currentWidget())
        actual = QApplication.focusWidget()
        indice = next((i for i, w in enumerate(regiones) if w is actual or (actual and w.isAncestorOf(actual))), -1)
        regiones[(indice + 1) % len(regiones)].setFocus(Qt.FocusReason.TabFocusReason)

    def acciones_de_paleta(self) -> list[tuple[QAction, str]]:
        return [(a, g) for a, g in self._acciones if g and a is not self.acc_tema]

    def abrir_paleta(self) -> None:
        paleta = PaletaComandos(self.acciones_de_paleta(), self)
        geo = self.geometry()
        paleta.move(geo.x() + (geo.width() - paleta.width()) // 2, geo.y() + 80)
        paleta.campo.setFocus()
        paleta.exec()

    def alternar_lateral(self) -> None:
        self.dock_lateral.setVisible(not self.dock_lateral.isVisible())

    def mostrar_lateral(self, clave: str) -> None:
        self.dock_lateral.show()
        self.lateral.mostrar(clave)

    def mostrar_faltantes(self) -> None:
        self.mostrar_lateral("faltantes")

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
                                     lambda: indice_sistema().cargar(), self,
                                     instalar=lambda fuente: self.instalar(instalacion_fuente(fuente)))
        if dialogo.exec() and dialogo.opcion_elegida() is not None:
            return dialogo.texto, dialogo.opcion_elegida()
        return None

    def _dialogo_sustitucion(self, preseleccion: str | None) -> PlanSustitucion | None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            dialogo = DialogoSustitucion(self.documento.doc, indice_incluidas(), indice_sistema(),
                                         familia_de(preseleccion) if preseleccion else None, self)
        finally:
            QApplication.restoreOverrideCursor()
        if dialogo.exec() and dialogo.plan is not None and dialogo.plan.operacion is not None:
            return dialogo.plan
        return None

    def _confirmar_instalacion(self, inst: Instalacion) -> bool:
        return bool(DialogoConfirmarInstalacion(inst, self).exec())

    def _ejecutar_instalacion(self, inst: Instalacion) -> tuple[bool, str]:
        return ejecutar_con_progreso(inst, self)

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

    # ------------------------------------------------------------------ instalación asistida
    def instalar(self, inst: Instalacion) -> bool:
        """Instala con confirmación explícita. Devuelve True si quedó instalado."""
        if not self._confirmar_instalacion(inst):
            return False
        ok, mensaje = self._ejecutar_instalacion(inst)
        self._informar("Instalación", mensaje, error=not ok)
        if ok:
            self.chequear_dependencias(mostrar=False)
        return ok

    def instalar_faltante(self, faltante: Faltante) -> None:
        if faltante.instalacion is not None:
            self.instalar(faltante.instalacion)

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
        self.analizador.cancelar()
        self.faltantes.set_estado("documento", None)
        self._estado_analisis()
        self.visor.set_documento(None)
        self.miniaturas.set_documento(None)
        self.busqueda.set_documento(None)
        self.busqueda.hide()
        self.panel_fuentes.set_fuentes(None)
        self.faltantes.set_documento([])
        self._actualizar_insignia()
        self.panel_firmas.set_colocando(False)
        doc.cerrar()
        self._actualizar_estado()
        return True

    def showEvent(self, evento) -> None:
        super().showEvent(evento)
        if not getattr(self, "_docks_dimensionados", False):
            # resizeDocks solo tiene efecto con la ventana ya visible
            self._docks_dimensionados = True
            ancho = round(360 * gestor().escala)
            QTimer.singleShot(0, lambda: self.resizeDocks([self.dock_lateral], [ancho], Qt.Orientation.Horizontal))

    def closeEvent(self, evento: QCloseEvent) -> None:
        if self.cerrar_documento():
            for hilo in list(self._hilos):
                hilo.wait(10_000)       # no destruir hilos en marcha
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
                                            "reemplazarlo, usá Más opciones → Sobrescribir original.")
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

    # ------------------------------------------------------------------ sustitución de fuentes
    def sustituir_fuentes(self, preseleccion: str | None = None) -> bool:
        if self.documento is None:
            return False
        plan = self._dialogo_sustitucion(preseleccion)
        if plan is None:
            return False
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.documento.ejecutar(plan.operacion)
            finally:
                QApplication.restoreOverrideCursor()
        except ErrorDocumento as e:
            self._informar("Reemplazar fuente", str(e), error=True)
            return False
        detalle = plan.resumen()
        if plan.ajustes:
            detalle += "\n\nLíneas con más ajuste:\n" + "\n".join(
                f"• Página {a.pagina + 1}: {a.porcentaje:+.1f}% «{a.texto[:50]}»" for a in plan.ajustes[:10])
        self._informar("Reemplazar fuente", detalle)
        self._chequear_fuentes_documento()
        return True

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
    def _en_hilo(self, funcion, al_terminar, al_fallar) -> EnHilo:
        hilo = EnHilo(funcion, self)
        hilo.listo.connect(al_terminar)
        hilo.fallo.connect(al_fallar)
        hilo.finished.connect(lambda h=hilo: self._hilos.remove(h) if h in self._hilos else None)
        self._hilos.append(hilo)
        hilo.start()
        return hilo

    def _estado_analisis(self) -> None:
        """Etiqueta de la barra de estado mientras algo se analiza (se limpia sola)."""
        partes = []
        if self.faltantes.estado("inicio") == "analizando":
            partes.append("dependencias")
        if self.faltantes.estado("documento") == "analizando":
            partes.append("fuentes del documento")
        self.etiqueta_analisis.setText("Analizando " + " y ".join(partes) + "…" if partes else "")

    def chequear_dependencias(self, mostrar: bool = True) -> None:
        """Chequea paquetes y programas en segundo plano (Tesseract puede tardar en responder)."""
        self.faltantes.set_estado("inicio", "analizando")
        self._estado_analisis()

        def listo(faltan) -> None:
            self.faltantes.set_generales(faltan)
            self.faltantes.set_estado("inicio", "ok")
            self._fin_de_analisis(mostrar)

        def fallo(error) -> None:
            registro().error("Falló el chequeo de dependencias: %r", error)
            self.faltantes.set_estado("inicio", "error", "No se pudieron chequear las dependencias.")
            self._fin_de_analisis(mostrar)

        self._en_hilo(chequear_inicio, listo, fallo)
        if self.documento is not None:
            self._chequear_fuentes_documento(mostrar=False)

    def _chequear_fuentes_documento(self, mostrar: bool = True) -> None:
        """Analiza las fuentes del documento en otro proceso: la interfaz sigue usable."""
        if self.documento is None:
            return
        doc = self.documento
        ruta = doc.ruta_base
        if doc.operaciones_activas:
            # Hubo cambios (p. ej. se reemplazó una fuente): se analiza el estado actual
            ruta = doc.ruta_base.with_name("analisis_actual.pdf")
            try:
                ruta.write_bytes(doc.doc.tobytes())
            except Exception as e:
                registro().error("No se pudo preparar el análisis: %r", e)
                ruta = doc.ruta_base
        self._mostrar_tras_analisis = mostrar
        self.faltantes.set_estado("documento", "analizando")
        self.panel_fuentes.set_estado("analizando")
        self._estado_analisis()
        self.analizador.iniciar(ruta, doc.contrasena)

    def _analisis_terminado(self, fuentes) -> None:
        if self.documento is None:
            return
        self.panel_fuentes.set_fuentes(fuentes)
        self.faltantes.set_documento(faltantes_de_fuentes(fuentes))
        self.faltantes.set_estado("documento", "ok")
        self._fin_de_analisis(self._mostrar_tras_analisis)

    def _analisis_fallo(self, mensaje: str) -> None:
        registro().error("Falló el análisis de fuentes: %s", mensaje)
        self.panel_fuentes.set_estado("error", mensaje)
        self.faltantes.set_estado("documento", "error", mensaje)
        self._fin_de_analisis(self._mostrar_tras_analisis)

    def _fin_de_analisis(self, mostrar: bool) -> None:
        self._estado_analisis()
        self._actualizar_insignia()
        importante = any(f.importancia != Importancia.OPCIONAL for f in self.faltantes.faltantes)
        if mostrar and (importante or self.faltantes.hay_error):
            self.mostrar_faltantes()

    @property
    def analisis_en_curso(self) -> bool:
        return "analizando" in (self.faltantes.estado("inicio"), self.faltantes.estado("documento"))
    def _actualizar_insignia(self) -> None:
        n = len(self.faltantes.faltantes)
        self.lateral.set_insignia("faltantes", n)
        if self.faltantes.hay_error:
            self.boton_faltantes.setText("Análisis incompleto")
            self.boton_faltantes.setToolTip("No se pudo terminar el análisis: ver detalles (Ctrl+Alt+3)")
        else:
            self.boton_faltantes.setText(f"{n} faltante{'s' if n != 1 else ''}")
            self.boton_faltantes.setToolTip("Ver qué falta instalar (Ctrl+Alt+3)")
        self.boton_faltantes.setVisible(n > 0 or self.faltantes.hay_error)

    # ------------------------------------------------------------------ estado
    def _actualizar_estado(self) -> None:
        hay = self.documento is not None
        for a in (self.acc_guardar, self.acc_guardar_como, self.acc_sobrescribir, self.acc_cerrar,
                  self.acc_rotar_der, self.acc_rotar_izq, self.acc_buscar, self.acc_acercar,
                  self.acc_alejar, self.acc_zoom_real, self.acc_ajustar_ancho, self.acc_ajustar_pagina,
                  self.acc_modo_resaltar, self.acc_modo_nota, self.acc_pag_ant, self.acc_pag_sig,
                  self.acc_insertar_imagen, self.acc_modo_texto, self.acc_editar_seleccion, self.acc_sustituir):
            a.setEnabled(hay)
        puede_deshacer = hay and self.documento.puede_deshacer
        puede_rehacer = hay and self.documento.puede_rehacer
        self.acc_deshacer.setEnabled(puede_deshacer)
        self.acc_rehacer.setEnabled(puede_rehacer)
        # El texto de la acción dice qué se deshace; el tooltip lo muestra con el atajo
        self.acc_deshacer.setText(f"Deshacer: {self.documento.texto_deshacer}" if puede_deshacer else "Deshacer")
        self.acc_rehacer.setText(f"Rehacer: {self.documento.texto_rehacer}" if puede_rehacer else "Rehacer")
        self._tooltip(self.acc_deshacer)
        self._tooltip(self.acc_rehacer)
        if hay:
            marca = "*" if self.documento.modificado else ""
            self.setWindowTitle(f"{self.documento.ruta_original.name}{marca} — {NOMBRE_APP}")
            self.etiqueta_pagina.setText(f"Página {self.visor.pagina_actual + 1} de {self.visor.cantidad_paginas}")
        else:
            self.setWindowTitle(NOMBRE_APP)
            self.etiqueta_pagina.setText("")
            self.etiqueta_zoom.setText("")

    def acerca_de(self) -> None:
        DialogoAcerca(self).exec()
