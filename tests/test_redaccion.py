"""Fase 7: redacción real y formularios."""
from __future__ import annotations

import pymupdf
import pytest

from pdf_studio.core import formularios as fm
from pdf_studio.core import redaccion as rd
from pdf_studio.core.documento import Documento
from pdf_studio.core.paginas import EliminarPaginas, ReordenarPaginas
from tests.conftest import DIR_PDFS, hash_archivo

DNI, CUIT, CORREO = "30.123.456", "27-30123456-4", "juana.perez@ejemplo.com.ar"


@pytest.fixture
def pdf_datos(tmp_path):
    """PDF sintético con datos personales ficticios en lugares difíciles."""
    d = pymupdf.open()
    p = d.new_page()
    p.insert_text((72, 100), f"Titular: Juana Perez, DNI {DNI}, CUIT {CUIT}", fontsize=11)
    p.insert_text((72, 130), f"Correo: {CORREO}  Tel: (011) 4567-8901", fontsize=11)
    p.insert_text((72, 160), "CBU 0110599520000012345678 y nada más", fontsize=11)
    p.draw_line((72, 140), (500, 140))
    p.add_text_annot((400, 300), f"Revisar CUIT {CUIT}")
    # Texto dentro de un Form XObject (show_pdf_page arma dos niveles de XObject)
    f = pymupdf.open()
    f.new_page(width=300, height=100).insert_text((10, 50), "Otro DNI 22.333.444 fin", fontsize=11)
    p.show_pdf_page(pymupdf.Rect(72, 200, 372, 300), f, 0)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "cuit"
    w.field_value = CUIT
    w.rect = pymupdf.Rect(72, 400, 300, 420)
    p.add_widget(w)
    otra = d.new_page()
    otra.insert_text((72, 100), "Página sin datos", fontsize=11)
    d.set_metadata({"author": f"Juana Perez {DNI}"})
    ruta = tmp_path / "datos.pdf"
    d.save(ruta)
    d.close()
    return ruta


def _todo(doc) -> rd.Redaccion:
    r = rd.Redaccion()
    for patron in rd.PATRONES:
        for c in rd.buscar(doc, rd.compilar(patron.expresion, True)):
            r.agregar(c)
    return r


# ---------------------------------------------------------------------- patrones
@pytest.mark.parametrize("nombre,texto,esperado", [
    ("DNI", "DNI 30.123.456 y 12345678.", ["30.123.456", "12345678"]),
    ("DNI", "CUIT 27-30123456-4", []),                      # no confunde el DNI dentro de un CUIT
    ("DNI", "Monto $ 1.234.567,89", []),                    # ni un importe con decimales
    ("CUIT / CUIL", "27-30123456-4 / 20304567891 / 99-12345678-1", ["27-30123456-4", "20304567891"]),
    ("CBU / CVU", "0110599520000012345678", ["0110599520000012345678"]),
    ("Correo electrónico", "escribí a ana.g+pdf@mail.com.ar.", ["ana.g+pdf@mail.com.ar"]),
    ("Teléfono", "Tel (011) 4567-8901 o 11 2345-6789", ["(011) 4567-8901", "11 2345-6789"]),
])
def test_patrones(nombre, texto, esperado):
    patron = next(p for p in rd.PATRONES if p.nombre == nombre)
    assert [m.group(0) for m in rd.compilar(patron.expresion, True).finditer(texto)] == esperado


def test_expresion_invalida_da_mensaje_claro():
    with pytest.raises(ValueError, match="no es válida"):
        rd.compilar("(abc", True)
    with pytest.raises(ValueError, match="Escribí"):
        rd.compilar("  ", False)


def test_busqueda_encuentra_texto_anotaciones_y_campos(pdf_datos):
    with pymupdf.open(pdf_datos) as d:
        encontrados = rd.buscar(d, rd.compilar(CUIT, False))
    assert sorted(c.origen for c in encontrados) == ["anotación", "campo", "texto"]
    texto = next(c for c in encontrados if c.origen == "texto")
    # La caja abarca exactamente el CUIT (no la palabra con la coma)
    assert texto.caja.width < 80


