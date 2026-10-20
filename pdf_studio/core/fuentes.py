"""Análisis de las fuentes de un PDF y búsqueda de fuentes instaladas en Windows.

Para cada fuente del PDF se informa:
- si está embebida completa, embebida como subconjunto (recortada) o no embebida;
- si está instalada en Windows (y en qué archivo), buscando por nombre interno.
"""
from __future__ import annotations

import io
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


# Letras que toda fuente de texto latina completa tiene
LETRAS_BASICAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def _programa_vacio(programa: list) -> bool:
    """Un charstring CFF sin trazos: solo números (ancho) y 'endchar'."""
    return all(not isinstance(x, str) or x == "endchar" for x in programa)


def letras_con_glifo(datos: bytes) -> tuple[set[str] | None, int]:
    """(letras básicas que tienen un glifo con contorno real, cantidad total de glifos).
    Las letras son None si el programa de fuente no trae un cmap usable."""
    from fontTools.ttLib import TTFont
    try:
        f = TTFont(io.BytesIO(datos), lazy=True)
        total = f["maxp"].numGlyphs
    except Exception:
        return _letras_cff(datos)
    cmap = f.getBestCmap()
    if not cmap:
        return None, total
    presentes: set[str] = set()
    if "glyf" in f:
        glyf = f["glyf"]
        for ch in LETRAS_BASICAS:
            nombre = cmap.get(ord(ch))
            if nombre and nombre in glyf and glyf[nombre].numberOfContours != 0:
                presentes.add(ch)
    elif "CFF " in f:
        charstrings = f["CFF "].cff.topDictIndex[0].CharStrings
        for ch in LETRAS_BASICAS:
            nombre = cmap.get(ord(ch))
            if nombre and nombre in charstrings:
                cs = charstrings[nombre]
                cs.decompile()
                if not _programa_vacio(cs.program):
                    presentes.add(ch)
    return presentes, total


def _letras_cff(datos: bytes) -> tuple[set[str] | None, int]:
    """Lo mismo para un CFF "pelado" (FontFile3): se usan los nombres de glifo."""
    from fontTools import agl
    from fontTools.cffLib import CFFFontSet
    try:
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(datos), None)
        charstrings = cff[cff.fontNames[0]].CharStrings
    except Exception:
        return None, 0
    nombres = set(charstrings.keys())
    if all(n.startswith("cid") or n == ".notdef" for n in nombres):
        return None, len(nombres)          # CFF con CIDs: sin nombres no se sabe qué letra es cuál
    presentes = set()
    for ch in LETRAS_BASICAS:
        nombre = agl.UV2AGL.get(ord(ch))
        if nombre in nombres:
            cs = charstrings[nombre]
            cs.decompile()
            if not _programa_vacio(cs.program):
                presentes.add(ch)
    return presentes, len(nombres)


def es_subconjunto_por_cobertura(datos: bytes) -> bool | None:
    """True si al programa de fuente le faltan letras básicas (está recortado aunque no
    tenga el prefijo ABCDEF+). None si no se puede saber."""
    letras, total = letras_con_glifo(datos)
    if letras is None:
        return (total < 150) if total else None
    if not letras and total >= 200:
        return False          # fuente de símbolos o de otro alfabeto, completa
    return len(letras) < len(LETRAS_BASICAS)


def clasificar_fuente(doc: pymupdf.Document, xref: int, nombre: str, tipo: str, extension: str) -> Embebido:
    """Como clasificar_embebido, pero mirando la cobertura real de glifos: una fuente
    embebida sin prefijo a la que le faltan letras es un subconjunto."""
    base = clasificar_embebido(nombre, tipo, extension)
    if base != Embebido.COMPLETA:
        return base
    try:
        datos = doc.extract_font(xref)[3]
    except Exception:
        datos = b""
    if datos and es_subconjunto_por_cobertura(datos):
        return Embebido.SUBCONJUNTO
    return base


