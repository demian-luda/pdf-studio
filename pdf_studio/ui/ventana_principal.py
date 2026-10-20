"""Ventana principal: encabezado con cinta, barra lateral, visor, búsqueda y paneles.

Todos los diálogos pasan por métodos _pedir_* / _informar / _preguntar / _confirmar_* para
que los tests puedan reemplazarlos sin abrir ventanas modales.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pymupdf
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QIcon, QImage, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QComboBox, QDockWidget, QFileDialog, QInputDialog, QLabel,
                               QLineEdit, QMainWindow, QMenu, QMessageBox, QProgressDialog, QToolButton,
                               QVBoxLayout, QWidget)

from pdf_studio import NOMBRE_APP
from pdf_studio.config import DIR_RECURSOS
from pdf_studio.core.anotaciones import AgregarNota, AgregarResaltado, quads_de_palabras
from pdf_studio.core.documento import Documento, ErrorContrasena, ErrorDocumento, Guardado
from pdf_studio.core.fuentes import familia_de, indice_incluidas, indice_sistema
from pdf_studio.core.ocr import (ErrorOCR, ModoOCR, capas_de_texto, comando_ocrmypdf, crear_operacion,
                                 entorno_ocr, mensaje_de_error_ocrmypdf, paginas_sin_texto, parece_escaneado)
from pdf_studio.core.imagenes import (EXTENSIONES_IMAGEN, BibliotecaFirmas, InsertarImagen, a_png,
                                      tiene_transparencia)
from pdf_studio.core.comprimir import NivelCompresion, ResultadoCompresion
from pdf_studio.core.formularios import AplanarFormulario, CompletarFormulario, campos, tiene_formulario
from pdf_studio.core.redaccion import Coincidencia, LimpiarMetadatos, OpcionesRedaccion, Redaccion, Redactar
from pdf_studio.core.paginas import (EliminarPaginas, FuentePDF, InsertarDesdePDF, InsertarPaginasEnBlanco,
                                     ReordenarPaginas, RotarPagina, extraer_paginas, mover_paginas,
                                     nombres_libres)
from pdf_studio import procesos
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
from pdf_studio.ui.comunes import BarraAviso
from pdf_studio.ui.dialogo_fidelidad import mostrar_guardado, preguntar_guardar_igual
from pdf_studio.ui.edicion_texto import DialogoEditarTexto
from pdf_studio.ui.firmas import DialogoQuitarFondo, PanelFirmas
from pdf_studio.ui.formulario import PanelFormulario
from pdf_studio.ui.instalacion import DialogoConfirmarInstalacion, ejecutar_con_progreso
from pdf_studio.ui.miniaturas import PanelMiniaturas
from pdf_studio.ui.ocr import DialogoOCR
from pdf_studio.ui.paginas import (DialogoComprimir, DialogoExtraer, DialogoInsertarPDF, DialogoMarcaAgua,
                                   DialogoNumeracion, DialogoResultadoCompresion, DialogoSeparar)
from pdf_studio.ui.paleta_comandos import PaletaComandos
from pdf_studio.ui.segundo_plano import AnalizadorFuentes, EnHilo, ProcesoExterno
from pdf_studio.ui.panel_faltantes import PanelFaltantes, PanelFuentes
from pdf_studio.ui.redaccion import DialogoAplicarRedaccion, DialogoBuscarRedactar
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
        self.proceso_ocr = ProcesoExterno(self)
        self.proceso_ocr.terminado.connect(self._ocr_terminado)
        self._ocr: tuple | None = None            # (modo, páginas, salida, documento) del OCR en curso
        self._progreso_ocr: QProgressDialog | None = None
        # Tareas pesadas de MuPDF (comprimir, comparar) en otro proceso
        self.proceso_tarea = ProcesoExterno(self)
        self.proceso_tarea.terminado.connect(self._tarea_terminada)
        self._tarea: tuple | None = None          # (nombre, archivo de resultado, función al terminar)
        self._progreso_tarea: QProgressDialog | None = None
        self.redaccion_pendiente = Redaccion()     # zonas marcadas, todavía sin aplicar
        self.setWindowTitle(NOMBRE_APP)
        self.setWindowIcon(QIcon(str(DIR_RECURSOS / "logo.ico")))
        self.resize(1280, 860)
        self.setAcceptDrops(True)

        # Centro: aviso + barra de búsqueda + visor
        self.visor = Visor(self)
        self.busqueda = BarraBusqueda(self)
        self.busqueda.hide()
        self.aviso = BarraAviso(self)
        self.aviso.accion.connect(self._accion_aviso)
        self._accion_del_aviso = None
        centro = QWidget(self)
        capa = QVBoxLayout(centro)
        capa.setContentsMargins(0, 0, 0, 0)
        capa.setSpacing(0)
        capa.addWidget(self.aviso)
        capa.addWidget(self.busqueda)
        capa.addWidget(self.visor, 1)
        self.setCentralWidget(centro)

        # Barra lateral (páginas / fuentes / faltantes)
        self.miniaturas = PanelMiniaturas(self)
        self.panel_fuentes = PanelFuentes(self)
        self.faltantes = PanelFaltantes(self)
        self.panel_formulario = PanelFormulario(self)
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
        self.lateral.agregar("formulario", "Formulario", "form-input", self.panel_formulario)
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
                tip: str = "", grupo: str = "", corto: str = "") -> QAction:
        """`corto`: texto debajo del ícono en la cinta (el nombre completo va en el tooltip y la paleta)."""
        a = QAction(texto, self)
        if corto:
            a.setIconText(corto)
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
                                          grupo=P, tip="Rota la página actual o las miniaturas seleccionadas.",
                                          corto="Rotar der.")
        self.acc_rotar_izq = self._accion("Rotar a la izquierda", "rotate-ccw", "Ctrl+Shift+R",
                                          lambda: self.rotar(-90), grupo=P, corto="Rotar izq.")
        self.acc_eliminar_paginas = self._accion("Eliminar páginas", "trash-2", None, self.eliminar_paginas,
                                                 grupo=P, tip="La página actual o las seleccionadas (Supr en las "
                                                              "miniaturas). Se puede deshacer.", corto="Eliminar")
        self.acc_subir_pagina = self._accion("Mover antes", "arrow-up", "Ctrl+Alt+Up",
                                             lambda: self.desplazar_paginas(-1), grupo=P,
                                             tip="Mueve la página (o las seleccionadas) un lugar antes. "
                                                 "También se puede arrastrar las miniaturas.", corto="Antes")
        self.acc_bajar_pagina = self._accion("Mover después", "arrow-down", "Ctrl+Alt+Down",
                                             lambda: self.desplazar_paginas(1), grupo=P,
                                             tip="Mueve la página (o las seleccionadas) un lugar después.",
                                             corto="Después")
        self.acc_insertar_blanco = self._accion("Insertar página en blanco", "file-plus-2", None,
                                                self.insertar_en_blanco, grupo=P,
                                                tip="Después de la página actual, del mismo tamaño.",
                                                corto="En blanco")
        self.acc_insertar_pdf = self._accion("Insertar desde PDF…", "file-input", None, self.insertar_desde_pdf,
                                             grupo=P, tip="Unir: agrega páginas de otros PDFs.", corto="Desde PDF")
        self.acc_extraer = self._accion("Extraer páginas…", "file-output", None, self.extraer, grupo=P,
                                        tip="Copia páginas a un PDF nuevo.", corto="Extraer")
        self.acc_separar = self._accion("Separar en archivos…", "scissors", None, self.separar, grupo=P,
                                        tip="Divide el documento en varios PDFs.", corto="Separar")
        self.acc_marca_agua = self._accion("Marca de agua…", "stamp", None, self.marca_de_agua, grupo=P,
                                           tip="Texto o imagen, con opacidad, posición y rango de páginas.",
                                           corto="Marca de agua")
        self.acc_numerar = self._accion("Numerar páginas…", "list-ordered", None, self.numerar_paginas, grupo=P,
                                        tip="Por ejemplo «Página 1 de 10».", corto="Numerar")

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
        self.acc_modo_redactar = self._accion("Marcar para redactar", "square-dashed", "Ctrl+Shift+D",
                                              lambda: self.set_modo(Modo.REDACTAR), True,
                                              "Arrastrá sobre lo que quieras eliminar del archivo; después, "
                                              "«Aplicar redacción».", E, corto="Marcar zona")
        self.acc_buscar_redactar = self._accion("Buscar datos para redactar…", "text-search", None,
                                                self.buscar_para_redactar, grupo=E,
                                                tip="DNI, CUIT/CUIL, CBU, correos, teléfonos o el texto que "
                                                    "quieras.", corto="Buscar datos")
        self.acc_aplicar_redaccion = self._accion("Aplicar redacción…", "ban", None, self.aplicar_redaccion,
                                                  grupo=E, tip="Elimina del archivo (no solo tapa) lo marcado.",
                                                  corto="Aplicar")
        self.acc_descartar_redaccion = self._accion("Descartar marcas de redacción", "x", None,
                                                    self.descartar_redaccion, grupo=E)
        self.acc_formulario = self._accion("Completar formulario", "form-input", None,
                                           lambda: self.mostrar_lateral("formulario"), grupo=E,
                                           tip="Muestra los campos del formulario en la barra lateral.",
                                           corto="Formulario")
        self.acc_aplanar = self._accion("Aplanar formulario…", "layers", None, self.aplanar_formulario, grupo=E,
                                        tip="Convierte los campos en contenido fijo.", corto="Aplanar")
        self.acc_borrar_metadatos = self._accion("Borrar metadatos", "brush-cleaning", None, self.borrar_metadatos,
                                                 grupo=H, tip="Quita autor, título, programas y fechas del archivo.")
        for a in (self.acc_modo_mano, self.acc_modo_resaltar, self.acc_modo_nota, self.acc_modo_texto,
                  self.acc_modo_redactar):
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
        self.acc_ocr = self._accion("Reconocer texto (OCR)…", "scan-text", "Ctrl+Shift+O", self.reconocer_texto,
                                    grupo=H, tip="Hace buscable y copiable el texto de páginas escaneadas.")
        self.acc_comprimir = self._accion("Comprimir…", "shrink", None, self.comprimir, grupo=H,
                                          tip="Crea una copia más liviana y muestra el tamaño antes y después.")
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
                  self.acc_sustituir, None, self.acc_modo_redactar, self.acc_buscar_redactar,
                  self.acc_aplicar_redaccion, None, self.acc_formulario, self.acc_aplanar):
            editar.addSeparator() if a is None else editar.addAction(a)
        firmar = enc.agregar_pestana("Firmar")
        for a in (self.acc_firmas, self.acc_insertar_imagen):
            firmar.addAction(a)
        paginas = enc.agregar_pestana("Páginas")
        for a in (self.acc_rotar_izq, self.acc_rotar_der, self.acc_subir_pagina, self.acc_bajar_pagina, None,
                  self.acc_insertar_blanco, self.acc_insertar_pdf, self.acc_eliminar_paginas, None, self.acc_extraer, self.acc_separar, None, self.acc_marca_agua,
                  self.acc_numerar):
            paginas.addSeparator() if a is None else paginas.addAction(a)
        herramientas = enc.agregar_pestana("Herramientas")
        for a in (self.acc_ocr, self.acc_comprimir, self.acc_borrar_metadatos, None, self.acc_sustituir,
                  self.acc_chequear, None, self.acc_paleta, self.acc_acerca):
            herramientas.addSeparator() if a is None else herramientas.addAction(a)
        enc.finalizar()
        enc.pestanas.setCurrentIndex(1)       # arranca en "Editar"
        self.setMenuWidget(enc)
        self._marcar_tema()

    def _conectar(self) -> None:
        self.visor.pagina_actual_cambio.connect(self._pagina_cambio)
        self.visor.zoom_cambio.connect(lambda z: self.etiqueta_zoom.setText(f"{z * 100:.0f}%"))
        self.visor.rect_seleccionado.connect(self._rect_en)
        self.panel_formulario.aplicar.connect(self.completar_formulario)
        self.panel_formulario.aplanar.connect(self.aplanar_formulario)
        self.panel_formulario.ir_a.connect(self._ir_a_campo)
        self.visor.punto_clic.connect(self._nota_en)
        self.miniaturas.pagina_elegida.connect(self.visor.ir_a_pagina)
        self.miniaturas.paginas_movidas.connect(self.mover_paginas)
        separador = QAction(self.miniaturas)
        separador.setSeparator(True)
        separador2 = QAction(self.miniaturas)
        separador2.setSeparator(True)
        for a in (self.acc_rotar_izq, self.acc_rotar_der, self.acc_subir_pagina, self.acc_bajar_pagina,
                  separador, self.acc_insertar_blanco,
                  self.acc_insertar_pdf, self.acc_extraer, separador2, self.acc_eliminar_paginas):
            self.miniaturas.addAction(a)
        suprimir = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.miniaturas)
        suprimir.setContext(Qt.ShortcutContext.WidgetShortcut)
        suprimir.activated.connect(self.eliminar_paginas)
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

    def _dialogo_ocr(self, sin_texto: list[int]) -> tuple[ModoOCR, list[int]] | None:
        dialogo = DialogoOCR(self.documento.doc.page_count, self.visor.pagina_actual, sin_texto, self)
        if dialogo.exec():
            return dialogo.modo, dialogo.paginas
        return None

    def _dialogo_insertar_pdf(self) -> tuple[int, list[FuentePDF]] | None:
        d = DialogoInsertarPDF(self.documento.doc.page_count, self.visor.pagina_actual, self)
        return (d.posicion, d.fuentes) if d.exec() else None

    def _dialogo_extraer(self) -> list[int] | None:
        d = DialogoExtraer(self.documento.doc.page_count, self.visor.pagina_actual,
                           self.miniaturas.paginas_seleccionadas(), self)
        return d.paginas if d.exec() else None

    def _dialogo_separar(self) -> list[list[int]] | None:
        d = DialogoSeparar(self.documento.doc.page_count, self)
        return d.grupos if d.exec() else None

    def _pedir_carpeta(self, titulo: str) -> str | None:
        ruta = QFileDialog.getExistingDirectory(self, titulo, str(self.documento.ruta_original.parent))
        return ruta or None

    def _dialogo_marca_agua(self):
        d = DialogoMarcaAgua(self.documento.doc, self.visor.pagina_actual, self)
        return d.operacion if d.exec() else None

    def _dialogo_numeracion(self):
        d = DialogoNumeracion(self.documento.doc, self.visor.pagina_actual, self)
        return d.operacion if d.exec() else None

    def _dialogo_comprimir(self, tamano: int, hay_ghostscript: bool) -> NivelCompresion | None:
        d = DialogoComprimir(tamano, hay_ghostscript, self)
        return d.nivel if d.exec() else None

    def _dialogo_resultado_compresion(self, resultado: ResultadoCompresion) -> bool:
        return bool(DialogoResultadoCompresion(resultado, self).exec())

    def _dialogo_buscar_redactar(self) -> list[Coincidencia] | None:
        d = DialogoBuscarRedactar(self.documento.doc, self)
        return d.elegidas if d.exec() else None

    def _dialogo_aplicar_redaccion(self, redaccion: Redaccion) -> tuple[OpcionesRedaccion, bool] | None:
        d = DialogoAplicarRedaccion(redaccion, self)
        return (d.opciones, d.borrar_metadatos) if d.exec() else None

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
        self._actualizar_formulario()
        if tiene_formulario(documento.doc):
            n = len(self.panel_formulario.campos)
            self.mostrar_aviso(f"Este PDF tiene un formulario con {n} campo{'s' if n != 1 else ''}. Podés "
                               "completarlo desde la barra lateral.", "Completar…",
                               lambda: self.mostrar_lateral("formulario"), "form-input")
        if parece_escaneado(documento.doc):
            self.mostrar_aviso("Este PDF parece escaneado: sus páginas son imágenes sin texto. Con el OCR "
                               "podés buscar, seleccionar y copiar el texto, sin que cambie su aspecto.",
                               "Reconocer texto…", self.reconocer_texto, "scan-text")
        return True

    # ------------------------------------------------------------------ aviso arriba del visor
    def mostrar_aviso(self, texto: str, boton: str, accion, icono: str = "info") -> None:
        self._accion_del_aviso = accion
        self.aviso.mostrar(texto, boton, icono)

    def _accion_aviso(self) -> None:
        if self._accion_del_aviso is not None:
            self._accion_del_aviso()

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
        self.cancelar_ocr()
        self.cancelar_tarea()
        self.aviso.hide()
        self.redaccion_pendiente = Redaccion()
        self.visor.mostrar_redaccion([])
        self.panel_formulario.set_campos([])
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
            self.analizador.cancelar()
            self.proceso_ocr.cancelar()
            self.proceso_tarea.cancelar()
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
            self.documento.ejecutar(RotarPagina(self.paginas_objetivo(), grados))

    def set_modo(self, modo: Modo) -> None:
        self.visor.set_modo(modo)
        {Modo.MANO: self.acc_modo_mano, Modo.RESALTAR: self.acc_modo_resaltar,
         Modo.NOTA: self.acc_modo_nota, Modo.TEXTO: self.acc_modo_texto,
         Modo.REDACTAR: self.acc_modo_redactar}[modo].setChecked(True)
        if modo != Modo.TEXTO:
            self.bloque = None

    def _rect_en(self, pagina: int, rect) -> None:
        if self.visor.modo == Modo.REDACTAR:
            self.marcar_para_redactar(pagina, rect)
        else:
            self._resaltar_en(pagina, rect)

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

    # ------------------------------------------------------------------ páginas
    def paginas_objetivo(self) -> list[int]:
        """Las miniaturas seleccionadas o, si no hay, la página actual."""
        seleccion = [p for p in self.miniaturas.paginas_seleccionadas() if p < self.documento.doc.page_count]
        return seleccion or [self.visor.pagina_actual]

    def _ejecutar(self, titulo: str, operacion) -> bool:
        """Ejecuta una operación avisando con un mensaje claro si no se puede."""
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                self.documento.ejecutar(operacion)
            finally:
                QApplication.restoreOverrideCursor()
        except (ErrorDocumento, ValueError) as e:
            self._informar(titulo, str(e), error=True)
            return False
        self.statusBar().showMessage(operacion.descripcion() + ": listo (Ctrl+Z para deshacer).", 6000)
        return True

    def eliminar_paginas(self) -> bool:
        if self.documento is None:
            return False
        try:
            op = EliminarPaginas(self.paginas_objetivo(), self.documento.doc.page_count)
        except (ErrorDocumento, ValueError) as e:
            self._informar("Eliminar páginas", str(e), error=True)
            return False
        return self._ejecutar("Eliminar páginas", op)

    def insertar_en_blanco(self) -> bool:
        if self.documento is None:
            return False
        actual = self.visor.pagina_actual
        pag = self.documento.doc[actual]
        if self._ejecutar("Insertar página", InsertarPaginasEnBlanco(actual + 1, 1, (pag.rect.width, pag.rect.height))):
            self.visor.ir_a_pagina(actual + 1)
            return True
        return False

    def insertar_desde_pdf(self) -> bool:
        if self.documento is None:
            return False
        eleccion = self._dialogo_insertar_pdf()
        if eleccion is None:
            return False
        posicion, fuentes = eleccion
        try:
            op = InsertarDesdePDF(posicion, fuentes)
        except (ErrorDocumento, ValueError) as e:
            self._informar("Insertar páginas", str(e), error=True)
            return False
        if self._ejecutar("Insertar páginas", op):
            self.visor.ir_a_pagina(posicion)
            return True
        return False

    def mover_paginas(self, paginas: list[int], destino: int) -> bool:
        if self.documento is None:
            return False
        total = self.documento.doc.page_count
        orden = mover_paginas(total, paginas, destino)
        if orden == list(range(total)):
            return False
        if not self._ejecutar("Reordenar páginas", ReordenarPaginas(orden)):
            return False
        nuevas = [orden.index(p) for p in sorted(paginas)]
        self.visor.ir_a_pagina(nuevas[0])
        return True

    def desplazar_paginas(self, delta: int) -> bool:
        """Mueve las páginas elegidas un lugar antes (-1) o después (+1): la alternativa con
        teclado a arrastrar las miniaturas."""
        if self.documento is None:
            return False
        paginas = self.paginas_objetivo()
        total = self.documento.doc.page_count
        destino = paginas[0] - 1 if delta < 0 else paginas[-1] + 2
        if destino < 0 or destino > total:
            return False
        orden = mover_paginas(total, paginas, destino)
        if not self.mover_paginas(paginas, destino):
            return False
        nuevas = [orden.index(p) for p in paginas]
        self.miniaturas.clearSelection()
        for p in nuevas:
            self.miniaturas.item(p).setSelected(True)
        return True

    def _aviso_contrasena(self) -> str:
        return ("\n\nOjo: el documento tiene contraseña, pero los archivos nuevos quedan sin protección."
                if self.documento.tiene_contrasena else "")

    def extraer(self) -> bool:
        if self.documento is None:
            return False
        paginas = self._dialogo_extraer()
        if not paginas:
            return False
        sugerida = self.documento.ruta_original.with_name(
            f"{self.documento.ruta_original.stem}_paginas.pdf")
        ruta = self._pedir_ruta_guardar(sugerida)
        if not ruta:
            return False
        destino = Path(ruta).with_suffix(".pdf")
        try:
            resultado = extraer_paginas(self.documento.doc, [paginas], [destino], sobrescribir=True,
                                        proteger=[self.documento.ruta_original])[0]
        except ErrorDocumento as e:
            self._informar("Extraer páginas", str(e), error=True)
            return False
        except OSError as e:
            self._informar("Extraer páginas", f"No se pudo escribir {destino}: {e}", error=True)
            return False
        n = len(paginas)
        verificado = ("Verificado: se ven igual que en el documento." if resultado.identico else
                      "¡Atención! Alguna página extraída no se ve igual que en el documento.")
        self._informar("Extraer páginas", f"Se guardó «{destino.name}» con {n} página{'s' if n != 1 else ''}.\n"
                                          f"{verificado}{self._aviso_contrasena()}", error=not resultado.identico)
        return True

    def separar(self) -> bool:
        if self.documento is None:
            return False
        grupos = self._dialogo_separar()
        if not grupos:
            return False
        carpeta = self._pedir_carpeta("Carpeta para los archivos separados")
        if not carpeta:
            return False
        destinos = nombres_libres(Path(carpeta), self.documento.ruta_original.stem, grupos)
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                resultados = extraer_paginas(self.documento.doc, grupos, destinos,
                                             proteger=[self.documento.ruta_original])
            finally:
                QApplication.restoreOverrideCursor()
        except ErrorDocumento as e:
            self._informar("Separar en archivos", str(e), error=True)
            return False
        except OSError as e:
            self._informar("Separar en archivos", f"No se pudo escribir en {carpeta}: {e}", error=True)
            return False
        mal = [r.ruta.name for r in resultados if not r.identico]
        texto = f"Se crearon {len(resultados)} archivos en {carpeta}."
        texto += ("\nVerificado: todas las páginas se ven igual que en el documento." if not mal else
                  "\n¡Atención! Estos archivos tienen páginas que no se ven igual: " + ", ".join(mal))
        self._informar("Separar en archivos", texto + self._aviso_contrasena(), error=bool(mal))
        return True

    def marca_de_agua(self) -> bool:
        if self.documento is None:
            return False
        op = self._dialogo_marca_agua()
        return op is not None and self._ejecutar("Marca de agua", op)

    def numerar_paginas(self) -> bool:
        if self.documento is None:
            return False
        op = self._dialogo_numeracion()
        return op is not None and self._ejecutar("Numerar páginas", op)

    # ------------------------------------------------------------------ redacción
    def _mostrar_redaccion_pendiente(self) -> None:
        r = self.redaccion_pendiente
        zonas = [(p, pymupdf.Rect(z)) for p, lista in r.zonas.items() for z in lista]
        zonas += [(p, pymupdf.Rect(z)) for p, lista in r.cajas_extra.items() for z in lista]
        self.visor.mostrar_redaccion(zonas)
        if r.vacia:
            self.aviso.hide()
            return
        n = len(zonas)
        self.mostrar_aviso(f"{n} elemento{'s' if n != 1 else ''} marcado{'s' if n != 1 else ''} para redactar "
                           "(todavía no se borró nada).", "Aplicar redacción…", self.aplicar_redaccion, "ban")
        self._actualizar_estado()

    def marcar_para_redactar(self, pagina: int, rect) -> None:
        if self.documento is None:
            return
        rect = pymupdf.Rect(rect)
        if rect.width < 2 or rect.height < 2:
            return
        self.redaccion_pendiente.agregar_zona(pagina, rect)
        self._mostrar_redaccion_pendiente()

    def buscar_para_redactar(self) -> bool:
        if self.documento is None:
            return False
        elegidas = self._dialogo_buscar_redactar()
        if not elegidas:
            return False
        for c in elegidas:
            self.redaccion_pendiente.agregar(c)
        self._mostrar_redaccion_pendiente()
        primera = min(elegidas, key=lambda c: c.pagina)
        self.visor.ir_a_pagina(primera.pagina)
        return True

    def descartar_redaccion(self) -> None:
        self.redaccion_pendiente = Redaccion()
        self._mostrar_redaccion_pendiente()

    def aplicar_redaccion(self) -> bool:
        if self.documento is None:
            return False
        if self.redaccion_pendiente.vacia:
            self._informar("Aplicar redacción", "No hay nada marcado. Usá «Marcar para redactar» (arrastrá sobre "
                                                "la página) o «Buscar datos para redactar».")
            return False
        eleccion = self._dialogo_aplicar_redaccion(self.redaccion_pendiente)
        if eleccion is None:
            return False
        opciones, metadatos = eleccion
        redaccion = self.redaccion_pendiente
        self.redaccion_pendiente = Redaccion()       # antes de ejecutar: el cambio del documento no avisa
        if not self._ejecutar("Aplicar redacción", Redactar(redaccion, opciones)):
            self.redaccion_pendiente = redaccion
            self._mostrar_redaccion_pendiente()
            return False
        if metadatos:
            self._ejecutar("Borrar metadatos", LimpiarMetadatos())
        self.set_modo(Modo.MANO)
        self._mostrar_redaccion_pendiente()
        self.statusBar().showMessage("Redacción aplicada. Al guardar, el archivo se reescribe completo y se "
                                     "verifica que lo redactado no se pueda extraer.", 10000)
        return True

    def borrar_metadatos(self) -> bool:
        if self.documento is None:
            return False
        return self._ejecutar("Borrar metadatos", LimpiarMetadatos())

    # ------------------------------------------------------------------ formularios
    def _actualizar_formulario(self) -> None:
        if self.documento is None:
            self.panel_formulario.set_campos([])
            return
        try:
            lista = campos(self.documento.doc)
        except Exception:
            registro().exception("No se pudieron leer los campos del formulario")
            lista = []
        self.panel_formulario.set_campos(lista)
        self.lateral.set_insignia("formulario", len(lista))
        self.acc_aplanar.setEnabled(bool(lista))

    def completar_formulario(self, valores: dict) -> bool:
        if self.documento is None:
            return False
        try:
            op = CompletarFormulario(valores)
        except ValueError as e:
            self._informar("Completar formulario", str(e), error=True)
            return False
        return self._ejecutar("Completar formulario", op)

    def aplanar_formulario(self) -> bool:
        if self.documento is None:
            return False
        if not tiene_formulario(self.documento.doc):
            self._informar("Aplanar formulario", "Este documento no tiene campos de formulario.")
            return False
        if self.panel_formulario.cambios() and self._preguntar(
                "Aplanar formulario", "Hay cambios en el formulario sin aplicar. ¿Aplicarlos antes de aplanar?"):
            self.completar_formulario(self.panel_formulario.cambios())
        if not self._preguntar("Aplanar formulario", "Los campos pasan a ser contenido fijo de la página: se ven "
                                                      "igual, pero ya no se pueden completar. Se puede deshacer "
                                                      "mientras el documento esté abierto. ¿Aplanar?"):
            return False
        return self._ejecutar("Aplanar formulario", AplanarFormulario())

    def _ir_a_campo(self, pagina: int, rect: tuple) -> None:
        if self.documento is None or pagina >= self.documento.doc.page_count:
            return
        self.visor.ir_a_pagina(pagina)
        self.visor.mostrar_resultados([(pagina, pymupdf.Rect(rect))], 0)

    # ------------------------------------------------------------------ tareas en otro proceso
    def _mostrar_progreso(self, titulo: str, texto: str, cancelar) -> QProgressDialog:
        progreso = QProgressDialog(texto, "Cancelar", 0, 0, self)
        progreso.setWindowTitle(titulo)
        progreso.setWindowModality(Qt.WindowModality.WindowModal)
        progreso.setMinimumDuration(0)
        progreso.canceled.connect(cancelar)
        progreso.show()
        return progreso

    def iniciar_tarea(self, tarea: str, pedido: dict, titulo: str, texto: str, al_terminar) -> bool:
        """Corre una tarea de pdf_studio.procesos sin trabar la interfaz.
        al_terminar(ok, resultado o mensaje de error)."""
        if self.documento is None or self.proceso_tarea.ocupado:
            return False
        carpeta = self.documento.carpeta_trabajo
        archivo_pedido, archivo_resultado = carpeta / f"{tarea}_pedido.json", carpeta / f"{tarea}_resultado.json"
        archivo_resultado.unlink(missing_ok=True)
        archivo_pedido.write_text(json.dumps(pedido), encoding="utf-8")
        programa, argumentos = procesos.comando(tarea, archivo_pedido, archivo_resultado)
        self._tarea = (tarea, archivo_resultado, al_terminar, self.documento)
        self.proceso_tarea.iniciar(programa, argumentos, procesos.entorno())
        self._progreso_tarea = self._mostrar_progreso(titulo, texto, self.cancelar_tarea)
        self._estado_analisis()
        return True

    def cancelar_tarea(self) -> None:
        if self.proceso_tarea.cancelar():
            self.statusBar().showMessage("Se canceló la tarea.", 5000)
        self._tarea = None
        self._cerrar_progreso_tarea()

    def _cerrar_progreso_tarea(self) -> None:
        progreso, self._progreso_tarea = self._progreso_tarea, None
        if progreso is not None:
            progreso.blockSignals(True)
            progreso.close()
            progreso.deleteLater()
        self._estado_analisis()

    def _tarea_terminada(self, codigo: int, error: str) -> None:
        self._cerrar_progreso_tarea()
        pendiente, self._tarea = self._tarea, None
        if pendiente is None:
            return
        tarea, archivo_resultado, al_terminar, documento = pendiente
        if documento is not self.documento:
            return
        try:
            datos = json.loads(archivo_resultado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            registro().error("La tarea %s terminó sin resultado (código %s): %s", tarea, codigo, error[-2000:])
            al_terminar(False, error if codigo < 0 else f"La tarea terminó sin resultado (código {codigo}).")
            return
        if not datos.get("ok"):
            if datos.get("detalle"):
                registro().error("Falló la tarea %s: %s", tarea, datos["detalle"])
            al_terminar(False, datos.get("error", "Error desconocido."))
            return
        al_terminar(True, datos["resultado"])

    def _preparar_estado_actual(self) -> Path | None:
        """Archivo con el estado actual (original + ediciones), verificado como al guardar."""
        if not self.documento.operaciones_activas:
            return self.documento.ruta_base
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                guardado = self.documento.preparar_guardado()
            finally:
                QApplication.restoreOverrideCursor()
        except ErrorDocumento as e:
            self._informar("Preparar el documento", str(e), error=True)
            return None
        if not guardado.es_fiel and not self._preguntar_guardar_igual(guardado):
            return None
        return guardado.ruta_temporal

    # ------------------------------------------------------------------ compresión
    def comprimir(self) -> bool:
        if self.documento is None:
            return False
        from pdf_studio.dependencias.chequeo import buscar_ghostscript
        entrada = self._preparar_estado_actual()
        if entrada is None:
            return False
        nivel = self._dialogo_comprimir(entrada.stat().st_size, buscar_ghostscript() is not None)
        if nivel is None:
            return False
        salida = self.documento.carpeta_trabajo / f"comprimido_{nivel.value}.pdf"
        salida.unlink(missing_ok=True)
        pedido = {"entrada": str(entrada), "salida": str(salida), "nivel": nivel.value,
                  "contrasena": self.documento.contrasena}
        return self.iniciar_tarea("comprimir", pedido, "Comprimir", f"Comprimiendo ({nivel.texto.lower()}) y "
                                  "verificando el resultado…", lambda ok, r: self._compresion_lista(ok, r, salida))

    def _compresion_lista(self, ok: bool, resultado, salida: Path) -> None:
        if not ok:
            self._informar("Comprimir", resultado, error=True)
            return
        resultado = ResultadoCompresion.desde_dict(resultado)
        if not self._dialogo_resultado_compresion(resultado):
            self.statusBar().showMessage("No se guardó la copia comprimida.", 5000)
            return
        original = self.documento.ruta_original
        ruta = self._pedir_ruta_guardar(original.with_name(f"{original.stem}_comprimido.pdf"))
        if not ruta:
            return
        destino = Path(ruta).with_suffix(".pdf")
        if destino.resolve() == original:
            self._informar("Comprimir", "Elegiste el archivo original: para no perderlo, la copia comprimida "
                                        "se guarda con otro nombre.", error=True)
            return
        try:
            temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
            shutil.copy2(salida, temporal)
            temporal.replace(destino)
        except OSError as e:
            self._informar("Comprimir", f"No se pudo escribir {destino}: {e}", error=True)
            return
        self.statusBar().showMessage(f"Copia comprimida guardada: {destino}", 8000)

    # ------------------------------------------------------------------ OCR
    def reconocer_texto(self) -> bool:
        """Pide modo y páginas, y corre ocrmypdf en otro proceso (la interfaz sigue respondiendo)."""
        if self.documento is None or self.proceso_ocr.ocupado:
            return False
        eleccion = self._dialogo_ocr(paginas_sin_texto(self.documento.doc))
        if eleccion is None:
            return False
        modo, paginas = eleccion
        try:
            entorno = entorno_ocr()
        except ErrorOCR as e:
            self._informar("Reconocer texto", str(e), error=True)
            self.chequear_dependencias(mostrar=True)
            return False
        carpeta = self.documento.carpeta_trabajo
        entrada, salida = carpeta / "ocr_entrada.pdf", carpeta / "ocr_salida.pdf"
        try:
            salida.unlink(missing_ok=True)
            # El estado actual (con las ediciones hechas) y sin cifrar: ocrmypdf no lee PDFs protegidos
            self.documento.doc.save(entrada, garbage=0, encryption=pymupdf.PDF_ENCRYPT_NONE)
        except Exception as e:
            self._informar("Reconocer texto", f"No se pudo preparar el OCR ({type(e).__name__}).", error=True)
            return False
        programa, argumentos = comando_ocrmypdf(entrada, salida, paginas)
        self._ocr = (modo, paginas, salida, self.documento)
        self.proceso_ocr.iniciar(programa, argumentos, entorno)
        n = len(paginas)
        progreso = QProgressDialog(f"Reconociendo texto en {n} página{'s' if n != 1 else ''}…", "Cancelar",
                                   0, 0, self)
        progreso.setWindowTitle("Reconocer texto (OCR)")
        progreso.setWindowModality(Qt.WindowModality.WindowModal)
        progreso.setMinimumDuration(0)
        progreso.canceled.connect(self.cancelar_ocr)
        progreso.show()
        self._progreso_ocr = progreso
        self._estado_analisis()
        return True

    def cancelar_ocr(self) -> None:
        if self.proceso_ocr.cancelar():
            self.statusBar().showMessage("Se canceló el OCR.", 5000)
        self._ocr = None
        self._cerrar_progreso_ocr()

    def _cerrar_progreso_ocr(self) -> None:
        progreso, self._progreso_ocr = self._progreso_ocr, None
        if progreso is not None:
            progreso.blockSignals(True)
            progreso.close()
            progreso.deleteLater()
        self._estado_analisis()

    def _ocr_terminado(self, codigo: int, error: str) -> None:
        self._cerrar_progreso_ocr()
        pendiente, self._ocr = self._ocr, None
        if pendiente is None:
            return
        modo, paginas, salida, documento = pendiente
        if documento is not self.documento:
            return
        if codigo != 0:
            mensaje = error if codigo < 0 else mensaje_de_error_ocrmypdf(codigo, error)
            registro().error("El OCR falló (código %s): %s", codigo, mensaje)
            self._informar("Reconocer texto", mensaje, error=True)
            return
        try:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                operacion = crear_operacion(modo, capas_de_texto(salida, paginas))
                self.documento.ejecutar(operacion)
            finally:
                QApplication.restoreOverrideCursor()
        except ErrorDocumento as e:
            self._informar("Reconocer texto", str(e), error=True)
            return
        except Exception as e:
            registro().exception("No se pudo leer el resultado del OCR")
            self._informar("Reconocer texto", f"No se pudo leer el resultado del OCR ({type(e).__name__}).",
                           error=True)
            return
        self.aviso.hide()
        n = len(operacion.paginas_afectadas())
        if modo == ModoOCR.IDENTICO:
            texto = (f"Texto reconocido en {n} página{'s' if n != 1 else ''}: ya se puede buscar y copiar. "
                     "El aspecto de las páginas no cambió.")
        else:
            texto = f"Texto reconstruido en {n} página{'s' if n != 1 else ''} (experimental): revisalo."
        self.statusBar().showMessage(texto, 10000)

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
        if not self.redaccion_pendiente.vacia:
            # Las zonas marcadas se refieren al documento anterior: no se pueden aplicar a otro
            self.redaccion_pendiente = Redaccion()
            self.visor.mostrar_redaccion([])
            self.aviso.hide()
            self.statusBar().showMessage("El documento cambió: se descartaron las marcas de redacción "
                                         "pendientes. Volvé a marcarlas.", 8000)
        self._actualizar_formulario()
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
        texto = "Analizando " + " y ".join(partes) + "…" if partes else ""
        if self.proceso_ocr.ocupado:
            texto = (texto + " · " if texto else "") + "Reconociendo texto…"
        if self.proceso_tarea.ocupado:
            texto = (texto + " · " if texto else "") + "Procesando…"
        self.etiqueta_analisis.setText(texto)

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
                  self.acc_insertar_imagen, self.acc_modo_texto, self.acc_editar_seleccion, self.acc_sustituir,
                  self.acc_ocr, self.acc_eliminar_paginas, self.acc_insertar_blanco, self.acc_insertar_pdf,
                  self.acc_extraer, self.acc_separar, self.acc_marca_agua, self.acc_numerar, self.acc_comprimir,
                  self.acc_subir_pagina, self.acc_bajar_pagina, self.acc_modo_redactar, self.acc_buscar_redactar,
                  self.acc_formulario, self.acc_borrar_metadatos):
            a.setEnabled(hay)
        pendiente = hay and not self.redaccion_pendiente.vacia
        self.acc_aplicar_redaccion.setEnabled(pendiente)
        self.acc_descartar_redaccion.setEnabled(pendiente)
        self.acc_aplanar.setEnabled(hay and bool(self.panel_formulario.campos))
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
