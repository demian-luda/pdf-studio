"""Formularios: detectar campos, completarlos y (opcionalmente) aplanarlos.

Completar cambia el valor de cada campo y regenera su apariencia; aplanar convierte los
campos en contenido fijo de la página (ya no se pueden editar). Las dos operaciones
declaran como zona editada el recuadro de cada campo: el resto de la página tiene que
quedar idéntico.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from pdf_studio.core.documento import ErrorDocumento, Limitacion, Operacion

TIPOS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "texto",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "casilla",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "opciones",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "lista",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "firma",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "boton",
}
EDITABLES = ("texto", "casilla", "radio", "opciones", "lista")
SOLO_LECTURA = 1          # bit 1 de /Ff
MULTILINEA = 1 << 12


@dataclass
class Campo:
    nombre: str
    tipo: str
    valor: object                          # str, o bool para las casillas
    pagina: int
    rects: list[tuple] = field(default_factory=list)
    opciones: list[str] = field(default_factory=list)   # opciones de listas y de grupos de radio
    solo_lectura: bool = False
    multilinea: bool = False

    @property
    def editable(self) -> bool:
        return self.tipo in EDITABLES and not self.solo_lectura


def _activo(widget) -> bool:
    return str(widget.field_value) not in ("Off", "", "False", "None")


def campos(doc: pymupdf.Document) -> list[Campo]:
    """Los campos del formulario en orden de aparición (un grupo de radio = un campo)."""
    por_nombre: dict[str, Campo] = {}
    orden: list[str] = []
    for pag in doc:
        for w in pag.widgets() or []:
            tipo = TIPOS.get(w.field_type, "otro")
            nombre = w.field_name or f"(sin nombre {w.xref})"
            c = por_nombre.get(nombre)
            if c is None:
                valor: object = w.field_value
                if tipo == "casilla":
                    valor = _activo(w)
                elif tipo == "radio":
                    valor = w.on_state() if _activo(w) else ""
                c = Campo(nombre, tipo, valor if valor is not None else "", pag.number,
                          solo_lectura=bool(w.field_flags & SOLO_LECTURA),
                          multilinea=bool(w.field_flags & MULTILINEA),
                          opciones=[str(v) for v in (w.choice_values or [])])
                por_nombre[nombre] = c
                orden.append(nombre)
            c.rects.append(tuple(w.rect))
            if tipo == "radio":
                estado = w.on_state()
                if estado not in (None, False, "Off") and str(estado) not in c.opciones:
                    c.opciones.append(str(estado))
                if _activo(w):
                    c.valor = str(estado)
    return [por_nombre[n] for n in orden]


def _objeto_del_campo(doc: pymupdf.Document, xref: int) -> int:
    """El objeto que guarda el valor (/V): el widget si tiene nombre propio, si no su padre."""
    actual = xref
    for _ in range(16):
        if doc.xref_get_key(actual, "T")[0] != "null":
            return actual
        tipo, valor = doc.xref_get_key(actual, "Parent")
        if tipo != "xref":
            return xref
        actual = int(valor.split()[0])
    return xref


def vaciar_campo(doc: pymupdf.Document, pag: pymupdf.Page, xref: int) -> None:
    """Deja vacío un campo de texto (PyMuPDF ignora field_value = "") y regenera su apariencia."""
    doc.xref_set_key(_objeto_del_campo(doc, xref), "V", "()")
    for w in pag.widgets() or []:
        if w.xref == xref:
            w.update()
            return


def tiene_formulario(doc: pymupdf.Document) -> bool:
    try:
        return any(True for pag in doc for _ in (pag.widgets() or []))
    except Exception:
        return False


class CompletarFormulario(Operacion):
    nombre = "Completar formulario"

    def __init__(self, valores: dict[str, object]):
        if not valores:
            raise ValueError("No cambió ningún campo.")
        self.valores = dict(valores)
        self._zonas: dict[int, list[tuple]] = {}

    def aplicar(self, doc: pymupdf.Document) -> None:
        self._zonas = {}
        encontrados = set()
        for pag in doc:
            for w in list(pag.widgets() or []):
                if w.field_name not in self.valores:
                    continue
                valor = self.valores[w.field_name]
                tipo = TIPOS.get(w.field_type)
                if w.field_flags & SOLO_LECTURA:
                    raise Limitacion(f"El campo «{w.field_name}» es de solo lectura.")
                if tipo == "casilla":
                    w.field_value = w.on_state() if valor else "Off"
                elif tipo == "radio":
                    w.field_value = w.on_state() if str(w.on_state()) == str(valor) else "Off"
                elif tipo in ("opciones", "lista"):
                    opciones = [str(v) for v in (w.choice_values or [])]
                    if valor not in opciones and not (w.field_flags & (1 << 18)):    # 1<<18 = editable
                        raise Limitacion(f"«{valor}» no es una opción de «{w.field_name}».")
                    w.field_value = valor
                elif tipo == "texto":
                    if not str(valor):
                        vaciar_campo(doc, pag, w.xref)
                        encontrados.add(w.field_name)
                        self._zonas.setdefault(pag.number, []).append(tuple(w.rect + (-2, -2, 2, 2)))
                        continue
                    w.field_value = str(valor)
                else:
                    continue
                w.update()
                encontrados.add(w.field_name)
                self._zonas.setdefault(pag.number, []).append(tuple(w.rect + (-2, -2, 2, 2)))
        faltan = set(self.valores) - encontrados
        if faltan:
            raise ErrorDocumento("No se encontraron los campos: " + ", ".join(sorted(faltan)))

    def paginas_afectadas(self) -> set[int]:
        return set(self._zonas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return dict(self._zonas)

    def descripcion(self) -> str:
        n = len(self.valores)
        return f"Completar {n} campo{'s' if n != 1 else ''} del formulario"


class AplanarFormulario(Operacion):
    """Pasa los campos a contenido fijo de la página: se ven igual pero ya no se editan."""

    nombre = "Aplanar formulario"

    def __init__(self):
        self._zonas: dict[int, list[tuple]] = {}

    def aplicar(self, doc: pymupdf.Document) -> None:
        self._zonas = {}
        for pag in doc:
            for w in pag.widgets() or []:
                self._zonas.setdefault(pag.number, []).append(tuple(w.rect + (-2, -2, 2, 2)))
        if not self._zonas:
            raise Limitacion("El documento no tiene campos de formulario.")
        doc.bake(annots=False, widgets=True)

    def paginas_afectadas(self) -> set[int]:
        return set(self._zonas)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return dict(self._zonas)

    def descripcion(self) -> str:
        return "Aplanar formulario"
