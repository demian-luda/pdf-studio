"""Operaciones sobre páginas (por ahora: rotar; la Fase 6 suma el resto)."""
from __future__ import annotations

import pymupdf

from pdf_studio.core.documento import Operacion


class RotarPagina(Operacion):
    nombre = "Rotar página"

    def __init__(self, pagina: int, grados: int):
        if grados % 90:
            raise ValueError("La rotación tiene que ser múltiplo de 90°.")
        self.pagina = pagina
        self.grados = grados

    def aplicar(self, doc: pymupdf.Document) -> None:
        pag = doc[self.pagina]
        pag.set_rotation((pag.rotation + self.grados) % 360)

    def paginas_afectadas(self) -> set[int]:
        return {self.pagina}

    def descripcion(self) -> str:
        sentido = "a la derecha" if self.grados > 0 else "a la izquierda"
        return f"Rotar página {self.pagina + 1} {sentido}"
