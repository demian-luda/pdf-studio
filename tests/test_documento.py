"""Abrir, editar, deshacer/rehacer y guardar copia sin tocar el original."""
from __future__ import annotations

import pymupdf
import pytest

from pdf_studio.core.anotaciones import AgregarNota, AgregarResaltado, quads_de_palabras
from pdf_studio.core.documento import Documento, ErrorContrasena, ErrorDocumento, Operacion
from pdf_studio.core.paginas import RotarPagina
from tests.conftest import hash_archivo


@pytest.fixture
def doc_texto(copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    d = Documento(ruta)
    yield d
    d.cerrar()


def test_abrir_no_modifica_original(copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    antes = hash_archivo(ruta)
    d = Documento(ruta)
    d.ejecutar(RotarPagina(0, 90))
    d.guardar(ruta.with_name("salida.pdf"))
    d.cerrar()
    assert hash_archivo(ruta) == antes


def test_rotar_deshacer_rehacer(doc_texto):
    d = doc_texto
    assert not d.modificado and not d.puede_deshacer
    d.ejecutar(RotarPagina(1, 90))
    assert d.doc[1].rotation == 90
    assert d.modificado
    assert d.texto_deshacer == "Rotar página 2 a la derecha"
    d.deshacer()
    assert d.doc[1].rotation == 0
    assert not d.modificado
    assert d.puede_rehacer
    d.rehacer()
    assert d.doc[1].rotation == 90


def test_nueva_operacion_borra_rehacer(doc_texto):
    d = doc_texto
    d.ejecutar(RotarPagina(0, 90))
    d.ejecutar(RotarPagina(0, 90))
    d.deshacer()
    d.ejecutar(RotarPagina(2, -90))
    assert not d.puede_rehacer
    assert d.doc[0].rotation == 90
    assert d.doc[2].rotation == 270


def test_paginas_editadas_y_mapa(doc_texto):
    d = doc_texto
    d.ejecutar(RotarPagina(1, 90))
    d.ejecutar(AgregarNota(4, (100, 100), "Revisar"))
    assert d.paginas_editadas() == {1, 4}
    assert set(d.mapa_paginas()) == {0, 2, 3, 5}


def test_guardado_incremental_conserva_bytes_originales(doc_texto, tmp_path):
    d = doc_texto
    d.ejecutar(RotarPagina(1, 90))
    destino = tmp_path / "rotado.pdf"
    g = d.guardar(destino)
    assert g.incremental
    assert g.bytes_originales_intactos
    assert g.es_fiel
    assert g.fidelidad.excluidas == [1]
    assert sorted(g.fidelidad.comparadas) == [0, 2, 3, 4, 5]
    assert destino.read_bytes().startswith(d.ruta_original.read_bytes())
    with pymupdf.open(destino) as res:
        assert [p.rotation for p in res] == [0, 90, 0, 0, 0, 0]
    assert not d.modificado
    assert d.ultimo_guardado == destino.resolve()


def test_guardar_dos_veces_parte_siempre_del_original(doc_texto, tmp_path):
    d = doc_texto
    d.ejecutar(RotarPagina(0, 90))
    d.guardar(tmp_path / "a.pdf")
    d.ejecutar(RotarPagina(2, 90))
    d.deshacer()
    d.deshacer()
    d.ejecutar(RotarPagina(3, 180))
    d.guardar(tmp_path / "b.pdf")
    with pymupdf.open(tmp_path / "b.pdf") as res:
        assert [p.rotation for p in res] == [0, 0, 0, 180, 0, 0]


def test_guardar_sin_cambios_es_copia_exacta(doc_texto, tmp_path):
    g = doc_texto.guardar(tmp_path / "igual.pdf")
    assert (tmp_path / "igual.pdf").read_bytes() == doc_texto.ruta_original.read_bytes()
    assert g.es_fiel


def test_no_sobrescribe_original_sin_permiso(doc_texto):
    doc_texto.ejecutar(RotarPagina(0, 90))
    with pytest.raises(ErrorDocumento):
        doc_texto.guardar(doc_texto.ruta_original)


def test_sobrescribir_original_explicito(doc_texto):
    doc_texto.ejecutar(RotarPagina(0, 90))
    doc_texto.guardar(doc_texto.ruta_original, sobrescribir_original=True)
    with pymupdf.open(doc_texto.ruta_original) as res:
        assert res[0].rotation == 90


def test_ruta_sugerida(doc_texto):
    assert doc_texto.ruta_sugerida().name == "texto_varias_paginas_editado.pdf"


def test_resaltado_y_nota(doc_texto, tmp_path):
    d = doc_texto
    pag = d.doc[0]
    rect = pag.search_for("presupuesto")[0]
    quads = quads_de_palabras(pag, rect)
    assert len(quads) == 1
    d.ejecutar(AgregarResaltado(0, quads))
    d.ejecutar(AgregarNota(2, (300, 300), "Comentario de prueba"))
    assert [a.type[1] for a in d.doc[0].annots()] == ["Highlight"]
    g = d.guardar(tmp_path / "anotado.pdf")
    assert g.es_fiel
    with pymupdf.open(tmp_path / "anotado.pdf") as res:
        assert [a.type[1] for a in res[0].annots()] == ["Highlight"]
        assert [a.info["content"] for a in res[2].annots()] == ["Comentario de prueba"]
    d.deshacer()
    d.deshacer()
    assert list(d.doc[0].annots()) == []


def test_error_en_operacion_no_deja_cambios_a_medias(doc_texto):
    class Fallida(Operacion):
        def aplicar(self, doc):
            doc[0].set_rotation(90)
            raise RuntimeError("falla a propósito")

    with pytest.raises(RuntimeError):
        doc_texto.ejecutar(Fallida())
    assert doc_texto.doc[0].rotation == 0
    assert not doc_texto.puede_deshacer


def test_pdf_protegido(copia_pdf, tmp_path):
    ruta = copia_pdf("protegido.pdf")
    with pytest.raises(ErrorContrasena):
        Documento(ruta)
    with pytest.raises(ErrorContrasena):
        Documento(ruta, "mala")
    d = Documento(ruta, "1234")
    try:
        d.ejecutar(RotarPagina(1, 90))
        g = d.guardar(tmp_path / "protegido_editado.pdf")
        assert g.es_fiel and g.incremental
        with pymupdf.open(tmp_path / "protegido_editado.pdf") as res:
            assert res.needs_pass  # la protección se conserva
            assert res.authenticate("1234")
            assert res[1].rotation == 90
    finally:
        d.cerrar()


def test_archivo_inexistente_o_no_pdf(tmp_path):
    with pytest.raises(ErrorDocumento):
        Documento(tmp_path / "no_existe.pdf")
    falso = tmp_path / "falso.pdf"
    falso.write_text("esto no es un pdf")
    with pytest.raises(ErrorDocumento):
        Documento(falso)


@pytest.mark.parametrize("nombre", ["tablas.pdf", "imagen_fondo.pdf", "formulario.pdf",
                                    "escaneado.pdf", "fuentes_recortadas.pdf"])
def test_fidelidad_en_todo_el_set(copia_pdf, tmp_path, nombre):
    """Principio 3 sobre cada PDF de prueba: editar una página no altera las demás."""
    ruta = copia_pdf(nombre)
    antes = hash_archivo(ruta)
    d = Documento(ruta)
    try:
        d.ejecutar(RotarPagina(0, 90))
        g = d.guardar(tmp_path / f"ed_{nombre}")
        assert g.es_fiel, g.resumen()
        assert g.bytes_originales_intactos
    finally:
        d.cerrar()
    assert hash_archivo(ruta) == antes


def test_cerrar_borra_temporales(copia_pdf):
    d = Documento(copia_pdf("tablas.pdf"))
    carpeta = d.ruta_base.parent
    assert carpeta.is_dir()
    d.cerrar()
    assert not carpeta.exists()