# ---------------------------------------------------------------------- redacción real
def test_redaccion_real_y_verificada(pdf_datos, tmp_path):
    antes = hash_archivo(pdf_datos)
    doc = Documento(pdf_datos)
    doc.ejecutar(rd.Redactar(_todo(doc.doc)))
    doc.ejecutar(rd.LimpiarMetadatos())
    texto = doc.doc[0].get_text()
    for dato in (DNI, CUIT, CORREO, "22.333.444", "4567-8901", "0110599520000012345678"):
        assert dato not in texto
    assert "Titular: Juana Perez, DNI" in texto and "fin" in texto     # el resto queda
    assert not list(doc.doc[0].annots())                              # la anotación con el CUIT se borró
    assert doc.doc[0].widgets().__next__().field_value == ""
    g = doc.guardar(tmp_path / "redactado.pdf")
    assert g.es_fiel, g.resumen()
    assert not g.incremental and not g.problemas
    assert "reescribió completo" in g.resumen()
    # En el archivo final no queda ni rastro, en ningún objeto
    with pymupdf.open(tmp_path / "redactado.pdf") as d:
        crudo = "".join(rd._flujos_decodificados(d))
        for dato in (DNI, CUIT, CORREO):
            assert dato not in crudo
        assert not d.metadata.get("author")
    doc.cerrar()
    assert hash_archivo(pdf_datos) == antes


def test_lo_que_sigue_en_la_linea_no_se_corre(pdf_datos):
    """MuPDF corre el texto posterior al redactar dentro de un TJ: se evita separando los TJ."""
    doc = Documento(pdf_datos)
    antes = {w[4]: w[0] for w in doc.doc[0].get_text("words")}
    r = rd.Redaccion()
    for c in rd.buscar(doc.doc, rd.compilar(DNI, False)):
        r.agregar(c)
    doc.ejecutar(rd.Redactar(r))
    despues = {w[4]: w[0] for w in doc.doc[0].get_text("words")}
    assert despues["CUIT"] == pytest.approx(antes["CUIT"], abs=0.01)
    doc.cerrar()


def test_separar_tj_no_cambia_el_aspecto(pdf_datos):
    from pdf_studio.core.fidelidad import comparar_paginas
    with pymupdf.open(pdf_datos) as original, pymupdf.open(pdf_datos) as d:
        rd.separar_tj(d, 0)
        assert comparar_paginas(original[0], d[0], 150)[0] == 0
        assert comparar_paginas(original[1], d[1], 150)[0] == 0


def test_metadatos_detectados_si_no_se_borran(pdf_datos):
    doc = Documento(pdf_datos)
    r = rd.Redaccion()
    for c in rd.buscar(doc.doc, rd.compilar(DNI, False)):
        r.agregar(c)
    doc.ejecutar(rd.Redactar(r))
    g = doc.preparar_guardado()
    assert not g.es_fiel
    assert any("aparece dentro del archivo" in p for p in g.problemas)
    assert DNI not in g.resumen()                   # los avisos nunca repiten lo redactado
    doc.cerrar()


def test_zona_marcada_a_mano_borra_imagen_y_dibujos(tmp_path, copia_pdf):
    doc = Documento(copia_pdf("imagen_fondo.pdf"))
    zona = pymupdf.Rect(100, 100, 300, 200)
    r = rd.Redaccion()
    r.agregar_zona(0, zona)
    doc.ejecutar(rd.Redactar(r, rd.OpcionesRedaccion(relleno=None)))
    assert not doc.doc[0].get_text(clip=zona).strip()
    g = doc.guardar(tmp_path / "s.pdf")
    assert g.es_fiel, g.resumen()
    doc.cerrar()


