"""Instalación asistida: descarga, extracción, confirmación obligatoria y registro de fuentes."""
from __future__ import annotations

import io
import json
import tarfile
import zipfile

import pytest

from pdf_studio import config
from pdf_studio.dependencias import instalador
from pdf_studio.dependencias.catalogo_fuentes import FuenteLibre
from pdf_studio.dependencias.chequeo import Faltante, Importancia
from pdf_studio.dependencias.instalador import (ErrorInstalacion, Instalacion, formato_tamano, instalacion_fuente,
                                                instalar_fuente)

TTF = (config.DIR_FUENTES_INCLUIDAS / "LiberationSans-Regular.ttf").read_bytes()


def _url(ruta) -> str:
    return ruta.resolve().as_uri()


def test_instala_desde_zip_solo_las_fuentes_pedidas(tmp_path):
    zipf = tmp_path / "fuentes.zip"
    with zipfile.ZipFile(zipf, "w") as z:
        z.writestr("dejavu/ttf/DejaVuSans.ttf", TTF)
        z.writestr("dejavu/ttf/DejaVuSans-Bold.ttf", TTF)
        z.writestr("dejavu/ttf/DejaVuSansMono.ttf", TTF)       # otra familia: no
        z.writestr("dejavu/LICENSE", b"texto")
        z.writestr("../../malicioso.ttf", TTF)                  # nunca fuera del destino
    fuente = FuenteLibre("DejaVu Sans", "libre", "archivo", _url(zipf), ("DejaVuSans.ttf", "DejaVuSans-*.ttf"))
    destino = tmp_path / "Fonts"
    progresos = []
    archivos = instalar_fuente(fuente, lambda h, t, m: progresos.append((h, t)), destino, registrar=False)
    assert sorted(a.name for a in archivos) == ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"]
    assert all(a.parent == destino for a in archivos)
    assert not (tmp_path / "malicioso.ttf").exists()
    assert max(h for h, _t in progresos) == zipf.stat().st_size      # se informó toda la descarga
    assert progresos[-1] == (0, 0)                                   # y después la extracción


def test_instala_desde_tar_gz(tmp_path):
    tarf = tmp_path / "lib.tar.gz"
    with tarfile.open(tarf, "w:gz") as t:
        for nombre in ("liberation/LiberationSerif-Regular.ttf", "liberation/LiberationSans-Regular.ttf"):
            info = tarfile.TarInfo(nombre)
            info.size = len(TTF)
            t.addfile(info, io.BytesIO(TTF))
    fuente = FuenteLibre("Liberation Serif", "OFL", "archivo", _url(tarf), ("LiberationSerif-*.ttf",))
    archivos = instalar_fuente(fuente, destino=tmp_path / "F", registrar=False)
    assert [a.name for a in archivos] == ["LiberationSerif-Regular.ttf"]


def test_instala_desde_google_fonts(tmp_path, monkeypatch):
    origen = tmp_path / "Roboto[wdth,wght].ttf"
    origen.write_bytes(TTF)
    listado = [{"name": origen.name, "download_url": _url(origen), "size": len(TTF)},
               {"name": "METADATA.pb", "download_url": "x", "size": 10}]
    monkeypatch.setattr(instalador, "leer_json", lambda url: listado)
    fuente = FuenteLibre("Roboto", "OFL", "google", "https://api.github.com/repos/google/fonts/contents/ofl/roboto")
    assert instalador.tamano_fuente(fuente) == len(TTF)
    archivos = instalar_fuente(fuente, destino=tmp_path / "F", registrar=False)
    assert [a.name for a in archivos] == ["Roboto.ttf"]          # sin '[wdth,wght]'


def test_archivo_sin_la_fuente_falla_claro(tmp_path):
    zipf = tmp_path / "vacio.zip"
    with zipfile.ZipFile(zipf, "w") as z:
        z.writestr("README", b"nada")
    fuente = FuenteLibre("X", "OFL", "archivo", _url(zipf), ("X-*.ttf",))
    with pytest.raises(ErrorInstalacion, match="no contiene"):
        instalar_fuente(fuente, destino=tmp_path / "F", registrar=False)


