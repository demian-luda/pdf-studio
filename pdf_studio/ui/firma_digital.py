"""Diálogos de la firma digital con certificado: firmar, crear un certificado de prueba y
ver el resultado de validar las firmas de un PDF."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QFrame,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from pdf_studio.core.firma_digital import (EstadoFirma, ErrorFirma, InfoCertificado, OpcionesFirma, fecha_legible,
                                           leer_certificado)
from pdf_studio.core.documento import ErrorDocumento
from pdf_studio.ui import iconos
from pdf_studio.ui.comunes import botonera, tarjeta_informativa
from pdf_studio.ui.tema import marcar_primario

POSICIONES = (("Abajo a la derecha", "abajo-derecha"), ("Abajo a la izquierda", "abajo-izquierda"),
              ("Arriba a la derecha", "arriba-derecha"), ("Arriba a la izquierda", "arriba-izquierda"))
MOTIVOS = ("Aprobado", "Revisado", "Soy el autor de este documento", "Conforme")
FILTRO_CERTIFICADOS = "Certificados con clave privada (*.pfx *.p12)"


class DialogoFirmaDigital(QDialog):
    """Elegir el certificado (.pfx / .p12) y su contraseña, el motivo y si la firma se ve."""

    def __init__(self, total_paginas: int, pagina_actual: int, firmas: list[Path], certificado: str = "",
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Firmar con certificado")
        self.setMinimumWidth(520)
        self.info: InfoCertificado | None = None
        self.crear_prueba = False                # el usuario pidió crear un certificado de prueba

        self.ruta = QLineEdit(certificado, self)
        self.ruta.setPlaceholderText("Archivo .pfx o .p12")
        self.ruta.setAccessibleName("Archivo del certificado")
        examinar = QPushButton("Examinar…", self)
        iconos.asignar(examinar, "folder-open")
        examinar.setAutoDefault(False)
        examinar.clicked.connect(self._examinar)
        self.contrasena = QLineEdit(self)
        self.contrasena.setEchoMode(QLineEdit.EchoMode.Password)
        self.contrasena.setAccessibleName("Contraseña del certificado")
        self.contrasena.setPlaceholderText("La del certificado (no se guarda)")
        self.contrasena.returnPressed.connect(self._cargar)
        self.ruta.editingFinished.connect(lambda: self._cargar() if self.contrasena.text() else None)
        self.contrasena.editingFinished.connect(self._cargar)
        self.estado = QLabel("", self)
        self.estado.setWordWrap(True)
        self.estado.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        prueba = QPushButton("Crear certificado de prueba…", self)
        iconos.asignar(prueba, "file-key")
        prueba.setAutoDefault(False)
        prueba.setToolTip("Un certificado autofirmado para probar la firma digital (no tiene validez legal)")
        prueba.clicked.connect(self._pedir_prueba)

        self.motivo = QComboBox(self)
        self.motivo.setEditable(True)
        self.motivo.addItems(MOTIVOS)
        self.motivo.setAccessibleName("Motivo de la firma")
        self.lugar = QLineEdit(self)
        self.lugar.setPlaceholderText("Opcional, p. ej. Buenos Aires")
        self.lugar.setAccessibleName("Lugar de la firma")

        self.visible = QGroupBox("Firma visible en la página", self)
        self.visible.setCheckable(True)
        self.visible.setChecked(True)
        self.pagina = QSpinBox(self)
        self.pagina.setRange(1, max(1, total_paginas))
        self.pagina.setValue(pagina_actual + 1)
        self.pagina.setAccessibleName("Página de la firma")
        self.posicion = QComboBox(self)
        for texto, valor in POSICIONES:
            self.posicion.addItem(texto, valor)
        self.posicion.setAccessibleName("Posición de la firma")
        self.imagen = QComboBox(self)
        self.imagen.addItem("Sin imagen", None)
        for f in firmas:
            self.imagen.addItem(iconos.crear_icono("signature"), f.stem, str(f))
        self.imagen.setAccessibleName("Imagen de la firma manuscrita")
        self.imagen.setToolTip("Las firmas de tu biblioteca (Firmar → Firmas)")
        self.con_texto = QCheckBox("Mostrar el nombre, la fecha y el motivo", self)
        self.con_texto.setChecked(True)
        forma_visible = QFormLayout(self.visible)
        forma_visible.addRow("Página:", self.pagina)
        forma_visible.addRow("Posición:", self.posicion)
        forma_visible.addRow("Imagen:", self.imagen)
        forma_visible.addRow("", self.con_texto)

        forma = QFormLayout()
        fila = QHBoxLayout()
        fila.addWidget(self.ruta, 1)
        fila.addWidget(examinar)
        forma.addRow("Certificado:", fila)
        forma.addRow("Contraseña:", self.contrasena)
        forma.addRow("", self.estado)
        forma.addRow("", prueba)
        forma.addRow("Motivo:", self.motivo)
        forma.addRow("Lugar:", self.lugar)

        self.botones = botonera(self, "Firmar…", "file-key")
        self.boton_firmar = self.botones.button(QDialogButtonBox.StandardButton.Ok)
        self.boton_firmar.setEnabled(False)
        capa = QVBoxLayout(self)
        capa.addLayout(forma)
        capa.addWidget(self.visible)
        capa.addWidget(tarjeta_informativa(
            "La firma se agrega a una copia nueva: el original no se modifica. Si después se edita el PDF "
            "firmado, la firma deja de ser válida.", self))
        capa.addWidget(self.botones)
        (self.contrasena if certificado else self.ruta).setFocus()

    def _examinar(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(self, "Elegir el certificado", self.ruta.text(), FILTRO_CERTIFICADOS)
        if ruta:
            self.ruta.setText(ruta)
            self.info = None
            self.boton_firmar.setEnabled(False)
            self.contrasena.setFocus()
            if self.contrasena.text():
                self._cargar()

    def _pedir_prueba(self) -> None:
        self.crear_prueba = True
        self.done(QDialog.DialogCode.Rejected)

    def _cargar(self) -> bool:
        """Lee el certificado con la contraseña y muestra para quién es."""
        ruta = self.ruta.text().strip()
        if not ruta:
            return False
        if not Path(ruta).is_file():
            self._mostrar_error("No existe el archivo del certificado.")
            return False
        try:
            self.info = leer_certificado(ruta, self.contrasena.text())
        except ErrorDocumento as e:
            self._mostrar_error(str(e))
            return False
        problemas = []
        if not self.info.vigente:
            problemas.append("no está vigente")
        if not self.info.sirve_para_firmar:
            problemas.append("no está habilitado para firmar")
        texto = self.info.descripcion().replace("\n¡El certificado no está vigente!", "")
        if problemas:
            self._mostrar_error(texto + "\nEl certificado " + " y ".join(problemas) + ".")
            return False
        self.estado.setObjectName("texto_suave")
        self.estado.setText(texto)
        self._refrescar_estilo()
        self.boton_firmar.setEnabled(True)
        return True

    def _mostrar_error(self, texto: str) -> None:
        self.info = None
        self.estado.setObjectName("texto_aviso")
        self.estado.setText(texto)
        self._refrescar_estilo()
        self.boton_firmar.setEnabled(False)

    def _refrescar_estilo(self) -> None:
        self.estado.style().unpolish(self.estado)
        self.estado.style().polish(self.estado)

    def accept(self) -> None:
        if self.info is None and not self._cargar():
            return
        super().accept()

    def resultado(self) -> tuple[str, str, OpcionesFirma]:
        opciones = OpcionesFirma(razon=self.motivo.currentText().strip(), ubicacion=self.lugar.text().strip(),
                                 visible=self.visible.isChecked(), pagina=self.pagina.value() - 1,
                                 posicion=self.posicion.currentData(), imagen=self.imagen.currentData(),
                                 con_texto=self.con_texto.isChecked())
        if opciones.visible and not opciones.imagen and not opciones.con_texto:
            opciones.con_texto = True               # un recuadro vacío no sirve de nada
        return self.ruta.text().strip(), self.contrasena.text(), opciones


class DialogoCertificadoPrueba(QDialog):
    """Datos de un certificado autofirmado para probar."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Crear certificado de prueba")
        self.setMinimumWidth(460)
        self.titular = QLineEdit(self)
        self.titular.setPlaceholderText("Nombre y apellido")
        self.titular.setAccessibleName("Titular del certificado")
        self.contrasena = QLineEdit(self)
        self.contrasena.setEchoMode(QLineEdit.EchoMode.Password)
        self.contrasena.setAccessibleName("Contraseña del certificado")
        self.repetir = QLineEdit(self)
        self.repetir.setEchoMode(QLineEdit.EchoMode.Password)
        self.repetir.setAccessibleName("Repetir la contraseña")
        self.dias = QSpinBox(self)
        self.dias.setRange(1, 3650)
        self.dias.setValue(365)
        self.dias.setSuffix(" días")
        self.dias.setAccessibleName("Validez del certificado")
        self.error = QLabel("", self)
        self.error.setObjectName("texto_aviso")
        self.error.setWordWrap(True)
        self.error.hide()
        forma = QFormLayout()
        forma.addRow("Titular:", self.titular)
        forma.addRow("Contraseña:", self.contrasena)
        forma.addRow("Repetila:", self.repetir)
        forma.addRow("Validez:", self.dias)
        capa = QVBoxLayout(self)
        capa.addWidget(tarjeta_informativa(
            "Sirve para probar la firma digital: no tiene validez legal y los programas la muestran como «no "
            "confiable». Para firmar de verdad usá el certificado que te da una autoridad certificante.", self,
            "triangle-alert"))
        capa.addLayout(forma)
        capa.addWidget(self.error)
        capa.addWidget(botonera(self, "Crear…", "file-key"))
        self.titular.setFocus()

    def accept(self) -> None:
        problema = ""
        if not self.titular.text().strip():
            problema = "Falta el nombre del titular."
        elif len(self.contrasena.text()) < 4:
            problema = "La contraseña tiene que tener al menos 4 caracteres."
        elif self.contrasena.text() != self.repetir.text():
            problema = "Las contraseñas no coinciden."
        if problema:
            self.error.setText(problema)
            self.error.show()
            return
        super().accept()

    def resultado(self) -> tuple[str, str, int]:
        return self.titular.text().strip(), self.contrasena.text(), self.dias.value()