def test_redaccion_sigue_a_la_pagina_movida(pdf_datos, tmp_path):
    doc = Documento(pdf_datos)
    doc.ejecutar(rd.Redactar(_todo(doc.doc)))
    doc.ejecutar(rd.LimpiarMetadatos())
    doc.ejecutar(ReordenarPaginas([1, 0]))
    g = doc.preparar_guardado()
    assert g.es_fiel, g.resumen()
    doc.ejecutar(EliminarPaginas([1], 2))           # se borra la página redactada: no hay nada que verificar
    assert doc.preparar_guardado().es_fiel
    doc.cerrar()


def test_redaccion_vacia():
    with pytest.raises(ValueError):
        rd.Redactar(rd.Redaccion())


def test_redaccion_y_deshacer(pdf_datos):
    doc = Documento(pdf_datos)
    doc.ejecutar(rd.Redactar(_todo(doc.doc)))
    assert DNI not in doc.doc[0].get_text()
    doc.deshacer()
    assert DNI in doc.doc[0].get_text()
    assert not doc.requiere_guardado_completo
    doc.cerrar()


# ---------------------------------------------------------------------- formularios
def test_detectar_campos():
    with pymupdf.open(DIR_PDFS / "formulario.pdf") as d:
        lista = fm.campos(d)
    assert [(c.nombre, c.tipo) for c in lista] == [("nombre", "texto"), ("localidad", "texto"),
                                                   ("acepta", "casilla"), ("turno", "opciones")]
    assert lista[3].opciones == ["Mañana", "Tarde", "Noche"] and lista[3].valor == "Mañana"


def test_completar_y_aplanar(copia_pdf, tmp_path):
    ruta = copia_pdf("formulario.pdf")
    antes = hash_archivo(ruta)
    doc = Documento(ruta)
    doc.ejecutar(fm.CompletarFormulario({"nombre": "Ana Gómez", "acepta": True, "turno": "Tarde"}))
    valores = {c.nombre: c.valor for c in fm.campos(doc.doc)}
    assert valores == {"nombre": "Ana Gómez", "localidad": "", "acepta": True, "turno": "Tarde"}
    g = doc.guardar(tmp_path / "completo.pdf")
    assert g.es_fiel and g.incremental, g.resumen()
    doc.ejecutar(fm.AplanarFormulario())
    assert not fm.tiene_formulario(doc.doc) and "Ana Gómez" in doc.doc[0].get_text()
    g = doc.guardar(tmp_path / "plano.pdf")
    assert g.es_fiel, g.resumen()
    with pymupdf.open(tmp_path / "plano.pdf") as d:
        assert not list(d[0].widgets())
    doc.deshacer()
    assert fm.tiene_formulario(doc.doc)
    doc.cerrar()
    assert hash_archivo(ruta) == antes


def _pdf_con_radios(ruta):
    """Grupo de radio «pago» armado como lo hace Acrobat: estados /Efectivo y /Tarjeta."""
    import pikepdf
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(595, 842))
    pag = pdf.pages[0]
    vacio = pdf.make_stream(b"")
    lleno = pdf.make_stream(b"0 g 3 3 8 8 re f")
    padre = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Btn, Ff=1 << 15, T=pikepdf.String("pago"),
                                                 V=pikepdf.Name.Off, Kids=pikepdf.Array()))
    pag.obj.Annots = pikepdf.Array()
    for i, estado in enumerate(("Efectivo", "Tarjeta")):
        hijo = pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Widget, Parent=padre, AS=pikepdf.Name.Off,
            Rect=[72, 700 - 30 * i, 86, 714 - 30 * i], P=pag.obj,
            AP=pikepdf.Dictionary(N=pikepdf.Dictionary({f"/{estado}": lleno, "/Off": vacio}))))
        padre.Kids.append(hijo)
        pag.obj.Annots.append(hijo)
    pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([padre]))
    pdf.save(ruta)


