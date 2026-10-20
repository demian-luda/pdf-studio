"""Verificación de fidelidad: detecta cualquier cambio visual en páginas no editadas."""
from __future__ import annotations

import shutil

import pymupdf
import pytest

from pdf_studio.core.documento import Documento, ErrorFidelidad, Operacion
from pdf_studio.core.fidelidad import comparar_documentos
from pdf_studio.core.paginas import RotarPagina


def test_documentos_identicos(copia_pdf, tmp_path):
    a = copia_pdf("imagen_fondo.pdf")
    b = tmp_path / "copia.pdf"
    shutil.copy2(a, b)
    res = comparar_documentos(a, b, dpi=72)
    assert res.ok
    assert res.comparadas == [0, 1]


def test_detecta_cambio_minimo(copia_pdf, tmp_path):
    a = copia_pdf("texto_varias_paginas.pdf")
    b = tmp_path / "cambiado.pdf"
    with pymupdf.open(a) as doc:
        # Un punto casi invisible en la página 3
        doc[2].draw_rect(pymupdf.Rect(300, 300, 301, 301), color=(0.9, 0.9, 0.9), width=0.3)
        doc.save(b)
    res = comparar_documentos(a, b, dpi=72)
    assert not res.ok
    assert [d.pagina for d in res.diferencias] == [2]
    assert res.diferencias[0].pixeles_distintos > 0
    assert "3" in res.resumen()


def test_paginas_excluidas_no_se_comparan(copia_pdf, tmp_path):
    a = copia_pdf("texto_varias_paginas.pdf")
    b = tmp_path / "cambiado.pdf"
    with pymupdf.open(a) as doc:
        doc[2].draw_rect(pymupdf.Rect(100, 100, 200, 200), fill=(1, 0, 0))
        doc.save(b)
    mapa = {i: i for i in range(6) if i != 2}
    res = comparar_documentos(a, b, mapa=mapa, dpi=72)
    assert res.ok
    assert res.excluidas == [2]


def test_cancelar_deja_verificacion_incompleta(copia_pdf, tmp_path):
    a = copia_pdf("texto_varias_paginas.pdf")
    res = comparar_documentos(a, a, dpi=72, progreso=lambda hechas, total: hechas < 2)
    assert not res.completa
    assert not res.ok
    assert len(res.comparadas) == 2


def test_operacion_que_toca_paginas_no_declaradas_se_bloquea(copia_pdf, tmp_path):
    """Si una operación altera una página que no declaró, el guardado se frena."""

    class Tramposa(Operacion):
        def aplicar(self, doc):
            doc[0].set_rotation(90)
            doc[3].draw_rect(pymupdf.Rect(50, 50, 150, 150), fill=(0, 0, 1))

        def paginas_afectadas(self):
            return {0}

    d = Documento(copia_pdf("texto_varias_paginas.pdf"))
    try:
        d.ejecutar(Tramposa())
        destino = tmp_path / "no_debe_existir.pdf"
        with pytest.raises(ErrorFidelidad) as info:
            d.guardar(destino)
        assert not destino.exists()
        assert [x.pagina for x in info.value.guardado.fidelidad.diferencias] == [3]
        # Forzando, se guarda igual (el usuario lo decidió)
        d.guardar(destino, forzar=True)
        assert destino.exists()
    finally:
        d.cerrar()


def test_rotacion_cambia_tamano_y_se_detecta(copia_pdf, tmp_path):
    a = copia_pdf("texto_varias_paginas.pdf")
    d = Documento(a)
    try:
        d.ejecutar(RotarPagina(0, 90))
        g = d.preparar_guardado()
        # Comparando TODO (sin excluir la editada) la página 1 debe aparecer distinta
        res = comparar_documentos(a, g.ruta_temporal, dpi=72)
        assert [x.pagina for x in res.diferencias] == [0]
        assert res.diferencias[0].motivo == "cambió el tamaño de la página"
    finally:
        d.cerrar()
