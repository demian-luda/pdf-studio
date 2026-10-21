"""Redacción real: eliminar del archivo (no solo tapar) el contenido de zonas marcadas.

- Se borra el texto, la parte de las imágenes y los dibujos que caen dentro de cada zona
  (MuPDF reescribe el contenido de la página sin esos elementos) y se pinta un recuadro.
- También se borran las anotaciones que tocan la zona y se vacían los campos de formulario
  cuyo valor coincide con lo buscado.
- El guardado es COMPLETO (garbage=4): si fuera incremental, el contenido viejo seguiría
  dentro del archivo, en la versión anterior.
- Después de guardar se verifica que en las zonas no quede texto extraíble y que lo
  redactado no aparezca en ningún objeto del archivo (metadatos, anotaciones, etc.).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_studio.core.documento import ErrorDocumento, Limitacion, Operacion
from pdf_studio.core.pdfutil import independizar_recursos

NEGRO = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Patron:
    nombre: str
    descripcion: str
    expresion: str


# Patrones frecuentes en documentos argentinos
PATRONES = [
    Patron("CUIT / CUIL", "11 dígitos, con o sin guiones (20-12345678-9)",
           r"(?<![\d.-])(?:20|23|24|25|26|27|30|33|34)-?\d{8}-?\d(?![\d-])"),
    Patron("DNI", "7 u 8 dígitos, con o sin puntos (12.345.678)",
           r"(?<![\d.,-])\d{1,2}\.?\d{3}\.?\d{3}(?![\d.,-]*\d)"),
    Patron("CBU / CVU", "22 dígitos", r"(?<!\d)\d{22}(?!\d)"),
    Patron("Correo electrónico", "nombre@dominio", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    Patron("Teléfono", "con característica, p. ej. (011) 4567-8901 o 11 2345-6789",
           r"(?<![\w.])(?:\+?54\s?9?\s?)?(?:\(?0?\d{2,4}\)?[\s-]?)(?:15[\s-]?)?\d{3,4}[\s-]\d{4}(?!\d)"),
]


@dataclass
class Coincidencia:
    pagina: int
    texto: str
    rects: list[tuple]                 # cajas (sin rotar), una por línea
    origen: str = "texto"              # "texto", "anotación" o "campo"
    xref: int = 0                      # anotación o campo de formulario, si corresponde

    @property
    def caja(self) -> pymupdf.Rect:
        r = pymupdf.Rect(self.rects[0])
        for x in self.rects[1:]:
            r |= pymupdf.Rect(x)
        return r


# ---------------------------------------------------------------------- búsqueda
def _caracteres_por_linea(pag: pymupdf.Page) -> list[list[tuple[str, pymupdf.Rect]]]:
    lineas = []
    datos = pag.get_text("rawdict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    for bloque in datos["blocks"]:
        for linea in bloque.get("lines", []):
            caracteres = [(c["c"], pymupdf.Rect(c["bbox"])) for s in linea["spans"] for c in s["chars"]]
            if caracteres:
                lineas.append(caracteres)
    return lineas


def compilar(patron: str, es_expresion: bool, mayusculas: bool = False) -> re.Pattern:
    """Lanza ValueError con un mensaje claro si la expresión es inválida."""
    if not patron.strip():
        raise ValueError("Escribí qué buscar.")
    banderas = 0 if mayusculas else re.IGNORECASE
    try:
        return re.compile(patron if es_expresion else re.escape(patron), banderas)
    except re.error as e:
        raise ValueError(f"La expresión regular no es válida: {e}") from e


def buscar(doc: pymupdf.Document, expresion: re.Pattern, paginas: list[int] | None = None,
           incluir_anotaciones: bool = True) -> list[Coincidencia]:
    """Coincidencias en el texto de las páginas (con sus cajas exactas, letra por letra), en
    el contenido de las anotaciones y en los valores de los campos de formulario."""
    resultado: list[Coincidencia] = []
    for p in (paginas if paginas is not None else range(doc.page_count)):
        pag = doc[p]
        # El texto que se ve dentro de un campo de formulario es su valor: se informa como campo
        cajas_campos = [w.rect for w in pag.widgets() or []]
        for caracteres in _caracteres_por_linea(pag):
            texto = "".join(c for c, _ in caracteres)
            for m in expresion.finditer(texto):
                if m.end() <= m.start():
                    continue
                caja = None
                for c, r in caracteres[m.start():m.end()]:
                    if r.is_empty or c.isspace():
                        continue
                    caja = r if caja is None else caja | r
                centro = (caja.tl + caja.br) / 2 if caja is not None else None
                if caja is not None and not any(centro in r for r in cajas_campos):
                    resultado.append(Coincidencia(p, m.group(0), [tuple(caja)]))
        if not incluir_anotaciones:
            continue
        for annot in pag.annots() or []:
            contenido = " ".join(v for v in (annot.info.get("content", ""), annot.info.get("title", ""),
                                            annot.info.get("subject", "")) if v)
            for m in expresion.finditer(contenido):
                resultado.append(Coincidencia(p, m.group(0), [tuple(annot.rect)], "anotación", annot.xref))
        for widget in pag.widgets() or []:
            valor = str(widget.field_value or "")
            for m in expresion.finditer(valor):
                resultado.append(Coincidencia(p, m.group(0), [tuple(widget.rect)], "campo", widget.xref))
    return resultado


# ---------------------------------------------------------------------- arreglo del texto TJ
# MuPDF (1.28) tiene un error al redactar texto dibujado con TJ: si antes de lo borrado
# quedan letras en el mismo TJ, pierde el avance de la primera letra borrada y corre todo
# lo que sigue en la línea. Con Tj lo hace bien. Por eso, antes de redactar, cada TJ se
# reescribe como la secuencia equivalente según la norma PDF: «[(a) n (b)] TJ» →
# «(a) Tj [n] TJ (b) Tj». Se dibuja exactamente igual y se escribe en objetos NUEVOS que
# usa solo esta página (copia al escribir): las otras páginas no se tocan.

def _sin_tj(datos: bytes) -> bytes | None:
    """El contenido con cada TJ separado en Tj; None si no había TJ con texto."""
    import pikepdf

    with pikepdf.Pdf.new() as auxiliar:
        instrucciones = pikepdf.parse_content_stream(pikepdf.Stream(auxiliar, datos))
        nuevas, cambio = [], False
        for ins in instrucciones:
            es_tj = (isinstance(ins, pikepdf.ContentStreamInstruction) and str(ins.operator) == "TJ"
                     and ins.operands and isinstance(ins.operands[0], pikepdf.Array))
            if es_tj:
                cambio = True
                for elemento in ins.operands[0]:
                    if isinstance(elemento, pikepdf.String):
                        nuevas.append(pikepdf.ContentStreamInstruction([elemento], pikepdf.Operator("Tj")))
                    else:
                        nuevas.append(pikepdf.ContentStreamInstruction([pikepdf.Array([elemento])],
                                                                       pikepdf.Operator("TJ")))
            else:
                nuevas.append(ins)
        return pikepdf.unparse_content_stream(nuevas) if cambio else None


def _xobjects(doc: pymupdf.Document, recursos) -> dict[str, int]:
    """{nombre: xref} de los XObject de un diccionario de recursos ya parseado."""
    from pdf_studio.core.pdfutil import Ref, parsear_objeto
    xobj = recursos.get("XObject") if isinstance(recursos, dict) else None
    if isinstance(xobj, Ref):
        xobj = parsear_objeto(doc.xref_object(int(xobj), compressed=True))
    if not isinstance(xobj, dict):
        return {}
    return {k: int(v) for k, v in xobj.items() if isinstance(v, Ref)}


def _recursos(doc: pymupdf.Document, xref: int, clave: str = "Resources"):
    from pdf_studio.core.pdfutil import parsear_objeto
    tipo, valor = doc.xref_get_key(xref, clave)
    if tipo == "xref":
        return parsear_objeto(doc.xref_object(int(valor.split()[0]), compressed=True))
    if tipo == "dict":
        return parsear_objeto(valor)
    return None


def _recursos_con(recursos: dict, doc: pymupdf.Document, cambios: dict[str, int]) -> dict:
    """Copia del diccionario de recursos con los XObject de `cambios` reemplazados."""
    from pdf_studio.core.pdfutil import Ref, parsear_objeto
    nuevo = dict(recursos)
    xobj = nuevo.get("XObject")
    if isinstance(xobj, Ref):
        xobj = parsear_objeto(doc.xref_object(int(xobj), compressed=True))
    xobj = dict(xobj)
    for nombre, xref in cambios.items():
        xobj[nombre] = Ref(xref)
    nuevo["XObject"] = xobj
    return nuevo


def _forma_sin_tj(doc: pymupdf.Document, xref: int, vistos: frozenset = frozenset()) -> int | None:
    """Copia del Form XObject (y de los que dibuja adentro) sin TJ; None si no hacía falta."""
    from pdf_studio.core.pdfutil import serializar_objeto
    if xref in vistos or len(vistos) > 8 or doc.xref_get_key(xref, "Subtype")[1] != "/Form":
        return None
    contenido = doc.xref_stream(xref) or b""
    nuevo = _sin_tj(contenido)
    recursos = _recursos(doc, xref)
    cambios = {}
    for nombre, sub in _xobjects(doc, recursos).items():
        copia = _forma_sin_tj(doc, sub, vistos | {xref})
        if copia is not None:
            cambios[nombre] = copia
    if nuevo is None and not cambios:
        return None
    copia = doc.get_new_xref()
    doc.update_object(copia, doc.xref_object(xref, compressed=True))
    doc.update_stream(copia, nuevo if nuevo is not None else contenido)
    if cambios:
        doc.xref_set_key(copia, "Resources", serializar_objeto(_recursos_con(recursos, doc, cambios)))
    return copia


def separar_tj(doc: pymupdf.Document, pagina: int) -> None:
    """Aplica _sin_tj al contenido de la página y a los Form XObject que dibuja (también los
    anidados). Todo se escribe en objetos nuevos que solo usa esta página."""
    from pdf_studio.core.pdfutil import serializar_objeto
    pag = doc[pagina]
    contenido = b"\n".join(doc.xref_stream(x) or b"" for x in pag.get_contents())
    nuevo = _sin_tj(contenido)
    if nuevo is not None:
        xref = doc.get_new_xref()
        doc.update_object(xref, "<<>>")
        doc.update_stream(xref, nuevo)
        doc.xref_set_key(pag.xref, "Contents", f"{xref} 0 R")
    recursos = _recursos(doc, pag.xref)
    cambios = {}
    for nombre, sub in _xobjects(doc, recursos).items():
        copia = _forma_sin_tj(doc, sub)
        if copia is not None:
            cambios[nombre] = copia
    if cambios:
        # Recursos propios de la página (no se toca un diccionario compartido)
        doc.xref_set_key(pag.xref, "Resources", serializar_objeto(_recursos_con(recursos, doc, cambios)))


# ---------------------------------------------------------------------- operación
@dataclass
class OpcionesRedaccion:
    relleno: tuple[float, float, float] | None = NEGRO      # None = sin recuadro (queda en blanco)
    borrar_imagenes: bool = True        # borrar los píxeles de imágenes dentro de la zona
    borrar_dibujos: bool = True         # borrar líneas y formas que quedan dentro de la zona


@dataclass
class Redaccion:
    zonas: dict[int, list[tuple]] = field(default_factory=dict)          # {página: [rect]}
    anotaciones: dict[int, list[int]] = field(default_factory=dict)      # {página: [xref]} a borrar
    campos: dict[int, list[int]] = field(default_factory=dict)           # {página: [xref]} a vaciar
    textos: list[str] = field(default_factory=list)                      # lo que no tiene que quedar
    cajas_extra: dict[int, list[tuple]] = field(default_factory=dict)    # recuadros de anotaciones y campos

    def agregar(self, c: Coincidencia) -> None:
        if c.origen in ("anotación", "campo"):
            destino = self.anotaciones if c.origen == "anotación" else self.campos
            destino.setdefault(c.pagina, []).append(c.xref)
            self.cajas_extra.setdefault(c.pagina, []).extend(c.rects)      # solo para mostrarlos
        else:
            self.zonas.setdefault(c.pagina, []).extend(c.rects)
        if c.texto.strip() and c.texto not in self.textos:
            self.textos.append(c.texto)

    def agregar_zona(self, pagina: int, rect) -> None:
        self.zonas.setdefault(pagina, []).append(tuple(pymupdf.Rect(rect)))

    @property
    def vacia(self) -> bool:
        return not (self.zonas or self.anotaciones or self.campos)

    @property
    def paginas(self) -> set[int]:
        return set(self.zonas) | set(self.anotaciones) | set(self.campos)


class Redactar(Operacion):
    """Elimina del archivo lo que hay en las zonas. Exige guardado completo."""

    nombre = "Redactar"
    requiere_guardado_completo = True

    def __init__(self, redaccion: Redaccion, opciones: OpcionesRedaccion | None = None):
        if redaccion.vacia:
            raise ValueError("No hay nada marcado para redactar.")
        self.redaccion = redaccion
        self.opciones = opciones or OpcionesRedaccion()
        self._zonas_afectadas: dict[int, list[tuple]] = {}

    def aplicar(self, doc: pymupdf.Document) -> None:
        o = self.opciones
        self._zonas_afectadas = {}
        for p in sorted(self.redaccion.paginas):
            if p >= doc.page_count:
                raise Limitacion(f"La página {p + 1} ya no existe: volvé a marcar la redacción.")
            independizar_recursos(doc, p)
            pag = doc[p]
            zonas = [pymupdf.Rect(r) for r in self.redaccion.zonas.get(p, [])]
            afectadas = [tuple(z + (-1, -1, 1, 1)) for z in zonas]
            # MuPDF borra cada letra que toque la zona: la zona real incluye esas letras enteras
            for caracteres in _caracteres_por_linea(pag):
                for _c, r in caracteres:
                    if any(r.intersects(z) for z in zonas):
                        afectadas.append(tuple(r + (-1, -1, 1, 1)))
            # Anotaciones que tocan la zona o que contienen lo buscado
            a_borrar = set(self.redaccion.anotaciones.get(p, []))
            for annot in list(pag.annots() or []):
                if annot.type[0] == pymupdf.PDF_ANNOT_REDACT:
                    continue
                if annot.xref in a_borrar or any(annot.rect.intersects(z) for z in zonas):
                    afectadas.append(tuple(annot.rect + (-2, -2, 2, 2)))
                    pag.delete_annot(annot)
            if zonas:
                separar_tj(doc, p)
                pag = doc[p]
            for z in zonas:
                pag.add_redact_annot(z, fill=o.relleno if o.relleno is not None else False)
            if zonas:
                pag.apply_redactions(
                    images=pymupdf.PDF_REDACT_IMAGE_PIXELS if o.borrar_imagenes else pymupdf.PDF_REDACT_IMAGE_NONE,
                    graphics=(pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED if o.borrar_dibujos
                              else pymupdf.PDF_REDACT_LINE_ART_NONE),
                    text=pymupdf.PDF_REDACT_TEXT_REMOVE)
            from pdf_studio.core.formularios import vaciar_campo
            for widget in list(pag.widgets() or []):
                if widget.xref in self.redaccion.campos.get(p, []):
                    afectadas.append(tuple(widget.rect + (-2, -2, 2, 2)))
                    vaciar_campo(doc, pag, widget.xref)
            self._zonas_afectadas[p] = afectadas

    def paginas_afectadas(self) -> set[int]:
        return set(self.redaccion.paginas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return dict(self._zonas_afectadas)

    def descripcion(self) -> str:
        n = sum(len(v) for v in self.redaccion.zonas.values())
        n += sum(len(v) for v in self.redaccion.anotaciones.values()) + sum(len(v) for v in self.redaccion.campos.values())
        return f"Redactar {n} zona{'s' if n != 1 else ''}"

    def verificar_guardado(self, ruta: Path, contrasena: str | None, paginas: dict[int, int]) -> list[str]:
        """Problemas encontrados en el archivo guardado (lista vacía = la redacción es real)."""
        return verificar_redaccion(ruta, contrasena, self.redaccion, paginas)


class LimpiarMetadatos(Operacion):
    """Borra título, autor, programas y fechas (diccionario Info y metadatos XMP)."""

    nombre = "Borrar metadatos"
    requiere_guardado_completo = True

    def aplicar(self, doc: pymupdf.Document) -> None:
        doc.set_metadata({})
        try:
            doc.del_xml_metadata()
        except Exception:
            pass

    def descripcion(self) -> str:
        return "Borrar metadatos"


# ---------------------------------------------------------------------- verificación
def _flujos_decodificados(doc: pymupdf.Document):
    """Cada objeto del archivo como texto (los flujos, descomprimidos)."""
    for xref in range(1, doc.xref_length()):
        try:
            yield doc.xref_object(xref, compressed=False)
        except Exception:
            pass
        try:
            if doc.xref_is_stream(xref):
                datos = doc.xref_stream(xref)
                if datos:
                    yield datos.decode("latin-1")
        except Exception:
            pass


def _variantes(texto: str) -> list[str]:
    """Formas en que un texto puede aparecer escrito «crudo» en un objeto PDF."""
    variantes = {texto}
    try:
        variantes.add(texto.encode("utf-16-be").decode("latin-1"))
    except UnicodeError:
        pass
    variantes.add(texto.encode("utf-16-be").hex())
    variantes.add(texto.encode("latin-1", errors="ignore").hex())
    return [v for v in variantes if len(v) >= 3]


def verificar_redaccion(ruta: Path, contrasena: str | None, redaccion: Redaccion,
                        paginas: dict[int, int] | None = None) -> list[str]:
    """Abre el archivo guardado y comprueba que:
    1. en las zonas redactadas no quede ninguna letra extraíble;
    2. lo redactado no aparezca en ningún lugar del archivo salvo donde siga visible a propósito
       (otras apariciones que el usuario no marcó).
    Los mensajes nunca incluyen el texto redactado."""
    problemas = []
    with pymupdf.open(ruta) as d:
        if d.needs_pass and not d.authenticate(contrasena or ""):
            return ["No se pudo abrir el archivo guardado para verificar la redacción."]
        for p, zonas in redaccion.zonas.items():
            final = paginas.get(p) if paginas is not None else p
            if final is None or final >= d.page_count:
                continue            # la página se eliminó después: su contenido ya no está
            pag = d[final]
            p = final
            quedan = 0
            for caracteres in _caracteres_por_linea(pag):
                for c, r in caracteres:
                    # Una letra «queda en la zona» si su centro cae adentro (las cajas de letras
                    # vecinas se superponen un poco con la zona sin estar redactadas)
                    centro = (r.tl + r.br) / 2
                    if not c.isspace() and any(centro in pymupdf.Rect(z) for z in zonas):
                        quedan += 1
            if quedan:
                problemas.append(f"Página {p + 1}: quedaron {quedan} letra(s) extraíbles dentro de una zona "
                                 "redactada.")
            for annot in pag.annots() or []:
                if any(annot.rect.intersects(pymupdf.Rect(z)) for z in zonas):
                    problemas.append(f"Página {p + 1}: quedó una anotación sobre una zona redactada.")
        visibles = "\n".join(pag.get_text() for pag in d)
        crudo = list(_flujos_decodificados(d))
        for i, texto in enumerate(redaccion.textos, 1):
            if texto in visibles:
                continue        # sigue visible en otro lugar que el usuario no marcó
            if any(v in objeto for objeto in crudo for v in _variantes(texto)):
                problemas.append(f"Lo redactado n.º {i} todavía aparece dentro del archivo (en un objeto "
                                 "oculto). No compartas este archivo.")
    return problemas
