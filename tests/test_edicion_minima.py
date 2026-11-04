"""Edición mínima: solo cambian los tramos editados y los caracteres sin glifo se conservan.

PDF sintético que imita una lista numerada exportada desde Word (ningún documento real).
"""
from __future__ import annotations

import shutil

import pikepdf
import pymupdf
import pytest
from PySide6.QtCore import Qt

from pdf_studio import config
from pdf_studio.core.documento import Documento
from pdf_studio.core.fidelidad import comparar_paginas
from pdf_studio.core.fuentes import IndiceFuentesSistema
from pdf_studio.core.cirugia import leer_flujo
from pdf_studio.core.sin_glifo import (Accion, Contexto, inventario, planificar_resolucion, vista_previa)
from pdf_studio.core.texto import (EditarMinimo, EditarTexto, Nivel, analizar_edicion, bloque_en, cambios_de_texto,
                                   lineas_de_pagina, planificar, texto_a_dibujar)
from pdf_studio.ui.edicion_texto import DialogoEditarTexto, desmarcar, marcar
from tests.fuentes_sinteticas import CODIGO_SIMBOLO, DESTINO, lista_word_simbolo, quicksand_sin_glifo
from tests.test_texto import ventana  # noqa: F401 (fixture)

LISTA = DESTINO / "lista_word_simbolo.pdf"
QUICKSAND = DESTINO / "quicksand_sin_glifo.pdf"
CUADRADITO = ""          # dentro del mismo TJ que el resto de la línea
SIMBOLO = ""             # como Word: en su propio operador y su propia fuente
LINEA_QUICKSAND = pymupdf.Point(60, 95)
LINEA_FFFD = pymupdf.Point(60, 135)
LINEA_LISTA = pymupdf.Point(100, 76)
DPI = 200


@pytest.fixture(scope="module", autouse=True)
def pdfs():
    for ruta, generar in ((LISTA, lista_word_simbolo), (QUICKSAND, quicksand_sin_glifo)):
        if not ruta.is_file():
            generar(ruta)


@pytest.fixture
def incluidas(tmp_path):
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path / "i.json")


@pytest.fixture
def abrir(tmp_path):
    documentos = []

    def _abrir(ruta) -> Documento:
        copia = tmp_path / f"copia{len(documentos)}.pdf"
        shutil.copy2(ruta, copia)
        documentos.append(Documento(copia))
        return documentos[-1]
    return _abrir


def _operadores_de_texto(pag) -> list[tuple[str, bytes]]:
    """(fuente, códigos) de cada cadena dibujada, en orden."""
    salida = []
    fuente = ""
    with pikepdf.Pdf.new() as aux:
        for ins in pikepdf.parse_content_stream(pikepdf.Stream(aux, pag.read_contents())):
            op = str(ins.operator)
            if op == "Tf":
                fuente = str(ins.operands[0])
            elif op == "Tj":
                salida.append((fuente, bytes(ins.operands[0])))
            elif op == "TJ":
                salida += [(fuente, bytes(e)) for e in ins.operands[0] if isinstance(e, pikepdf.String)]
    return salida


def _editar(documento: Documento, punto, nuevo, incluidas, nivel=Nivel.LINEA):
    """`nuevo`: el texto, o una función que lo arma a partir del texto actual del bloque."""
    doc = documento.doc
    bloque = bloque_en(doc[0], 0, punto, nivel)
    nuevo = nuevo(bloque.texto) if callable(nuevo) else nuevo
    analisis = analizar_edicion(doc, doc[0], bloque, nuevo, None, incluidas)
    plan = planificar(doc, bloque, nuevo, analisis.recomendada)
    documento.ejecutar(plan.operacion)
    return plan


def _glifos(pag) -> list[tuple]:
    return [(c[0], c[1], round(c[2][0], 3), round(c[2][1], 3)) for s in pag.get_texttrace() for c in s["chars"]]


# ---------------------------------------------------------------------- diff
@pytest.mark.parametrize("a,b,esperado", [
    ("Hola mundo", "Hola mundo", []),
    ("Hola mundo", "Hola munda", [[9, 10, 9, 10]]),
    ("abc X def", "abc def", [[4, 6, 4, 4]]),
    ("abc", "abcd", [[3, 3, 3, 4]]),
    ("abc", "xabc", [[0, 0, 0, 1]]),
])
def test_diff(a, b, esperado):
    assert cambios_de_texto(a, b) == esperado


