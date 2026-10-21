"""Fase 10: procesamiento por lotes (carpeta → resultados verificados, registro, sin pisar
nada, errores que no frenan el lote) y su interfaz."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pymupdf
import pytest

from pdf_studio import procesos
from pdf_studio.core import lotes
from pdf_studio.core.documento import Documento, Limitacion
from pdf_studio.core.marcas import OpcionesMarca, OpcionesNumeracion
from tests.conftest import hash_archivo
from tests.test_conversion import requiere_tesseract


def _pdf(ruta: Path, paginas: int = 2, texto: str = "Informe") -> Path:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as doc:
        for i in range(paginas):
            pag = doc.new_page()
            pag.insert_text((72, 100), f"{texto} — página {i + 1}", fontsize=14)
            pag.draw_rect(pymupdf.Rect(72, 130, 400, 260), color=(0.2, 0.4, 0.6), width=1)
        doc.save(ruta)
    return ruta


@pytest.fixture
def carpeta(tmp_path) -> Path:
    c = tmp_path / "entrada"
    _pdf(c / "a.pdf")
    _pdf(c / "b.pdf", 3, "Acta")
    _pdf(c / "sub" / "c.pdf", 1, "Nota")
    (c / "notas.txt").write_text("no es un PDF", encoding="utf-8")
    return c


def _marca() -> dict:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in asdict(OpcionesMarca(texto="COPIA")).items()
            if k != "imagen"}


def _numeracion() -> dict:
    return {k: (v.value if hasattr(v, "value") else v) for k, v in asdict(OpcionesNumeracion()).items()}


def _texto(ruta: Path) -> str:
    with pymupdf.open(ruta) as doc:
        return "\n".join(p.get_text() for p in doc)


def test_listar_pdfs(carpeta):
    assert [p.name for p in lotes.listar_pdfs(carpeta)] == ["a.pdf", "b.pdf"]
    assert [p.name for p in lotes.listar_pdfs(carpeta, True)] == ["a.pdf", "b.pdf", "c.pdf"]
    _pdf(carpeta / "procesados" / "a.pdf")
    assert len(lotes.listar_pdfs(carpeta, True, excluir=carpeta / "procesados")) == 3
    with pytest.raises(Limitacion):
        lotes.listar_pdfs(carpeta / "no_existe")


def test_lote_con_marca_numeracion_y_compresion(carpeta):
    huellas = {p: hash_archivo(p) for p in carpeta.rglob("*.pdf")}
    opciones = lotes.OpcionesLote(str(carpeta), subcarpetas=True, marca=_marca(), numeracion=_numeracion(),
                                  comprimir="bajo")
    avances = []
    informe = lotes.procesar_lote(opciones, lambda h, t, a, r: avances.append((h, t, a)))
    assert informe.cuenta(lotes.OK) == 3 and not informe.cancelado
    assert avances[0] == (0, 3, "a.pdf") and avances[-1] == (3, 3, None)
    destino = carpeta / "procesados"
    for nombre, paginas in (("a.pdf", 2), ("b.pdf", 3), ("sub/c.pdf", 1)):
        salida = destino / nombre
        assert salida.is_file()
        texto = _texto(salida)
        assert "COPIA" in texto and f"Página 1 de {paginas}" in texto
    assert all(hash_archivo(p) == h for p, h in huellas.items())          # los originales intactos
    registro = Path(informe.registro).read_text(encoding="utf-8")
    assert "Tareas: marca de agua, numeración, compresión" in registro
    assert "[OK] a.pdf" in registro and "3 archivos, 3 ok." in registro


def test_nunca_pisa_resultados_anteriores(carpeta):
    opciones = lotes.OpcionesLote(str(carpeta), marca=_marca())
    lotes.procesar_lote(opciones)
    segundo = lotes.procesar_lote(opciones)
    assert (carpeta / "procesados" / "a (2).pdf").is_file()
    assert any(s.endswith("a (2).pdf") for r in segundo.resultados for s in r.salidas)
    assert len(list((carpeta / "procesados").glob("registro_lote_*.txt"))) == 2


def test_misma_carpeta_de_destino_usa_otro_nombre(carpeta):
    opciones = lotes.OpcionesLote(str(carpeta), destino=str(carpeta), numeracion=_numeracion())
    informe = lotes.procesar_lote(opciones)
    assert (carpeta / "a_procesado.pdf").is_file() and informe.cuenta(lotes.OK) == 2


def test_archivos_problematicos_no_frenan_el_lote(carpeta):
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 100), "secreto")
        doc.save(carpeta / "protegido.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    (carpeta / "roto.pdf").write_bytes(b"%PDF-1.7\nesto no es un PDF de verdad")
    informe = lotes.procesar_lote(lotes.OpcionesLote(str(carpeta), marca=_marca()))
    por_nombre = {r.archivo: r for r in informe.resultados}
    assert por_nombre["protegido.pdf"].estado == lotes.OMITIDO
    assert por_nombre["roto.pdf"].estado in (lotes.ERROR, lotes.OMITIDO)
    assert por_nombre["a.pdf"].estado == por_nombre["b.pdf"].estado == lotes.OK
    assert not (carpeta / "procesados" / "roto.pdf").exists()


def test_si_falla_la_fidelidad_no_se_guarda(carpeta, monkeypatch):
    original = Documento.preparar_guardado

    def infiel(self, progreso=None):
        g = original(self, progreso)
        g.estructura_ok = False
        return g
    monkeypatch.setattr(Documento, "preparar_guardado", infiel)
    informe = lotes.procesar_lote(lotes.OpcionesLote(str(carpeta), marca=_marca()))
    assert informe.cuenta(lotes.ERROR) == 2
    assert all("fidelidad" in r.mensajes[-1] for r in informe.resultados)
    assert not (carpeta / "procesados" / "a.pdf").exists()


def test_sin_tareas_o_sin_pdfs(tmp_path, carpeta):
    with pytest.raises(Limitacion, match="al menos una tarea"):
        lotes.procesar_lote(lotes.OpcionesLote(str(carpeta)))
    (tmp_path / "vacia").mkdir()
    with pytest.raises(Limitacion, match="No hay PDFs"):
        lotes.procesar_lote(lotes.OpcionesLote(str(tmp_path / "vacia"), marca=_marca()))


def test_tarea_en_otro_proceso_informa_el_avance(carpeta, tmp_path):
    progreso = tmp_path / "progreso.json"
    pedido = {"opciones": lotes.OpcionesLote(str(carpeta), numeracion=_numeracion()).a_dict(),
              "progreso": str(progreso)}
    (tmp_path / "p.json").write_text(json.dumps(pedido), encoding="utf-8")
    procesos.ejecutar("lotes", tmp_path / "p.json", tmp_path / "r.json")
    salida = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert salida["ok"], salida
    informe = lotes.InformeLote.desde_dict(salida["resultado"])
    assert informe.cuenta(lotes.OK) == 2
    ultimo = json.loads(progreso.read_text(encoding="utf-8"))
    assert ultimo["hechas"] == ultimo["total"] == 2 and "Anterior: b.pdf — OK" in ultimo["etiqueta"]


@requiere_tesseract
def test_lote_con_ocr_y_word_editable(tmp_path):
    from tests import sops_sinteticos as sop
    entrada = tmp_path / "escaneos"
    entrada.mkdir()
    sop.crear_sop(tmp_path / "sop.pdf")
    sop.escanear(tmp_path / "sop.pdf", entrada / "sop_escaneado.pdf")
    _pdf(entrada / "digital.pdf")
    informe = lotes.procesar_lote(lotes.OpcionesLote(str(entrada), ocr=True, editable=True))
    por_nombre = {r.archivo: r for r in informe.resultados}
    escaneo = por_nombre["sop_escaneado.pdf"]
    assert escaneo.estado == lotes.OK, escaneo.mensajes
    assert (entrada / "procesados" / "sop_escaneado.pdf").is_file()
    assert (entrada / "procesados" / "sop_escaneado.docx").is_file()
    assert sop.CODIGO in _texto(entrada / "procesados" / "sop_escaneado.pdf")
    digital = por_nombre["digital.pdf"]
    assert digital.estado == lotes.OMITIDO and "ya tenía texto" in digital.mensajes[0]


# ---------------------------------------------------------------------- interfaz
def test_dialogo_lotes(qtbot, carpeta):
    from pdf_studio.ui.lotes import DialogoLotes
    d = DialogoLotes(str(carpeta), hay_ocr=False)
    qtbot.addWidget(d)
    assert d.cantidad.text() == "2 PDFs" and not d.boton_procesar.isEnabled()
    assert not d.ocr.isEnabled() and "Tesseract" in d.ocr.text()
    d.subcarpetas.setChecked(True)
    assert d.cantidad.text() == "3 PDFs"
    d.numerar.setChecked(True)
    assert d.boton_procesar.isEnabled()
    d.comprimir.setChecked(True)
    o = d.resultado()
    assert o.subcarpetas and o.numeracion["formato"] == "Página {n} de {total}" and o.comprimir == "medio"
    assert o.marca is None and not o.ocr and o.carpeta_destino == carpeta / "procesados"


def test_informe_lote(qtbot, carpeta):
    from pdf_studio.ui.lotes import DialogoInformeLote
    informe = lotes.procesar_lote(lotes.OpcionesLote(str(carpeta), marca=_marca()))
    d = DialogoInformeLote(informe)
    qtbot.addWidget(d)
    assert d.lista.count() == 2 and "a.pdf — OK" in d.lista.item(0).text()


def test_ventana_procesa_por_lotes(ventana, carpeta, monkeypatch, qtbot):
    vistos = []
    monkeypatch.setattr(ventana, "_dialogo_lotes", lambda inicial, hay_ocr: lotes.OpcionesLote(
        str(carpeta), subcarpetas=True, numeracion=_numeracion()))
    monkeypatch.setattr(ventana, "_mostrar_informe_lote", lambda informe: vistos.append(informe))
    assert ventana.procesar_por_lotes()
    qtbot.waitUntil(lambda: bool(vistos) or bool(ventana.mensajes), timeout=120_000)
    assert vistos, ventana.mensajes
    assert vistos[0].cuenta(lotes.OK) == 3 and (carpeta / "procesados" / "sub" / "c.pdf").is_file()
