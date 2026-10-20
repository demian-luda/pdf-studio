"""Fase 6: páginas (rotar, eliminar, reordenar, insertar, extraer, separar), marca de agua,
numeración y compresión. En todos los casos se verifica la fidelidad de lo no editado."""
from __future__ import annotations

import json
import shutil

import pymupdf
import pytest

from pdf_studio.core import comprimir as cz
from pdf_studio.core.documento import Documento, ErrorDocumento
from pdf_studio.core.imagenes import InsertarImagen
from pdf_studio.core.marcas import (MarcaDeAgua, NumerarPaginas, OpcionesMarca, OpcionesNumeracion, Posicion,
                                    vista_previa)
from pdf_studio.core.paginas import (EliminarPaginas, FuentePDF, InsertarDesdePDF, InsertarPaginasEnBlanco,
                                     ReordenarPaginas, RotarPagina, extraer_paginas, grupos_cada, mover_paginas,
                                     nombres_libres)
from tests.conftest import DIR_PDFS, hash_archivo

FIRMA = (DIR_PDFS.parent / "imagenes_prueba" / "firma_escaneada.jpg").read_bytes()


def _primera_linea(pag: pymupdf.Page) -> str:
    return pag.get_text().strip().splitlines()[0]


@pytest.fixture
def doc_texto(copia_pdf):
    ruta = copia_pdf("texto_varias_paginas.pdf")
    antes = hash_archivo(ruta)
    d = Documento(ruta)
    d.lineas = [_primera_linea(p) for p in d.doc]
    yield d
    d.cerrar()
    assert hash_archivo(ruta) == antes


def _numerar_para_distinguir(tmp_path) -> Documento:
    """6 páginas con un texto distinto en cada una (para seguirlas al moverlas)."""
    d = pymupdf.open()
    for i in range(6):
        p = d.new_page(width=300, height=400)
        p.insert_text((40, 60 + 30 * i), f"Hoja numero {i + 1}", fontsize=16)
    ruta = tmp_path / "hojas.pdf"
    d.save(ruta)
    d.close()
    return Documento(ruta)


def _hojas(doc: pymupdf.Document) -> list[int]:
    return [int(p.get_text().split()[2]) for p in doc]


# ---------------------------------------------------------------------- utilidades
@pytest.mark.parametrize("paginas,destino,esperado", [
    ([0], 3, [1, 2, 0, 3, 4]), ([4], 0, [4, 0, 1, 2, 3]), ([1, 3], 5, [0, 2, 4, 1, 3]),
    ([2], 2, [0, 1, 2, 3, 4]), ([0, 1], 0, [0, 1, 2, 3, 4]),
])
def test_mover_paginas(paginas, destino, esperado):
    assert mover_paginas(5, paginas, destino) == esperado


def test_grupos_y_nombres(tmp_path):
    assert grupos_cada(5, 2) == [[0, 1], [2, 3], [4]]
    (tmp_path / "doc_p1-2.pdf").write_bytes(b"x")
    nombres = [p.name for p in nombres_libres(tmp_path, "doc", [[0, 1], [2], [3, 4, 6]])]
    assert nombres == ["doc_p1-2 (2).pdf", "doc_p3.pdf", "doc_p4-5_7.pdf"]


# ---------------------------------------------------------------------- rotar / eliminar / reordenar
def test_rotar_varias_paginas(doc_texto, tmp_path):
    doc_texto.ejecutar(RotarPagina([1, 3], 90))
    assert [p.rotation for p in doc_texto.doc] == [0, 90, 0, 90, 0, 0]
    assert "páginas 2, 4" in doc_texto.texto_deshacer
    g = doc_texto.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and sorted(g.fidelidad.comparadas) == [0, 2, 4, 5]


def test_eliminar_paginas_sigue_a_las_que_quedan(tmp_path):
    d = _numerar_para_distinguir(tmp_path)
    d.ejecutar(EliminarPaginas([0, 3], d.doc.page_count))
    assert _hojas(d.doc) == [2, 3, 5, 6]
    assert d.mapa_paginas() == {0: 1, 1: 2, 2: 4, 3: 5}
    g = d.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and g.incremental and g.bytes_originales_intactos
    assert len(g.fidelidad.comparadas) == 4
    d.deshacer()
    assert _hojas(d.doc) == [1, 2, 3, 4, 5, 6]
    d.cerrar()