def test_diff_une_cambios_muy_cercanos():
    assert cambios_de_texto("casa grande", "taza grande") == [[0, 3, 0, 3]]


def test_diff_nunca_toca_un_conservado_que_sigue_estando():
    """Un diff común emparejaría cualquier cosa: el símbolo es un ancla."""
    original = f"a{SIMBOLO}b{SIMBOLO}c"
    nuevo = f"xxxx{SIMBOLO}yyyy{SIMBOLO}zzzz"
    for i1, i2, _j1, _j2 in cambios_de_texto(original, nuevo, {SIMBOLO}):
        assert SIMBOLO not in original[i1:i2]


def test_a_dibujar_excluye_lo_que_no_cambia():
    with pymupdf.open(QUICKSAND) as doc:
        bloque = bloque_en(doc[0], 0, LINEA_FFFD, Nivel.LINEA)
        assert texto_a_dibujar(bloque, bloque.texto) == ""
        assert texto_a_dibujar(bloque, bloque.texto + "Z") == "Z"


# ---------------------------------------------------------------------- flujo
@pytest.mark.parametrize("ruta", [LISTA, QUICKSAND])
def test_el_flujo_se_alinea_con_la_traza(ruta):
    with pymupdf.open(ruta) as doc:
        flujo = leer_flujo(doc, doc[0])
        assert flujo is not None
        for g in flujo.glifos:
            assert g.mostrar is not None and g.codigo


def test_flujo_conoce_el_codigo_del_caracter_sin_mapa():
    with pymupdf.open(QUICKSAND) as doc:
        flujo = leer_flujo(doc, doc[0])
        raro = next(g for g in flujo.glifos if g.ucs == 0xFFFD)
        assert raro.codigo == (201).to_bytes(2, "big")


# ---------------------------------------------------------------------- edición sin tocar el resto
def _sin_espacios(texto: str) -> str:
    return "".join(texto.split())


def pikepdf_hex(pag) -> bytes:
    return b"".join(c for _f, c in _operadores_de_texto(pag)).hex().encode()


@pytest.mark.parametrize("nuevo", [
    f"Hoka {CUADRADITO} mundo",       # antes del símbolo
    f"Hola{CUADRADITO} mundo",        # pegado al símbolo
    f"Hola {CUADRADITO}mundo",        # del otro lado
    f"Hola {CUADRADITO} munda",       # después del símbolo
])
def test_simbolo_en_el_mismo_operador_byte_a_byte(abrir, incluidas, nuevo):
    d = abrir(QUICKSAND)
    xref = next(f[0] for f in d.doc[0].get_fonts(full=True) if "Quicksand" in f[3])
    _editar(d, LINEA_QUICKSAND, nuevo, incluidas)
    pag = d.doc[0]
    assert b"00c8" in pikepdf_hex(pag)         # CID 200: el código del símbolo, tal cual
    simbolo = next(c for s in pag.get_texttrace() for c in s["chars"] if c[0] == ord(CUADRADITO))
    assert simbolo[1] == 0                     # sigue dibujando .notdef, sin pedir un glifo nuevo
    assert xref in [f[0] for f in pag.get_fonts(full=True)]
    assert _sin_espacios(nuevo) in _sin_espacios(pag.get_text())


@pytest.mark.parametrize("cambio", [
    lambda t: t + ".",                                  # agrega al final
    lambda t: t.replace("punto", "panto"),              # una letra en el medio (mismo ancho)
    lambda t: t.replace("lista", "lisia"),
    lambda t: t.replace("Primer", "Primar"),            # antes del resto de la línea, después del símbolo
    lambda t: t.replace("1.", "7."),                    # antes del símbolo
])
def test_lista_de_word_el_simbolo_queda_identico(abrir, incluidas, cambio):
    """El símbolo está en otro operador y otra fuente: ni siquiera se toca ese operador."""
    d = abrir(LISTA)
    pag = d.doc[0]
    simbolos_antes = [o for o in _operadores_de_texto(pag) if o[1] == bytes([CODIGO_SIMBOLO])]
    assert len(simbolos_antes) == 3
    xref_antes = next(f[0] for f in pag.get_fonts(full=True) if "SymbolMT" in f[3])
    posiciones_antes = [g for g in _glifos(pag) if g[0] == ord(SIMBOLO)]
    bloque = bloque_en(pag, 0, LINEA_LISTA, Nivel.LINEA)
    esperado = cambio(bloque.texto)
    _editar(d, LINEA_LISTA, cambio, incluidas)
    pag = d.doc[0]
    assert [o for o in _operadores_de_texto(pag) if o[1] == bytes([CODIGO_SIMBOLO])] == simbolos_antes
    assert next(f[0] for f in pag.get_fonts(full=True) if "SymbolMT" in f[3]) == xref_antes
    assert [g for g in _glifos(pag) if g[0] == ord(SIMBOLO)] == posiciones_antes
    assert esperado in pag.get_text()


