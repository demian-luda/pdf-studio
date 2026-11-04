"""Fuentes variables, refresco del índice, clase de Quicksand y caracteres sin glifo.

Se usa una Quicksand real (OFL, en tests/fuentes_prueba) y un PDF sintético: ningún documento real.
"""
from __future__ import annotations

import io
import shutil

import pymupdf
import pytest
from fontTools.ttLib import TTFont

from pdf_studio import config
from pdf_studio.core.documento import Documento, Limitacion
from pdf_studio.core.fuentes import (Clase, Embebido, IndiceFuentesSistema, _ancho_del_primer_trazo, _contorno_de,
                                     analizar_fuentes, bytes_de_fuente, clase_tipografica, clave_fuente,
                                     es_no_dibujable, instanciar_variable)
from pdf_studio.core.sustitucion import candidatas, familias_del_documento
from pdf_studio.core.texto import (MotivoSinGlifo, Nivel, analizar_edicion, analizar_fuente, bloque_en,
                                   lineas_de_pagina,
                                   localizar_sin_glifo, planificar)
from pdf_studio.ui.edicion_texto import DialogoEditarTexto
from tests.fuentes_sinteticas import DESTINO, QUICKSAND_VARIABLE, quicksand_sin_glifo
from tests.test_texto import ventana  # noqa: F401 (fixture)

PDF = DESTINO / "quicksand_sin_glifo.pdf"
PUNTO_CUADRADITO = pymupdf.Point(60, 95)        # línea con U+E123 → .notdef
PUNTO_SIN_MAPA = pymupdf.Point(60, 135)         # línea con un código que el PDF no mapea


@pytest.fixture(scope="module", autouse=True)
def pdf_generado():
    if not PDF.is_file():
        quicksand_sin_glifo(PDF)


@pytest.fixture
def incluidas(tmp_path):
    return IndiceFuentesSistema([config.DIR_FUENTES_INCLUIDAS], tmp_path / "incluidas.json")


@pytest.fixture
def carpeta(tmp_path):
    """Carpeta de fuentes vacía, como un Windows sin Quicksand."""
    ruta = tmp_path / "fuentes"
    ruta.mkdir()
    return ruta


@pytest.fixture
def instalada(carpeta, tmp_path):
    """Índice con Quicksand.ttf (variable) instalada, como la baja Google Fonts."""
    shutil.copy2(QUICKSAND_VARIABLE, carpeta / "Quicksand.ttf")
    return IndiceFuentesSistema([carpeta], tmp_path / "sistema.json")


def _variable_modificada(destino, cambiar) -> None:
    f = TTFont(str(QUICKSAND_VARIABLE))
    cambiar(f)
    f.save(str(destino))


def _grosor_del_asta(datos: bytes) -> float:
    f = TTFont(io.BytesIO(datos))
    glifos = f.getGlyphSet()
    segmentos = _contorno_de(glifos[f.getBestCmap()[ord("H")]], glifos)
    alto = max(p[1] for s in segmentos for p in s)
    return _ancho_del_primer_trazo(segmentos, 0.25 * alto)


# ---------------------------------------------------------------------- 1. fuentes variables
@pytest.mark.parametrize("nombre,peso", [
    ("Quicksand-Bold", 700), ("ABCDEF+Quicksand-Bold", 700), ("Quicksand,Bold", 700), ("Quicksand Bold", 700),
    ("Quicksand-SemiBold", 600), ("Quicksand-DemiBold", 600), ("Quicksand-Regular", 400), ("Quicksand", 400),
    ("Quicksand-Light", 300),
])
def test_el_indice_reconoce_las_instancias(instalada, nombre, peso):
    cara = instalada.buscar(nombre)
    assert cara is not None and cara.archivo.endswith("Quicksand.ttf")
    assert cara.variacion == (("wght", float(peso)),)