def test_no_se_pueden_eliminar_todas(doc_texto):
    with pytest.raises(ErrorDocumento, match="al menos una"):
        EliminarPaginas(list(range(6)), 6)


def test_reordenar_y_verificar_cada_pagina_en_su_lugar_nuevo(tmp_path):
    d = _numerar_para_distinguir(tmp_path)
    orden = mover_paginas(6, [4, 5], 1)
    d.ejecutar(ReordenarPaginas(orden))
    assert _hojas(d.doc) == [1, 5, 6, 2, 3, 4]
    g = d.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and len(g.fidelidad.comparadas) == 6 and g.incremental
    with pymupdf.open(tmp_path / "s.pdf") as r:
        assert _hojas(r) == [1, 5, 6, 2, 3, 4]
    d.cerrar()


def test_zonas_editadas_siguen_a_la_pagina_movida(tmp_path):
    """Una firma en la página 3 que después pasa a ser la 1: se compara contra la original 3
    fuera de la zona de la firma (antes se comparaba la página equivocada)."""
    d = _numerar_para_distinguir(tmp_path)
    d.ejecutar(InsertarImagen(2, ((150, 300), (250, 300), (150, 350)), FIRMA))
    d.ejecutar(ReordenarPaginas(mover_paginas(6, [2], 0)))
    assert d.regiones_editadas() == {0: (2, d.regiones_editadas()[0][1])}
    assert 0 not in d.mapa_paginas() and d.mapa_paginas()[1] == 0
    g = d.guardar(tmp_path / "s.pdf")
    assert g.es_fiel, g.resumen()
    assert g.fidelidad.parciales == [0] and len(g.fidelidad.comparadas) == 5
    d.cerrar()


def test_reordenar_rechaza_ordenes_invalidos():
    with pytest.raises(ValueError):
        ReordenarPaginas([0, 0, 1])
    with pytest.raises(ValueError, match="no cambió"):
        ReordenarPaginas([0, 1, 2])


# ---------------------------------------------------------------------- insertar
def test_insertar_en_blanco(tmp_path):
    d = _numerar_para_distinguir(tmp_path)
    d.ejecutar(InsertarPaginasEnBlanco(2, 2, (300, 400)))
    assert d.doc.page_count == 8 and not d.doc[2].get_text() and not d.doc[3].get_text()
    assert d.paginas_nuevas() == [2, 3]
    g = d.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and len(g.fidelidad.comparadas) == 6 and g.fidelidad.excluidas == [2, 3]
    d.cerrar()


def test_unir_documentos(tmp_path, copia_pdf):
    d = _numerar_para_distinguir(tmp_path)
    tablas = FuentePDF.desde_archivo(copia_pdf("tablas.pdf"))
    otro = FuentePDF.desde_archivo(copia_pdf("texto_varias_paginas.pdf"), paginas=[0, 5])
    d.ejecutar(InsertarDesdePDF(6, [tablas, otro]))
    n_tablas = tablas.cantidad()
    assert d.doc.page_count == 6 + n_tablas + 2
    with pymupdf.open(DIR_PDFS / "tablas.pdf") as t:
        from pdf_studio.core.fidelidad import comparar_paginas
        assert comparar_paginas(t[0], d.doc[6], 80)[0] == 0      # copiada tal cual
    g = d.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and len(g.fidelidad.comparadas) == 6
    d.deshacer()
    assert d.doc.page_count == 6
    d.cerrar()


def test_insertar_en_el_medio_corre_las_siguientes(tmp_path, copia_pdf):
    d = _numerar_para_distinguir(tmp_path)
    d.ejecutar(InsertarDesdePDF(1, [FuentePDF.desde_archivo(copia_pdf("tablas.pdf"), paginas=[0])]))
    assert d.mapa_paginas()[2] == 1 and 1 not in d.mapa_paginas()
    assert d.guardar(tmp_path / "s.pdf").es_fiel
    d.cerrar()