@pytest.mark.parametrize("ruta,punto,cambio", [
    (QUICKSAND, LINEA_QUICKSAND, lambda t: t.replace("Hola", "Hoka")),
    (QUICKSAND, LINEA_QUICKSAND, lambda t: t.replace("mundo", "munda")),
    (QUICKSAND, LINEA_QUICKSAND, lambda t: t + " mundo"),
    (QUICKSAND, LINEA_FFFD, lambda t: t.replace("final", "fina")),
    (LISTA, LINEA_LISTA, lambda t: t.replace("punto", "panto")),
    (LISTA, LINEA_LISTA, lambda t: t + " final"),
    (LISTA, LINEA_LISTA, lambda t: t.replace("1.", "7.")),
])
def test_pixeles_fuera_del_tramo_editado_son_cero(abrir, incluidas, ruta, punto, cambio):
    d = abrir(ruta)
    with pymupdf.open(ruta) as original:
        plan = _editar(d, punto, cambio, incluidas)
        zonas = plan.operacion.regiones_afectadas()[0]
        assert isinstance(plan.operacion, EditarMinimo) and zonas
        distintos, _total, _ = comparar_paginas(original[0], d.doc[0], DPI, zonas)
        assert distintos == 0


def test_el_tramo_excluido_es_chico_cuando_cambia_una_letra(abrir, incluidas):
    d = abrir(LISTA)
    plan = _editar(d, LINEA_LISTA, lambda t: t.replace("punto", "panto"), incluidas)
    zonas = plan.operacion.regiones_afectadas()[0]
    assert len(zonas) == 1 and pymupdf.Rect(zonas[0]).width < 12      # una letra y su margen


def test_cada_glifo_que_no_cambia_sigue_exactamente_en_su_lugar(abrir, incluidas):
    d = abrir(QUICKSAND)
    antes = _glifos(d.doc[0])
    _editar(d, LINEA_QUICKSAND, f"Hola {CUADRADITO} munda", incluidas)
    despues = _glifos(d.doc[0])
    cambiados = [g for g in antes if g not in despues]
    assert len(cambiados) == 1 and cambiados[0][0] == ord("o")


def test_guardado_incremental_y_fiel(abrir, incluidas, tmp_path):
    d = abrir(LISTA)
    _editar(d, LINEA_LISTA, lambda t: t.replace("punto", "panto"), incluidas)
    guardado = d.guardar(tmp_path / "salida.pdf")
    assert guardado.es_fiel, guardado.resumen()
    assert guardado.incremental and guardado.bytes_originales_intactos


def test_deshacer_y_rehacer(abrir, incluidas):
    d = abrir(QUICKSAND)
    antes = _glifos(d.doc[0])
    _editar(d, LINEA_QUICKSAND, f"Hola {CUADRADITO} munda", incluidas)
    editado = _glifos(d.doc[0])
    d.deshacer()
    assert _glifos(d.doc[0]) == antes
    d.rehacer()
    assert _glifos(d.doc[0]) == editado


def test_dos_ediciones_seguidas(abrir, incluidas):
    d = abrir(QUICKSAND)
    _editar(d, LINEA_QUICKSAND, f"Hola {CUADRADITO} munda", incluidas)
    _editar(d, LINEA_QUICKSAND, f"Hoka {CUADRADITO} munda", incluidas)
    assert f"Hoka {CUADRADITO} munda" in d.doc[0].get_text()
    assert b"00c8" in pikepdf_hex(d.doc[0])