@pytest.mark.parametrize("nombre", ["Quicksand-Black", "Quicksand-Thin", "Quicksand-BoldItalic", "Quicksand-Italic"])
def test_no_inventa_pesos_ni_cursivas(instalada, nombre):
    """El eje va de 300 a 700 y el archivo no es cursivo."""
    assert instalada.buscar(nombre) is None


def test_familia_de_una_variable_y_cache_en_disco(instalada, tmp_path, carpeta):
    assert {c.nombre_visible for c in instalada.familia("Quicksand")} == {
        "Quicksand Light", "Quicksand Regular", "Quicksand Medium", "Quicksand SemiBold", "Quicksand Bold"}
    # La segunda carga sale de la caché JSON y la variación tiene que sobrevivir, hasheable
    otra = IndiceFuentesSistema([carpeta], tmp_path / "sistema.json")
    cara = otra.buscar("Quicksand-Bold")
    assert cara.variacion == (("wght", 700.0),) and hash(cara.variacion)
    assert cara.detalle_variacion == "wght=700"


def test_cache_vieja_sin_variacion_se_descarta(carpeta, tmp_path):
    """La caché de antes guardó Quicksand.ttf como una sola fuente 'Light': no sirve."""
    import json
    shutil.copy2(QUICKSAND_VARIABLE, carpeta / "Quicksand.ttf")
    st = (carpeta / "Quicksand.ttf").stat()
    cache = tmp_path / "vieja.json"
    cache.write_text(json.dumps({"version": 1, "archivos": {str(carpeta / "Quicksand.ttf"): {
        "firma": f"{st.st_size}:{int(st.st_mtime)}",
        "fuentes": [[0, "Quicksand-Light", "Quicksand", "Light"]]}}}), encoding="utf-8")
    assert IndiceFuentesSistema([carpeta], cache).buscar("Quicksand-Bold") is not None


def test_instancia_estatica_con_su_nombre(instalada):
    cara = instalada.buscar("Quicksand-Bold")
    datos = cara.datos()
    f = TTFont(io.BytesIO(datos))
    assert not {"fvar", "gvar", "HVAR", "avar"} & set(f.keys())
    assert f["OS/2"].usWeightClass == 700
    assert f["name"].getDebugName(6) == "Quicksand-Bold"
    # El peso es real: el asta de la H es bastante más gruesa que la del master (Light)
    assert _grosor_del_asta(datos) > 1.5 * _grosor_del_asta(QUICKSAND_VARIABLE.read_bytes())


def test_prefiere_la_estatica_si_tambien_esta(instalada, carpeta):
    bold = instanciar_variable(QUICKSAND_VARIABLE.read_bytes(), (("wght", 700.0),), "Quicksand-Bold")
    # El nombre del archivo hace que se lea después que la variable: igual tiene que ganar
    (carpeta / "Z-Quicksand-Bold.ttf").write_bytes(bold)
    instalada.recargar()
    cara = instalada.buscar("Quicksand-Bold")
    assert cara.archivo.endswith("Z-Quicksand-Bold.ttf") and not cara.es_variable


def test_sin_instancias_con_nombre_usa_stat(carpeta, tmp_path):
    def sacar(f):
        f["fvar"].instances = []
    _variable_modificada(carpeta / "Quicksand.ttf", sacar)
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Quicksand-Medium").variacion == (("wght", 500.0),)
    assert idx.buscar("Quicksand-Bold").variacion == (("wght", 700.0),)


def test_sin_stat_usa_pesos_estandar_dentro_del_rango(carpeta, tmp_path):
    def sacar(f):
        f["fvar"].instances = []
        del f["STAT"]
    _variable_modificada(carpeta / "Quicksand.ttf", sacar)
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Quicksand-Bold").variacion == (("wght", 700.0),)
    assert idx.buscar("Quicksand-Black") is None


def test_archivo_cursivo_solo_da_cursivas(carpeta, tmp_path):
    def cursiva(f):
        f["fvar"].instances = []
        f["post"].italicAngle = -10.0
    _variable_modificada(carpeta / "Quicksand.ttf", cursiva)
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Quicksand-BoldItalic").variacion == (("wght", 700.0),)
    assert idx.buscar("Quicksand-Bold") is None