@pytest.mark.parametrize("nombre,mensaje", [("protegido.pdf", "contraseña"), ("vacio.pdf", "vacío")])
def test_insertar_desde_archivo_invalido(tmp_path, copia_pdf, nombre, mensaje):
    if nombre == "vacio.pdf":
        ruta = tmp_path / nombre
        ruta.write_bytes(b"")
    else:
        ruta = copia_pdf(nombre)
    with pytest.raises(ErrorDocumento, match=mensaje):
        FuentePDF.desde_archivo(ruta).cantidad()


# ---------------------------------------------------------------------- extraer / separar
def test_extraer_paginas(doc_texto, tmp_path):
    destino = tmp_path / "extraidas.pdf"
    r = extraer_paginas(doc_texto.doc, [[1, 3]], [destino])[0]
    assert r.identico
    with pymupdf.open(destino) as d:
        assert d.page_count == 2 and _primera_linea(d[0]) == doc_texto.lineas[1]
    with pytest.raises(ErrorDocumento, match="Ya existe"):
        extraer_paginas(doc_texto.doc, [[0]], [destino])
    with pytest.raises(ErrorDocumento, match="original"):
        extraer_paginas(doc_texto.doc, [[0]], [doc_texto.ruta_original], sobrescribir=True,
                        proteger=[doc_texto.ruta_original])
    assert doc_texto.operaciones_activas == []          # extraer no cambia el documento


def test_separar_en_archivos(doc_texto, tmp_path):
    grupos = grupos_cada(6, 4)
    destinos = nombres_libres(tmp_path, "doc", grupos)
    resultados = extraer_paginas(doc_texto.doc, grupos, destinos)
    assert all(r.identico for r in resultados)
    assert [pymupdf.open(r.ruta).page_count for r in resultados] == [4, 2]


# ---------------------------------------------------------------------- marca de agua y numeración
@pytest.mark.parametrize("rotacion", [0, 90, 180, 270])
def test_marca_de_agua_de_texto(tmp_path, rotacion):
    d = _numerar_para_distinguir(tmp_path)
    if rotacion:
        d.ejecutar(RotarPagina([0, 1, 2, 3, 4, 5], rotacion))
    d.ejecutar(MarcaDeAgua([0, 2], OpcionesMarca(texto="CONFIDENCIAL", tamano=40, angulo=45, opacidad=0.3)))
    assert "CONFIDENCIAL" in d.doc[0].get_text() and "CONFIDENCIAL" not in d.doc[1].get_text()
    zona = pymupdf.Rect(d.operaciones_activas[-1].regiones_afectadas()[0][0])
    # La marca queda centrada en la página tal como se ve
    centro = (zona.tl + zona.br) / 2 * d.doc[0].rotation_matrix
    visible = d.doc[0].rect
    assert abs(centro.x - visible.width / 2) < 8 and abs(centro.y - visible.height / 2) < 8
    if not rotacion:
        g = d.guardar(tmp_path / "s.pdf")
        assert g.es_fiel, g.resumen()
        assert sorted(g.fidelidad.parciales) == [0, 2] and len(g.fidelidad.comparadas) == 4
    else:
        # Las páginas rotadas se excluyen (rotar cambia la página entera), pero la zona declarada
        # tiene que contener todo lo que cambió: se compara contra la versión solo rotada
        from pdf_studio.core.fidelidad import comparar_paginas
        solo_rotada = pymupdf.open()
        solo_rotada.insert_pdf(pymupdf.open(d.ruta_base), from_page=0, to_page=0)
        solo_rotada[0].set_rotation(rotacion)
        distintos, _, _ = comparar_paginas(solo_rotada[0], d.doc[0], 100,
                                           excluir=d.operaciones_activas[-1].regiones_afectadas()[0])
        assert distintos == 0
    d.cerrar()


def test_marca_de_agua_con_imagen_y_detras(doc_texto, tmp_path):
    doc_texto.ejecutar(MarcaDeAgua([0], OpcionesMarca(imagen=FIRMA, opacidad=0.5, posicion=Posicion.ABAJO_DER,
                                                     escala_imagen=0.3)))
    assert len(doc_texto.doc[0].get_images()) == 1
    doc_texto.ejecutar(MarcaDeAgua([1], OpcionesMarca(texto="BORRADOR", detras=True)))
    g = doc_texto.guardar(tmp_path / "s.pdf")
    assert g.es_fiel, g.resumen()