# ---------------------------------------------------------------------- orden de lectura
def test_la_palabra_editada_se_puede_buscar(abrir, incluidas):
    """El texto nuevo queda en su lugar del contenido, no al final de la página."""
    d = abrir(LISTA)
    _editar(d, LINEA_LISTA, lambda t: t.replace("punto", "panto"), incluidas)
    assert d.doc[0].search_for("panto de la")
    assert "Primer panto de la lista" in d.doc[0].get_text()


def test_con_otra_fuente_tambien_queda_en_su_lugar(abrir, incluidas):
    """La Z no está en el subconjunto: entra otra fuente en medio del operador."""
    d = abrir(LISTA)
    _editar(d, LINEA_LISTA, lambda t: t.replace("lista", "Zona"), incluidas)
    pag = d.doc[0]
    assert "Primer punto de la Zona" in pag.get_text()
    assert pag.search_for("de la Zona")
    # Palabra entera en la fuente nueva; el resto de la línea, en la original
    fuentes = {sp["font"]: sp["text"] for b in pag.get_text("dict")["blocks"] for ln in b["lines"]
               for sp in ln["spans"] if "Zona" in sp["text"]}
    assert all("Primer" not in t for t in fuentes.values())


# ---------------------------------------------------------------------- ancho distinto
def test_texto_mas_ancho_corre_lo_que_sigue_con_sus_codigos(abrir, incluidas):
    d = abrir(QUICKSAND)
    glifos_antes = {g[0]: g[2] for g in _glifos(d.doc[0]) if g[0] == ord(CUADRADITO)}
    _editar(d, LINEA_QUICKSAND, f"Holaaaa {CUADRADITO} mundo", incluidas)
    glifos_despues = {g[0]: g[2] for g in _glifos(d.doc[0]) if g[0] == ord(CUADRADITO)}
    assert glifos_despues[ord(CUADRADITO)] > glifos_antes[ord(CUADRADITO)]      # se corrió
    todo = b"".join(c for _f, c in _operadores_de_texto(d.doc[0]))
    for codigo in (b"\x00\xc8", b"\x00\x6d\x00\x70\x00\x6e\x00\x68\x00\x6f"):   # símbolo y "mundo"
        assert codigo in todo


def test_hueco_chico_no_mueve_nada(abrir, incluidas):
    d = abrir(LISTA)
    # Una letra más angosta: el hueco es menor que lo tolerado y todo lo que sigue queda en su lugar
    linea = bloque_en(d.doc[0], 0, LINEA_LISTA, Nivel.LINEA).lineas[0]
    antes = [g for g in _glifos(d.doc[0]) if g[3] == round(linea.base_y, 3) and g[2] > 130]
    assert antes
    _editar(d, LINEA_LISTA, lambda t: t.replace("punto", "panto"), incluidas)
    despues = _glifos(d.doc[0])
    assert all(g in despues for g in antes)


def test_hueco_grande_se_cierra_para_no_partir_la_linea(abrir, incluidas):
    d = abrir(LISTA)
    _editar(d, LINEA_LISTA, lambda t: t.replace("Primer punto", "Primer"), incluidas)
    assert "Primer de la lista" in d.doc[0].get_text()
    assert d.doc[0].search_for("Primer de la")


def test_operador_siguiente_se_corre_con_un_numero_inicial(tmp_path, incluidas):
    """Otro operador en la misma línea, posicionado con Td: también tiene que correrse."""
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "Primera parte", fontname="helv", fontsize=12)
    ancho = pymupdf.get_text_length("Primera parte", fontname="helv", fontsize=12)
    pag.insert_text((72 + ancho, 100), " segunda parte", fontname="helv", fontsize=12)
    ruta = tmp_path / "dos.pdf"
    doc.save(ruta)
    d = Documento(ruta)
    bloque = bloque_en(d.doc[0], 0, pymupdf.Point(80, 96), Nivel.LINEA)
    nuevo = bloque.texto.replace("Primera", "Primeraaaaaaa")
    d.ejecutar(planificar(d.doc, bloque, nuevo, analizar_edicion(d.doc, d.doc[0], bloque, nuevo, None, incluidas)
                          .recomendada).operacion)
    assert nuevo in d.doc[0].get_text()


