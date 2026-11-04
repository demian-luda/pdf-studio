"""Análisis de las fuentes de un PDF y búsqueda de fuentes instaladas en Windows.

Para cada fuente del PDF se informa:
- si está embebida completa, embebida como subconjunto (recortada) o no embebida;
- si está instalada en Windows (y en qué archivo), buscando por nombre interno.

Las fuentes variables (un archivo con eje wght, por ejemplo) se indexan una vez por
instancia: "Quicksand-Bold" es Quicksand.ttf con wght=700. Al escribir con una de
ellas se genera la instancia estática, porque un PDF no puede usar una variable.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import statistics
import threading
import unicodedata
from dataclasses import asdict, dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

import pymupdf

from pdf_studio import config

# fontTools avisa por cada detalle menor de las fuentes (fechas raras, tablas extra): no interesa
logging.getLogger("fontTools").setLevel(logging.ERROR)

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
    NO_ANALIZADA = "no_analizada"   # embebida, pero su programa de fuente no se pudo leer

    @property
    def texto(self) -> str:
        return {
            "completa": "Embebida completa",
            "subconjunto": "Embebida como subconjunto (recortada)",
            "no_embebida": "No embebida",
            "type3": "Type 3 (dibujada en el PDF)",
            "no_analizada": "Embebida (no se pudo analizar)",
        }[self.value]

    @property
    def solo_letras_usadas(self) -> bool:
        """Si para escribir texto nuevo solo se puede contar con las letras ya usadas."""
        return self in (Embebido.SUBCONJUNTO, Embebido.TYPE3, Embebido.NO_ANALIZADA)


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
    variacion: tuple = ()    # fuente variable: ((eje, valor), ...) de esta instancia; () si es estática

    def __post_init__(self) -> None:
        # Desde JSON llegan listas: tiene que ser hasheable para la caché de bytes_de_fuente
        self.variacion = tuple((eje, float(valor)) for eje, valor in self.variacion)

    @property
    def nombre_visible(self) -> str:
        return f"{self.familia} {self.estilo}".strip()

    @property
    def es_variable(self) -> bool:
        return bool(self.variacion)

    @property
    def detalle_variacion(self) -> str:
        """'wght=700' para mostrar; vacío si la fuente es estática."""
        return ", ".join(f"{eje}={valor:g}" for eje, valor in self.variacion)

    def datos(self) -> bytes:
        """Bytes de esta cara, listos para usar (estática, aunque venga de una variable)."""
        return bytes_de_fuente(self.archivo, self.indice, self.variacion,
                               self.postscript if self.variacion else "")


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


# Pesos estándar (OpenType): sirven cuando la fuente tiene el eje pero no los nombra
_PESOS = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium",
          600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
# Otros nombres con los que los PDF llaman al mismo peso
_SINONIMOS_PESO = {"semibold": "demibold", "extrabold": "ultrabold", "extralight": "ultralight", "black": "heavy"}


def _pesos_de_stat(f) -> list[tuple[str, float]]:
    """(nombre, valor) de cada peso que declara la tabla STAT."""
    if "STAT" not in f:
        return []
    stat = f["STAT"].table
    if stat.AxisValueArray is None or stat.DesignAxisRecord is None:
        return []
    ejes = [r.AxisTag for r in stat.DesignAxisRecord.Axis]
    pesos = []
    for v in stat.AxisValueArray.AxisValue:
        if v.Format in (1, 3):
            eje, valor = ejes[v.AxisIndex], v.Value
        elif v.Format == 2:
            eje, valor = ejes[v.AxisIndex], v.NominalValue
        elif v.Format == 4 and len(v.AxisValueRecord) == 1:
            eje, valor = ejes[v.AxisValueRecord[0].AxisIndex], v.AxisValueRecord[0].Value
        else:
            continue
        nombre = f["name"].getDebugName(v.ValueNameID)
        if eje == "wght" and nombre:
            pesos.append((nombre, valor))
    return pesos


def _instancias_variables(f, familia: str, estilo: str) -> list[list]:
    """[postscript, familia, estilo, variacion] de cada instancia de una fuente variable.

    Fuentes, de mayor a menor autoridad: las instancias con nombre de fvar, los pesos de
    STAT y los pesos estándar que entren en el rango del eje wght. Nunca se inventa
    un peso fuera del rango ni una cursiva que el archivo no tenga."""
    fvar = f["fvar"]
    tabla = f["name"]
    ejes = {a.axisTag: a for a in fvar.axes}
    defecto = {tag: a.defaultValue for tag, a in ejes.items()}
    caras: dict[str, list] = {}

    def agregar(nombre: str, coordenadas: dict, postscript: str = "") -> None:
        completas = {**defecto, **coordenadas}
        if any(tag not in ejes or not ejes[tag].minValue <= v <= ejes[tag].maxValue
               for tag, v in completas.items()):
            return
        clave = _normalizar(nombre)
        if not clave or clave in caras:
            return
        postscript = postscript or f"{familia.replace(' ', '')}-{nombre.replace(' ', '')}"
        caras[clave] = [postscript, familia, nombre, sorted(completas.items())]

    for instancia in fvar.instances:
        nombre = tabla.getDebugName(instancia.subfamilyNameID)
        ps = tabla.getDebugName(instancia.postscriptNameID) if instancia.postscriptNameID != 0xFFFF else ""
        if nombre:
            agregar(nombre, instancia.coordinates, ps or "")
    agregar(estilo or "Regular", {})      # el master del archivo, si no tiene nombre propio
    if "wght" in ejes:
        cursiva = bool(f["post"].italicAngle) if "post" in f else False

        def con_cursiva(nombre: str) -> str:
            if not cursiva:
                return nombre
            return "Italic" if nombre == "Regular" else f"{nombre} Italic"

        try:
            for nombre, valor in _pesos_de_stat(f):
                agregar(con_cursiva(nombre), {"wght": valor})
        except (AttributeError, IndexError):
            pass      # STAT mal armada: alcanza con fvar y los pesos estándar
        for valor, nombre in _PESOS.items():
            agregar(con_cursiva(nombre), {"wght": float(valor)})
    return list(caras.values())


def _caras_de_fuente(f, indice: int) -> list[list]:
    """Entradas del índice para una fuente abierta: una, o una por instancia si es variable."""
    postscript, familia, estilo = _nombres_ttf(f)
    if "fvar" in f:
        try:
            instancias = _instancias_variables(f, familia, estilo)
            if instancias:
                return [[indice, *i] for i in instancias]
        except Exception:
            pass      # tablas variables dañadas: queda como una fuente común con su peso por defecto
    return [[indice, postscript, familia, estilo, []]]


def instanciar_variable(datos: bytes, variacion: tuple, postscript: str = "") -> bytes:
    """Genera la instancia estática de una fuente variable, con su propio nombre interno
    (si no, en el PDF figuraría con el nombre del master: 'Quicksand-Light')."""
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    fuente = TTFont(io.BytesIO(datos))
    instancer.instantiateVariableFont(fuente, dict(variacion), inplace=True)
    if "CFF2" in fuente:
        # PDF solo admite CFF: una instancia CFF2 sin convertir no se podría embeber
        from fontTools.cffLib.CFF2ToCFF import convertCFF2ToCFF
        convertCFF2ToCFF(fuente)
    if postscript:
        familia, _, estilo = postscript.partition("-")
        completo = postscript.replace("-", " ")
        valores = {1: completo, 2: "Regular", 4: completo, 6: postscript, 16: familia, 17: estilo or "Regular"}
        for registro in fuente["name"].names:
            if registro.nameID in valores:
                registro.string = valores[registro.nameID]
    salida = io.BytesIO()
    fuente.save(salida)
    return salida.getvalue()


@lru_cache(maxsize=32)
def bytes_de_fuente(archivo: str, indice: int = 0, variacion: tuple = (), postscript: str = "") -> bytes:
    """Bytes de una fuente; de una colección .ttc se extrae la cara pedida y de una
    variable, la instancia pedida."""
    ruta = Path(archivo)
    if ruta.suffix.lower() == ".ttc":
        from fontTools.ttLib import TTCollection
        col = TTCollection(str(ruta))
        buf = io.BytesIO()
        col.fonts[indice].save(buf)
        datos = buf.getvalue()
    else:
        datos = ruta.read_bytes()
    return instanciar_variable(datos, variacion, postscript) if variacion else datos


class IndiceFuentesSistema:
    """Índice de las fuentes instaladas (sistema + usuario), con caché en disco."""

    VERSION_CACHE = 2       # 2: entradas con la variación de las fuentes variables

    def __init__(self, carpetas: list[Path] | None = None, archivo_cache: Path | None = None):
        self.carpetas = carpetas if carpetas is not None else [
            config.DIR_FUENTES_WINDOWS, config.DIR_FUENTES_USUARIO]
        self.archivo_cache = archivo_cache if archivo_cache is not None else (
            config.DIR_CACHE / "fuentes_sistema.json")
        self.fuentes: list[FuenteSistema] = []
        self._por_clave: dict[str, FuenteSistema] = {}
        self._por_familia: dict[str, list[FuenteSistema]] = {}
        self._cargado = False
        self._candado = threading.RLock()

    @property
    def cargado(self) -> bool:
        return self._cargado

    def cargar(self) -> None:
        with self._candado:
            self._cargar()

    def recargar(self) -> None:
        """Olvida lo que se sabía de las fuentes y vuelve a leer las carpetas: para cuando se
        instaló o se cambió una fuente con la app abierta. Si algo falla, queda el índice anterior."""
        with self._candado:
            bytes_de_fuente.cache_clear()
            self._cargar(usar_cache=False)

    def _cargar(self, usar_cache: bool = True) -> None:
        from fontTools.ttLib import TTCollection, TTFont

        cache = self._leer_cache() if usar_cache else {}
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
                                entradas.extend(_caras_de_fuente(f, i))
                            col.close()
                        else:
                            f = TTFont(str(archivo), lazy=True)
                            entradas.extend(_caras_de_fuente(f, 0))
                            f.close()
                    except Exception:
                        entradas = []  # archivo dañado o formato no soportado
                nuevo_cache[str(archivo)] = {"firma": firma, "fuentes": entradas}
                for indice, ps, fam, est, variacion in entradas:
                    fuentes.append(FuenteSistema(str(archivo), indice, ps, fam, est, variacion))
        self._escribir_cache(nuevo_cache)
        self._indexar(fuentes)

    def _indexar(self, fuentes: list[FuenteSistema]) -> None:
        # Si hay una estática y una variable de la misma cara, se prefiere la estática
        fuentes = sorted(fuentes, key=lambda f: f.es_variable)
        self.fuentes = fuentes
        self._por_clave.clear()
        self._por_familia.clear()
        for f in fuentes:
            claves = {clave_fuente(f.postscript)} if f.postscript else set()
            claves.add(clave_fuente(f"{f.familia},{f.estilo}"))
            if f.es_variable:
                estilo = _normalizar(f.estilo)
                for peso, sinonimo in _SINONIMOS_PESO.items():
                    if peso in estilo:
                        claves.add(_normalizar(f.familia) + estilo.replace(peso, sinonimo))
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
            # Atómico: el proceso de análisis puede estar leyendo la caché en este momento
            temporal = self.archivo_cache.with_name(f"{self.archivo_cache.name}.{os.getpid()}.tmp")
            temporal.write_text(json.dumps({"version": self.VERSION_CACHE, "archivos": archivos}), encoding="utf-8")
            os.replace(temporal, self.archivo_cache)
        except OSError:
            pass  # sin caché solo es más lento

    def buscar(self, nombre_pdf: str) -> FuenteSistema | None:
        """Busca la fuente instalada que corresponde a un nombre de fuente del PDF."""
        with self._candado:
            if not self._cargado:
                self._cargar()
            return self._por_clave.get(clave_fuente(nombre_pdf))

    def familia(self, nombre: str) -> list[FuenteSistema]:
        with self._candado:
            if not self._cargado:
                self._cargar()
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
    error: str = ""                           # por qué no se pudo analizar (tipo de error)

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

    @classmethod
    def desde_dict(cls, d: dict) -> "InfoFuente":
        d = dict(d)
        d["embebido"] = Embebido(d["embebido"])
        for clave in ("instalada", "incluida"):
            if d.get(clave):
                d[clave] = FuenteSistema(**d[clave])
        return cls(**d)


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


def _verificador_de_contorno(f):
    """Función gid -> bool que dice si el glifo tiene trazos (None si no hay glifos legibles)."""
    orden = f.getGlyphOrder()
    if "glyf" in f:
        glyf = f["glyf"]

        def tiene(gid: int) -> bool:
            return 0 <= gid < len(orden) and glyf[orden[gid]].numberOfContours != 0
        return tiene
    if "CFF " in f:
        charstrings = f["CFF "].cff.topDictIndex[0].CharStrings

        def tiene(gid: int) -> bool:
            if not 0 <= gid < len(orden) or orden[gid] not in charstrings:
                return False
            cs = charstrings[orden[gid]]
            cs.decompile()
            return not _programa_vacio(cs.program)
        return tiene
    return None


def letras_con_glifo(datos: bytes, unicode_a_codigo: dict[str, int] | None = None,
                     codigo_a_gid=None) -> tuple[set[str] | None, int]:
    """(letras básicas que tienen un glifo con contorno real, cantidad total de glifos).

    Primero se usa el cmap del programa de fuente. Muchos subconjuntos (sobre todo
    TrueType CID) no traen cmap: en ese caso, si se pasan los mapas del PDF
    (ToUnicode y CIDToGIDMap), se llega al glifo por ese camino. Las letras son
    None si no hay forma de saber qué glifo es cada letra."""
    from fontTools.ttLib import TTFont
    try:
        f = TTFont(io.BytesIO(datos), lazy=True)
        orden = f.getGlyphOrder()
        total = f["maxp"].numGlyphs if "maxp" in f else len(orden)
    except Exception:
        return _letras_cff(datos)
    tiene = _verificador_de_contorno(f)
    if tiene is None:
        return None, total
    cmap = None
    if "cmap" in f:
        try:
            cmap = f.getBestCmap()
        except Exception:
            cmap = None
    presentes: set[str] = set()
    if cmap:
        for ch in LETRAS_BASICAS:
            nombre = cmap.get(ord(ch))
            if nombre is not None:
                try:
                    if tiene(f.getGlyphID(nombre)):
                        presentes.add(ch)
                except Exception:
                    pass
        return presentes, total
    if unicode_a_codigo and codigo_a_gid is not None:
        for ch in LETRAS_BASICAS:
            codigo = unicode_a_codigo.get(ch)
            gid = codigo_a_gid(codigo) if codigo is not None else None
            if gid is not None and tiene(gid):
                presentes.add(ch)
        return presentes, total
    return None, total

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
            try:
                cs = charstrings[nombre]
                cs.decompile()
                if not _programa_vacio(cs.program):
                    presentes.add(ch)
            except Exception:
                pass
    return presentes, len(nombres)


def es_subconjunto_por_cobertura(datos: bytes, unicode_a_codigo: dict[str, int] | None = None,
                                 codigo_a_gid=None) -> bool | None:
    """True si al programa de fuente le faltan letras básicas (está recortado aunque no
    tenga el prefijo ABCDEF+). None si no se puede saber."""
    letras, total = letras_con_glifo(datos, unicode_a_codigo, codigo_a_gid)
    if letras is None:
        if not total:
            total = _glifos_segun_pymupdf(datos)
        return (total < 150) if total else None
    if not letras and total >= 200:
        return False          # fuente de símbolos o de otro alfabeto, completa
    return len(letras) < len(LETRAS_BASICAS)


def _glifos_segun_pymupdf(datos: bytes) -> int:
    """Último recurso: cuántos glifos tiene la fuente según MuPDF (sirve para Type1 y CFF raros)."""
    try:
        return pymupdf.Font(fontbuffer=datos).glyph_count
    except Exception:
        return 0


def _mapas_del_pdf(doc: pymupdf.Document, xref: int, tipo: str):
    """(unicode -> código, código -> gid) a partir del /ToUnicode y del /CIDToGIDMap."""
    from pdf_studio.core.codificacion import parsear_tounicode
    t, v = doc.xref_get_key(xref, "ToUnicode")
    if t != "xref":
        return None, None
    mapa, _ = parsear_tounicode(doc.xref_stream(int(v.split()[0])) or b"")
    inverso: dict[str, int] = {}
    for codigo, texto in sorted(mapa.items()):
        if len(texto) == 1 and texto not in inverso:
            inverso[texto] = codigo
    if tipo != "Type0":
        return inverso, None          # fuente simple sin cmap: el código no dice qué glifo es
    t, v = doc.xref_get_key(xref, "DescendantFonts")
    m = re.search(r"(\d+)\s+0\s+R", v if t == "array" else doc.xref_object(int(v.split()[0])) if t == "xref" else "")
    if not m:
        return inverso, None
    desc = int(m.group(1))
    t, v = doc.xref_get_key(desc, "CIDToGIDMap")
    if t == "xref":
        tabla = doc.xref_stream(int(v.split()[0])) or b""

        def codigo_a_gid(cid: int) -> int | None:
            if 2 * cid + 1 < len(tabla):
                return tabla[2 * cid] << 8 | tabla[2 * cid + 1]
            return None
        return inverso, codigo_a_gid
    return inverso, (lambda cid: cid)       # Identity (o ausente): CID = número de glifo


def clasificar_fuente(doc: pymupdf.Document, xref: int, nombre: str, tipo: str, extension: str) -> Embebido:
    """Como clasificar_embebido, pero mirando la cobertura real de glifos: una fuente
    embebida sin prefijo a la que le faltan letras es un subconjunto.
    Nunca lanza excepciones: si no se puede leer, devuelve NO_ANALIZADA."""
    base = clasificar_embebido(nombre, tipo, extension)
    if base != Embebido.COMPLETA:
        return base
    try:
        datos = doc.extract_font(xref)[3]
    except Exception:
        return Embebido.NO_ANALIZADA
    if not datos:
        return Embebido.NO_ANALIZADA
    try:
        inverso, a_gid = _mapas_del_pdf(doc, xref, tipo)
    except Exception:
        inverso, a_gid = None, None
    try:
        recortada = es_subconjunto_por_cobertura(datos, inverso, a_gid)
    except Exception:
        recortada = None
    if recortada is None:
        return Embebido.NO_ANALIZADA
    return Embebido.SUBCONJUNTO if recortada else Embebido.COMPLETA

# ---------------------------------------------------------------------- clase tipográfica
class Clase:
    SANS = "sans"
    SERIF = "serif"
    MONO = "mono"


_MONO = ("mono", "courier", "consol", "code", "typewriter", "fixed", "lucidaconsole", "menlo", "inconsolata")
_SANS = ("sans", "arial", "helvetica", "calibri", "carlito", "verdana", "tahoma", "segoe", "roboto", "lato",
         "montserrat", "frutiger", "futura", "gill", "univers", "myriad", "trebuchet", "centurygothic", "candara",
         "corbel", "aptos", "inter", "ubuntu", "raleway", "poppins", "nunito", "franklin", "avenir", "dejavusans",
         "liberationsans", "arimo", "notosans", "sourcesans", "ptsans", "worksans", "manrope", "lexend",
         # Sans cuyo nombre no lo dice, frecuentes en Google Fonts
         "quicksand", "comfortaa", "varelaround", "dosis", "oswald", "rubik", "mulish", "karla", "barlow",
         "josefin", "titillium", "heebo", "questrial", "catamaran", "bahnschrift")
_SERIF = ("serif", "times", "tinos", "georgia", "garamond", "cambria", "caladea", "palatino", "bookantiqua",
          "minion", "baskerville", "bodoni", "centuryschoolbook", "constantia", "didot", "merriweather",
          "lora", "crimson", "gelasio", "charter", "cochin", "plantin", "sabon", "caslon", "bookman")


# Letras con un asta recta que llega a la línea base: con serif, el pie es mucho más ancho
_LETRAS_ASTA = "HIMNTFPhilmnrfk"
# Relación pie/asta: las sans miden cerca de 1,0 (hasta 1,15) y las serif, de 1,6 en adelante
_UMBRAL_SERIF = 1.4
_MINIMO_LETRAS_MONO = 6


def _contorno_de(glifo, glifos) -> list[tuple]:
    """Segmentos rectos del contorno de un glifo (las curvas se aproximan)."""
    from fontTools.pens.basePen import BasePen

    class Pluma(BasePen):
        def __init__(self):
            super().__init__(glifos)
            self.segmentos: list[tuple] = []
            self.actual = self.inicio = (0, 0)

        def _moveTo(self, p):
            self.actual = self.inicio = p

        def _lineTo(self, p):
            self.segmentos.append((self.actual, p))
            self.actual = p

        def _curveToOne(self, a, b, c):
            p0, previo = self.actual, self.actual
            for i in range(1, 9):
                t, u = i / 8, 1 - i / 8
                q = tuple(u ** 3 * p0[k] + 3 * u * u * t * a[k] + 3 * u * t * t * b[k] + t ** 3 * c[k] for k in (0, 1))
                self.segmentos.append((previo, q))
                previo = q
            self.actual = c

        def _closePath(self):
            if self.actual != self.inicio:
                self.segmentos.append((self.actual, self.inicio))

        _endPath = _closePath

    pluma = Pluma()
    glifo.draw(pluma)
    return pluma.segmentos


def _ancho_del_primer_trazo(segmentos: list[tuple], y: float) -> float | None:
    cruces = sorted(a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) for a, b in segmentos
                    if a[1] <= y < b[1] or b[1] <= y < a[1])
    return cruces[1] - cruces[0] if len(cruces) >= 2 else None


def _relacion_pie_asta(glifo, glifos) -> float | None:
    segmentos = _contorno_de(glifo, glifos)
    if not segmentos:
        return None
    alturas = [p[1] for s in segmentos for p in s]
    abajo, alto = min(alturas), max(alturas) - min(alturas)
    pie = _ancho_del_primer_trazo(segmentos, abajo + 0.02 * alto)
    asta = _ancho_del_primer_trazo(segmentos, abajo + 0.25 * alto)
    return pie / asta if pie and asta else None


def _clase_por_contornos(datos: bytes, unicode_a_codigo: dict[str, int] | None = None,
                         codigo_a_gid=None) -> str | None:
    """Mira la forma real de los glifos: sirve cuando la fuente no declara nada (PANOSE en 0,
    como Quicksand). Con los mapas del PDF llega también a subconjuntos sin cmap.
    None si no hay letras que medir."""
    from fontTools.ttLib import TTFont
    fuente = TTFont(io.BytesIO(datos), lazy=True)
    orden = fuente.getGlyphOrder()
    cmap = (fuente.getBestCmap() or {}) if "cmap" in fuente else {}
    glifos = fuente.getGlyphSet()
    avances: dict[str, float] = {}
    relaciones: list[float] = []
    for ch in LETRAS_BASICAS:
        # Algunas fuentes simbólicas usan solo el rango U+F0xx
        nombre = cmap.get(ord(ch)) or cmap.get(0xF000 + ord(ch))
        if nombre is None and unicode_a_codigo and codigo_a_gid is not None and ch in unicode_a_codigo:
            gid = codigo_a_gid(unicode_a_codigo[ch])
            nombre = orden[gid] if gid is not None and 0 < gid < len(orden) else None
        if nombre is None or nombre not in glifos:
            continue
        avances[ch] = glifos[nombre].width
        if ch in _LETRAS_ASTA:
            relacion = _relacion_pie_asta(glifos[nombre], glifos)
            if relacion is not None:
                relaciones.append(relacion)
    if len(avances) >= _MINIMO_LETRAS_MONO and len(set(avances.values())) == 1:
        return Clase.MONO
    if not relaciones:
        return None
    return Clase.SERIF if statistics.median(relaciones) >= _UMBRAL_SERIF else Clase.SANS


def clase_tipografica(nombre: str, flags: int = 0, datos: bytes | None = None, mapas: tuple | None = None) -> str:
    """sans / serif / mono, en este orden de confianza: nombre de la familia, lo que declara
    el programa de fuente (ancho fijo, PANOSE), la forma de sus glifos y, por último, los
    flags. Los flags valen poco: los generadores los llenan mal y MuPDF marca como serif a
    toda fuente que no se declare sans. `mapas`: (unicode -> código, código -> gid) del PDF."""
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
        try:
            medida = _clase_por_contornos(datos, *(mapas or ()))
            if medida is not None:
                return medida
        except Exception:
            pass      # programa de fuente que fontTools no puede leer (Type1, CFF pelado...)
    if flags & 8:
        return Clase.MONO
    if flags & 4:
        return Clase.SERIF
    return Clase.SANS


def clase_de_fuente_pdf(doc: pymupdf.Document, xref: int, nombre: str, flags: int = 0) -> str:
    """Clase de una fuente del PDF, mirando también su programa embebido si lo tiene."""
    datos, mapas = None, None
    try:
        datos = doc.extract_font(xref)[3] or None
        tipo = doc.xref_get_key(xref, "Subtype")[1].lstrip("/")
        mapas = _mapas_del_pdf(doc, xref, tipo)
    except Exception:
        pass
    return clase_tipografica(nombre, flags, datos, mapas)


def es_no_dibujable(c: str) -> bool:
    """Caracteres que ninguna fuente debería dibujar: U+FFFD (el PDF no dice qué carácter es),
    controles, sustitutos y códigos sin asignar en Unicode. El uso privado (U+E000...) no entra:
    las fuentes de símbolos lo usan legítimamente."""
    if c in "\n\r\t":
        return False
    return c == "\ufffd" or unicodedata.category(c) in ("Cc", "Cs", "Cn")


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
    clasificadas: dict[int, tuple[Embebido, str]] = {}
    for pno in range(doc.page_count):
        try:
            fuentes_pagina = doc.get_page_fonts(pno)
        except Exception:
            continue          # página dañada: el resto se analiza igual
        for xref, ext, tipo, basefont, _ref, _enc in fuentes_pagina:
            nombre_pdf = basefont or f"(sin nombre, xref {xref})"
            if xref not in clasificadas:
                # Cada fuente se analiza por separado: una rota nunca frena a las demás
                try:
                    clasificadas[xref] = (clasificar_fuente(doc, xref, nombre_pdf, tipo, ext), "")
                except Exception as e:
                    clasificadas[xref] = (Embebido.NO_ANALIZADA, type(e).__name__)
            emb, error = clasificadas[xref]
            clave = (sin_prefijo(nombre_pdf), emb)
            info = grupos.get(clave)
            if info is None:
                info = InfoFuente(sin_prefijo(nombre_pdf), nombre_pdf, tipo, ext, emb, error=error)
                grupos[clave] = info
            if xref not in info.xrefs:
                info.xrefs.append(xref)
            if pno not in info.paginas:
                info.paginas.append(pno)
    fuentes = sorted(grupos.values(), key=lambda f: f.nombre.lower())
    for f in fuentes:
        if f.embebido == Embebido.TYPE3:
            continue
        try:
            if indice is not None:
                f.instalada = indice.buscar(f.nombre)
            if incluidas is not None:
                f.incluida = incluidas.buscar(f.nombre)
        except Exception as e:
            f.error = f.error or type(e).__name__
    return fuentes