def test_marca_de_agua_valida_opciones():
    with pytest.raises(ValueError, match="texto"):
        MarcaDeAgua([0], OpcionesMarca(texto="  "))
    with pytest.raises(ValueError, match="opacidad"):
        MarcaDeAgua([0], OpcionesMarca(opacidad=0))


def test_numeracion(doc_texto, tmp_path):
    op = NumerarPaginas([1, 2, 3, 4, 5], OpcionesNumeracion(formato="Página {n} de {total}", inicio=2,
                                                            posicion=Posicion.ABAJO_DER))
    doc_texto.ejecutar(op)
    assert "Página 2 de 6" in doc_texto.doc[1].get_text()
    assert "Página 6 de 6" in doc_texto.doc[5].get_text()
    assert " de 6" not in doc_texto.doc[0].get_text()
    zona = pymupdf.Rect(op.regiones_afectadas()[1][0])
    assert zona.x1 > 500 and zona.y1 > 780          # abajo a la derecha
    g = doc_texto.guardar(tmp_path / "s.pdf")
    assert g.es_fiel and g.fidelidad.comparadas == [0]


@pytest.mark.parametrize("formato", ["Página", "Pág. {x}", "{n} {"])
def test_numeracion_formato_invalido(formato):
    with pytest.raises(ValueError):
        NumerarPaginas([0], OpcionesNumeracion(formato=formato))


def test_vista_previa_no_toca_el_documento(doc_texto):
    op = NumerarPaginas([0, 1, 2], OpcionesNumeracion())
    pix = vista_previa(doc_texto.doc, 1, op)
    assert pix.width > 0
    assert "Página 2 de 3" not in doc_texto.doc[1].get_text()


# ---------------------------------------------------------------------- compresión
@pytest.mark.parametrize("nivel", [cz.NivelCompresion.BAJO, cz.NivelCompresion.MEDIO, cz.NivelCompresion.ALTO])
def test_comprimir_niveles(tmp_path, copia_pdf, nivel):
    entrada = copia_pdf("imagen_fondo.pdf")
    antes = hash_archivo(entrada)
    r = cz.comprimir_y_verificar(entrada, tmp_path / "c.pdf", nivel)
    assert r.fidelidad.ok, r.resumen()
    assert r.imagenes_excluidas == nivel.con_perdida
    if nivel != cz.NivelCompresion.BAJO:
        assert r.reduce and r.reduccion > 0.5
    assert hash_archivo(entrada) == antes
    assert "Verificado" in r.resumen()


def test_comprimir_sin_perdida_exige_identidad(tmp_path, copia_pdf):
    r = cz.comprimir_y_verificar(copia_pdf("texto_varias_paginas.pdf"), tmp_path / "c.pdf", cz.NivelCompresion.BAJO)
    assert r.fidelidad.ok and len(r.fidelidad.comparadas) == 6 and r.reduce
    assert "idénticas" in r.resumen()


def test_comprimir_pdf_protegido(tmp_path, copia_pdf):
    r = cz.comprimir_y_verificar(copia_pdf("protegido.pdf"), tmp_path / "c.pdf", cz.NivelCompresion.BAJO,
                                 contrasena="1234")
    assert r.fidelidad.ok
    with pymupdf.open(tmp_path / "c.pdf") as d:
        assert d.needs_pass                    # sigue protegido


def test_comprimir_no_escribe_sobre_la_entrada(copia_pdf):
    entrada = copia_pdf("tablas.pdf")
    with pytest.raises(ErrorDocumento):
        cz.comprimir(entrada, entrada, cz.NivelCompresion.BAJO)


def test_compresion_maxima_sin_ghostscript(tmp_path, copia_pdf, monkeypatch):
    from pdf_studio.dependencias import chequeo
    monkeypatch.setattr(chequeo, "buscar_ghostscript", lambda: None)
    with pytest.raises(ErrorDocumento, match="Ghostscript"):
        cz.comprimir(copia_pdf("tablas.pdf"), tmp_path / "c.pdf", cz.NivelCompresion.MAXIMO)