def test_columna_separada_no_se_mueve(tmp_path, incluidas):
    """Una tabla: la segunda columna queda donde está aunque cambie la primera."""
    doc = pymupdf.open()
    pag = doc.new_page()
    pag.insert_text((72, 100), "Nombre", fontname="helv", fontsize=12)
    pag.insert_text((300, 100), "Valor", fontname="helv", fontsize=12)
    ruta = tmp_path / "tabla.pdf"
    doc.save(ruta)
    d = Documento(ruta)
    for lineas in lineas_de_pagina(d.doc[0]):
        for ln in lineas:
            if "Nombre" not in ln.texto:
                continue
            bloque = bloque_en(d.doc[0], 0, pymupdf.Point(ln.glifos[1].x + 1, ln.base_y - 3), Nivel.LINEA)
            nuevo = bloque.texto.replace("Nombre", "N")
            analisis = analizar_edicion(d.doc, d.doc[0], bloque, nuevo, None, incluidas)
            d.ejecutar(planificar(d.doc, bloque, nuevo, analisis.recomendada).operacion)
    x = [g[2] for g in _glifos(d.doc[0]) if g[0] == ord("V")]
    assert x and abs(x[0] - 300) < 0.01


# ---------------------------------------------------------------------- párrafo
def test_parrafo_que_se_reacomoda_conserva_el_codigo(abrir, incluidas):
    """Un párrafo que crece reescribe más, pero el carácter sin glifo sigue con su código."""
    d = abrir(QUICKSAND)
    doc = d.doc
    bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.PARRAFO)
    assert CUADRADITO in bloque.texto
    largo = " mundo" * 18
    nuevo = bloque.texto + largo
    analisis = analizar_edicion(doc, doc[0], bloque, nuevo, None, incluidas)
    plan = planificar(doc, bloque, nuevo, analisis.recomendada)
    assert isinstance(plan.operacion, EditarTexto)      # hubo reacomodo
    d.ejecutar(plan.operacion)
    assert b"00c8" in pikepdf_hex(d.doc[0])


def test_parrafo_sin_reacomodo_edita_minimo(abrir, incluidas):
    d = abrir(QUICKSAND)
    doc = d.doc
    bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.PARRAFO)
    nuevo = bloque.texto.replace("mundo", "munda")
    analisis = analizar_edicion(doc, doc[0], bloque, nuevo, None, incluidas)
    plan = planificar(doc, bloque, nuevo, analisis.recomendada)
    assert isinstance(plan.operacion, EditarMinimo)


def test_fragmento_con_el_resto_de_la_linea_afuera(abrir, incluidas):
    d = abrir(LISTA)
    bloque = bloque_en(d.doc[0], 0, LINEA_LISTA, Nivel.FRAGMENTO)
    nuevo = bloque.texto.replace("punto", "panto")
    analisis = analizar_edicion(d.doc, d.doc[0], bloque, nuevo, None, incluidas)
    d.ejecutar(planificar(d.doc, bloque, nuevo, analisis.recomendada).operacion)
    assert "panto" in d.doc[0].get_text() and bytes([CODIGO_SIMBOLO]) in [o[1] for o in _operadores_de_texto(d.doc[0])]


def test_texto_en_form_xobject_usa_el_metodo_anterior(tmp_path, incluidas):
    """A los Form XObject no se llega con la cirugía: sigue andando como antes."""
    origen = pymupdf.open()
    p = origen.new_page(width=300, height=200)
    p.insert_text((20, 50), "Texto dentro de un formulario", fontname="helv", fontsize=11)
    doc = pymupdf.open()
    pag = doc.new_page(width=300, height=200)
    pag.show_pdf_page(pag.rect, origen, 0)
    ruta = tmp_path / "forma.pdf"
    doc.save(ruta)
    d = Documento(ruta)
    bloque = bloque_en(d.doc[0], 0, pymupdf.Point(40, 47), Nivel.LINEA)
    nuevo = bloque.texto.replace("dentro", "adentro")
    analisis = analizar_edicion(d.doc, d.doc[0], bloque, nuevo, None, incluidas)
    plan = planificar(d.doc, bloque, nuevo, analisis.recomendada)
    assert isinstance(plan.operacion, EditarTexto)
    d.ejecutar(plan.operacion)
    assert nuevo in d.doc[0].get_text()


