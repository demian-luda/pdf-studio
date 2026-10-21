"""«Convertir escaneado a documento editable»: elegir el origen y revisar el resultado.

La revisión muestra, lado a lado, el escaneo original (con las palabras dudosas marcadas)
y el documento reconstruido. Las palabras con baja confianza se recorren con Enter: se
corrigen en el campo de abajo (o se confirman tal cual) y se pasa a la siguiente.
"""
from __future__ import annotations

import html

from PySide6.QtCore import QPointF, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea,
                               QSplitter, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from pdf_studio.core.maquetado import tramos
from pdf_studio.core.reconstruccion import Alineacion, Bloque, Fuente, Palabra, Reconstruccion, Tipo

ZOOMS = (("Ajustar al ancho", 1.0), ("150 %", 1.5), ("200 %", 2.0), ("300 %", 3.0))
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import botonera, tarjeta_informativa
from pdf_studio.ui.tema import gestor


class Origen:
    ABIERTO = "abierto"
    PDF = "pdf"
    FOTOS = "fotos"


class DialogoOrigenConversion(QDialog):
    """De dónde sale el escaneo: el PDF abierto, otro PDF o fotos."""

    def __init__(self, nombre_abierto: str | None, paginas_abierto: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Convertir escaneado a documento editable")
        self.grupo = QButtonGroup(self)
        capa = QVBoxLayout(self)
        capa.addWidget(QLabel("<b>¿Qué querés convertir?</b>", self))
        opciones = []
        if nombre_abierto:
            n = paginas_abierto
            opciones.append((Origen.ABIERTO, f"El PDF abierto: «{nombre_abierto}» ({n} página{'s' if n != 1 else ''})"))
        opciones += [(Origen.PDF, "Otro PDF escaneado…"),
                     (Origen.FOTOS, "Fotos de documentos (con recorte y corrección de perspectiva)…")]
        self._valores = {}
        for i, (valor, texto) in enumerate(opciones):
            radio = QRadioButton(texto, self)
            self.grupo.addButton(radio, i)
            self._valores[i] = valor
            capa.addWidget(radio)
        self.grupo.button(0).setChecked(True)
        capa.addWidget(tarjeta_informativa(
            "Se reconoce el texto (español e inglés) y se rearma la estructura: títulos, párrafos, listas, tablas, "
            "encabezado y pie. Los logos, firmas y sellos se recortan del escaneo como imágenes. Antes de exportar "
            "vas a poder revisar las palabras dudosas. El archivo original no se modifica.", self))
        self.botones = botonera(self, "Siguiente", "scan-text")
        capa.addWidget(self.botones)

    @property
    def origen(self) -> str:
        return self._valores[self.grupo.checkedId()]


# ---------------------------------------------------------------------- vista del escaneo
class LienzoEscaneo(QWidget):
    """La página escaneada con las palabras dudosas marcadas. Un clic sobre una palabra la elige."""

    elegida = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAccessibleName("Escaneo original")
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self._imagen: QPixmap | None = None
        self._escala = 1.0              # px de la imagen por pt de la página
        self._zoom = 1.0                # px de pantalla por px de la imagen
        self.aumento = 1.0              # sobre «ajustar al ancho»
        self._palabras: list[Palabra] = []
        self.actual: str | None = None

    def mostrar(self, ruta_imagen: str, ancho_pt: float, palabras: list[Palabra]) -> None:
        self._imagen = QPixmap(ruta_imagen) if ruta_imagen else None
        if self._imagen is not None and self._imagen.isNull():
            self._imagen = None
        self._escala = (self._imagen.width() / ancho_pt) if self._imagen is not None and ancho_pt else 1.0
        self._palabras = palabras
        self.ajustar_ancho(self.parentWidget().width() if self.parentWidget() else 600)

    def ajustar_ancho(self, ancho: int) -> None:
        if self._imagen is None:
            self.setFixedSize(200, 200)
            return
        self._zoom = max(0.2, (ancho - 4) / self._imagen.width()) * self.aumento
        self.setFixedSize(int(self._imagen.width() * self._zoom), int(self._imagen.height() * self._zoom))
        self.update()

    def rect_de(self, p: Palabra) -> QRectF:
        f = self._escala * self._zoom
        x0, y0, x1, y1 = p.caja
        return QRectF(x0 * f - 2, y0 * f - 2, (x1 - x0) * f + 4, (y1 - y0) * f + 4)

    def recorte(self, p: Palabra) -> QPixmap | None:
        """La palabra tal como está en el escaneo (con un poco de contexto)."""
        if self._imagen is None:
            return None
        f = self._escala
        x0, y0, x1, y1 = p.caja
        m = (y1 - y0) * f * 0.6
        return self._imagen.copy(int(x0 * f - m), int(y0 * f - m), int((x1 - x0) * f + 2 * m),
                                 int((y1 - y0) * f + 2 * m))

    def paintEvent(self, evento) -> None:
        pintor = QPainter(self)
        pintor.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._imagen is not None:
            pintor.drawPixmap(self.rect(), self._imagen)
        paleta = gestor().paleta
        aviso = QColor(paleta.aviso)
        relleno = QColor(aviso)
        relleno.setAlpha(60)
        for p in self._palabras:
            if p.id == self.actual:
                continue
            if p.dudosa:
                pintor.setPen(QPen(aviso, 1.5))
                pintor.setBrush(relleno)
                pintor.drawRect(self.rect_de(p))
        actual = next((p for p in self._palabras if p.id == self.actual), None)
        if actual is not None:
            acento = QColor(paleta.acento)
            pintor.setPen(QPen(acento, 2.5))
            pintor.setBrush(Qt.BrushStyle.NoBrush)
            pintor.drawRect(self.rect_de(actual).adjusted(-2, -2, 2, 2))
        pintor.end()

    def mousePressEvent(self, evento) -> None:
        punto = QPointF(evento.position())
        for p in self._palabras:
            if self.rect_de(p).contains(punto):
                self.elegida.emit(p.id)
                return
        super().mousePressEvent(evento)


# ---------------------------------------------------------------------- vista del resultado
def _estilo_alineacion(b: Bloque) -> str:
    return {Alineacion.CENTRO: "center", Alineacion.DERECHA: "right",
            Alineacion.JUSTIFICADO: "justify"}.get(b.alineacion, "left")


def html_de_pagina(pagina, rec: Reconstruccion, actual: str | None) -> str:
    """La página reconstruida como HTML (solo para mirar): cada palabra es un enlace que la
    elige para editarla; las dudosas van resaltadas."""
    p = gestor().paleta
    familia = html.escape(rec.fuente.familia if rec.fuente else "Arial")
    partes = [f'<html><body style="font-family:\'{familia}\'; color:{p.texto}; background:{p.superficie};">']

    def palabra(w: Palabra, negrita: bool) -> str:
        estilo = f"color:{p.texto}; text-decoration:none;"
        if w.id == actual:
            estilo += f" background:{p.acento}; color:{p.sobre_acento};"
        elif w.dudosa:
            estilo += f" background:{p.acento_suave}; border-bottom:2px solid {p.aviso};"
            estilo += f" text-decoration:underline; text-decoration-color:{p.aviso};"
        texto = html.escape(w.texto)
        if negrita:
            texto = f"<b>{texto}</b>"
        return f'<a href="w:{w.id}" style="{estilo}">{texto}</a>'

    def texto(b: Bloque) -> str:
        salida = []
        for contenido, negrita, _color, palabras in tramos(b):
            salida.append(" ".join(palabra(w, negrita) for w in palabras))
        return " ".join(salida)

    def bloque(b: Bloque) -> str:
        tamano = b.tamano or rec.tamano_cuerpo
        if b.tipo == Tipo.IMAGEN:
            x0, y0, x1, y1 = b.caja
            src = QUrl.fromLocalFile(b.archivo).toString()
            return (f'<p style="text-align:{_estilo_alineacion(b)}; margin:4px 0;">'
                    f'<img src="{src}" width="{int((x1 - x0) * 1.3)}"></p>')
        if b.tipo == Tipo.TABLA:
            filas: dict[int, list] = {}
            for c in sorted(b.celdas, key=lambda c: (c.fila, c.columna)):
                contenido = "".join(bloque(x) for x in c.bloques) or "&nbsp;"
                filas.setdefault(c.fila, []).append(
                    f'<td rowspan="{c.filas}" colspan="{c.columnas}" style="border:1px solid {p.borde_control}; '
                    f'padding:3px;">{contenido}</td>')
            cuerpo = "".join(f"<tr>{''.join(celdas)}</tr>" for _, celdas in sorted(filas.items()))
            return (f'<table cellspacing="0" cellpadding="3" style="border-collapse:collapse; margin:6px 0;" '
                    f'border="1">{cuerpo}</table>')
        estilo = (f"text-align:{_estilo_alineacion(b)}; font-size:{tamano}pt; margin:"
                  f"{max(0, b.espacio_antes) * 0.8:.0f}px 0 0 {b.sangria:.0f}px;")
        if b.tipo == Tipo.TITULO:
            return f'<p style="{estilo} font-weight:bold;">{texto(b)}</p>'
        if b.tipo == Tipo.ITEM:
            marcador = "•" if not b.numerado else html.escape(b.marcador)
            return f'<p style="{estilo}">{marcador}&nbsp;&nbsp;{texto(b)}</p>'
        return f'<p style="{estilo}">{texto(b)}</p>'

    for titulo, bloques in (("Encabezado", pagina.encabezado), (None, pagina.bloques), ("Pie", pagina.pie)):
        if not bloques:
            continue
        if titulo:
            partes.append(f'<p style="color:{p.texto_suave}; font-size:8pt; margin-top:8px;">{titulo} '
                          f'(se repite en todas las páginas)</p>')
        partes += [bloque(b) for b in bloques]
    partes.append("</body></html>")
    return "".join(partes)


# ---------------------------------------------------------------------- revisión
class DialogoRevision(QDialog):
    def __init__(self, rec: Reconstruccion, parent: QWidget | None = None):
        super().__init__(parent)
        self.rec = rec
        self.setWindowTitle("Revisar la conversión")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1280, 860)
        self.pagina = 0
        self.dudosas = [p.id for p in rec.dudosas()]
        self.indice = -1
        self.actual: str | None = None

        # Navegación por páginas
        self.anterior_pag = self._boton_icono("Página anterior", "arrow-up", lambda: self.ir_a_pagina(self.pagina - 1))
        self.siguiente_pag = self._boton_icono("Página siguiente", "arrow-down",
                                               lambda: self.ir_a_pagina(self.pagina + 1))
        self.etiqueta_pagina = QLabel(self)
        self.etiqueta_dudosas = QLabel(self)
        self.etiqueta_dudosas.setObjectName("texto_suave")
        self.zoom = QComboBox(self)
        self.zoom.setAccessibleName("Zoom del escaneo")
        self.zoom.setToolTip("Zoom del escaneo")
        for texto, valor in ZOOMS:
            self.zoom.addItem(texto, valor)
        self.zoom.currentIndexChanged.connect(self._cambiar_zoom)
        barra = QHBoxLayout()
        barra.addWidget(self.anterior_pag)
        barra.addWidget(self.siguiente_pag)
        barra.addWidget(self.etiqueta_pagina)
        barra.addSpacing(16)
        barra.addWidget(self.etiqueta_dudosas, 1)
        barra.addWidget(QLabel("Escaneo:", self))
        barra.addWidget(self.zoom)

        # Lado a lado
        self.lienzo = LienzoEscaneo()
        self.lienzo.elegida.connect(self.elegir)
        self.desplazable = QScrollArea(self)
        self.desplazable.setWidget(self.lienzo)
        self.desplazable.setWidgetResizable(False)
        self.desplazable.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.resultado = QTextBrowser(self)
        self.resultado.setAccessibleName("Documento reconstruido")
        self.resultado.setOpenLinks(False)
        self.resultado.anchorClicked.connect(self._enlace)
        paneles = QSplitter(Qt.Orientation.Horizontal, self)
        for titulo, vista in (("Escaneo original", self.desplazable), ("Documento reconstruido", self.resultado)):
            caja = QWidget()
            c = QVBoxLayout(caja)
            c.setContentsMargins(0, 0, 0, 0)
            c.addWidget(QLabel(f"<b>{titulo}</b>", caja))
            c.addWidget(vista, 1)
            paneles.addWidget(caja)
        paneles.setChildrenCollapsible(False)
        paneles.setSizes([1, 1])
        paneles.splitterMoved.connect(lambda *_: self._ajustar_lienzo())

        # Edición de la palabra elegida
        self.muestra = QLabel(self)
        self.muestra.setMinimumSize(160, 48)
        self.muestra.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.muestra.setAccessibleName("La palabra en el escaneo")
        self.detalle = QLabel(self)
        self.detalle.setObjectName("texto_suave")
        self.campo = QLineEdit(self)
        self.campo.setAccessibleName("Texto de la palabra")
        self.campo.setPlaceholderText("Elegí una palabra (clic en cualquiera de las dos vistas)")
        self.campo.returnPressed.connect(self.confirmar_y_seguir)
        self.anterior = QPushButton("Anterior", self)
        iconos.asignar(self.anterior, "chevron-up")
        self.anterior.setToolTip("Dudosa anterior (Mayús+Enter)")
        self.anterior.clicked.connect(lambda: self.mover(-1))
        self.siguiente = QPushButton("Siguiente dudosa", self)
        iconos.asignar(self.siguiente, "chevron-down")
        self.siguiente.setToolTip("Confirmar y pasar a la siguiente dudosa (Enter)")
        self.siguiente.clicked.connect(self.confirmar_y_seguir)
        for b in (self.anterior, self.siguiente):
            b.setAutoDefault(False)
        atajo = QShortcut(QKeySequence("Shift+Return"), self.campo)
        atajo.activated.connect(lambda: self.mover(-1, confirmar=True))
        edicion = QGroupBox("Palabra", self)
        capa_edicion = QHBoxLayout(edicion)
        capa_edicion.addWidget(self.muestra)
        columna = QVBoxLayout()
        columna.addWidget(self.detalle)
        columna.addWidget(self.campo)
        capa_edicion.addLayout(columna, 1)
        capa_edicion.addWidget(self.anterior)
        capa_edicion.addWidget(self.siguiente)

        # Opciones de exportación
        self.fuente = QComboBox(self)
        self.fuente.setAccessibleName("Fuente del documento")
        fuentes = ([rec.fuente] if rec.fuente else []) + list(rec.alternativas)
        for i, f in enumerate(fuentes):
            lugar = "instalada en Windows" if f.origen == "sistema" else "incluida con PDF Studio"
            self.fuente.addItem(f"{f.familia}" + ("  (la más parecida)" if i == 0 else "") + f" — {lugar}", f)
        if not fuentes:
            self.fuente.addItem("Arial", None)
        self.con_pdf = QCheckBox("Exportar también un PDF con texto real (misma reconstrucción)", self)
        opciones = QGroupBox("Exportar", self)
        formulario = QFormLayout(opciones)
        formulario.addRow("Fuente:", self.fuente)
        formulario.addRow(self.con_pdf)
        for aviso in rec.avisos:
            formulario.addRow(tarjeta_informativa(aviso, opciones))

        self.botones = botonera(self, "Exportar .docx…", "file-output")
        # Enter en el campo navega: el botón principal no lo toma
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setAutoDefault(False)
        self.botones.button(QDialogButtonBox.StandardButton.Ok).setDefault(False)

        capa = QVBoxLayout(self)
        capa.addLayout(barra)
        capa.addWidget(paneles, 1)
        capa.addWidget(edicion)
        capa.addWidget(opciones)
        capa.addWidget(self.botones)

        self.ir_a_pagina(0)
        if self.dudosas:
            self.mover(1)
        self._actualizar_contador()

    def _boton_icono(self, texto: str, icono: str, accion) -> QToolButton:
        b = QToolButton(self)
        b.setToolTip(texto)
        b.setAccessibleName(texto)
        iconos.asignar(b, icono)
        b.clicked.connect(accion)
        return b

    # ------------------------------------------------------------------ datos
    @property
    def fuente_elegida(self) -> Fuente | None:
        return self.fuente.currentData()

    def _palabras_de(self, numero: int) -> list[Palabra]:
        pag = self.rec.paginas[numero]
        return [p for b in pag.todos() for p in b.palabras]

    # ------------------------------------------------------------------ navegación
    def ir_a_pagina(self, numero: int) -> None:
        if not 0 <= numero < len(self.rec.paginas):
            return
        self.pagina = numero
        pag = self.rec.paginas[numero]
        self.lienzo.actual = self.actual
        self.lienzo.mostrar(pag.imagen, pag.ancho, self._palabras_de(numero))
        self._ajustar_lienzo()
        self._mostrar_resultado()
        total = len(self.rec.paginas)
        self.etiqueta_pagina.setText(f"Página {numero + 1} de {total}")
        self.anterior_pag.setEnabled(numero > 0)
        self.siguiente_pag.setEnabled(numero < total - 1)

    def _ajustar_lienzo(self) -> None:
        self.lienzo.ajustar_ancho(self.desplazable.viewport().width())

    def _cambiar_zoom(self) -> None:
        self.lienzo.aumento = float(self.zoom.currentData())
        self._ajustar_lienzo()
        palabra = self.rec.buscar(self.actual) if self.actual else None
        if palabra is not None:
            rect = self.lienzo.rect_de(palabra)
            self.desplazable.ensureVisible(int(rect.center().x()), int(rect.center().y()), 80, 120)

    def resizeEvent(self, evento) -> None:
        super().resizeEvent(evento)
        self._ajustar_lienzo()

    def _mostrar_resultado(self) -> None:
        barra = self.resultado.verticalScrollBar().value()
        self.resultado.setHtml(html_de_pagina(self.rec.paginas[self.pagina], self.rec, self.actual))
        self.resultado.verticalScrollBar().setValue(barra)

    def _enlace(self, url: QUrl) -> None:
        texto = url.toString()
        if texto.startswith("w:"):
            self.elegir(texto[2:])

    def elegir(self, id_palabra: str) -> None:
        palabra = self.rec.buscar(id_palabra)
        if palabra is None:
            return
        self.actual = id_palabra
        if id_palabra in self.dudosas:
            self.indice = self.dudosas.index(id_palabra)
        numero = self.rec.pagina_de(id_palabra)
        if numero != self.pagina:
            self.ir_a_pagina(numero)
        else:
            self.lienzo.actual = id_palabra
            self.lienzo.update()
            self._mostrar_resultado()
        self.lienzo.actual = id_palabra
        rect = self.lienzo.rect_de(palabra)
        self.desplazable.ensureVisible(int(rect.center().x()), int(rect.center().y()), 50, 120)
        self.resultado.scrollToAnchor(f"w:{id_palabra}")
        recorte = self.lienzo.recorte(palabra)
        if recorte is not None and not recorte.isNull():
            self.muestra.setPixmap(recorte.scaledToHeight(min(64, max(32, recorte.height() * 2)),
                                                          Qt.TransformationMode.SmoothTransformation))
        self.detalle.setText(f"Confianza del reconocimiento: {palabra.conf:.0f} %"
                             + (" · revisada" if palabra.revisada else ""))
        self.campo.setText(palabra.texto)
        self.campo.selectAll()
        self.campo.setFocus()
        self._actualizar_contador()

    def mover(self, paso: int, confirmar: bool = False) -> None:
        if confirmar:
            self.confirmar()
        if not self.dudosas:
            return
        self.indice = (self.indice + paso) % len(self.dudosas)
        self.elegir(self.dudosas[self.indice])

    def confirmar(self) -> None:
        """Aplica el texto del campo a la palabra elegida y la marca como revisada."""
        if self.actual is None:
            return
        palabra = self.rec.buscar(self.actual)
        if palabra is None:
            return
        texto = " ".join(self.campo.text().split())
        if texto:
            palabra.texto = texto
        palabra.revisada = True
        self._mostrar_resultado()
        self.lienzo.update()
        self._actualizar_contador()

    def confirmar_y_seguir(self) -> None:
        self.confirmar()
        pendientes = [i for i in self.dudosas if (p := self.rec.buscar(i)) is not None and p.dudosa]
        if pendientes:
            self.mover(1)
        else:
            self.campo.clearFocus()
            self.botones.button(QDialogButtonBox.StandardButton.Ok).setFocus()

    def _actualizar_contador(self) -> None:
        pendientes = len(self.rec.dudosas())
        total = len(self.dudosas)
        if not total:
            self.etiqueta_dudosas.setText("No hay palabras dudosas. Podés hacer clic en cualquier palabra para "
                                          "corregirla.")
        else:
            posicion = f"Dudosa {self.indice + 1} de {total}" if self.indice >= 0 else f"{total} dudosas"
            self.etiqueta_dudosas.setText(f"{posicion} · quedan {pendientes} sin revisar · Enter: confirmar y "
                                          "seguir")
        self.anterior.setEnabled(total > 0)
        self.siguiente.setEnabled(total > 0)