def _tarjeta_firma(estado: EstadoFirma, parent: QWidget) -> QFrame:
    tarjeta = QFrame(parent)
    tarjeta.setObjectName("tarjeta")
    capa = QHBoxLayout(tarjeta)
    capa.setContentsMargins(12, 10, 12, 10)
    if estado.valida and estado.confiable and estado.cubre_todo:
        icono = "badge-check"
    elif estado.valida:
        icono = "circle-alert"
    else:
        icono = "square-x"
    simbolo = QLabel(tarjeta)
    simbolo.setPixmap(iconos.crear_icono(icono).pixmap(22, 22))
    simbolo.setAlignment(Qt.AlignmentFlag.AlignTop)
    titulo = QLabel(f"<b>{_escapar(estado.titular)}</b> — {_escapar(estado.campo)}", tarjeta)
    resumen = QLabel(estado.resumen(), tarjeta)
    resumen.setWordWrap(True)
    resumen.setObjectName("texto_aviso" if not estado.valida else "")
    detalles = [f"Fecha de la firma: {fecha_legible(estado.fecha, hora=True)}",
                f"Emitido por: {estado.emisor}" + (" (autofirmado)" if estado.autofirmado else "")]
    if estado.razon:
        detalles.append(f"Motivo: {estado.razon}")
    if estado.ubicacion:
        detalles.append(f"Lugar: {estado.ubicacion}")
    detalles.append(f"Página: {estado.pagina + 1}" if estado.pagina is not None else "Firma invisible")
    detalle = QLabel("\n".join(detalles), tarjeta)
    detalle.setObjectName("texto_suave")
    detalle.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    textos = QVBoxLayout()
    for w in (titulo, resumen, detalle):
        textos.addWidget(w)
    capa.addWidget(simbolo)
    capa.addLayout(textos, 1)
    tarjeta.setAccessibleName(f"Firma de {estado.titular}")
    tarjeta.setAccessibleDescription(estado.resumen())
    return tarjeta