# ---------------------------------------------------------------------- editor con marcas
def _dialogo(doc, bloque, incluidas, qtbot, patrones=None):
    dialogo = DialogoEditarTexto(bloque, lambda t: analizar_edicion(doc, doc[0], bloque, t, None, incluidas),
                                 patrones=patrones)
    qtbot.addWidget(dialogo)
    dialogo._temporizador.setInterval(10 ** 7)
    return dialogo


def test_marcar_y_desmarcar():
    assert marcar("ab", {SIMBOLO}) == "a⟦U+F0B7⟧b"
    assert desmarcar("a⟦U+F0B7⟧b") == "ab"
    assert desmarcar("⟦no es marca⟧") == "⟦no es marca⟧"


def test_el_editor_muestra_la_marca_y_devuelve_el_codigo(incluidas, qtbot):
    with pymupdf.open(QUICKSAND) as doc:
        bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, incluidas, qtbot)
        assert "⟦U+E123⟧" in dialogo.editor.toPlainText()
        assert dialogo.texto == bloque.texto
        assert len(dialogo.editor.extraSelections()) == 1
        assert dialogo.botones.button(dialogo.botones.StandardButton.Ok).isEnabled()


@pytest.mark.parametrize("tecla,posicion", [(Qt.Key.Key_Backspace, "fin"), (Qt.Key.Key_Delete, "inicio")])
def test_la_marca_se_borra_entera(incluidas, qtbot, tecla, posicion):
    with pymupdf.open(QUICKSAND) as doc:
        bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, incluidas, qtbot)
        inicio, fin = dialogo.editor.marcas()[0]
        cursor = dialogo.editor.textCursor()
        cursor.setPosition(fin if posicion == "fin" else inicio)
        dialogo.editor.setTextCursor(cursor)
        qtbot.keyClick(dialogo.editor, tecla)
        assert dialogo.editor.marcas() == []
        assert dialogo.texto == bloque.texto.replace(CUADRADITO, "")


def test_el_cursor_no_entra_en_la_marca(incluidas, qtbot):
    with pymupdf.open(QUICKSAND) as doc:
        bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, incluidas, qtbot)
        inicio, fin = dialogo.editor.marcas()[0]
        cursor = dialogo.editor.textCursor()
        cursor.setPosition(inicio)
        dialogo.editor.setTextCursor(cursor)
        qtbot.keyClick(dialogo.editor, Qt.Key.Key_Right)
        assert dialogo.editor.textCursor().position() == fin       # salta la marca entera
        qtbot.keyClick(dialogo.editor, Qt.Key.Key_Left)
        assert dialogo.editor.textCursor().position() == inicio
        # No se puede escribir adentro: lo escrito queda antes o después de la marca
        cursor = dialogo.editor.textCursor()
        cursor.setPosition(inicio + 3)
        dialogo.editor.setTextCursor(cursor)
        qtbot.keyClick(dialogo.editor, Qt.Key.Key_A)
        assert dialogo.editor.marcas()
        assert CUADRADITO in dialogo.texto


def test_una_seleccion_toma_la_marca_entera(incluidas, qtbot):
    with pymupdf.open(QUICKSAND) as doc:
        bloque = bloque_en(doc[0], 0, LINEA_QUICKSAND, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, incluidas, qtbot)
        inicio, fin = dialogo.editor.marcas()[0]
        cursor = dialogo.editor.textCursor()
        cursor.setPosition(inicio - 2)
        cursor.setPosition(inicio + 3, cursor.MoveMode.KeepAnchor)
        dialogo.editor.setTextCursor(cursor)
        c = dialogo.editor.textCursor()
        assert c.selectionEnd() == fin and c.selectionStart() == inicio - 2


def test_aplicar_desde_el_dialogo_no_pide_glifo_y_conserva(abrir, incluidas, qtbot):
    d = abrir(QUICKSAND)
    doc = d.doc
    bloque = bloque_en(doc[0], 0, LINEA_FFFD, Nivel.LINEA)          # U+FFFD: nadie tiene ese glifo
    dialogo = _dialogo(doc, bloque, incluidas, qtbot)
    cursor = dialogo.editor.textCursor()
    cursor.movePosition(cursor.MoveOperation.End)
    cursor.insertText("i")                                          # "finali"
    dialogo.editor.setTextCursor(cursor)
    dialogo.actualizar()
    assert dialogo.botones.button(dialogo.botones.StandardButton.Ok).isEnabled()
    assert dialogo.opcion_elegida().tipo == "original"
    d.ejecutar(planificar(doc, bloque, dialogo.texto, dialogo.opcion_elegida()).operacion)
    assert b"00c9" in pikepdf_hex(d.doc[0])                         # el código sin mapa, intacto
    assert "finali" in d.doc[0].get_text()