def test_fvar_danada_o_archivo_roto_no_frenan_el_indice(carpeta, tmp_path):
    shutil.copy2(config.DIR_FUENTES_INCLUIDAS / "Carlito-Bold.ttf", carpeta / "Carlito-Bold.ttf")
    (carpeta / "roto.ttf").write_bytes(b"esto no es una fuente")
    datos = bytearray(QUICKSAND_VARIABLE.read_bytes())
    (carpeta / "cortada.ttf").write_bytes(bytes(datos[:2000]))
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Carlito-Bold") is not None


def test_instancia_imposible_no_rompe_el_analisis(instalada, incluidas, monkeypatch):
    """Si no se puede generar la instancia se avisa al aplicar, en vez de explotar."""
    monkeypatch.setattr("pdf_studio.core.fuentes.instanciar_variable",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("fvar rota")))
    bytes_de_fuente.cache_clear()
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        texto = "Zapato"
        analisis = analizar_fuente(doc, doc[0], bloque.estilo, texto, instalada, incluidas)
        opcion = next(o for o in analisis.opciones if o.tipo == "sistema")
        with pytest.raises(Limitacion, match="No se pudo preparar"):
            planificar(doc, bloque, texto, opcion)
    bytes_de_fuente.cache_clear()


def test_editar_escribe_con_la_instancia_y_embebe_estatica(instalada, incluidas, tmp_path):
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    documento = Documento(copia)
    doc = documento.doc
    bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
    texto = "Zapato azul"                      # Z, p, t, z no están en el subconjunto
    analisis = analizar_fuente(doc, doc[0], bloque.estilo, texto, instalada, incluidas)
    previas = {f[0] for f in doc[0].get_fonts(full=True)}
    assert analisis.recomendada.tipo == "sistema"
    assert "wght=700" in analisis.recomendada.descripcion
    documento.ejecutar(planificar(doc, bloque, texto, analisis.recomendada).operacion)
    assert texto in documento.doc[0].get_text()
    fuentes = {f.nombre: f for f in analizar_fuentes(documento.doc)}
    assert "Quicksand-Bold" in fuentes and "Quicksand-Light" not in fuentes
    tramo = next(t for b in lineas_de_pagina(documento.doc[0]) for ln in b for t in ln.tramos if texto in t.texto)
    assert tramo.fuente.endswith("Quicksand-Bold")
    # Lo embebido es una fuente estática y con el peso correcto
    xref = next(f[0] for f in documento.doc[0].get_fonts(full=True)
                if clave_fuente(f[3]) == "quicksandbold" and f[0] not in previas)
    programa = TTFont(io.BytesIO(documento.doc.extract_font(xref)[3]))
    assert "fvar" not in programa and "gvar" not in programa
    assert programa["OS/2"].usWeightClass == 700
    guardado = documento.guardar(tmp_path / "salida.pdf")
    assert guardado.es_fiel, guardado.resumen()


# ---------------------------------------------------------------------- 2. refrescar sin reiniciar
def test_recargar_ve_lo_instalado_por_fuera(carpeta, tmp_path):
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Quicksand-Bold") is None
    shutil.copy2(QUICKSAND_VARIABLE, carpeta / "Quicksand.ttf")
    assert idx.buscar("Quicksand-Bold") is None             # todavía no se volvió a mirar
    idx.recargar()
    assert idx.buscar("Quicksand-Bold") is not None
    (carpeta / "Quicksand.ttf").unlink()
    idx.recargar()
    assert idx.buscar("Quicksand-Bold") is None