@pytest.mark.skipif(__import__("pdf_studio.dependencias.chequeo", fromlist=["x"]).buscar_ghostscript() is None,
                    reason="Ghostscript no está instalado")
def test_compresion_maxima_informa_diferencias(tmp_path, copia_pdf):
    r = cz.comprimir_y_verificar(copia_pdf("escaneado.pdf"), tmp_path / "c.pdf", cz.NivelCompresion.MAXIMO)
    assert r.reduce
    assert ("Verificado" in r.resumen()) == r.fidelidad.ok
    if not r.fidelidad.ok:
        assert "cambió en" in r.resumen()


def test_resultado_ida_y_vuelta():
    from pdf_studio.core.fidelidad import DiferenciaPagina, ResultadoFidelidad
    r = cz.ResultadoCompresion(cz.NivelCompresion.ALTO, 1000, 400,
                               ResultadoFidelidad(100, comparadas=[0], parciales=[1],
                                                  diferencias=[DiferenciaPagina(1, 1, 5, 100, "x")]), True)
    r2 = cz.ResultadoCompresion.desde_dict(json.loads(json.dumps(r.a_dict())))
    assert r2.resumen() == r.resumen() and "el 5,0 %" in r.resumen()


def test_tarea_comprimir_en_otro_proceso(tmp_path, copia_pdf):
    import subprocess
    from pdf_studio import procesos
    pedido, resultado = tmp_path / "p.json", tmp_path / "r.json"
    pedido.write_text(json.dumps({"entrada": str(copia_pdf("imagen_fondo.pdf")), "salida": str(tmp_path / "c.pdf"),
                                  "nivel": "medio"}), encoding="utf-8")
    programa, argumentos = procesos.comando("comprimir", pedido, resultado)
    subprocess.run([programa, *argumentos], env=procesos.entorno(), check=True, timeout=120)
    datos = json.loads(resultado.read_text(encoding="utf-8"))
    assert datos["ok"] and cz.ResultadoCompresion.desde_dict(datos["resultado"]).fidelidad.ok


def test_tarea_con_error_devuelve_mensaje(tmp_path):
    from pdf_studio import procesos
    pedido, resultado = tmp_path / "p.json", tmp_path / "r.json"
    pedido.write_text(json.dumps({"entrada": str(tmp_path / "no.pdf"), "salida": str(tmp_path / "c.pdf"),
                                  "nivel": "bajo"}), encoding="utf-8")
    procesos.ejecutar("comprimir", pedido, resultado)
    datos = json.loads(resultado.read_text(encoding="utf-8"))
    assert not datos["ok"] and datos["error"]


# ---------------------------------------------------------------------- interfaz
def test_miniaturas_arrastrar_reordena(ventana, tmp_path, qtbot):
    d = _numerar_para_distinguir(tmp_path)
    ruta = d.ruta_original
    d.cerrar()
    ventana.abrir(ruta)
    ventana.miniaturas.paginas_movidas.emit([0], 3)
    assert _hojas(ventana.documento.doc) == [2, 3, 1, 4, 5, 6]
    assert ventana.miniaturas.count() == 6
    assert "Reordenar" in ventana.acc_deshacer.text()
    ventana.acc_deshacer.trigger()
    assert _hojas(ventana.documento.doc) == [1, 2, 3, 4, 5, 6]