def test_el_dialogo_muestra_el_patron_del_documento(incluidas, qtbot):
    with pymupdf.open(LISTA) as doc:
        bloque = bloque_en(doc[0], 0, pymupdf.Point(44, 76), Nivel.LINEA)
        assert SIMBOLO in bloque.texto
        dialogo = _dialogo(doc, bloque, incluidas, qtbot, patrones=lambda: inventario(doc))
        assert "3 veces" in dialogo.etiqueta_patron.text()
        assert "después de un número" in dialogo.etiqueta_patron.text()
        assert not dialogo.boton_todo_documento.isHidden()
        dialogo.boton_todo_documento.click()
        assert dialogo.pidio_documento


# ---------------------------------------------------------------------- patrón en el documento
def test_inventario_de_la_lista():
    with pymupdf.open(LISTA) as doc:
        patrones = inventario(doc)
    assert len(patrones) == 1
    p = patrones[0]
    assert p.caracter == SIMBOLO and p.total == 3 and p.paginas == [0] and p.fuente == "SymbolMT"
    assert p.contexto_tipico == f"siempre {Contexto.TRAS_NUMERO}"
    assert "U+F0B7" in p.resumen() and "3 veces" in p.resumen()


def test_inventario_vacio_en_un_pdf_normal():
    from tests.conftest import DIR_PDFS
    with pymupdf.open(DIR_PDFS / "tablas.pdf") as doc:
        assert inventario(doc) == []


@pytest.mark.parametrize("accion,hay_simbolo,en_texto", [
    (Accion.CONSERVAR, True, None),
    (Accion.ESPACIO, False, None),
    (Accion.TABULACION, False, None),
    (Accion.VINETA, False, "•"),
    (Accion.QUITAR, False, None),
])
def test_acciones_en_todo_el_documento(abrir, incluidas, tmp_path, accion, hay_simbolo, en_texto):
    d = abrir(LISTA)
    patrones = inventario(d.doc)
    antes = [g for g in _glifos(d.doc[0]) if g[0] != ord(SIMBOLO)]
    plan = planificar_resolucion(d.doc, patrones, {patrones[0].clave: accion}, incluidas)
    if accion == Accion.CONSERVAR:
        assert plan.operacion is None
        return
    assert plan.cambiados == 3 and plan.omitidos == 0
    vista = vista_previa(d.doc, plan, patrones[0].apariciones[0])
    assert vista.pixeles_distintos == 0 and vista.antes is not None and vista.despues is not None
    assert d.modificado is False                    # la vista previa no toca el documento
    d.ejecutar(plan.operacion)
    pag = d.doc[0]
    despues = _glifos(pag)
    assert (ord(SIMBOLO) in [g[0] for g in despues]) is hay_simbolo
    # Todo lo demás, en su lugar
    assert all(g in despues for g in antes)
    if en_texto:
        assert pag.get_text().count(en_texto) == 3
    guardado = d.guardar(tmp_path / f"{accion}.pdf")
    assert guardado.es_fiel, guardado.resumen()
    d.deshacer()
    assert ord(SIMBOLO) in [g[0] for g in _glifos(d.doc[0])]


def test_dialogo_del_documento_conservar_por_defecto(incluidas, qtbot):
    from pdf_studio.ui.sin_glifo import DialogoSinGlifo
    with pymupdf.open(LISTA) as doc:
        dialogo = DialogoSinGlifo(doc, inventario(doc), incluidas)
        qtbot.addWidget(dialogo)
        combo = next(iter(dialogo.combos.values()))
        assert combo.currentData() == Accion.CONSERVAR
        assert not dialogo.boton_aplicar.isEnabled()
        combo.setCurrentIndex(combo.findData(Accion.VINETA))
        dialogo.actualizar()
        assert dialogo.boton_aplicar.isEnabled()
        assert "píxeles distintos = 0" in dialogo.estado.text()
        assert not dialogo.despues.pixmap().isNull()