def test_recargar_ignora_la_cache_en_disco(carpeta, tmp_path):
    """Un archivo cambiado con el mismo tamaño y fecha engaña a la firma de la caché."""
    import os
    ruta = carpeta / "fuente.ttf"
    shutil.copy2(config.DIR_FUENTES_INCLUIDAS / "Carlito-Bold.ttf", ruta)
    st = ruta.stat()
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    assert idx.buscar("Carlito-Bold") is not None
    nueva = TTFont(str(ruta))
    for registro in nueva["name"].names:
        registro.string = str(registro).replace("Carlito", "Carlita")      # mismo largo
    nueva.save(str(ruta))
    os.utime(ruta, (st.st_atime, st.st_mtime))
    assert ruta.stat().st_size == st.st_size
    idx.cargar()
    assert idx.buscar("Carlita-Bold") is None                # la firma coincide: sale de la caché
    idx.recargar()
    assert idx.buscar("Carlita-Bold") is not None and idx.buscar("Carlito-Bold") is None


def test_recargar_limpia_los_bytes_en_memoria(tmp_path):
    ruta = tmp_path / "f.ttf"
    ruta.write_bytes(b"uno")
    bytes_de_fuente.cache_clear()
    assert bytes_de_fuente(str(ruta)) == b"uno"
    ruta.write_bytes(b"dos")
    assert bytes_de_fuente(str(ruta)) == b"uno"
    IndiceFuentesSistema([tmp_path / "nada"], tmp_path / "c.json").recargar()
    assert bytes_de_fuente(str(ruta)) == b"dos"


def test_recargar_que_falla_conserva_el_indice(instalada, monkeypatch):
    assert instalada.buscar("Quicksand-Bold") is not None
    monkeypatch.setattr(instalada, "_indexar", lambda f: (_ for _ in ()).throw(OSError("disco")))
    with pytest.raises(OSError):
        instalada.recargar()
    assert instalada.buscar("Quicksand-Bold") is not None


def _dialogo(doc, bloque, indice, incluidas, reescanear, qtbot, instalar=None):
    dialogo = DialogoEditarTexto(bloque, lambda t: analizar_edicion(doc, doc[0], bloque, t, indice, incluidas),
                                 reescanear=reescanear, instalar=instalar)
    qtbot.addWidget(dialogo)
    # El test llama a actualizar(): que el temporizador no dispare con el PDF ya cerrado
    dialogo._temporizador.setInterval(10 ** 7)
    return dialogo


def test_boton_ya_la_instale_la_encuentra(carpeta, tmp_path, incluidas, qtbot):
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, idx, incluidas, idx.recargar, qtbot)
        dialogo.editor.setPlainText("Zapato")
        dialogo.actualizar()
        assert "no instalada" in dialogo.info.text()
        assert dialogo.fila_instalar is not None           # Quicksand es libre: se ofrece instalarla
        ya = next(b for b in dialogo.fila_instalar.findChildren(type(dialogo.boton_instalar))
                  if b.text() == "Ya la instalé")
        ya.click()                                          # todavía no está: lo dice, no queda mudo
        assert not dialogo.resultado_busqueda.isHidden()
        assert "no aparece" in dialogo.resultado_busqueda.text()
        shutil.copy2(QUICKSAND_VARIABLE, carpeta / "Quicksand.ttf")
        ya = next(b for b in dialogo.fila_instalar.findChildren(type(dialogo.boton_instalar))
                  if b.text() == "Ya la instalé")
        ya.click()
        assert dialogo.opcion_elegida().tipo == "sistema"
        assert "Encontrada" in dialogo.resultado_busqueda.text()
        assert "instalada (Quicksand.ttf, variable: wght=700)" in dialogo.info.text()
        assert dialogo.fila_instalar is None


def test_boton_refrescar_del_dialogo_si_falla_avisa(carpeta, tmp_path, incluidas, qtbot):
    idx = IndiceFuentesSistema([carpeta], tmp_path / "c.json")

    def falla():
        raise OSError("sin permiso")

    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, idx, incluidas, falla, qtbot)
        dialogo.boton_buscar.click()
        assert "No se pudieron volver a leer" in dialogo.resultado_busqueda.text()