# ---------------------------------------------------------------------- clase tipográfica
class Clase:
    SANS = "sans"
    SERIF = "serif"
    MONO = "mono"


_MONO = ("mono", "courier", "consol", "code", "typewriter", "fixed", "lucidaconsole", "menlo", "inconsolata")
_SANS = ("sans", "arial", "helvetica", "calibri", "carlito", "verdana", "tahoma", "segoe", "roboto", "lato",
         "montserrat", "frutiger", "futura", "gill", "univers", "myriad", "trebuchet", "centurygothic", "candara",
         "corbel", "aptos", "inter", "ubuntu", "raleway", "poppins", "nunito", "franklin", "avenir", "dejavusans",
         "liberationsans", "arimo", "notosans", "sourcesans", "ptsans", "worksans", "manrope", "lexend")
_SERIF = ("serif", "times", "tinos", "georgia", "garamond", "cambria", "caladea", "palatino", "bookantiqua",
          "minion", "baskerville", "bodoni", "centuryschoolbook", "constantia", "didot", "merriweather",
          "lora", "crimson", "gelasio", "charter", "cochin", "plantin", "sabon", "caslon", "bookman")


def clase_tipografica(nombre: str, flags: int = 0, datos: bytes | None = None) -> str:
    """sans / serif / mono, en este orden de confianza: nombre de la familia, datos del
    programa de fuente (PANOSE, ancho fijo) y, por último, los flags del PDF (que muchos
    generadores llenan mal)."""
    familia = familia_de(nombre)
    if any(k in familia for k in _MONO):
        return Clase.MONO
    # 'sans' antes que 'serif': "DejaVuSans", "Liberation Sans", "PT Sans"...
    if any(k in familia for k in _SANS):
        return Clase.SANS
    if any(k in familia for k in _SERIF):
        return Clase.SERIF
    if datos:
        try:
            from fontTools.ttLib import TTFont
            f = TTFont(io.BytesIO(datos), lazy=True)
            if "post" in f and f["post"].isFixedPitch:
                return Clase.MONO
            if "OS/2" in f:
                panose = f["OS/2"].panose
                if panose.bFamilyType == 2:
                    if panose.bProportion == 9:
                        return Clase.MONO
                    if panose.bSerifStyle in (11, 12, 13):
                        return Clase.SANS
                    if 2 <= panose.bSerifStyle <= 10:
                        return Clase.SERIF
        except Exception:
            pass
    if flags & 8:
        return Clase.MONO
    if flags & 4:
        return Clase.SERIF
    return Clase.SANS


def estilo_de(nombre: str, flags: int = 0) -> str:
    """'', 'bold', 'italic' o 'bolditalic', según el nombre y los flags."""
    estilo = separar_nombre(nombre)[1]
    negrita = bool(flags & 16) or any(k in estilo for k in ("bold", "black", "heavy", "semibold", "demibold"))
    italica = bool(flags & 2) or any(k in estilo for k in ("italic", "oblique"))
    return {(True, True): "bolditalic", (True, False): "bold", (False, True): "italic"}.get((negrita, italica), "")


def analizar_fuentes(doc: pymupdf.Document,
                     indice: IndiceFuentesSistema | None = None,
                     incluidas: IndiceFuentesSistema | None = None) -> list[InfoFuente]:
    """Lista las fuentes del documento, agrupadas por nombre y tipo de embebido."""
    grupos: dict[tuple[str, Embebido], InfoFuente] = {}
    clasificadas: dict[int, Embebido] = {}
    for pno in range(doc.page_count):
        for xref, ext, tipo, basefont, _ref, _enc in doc.get_page_fonts(pno):
            nombre_pdf = basefont or f"(sin nombre, xref {xref})"
            if xref not in clasificadas:
                clasificadas[xref] = clasificar_fuente(doc, xref, nombre_pdf, tipo, ext)
            emb = clasificadas[xref]
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
