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

# En las páginas editadas, fuera de las zonas editadas, se toleran diferencias de hasta
# 2/255 por canal: es lo que cambia el antialiasing cuando MuPDF compone una página que
# pasó a tener transparencia (p. ej. una firma PNG). Las páginas NO editadas exigen 0.
TOLERANCIA_ZONAS = 2

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
    parciales: list[int] = field(default_factory=list)    # editadas, comparadas fuera de las zonas editadas
    enteras: list[int] = field(default_factory=list)      # editadas sin cambio visible (p. ej. OCR invisible)
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
            enteras = [d for d in self.diferencias if d.pagina not in self.parciales]
            zonas = [d for d in self.diferencias if d.pagina in self.parciales]
            partes = []
            if enteras:
                partes.append("¡Atención! Hay diferencias visuales en páginas que no se editaron: "
                              + ", ".join(str(d.pagina + 1) for d in enteras) + ".")
            if zonas:
                partes.append("¡Atención! En páginas editadas cambió algo fuera de la zona editada: "
                              + ", ".join(str(d.pagina + 1) for d in zonas) + ".")
            return " ".join(partes)
        if not self.comparadas and not self.parciales:
            return "No había páginas sin editar para comparar."
        partes = []
        if self.comparadas:
            partes.append(f"Fidelidad verificada: {len(self.comparadas)} páginas sin editar son idénticas "
                          f"al original (diferencia de píxeles = 0 a {self.dpi} dpi).")
        enteras = [p for p in self.parciales if p in self.enteras]
        con_zonas = len(self.parciales) - len(enteras)
        if enteras:
            partes.append(f"{'Además, l' if partes else 'Fidelidad verificada: l'}as {len(enteras)} página(s) "
                          f"editada(s) sin cambios visibles se ven idénticas al original (diferencia de "
                          f"píxeles = 0 a {self.dpi} dpi).")
        if con_zonas:
            partes.append(f"En {con_zonas} página(s) editada(s), todo lo que está fuera de "
                          "las zonas editadas también es idéntico.")
        return " ".join(partes)


def _abrir(ruta: Path | str, contrasena: str | None) -> pymupdf.Document:
    doc = pymupdf.open(ruta)
    if doc.needs_pass and not doc.authenticate(contrasena or ""):
        doc.close()
        raise ValueError(f"No se pudo abrir {ruta}: contraseña incorrecta.")
    return doc


def comparar_paginas(pag_a: pymupdf.Page, pag_b: pymupdf.Page, dpi: int,
                     excluir: list[tuple] | None = None) -> tuple[int, int, str]:
    """Devuelve (píxeles distintos, píxeles totales, motivo).
    `excluir`: zonas (rects sin rotar, en puntos) que no se comparan."""
    pix_a = pag_a.get_pixmap(dpi=dpi, alpha=False, annots=True)
    pix_b = pag_b.get_pixmap(dpi=dpi, alpha=False, annots=True)
    total = pix_a.width * pix_a.height
    if (pix_a.width, pix_a.height) != (pix_b.width, pix_b.height):
        return max(total, pix_b.width * pix_b.height), total, "cambió el tamaño de la página"
    if pix_a.samples_mv == pix_b.samples_mv:
        return 0, total, ""
    a = np.frombuffer(pix_a.samples_mv, dtype=np.uint8).reshape(pix_a.height, pix_a.width, pix_a.n)
    b = np.frombuffer(pix_b.samples_mv, dtype=np.uint8).reshape(pix_b.height, pix_b.width, pix_b.n)
    if excluir:
        mascara = np.any(np.abs(a.astype(np.int16) - b.astype(np.int16)) > TOLERANCIA_ZONAS, axis=2)
    else:
        mascara = np.any(a != b, axis=2)
    if excluir:
        escala = pymupdf.Matrix(dpi / 72, dpi / 72)
        for r in excluir:
            z = (pymupdf.Rect(r) * pag_b.rotation_matrix * escala).irect
            y0, y1 = max(0, z.y0 - 2), min(pix_a.height, z.y1 + 2)
            x0, x1 = max(0, z.x0 - 2), min(pix_a.width, z.x1 + 2)
            mascara[y0:y1, x0:x1] = False
    distintos = int(mascara.sum())
    return distintos, total, ("cambió el contenido fuera de la zona editada" if excluir
                              else "cambió el contenido visible")


def comparar_documentos(
    ruta_original: Path | str,
    ruta_resultado: Path | str,
    mapa: dict[int, int] | None = None,
    dpi: int = DPI_FIDELIDAD,
    contrasena: str | None = None,
    progreso: Progreso | None = None,
    parciales: dict[int, tuple[int, list[tuple]]] | None = None,
) -> ResultadoFidelidad:
    """Compara páginas del resultado contra el original.

    mapa: {índice en resultado: índice en original} con las páginas que NO se
    editaron. Si es None se comparan todas, una a una.
    parciales: {índice en resultado: (índice en original, zonas editadas)} para páginas
    editadas que se comparan fuera de esas zonas.
    """
    original = _abrir(ruta_original, contrasena)
    resultado = _abrir(ruta_resultado, contrasena)
    try:
        if mapa is None:
            mapa = {i: i for i in range(min(original.page_count, resultado.page_count))}
        res = ResultadoFidelidad(dpi=dpi)
        res.excluidas = [i for i in range(resultado.page_count) if i not in mapa]
        parciales = parciales or {}
        pares = [(i_res, i_orig, None) for i_res, i_orig in sorted(mapa.items())]
        pares += [(i_res, i_orig, zonas) for i_res, (i_orig, zonas) in sorted(parciales.items())
                  if i_res < resultado.page_count and i_orig < original.page_count]
        for n, (i_res, i_orig, zonas) in enumerate(pares):
            if progreso is not None and not progreso(n, len(pares)):
                res.completa = False
                break
            distintos, total, motivo = comparar_paginas(original[i_orig], resultado[i_res], dpi, zonas)
            (res.comparadas if zonas is None else res.parciales).append(i_res)
            if zonas is not None and not zonas:
                res.enteras.append(i_res)
            if distintos:
                res.diferencias.append(DiferenciaPagina(i_res, i_orig, distintos, total, motivo))
        if progreso is not None and res.completa:
            progreso(len(pares), len(pares))
        return res
    finally:
        original.close()
        resultado.close()