def test_formulario_con_radios(tmp_path):
    _pdf_con_radios(tmp_path / "radios.pdf")
    doc = Documento(tmp_path / "radios.pdf")
    campo = fm.campos(doc.doc)[0]
    assert campo.tipo == "radio" and len(campo.rects) == 2
    assert campo.opciones == ["Efectivo", "Tarjeta"] and campo.valor == ""
    doc.ejecutar(fm.CompletarFormulario({"pago": "Tarjeta"}))
    assert fm.campos(doc.doc)[0].valor == "Tarjeta"
    assert doc.preparar_guardado().es_fiel
    doc.cerrar()


def test_vaciar_un_campo(copia_pdf):
    doc = Documento(copia_pdf("formulario.pdf"))
    doc.ejecutar(fm.CompletarFormulario({"nombre": "Ana"}))
    doc.ejecutar(fm.CompletarFormulario({"nombre": ""}))
    assert {c.nombre: c.valor for c in fm.campos(doc.doc)}["nombre"] == ""
    assert "Ana" not in doc.doc[0].get_text()
    doc.cerrar()


def test_valores_invalidos(copia_pdf):
    doc = Documento(copia_pdf("formulario.pdf"))
    with pytest.raises(Exception, match="no es una opción"):
        doc.ejecutar(fm.CompletarFormulario({"turno": "Madrugada"}))
    with pytest.raises(Exception, match="No se encontraron"):
        doc.ejecutar(fm.CompletarFormulario({"inexistente": "x"}))
    assert doc.operaciones_activas == []
    doc.cerrar()


# ---------------------------------------------------------------------- interfaz
def test_marcar_y_aplicar_redaccion_en_la_interfaz(ventana, pdf_datos, tmp_path, monkeypatch):
    from pdf_studio.ui.visor import Modo
    ventana.abrir(pdf_datos)
    ventana.acc_modo_redactar.trigger()
    assert ventana.visor.modo == Modo.REDACTAR
    caja = next(w for w in ventana.documento.doc[0].get_text("words") if w[4].startswith("Juana"))
    ventana.visor.rect_seleccionado.emit(0, pymupdf.Rect(caja[:4]))
    assert not ventana.redaccion_pendiente.vacia
    assert len(ventana.visor._marcas_redaccion) == 1
    assert ventana.aviso.isVisible() and "marcado" in ventana.aviso.texto.text()
    assert ventana.acc_aplicar_redaccion.isEnabled()
    assert ventana.documento.operaciones_activas == []        # marcar todavía no borra nada
    # Búsqueda de datos
    monkeypatch.setattr(ventana, "_dialogo_buscar_redactar",
                        lambda: rd.buscar(ventana.documento.doc, rd.compilar(CUIT, False)))
    assert ventana.buscar_para_redactar()
    assert len(ventana.visor._marcas_redaccion) == 4          # zona + texto + anotación + campo
    monkeypatch.setattr(ventana, "_dialogo_aplicar_redaccion", lambda r: (rd.OpcionesRedaccion(), True))
    assert ventana.aplicar_redaccion()
    assert "Juana" not in ventana.documento.doc[0].get_text()
    assert ventana.redaccion_pendiente.vacia and not ventana.visor._marcas_redaccion
    assert not ventana.acc_aplicar_redaccion.isEnabled()
    assert ventana.visor.modo == Modo.MANO
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "r.pdf"))
    assert ventana.guardar_como()
    guardado = ventana.mensajes[-1][1]
    assert guardado.es_fiel and not guardado.incremental, guardado.resumen()
    with pymupdf.open(tmp_path / "r.pdf") as d:
        crudo = "".join(rd._flujos_decodificados(d))
        assert "Juana" not in crudo and CUIT not in crudo