def test_refrescar_de_los_paneles_rescanea_y_chequea(ventana, monkeypatch, qtbot):
    from pdf_studio.ui import ventana_principal
    llamadas = []

    class Indice:
        def recargar(self):
            llamadas.append("recargar")

        def cargar(self):
            pass

    monkeypatch.setattr(ventana_principal, "indice_sistema", lambda: Indice())
    monkeypatch.setattr(ventana, "chequear_dependencias", lambda *a: llamadas.append("chequear"))
    ventana.panel_fuentes.boton_refrescar.click()
    qtbot.waitUntil(lambda: llamadas == ["recargar", "chequear"], timeout=5000)
    llamadas.clear()
    ventana.faltantes.rechequear.emit()
    qtbot.waitUntil(lambda: llamadas == ["recargar", "chequear"], timeout=5000)


def test_el_comando_de_instalacion_no_pide_reiniciar():
    from pdf_studio.dependencias.catalogo_fuentes import buscar_libre
    comando = buscar_libre("Quicksand-Bold").comando
    assert "Reiniciá" not in comando and "no hace falta reiniciar" in comando


# ---------------------------------------------------------------------- 3. clase tipográfica
@pytest.mark.parametrize("nombre", ["Quicksand-Bold", "ABCDEF+Quicksand-Bold", "Quicksand", "Quicksand,Bold"])
def test_quicksand_es_sans_por_nombre_aunque_los_flags_mientan(nombre):
    assert clase_tipografica(nombre, 4) == Clase.SANS


def _sin_declarar(ruta) -> bytes:
    """El programa con PANOSE en cero: lo único que queda es la forma de los glifos."""
    f = TTFont(str(ruta))
    p = f["OS/2"].panose
    p.bFamilyType = p.bSerifStyle = p.bProportion = 0
    f["post"].isFixedPitch = 0
    buf = io.BytesIO()
    f.save(buf)
    return buf.getvalue()


@pytest.mark.parametrize("archivo,clase", [
    ("Quicksand", Clase.SANS), ("LiberationSans-Regular", Clase.SANS), ("LiberationSans-Bold", Clase.SANS),
    ("Carlito-Regular", Clase.SANS), ("LiberationSerif-Regular", Clase.SERIF), ("LiberationSerif-Bold", Clase.SERIF),
    ("Caladea-Regular", Clase.SERIF), ("LiberationMono-Regular", Clase.MONO),
])
def test_clase_por_la_forma_de_los_glifos(archivo, clase):
    """Con un nombre que no dice nada y con los flags de MuPDF (marca serif a casi todo)."""
    ruta = QUICKSAND_VARIABLE if archivo == "Quicksand" else config.DIR_FUENTES_INCLUIDAS / f"{archivo}.ttf"
    assert clase_tipografica("XYZ123", 4, _sin_declarar(ruta)) == clase


def test_sin_programa_legible_siguen_valiendo_los_flags():
    assert clase_tipografica("XYZ123", 4, b"basura") == Clase.SERIF
    assert clase_tipografica("XYZ123", 0, b"basura") == Clase.SANS


@pytest.mark.parametrize("punto", [PUNTO_CUADRADITO, PUNTO_SIN_MAPA])
def test_quicksand_bold_nunca_propone_serif(punto, incluidas, tmp_path, carpeta):
    """El bug: con un carácter sin código se perdía la fuente original y mandaban los flags."""
    vacio = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, punto, Nivel.LINEA)
        assert bloque.estilo.flags & 4                       # MuPDF la marca como serif
        analisis = analizar_fuente(doc, doc[0], bloque.estilo, "Zapato", vacio, incluidas)
        assert analisis.embebido == Embebido.SUBCONJUNTO and analisis.opciones[0].tipo == "original"
        assert analisis.clase == Clase.SANS and analisis.estilo == "bold"
        sustituta = next(o for o in analisis.opciones if o.tipo == "sustituta")
        assert sustituta.nombre == "Liberation Sans Bold"
        assert not any("Serif" in o.nombre or "Mono" in o.nombre for o in analisis.opciones)
        assert analisis.instalable is not None and analisis.instalable.nombre == "Quicksand"


