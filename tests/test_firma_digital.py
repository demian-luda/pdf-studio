"""Fase 9: firma digital con certificado de prueba (generado en cada test, nunca en el repo),
firma visible e invisible, varias firmas, validación, cambios después de firmar, la
fidelidad (píxeles = 0 fuera del recuadro) y el flujo desde la ventana."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pymupdf
import pytest

pytest.importorskip("pyhanko")

from pdf_studio import procesos  # noqa: E402
from pdf_studio.core import firma_digital as fd  # noqa: E402
from pdf_studio.core.documento import Limitacion  # noqa: E402
from tests.conftest import hash_archivo  # noqa: E402

CLAVE = "prueba123"


@pytest.fixture
def certificado(tmp_path) -> Path:
    ruta = tmp_path / "certificado_prueba.pfx"
    fd.crear_certificado_prueba(ruta, "Ana Gómez", CLAVE, dias=30)
    return ruta


@pytest.fixture
def pdf(tmp_path) -> Path:
    ruta = tmp_path / "contrato.pdf"
    with pymupdf.open() as doc:
        for i in range(3):
            pag = doc.new_page()
            pag.insert_text((72, 100), f"Contrato de prueba, página {i + 1}", fontsize=16)
            pag.draw_rect(pymupdf.Rect(72, 130, 520, 300), color=(0.2, 0.3, 0.6), width=1.5)
        doc[1].set_rotation(90)
        doc.save(ruta)
    return ruta


def _firma_png(tmp_path) -> Path:
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (300, 100), (0, 0, 0, 0))
    ImageDraw.Draw(img).line([(10, 80), (90, 20), (180, 75), (290, 25)], fill=(20, 30, 130, 255), width=7)
    ruta = tmp_path / "firma.png"
    img.save(ruta)
    return ruta


# ---------------------------------------------------------------------- certificados
def test_certificado_de_prueba(certificado):
    info = fd.leer_certificado(certificado, CLAVE)
    assert info.titular == "Ana Gómez" and info.autofirmado and info.vigente and info.sirve_para_firmar
    assert "autofirmado" in info.descripcion()


def test_certificado_con_contrasena_incorrecta_o_archivo_invalido(certificado, tmp_path):
    with pytest.raises(fd.ErrorFirma, match="contraseña"):
        fd.leer_certificado(certificado, "otra")
    falso = tmp_path / "falso.pfx"
    falso.write_bytes(b"esto no es un certificado")
    with pytest.raises(fd.ErrorFirma, match="no es un certificado"):
        fd.leer_certificado(falso, CLAVE)


def _certificado_vencido(ruta: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Vencido")])
    ahora = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(clave.public_key())
            .serial_number(1).not_valid_before(ahora - dt.timedelta(days=60))
            .not_valid_after(ahora - dt.timedelta(days=1)).sign(clave, hashes.SHA256()))
    ruta.write_bytes(pkcs12.serialize_key_and_certificates(b"v", clave, cert, None,
                                                           serialization.BestAvailableEncryption(CLAVE.encode())))


def test_certificado_vencido_no_firma(pdf, tmp_path):
    vencido = tmp_path / "vencido.pfx"
    _certificado_vencido(vencido)
    with pytest.raises(Limitacion, match="venció"):
        fd.firmar(pdf, tmp_path / "firmado.pdf", vencido, CLAVE)
    assert not (tmp_path / "firmado.pdf").exists()


# ---------------------------------------------------------------------- firmar y validar
def test_firma_invisible_valida_y_original_intacto(pdf, certificado, tmp_path):
    huella = hash_archivo(pdf)
    destino = tmp_path / "contrato_firmado.pdf"
    r = fd.firmar(pdf, destino, certificado, CLAVE, fd.OpcionesFirma(razon="Aprobado", ubicacion="Córdoba"))
    assert hash_archivo(pdf) == huella
    assert "diferencia de píxeles = 0" in r.fidelidad and r.avisos      # avisa que es autofirmado
    [estado] = fd.validar(destino)
    assert estado.valida and estado.integra and estado.correcta and estado.cubre_todo
    assert not estado.confiable and estado.autofirmado
    assert estado.titular == "Ana Gómez" and estado.razon == "Aprobado" and estado.ubicacion == "Córdoba"
    assert estado.pagina is None                                          # invisible
    assert estado.fecha is not None and abs((dt.datetime.now(dt.timezone.utc) - estado.fecha).total_seconds()) < 300
    assert "Firma válida" in estado.resumen() and "autofirmado" in estado.resumen()
    assert not list(destino.parent.glob("*.pdfstudio-tmp"))
    with pymupdf.open(destino) as doc:
        assert fd.tiene_firmas(doc)
    with pymupdf.open(pdf) as doc:
        assert not fd.tiene_firmas(doc)


def test_firma_visible_con_imagen_en_pagina_rotada(pdf, certificado, tmp_path):
    destino = tmp_path / "firmado.pdf"
    with pymupdf.open(pdf) as doc:
        caja = fd.caja_por_defecto(doc[1], "abajo-derecha")
    opciones = fd.OpcionesFirma(razon="Revisado", visible=True, pagina=1, imagen=str(_firma_png(tmp_path)))
    r = fd.firmar(pdf, destino, certificado, CLAVE, opciones)
    assert "fuera de las zonas editadas también es idéntico" in r.fidelidad
    [estado] = fd.validar(destino)
    assert estado.valida and estado.pagina == 1
    with pymupdf.open(destino) as doc:
        pag = doc[1]
        [widget] = list(pag.widgets())
        vista = widget.rect * pag.rotation_matrix
        vista.normalize()
        assert all(abs(a - b) <= 1.5 for a, b in zip(vista, caja))           # donde se pidió, en la vista
        # El sello se ve derecho: el texto del sello es más ancho que alto en la vista
        pix = pag.get_pixmap(dpi=150, clip=vista)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)
        tinta = np.argwhere(img[:, :, :3].min(axis=2) < 128)
        assert len(tinta) > 200
        alto, ancho = np.ptp(tinta[:, 0]), np.ptp(tinta[:, 1])
        assert ancho > 1.5 * alto


def test_segunda_firma_no_invalida_la_primera(pdf, certificado, tmp_path):
    una, dos = tmp_path / "una.pdf", tmp_path / "dos.pdf"
    fd.firmar(pdf, una, certificado, CLAVE, fd.OpcionesFirma(razon="Autor"))
    fd.firmar(una, dos, certificado, CLAVE, fd.OpcionesFirma(razon="Aprobado", visible=True, pagina=0))
    primera, segunda = fd.validar(dos)
    assert (primera.campo, segunda.campo) == ("Firma1", "Firma2")
    assert primera.valida and segunda.valida
    assert not primera.cubre_todo and "otras firmas" in primera.cambios_despues
    assert segunda.cubre_todo


def test_cambios_despues_de_firmar(pdf, certificado, tmp_path):
    firmado = tmp_path / "firmado.pdf"
    fd.firmar(pdf, firmado, certificado, CLAVE)
    # Un agregado incremental: la parte firmada sigue intacta, pero se avisa que hay cambios
    agregado = tmp_path / "agregado.pdf"
    agregado.write_bytes(firmado.read_bytes())
    with pymupdf.open(agregado) as doc:
        doc[0].insert_text((72, 400), "Texto agregado después", fontsize=12)
        doc.saveIncr()
    [estado] = fd.validar(agregado)
    assert not estado.cubre_todo and "Después" in estado.resumen()
    # Reescribir el archivo entero rompe la firma
    reescrito = tmp_path / "reescrito.pdf"
    with pymupdf.open(firmado) as doc:
        doc[0].insert_text((72, 420), "Otro cambio", fontsize=12)
        doc.save(reescrito, garbage=3)
    estados = fd.validar(reescrito)
    assert all(not e.valida for e in estados) and all("INVÁLIDA" in e.resumen() or e.error for e in estados)


def test_no_se_puede_firmar_sobre_el_original(pdf, certificado):
    huella = hash_archivo(pdf)
    with pytest.raises(fd.ErrorFirma, match="otro nombre"):
        fd.firmar(pdf, pdf, certificado, CLAVE)
    assert hash_archivo(pdf) == huella


def test_si_falla_la_fidelidad_no_se_guarda(pdf, certificado, tmp_path, monkeypatch):
    from pdf_studio.core.fidelidad import DiferenciaPagina, ResultadoFidelidad

    def falla(*a, **k):
        return ResultadoFidelidad(150, diferencias=[DiferenciaPagina(0, 0, 10, 100)])
    monkeypatch.setattr(fd, "comparar_documentos", falla)
    destino = tmp_path / "firmado.pdf"
    with pytest.raises(fd.ErrorFirma, match="aspecto"):
        fd.firmar(pdf, destino, certificado, CLAVE)
    assert not destino.exists() and not list(tmp_path.glob("*.pdfstudio-tmp"))


def test_pdf_sin_firmas(pdf):
    assert fd.validar(pdf) == []


def test_tarea_verificar_firma_sin_la_contrasena(pdf, certificado, tmp_path):
    """La verificación corre en otro proceso y el pedido no lleva la contraseña del certificado."""
    temporal, destino = tmp_path / "firmando.pdf", tmp_path / "firmado.pdf"
    preparada = fd.preparar_firma(pdf, temporal, certificado, CLAVE, fd.OpcionesFirma(visible=True, pagina=2))
    pedido = {"origen": str(pdf), "firma": preparada.a_dict(), "destino": str(destino), "contrasena": None}
    assert CLAVE not in json.dumps(pedido)
    (tmp_path / "p.json").write_text(json.dumps(pedido), encoding="utf-8")
    procesos.ejecutar("verificar_firma", tmp_path / "p.json", tmp_path / "r.json")
    salida = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert salida["ok"], salida
    assert Path(salida["resultado"]["destino"]) == destino and fd.validar(destino)[0].valida


# ---------------------------------------------------------------------- interfaz
def test_dialogo_firma_carga_el_certificado(qtbot, certificado):
    from pdf_studio.ui.firma_digital import DialogoFirmaDigital
    d = DialogoFirmaDigital(3, 1, [], str(certificado))
    qtbot.addWidget(d)
    assert not d.boton_firmar.isEnabled()
    d.contrasena.setText("mala")
    assert not d._cargar() and "contraseña" in d.estado.text() and not d.boton_firmar.isEnabled()
    d.contrasena.setText(CLAVE)
    assert d._cargar() and "Ana Gómez" in d.estado.text() and d.boton_firmar.isEnabled()
    ruta, clave, opciones = d.resultado()
    assert ruta == str(certificado) and clave == CLAVE
    assert opciones.visible and opciones.pagina == 1 and opciones.posicion == "abajo-derecha"


def test_dialogo_validar_muestra_cada_firma(qtbot, pdf, certificado, tmp_path):
    from pdf_studio.ui.firma_digital import DialogoValidarFirmas
    destino = tmp_path / "firmado.pdf"
    fd.firmar(pdf, destino, certificado, CLAVE, fd.OpcionesFirma(razon="Aprobado"))
    d = DialogoValidarFirmas(fd.validar(destino), destino.name)
    qtbot.addWidget(d)
    from PySide6.QtWidgets import QLabel
    textos = " ".join(w.text() for w in d.findChildren(QLabel))
    assert "Ana Gómez" in textos and "Firma válida" in textos and "Motivo: Aprobado" in textos


def test_ventana_firma_el_documento_abierto(ventana, pdf, certificado, tmp_path, monkeypatch, qtbot):
    huella = hash_archivo(pdf)
    assert ventana.abrir(pdf)
    destino = tmp_path / "contrato_firmado.pdf"
    sugeridas, abiertos = [], []
    monkeypatch.setattr(ventana, "_dialogo_firma_digital", lambda: (str(certificado), CLAVE, fd.OpcionesFirma(
        razon="Aprobado", visible=True, pagina=0)))
    monkeypatch.setattr(ventana, "_pedir_ruta_firmado", lambda s: sugeridas.append(s) or str(destino))
    monkeypatch.setattr(ventana, "_preguntar_abrir_firmado", lambda texto, avisos: abiertos.append(texto) or False)
    assert ventana.firmar_con_certificado()
    qtbot.waitUntil(lambda: bool(abiertos) or bool(ventana.mensajes), timeout=120_000)
    assert abiertos, ventana.mensajes
    assert sugeridas[0].name == "contrato_firmado.pdf"
    assert "Se guardó el PDF firmado" in abiertos[0] and "diferencia de píxeles = 0" in abiertos[0]
    assert fd.validar(destino)[0].valida and hash_archivo(pdf) == huella
    assert ventana._ultimo_certificado == str(certificado)
    # Validar desde la ventana (el documento abierto no tiene firmas)
    vistas = []
    monkeypatch.setattr(ventana, "_mostrar_validacion", lambda estados, nombre, aviso: vistas.append(estados))
    assert ventana.validar_firmas() and not vistas
    assert "no tiene firmas digitales" in ventana.mensajes[-1][1]
    assert ventana.abrir(destino)
    assert ventana.validar_firmas() and vistas[0][0].valida


def test_ventana_avisa_antes_de_guardar_un_pdf_firmado(ventana, pdf, certificado, tmp_path, monkeypatch):
    firmado = tmp_path / "firmado.pdf"
    fd.firmar(pdf, firmado, certificado, CLAVE)
    assert ventana.abrir(firmado)
    ventana.rotar(90)
    preguntas = []
    monkeypatch.setattr(ventana, "_preguntar", lambda t, x: preguntas.append(x) or False)
    assert not ventana._guardar_en(tmp_path / "editado.pdf")
    assert preguntas and "firmas digitales" in preguntas[0] and not (tmp_path / "editado.pdf").exists()


def test_ventana_crea_certificado_de_prueba(ventana, tmp_path, monkeypatch):
    destino = tmp_path / "mio.pfx"
    monkeypatch.setattr(ventana, "_dialogo_certificado_prueba", lambda: ("Luis Pérez", "clave99", 10))
    monkeypatch.setattr(ventana, "_pedir_ruta_certificado", lambda s: str(destino))
    assert ventana.crear_certificado_prueba() == destino
    assert fd.leer_certificado(destino, "clave99").titular == "Luis Pérez"
    assert "certificado de prueba" in ventana.mensajes[-1][1]