def test_dialogo_del_documento_bloquea_si_cambia_otra_cosa(incluidas, qtbot, monkeypatch):
    from pdf_studio.ui import sin_glifo as ui
    from pdf_studio.core.sin_glifo import VistaPrevia
    monkeypatch.setattr(ui, "vista_previa", lambda *a: VistaPrevia(pixeles_distintos=7))
    with pymupdf.open(LISTA) as doc:
        dialogo = ui.DialogoSinGlifo(doc, inventario(doc), incluidas)
        qtbot.addWidget(dialogo)
        combo = next(iter(dialogo.combos.values()))
        combo.setCurrentIndex(combo.findData(Accion.QUITAR))
        dialogo.actualizar()
        assert not dialogo.boton_aplicar.isEnabled()
        assert "7 píxeles" in dialogo.estado.text()


def test_ventana_flujo_del_documento(ventana, abrir, tmp_path, monkeypatch, incluidas, qtbot):
    copia = tmp_path / "lista.pdf"
    shutil.copy2(LISTA, copia)
    ventana.abrir(copia)
    assert ventana.acc_sin_glifo.isEnabled()
    patrones = inventario(ventana.documento.doc)
    plan = planificar_resolucion(ventana.documento.doc, patrones, {patrones[0].clave: Accion.QUITAR}, incluidas)
    monkeypatch.setattr(ventana, "_dialogo_sin_glifo", lambda p: plan)
    assert ventana.resolver_sin_glifo()
    assert ventana.documento.puede_deshacer
    assert SIMBOLO not in ventana.documento.doc[0].get_text()


def test_ventana_aplicar_sin_cambios_no_toca_nada(ventana, tmp_path):
    copia = tmp_path / "lista.pdf"
    shutil.copy2(LISTA, copia)
    ventana.abrir(copia)
    bloque = ventana.seleccionar_texto(0, LINEA_LISTA)
    assert ventana._aplicar_plan(bloque, bloque.texto, None, (0.0, 0.0)) is False
    assert not ventana.documento.puede_deshacer


def test_flujo_que_no_alinea_vuelve_al_metodo_anterior(abrir, incluidas, monkeypatch):
    from pdf_studio.core import texto
    monkeypatch.setattr(texto, "leer_flujo", lambda *a: None)
    d = abrir(QUICKSAND)
    bloque = bloque_en(d.doc[0], 0, LINEA_QUICKSAND, Nivel.LINEA)
    nuevo = bloque.texto + " mundo"
    analisis = analizar_edicion(d.doc, d.doc[0], bloque, nuevo, None, incluidas)
    plan = planificar(d.doc, bloque, nuevo, analisis.recomendada)
    assert isinstance(plan.operacion, EditarTexto)
    d.ejecutar(plan.operacion)
    assert "mundo mundo" in d.doc[0].get_text()


# ---------------------------------------------------------------------- códigos engañosos
def test_codigo_con_ancho_cero_no_esta_disponible():
    """Los subconjuntos dejan en 0 el /Widths de los códigos que no usan: no es un glifo real."""
    from pdf_studio.core.codificacion import CodificadorFuente
    from pdf_studio.core.fuentes import Embebido
    cod = CodificadorFuente(1, "F", "TrueType", Embebido.COMPLETA, 1, {"a": 97, "1": 49, "́": 200},
                            {97: 500.0, 49: 0.0, 200: 0.0}, 0.0)
    assert cod.faltantes("a") == []
    assert cod.faltantes("1") == ["1"]
    assert cod.faltantes("́") == []         # un acento combinante sí puede no tener avance


def test_prefiere_el_codigo_que_la_pagina_ya_usa():
    """Varios códigos mapean al espacio; solo el que ya se dibuja es seguro."""
    from pdf_studio.core.cirugia import Flujo, GlifoFlujo
    glifos = [GlifoFlujo(None, 0, bytes([0]), 7, ord(" "), 0), GlifoFlujo(None, 0, bytes([32]), 7, ord(" "), 3),
              GlifoFlujo(None, 0, bytes([65]), 8, ord(" "), 5)]
    flujo = Flujo(glifos)
    assert flujo.codigo_usado(7, " ") == bytes([32])     # el 00 dibuja .notdef; el de otra fuente no sirve
    assert flujo.codigo_usado(7, "x") is None