def test_sustitucion_global_tampoco_cambia_de_clase(incluidas):
    with pymupdf.open(PDF) as doc:
        familia = next(f for f in familias_del_documento(doc) if f.clave == "quicksand")
        assert familia.clase == Clase.SANS
        nombres = [c.familia for c in candidatas(doc, familia, incluidas, None)]
        assert nombres[0] == "Liberation Sans" and "Liberation Serif" not in nombres


def test_subconjunto_con_nombre_raro_se_mide_por_el_pdf(incluidas):
    """Sin nombre útil y sin cmap: se llega a los glifos por ToUnicode y CIDToGIDMap."""
    with pymupdf.open(DESTINO / "truetype_sin_cmap.pdf") as doc:
        from pdf_studio.core.fuentes import _mapas_del_pdf, clase_de_fuente_pdf
        xref = next(f[0] for f in doc[0].get_fonts(full=True) if "FacturaSans" in f[3])
        assert clase_de_fuente_pdf(doc, xref, "XYZ123", 4) == Clase.SANS
        datos = doc.extract_font(xref)[3]
        assert clase_tipografica("XYZ123", 4, datos, _mapas_del_pdf(doc, xref, "Type0")) == Clase.SANS


# ---------------------------------------------------------------------- 4. caracteres sin glifo
def test_detecta_el_notdef_por_posicion():
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        assert bloque.texto == "Hola  mundo"
        assert bloque.caracteres_sin_glifo == {""}
        raro = localizar_sin_glifo(bloque.texto, bloque)
        assert len(raro) == 1 and raro[0].codigo == "U+E123" and raro[0].posiciones == (6,)
        assert raro[0].motivo == MotivoSinGlifo.NOTDEF


def test_codigo_sin_mapa_se_lee_fffd_y_no_una_letra_inventada():
    """MuPDF, con su flag por defecto, devolvería el CID 201 como «É»."""
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_SIN_MAPA, Nivel.LINEA)
        assert bloque.texto == "Linea � final"
        raro = localizar_sin_glifo(bloque.texto, bloque)
        assert raro[0].codigo == "U+FFFD" and raro[0].posiciones == (7,)


@pytest.mark.parametrize("c,esperado", [("�", True), ("\x07", True), ("", False), ("", False),
                                        ("a", False), ("ñ", False), ("\n", False), ("͸", True)])
def test_es_no_dibujable(c, esperado):
    assert es_no_dibujable(c) is esperado


def test_la_posicion_sigue_al_texto_editado():
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        raro = localizar_sin_glifo("Ahora dice otra cosa  y ", bloque)
        assert raro[0].posiciones == (22, 26)
        assert localizar_sin_glifo("ya no esta", bloque) == []


@pytest.mark.parametrize("nombre", ["texto_varias_paginas.pdf", "tablas.pdf", "fuentes_recortadas.pdf",
                                    "imagen_fondo.pdf", "formulario.pdf"])
def test_sin_falsos_positivos_en_pdfs_normales(nombre):
    from tests.conftest import DIR_PDFS
    with pymupdf.open(DIR_PDFS / nombre) as doc:
        for pag in doc:
            for lineas in lineas_de_pagina(pag):
                for ln in lineas:
                    assert not [g.c for g in ln.glifos if g.sin_glifo and not g.c.isspace()], ln.texto


def test_espacio_que_dibuja_notdef_no_es_un_problema():
    """Muchos subconjuntos no traen el glifo del espacio; no se ve nada raro."""
    from pdf_studio.core.texto import BloqueTexto, Glifo, Linea, Tramo
    glifos = [Glifo("a", 0, 0, (0, 0, 1, 1)), Glifo(" ", 1, 0, (1, 0, 2, 1), True), Glifo("b", 2, 0, (2, 0, 3, 1))]
    bloque = BloqueTexto(0, Nivel.LINEA, [Linea([Tramo("F", 10, (0, 0, 0), 0, glifos=glifos)])])
    assert bloque.caracteres_sin_glifo == set()


