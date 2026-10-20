"""Verificación de fidelidad visual (principio 3).

Se renderiza cada página NO editada del original y del resultado a la misma
resolución y se comparan los píxeles. La diferencia esperada es exactamente 0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pymupdf

from pdf_studio.config import DPI_FIDELIDAD

# progreso(hechas, total) -> True para seguir, False para cancelar
Progreso = Callable[[int, int], bool]


@dataclass
class DiferenciaPagina:
    pagina: int              # índice (desde 0) en el resultado
    pagina_original: int     # índice (desde 0) en el original
    pixeles_distintos: int
    pixeles_totales: int
    motivo: str = ""

    @property
    def porcentaje(self) -> float:
        return 100.0 * self.pixeles_distintos / max(1, self.pixeles_totales)


@dataclass
class ResultadoFidelidad:
    dpi: int
    comparadas: list[int] = field(default_factory=list)   # páginas del resultado comparadas
    excluidas: list[int] = field(default_factory=list)    # páginas editadas (no se comparan)
    diferencias: list[DiferenciaPagina] = field(default_factory=list)
    completa: bool = True                                  # False si el usuario canceló

    @property
    def ok(self) -> bool:
        return self.completa and not self.diferencias

    def resumen(self) -> str:
        if not self.completa:
            return (f"Verificación cancelada: se compararon {len(self.comparadas)} páginas; "
                    "el resto quedó sin verificar.")
        if self.diferencias:
            paginas = ", ".join(str(d.pagina + 1) for d in self.diferencias)
            return (f"¡Atención! Hay diferencias visuales en páginas que no se editaron: {paginas}.")
        if not self.comparadas:
            return "No había páginas sin editar para comparar."
        return (f"Fidelidad verificada: {len(self.comparadas)} páginas sin editar son idénticas "
                f"al original (diferencia de píxeles = 0 a {self.dpi} dpi).")


def _abrir(ruta: Path | str, contrasena: str | None) -> pymupdf.Document:
    doc = pymupdf.open(ruta)
    if doc.needs_pass and not doc.authenticate(contrasena or ""):
        doc.close()
        raise ValueError(f"No se pudo abrir {ruta}: contraseña incorrecta.")
    return doc


def comparar_paginas(pag_a: pymupdf.Page, pag_b: pymupdf.Page, dpi: int) -> tuple[int, int, str]:
    """Devuelve (píxeles distintos, píxeles totales, motivo)."""
    pix_a = pag_a.get_pixmap(dpi=dpi, alpha=False, annots=True)
    pix_b = pag_b.get_pixmap(dpi=dpi, alpha=False, annots=True)
    total = pix_a.width * pix_a.height
    if (pix_a.width, pix_a.height) != (pix_b.width, pix_b.height):
        return max(total, pix_b.width * pix_b.height), total, "cambió el tamaño de la página"
    if pix_a.samples_mv == pix_b.samples_mv:
        return 0, total, ""
    a = np.frombuffer(pix_a.samples_mv, dtype=np.uint8).reshape(pix_a.height, pix_a.width, pix_a.n)
    b = np.frombuffer(pix_b.samples_mv, dtype=np.uint8).reshape(pix_b.height, pix_b.width, pix_b.n)
    distintos = int(np.any(a != b, axis=2).sum())
    return distintos, total, "cambió el contenido visible"


def comparar_documentos(
    ruta_original: Path | str,
    ruta_resultado: Path | str,
    mapa: dict[int, int] | None = None,
    dpi: int = DPI_FIDELIDAD,
    contrasena: str | None = None,
    progreso: Progreso | None = None,
) -> ResultadoFidelidad:
    """Compara páginas del resultado contra el original.

    mapa: {índice en resultado: índice en original} con las páginas que NO se
    editaron. Si es None se comparan todas, una a una.
    """
    original = _abrir(ruta_original, contrasena)
    resultado = _abrir(ruta_resultado, contrasena)
    try:
        if mapa is None:
            mapa = {i: i for i in range(min(original.page_count, resultado.page_count))}
        res = ResultadoFidelidad(dpi=dpi)
        res.excluidas = [i for i in range(resultado.page_count) if i not in mapa]
        pares = sorted(mapa.items())
        for n, (i_res, i_orig) in enumerate(pares):
            if progreso is not None and not progreso(n, len(pares)):
                res.completa = False
                break
            distintos, total, motivo = comparar_paginas(original[i_orig], resultado[i_res], dpi)
            res.comparadas.append(i_res)
            if distintos:
                res.diferencias.append(DiferenciaPagina(i_res, i_orig, distintos, total, motivo))
        if progreso is not None and res.completa:
            progreso(len(pares), len(pares))
        return res
    finally:
        original.close()
        resultado.close()