def test_guardar_avisa_si_la_redaccion_no_es_completa(ventana, pdf_datos, tmp_path, monkeypatch):
    """El DNI también está en los metadatos: si no se borran, guardar avisa y no guarda sin permiso."""
    ventana.abrir(pdf_datos)
    monkeypatch.setattr(ventana, "_dialogo_buscar_redactar",
                        lambda: rd.buscar(ventana.documento.doc, rd.compilar(DNI, False)))
    ventana.buscar_para_redactar()
    monkeypatch.setattr(ventana, "_dialogo_aplicar_redaccion", lambda r: (rd.OpcionesRedaccion(), False))
    assert ventana.aplicar_redaccion()
    vistos = []
    monkeypatch.setattr(ventana, "_pedir_ruta_guardar", lambda s: str(tmp_path / "r.pdf"))
    monkeypatch.setattr(ventana, "_preguntar_guardar_igual", lambda g: vistos.append(g) or False)
    assert not ventana.guardar_como()
    assert vistos and any("aparece dentro del archivo" in p for p in vistos[0].problemas)
    assert not (tmp_path / "r.pdf").exists()


def test_marcas_pendientes_se_descartan_si_cambia_el_documento(ventana, pdf_datos):
    ventana.abrir(pdf_datos)
    ventana.marcar_para_redactar(0, pymupdf.Rect(72, 90, 200, 105))
    assert not ventana.redaccion_pendiente.vacia
    ventana.rotar(90)
    assert ventana.redaccion_pendiente.vacia and not ventana.visor._marcas_redaccion


def test_aplicar_sin_marcas_avisa(ventana, pdf_datos):
    ventana.abrir(pdf_datos)
    assert not ventana.aplicar_redaccion()
    assert "No hay nada marcado" in ventana.mensajes[-1][1]


def test_formulario_en_la_barra_lateral(ventana, copia_pdf, monkeypatch):
    ventana.abrir(copia_pdf("formulario.pdf"))
    assert ventana.aviso.isVisible() and "formulario" in ventana.aviso.texto.text()
    panel = ventana.panel_formulario
    assert len(panel.campos) == 4
    panel.editores["nombre"].setText("Ana")
    panel.editores["acepta"].setChecked(True)
    assert panel.cambios() == {"nombre": "Ana", "acepta": True}
    panel.boton_aplicar.click()
    assert {c.nombre: c.valor for c in fm.campos(ventana.documento.doc)}["nombre"] == "Ana"
    assert panel.editores["nombre"].text() == "Ana"           # el panel se recargó
    monkeypatch.setattr(ventana, "_preguntar", lambda t, x: True)
    assert ventana.aplanar_formulario()
    assert ventana.panel_formulario.campos == [] and not ventana.acc_aplanar.isEnabled()
    ventana.acc_deshacer.trigger()
    assert len(ventana.panel_formulario.campos) == 4


def test_dialogos_de_redaccion(qtbot, pdf_datos):
    from pdf_studio.ui.redaccion import DialogoAplicarRedaccion, DialogoBuscarRedactar
    from PySide6.QtWidgets import QDialogButtonBox
    with pymupdf.open(pdf_datos) as doc:
        d = DialogoBuscarRedactar(doc)
        qtbot.addWidget(d)
        d.tipo.setCurrentIndex(0)                 # CUIT/CUIL
        d.buscar()
        assert len(d.coincidencias) == 3 and "3 de 3" in d.resumen.text()
        from PySide6.QtCore import Qt
        d.lista.item(1).setCheckState(Qt.CheckState.Unchecked)
        d.accept()
        assert len(d.elegidas) == 2
        d2 = DialogoBuscarRedactar(doc)
        qtbot.addWidget(d2)
        d2.tipo.setCurrentIndex(d2.tipo.findText("Expresión regular"))
        d2.campo.setText("(roto")
        d2.buscar()
        assert d2.error.isVisibleTo(d2) and "no es válida" in d2.error.text()
        r = rd.Redaccion()
        r.agregar_zona(0, (0, 0, 10, 10))
        a = DialogoAplicarRedaccion(r)
        qtbot.addWidget(a)
        caja = a.findChild(QDialogButtonBox)
        assert caja.button(QDialogButtonBox.StandardButton.Cancel).isDefault()     # lo destructivo no es default
        a.relleno.setCurrentIndex(2)
        a.metadatos.setChecked(True)
        a.accept()
        assert a.opciones.relleno is None and a.borrar_metadatos