def test_mismo_caracter_con_y_sin_glifo_no_se_marca():
    """Quitarlo borraría también las apariciones que se ven bien."""
    from pdf_studio.core.texto import BloqueTexto, Glifo, Linea, Tramo
    glifos = [Glifo("a", 0, 0, (0, 0, 1, 1)), Glifo("a", 1, 0, (1, 0, 2, 1), True)]
    bloque = BloqueTexto(0, Nivel.LINEA, [Linea([Tramo("F", 10, (0, 0, 0), 0, glifos=glifos)])])
    assert bloque.caracteres_sin_glifo == set()


@pytest.mark.parametrize("punto,codigo,posicion", [(PUNTO_CUADRADITO, "U+E123", 6), (PUNTO_SIN_MAPA, "U+FFFD", 7)])
@pytest.mark.parametrize("boton,reemplazo", [("boton_reemplazar_espacio", " "), ("boton_quitar_caracter", "")])
def test_dialogo_muestra_y_resuelve(punto, codigo, posicion, boton, reemplazo, incluidas, instalada, qtbot):
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, punto, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, instalada, incluidas, instalada.recargar, qtbot)
        assert not dialogo.tarjeta_sin_glifo.isHidden()
        texto = dialogo.etiqueta_sin_glifo.text()
        assert codigo in texto and f"posición {posicion}" in texto
        assert dialogo.editor.extraSelections()              # marcado dentro del texto
        getattr(dialogo, boton).click()
        assert dialogo.tarjeta_sin_glifo.isHidden() and not dialogo.editor.extraSelections()
        assert dialogo.texto == bloque.texto.replace(chr(int(codigo[2:], 16)), reemplazo)
        assert dialogo.sin_glifo == []
        assert dialogo.botones.button(dialogo.botones.StandardButton.Ok).isEnabled()
        assert dialogo.opcion_elegida() is not None


def test_dialogo_con_caracter_sin_glifo_no_bloquea(incluidas, tmp_path, carpeta, qtbot):
    """Lo que no cambia no pide glifo: Aplicar sigue activo sin tener que resolver nada."""
    vacio = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_SIN_MAPA, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, vacio, incluidas, vacio.recargar, qtbot)
        assert dialogo.botones.button(dialogo.botones.StandardButton.Ok).isEnabled()
        assert not dialogo.tarjeta_sin_glifo.isHidden()
        assert dialogo.opcion_elegida().tipo == "original"


def test_deshacer_del_editor_recupera_el_caracter(incluidas, instalada, qtbot):
    with pymupdf.open(PDF) as doc:
        bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
        dialogo = _dialogo(doc, bloque, instalada, incluidas, instalada.recargar, qtbot)
        dialogo.boton_quitar_caracter.click()
        assert "" not in dialogo.texto
        dialogo.editor.undo()
        assert "" in dialogo.texto


@pytest.mark.parametrize("punto,raro", [(PUNTO_CUADRADITO, ""), (PUNTO_SIN_MAPA, "�")])
@pytest.mark.parametrize("reemplazo", [" ", ""])
def test_aplicar_saca_el_caracter_del_pdf(punto, raro, reemplazo, incluidas, tmp_path, carpeta):
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    vacio = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    documento = Documento(copia)
    doc = documento.doc
    bloque = bloque_en(doc[0], 0, punto, Nivel.LINEA)
    otra_linea = "Linea" if punto is PUNTO_CUADRADITO else "Hola"
    texto = bloque.texto.replace(raro, reemplazo)
    analisis = analizar_fuente(doc, doc[0], bloque.estilo, texto, vacio, incluidas)
    assert analisis.recomendada.tipo == "original"
    documento.ejecutar(planificar(doc, bloque, texto, analisis.recomendada).operacion)
    extraido = documento.doc[0].get_text()
    assert raro not in extraido and otra_linea in extraido and "referencia" in extraido
    guardado = documento.guardar(tmp_path / "salida.pdf")
    assert guardado.es_fiel, guardado.resumen()
    documento.deshacer()
    assert raro in documento.doc[0].get_text("text", flags=pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_CID_FOR_UNKNOWN_UNICODE)