def test_miniaturas_destino_del_arrastre(ventana, copia_pdf, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    m = ventana.miniaturas
    r = m.visualItemRect(m.item(1))
    assert m.fila_destino(r.center() - type(r.center())(0, r.height() // 4)) == 1
    assert m.fila_destino(r.center() + type(r.center())(0, r.height() // 4)) == 2


def test_eliminar_paginas_seleccionadas(ventana, copia_pdf):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    for fila in (1, 2):
        ventana.miniaturas.item(fila).setSelected(True)
    assert set(ventana.paginas_objetivo()) >= {1, 2}
    ventana.miniaturas.clearSelection()
    ventana.miniaturas.item(4).setSelected(True)
    ventana.miniaturas.item(5).setSelected(True)
    assert ventana.eliminar_paginas()
    assert ventana.documento.doc.page_count == 4
    assert ventana.visor.cantidad_paginas == 4 and ventana.miniaturas.count() == 4


def test_acciones_de_paginas_en_la_interfaz(ventana, copia_pdf, tmp_path, monkeypatch):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    assert ventana.insertar_en_blanco()
    assert ventana.documento.doc.page_count == 7 and ventana.visor.pagina_actual == 1
    fuente = FuentePDF.desde_archivo(copia_pdf("tablas.pdf"))
    monkeypatch.setattr(ventana, "_dialogo_insertar_pdf", lambda: (7, [fuente]))
    assert ventana.insertar_desde_pdf()
    assert ventana.documento.doc.page_count == 7 + fuente.cantidad()
    monkeypatch.setattr(ventana, "_dialogo_marca_agua",
                        lambda: MarcaDeAgua([0], OpcionesMarca(texto="COPIA")))
    assert ventana.marca_de_agua()
    monkeypatch.setattr(ventana, "_dialogo_numeracion",
                        lambda: NumerarPaginas([0, 1], OpcionesNumeracion()))
    assert ventana.numerar_paginas()
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "final.pdf"))
    assert ventana.guardar_como()
    guardado = ventana.mensajes[-1][1]
    assert guardado.es_fiel, guardado.resumen()


def test_extraer_y_separar_desde_la_interfaz(ventana, copia_pdf, tmp_path, monkeypatch):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    monkeypatch.setattr(ventana, "_dialogo_extraer", lambda: [0, 2])
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "extracto.pdf"))
    assert ventana.extraer()
    assert "Verificado" in ventana.mensajes[-1][1] and not ventana.mensajes[-1][2]
    salida = tmp_path / "partes"
    salida.mkdir()
    monkeypatch.setattr(ventana, "_dialogo_separar", lambda: [[0, 1, 2], [3, 4, 5]])
    monkeypatch.setattr(ventana, "_pedir_carpeta", lambda t: str(salida))
    assert ventana.separar()
    assert sorted(p.name for p in salida.iterdir()) == ["texto_varias_paginas_p1-3.pdf",
                                                        "texto_varias_paginas_p4-6.pdf"]


def test_comprimir_desde_la_interfaz(ventana, copia_pdf, tmp_path, monkeypatch, qtbot):
    ventana.abrir(copia_pdf("imagen_fondo.pdf"))
    vistos = []
    monkeypatch.setattr(ventana, "_dialogo_comprimir", lambda tam, gs: cz.NivelCompresion.MEDIO)
    monkeypatch.setattr(ventana, "_dialogo_resultado_compresion", lambda r: vistos.append(r) or True)
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "liviano.pdf"))
    assert ventana.comprimir()
    assert ventana.proceso_tarea.ocupado
    ventana.visor.acercar()                    # la interfaz sigue respondiendo
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
    assert vistos and vistos[0].fidelidad.ok and vistos[0].reduce
    assert (tmp_path / "liviano.pdf").stat().st_size < (DIR_PDFS / "imagen_fondo.pdf").stat().st_size
    assert not ventana.documento.modificado     # comprimir no es una edición


def test_comprimir_con_ediciones_usa_el_estado_actual(ventana, copia_pdf, tmp_path, monkeypatch, qtbot):
    ventana.abrir(copia_pdf("texto_varias_paginas.pdf"))
    ventana.documento.ejecutar(EliminarPaginas([0], 6))
    monkeypatch.setattr(ventana, "_dialogo_comprimir", lambda tam, gs: cz.NivelCompresion.BAJO)
    monkeypatch.setattr(ventana, "_dialogo_resultado_compresion", lambda r: True)
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "c.pdf"))
    assert ventana.comprimir()
    qtbot.waitUntil(lambda: not ventana.proceso_tarea.ocupado, timeout=60_000)
    with pymupdf.open(tmp_path / "c.pdf") as d:
        assert d.page_count == 5


