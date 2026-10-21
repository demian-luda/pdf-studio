"""Firma digital con certificado (.pfx / .p12) y validación de firmas, con pyHanko.

- Firmar: la firma se agrega como una actualización incremental sobre una COPIA (el
  original no se toca y las firmas anteriores siguen valiendo). Puede ser invisible o
  visible, con la imagen de una firma manuscrita y/o el nombre y la fecha.
- Después de firmar se verifica que la firma sea íntegra y que las páginas se vean igual
  que antes (diferencia de píxeles = 0; en la página firmada, fuera del recuadro).
- Validar: por cada firma del PDF, si el documento no cambió desde que se firmó, si la
  firma es correcta, si el certificado es de confianza (certificados raíz de Windows) y
  si se agregó algo después.
- Certificado de prueba: un .pfx autofirmado para probar (no tiene validez legal).

La verificación de revocación necesita conexión y no se hace: se avisa.
"""
from __future__ import annotations

import datetime as _dt
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_studio import config
from pdf_studio.core.documento import ErrorDocumento, Limitacion
from pdf_studio.core.fidelidad import comparar_documentos

FUENTE_SELLO = config.DIR_FUENTES_INCLUIDAS / "LiberationSans-Regular.ttf"
TAMANO_SELLO = (210.0, 64.0)          # pt: tamaño por defecto de la firma visible
MARGEN_SELLO = 36.0

# pyHanko registra con traceback completo cada problema de confianza (por ejemplo, un
# certificado autofirmado): eso es un resultado de la validación, no un error del programa
for _nombre in ("pyhanko", "pyhanko_certvalidator", "fontTools"):
    logging.getLogger(_nombre).setLevel(logging.CRITICAL)


class ErrorFirma(ErrorDocumento):
    pass


def verificar_dependencias() -> None:
    try:
        import pyhanko  # noqa: F401
        import pyhanko_certvalidator  # noqa: F401
    except ImportError as e:
        raise Limitacion("Falta pyHanko para la firma digital. Instalalo desde el panel Faltantes "
                         "(Ver → Ver faltantes).") from e


# ---------------------------------------------------------------------- certificados
@dataclass
class InfoCertificado:
    titular: str
    emisor: str
    desde: _dt.datetime
    hasta: _dt.datetime
    autofirmado: bool
    huella: str                       # SHA-256, para reconocerlo
    sirve_para_firmar: bool

    @property
    def vigente(self) -> bool:
        ahora = _dt.datetime.now(_dt.timezone.utc)
        return self.desde <= ahora <= self.hasta

    def descripcion(self) -> str:
        partes = [f"Titular: {self.titular}", f"Emitido por: {self.emisor}"
                  + (" (autofirmado)" if self.autofirmado else ""),
                  f"Válido del {fecha_legible(self.desde)} al {fecha_legible(self.hasta)}"]
        if not self.vigente:
            partes.append("¡El certificado no está vigente!")
        return "\n".join(partes)


def fecha_legible(d: _dt.datetime | None, hora: bool = False) -> str:
    if d is None:
        return "—"
    local = d.astimezone() if d.tzinfo else d
    return local.strftime("%d/%m/%Y %H:%M" if hora else "%d/%m/%Y")


def _nombre_de(nombre) -> str:
    """Nombre legible de un x509.Name de cryptography: el CN, o lo que haya."""
    from cryptography.x509.oid import NameOID
    for oid in (NameOID.COMMON_NAME, NameOID.ORGANIZATION_NAME):
        valores = nombre.get_attributes_for_oid(oid)
        if valores:
            return str(valores[0].value)
    return nombre.rfc4514_string() or "(sin nombre)"