def test_editar_otra_palabra_conservando_el_cuadradito(incluidas, tmp_path, carpeta):
    """El carácter mapeado se puede reescribir tal cual: se elige, no se obliga a quitarlo."""
    vacio = IndiceFuentesSistema([carpeta], tmp_path / "c.json")
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    documento = Documento(copia)
    doc = documento.doc
    bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
    texto = "Hola  mundo mundo"
    analisis = analizar_fuente(doc, doc[0], bloque.estilo, texto, vacio, incluidas)
    assert analisis.recomendada.tipo == "original"
    documento.ejecutar(planificar(doc, bloque, texto, analisis.recomendada).operacion)
    assert "mundo mundo" in documento.doc[0].get_text()


def test_mover_el_cuadradito_mapeado_funciona(incluidas, tmp_path):
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    documento = Documento(copia)
    doc = documento.doc
    bloque = bloque_en(doc[0], 0, PUNTO_CUADRADITO, Nivel.LINEA)
    documento.ejecutar(planificar(doc, bloque, bloque.texto, None, (0.0, 12.0)).operacion)
    assert "Hola" in documento.doc[0].get_text()


def test_mover_con_codigo_sin_mapa_conserva_el_codigo(tmp_path):
    """Antes se rechazaba: ahora se mueve con el código original, que sale del contenido."""
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    documento = Documento(copia)
    bloque = bloque_en(documento.doc[0], 0, PUNTO_SIN_MAPA, Nivel.LINEA)
    documento.ejecutar(planificar(documento.doc, bloque, bloque.texto, None, (0.0, 12.0)).operacion)
    assert b"00c9" in documento.doc[0].read_contents().lower()


def test_sustitucion_global_omite_lo_que_no_se_puede_dibujar(incluidas):
    """No reemplaza el cuadradito por un rombo con signo de pregunta: lo informa."""
    from pdf_studio.core.sustitucion import planificar_sustitucion
    with pymupdf.open(PDF) as doc:
        familia = next(f for f in familias_del_documento(doc) if f.clave == "quicksand")
        plan = planificar_sustitucion(doc, {familia.clave: candidatas(doc, familia, incluidas, None)[0]})
        assert any("Linea" in o for o in plan.omitidos) and any("Hola" in o for o in plan.omitidos)


def test_flujo_completo_en_la_ventana(ventana, tmp_path, monkeypatch, qtbot, carpeta):
    """Abrir, editar con el diálogo real y quitar el carácter, con la fuente instalada de verdad."""
    from pdf_studio.ui import ventana_principal
    shutil.copy2(QUICKSAND_VARIABLE, carpeta / "Quicksand.ttf")
    idx = IndiceFuentesSistema([carpeta], tmp_path / "s.json")
    monkeypatch.setattr(ventana_principal, "indice_sistema", lambda: idx)
    copia = tmp_path / "doc.pdf"
    shutil.copy2(PDF, copia)
    ventana.abrir(copia)
    bloque = ventana.seleccionar_texto(0, PUNTO_SIN_MAPA)
    dialogo = DialogoEditarTexto(bloque, lambda t: ventana._analizar(bloque, t), ventana._reescanear_fuentes)
    qtbot.addWidget(dialogo)
    assert "U+FFFD" in dialogo.etiqueta_sin_glifo.text()
    dialogo.boton_quitar_caracter.click()
    assert ventana._aplicar_plan(bloque, dialogo.texto, dialogo.opcion_elegida(), (0.0, 0.0))
    assert "Linea" in ventana.documento.doc[0].get_text()
