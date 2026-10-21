"""Operaciones sobre páginas: rotar, eliminar, reordenar, insertar (en blanco o desde otro
PDF) y extraer/separar en archivos nuevos.

Las páginas que solo cambian de lugar conservan su contenido intacto: el documento las
sigue (Operacion.reubicar) y la verificación de fidelidad las compara contra su página
original, en su nueva posición.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pymupdf

from pdf_studio.core.documento import ErrorDocumento, Limitacion, Operacion
from pdf_studio.core.fidelidad import comparar_paginas
from pdf_studio.core.rangos import describir_rango


def _paginas_texto(paginas) -> str:
    paginas = sorted(paginas)
    return f"página {paginas[0] + 1}" if len(paginas) == 1 else f"páginas {describir_rango(paginas)}"


class RotarPagina(Operacion):
    nombre = "Rotar página"

    def __init__(self, pagina: int | list[int], grados: int):
        if grados % 90:
            raise ValueError("La rotación tiene que ser múltiplo de 90°.")
        self.paginas = sorted({pagina} if isinstance(pagina, int) else set(pagina))
        if not self.paginas:
            raise ValueError("No hay páginas para rotar.")
        self.grados = grados

    @property
    def pagina(self) -> int:
        return self.paginas[0]

    def aplicar(self, doc: pymupdf.Document) -> None:
        for p in self.paginas:
            pag = doc[p]
            pag.set_rotation((pag.rotation + self.grados) % 360)

    def paginas_afectadas(self) -> set[int]:
        return set(self.paginas)

    def descripcion(self) -> str:
        sentido = "a la derecha" if self.grados > 0 else "a la izquierda"
        return f"Rotar {_paginas_texto(self.paginas)} {sentido}"


class EliminarPaginas(Operacion):
    nombre = "Eliminar páginas"

    def __init__(self, paginas: list[int], total: int):
        self.paginas = sorted(set(paginas))
        if not self.paginas:
            raise ValueError("No hay páginas para eliminar.")
        if len(self.paginas) >= total:
            raise Limitacion("No se pueden eliminar todas las páginas: el PDF tiene que tener al menos una.")

    def aplicar(self, doc: pymupdf.Document) -> None:
        doc.delete_pages(self.paginas)

    def reubicar(self, total: int) -> tuple[dict[int, int], int]:
        quedan = [i for i in range(total) if i not in self.paginas]
        return {viejo: nuevo for nuevo, viejo in enumerate(quedan)}, len(quedan)

    def descripcion(self) -> str:
        return f"Eliminar {_paginas_texto(self.paginas)}"


class ReordenarPaginas(Operacion):
    """`orden[i]` = índice (antes de la operación) de la página que queda en la posición i."""

    nombre = "Reordenar páginas"

    def __init__(self, orden: list[int]):
        if sorted(orden) != list(range(len(orden))):
            raise ValueError("El nuevo orden tiene que incluir cada página una sola vez.")
        if orden == sorted(orden):
            raise ValueError("El orden no cambió.")
        self.orden = list(orden)

    def aplicar(self, doc: pymupdf.Document) -> None:
        if doc.page_count != len(self.orden):
            raise Limitacion("La cantidad de páginas cambió: no se puede reordenar.")
        # Movimientos de a una (move_page) en vez de select(): no se reescribe el árbol entero
        actual = list(range(len(self.orden)))
        for destino, pagina in enumerate(self.orden):
            desde = actual.index(pagina)
            if desde != destino:
                doc.move_page(desde, destino)
                actual.insert(destino, actual.pop(desde))

    def reubicar(self, total: int) -> tuple[dict[int, int], int]:
        return {viejo: nuevo for nuevo, viejo in enumerate(self.orden)}, total

    def descripcion(self) -> str:
        movidas = [i for i, p in enumerate(self.orden) if i != p]
        if len(movidas) == 2 or len(self.orden) < 2:
            return "Reordenar páginas"
        return f"Reordenar {len(movidas)} páginas"


def mover_paginas(total: int, paginas: list[int], destino: int) -> list[int]:
    """Orden resultante de mover `paginas` (en su orden) para que queden antes de la página
    `destino` (índice antes de mover; `total` = al final)."""
    paginas = sorted(set(paginas))
    resto = [i for i in range(total) if i not in paginas]
    posicion = sum(1 for i in resto if i < destino)
    return resto[:posicion] + paginas + resto[posicion:]


class InsertarPaginasEnBlanco(Operacion):
    nombre = "Insertar página en blanco"

    def __init__(self, posicion: int, cantidad: int = 1, tamano: tuple[float, float] = (595.0, 842.0)):
        if cantidad < 1:
            raise ValueError("Hay que insertar al menos una página.")
        self.posicion = posicion
        self.cantidad = cantidad
        self.tamano = (float(tamano[0]), float(tamano[1]))

    def aplicar(self, doc: pymupdf.Document) -> None:
        for i in range(self.cantidad):
            doc.new_page(self.posicion + i, width=self.tamano[0], height=self.tamano[1])

    def reubicar(self, total: int) -> tuple[dict[int, int], int]:
        return ({i: (i if i < self.posicion else i + self.cantidad) for i in range(total)},
                total + self.cantidad)

    def paginas_afectadas(self) -> set[int]:
        return set(range(self.posicion, self.posicion + self.cantidad))

    def descripcion(self) -> str:
        n = self.cantidad
        return f"Insertar {n} página{'s' if n != 1 else ''} en blanco"


@dataclass
class FuentePDF:
    nombre: str              # nombre del archivo (para mostrar)
    datos: bytes             # el PDF completo, para que la operación sea reproducible
    paginas: list[int] | None = None    # None = todas
    contrasena: str | None = None

    @classmethod
    def desde_archivo(cls, ruta: str | Path, paginas: list[int] | None = None,
                      contrasena: str | None = None) -> "FuentePDF":
        ruta = Path(ruta)
        from pdf_studio.core.documento import validar_archivo
        validar_archivo(ruta)
        fuente = cls(ruta.name, ruta.read_bytes(), paginas, contrasena)
        with fuente.abrir() as d:
            if fuente.paginas is not None and any(p >= d.page_count for p in fuente.paginas):
                raise Limitacion(f"«{ruta.name}» tiene {d.page_count} páginas.")
        return fuente

    def abrir(self) -> pymupdf.Document:
        try:
            d = pymupdf.open("pdf", self.datos)
        except Exception as e:
            raise ErrorDocumento(f"«{self.nombre}» está dañado y no se puede abrir ({type(e).__name__}).") from e
        if d.needs_pass and not d.authenticate(self.contrasena or ""):
            d.close()
            raise ErrorDocumento(f"«{self.nombre}» está protegido con contraseña.")
        if d.page_count == 0:
            d.close()
            raise ErrorDocumento(f"«{self.nombre}» no tiene páginas.")
        return d

    def cantidad(self) -> int:
        if self.paginas is not None:
            return len(self.paginas)
        with self.abrir() as d:
            return d.page_count


class InsertarDesdePDF(Operacion):
    """Inserta páginas de otros PDFs (unir documentos) en una posición."""

    nombre = "Insertar páginas"

    def __init__(self, posicion: int, fuentes: list[FuentePDF]):
        if not fuentes:
            raise ValueError("No hay archivos para insertar.")
        self.posicion = posicion
        self.fuentes = list(fuentes)
        self._cantidad = sum(f.cantidad() for f in self.fuentes)

    def aplicar(self, doc: pymupdf.Document) -> None:
        destino = self.posicion
        for fuente in self.fuentes:
            with fuente.abrir() as d:
                paginas = fuente.paginas if fuente.paginas is not None else list(range(d.page_count))
                for p in paginas:
                    doc.insert_pdf(d, from_page=p, to_page=p, start_at=destino, links=True, annots=True)
                    destino += 1

    def reubicar(self, total: int) -> tuple[dict[int, int], int]:
        n = self._cantidad
        return {i: (i if i < self.posicion else i + n) for i in range(total)}, total + n

    def paginas_afectadas(self) -> set[int]:
        return set(range(self.posicion, self.posicion + self._cantidad))

    def descripcion(self) -> str:
        nombres = ", ".join(f.nombre for f in self.fuentes[:2]) + ("…" if len(self.fuentes) > 2 else "")
        return f"Insertar {self._cantidad} página{'s' if self._cantidad != 1 else ''} de {nombres}"


# ---------------------------------------------------------------------- extraer / separar
@dataclass
class ArchivoExtraido:
    ruta: Path
    paginas: list[int]
    identico: bool           # el render de cada página coincide con el del documento


def extraer_paginas(doc: pymupdf.Document, grupos: list[list[int]], destinos: list[Path],
                    dpi: int = 100, sobrescribir: bool = False,
                    proteger: list[Path] | None = None) -> list[ArchivoExtraido]:
    """Copia cada grupo de páginas a un PDF nuevo (sin re-renderizar nada) y verifica que
    cada página extraída se vea igual que en el documento. Sin `sobrescribir`, si algún
    destino existe se lanza ErrorDocumento antes de escribir nada. Los archivos de `proteger`
    (el original) nunca se pisan."""
    if len(grupos) != len(destinos):
        raise ValueError("Cada grupo de páginas necesita un archivo de destino.")
    protegidos = {Path(p).resolve() for p in proteger or []}
    for destino in destinos:
        if Path(destino).resolve() in protegidos:
            raise Limitacion(f"«{Path(destino).name}» es el archivo original: elegí otro nombre.")
        if Path(destino).exists() and not sobrescribir:
            raise Limitacion(f"Ya existe «{Path(destino).name}»: elegí otro nombre o carpeta.")
    resultados = []
    for grupo, destino in zip(grupos, destinos):
        nuevo = pymupdf.open()
        try:
            for p in grupo:
                nuevo.insert_pdf(doc, from_page=p, to_page=p, links=True, annots=True)
            identico = all(comparar_paginas(doc[p], nuevo[i], dpi)[0] == 0 for i, p in enumerate(grupo))
            nuevo.save(destino, garbage=3, deflate=True)
        finally:
            nuevo.close()
        resultados.append(ArchivoExtraido(Path(destino), list(grupo), identico))
    return resultados


def nombres_libres(carpeta: Path, base: str, grupos: list[list[int]]) -> list[Path]:
    """«base_p1-3.pdf», «base_p4.pdf»…; si alguno existe, se agrega « (2)», « (3)»…"""
    rutas = []
    for grupo in grupos:
        parte = describir_rango(grupo).replace(", ", "_")
        ruta = Path(carpeta) / f"{base}_p{parte}.pdf"
        n = 2
        while ruta.exists() or ruta in rutas:
            ruta = Path(carpeta) / f"{base}_p{parte} ({n}).pdf"
            n += 1
        rutas.append(ruta)
    return rutas


def grupos_cada(total: int, cada: int) -> list[list[int]]:
    """Separar cada N páginas: [[0..N-1], [N..2N-1], …]."""
    if cada < 1:
        raise ValueError("La cantidad de páginas por archivo tiene que ser al menos 1.")
    return [list(range(i, min(total, i + cada))) for i in range(0, total, cada)]