def _cargar_pkcs12(ruta: Path, contrasena: str):
    from cryptography.hazmat.primitives.serialization import pkcs12
    try:
        datos = Path(ruta).read_bytes()
    except OSError as e:
        raise ErrorFirma(f"No se pudo leer el certificado «{Path(ruta).name}»: {e.strerror or e}") from e
    try:
        clave, cert, extras = pkcs12.load_key_and_certificates(datos, contrasena.encode("utf-8") if contrasena
                                                                 else None)
    except ValueError as e:
        texto = str(e).lower()
        if "password" in texto or "mac" in texto or "decrypt" in texto:
            raise ErrorFirma("La contraseña del certificado no es correcta.") from e
        raise ErrorFirma(f"«{Path(ruta).name}» no es un certificado .pfx / .p12 válido.") from e
    except Exception as e:
        raise ErrorFirma(f"«{Path(ruta).name}» no es un certificado .pfx / .p12 válido "
                         f"({type(e).__name__}).") from e
    if cert is None:
        raise ErrorFirma("El archivo no tiene ningún certificado.")
    if clave is None:
        raise ErrorFirma("El archivo tiene el certificado pero no la clave privada: para firmar hace falta "
                         "el .pfx / .p12 completo (exportado «con la clave privada»).")
    return clave, cert, list(extras or [])


def leer_certificado(ruta: Path | str, contrasena: str) -> InfoCertificado:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    _, cert, _ = _cargar_pkcs12(Path(ruta), contrasena)
    try:
        uso = cert.extensions.get_extension_for_class(x509.KeyUsage).value
        sirve = bool(uso.digital_signature or uso.content_commitment)
    except x509.ExtensionNotFound:
        sirve = True
    return InfoCertificado(_nombre_de(cert.subject), _nombre_de(cert.issuer), cert.not_valid_before_utc,
                           cert.not_valid_after_utc, cert.subject == cert.issuer,
                           cert.fingerprint(hashes.SHA256()).hex(":")[:47].upper(), sirve)


