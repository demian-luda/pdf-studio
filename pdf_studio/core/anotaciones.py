"""Herramientas de lectura que modifican el PDF: resaltados y notas.

Las coordenadas que reciben estas operaciones están en el espacio de la página
SIN rotar (el mismo que devuelven get_text y search_for).
"""
from __future__ import annotations

import pymupdf

from pdf_studio import NOMBRE_APP
from pdf_studio.core.documento import Operacion

AMARILLO = (1.0, 0.92, 0.23)

# Apariencia de las notas: un globito chico (unos 11 x 10 pt) y semitransparente, arriba a la
# izquierda del área de 16 x 16 pt que exige MuPDF. El ícono "Note" estándar tapaba el texto.
APARIENCIA_NOTA = (b"q /G gs 0.95 0.76 0.31 rg 0.62 0.45 0.08 RG 0.5 w "
                   b"3.5 15.5 m 11.5 15.5 l 12.6 15.5 13.5 14.6 13.5 13.5 c 13.5 9.5 l 13.5 8.4 12.6 7.5 11.5 7.5 c "
                   b"6.5 7.5 l 4.5 5.5 l 4.5 7.5 l 3.4 7.5 2.5 8.4 2.5 9.5 c 2.5 13.5 l 2.5 14.6 3.4 15.5 4.5 15.5 c "
                   b"h B Q")


class AgregarResaltado(Operacion):
    nombre = "Resaltar"

    def __init__(self, pagina: int, quads: list[tuple[float, ...]], color=AMARILLO):
        if not quads:
            raise ValueError("No hay texto para resaltar.")
        self.pagina = pagina
        # Cada quad como 8 números (ul, ur, ll, lr) para no guardar objetos de PyMuPDF
        self.quads = [tuple(float(v) for v in q) for q in quads]
        self.color = color

    def aplicar(self, doc: pymupdf.Document) -> None:
        quads = [pymupdf.Quad(q[0:2], q[2:4], q[4:6], q[6:8]) for q in self.quads]
        pag = doc[self.pagina]  # la página tiene que seguir viva mientras se usa la anotación
        annot = pag.add_highlight_annot(quads=quads)
        annot.set_colors(stroke=self.color)
        annot.set_info(title=NOMBRE_APP)
        annot.update()

    def paginas_afectadas(self) -> set[int]:
        return {self.pagina}

    def descripcion(self) -> str:
        return f"Resaltar texto en página {self.pagina + 1}"


class AgregarNota(Operacion):
    nombre = "Nota"

    def __init__(self, pagina: int, punto: tuple[float, float], texto: str):
        self.pagina = pagina
        self.punto = (float(punto[0]), float(punto[1]))
        self.texto = texto

    def aplicar(self, doc: pymupdf.Document) -> None:
        pag = doc[self.pagina]
        annot = pag.add_text_annot(self.punto, self.texto, icon="Comment")
        annot.set_info(title=NOMBRE_APP)
        annot.update()
        # Apariencia propia, más chica y discreta que la de MuPDF
        tipo, valor = doc.xref_get_key(annot.xref, "AP/N")
        if tipo == "xref":
            xref = int(valor.split()[0])
            doc.update_stream(xref, APARIENCIA_NOTA)
            doc.xref_set_key(xref, "BBox", "[0 0 16 16]")
            doc.xref_set_key(xref, "Matrix", "[1 0 0 1 0 0]")
            doc.xref_set_key(xref, "Resources", "<</ExtGState<</G<</ca 0.9/CA 0.9>>>>>>")

    def paginas_afectadas(self) -> set[int]:
        return {self.pagina}

    def descripcion(self) -> str:
        return f"Nota en página {self.pagina + 1}"


def quads_de_palabras(pagina: pymupdf.Page, rect: pymupdf.Rect) -> list[tuple[float, ...]]:
    """Quads (uno por línea) de las palabras que tocan el rectángulo, en coordenadas sin rotar."""
    palabras = [w for w in pagina.get_text("words") if pymupdf.Rect(w[:4]).intersects(rect)]
    # Agrupa por (bloque, línea) para generar un quad por línea, como un resaltado manual
    lineas: dict[tuple[int, int], pymupdf.Rect] = {}
    for x0, y0, x1, y1, _txt, bloque, linea, _n in palabras:
        clave = (bloque, linea)
        r = pymupdf.Rect(x0, y0, x1, y1)
        lineas[clave] = lineas[clave] | r if clave in lineas else r
    quads = []
    for r in lineas.values():
        q = r.quad
        quads.append((q.ul.x, q.ul.y, q.ur.x, q.ur.y, q.ll.x, q.ll.y, q.lr.x, q.lr.y))
    return quads