def test_dialogos_de_paginas(qtbot, copia_pdf, tmp_path):
    from pdf_studio.ui.paginas import (DialogoComprimir, DialogoExtraer, DialogoInsertarPDF, DialogoMarcaAgua,
                                       DialogoNumeracion, DialogoResultadoCompresion, DialogoSeparar)
    with pymupdf.open(DIR_PDFS / "texto_varias_paginas.pdf") as doc:
        m = DialogoMarcaAgua(doc, 0)
        qtbot.addWidget(m)
        m.texto.setText("PRUEBA")
        m._refrescar()
        assert m.previa.pixmap() is not None and not m.previa.pixmap().isNull()   # la vista previa anda
        m.selector.set_rango("1-2")
        m.accept()
        assert isinstance(m.operacion, MarcaDeAgua) and m.operacion.paginas == [0, 1]
        m2 = DialogoMarcaAgua(doc, 0, pedir_imagen=lambda: str(DIR_PDFS.parent / "imagenes_prueba" /
                                                                "firma_escaneada.jpg"))
        qtbot.addWidget(m2)
        m2.elegir_imagen()
        m2.accept()
        assert m2.operacion.opciones.imagen is not None
        n = DialogoNumeracion(doc, 2)
        qtbot.addWidget(n)
        n._refrescar()
        assert not n.previa.pixmap().isNull()
        n.formato.setCurrentText("{n}")
        n.selector.set_rango("3-fin")
        assert n.paginas_previa() == [2, 3, 4, 5]
        n.accept()
        assert n.operacion.paginas == [2, 3, 4, 5]
        n.formato.setCurrentText("sin número")
        n.accept()
        assert n.error.isVisibleTo(n)
    e = DialogoExtraer(6, 2, [1, 3])
    qtbot.addWidget(e)
    e.accept()
    assert e.paginas == [1, 3]
    s = DialogoSeparar(6)
    qtbot.addWidget(s)
    s.cada.setValue(4)
    assert "2 archivos" in s.resumen.text()
    s.rangos.setText("1-2; 9")
    s.radio_rangos.setChecked(True)
    s.accept()
    assert "no existe" in s.error.text()
    archivo = tmp_path / "otro.pdf"
    shutil.copy2(DIR_PDFS / "tablas.pdf", archivo)
    i = DialogoInsertarPDF(6, 2, pedir_archivos=lambda: [str(archivo)])
    qtbot.addWidget(i)
    i.agregar()
    i.radio_despues.setChecked(True)
    i.accept()
    assert i.posicion == 3 and len(i.fuentes) == 1
    c = DialogoComprimir(123456, False)
    qtbot.addWidget(c)
    assert not c._radios[cz.NivelCompresion.MAXIMO].isEnabled()
    c.accept()
    assert c.nivel == cz.NivelCompresion.MEDIO
    from pdf_studio.core.fidelidad import ResultadoFidelidad
    malo = cz.ResultadoCompresion(cz.NivelCompresion.BAJO, 100, 200, ResultadoFidelidad(100, [0]), False)
    r = DialogoResultadoCompresion(malo)
    qtbot.addWidget(r)
    from PySide6.QtWidgets import QDialogButtonBox
    caja = r.findChild(QDialogButtonBox)
    assert caja.button(QDialogButtonBox.StandardButton.Cancel).isDefault()
    assert not caja.button(QDialogButtonBox.StandardButton.Ok).property("primario")


def test_mover_paginas_con_teclado(ventana, tmp_path):
    d = _numerar_para_distinguir(tmp_path)
    ruta = d.ruta_original
    d.cerrar()
    ventana.abrir(ruta)
    ventana.visor.ir_a_pagina(2)
    ventana.miniaturas.marcar_pagina(2)
    ventana.acc_bajar_pagina.trigger()
    assert _hojas(ventana.documento.doc) == [1, 2, 4, 3, 5, 6]
    assert ventana.miniaturas.paginas_seleccionadas() == [3]
    ventana.acc_subir_pagina.trigger()
    ventana.acc_subir_pagina.trigger()
    assert _hojas(ventana.documento.doc) == [1, 3, 2, 4, 5, 6]
    ventana.miniaturas.clearSelection()
    ventana.miniaturas.item(0).setSelected(True)
    assert not ventana.desplazar_paginas(-1)        # ya es la primera