def crear_certificado_prueba(destino: Path | str, titular: str, contrasena: str, dias: int = 365) -> InfoCertificado:
    """Un certificado autofirmado para probar la firma digital. No tiene validez legal: los
    programas lo muestran como «no confiable»."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID
    if not titular.strip():
        raise ErrorFirma("Falta el nombre del titular.")
    if len(contrasena) < 4:
        raise ErrorFirma("La contraseña tiene que tener al menos 4 caracteres.")
    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, titular.strip()),
                        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Certificado de prueba (PDF Studio)")])
    ahora = _dt.datetime.now(_dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre).public_key(clave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora - _dt.timedelta(minutes=5)).not_valid_after(ahora + _dt.timedelta(days=dias))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=True, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                         crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .sign(clave, hashes.SHA256()))
    datos = pkcs12.serialize_key_and_certificates(titular.strip().encode("utf-8"), clave, cert, None,
                                                  serialization.BestAvailableEncryption(contrasena.encode("utf-8")))
    destino = Path(destino)
    try:
        destino.write_bytes(datos)
    except OSError as e:
        raise ErrorFirma(f"No se pudo guardar el certificado en {destino}: {e.strerror or e}") from e
    return leer_certificado(destino, contrasena)


# ---------------------------------------------------------------------- firmar
@dataclass
class OpcionesFirma:
    razon: str = ""
    ubicacion: str = ""
    contacto: str = ""
    visible: bool = False
    pagina: int = 0                              # índice (desde 0)
    # En puntos, en coordenadas de la página como se ve (origen arriba a la izquierda)
    caja: tuple[float, float, float, float] | None = None
    posicion: str = "abajo-derecha"              # si no hay `caja`: esquina de la página
    imagen: str | None = None                    # firma manuscrita (PNG) dentro del recuadro
    con_texto: bool = True                       # nombre del titular y fecha dentro del recuadro


@dataclass
class ResultadoFirma:
    destino: Path
    campo: str
    avisos: list[str] = field(default_factory=list)
    fidelidad: str = ""

    def a_dict(self) -> dict:
        return {"destino": str(self.destino), "campo": self.campo, "avisos": list(self.avisos),
                "fidelidad": self.fidelidad}

    @classmethod
    def desde_dict(cls, d: dict) -> "ResultadoFirma":
        return cls(Path(d["destino"]), d["campo"], list(d.get("avisos", [])), d.get("fidelidad", ""))


def caja_por_defecto(pag: pymupdf.Page, posicion: str = "abajo-derecha") -> tuple[float, float, float, float]:
    """Recuadro de la firma visible en una esquina de la página (coordenadas de la página)."""
    ancho, alto = TAMANO_SELLO
    r = pag.rect
    x0 = r.x1 - MARGEN_SELLO - ancho if "derecha" in posicion else r.x0 + MARGEN_SELLO
    y0 = r.y1 - MARGEN_SELLO - alto if "abajo" in posicion else r.y0 + MARGEN_SELLO
    return (x0, y0, x0 + ancho, y0 + alto)


def _caja_pdf(pag: pymupdf.Page, caja) -> tuple[int, int, int, int]:
    """De coordenadas de la página (como se ve, origen arriba a la izquierda) al espacio del
    PDF (origen abajo a la izquierda, sin rotar), que es lo que usa el campo de firma."""
    r = pymupdf.Rect(caja) * pag.derotation_matrix * ~pag.transformation_matrix
    r.normalize()
    return (int(round(r.x0)), int(round(r.y0)), int(round(r.x1)), int(round(r.y1)))


def _campos_existentes(lector) -> set[str]:
    try:
        return {s.field_name for s in lector.embedded_signatures}
    except Exception:
        return set()


def _nombre_campo(existentes: set[str], doc: pymupdf.Document) -> str:
    try:
        existentes |= {w.field_name for pag in doc for w in (pag.widgets() or [])}
    except Exception:
        pass
    n = 1
    while f"Firma{n}" in existentes:
        n += 1
    return f"Firma{n}"


def _derecho_en_la_pagina(estilo, rotacion: int):
    """Envuelve el estilo para que, en una página rotada (/Rotate), el sello se vea derecho:
    se dibuja en el tamaño que se ve y el aspecto lleva una /Matrix que lo gira."""
    import dataclasses

    from pyhanko.pdf_utils import generic, layout
    from pyhanko.stamp import TextStamp, TextStampStyle
    rotacion %= 360
    if rotacion == 0:
        return estilo

    class EstiloGirado(TextStampStyle):
        def create_stamp(self, writer, box, text_params):
            if rotacion in (90, 270):
                box = layout.BoxConstraints(width=box.height, height=box.width)
            sello = TextStamp(writer=writer, style=self, box=box, text_params=text_params)
            original = sello.as_form_xobject

            def con_matriz():
                xobj = original()
                w, h = sello.box.width, sello.box.height
                m = {90: (0, 1, -1, 0, h, 0), 180: (-1, 0, 0, -1, w, h), 270: (0, -1, 1, 0, 0, w)}[rotacion]
                xobj["/Matrix"] = generic.ArrayObject([generic.FloatObject(v) for v in m])
                return xobj

            sello.as_form_xobject = con_matriz
            return sello

    return EstiloGirado(**{f.name: getattr(estilo, f.name) for f in dataclasses.fields(estilo) if f.init})


def _fuente_del_sello() -> Path | None:
    """La fuente del texto del sello, con 1000 unidades por em: pyHanko escribe los anchos de
    las letras sin escalarlos, así que con una TrueType de 2048 las letras quedarían separadas."""
    if not FUENTE_SELLO.exists():
        return None
    destino = config.DIR_CACHE / f"sello-{FUENTE_SELLO.stem}-1000.ttf"
    if destino.exists():
        return destino
    try:
        from fontTools.ttLib import TTFont
        from fontTools.ttLib.scaleUpem import scale_upem
        fuente = TTFont(str(FUENTE_SELLO))
        if fuente["head"].unitsPerEm != 1000:
            scale_upem(fuente, 1000)
        destino.parent.mkdir(parents=True, exist_ok=True)
        temporal = destino.with_suffix(".tmp")
        fuente.save(str(temporal))
        temporal.replace(destino)
        return destino
    except Exception:
        return None                       # se usa la fuente básica de pyHanko (sin tildes raras)


def _estilo(opciones: OpcionesFirma, titular: str):
    from pyhanko import stamp
    from pyhanko.pdf_utils import images, layout, text
    from pyhanko.pdf_utils.font import opentype
    lineas = []
    if opciones.con_texto:
        lineas = [f"Firmado digitalmente por {titular}", "Fecha: %(ts)s"]
        if opciones.razon:
            lineas.append(f"Motivo: {opciones.razon}")
        if opciones.ubicacion:
            lineas.append(f"Lugar: {opciones.ubicacion}")
    # El tamaño tiene que ser el mismo en la fuente y en la caja: la fuente ubica las letras
    tamano = 7
    archivo = _fuente_del_sello()
    fuente = opentype.GlyphAccumulatorFactory(str(archivo), font_size=tamano) if archivo else None
    caja_texto = (text.TextBoxStyle(font=fuente, font_size=tamano) if fuente
                  else text.TextBoxStyle(font_size=tamano))
    fondo = None
    if opciones.imagen:
        try:
            from PIL import Image
            with Image.open(opciones.imagen) as img:
                img.load()
                fondo = images.PdfImage(img.convert("RGBA") if img.mode not in ("RGB", "RGBA") else img.copy())
        except Exception as e:
            raise ErrorFirma(f"No se pudo usar la imagen de la firma ({type(e).__name__}).") from e
    disposicion = layout.SimpleBoxLayoutRule(x_align=layout.AxisAlignment.ALIGN_MID,
                                             y_align=layout.AxisAlignment.ALIGN_MID,
                                             margins=layout.Margins.uniform(2))
    return stamp.TextStampStyle(stamp_text="\n".join(lineas) if lineas else " ", border_width=0,
                                background=fondo, background_opacity=1.0, background_layout=disposicion,
                                text_box_style=caja_texto, timestamp_format="%d/%m/%Y %H:%M")


@dataclass
class FirmaPreparada:
    """Una firma recién hecha en un archivo temporal, todavía sin verificar (ver `verificar_firma`)."""
    temporal: Path
    campo: str
    pagina: int | None = None                    # la de la firma visible
    zona: tuple[float, float, float, float] | None = None   # su recuadro, sin rotar (para la fidelidad)
    autofirmado: bool = False

    def a_dict(self) -> dict:
        return {"temporal": str(self.temporal), "campo": self.campo, "pagina": self.pagina,
                "zona": list(self.zona) if self.zona else None, "autofirmado": self.autofirmado}

    @classmethod
    def desde_dict(cls, d: dict) -> "FirmaPreparada":
        return cls(Path(d["temporal"]), d["campo"], d.get("pagina"), tuple(d["zona"]) if d.get("zona") else None,
                   bool(d.get("autofirmado", False)))


def firmar(origen: Path | str, destino: Path | str, certificado: Path | str, contrasena: str,
           opciones: OpcionesFirma | None = None, contrasena_pdf: str | None = None) -> ResultadoFirma:
    """Firma una copia de `origen` en `destino` y la verifica."""
    destino = Path(destino)
    if destino.resolve() == Path(origen).resolve():
        raise ErrorFirma("El PDF firmado tiene que guardarse con otro nombre: el original no se modifica.")
    temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
    preparada = preparar_firma(origen, temporal, certificado, contrasena, opciones, contrasena_pdf)
    return verificar_firma(origen, preparada, destino, contrasena_pdf)


def preparar_firma(origen: Path | str, temporal: Path | str, certificado: Path | str, contrasena: str,
                   opciones: OpcionesFirma | None = None, contrasena_pdf: str | None = None) -> FirmaPreparada:
    """Firma una copia de `origen` en `temporal` (rápido; necesita la contraseña del certificado,
    que no se guarda en ningún lado). Falta verificarla con `verificar_firma`."""
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers

    verificar_dependencias()
    origen, temporal = Path(origen), Path(temporal)
    opciones = opciones or OpcionesFirma()
    if temporal.resolve() == origen.resolve():
        raise ErrorFirma("El PDF firmado tiene que guardarse con otro nombre: el original no se modifica.")
    info = leer_certificado(certificado, contrasena)
    ahora = _dt.datetime.now(_dt.timezone.utc)
    if ahora > info.hasta:
        raise Limitacion(f"El certificado venció el {fecha_legible(info.hasta)}: una firma con él no es válida.")
    if ahora < info.desde:
        raise Limitacion(f"El certificado todavía no es válido (empieza el {fecha_legible(info.desde)}).")
    if not info.sirve_para_firmar:
        raise Limitacion("El certificado no está habilitado para firmar (su «uso de clave» no incluye la "
                         "firma digital).")
    try:
        datos = origen.read_bytes()
    except OSError as e:
        raise ErrorFirma(f"No se pudo leer «{origen.name}»: {e.strerror or e}") from e
    try:
        doc = pymupdf.open(stream=datos, filetype="pdf")
    except Exception as e:
        raise ErrorFirma(f"«{origen.name}» no es un PDF válido.") from e
    with doc:
        if doc.needs_pass and not doc.authenticate(contrasena_pdf or ""):
            raise Limitacion("El PDF está protegido con contraseña: abrilo con la contraseña antes de firmar.")
        if doc.is_encrypted or doc.needs_pass:
            raise Limitacion("El PDF está cifrado: para firmarlo, primero guardá una copia sin protección.")
        if opciones.visible:
            if not 0 <= opciones.pagina < doc.page_count:
                raise ErrorFirma("La página elegida para la firma no existe.")
            pag = doc[opciones.pagina]
            caja = opciones.caja or caja_por_defecto(pag, opciones.posicion)
            caja_pdf = _caja_pdf(pag, caja)
            sin_rotar = tuple(pymupdf.Rect(caja) * pag.derotation_matrix)
            rotacion = pag.rotation
        try:
            entrada = io.BytesIO(datos)
            escritor = IncrementalPdfFileWriter(entrada, strict=False)
        except Exception as e:
            raise ErrorFirma(f"pyHanko no pudo leer la estructura del PDF ({type(e).__name__}); probá primero "
                             "guardar una copia con PDF Studio y firmar esa.") from e
        campo = _nombre_campo(_campos_existentes(escritor.prev), doc)

    try:
        firmante = signers.SimpleSigner.load_pkcs12(str(certificado), passphrase=contrasena.encode("utf-8"))
    except Exception as e:
        raise ErrorFirma(f"No se pudo usar el certificado para firmar ({type(e).__name__}).") from e
    if firmante is None:
        raise ErrorFirma("No se pudo usar el certificado para firmar (¿contraseña correcta?).")
    meta = signers.PdfSignatureMetadata(field_name=campo, md_algorithm="sha256", reason=opciones.razon or None,
                                        location=opciones.ubicacion or None, contact_info=opciones.contacto or None,
                                        name=info.titular, subfilter=fields.SigSeedSubFilter.PADES)
    espec = None
    estilo = None
    if opciones.visible:
        espec = fields.SigFieldSpec(campo, on_page=opciones.pagina, box=caja_pdf)
        estilo = _derecho_en_la_pagina(_estilo(opciones, info.titular), rotacion)
    else:
        espec = fields.SigFieldSpec(campo)
    try:
        with open(temporal, "wb") as salida:
            signers.PdfSigner(meta, signer=firmante, stamp_style=estilo, new_field_spec=espec).sign_pdf(
                escritor, output=salida)
    except OSError as e:
        temporal.unlink(missing_ok=True)
        raise ErrorFirma(f"No se pudo escribir {temporal}: {e.strerror or e}") from e
    except ErrorDocumento:
        temporal.unlink(missing_ok=True)
        raise
    except Exception as e:
        temporal.unlink(missing_ok=True)
        raise ErrorFirma(f"pyHanko no pudo firmar el PDF ({type(e).__name__}: {str(e)[:200]}).") from e

    return FirmaPreparada(temporal, campo, opciones.pagina if opciones.visible else None,
                          sin_rotar if opciones.visible else None, info.autofirmado)


def verificar_firma(origen: Path | str, preparada: FirmaPreparada, destino: Path | str,
                    contrasena_pdf: str | None = None) -> ResultadoFirma:
    """La firma nueva tiene que ser íntegra y el resto del documento verse igual que el
    original (píxeles = 0; en la página de la firma visible, fuera del recuadro). Recién
    entonces el temporal pasa a ser `destino`; si no, se borra y se avisa."""
    origen, destino, temporal = Path(origen), Path(destino), preparada.temporal
    campo = preparada.campo
    try:
        estados = validar(temporal)
        nueva = next((e for e in estados if e.campo == campo), None)
        if nueva is None or not (nueva.integra and nueva.correcta):
            raise ErrorFirma("La firma recién hecha no pasó la verificación; no se guardó el archivo.")
        visible = preparada.pagina is not None
        parciales = {preparada.pagina: (preparada.pagina, [preparada.zona])} if visible else None
        with pymupdf.open(origen) as d:
            if d.needs_pass:
                d.authenticate(contrasena_pdf or "")
            total = d.page_count
        mapa = {i: i for i in range(total) if i != preparada.pagina}
        resultado = comparar_documentos(origen, temporal, mapa=mapa, parciales=parciales,
                                        contrasena=contrasena_pdf)
        if not resultado.ok:
            raise ErrorFirma("Al firmar cambió el aspecto de alguna página: " + resultado.resumen()
                             + " No se guardó el archivo.")
        temporal.replace(destino)
    except PermissionError as e:
        temporal.unlink(missing_ok=True)
        raise ErrorFirma(f"No se pudo escribir {destino}. ¿Está abierto en otro programa?") from e
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    avisos = []
    if preparada.autofirmado:
        avisos.append("El certificado es autofirmado: la firma es correcta, pero otros programas la van a "
                      "mostrar como «de identidad desconocida» o «no confiable».")
    return ResultadoFirma(destino, campo, avisos, resultado.resumen())


# ---------------------------------------------------------------------- validar
@dataclass
class EstadoFirma:
    campo: str
    titular: str = "(desconocido)"
    emisor: str = ""
    fecha: _dt.datetime | None = None
    integra: bool = False            # el contenido firmado no cambió
    correcta: bool = False           # la firma criptográfica corresponde al certificado
    confiable: bool = False          # el certificado lleva a una raíz de confianza de Windows
    cubre_todo: bool = False         # nada se agregó después
    cambios_despues: str = ""        # qué se agregó después, si algo
    razon: str = ""
    ubicacion: str = ""
    pagina: int | None = None
    autofirmado: bool = False
    vigente_al_firmar: bool = True
    error: str = ""

    @property
    def valida(self) -> bool:
        return self.integra and self.correcta and not self.error

    def resumen(self) -> str:
        if self.error:
            return f"No se pudo validar: {self.error}"
        if not self.integra:
            return "INVÁLIDA: el documento se modificó después de firmarlo."
        if not self.correcta:
            return "INVÁLIDA: la firma no corresponde al certificado."
        partes = ["Firma válida: el documento no cambió desde que se firmó."]
        if not self.confiable:
            partes.append("El certificado no es de una autoridad de confianza"
                          + (" (es autofirmado)." if self.autofirmado else "."))
        if not self.vigente_al_firmar:
            partes.append("El certificado no estaba vigente al firmar.")
        if not self.cubre_todo:
            partes.append(self.cambios_despues or "Después de la firma se agregaron cambios.")
        return " ".join(partes)


def _raices_de_windows() -> list:
    """Certificados raíz de confianza de Windows (para saber si un certificado es confiable)."""
    import ssl

    from asn1crypto import x509 as asn1_x509
    raices = []
    for almacen in ("ROOT", "CA"):
        try:
            for der, codificacion, uso in ssl.enum_certificates(almacen):
                if codificacion != "x509_asn":
                    continue
                if uso is not True and "1.3.6.1.5.5.7.3.4" not in uso and "1.3.6.1.4.1.311.10.3.12" not in uso \
                        and "2.5.29.37.0" not in uso:
                    continue                   # raíces solo para servidores web, por ejemplo
                try:
                    cert = asn1_x509.Certificate.load(der)
                    if almacen == "ROOT" or cert.self_signed == "maybe":
                        raices.append(cert)
                except Exception:
                    continue
        except (AttributeError, OSError):
            continue                           # fuera de Windows no hay almacén
    return raices


def validar(ruta: Path | str, contrasena_pdf: str | None = None) -> list[EstadoFirma]:
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign import validation
    from pyhanko.sign.validation.status import SignatureCoverageLevel
    from pyhanko_certvalidator import ValidationContext

    verificar_dependencias()
    ruta = Path(ruta)
    try:
        datos = ruta.read_bytes()
    except OSError as e:
        raise ErrorFirma(f"No se pudo leer «{ruta.name}»: {e.strerror or e}") from e
    try:
        lector = PdfFileReader(io.BytesIO(datos), strict=False)
        if lector.encrypted:
            if not contrasena_pdf:
                raise Limitacion("El PDF está protegido con contraseña.")
            lector.decrypt(contrasena_pdf)
        firmas = list(lector.embedded_signatures)
    except ErrorDocumento:
        raise
    except Exception as e:
        raise ErrorFirma(f"No se pudieron leer las firmas del PDF ({type(e).__name__}).") from e
    if not firmas:
        return []
    raices = _raices_de_windows()
    paginas = _paginas_de_campos(datos, contrasena_pdf)
    estados = []
    for firma in firmas:
        estado = EstadoFirma(firma.field_name or "(sin nombre)", pagina=paginas.get(firma.field_name))
        try:
            dic = firma.sig_object
            estado.razon = str(dic.get("/Reason", "") or "")
            estado.ubicacion = str(dic.get("/Location", "") or "")
            contexto = ValidationContext(trust_roots=raices, allow_fetching=False)
            st = validation.validate_pdf_signature(firma, contexto)
            estado.integra, estado.correcta, estado.confiable = bool(st.intact), bool(st.valid), bool(st.trusted)
            estado.fecha = st.signer_reported_dt
            cert = st.signing_cert
            estado.titular = cert.subject.native.get("common_name") or cert.subject.human_friendly
            estado.emisor = cert.issuer.native.get("common_name") or cert.issuer.human_friendly
            estado.autofirmado = cert.subject == cert.issuer
            if estado.fecha is not None:
                validez = cert["tbs_certificate"]["validity"]
                estado.vigente_al_firmar = (validez["not_before"].native <= estado.fecha
                                            <= validez["not_after"].native)
            estado.cubre_todo = st.coverage == SignatureCoverageLevel.ENTIRE_FILE
            if not estado.cubre_todo:
                nivel = getattr(st, "modification_level", None)
                nombre = getattr(nivel, "name", "")
                estado.cambios_despues = {
                    "LTA_UPDATES": "Después se agregó información de validación (no cambia el contenido).",
                    "FORM_FILLING": "Después se completaron campos de formulario o se agregaron otras firmas.",
                    "ANNOTATIONS": "Después se agregaron anotaciones o comentarios.",
                    "OTHER": "Después se hicieron otros cambios en el documento: revisá qué cambió.",
                }.get(nombre, "Después de la firma se agregaron cambios.")
        except Exception as e:
            estado.error = f"{type(e).__name__}: {str(e)[:160]}"
        estados.append(estado)
    return estados


def _paginas_de_campos(datos: bytes, contrasena: str | None) -> dict[str, int]:
    paginas: dict[str, int] = {}
    try:
        with pymupdf.open(stream=datos, filetype="pdf") as doc:
            if doc.needs_pass:
                doc.authenticate(contrasena or "")
            for pag in doc:
                for w in pag.widgets() or []:
                    # Una firma invisible también tiene su widget, pero sin tamaño
                    if (w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE and w.rect.width > 0.5
                            and w.rect.height > 0.5):
                        paginas.setdefault(w.field_name, pag.number)
    except Exception:
        pass
    return paginas


def tiene_firmas(doc: pymupdf.Document) -> bool:
    """Para avisar antes de guardar cambios: editar un PDF firmado invalida sus firmas."""
    try:
        return bool(doc.get_sigflags() > 0) or any(
            w.field_type == pymupdf.PDF_WIDGET_TYPE_SIGNATURE for pag in doc for w in (pag.widgets() or []))
    except Exception:
        return False
