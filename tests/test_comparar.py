"""Fase 8: comparar versiones (texto, visual, informe y la ventana de comparación)."""
from __future__ import annotations

import json

import numpy as np
import pymupdf
import pytest

from pdf_studio.core import comparar as cp
from pdf_studio.core.documento import ErrorDocumento
from tests.conftest import DIR_PDFS, hash_archivo

ORIGINAL = DIR_PDFS / "texto_varias_paginas.pdf"


@pytest.fixture
def version_b(tmp_path):
    """Copia de texto_varias_paginas.pdf con cambios conocidos."""
    d = pymupdf.open(ORIGINAL)
    pag = d[1]
    palabra = next(w for w in pag.get_text("words") if w[4] == "presupuesto")
    pag.add_redact_annot(pymupdf.Rect(palabra[:4]))
    pag.apply_redactions()
    pag.insert_text((72, 780), "Texto agregado al final", fontsize=11)
    d[3].draw_rect(pymupdf.Rect(400, 700, 500, 750), color=(1, 0, 0), fill=(1, 0, 0))
    d.delete_page(5)
    ruta = tmp_path / "version_b.pdf"
    d.save(ruta)
    d.close()
    return ruta


def test_documentos_iguales():
    r = cp.comparar_archivos(ORIGINAL, ORIGINAL)
    assert r.iguales and r.cambios == [] and r.visuales == []
    assert "son iguales" in r.resumen()


def test_diferencias_de_texto_y_visuales(version_b):
    antes = hash_archivo(ORIGINAL)
    r = cp.comparar_archivos(ORIGINAL, version_b)
    quitados = [c for c in r.cambios if c.tipo == "quitado"]
    agregados = [c for c in r.cambios if c.tipo == "agregado"]
    assert any(c.pagina == 1 and c.texto == "presupuesto" for c in quitados)
    assert any(c.pagina == 1 and c.texto == "Texto agregado al final" for c in agregados)
    assert any(c.pagina == 5 and c.texto.startswith("Capítulo 6") for c in quitados)   # página borrada
    assert r.paginas_a == 6 and r.paginas_b == 5
    # El rectángulo rojo de la página 4 aparece como diferencia visual en ese lugar
    visual = r.visual_de(3)
    assert visual is not None and any(pymupdf.Rect(z).intersects(pymupdf.Rect(400, 700, 500, 750))
                                      for z in visual.zonas)
    assert r.visual_de(0) is None and r.visual_de(2) is None        # páginas sin cambios
    assert r.paginas_con_cambios() == [1, 3, 5]
    assert "Cantidad de páginas: 6 → 5" in r.resumen()
    assert hash_archivo(ORIGINAL) == antes


def test_cajas_del_texto_quitado(version_b):
    r = cp.comparar_archivos(ORIGINAL, version_b)
    c = next(c for c in r.cambios if c.texto == "presupuesto")
    with pymupdf.open(ORIGINAL) as d:
        original = next(w for w in d[1].get_text("words") if w[4] == "presupuesto")
    assert tuple(c.rects[0]) == pytest.approx(tuple(original[:4]), abs=0.5)


def test_paginas_rotadas_y_de_otro_tamano(tmp_path):
    d = pymupdf.open(ORIGINAL)
    d[0].set_rotation(90)
    d.new_page(1, width=300, height=300)
    d.save(tmp_path / "b.pdf")
    d.close()
    r = cp.comparar_archivos(ORIGINAL, tmp_path / "b.pdf")
    assert 0 in r.tamanos_distintos                  # rotada: otro tamaño visible
    assert 1 in r.tamanos_distintos


def test_zonas_agrupan_celdas_vecinas():
    mascara = np.zeros((60, 60), dtype=bool)
    mascara[5:8, 5:8] = True
    mascara[9:11, 9:11] = True          # vecina: misma zona
    mascara[40:42, 40:42] = True        # lejos: otra zona
    zonas = cp._zonas(mascara)
    assert len(zonas) == 2


def test_ida_y_vuelta_json(version_b):
    r = cp.comparar_archivos(ORIGINAL, version_b)
    r2 = cp.ResultadoComparacion.desde_dict(json.loads(json.dumps(r.a_dict())))
    assert r2.resumen() == r.resumen() and r2.paginas_con_cambios() == r.paginas_con_cambios()


def test_archivo_invalido(tmp_path):
    vacio = tmp_path / "vacio.pdf"
    vacio.write_bytes(b"")
    with pytest.raises(ErrorDocumento, match="vacío"):
        cp.comparar_archivos(ORIGINAL, vacio)
    with pytest.raises(ErrorDocumento, match="contraseña"):
        cp.comparar_archivos(ORIGINAL, DIR_PDFS / "protegido.pdf")
    r = cp.comparar_archivos(DIR_PDFS / "protegido.pdf", DIR_PDFS / "protegido.pdf", "1234", "1234")
    assert r.iguales