def _escapar(texto: str) -> str:
    import html
    return html.escape(texto or "")


class DialogoValidarFirmas(QDialog):
    def __init__(self, estados: list[EstadoFirma], nombre: str, aviso: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Firmas digitales")
        self.resize(600, 460)
        capa = QVBoxLayout(self)
        validas = sum(e.valida for e in estados)
        cabecera = QLabel(f"<b>{_escapar(nombre)}</b><br>{len(estados)} firma{'s' if len(estados) != 1 else ''}: "
                          f"{validas} válida{'s' if validas != 1 else ''}", self)
        cabecera.setWordWrap(True)
        capa.addWidget(cabecera)
        if aviso:
            capa.addWidget(tarjeta_informativa(aviso, self, "triangle-alert"))
        lista = QWidget(self)
        capa_lista = QVBoxLayout(lista)
        capa_lista.setContentsMargins(0, 0, 0, 0)
        for e in estados:
            capa_lista.addWidget(_tarjeta_firma(e, lista))
        capa_lista.addStretch(1)
        desplazable = QScrollArea(self)
        desplazable.setWidgetResizable(True)
        desplazable.setFrameShape(QFrame.Shape.NoFrame)
        desplazable.setWidget(lista)
        capa.addWidget(desplazable, 1)
        capa.addWidget(tarjeta_informativa(
            "La confianza se comprueba con los certificados raíz de Windows. No se consulta si el certificado "
            "fue revocado (eso necesita conexión a internet).", self))
        botones = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        cerrar = botones.button(QDialogButtonBox.StandardButton.Close)
        cerrar.setText("Cerrar")
        marcar_primario(cerrar)
        botones.rejected.connect(self.reject)
        capa.addWidget(botones)


__all__ = ["DialogoFirmaDigital", "DialogoCertificadoPrueba", "DialogoValidarFirmas", "ErrorFirma"]