def test_registrar_fuente_en_hkcu(tmp_path, monkeypatch):
    """Se registra en HKCU (sin admin) con el nombre que espera Windows."""
    escritos = {}

    class ClaveFalsa:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import winreg
    monkeypatch.setattr(winreg, "CreateKeyEx", lambda raiz, ruta, r, acceso: (escritos.setdefault("raiz", raiz),
                                                                             escritos.setdefault("ruta", ruta),
                                                                             ClaveFalsa())[2])
    monkeypatch.setattr(winreg, "SetValueEx", lambda k, nombre, r, tipo, valor: escritos.update(nombre=nombre,
                                                                                               valor=valor))
    ruta = tmp_path / "DejaVuSans-Bold.ttf"
    instalador.registrar_fuente(ruta)
    assert escritos["raiz"] == winreg.HKEY_CURRENT_USER
    assert escritos["ruta"].endswith(r"CurrentVersion\Fonts")
    assert escritos["nombre"] == "DejaVuSans-Bold (TrueType)" and escritos["valor"] == str(ruta)


def test_instalacion_de_fuente_describe_todo():
    from pdf_studio.dependencias.catalogo_fuentes import buscar_libre
    inst = instalacion_fuente(buscar_libre("DejaVuSans-Bold"))
    assert inst.titulo == "Fuente «DejaVu Sans»"
    assert "dejavu-fonts" in inst.origen and inst.licencia
    assert not inst.requiere_admin and "usuario" in inst.destino
    assert inst.tamano() == 5522795
    assert "DejaVuSans-*.ttf" in inst.comando


def test_formato_tamano():
    assert formato_tamano(None) == "desconocido"
    assert formato_tamano(2048) == "2 KB"
    assert formato_tamano(5522795) == "5.3 MB"


def test_faltantes_de_programas_tienen_instalacion(monkeypatch):
    from pdf_studio.dependencias import chequeo
    monkeypatch.setattr(chequeo, "buscar_tesseract", lambda: None)
    monkeypatch.setattr(chequeo, "buscar_ghostscript", lambda: None)
    monkeypatch.setattr(instalador.shutil, "which", lambda n: "winget.exe" if n == "winget" else None)
    falt = {f.clave: f for f in chequeo.chequear_tesseract() + chequeo.chequear_ghostscript()}
    assert falt["programa:tesseract"].instalacion.requiere_admin
    assert not falt["idioma:ocr"].instalacion.requiere_admin
    assert falt["programa:ghostscript"].instalacion.requiere_admin
    assert all(f.comando for f in falt.values())          # siempre también el comando


# ---------------------------------------------------------------------- interfaz
def _instalacion_falsa(registro: list) -> Instalacion:
    return Instalacion("x", "Cosa X", "ejemplo.org", "MIT", "tu usuario", False, "comando-x",
                       lambda: 1234, lambda progreso: (registro.append("instalado"), "Listo")[1])


@pytest.fixture
def ventana(qtbot, tmp_path, monkeypatch):
    from pdf_studio.core.imagenes import BibliotecaFirmas
    from pdf_studio.ui.ventana_principal import VentanaPrincipal
    v = VentanaPrincipal(chequear_al_iniciar=False, biblioteca=BibliotecaFirmas(tmp_path / "f"))
    qtbot.addWidget(v)
    v.mensajes = []
    monkeypatch.setattr(v, "_informar", lambda t, x, error=False: v.mensajes.append((x, error)))
    monkeypatch.setattr(v, "chequear_dependencias", lambda mostrar=True: None)
    return v


def test_sin_confirmacion_no_se_instala(ventana, monkeypatch):
    registro = []
    monkeypatch.setattr(ventana, "_confirmar_instalacion", lambda inst: False)
    assert not ventana.instalar(_instalacion_falsa(registro))
    assert registro == []


def test_con_confirmacion_se_instala_en_segundo_plano(ventana, monkeypatch):
    registro = []
    pedidas = []
    monkeypatch.setattr(ventana, "_confirmar_instalacion", lambda inst: pedidas.append(inst) or True)
    assert ventana.instalar(_instalacion_falsa(registro))
    assert registro == ["instalado"] and len(pedidas) == 1
    assert ventana.mensajes[-1] == ("Listo", False)


