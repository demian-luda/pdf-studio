"""Análisis de las fuentes de un PDF y búsqueda de fuentes instaladas en Windows.

Para cada fuente del PDF se informa:
- si está embebida completa, embebida como subconjunto (recortada) o no embebida;
- si está instalada en Windows (y en qué archivo), buscando por nombre interno.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path

import pymupdf

from pdf_studio import config

_PREFIJO_SUBCONJUNTO = re.compile(r"^[A-Z]{6}\+")

# Las 14 fuentes estándar de PDF: todo lector las tiene, aunque no estén embebidas
ESTANDAR_14 = {
    "courier", "courierbold", "courieroblique", "courierboldoblique",
    "helvetica", "helveticabold", "helveticaoblique", "helveticaboldoblique",
    "timesroman", "timesbold", "timesitalic", "timesbolditalic",
    "symbol", "zapfdingbats",
}

# Palabras de estilo que se sacan del final del nombre para obtener la familia
_ESTILOS = [
    "extrabolditalic", "semibolditalic", "bolditalic", "boldoblique", "lightitalic",
    "mediumitalic", "blackitalic", "thinitalic", "boldit",
    "extrabold", "ultrabold", "semibold", "demibold", "bold", "black", "heavy",
    "extralight", "ultralight", "light", "thin", "medium", "regular",
    "normal", "italic", "oblique", "it",
]
# 'roman', 'book', 'narrow' no se recortan: forman parte de nombres de familia
# (Times New Roman, Book Antiqua, Arial Narrow). Sí valen como estilo tras un guion.

_ALIAS_ESTILO = {
    "regular": "", "roman": "", "book": "", "normal": "", "plain": "",
    "oblique": "italic", "it": "italic",
    "boldoblique": "bolditalic", "boldit": "bolditalic", "italicbold": "bolditalic",
    "obliquebold": "bolditalic",
}


class Embebido(str, Enum):
    COMPLETA = "completa"
    SUBCONJUNTO = "subconjunto"
    NO_EMBEBIDA = "no_embebida"
    TYPE3 = "type3"           # glifos dibujados dentro del PDF; no hay archivo de fuente

    @property
    def texto(self) -> str:
        return {
            "completa": "Embebida completa",
            "subconjunto": "Embebida como subconjunto (recortada)",
            "no_embebida": "No embebida",
            "type3": "Type 3 (dibujada en el PDF)",
        }[self.value]


def _normalizar(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def sin_prefijo(nombre: str) -> str:
    """'ABCDEF+Arial-BoldMT' -> 'Arial-BoldMT'."""
    return _PREFIJO_SUBCONJUNTO.sub("", nombre)


def _limpiar_mt_ps(parte: str) -> str:
    # Sufijos de Monotype/PostScript: 'ArialMT', 'TimesNewRomanPSMT', 'BoldMT'
    for sufijo in ("MT", "PS"):
        if parte.endswith(sufijo) and len(parte) > len(sufijo):
            parte = parte[: -len(sufijo)]
    return parte


def separar_nombre(nombre: str) -> tuple[str, str]:
    """(familia, estilo) normalizados.

    'ABCDEF+Arial-BoldMT' -> ('arial', 'bold');  'Arial,Bold' -> ('arial', 'bold')
    'Liberation Sans Regular' -> ('liberationsans', '');  'TimesNewRomanPSMT' -> ('timesnewroman', '')
    """
    nombre = sin_prefijo(nombre).strip()
    for sep in (",", "-"):
        if sep in nombre:
            familia, estilo = nombre.split(sep, 1)
            familia = _normalizar(_limpiar_mt_ps(familia))
            estilo = _normalizar(_limpiar_mt_ps(estilo))
            break
    else:
        # Sin separador ('Liberation Sans Bold'): se sacan las palabras de estilo del final
        familia = _normalizar(_limpiar_mt_ps(nombre))
        tokens: list[str] = []
        cambio = True
        while cambio:
            cambio = False
            for e in _ESTILOS:
                if familia.endswith(e) and len(familia) > len(e) + 2:
                    familia = familia[: -len(e)]
                    tokens.insert(0, e)
                    cambio = True
                    break
        estilo = "".join("" if t in ("regular", "roman", "book", "normal") else t for t in tokens)
    return familia, _ALIAS_ESTILO.get(estilo, estilo)


def clave_fuente(nombre: str) -> str:
    """Clave normalizada familia+estilo para comparar nombres de distintas fuentes.

    'ABCDEF+Arial-BoldMT', 'Arial,Bold', 'Arial Bold' -> 'arialbold'
    """
    familia, estilo = separar_nombre(nombre)
    return familia + estilo


def familia_de(nombre: str) -> str:
    """Familia normalizada, sin estilo: 'OpenSans-SemiBold' -> 'opensans'."""
    familia = separar_nombre(nombre)[0]
    # 'OpenSans-SemiBold' ya viene separado; 'OpenSansSemiBold' no: se limpia igual
    return separar_nombre(familia)[0] if familia else familia


# ---------------------------------------------------------------------- fuentes del sistema
@dataclass
class FuenteSistema:
    archivo: str
    indice: int              # índice dentro de una colección .ttc
    postscript: str
    familia: str
    estilo: str

    @property
    def nombre_visible(self) -> str:
        return f"{self.familia} {self.estilo}".strip()


def _nombres_ttf(fuente) -> tuple[str, str, str]:
    """(postscript, familia, estilo) desde la tabla 'name' de fontTools."""
    tabla = fuente["name"]

    def leer(*ids: int) -> str:
        for i in ids:
            n = tabla.getDebugName(i)
            if n:
                return n
        return ""

    return leer(6), leer(16, 1), leer(17, 2)


class IndiceFuentesSistema:
    """Índice de las fuentes instaladas (sistema + usuario), con caché en disco."""

    VERSION_CACHE = 1

    def __init__(self, carpetas: list[Path] | None = None, archivo_cache: Path | None = None):
        self.carpetas = carpetas if carpetas is not None else [
            config.DIR_FUENTES_WINDOWS, config.DIR_FUENTES_USUARIO]
        self.archivo_cache = archivo_cache if archivo_cache is not None else (
            config.DIR_CACHE / "fuentes_sistema.json")
        self.fuentes: list[FuenteSistema] = []
        self._por_clave: dict[str, FuenteSistema] = {}
        self._por_familia: dict[str, list[FuenteSistema]] = {}
        self._cargado = False

    def cargar(self) -> None:
        from fontTools.ttLib import TTCollection, TTFont

        cache = self._leer_cache()
        nuevo_cache: dict[str, dict] = {}
        fuentes: list[FuenteSistema] = []
        for carpeta in self.carpetas:
            if not carpeta.is_dir():
                continue
            for archivo in carpeta.iterdir():
                if archivo.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                    continue
                try:
                    st = archivo.stat()
                except OSError:
                    continue
                firma = f"{st.st_size}:{int(st.st_mtime)}"
                previo = cache.get(str(archivo))
                if previo and previo.get("firma") == firma:
                    entradas = previo["fuentes"]
                else:
                    entradas = []
                    try:
                        if archivo.suffix.lower() == ".ttc":
                            col = TTCollection(str(archivo), lazy=True)
                            for i, f in enumerate(col.fonts):
                                entradas.append([i, *_nombres_ttf(f)])
                            col.close()
                        else:
                            f = TTFont(str(archivo), lazy=True)
                            entradas.append([0, *_nombres_ttf(f)])
                            f.close()
                    except Exception:
                        entradas = []  # archivo dañado o formato no soportado
                nuevo_cache[str(archivo)] = {"firma": firma, "fuentes": entradas}
                for indice, ps, fam, est in entradas:
                    fuentes.append(FuenteSistema(str(archivo), indice, ps, fam, est))
        self._escribir_cache(nuevo_cache)
        self._indexar(fuentes)

    def _indexar(self, fuentes: list[FuenteSistema]) -> None:
        self.fuentes = fuentes
        self._por_clave.clear()
        self._por_familia.clear()
        for f in fuentes:
            claves = {clave_fuente(f.postscript)} if f.postscript else set()
            claves.add(clave_fuente(f"{f.familia},{f.estilo}"))
            for c in claves:
                self._por_clave.setdefault(c, f)
            self._por_familia.setdefault(_normalizar(f.familia), []).append(f)
        self._cargado = True

    def _leer_cache(self) -> dict:
        try:
            datos = json.loads(self.archivo_cache.read_text(encoding="utf-8"))
            if datos.get("version") == self.VERSION_CACHE:
                return datos.get("archivos", {})
        except (OSError, ValueError):
            pass
        return {}

    def _escribir_cache(self, archivos: dict) -> None:
        try:
            self.archivo_cache.parent.mkdir(parents=True, exist_ok=True)
            self.archivo_cache.write_text(
                json.dumps({"version": self.VERSION_CACHE, "archivos": archivos}), encoding="utf-8")
        except OSError:
            pass  # sin caché solo es más lento

    def buscar(self, nombre_pdf: str) -> FuenteSistema | None:
        """Busca la fuente instalada que corresponde a un nombre de fuente del PDF."""
        if not self._cargado:
            self.cargar()
        return self._por_clave.get(clave_fuente(nombre_pdf))

    def familia(self, nombre: str) -> list[FuenteSistema]:
        if not self._cargado:
            self.cargar()
        return list(self._por_familia.get(_normalizar(nombre), []))


_indice_global: IndiceFuentesSistema | None = None


def indice_sistema() -> IndiceFuentesSistema:
    global _indice_global
    if _indice_global is None:
        _indice_global = IndiceFuentesSistema()
    return _indice_global


_indice_incluidas: IndiceFuentesSistema | None = None


def indice_incluidas() -> IndiceFuentesSistema:
    """Fuentes libres que vienen con PDF Studio (Liberation, Carlito, Caladea)."""
    global _indice_incluidas
    if _indice_incluidas is None:
        _indice_incluidas = IndiceFuentesSistema(
            [config.DIR_FUENTES_INCLUIDAS], config.DIR_CACHE / "fuentes_incluidas.json")
    return _indice_incluidas


# ---------------------------------------------------------------------- fuentes del PDF
@dataclass
class InfoFuente:
    nombre: str              # nombre sin prefijo de subconjunto
    nombre_pdf: str          # tal cual figura en el PDF
    tipo: str                # Type1, TrueType, Type0, Type3...
    extension: str           # ttf, cff, pfa... o 'n/a'
    embebido: Embebido
    xrefs: list[int] = field(default_factory=list)
    paginas: list[int] = field(default_factory=list)   # índices desde 0
    instalada: FuenteSistema | None = None    # instalada en Windows
    incluida: FuenteSistema | None = None     # viene con PDF Studio

    @property
    def es_estandar(self) -> bool:
        return self.embebido == Embebido.NO_EMBEBIDA and _normalizar(self.nombre) in ESTANDAR_14

    @property
    def familia(self) -> str:
        return familia_de(self.nombre)

    def a_dict(self) -> dict:
        d = asdict(self)
        d["embebido"] = self.embebido.value
        return d


def clasificar_embebido(nombre: str, tipo: str, extension: str) -> Embebido:
    if tipo == "Type3":
        return Embebido.TYPE3
    if not extension or extension == "n/a":
        return Embebido.NO_EMBEBIDA
    if _PREFIJO_SUBCONJUNTO.match(nombre):
        return Embebido.SUBCONJUNTO
    return Embebido.COMPLETA


def analizar_fuentes(doc: pymupdf.Document,
                     indice: IndiceFuentesSistema | None = None,
                     incluidas: IndiceFuentesSistema | None = None) -> list[InfoFuente]:
    """Lista las fuentes del documento, agrupadas por nombre y tipo de embebido."""
    grupos: dict[tuple[str, Embebido], InfoFuente] = {}
    for pno in range(doc.page_count):
        for xref, ext, tipo, basefont, _ref, _enc in doc.get_page_fonts(pno):
            nombre_pdf = basefont or f"(sin nombre, xref {xref})"
            emb = clasificar_embebido(nombre_pdf, tipo, ext)
            clave = (sin_prefijo(nombre_pdf), emb)
            info = grupos.get(clave)
            if info is None:
                info = InfoFuente(sin_prefijo(nombre_pdf), nombre_pdf, tipo, ext, emb)
                grupos[clave] = info
            if xref not in info.xrefs:
                info.xrefs.append(xref)
            if pno not in info.paginas:
                info.paginas.append(pno)
    fuentes = sorted(grupos.values(), key=lambda f: f.nombre.lower())
    for f in fuentes:
        if f.embebido == Embebido.TYPE3:
            continue
        if indice is not None:
            f.instalada = indice.buscar(f.nombre)
        if incluidas is not None:
            f.incluida = incluidas.buscar(f.nombre)
    return fuentes