def test_informe_pdf(version_b, tmp_path):
    r = cp.comparar_archivos(ORIGINAL, version_b)
    destino = tmp_path / "informe.pdf"
    paginas = cp.exportar_informe(r, ORIGINAL, version_b, destino)
    assert paginas == 1 + len(r.paginas_con_cambios())
    with pymupdf.open(destino) as d:
        portada = d[0].get_text()
        assert "Informe de diferencias" in portada and "texto_varias_paginas.pdf" in portada
        assert "Páginas con cambios: 2, 4, 6" in portada
        assert "− presupuesto" in d[1].get_text() and "+ Texto agregado al final" in d[1].get_text()
        assert "no tiene esta página" in d[3].get_text()
    assert destino.stat().st_size < 1_500_000


def test_tareas_en_otro_proceso(version_b, tmp_path):
    import subprocess
    from pdf_studio import procesos
    pedido, resultado = tmp_path / "p.json", tmp_path / "r.json"
    pedido.write_text(json.dumps({"a": str(ORIGINAL), "b": str(version_b), "nombre_a": "antes.pdf"}),
                      encoding="utf-8")
    programa, argumentos = procesos.comando("comparar", pedido, resultado)
    subprocess.run([programa, *argumentos], env=procesos.entorno(), check=True, timeout=120)
    datos = json.loads(resultado.read_text(encoding="utf-8"))
    assert datos["ok"]
    r = cp.ResultadoComparacion.desde_dict(datos["resultado"])
    assert r.nombre_a == "antes.pdf" and r.paginas_con_cambios() == [1, 3, 5]
    pedido.write_text(json.dumps({"resultado": datos["resultado"], "a": str(ORIGINAL), "b": str(version_b),
                                  "destino": str(tmp_path / "inf.pdf")}), encoding="utf-8")
    programa, argumentos = procesos.comando("informe", pedido, resultado)
    subprocess.run([programa, *argumentos], env=procesos.entorno(), check=True, timeout=120)
    assert json.loads(resultado.read_text(encoding="utf-8"))["resultado"]["paginas"] == 4


# ---------------------------------------------------------------------- interfaz
def test_dialogo_comparar(qtbot, version_b):
    from pdf_studio.ui.comparar import DialogoComparar
    r = cp.comparar_archivos(ORIGINAL, version_b)
    d = DialogoComparar(r, ORIGINAL, version_b)
    qtbot.addWidget(d)
    d.show()
    assert d.solo_cambios.isChecked() and d.combo.count() == 3
    assert d.pagina == 1
    assert not d.vista_a.etiqueta.pixmap().isNull()
    d._saltar(1)
    assert d.pagina == 3
    d._saltar(1)
    assert d.pagina == 5 and "no tiene esta página" in d.vista_b.etiqueta.text()
    d.solo_cambios.setChecked(False)
    assert d.combo.count() == 6
    d.lista.setCurrentRow(0)
    d._item_elegido(d.lista.item(0))
    assert d.pagina == 1
    vistos = []
    d.exportar.connect(lambda: vistos.append(True))
    d.boton_exportar.click()
    assert vistos
    d.close()


def test_comparar_desde_la_ventana(ventana, copia_pdf, version_b, tmp_path, monkeypatch, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    mostrados = []
    monkeypatch.setattr(ventana, "_pedir_ruta_comparar", lambda titulo="": str(version_b))
    monkeypatch.setattr(ventana, "_mostrar_comparacion", lambda *a: mostrados.append(a))
    assert ventana.comparar_versiones()
    assert ventana.proceso_tarea.ocupado
    ventana.visor.acercar()                         # la interfaz sigue respondiendo
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
    assert mostrados
    resultado = mostrados[0][0]
    assert resultado.paginas_con_cambios() == [1, 3, 5]
    # Exportar el informe desde lo que muestra la ventana
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "informe.pdf"))
    assert ventana.exportar_informe(*mostrados[0])
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
    assert (tmp_path / "informe.pdf").is_file()
    assert "informe" in ventana.mensajes[-1][1].lower()


def test_comparar_con_cambios_sin_guardar_usa_el_estado_actual(ventana, copia_pdf, monkeypatch, qtbot):
    from pdf_studio.core.paginas import EliminarPaginas
    ruta = copia_pdf("texto_varias_paginas.pdf")
    ventana.abrir(ruta)
    ventana.documento.ejecutar(EliminarPaginas([5], 6))
    mostrados = []
    monkeypatch.setattr(ventana, "_pedir_ruta_comparar", lambda titulo="": str(ORIGINAL))
    monkeypatch.setattr(ventana, "_mostrar_comparacion", lambda *a: mostrados.append(a))
    assert ventana.comparar_versiones()
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
    resultado = mostrados[0][0]
    assert resultado.paginas_a == 5 and resultado.paginas_b == 6      # A = lo que se está editando
    assert "(con cambios sin guardar)" in resultado.nombre_a


def test_comparar_archivo_protegido_pide_contrasena(ventana, copia_pdf, monkeypatch, qtbot):
    ventana.abrir(copia_pdf("tablas.pdf"))
    intentos = []
    monkeypatch.setattr(ventana, "_pedir_ruta_comparar", lambda titulo="": str(DIR_PDFS / "protegido.pdf"))
    monkeypatch.setattr(ventana, "_pedir_contrasena", lambda nombre, i: intentos.append(i) or ("x" if i == 0 else "1234"))
    monkeypatch.setattr(ventana, "_mostrar_comparacion", lambda *a: None)
    assert ventana.comparar_versiones()
    assert intentos == [0, 1]
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