def test_error_de_instalacion_se_muestra(ventana, monkeypatch):
    def falla(progreso):
        raise ErrorInstalacion("sin conexión")
    inst = Instalacion("x", "X", "o", "l", "d", False, "c", lambda: None, falla)
    monkeypatch.setattr(ventana, "_confirmar_instalacion", lambda inst: True)
    assert not ventana.instalar(inst)
    assert ventana.mensajes[-1] == ("sin conexión", True)


def test_dialogo_confirmacion_muestra_los_datos(qtbot):
    from PySide6.QtWidgets import QLabel
    from pdf_studio.ui.instalacion import DialogoConfirmarInstalacion
    d = DialogoConfirmarInstalacion(_instalacion_falsa([]))
    qtbot.addWidget(d)
    textos = " ".join(l.text() for l in d.findChildren(QLabel))
    for dato in ("Cosa X", "ejemplo.org", "MIT", "1 KB", "tu usuario", "No hace falta ser administrador"):
        assert dato in textos


def test_tarjeta_faltante_tiene_instalar_y_copiar(ventana, qtbot, monkeypatch):
    llamados = []
    monkeypatch.setattr(ventana, "instalar", lambda inst: llamados.append(inst) or True)
    f = Faltante("x", "Cosa X", "para probar", Importancia.OPCIONAL, "comando-x",
                 instalacion=_instalacion_falsa([]))
    ventana.faltantes.set_generales([f])
    tarjeta = ventana.faltantes.tarjetas()[0]
    assert tarjeta.boton_instalar is not None and tarjeta.boton_copiar is not None
    tarjeta.boton_instalar.click()
    assert llamados and llamados[0].titulo == "Cosa X"


def test_dialogo_texto_instala_y_actualiza(qtbot, tmp_path):
    """Instalar desde el diálogo deja disponible la fuente completa sin reiniciar."""
    import pymupdf
    from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en
    from pdf_studio.core.fuentes import IndiceFuentesSistema
    from pdf_studio.ui.edicion_texto import DialogoEditarTexto
    from tests.test_fuentes_cobertura import TEXTO
    from tests import test_fuentes_cobertura as tfc

    ruta = tfc.pdf_dejavu.__wrapped__(type("F", (), {"mktemp": lambda self, n: tmp_path})())
    doc = pymupdf.open(ruta)
    carpeta = tmp_path / "sistema"
    carpeta.mkdir()
    sistema = IndiceFuentesSistema([carpeta], tmp_path / "s.json")
    bloque = bloque_en(doc[0], 0, pymupdf.Point(100, 96), Nivel.LINEA)

    def instalar(fuente):
        # Simula la instalación: la "DejaVu Sans Bold" aparece en la carpeta de fuentes
        from fontTools.ttLib import TTFont
        f = TTFont(str(config.DIR_FUENTES_INCLUIDAS / "LiberationSans-Bold.ttf"))
        for registro in f["name"].names:
            if registro.nameID in (1, 16):
                registro.string = "DejaVu Sans"
            elif registro.nameID == 6:
                registro.string = "DejaVuSans-Bold"
        f.save(str(carpeta / "DejaVuSans-Bold.ttf"))
        return True

    dialogo = DialogoEditarTexto(bloque, lambda t: analizar_fuente(doc, doc[0], bloque.estilo, t, sistema, None),
                                 reescanear=sistema.cargar, instalar=instalar)
    qtbot.addWidget(dialogo)
    dialogo.editor.setPlainText("HOLA " + TEXTO)
    dialogo.actualizar()
    tipos = [o.tipo for o in dialogo.analisis.opciones]
    assert tipos[0] == "original" and "sistema" not in tipos
    assert dialogo.fila_instalar is not None and dialogo.boton_instalar.isEnabled()
    # El orden: la fila de instalar va antes que la sustituta
    widgets = [dialogo.capa_opciones.itemAt(i).widget() for i in range(dialogo.capa_opciones.count())]
    assert widgets.index(dialogo.fila_instalar) < len(widgets) - 1
    dialogo.boton_instalar.click()
    assert dialogo.opcion_elegida().tipo == "sistema"
    assert dialogo.fila_instalar is None          # ya no hace falta instalar
