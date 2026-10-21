"""Reconstrucción de la estructura de un escaneo a partir de las palabras del OCR.

Recibe, por página, las palabras reconocidas (con caja y confianza), las grillas de las
tablas, las imágenes recortadas (logos, firmas, sellos) y las viñetas dibujadas, todo en
puntos. Devuelve una `Reconstruccion`: títulos, párrafos (con los renglones unidos),
listas, tablas, imágenes, encabezado y pie, márgenes y espaciados.

Son funciones puras (sin imágenes ni OCR) para poder probarlas con datos armados a mano.
"""
from __future__ import annotations

import difflib
import math
import re
import statistics
from dataclasses import dataclass, field

from pdf_studio.core.reconstruccion import (UMBRAL_DUDOSA, Alineacion, Bloque, Caja, Celda, Linea,
                                            PaginaReconstruida, Palabra, Reconstruccion, Tipo, union)

# Proporciones de una letra respecto del tamaño de la fuente (Arial/Helvetica; se
# recalculan con la fuente elegida). La caja de una palabra va de lo más alto a lo más bajo
# de su tinta: depende de si tiene letras altas (b, d, mayúsculas), con «patas» (g, p) o ninguna.
# Opcionales: «medidor» (texto → alto y profundidad exactos con la fuente elegida) y
# «exceso», cuántos pt engorda la tinta el escaneo (ver conversion.estimar_exceso).
FACTORES = {"alta": 0.72, "pata": 0.21, "x": 0.52, "acento": 0.90}
_ALTAS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZÑÇbdfhiklt0123456789áéíóúàèìòùäëïöüñ¿?¡!/\\()[]{}%&#@$|ß'\"")
_PATAS = set("gjpqyQ,;()[]{}|ç")
_MAYUS_ACENTO = set("ÁÉÍÓÚÀÈÌÒÙÄËÏÖÜÑ")

VINETAS = set("•·●▪■◦○◆◇►▶>»«✓✔➢➤-–—*°¢©®")
VINETAS_LETRA = set("oeO")             # el OCR suele leer así una viñeta redonda
NUMERO = re.compile(r"^(\()?(\d{1,2}|[a-zA-Z]|[ivxIVX]{1,4})([.)])$")
NUMERO_PEGADO = re.compile(r"^(\d{1,2})([.)])(\S+)$")
PAGINA_X_DE_Y = re.compile(r"(p[áa]g(?:ina)?\.?\s*)(\d+)(?:(\s*(?:de|/)\s*)(\d+))?", re.IGNORECASE)
CLAVES_ENCABEZADO = ("código", "codigo", "versión", "version", "revisión", "revision", "página", "pagina",
                     "procedimiento", "sop", "vigencia", "edición", "edicion", "documento")

ESCALA_TITULO = 1.15          # un renglón al menos así de grande que el cuerpo es un título
UMBRAL_NEGRITA = 1.3          # trazo al menos así de grueso que la mediana = negrita


# ---------------------------------------------------------------------- entrada
@dataclass
class Grilla:
    """Una tabla detectada por sus líneas: posiciones de las líneas y celdas (combinadas)."""
    filas: list[float]                       # y de las líneas horizontales, de arriba a abajo
    columnas: list[float]                    # x de las líneas verticales
    celdas: list[tuple[int, int, int, int]]  # (fila, columna, filas que ocupa, columnas que ocupa)
    sin_borde: list[int] = field(default_factory=list)   # líneas verticales que no se ven (agregadas)

    @property
    def caja(self) -> Caja:
        return (self.columnas[0], self.filas[0], self.columnas[-1], self.filas[-1])

    def caja_celda(self, celda: tuple[int, int, int, int]) -> Caja:
        f, c, nf, nc = celda
        return (self.columnas[c], self.filas[f], self.columnas[c + nc], self.filas[f + nf])


@dataclass
class RegionImagen:
    caja: Caja
    archivo: str
    clase: str = "imagen"


@dataclass
class PalabraOCR:
    palabra: Palabra
    linea: tuple                              # clave del renglón según el OCR (bloque, párrafo, línea)


@dataclass
class AnalisisPagina:
    numero: int
    ancho: float
    alto: float
    imagen: str = ""
    palabras: list[PalabraOCR] = field(default_factory=list)
    grillas: list[Grilla] = field(default_factory=list)
    imagenes: list[RegionImagen] = field(default_factory=list)
    vinetas: list[Caja] = field(default_factory=list)


# ---------------------------------------------------------------------- tamaño y línea de base
def _clase_palabra(texto: str) -> str | None:
    letras = [c for c in texto if not c.isspace()]
    if not letras or all(c in ".,:;-–—_'\"*·•" for c in letras):
        return None
    alta = any(c in _ALTAS for c in letras)
    acento = any(c in _MAYUS_ACENTO for c in letras)
    pata = any(c in _PATAS for c in letras)
    return ("acento" if acento else "alta" if alta else "x") + ("+pata" if pata else "")


def tamano_palabra(p: Palabra, factores: dict = FACTORES) -> float | None:
    """Tamaño de letra (pt) que explica la altura de la caja de la palabra."""
    clase = _clase_palabra(p.texto)
    if clase is None:
        return None
    medidor = factores.get("medidor")
    medida = medidor(p.texto) if medidor is not None else None
    if medida is not None and sum(medida) >= 0.3:
        factor = sum(medida)
    else:
        base, _, pata = clase.partition("+")
        factor = factores[base] + (factores["pata"] if pata else 0.0)
    alto = p.caja[3] - p.caja[1] - factores.get("exceso", 0.0)
    return alto / factor if factor > 0 and alto > 0 else None


def tamano_linea(palabras: list[Palabra], factores: dict = FACTORES) -> float:
    """Mediana de los tamaños de sus palabras, preferentemente de las que tienen letras altas
    (la altura de la «x» varía mucho de una fuente a otra)."""
    altas, otras = [], []
    for p in palabras:
        t = tamano_palabra(p, factores)
        if t is None:
            continue
        (altas if _clase_palabra(p.texto).split("+")[0] != "x" else otras).append(t)
    valores = altas or otras
    if not valores:
        alto = statistics.median(p.caja[3] - p.caja[1] for p in palabras) if palabras else 10.0
        return redondear_tamano(alto / 0.72)
    return redondear_tamano(statistics.median(valores))


def redondear_tamano(t: float) -> float:
    return max(4.0, round(t * 2) / 2)


def linea_de_base(palabras: list[Palabra], tamano: float, factores: dict = FACTORES) -> float:
    bases = []
    for p in palabras:
        clase = _clase_palabra(p.texto)
        if clase is None:
            continue
        medidor = factores.get("medidor")
        medida = medidor(p.texto) if medidor is not None else None
        pata = medida[1] if medida is not None else (factores["pata"] if clase.endswith("+pata") else 0.0)
        bases.append(p.caja[3] - pata * tamano - factores.get("exceso", 0.0) / 2)
    return statistics.median(bases) if bases else max(p.caja[3] for p in palabras)


# ---------------------------------------------------------------------- negritas
def marcar_negritas(lineas: list[Linea], trazo_fuente: tuple[float, float] | None = None) -> None:
    """Negrita = trazo más grueso que el que corresponde al tamaño de letra del renglón.

    Con `trazo_fuente` = (grosor de la regular, grosor de la negrita) de la fuente elegida,
    en fracciones del tamaño, se descuenta primero lo que engorda el escaneo (el desenfoque
    suma lo mismo a todas las letras): trazo medido ≈ grosor × tamaño + exceso. Sin eso, un
    título grande en regular parecería negrita y una negrita chica, regular.

    Se decide por renglón (en una palabra sola se mide mal) y los textos que se repiten
    («Firma:» en cada fila de una tabla) comparten la decisión."""
    medibles = [(p, l) for l in lineas for p in l.palabras if p.trazo > 0 and l.tamano > 0]
    if len(medibles) < 3:
        return
    if trazo_fuente is not None and trazo_fuente[0] > 0:
        regular, negrita = trazo_fuente
        restos = sorted(p.trazo - regular * l.tamano for p, l in medibles if len(p.texto) >= 3)
        if not restos:
            return
        # Con la mayoría del texto en regular, el percentil 30 es el exceso de una regular
        exceso = max(0.0, restos[int(0.3 * (len(restos) - 1))])
        relacion = {id(p): (p.trazo - exceso) / (regular * l.tamano) for p, l in medibles}
        umbral_fijo = math.sqrt(max(negrita, regular * 1.2) / regular)
    else:
        crudos = {id(p): p.trazo / l.tamano for p, l in medibles}
        validas = [crudos[id(p)] for p, _ in medibles if len(p.texto) >= 3]
        mediana = statistics.median(validas) if len(validas) >= 3 else 0.0
        if mediana <= 0:
            return
        relacion = {k: v / mediana for k, v in crudos.items()}
        umbral_fijo = UMBRAL_NEGRITA

    def valor_linea(l: Linea) -> float | None:
        vals = [(relacion[id(p)], len(p.texto)) for p in l.palabras if id(p) in relacion]
        largas = [v for v, n in vals if n >= 3] or [v for v, _ in vals]
        return statistics.median(largas) if largas else None

    valores: dict[int, float] = {}
    por_texto: dict[str, list[float]] = {}
    for l in lineas:
        v = valor_linea(l)
        if v is not None:
            valores[id(l)] = v
            por_texto.setdefault(l.texto.strip().lower(), []).append(v)
    for l in lineas:
        repetidos = por_texto.get(l.texto.strip().lower(), [])
        if id(l) in valores and len(repetidos) >= 2:
            valores[id(l)] = statistics.median(repetidos)
    umbral = _umbral_negrita([(valores[id(l)], sum(len(p.texto) for p in l.palabras)) for l in lineas
                              if id(l) in valores], umbral_fijo)
    for l in lineas:
        v = valores.get(id(l))
        en_negrita = v is not None and v >= umbral
        for p in l.palabras:
            propia = relacion.get(id(p), 0.0)
            # Dentro de un renglón regular, una palabra larga claramente más gruesa es negrita
            p.negrita = en_negrita or (len(p.texto) >= 4 and propia >= umbral + 0.25)


def _umbral_negrita(valores: list[tuple[float, int]], umbral_fijo: float) -> float:
    """El corte que mejor separa los renglones en dos grupos (regular y negrita), pesados por
    letras; si no hay dos grupos claros, el fijo."""
    if len(valores) < 4:
        return umbral_fijo
    valores = sorted(valores)
    total = sum(w for _, w in valores) or 1
    media = sum(v * w for v, w in valores) / total
    mejor, corte = 0.0, umbral_fijo
    acumulado, suma = 0, 0.0
    for i in range(len(valores) - 1):
        acumulado += valores[i][1]
        suma += valores[i][0] * valores[i][1]
        if acumulado == 0 or acumulado == total:
            continue
        m0 = suma / acumulado
        m1 = (media * total - suma) / (total - acumulado)
        entre = acumulado * (total - acumulado) * (m1 - m0) ** 2
        candidato = (valores[i][0] + valores[i + 1][0]) / 2
        # Solo cortes razonables: la negrita es al menos un 10 % más gruesa que la regular
        if entre > mejor and m1 >= 1.1 * m0 and abs(candidato - umbral_fijo) <= 0.12:
            mejor, corte = entre, candidato
    return corte


# ---------------------------------------------------------------------- renglones
def armar_lineas(palabras: list[PalabraOCR], factores: dict = FACTORES) -> list[Linea]:
    """Agrupa las palabras en renglones según el OCR y los corta donde hay un hueco muy grande
    (dos columnas en el mismo renglón, por ejemplo «Documento controlado      Página 1»)."""
    grupos: dict[tuple, list[Palabra]] = {}
    for po in palabras:
        grupos.setdefault(po.linea, []).append(po.palabra)
    lineas = []
    for grupo in grupos.values():
        grupo.sort(key=lambda p: p.caja[0])
        tamano = tamano_linea(grupo, factores)
        actual = [grupo[0]]
        for p in grupo[1:]:
            if p.caja[0] - actual[-1].caja[2] > 2.2 * tamano:
                lineas.append(actual)
                actual = [p]
            else:
                actual.append(p)
        lineas.append(actual)
    resultado = []
    for grupo in lineas:
        tamano = tamano_linea(grupo, factores)
        resultado.append(Linea(grupo, tamano, linea_de_base(grupo, tamano, factores)))
    # Renglones que el OCR partió en dos pedazos a la misma altura y pegados: se juntan
    resultado.sort(key=lambda l: (round(l.base), l.caja[0]))
    unidas: list[Linea] = []
    for l in resultado:
        previa = unidas[-1] if unidas else None
        if (previa is not None and abs(previa.base - l.base) <= 0.3 * l.tamano
                and abs(previa.tamano - l.tamano) <= 0.2 * l.tamano
                and 0 <= l.caja[0] - previa.caja[2] <= 1.2 * l.tamano):
            previa.palabras.extend(l.palabras)
            previa.tamano = tamano_linea(previa.palabras, factores)
        else:
            unidas.append(l)
    return ordenar_lineas(unidas)


def ordenar_lineas(lineas: list[Linea]) -> list[Linea]:
    """De arriba a abajo; los renglones a la misma altura, de izquierda a derecha."""
    lineas = sorted(lineas, key=lambda l: l.base)
    resultado, fila = [], []
    for l in lineas:
        if fila and l.base - fila[0].base > 0.4 * l.tamano:
            resultado += sorted(fila, key=lambda x: x.caja[0])
            fila = []
        fila.append(l)
    return resultado + sorted(fila, key=lambda x: x.caja[0])


# ---------------------------------------------------------------------- listas
def _valor_numero(texto: str) -> int:
    if texto.isdigit():
        return int(texto)
    romanos = {"i": 1, "v": 5, "x": 10, "l": 50}
    t = texto.lower()
    if len(t) > 1 and all(c in romanos for c in t):
        total, previo = 0, 0
        for c in reversed(t):
            v = romanos.get(c, 0)
            total = total - v if v < previo else total + v
            previo = max(previo, v)
        return max(1, total)
    return ord(t) - ord("a") + 1


def detectar_marcador(linea: Linea, vinetas: list[Caja]) -> tuple[str, int, float] | None:
    """(marcador, número, x del marcador) si el renglón empieza un ítem de lista. Si el
    marcador es una palabra del OCR (una viñeta leída como «•» u «o»), se quita del renglón."""
    if not linea.palabras:
        return None
    t = linea.tamano
    x0, y0, _, y1 = linea.caja
    # Viñeta dibujada (un punto o un cuadradito) justo a la izquierda del renglón
    for v in vinetas:
        cy = (v[1] + v[3]) / 2
        if 0 < x0 - v[2] <= 2.5 * t and y0 - 0.1 * t <= cy <= y1 + 0.1 * t:
            return "•", 1, v[0]
    primera = linea.palabras[0]
    texto = primera.texto
    if len(linea.palabras) >= 2:
        segunda = linea.palabras[1]
        hueco = segunda.caja[0] - primera.caja[2]
        ancho, alto = primera.caja[2] - primera.caja[0], primera.caja[3] - primera.caja[1]
        if len(texto) == 1 and texto in VINETAS | VINETAS_LETRA and ancho <= 0.75 * t and alto <= 0.75 * t:
            if texto in VINETAS or hueco >= 0.6 * t:
                linea.palabras.pop(0)
                return "•", 1, primera.caja[0]
        m = NUMERO.match(texto)
        if m and hueco >= 0.15 * t:
            marcador = texto
            return marcador, _valor_numero(m.group(2)), primera.caja[0]
    m = NUMERO_PEGADO.match(texto)
    if m and len(linea.palabras) >= 1 and m.group(3)[:1].isalpha():
        return m.group(1) + m.group(2), int(m.group(1)), primera.caja[0]
    return None


# ---------------------------------------------------------------------- párrafos
@dataclass
class Columna:
    izq: float
    der: float
    centro_hoja: float | None = None    # con texto en bandera, el centro de la columna queda corrido

    @property
    def ancho(self) -> float:
        return max(1.0, self.der - self.izq)

    @property
    def centro(self) -> float:
        return (self.izq + self.der) / 2


def _ancho_palabra(p: Palabra) -> float:
    return p.caja[2] - p.caja[0]


def es_tipo_titulo(lineas: list[Linea], cuerpo: float) -> bool:
    palabras = sum(len(l.palabras) for l in lineas)
    tamano = max(l.tamano for l in lineas)
    return tamano >= ESCALA_TITULO * cuerpo or (all(l.negrita for l in lineas) and palabras <= 14)


def _centrada(linea: Linea, col: Columna) -> bool:
    x0, _, x1, _ = linea.caja
    centros = [col.centro] + ([col.centro_hoja] if col.centro_hoja is not None else [])
    return (any(abs((x0 + x1) / 2 - c) <= 0.03 * col.ancho + 2 for c in centros)
            and x0 - col.izq > 1.5 * linea.tamano and col.der - x1 > 1.5 * linea.tamano)


def _continua(bloque: Bloque, linea: Linea, col: Columna, cuerpo: float) -> bool:
    """¿El renglón sigue el párrafo (o ítem) en curso?"""
    previa = bloque.lineas[-1]
    t = max(previa.tamano, linea.tamano)
    if abs(previa.tamano - linea.tamano) > 0.2 * t:
        return False
    salto = linea.base - previa.base
    if salto <= 0.5 * t or salto > 1.8 * t:
        return False
    if len(bloque.lineas) >= 2:
        anterior = bloque.lineas[-1].base - bloque.lineas[-2].base
        if salto > anterior * 1.2 + 1:
            return False
    titulo_previo = es_tipo_titulo([previa], cuerpo)
    if titulo_previo != es_tipo_titulo([linea], cuerpo) or previa.negrita != linea.negrita:
        return False
    if _centrada(previa, col) and _centrada(linea, col):
        # Centrados: siguen el párrafo solo si el renglón se cortó porque no entraba la palabra
        # siguiente («CONTROL / DE / CALIDAD» en una celda son tres renglones a propósito)
        ancho = previa.caja[2] - previa.caja[0] + 0.3 * t + _ancho_palabra(linea.palabras[0])
        return ancho > col.ancho - 0.8 * t
    # Sangría: los renglones siguientes arrancan donde arranca el texto del párrafo
    if bloque.tipo == Tipo.ITEM:
        inicio = bloque.lineas[0].caja[0]
        if not (abs(linea.caja[0] - inicio) <= 1.0 * t or abs(linea.caja[0] - bloque.x_marcador) <= 0.6 * t):
            return False
    elif len(bloque.lineas) == 1:
        # Admite sangría de primera línea (el primero más adentro) o francesa (más afuera)
        if not -3.5 * t <= previa.caja[0] - linea.caja[0] <= 4.0 * t:
            return False
    elif abs(linea.caja[0] - bloque.lineas[1].caja[0]) > 1.0 * t:
        return False
    # El renglón anterior se cortó porque no entraba la primera palabra de este
    entraria = previa.caja[2] + 0.3 * t + _ancho_palabra(linea.palabras[0])
    return entraria > col.der - 0.4 * t


def armar_bloques(lineas: list[Linea], vinetas: list[Caja], col: Columna, cuerpo: float) -> list[Bloque]:
    bloques: list[Bloque] = []
    actual: Bloque | None = None
    for linea in lineas:
        marcador = None
        if not es_tipo_titulo([linea], cuerpo):
            marcador = detectar_marcador(linea, vinetas)
            if not linea.palabras:
                continue
        if marcador is not None:
            texto, numero, x = marcador
            vineta = texto == "•"
            actual = Bloque(Tipo.ITEM, [linea], numerado=not vineta, marcador=texto, numero=numero, x_marcador=x)
            if not vineta:
                # El número queda en el marcador, no en el texto
                primera = linea.palabras[0]
                m = NUMERO_PEGADO.match(primera.texto)
                if m and primera.texto != texto:
                    primera.texto = m.group(3)
                elif primera.texto == texto and len(linea.palabras) > 1:
                    actual.x_marcador = primera.caja[0]
                    linea.palabras.pop(0)
            bloques.append(actual)
        elif actual is not None and _continua(actual, linea, col, cuerpo):
            actual.lineas.append(linea)
        else:
            actual = Bloque(Tipo.PARRAFO, [linea])
            bloques.append(actual)
    return bloques


# ---------------------------------------------------------------------- texto de un bloque
def tramos(bloque: Bloque) -> list[tuple[str, bool, str, list[Palabra]]]:
    """El texto del bloque como tramos (texto, negrita, color, palabras), con los renglones
    unidos: los cortes con guion se pegan («desin-» + «fección»)."""
    salida: list[list] = []
    palabras = [p for l in bloque.lineas for p in l.palabras]
    fines = set()
    i = 0
    for l in bloque.lineas:
        i += len(l.palabras)
        fines.add(i - 1)
    for n, p in enumerate(palabras):
        texto = p.texto
        separador = " " if n > 0 else ""
        if n - 1 in fines and salida:
            previo = palabras[n - 1].texto
            if (len(previo) > 2 and previo.endswith("-") and previo[-2].isalpha()
                    and texto[:1].islower()):
                ultimo = salida[-1]
                ultimo[0] = ultimo[0][:-1]          # sin el guion del corte
                separador = ""
        if salida and salida[-1][1] == p.negrita and salida[-1][2] == p.color:
            salida[-1][0] += separador + texto
            salida[-1][3].append(p)
        else:
            if salida and separador:
                salida[-1][0] += separador
            salida.append([texto, p.negrita, p.color, [p]])
    return [tuple(t) for t in salida]


def texto_de(bloque: Bloque) -> str:
    if bloque.tipo == Tipo.TABLA:
        return "\n".join(texto_de(b) for c in bloque.celdas for b in c.bloques)
    return "".join(t[0] for t in tramos(bloque))


# ---------------------------------------------------------------------- tablas
def partir_columnas(grilla: Grilla, palabras: list[PalabraOCR], factores: dict) -> Grilla:
    """Separa en columnas propias lo que en el escaneo está en la misma celda pero lejos, como
    una etiqueta y su valor («POE N°:     POE-XX-00-01»): cada valor queda en su celda, en el
    mismo renglón que su etiqueta, sin espacios ni tabulaciones para ubicarlo. La línea nueva
    no se dibuja (no estaba en el original).

    El corte tiene que dejar texto a los dos lados en algún renglón y no atravesar ningún texto
    de esa columna en ninguna fila."""
    filas, columnas = list(grilla.filas), list(grilla.columnas)
    celdas, sin_borde = list(grilla.celdas), list(grilla.sin_borde)
    j = 0
    while j < len(columnas) - 1:
        propias = [c for c in celdas if c[1] == j and c[3] == 1]
        fragmentos = []          # por celda, los renglones armados (cortados en los huecos grandes)
        for c in propias:
            caja = (columnas[c[1]], filas[c[0]], columnas[c[1] + 1], filas[c[0] + c[2]])
            dentro = [po for po in palabras if _centro_en(po.palabra.caja, caja)]
            fragmentos.append(armar_lineas(dentro, factores) if dentro else [])
        corte = _corte_vertical(fragmentos)
        if corte is None:
            j += 1
            continue
        columnas.insert(j + 1, corte)
        nuevas = []
        for f, k, nf, nc in celdas:
            if k == j and nc == 1:
                nuevas += [(f, j, nf, 1), (f, j + 1, nf, 1)]
            elif k > j:
                nuevas.append((f, k + 1, nf, nc))
            elif k + nc > j:
                nuevas.append((f, k, nf, nc + 1))
            else:
                nuevas.append((f, k, nf, nc))
        celdas = nuevas
        sin_borde = [v + 1 if v > j else v for v in sin_borde] + [j + 1]
        # La columna de la izquierda puede tener otro corte: se vuelve a mirar
    return Grilla(filas, columnas, sorted(celdas), sorted(sin_borde))


def _corte_vertical(por_celda: list[list[Linea]]) -> float | None:
    izquierdas, derechas = [], []        # fin del texto de la izquierda y comienzo del de la derecha
    for lineas in por_celda:
        for a in lineas:
            for b in lineas:
                if (b.caja[0] > a.caja[2] and min(a.caja[3], b.caja[3]) - max(a.caja[1], b.caja[1])
                        > 0.3 * min(a.caja[3] - a.caja[1], b.caja[3] - b.caja[1])):
                    izquierdas.append(a.caja[2])
                    derechas.append(b.caja[0])
    if not izquierdas:
        return None
    # El corte va justo antes del valor que empieza más a la izquierda
    tamano = statistics.median(l.tamano for lineas in por_celda for l in lineas)
    fin, inicio = max(izquierdas), min(derechas)
    if inicio - fin < 1.5 * tamano:
        return None
    corte = max(fin + 0.5 * tamano, inicio - 0.4 * tamano)
    for lineas in por_celda:
        for l in lineas:
            if l.caja[0] < corte < l.caja[2]:
                return None
    return corte


def armar_tabla(grilla: Grilla, palabras: list[PalabraOCR], imagenes: list[RegionImagen], vinetas: list[Caja],
                cuerpo: float, factores: dict) -> Bloque:
    grilla = partir_columnas(grilla, palabras, factores)
    por_celda = []
    for celda in grilla.celdas:
        caja = grilla.caja_celda(celda)
        propias = [po for po in palabras if _centro_en(po.palabra.caja, caja)]
        por_celda.append((celda, caja, armar_lineas(propias, factores) if propias else []))
    # El margen interno de las celdas es el de la tabla: lo que arranca ahí no tiene sangría
    huecos = sorted(l.caja[0] - caja[0] for _, caja, lineas in por_celda for l in lineas)
    relleno = max(1.0, min(12.0, _percentil(huecos, 0.2))) if huecos else 2.0
    celdas = []
    for celda, caja, lineas in por_celda:
        col = Columna(caja[0] + relleno, caja[2] - relleno)
        bloques = armar_bloques(lineas, vinetas, col, cuerpo) if lineas else []
        for b in bloques:
            alinear(b, col)
        for img in imagenes:
            if _centro_en(img.caja, caja):
                bloques.append(bloque_imagen(img, col))
        bloques.sort(key=lambda b: b.caja[1])
        celdas.append(Celda(celda[0], celda[1], celda[2], celda[3], bloques))
    return Bloque(Tipo.TABLA, caja_fija=grilla.caja, filas=list(grilla.filas), columnas=list(grilla.columnas),
                  celdas=celdas, columnas_sin_borde=list(grilla.sin_borde))


def _centro_en(caja: Caja, contenedor: Caja) -> bool:
    cx, cy = (caja[0] + caja[2]) / 2, (caja[1] + caja[3]) / 2
    return contenedor[0] <= cx <= contenedor[2] and contenedor[1] <= cy <= contenedor[3]


def bloque_imagen(img: RegionImagen, col: Columna) -> Bloque:
    b = Bloque(Tipo.IMAGEN, caja_fija=img.caja, archivo=img.archivo, clase=img.clase)
    x0, _, x1, _ = img.caja
    if abs((x0 + x1) / 2 - col.centro) <= 0.06 * col.ancho:
        b.alineacion = Alineacion.CENTRO
    elif col.der - x1 < x0 - col.izq and col.der - x1 <= 0.15 * col.ancho:
        b.alineacion = Alineacion.DERECHA
    else:
        b.sangria = max(0.0, x0 - col.izq)
    return b


# ---------------------------------------------------------------------- alineación y sangrías
def alinear(bloque: Bloque, col: Columna) -> None:
    if bloque.tipo not in (Tipo.PARRAFO, Tipo.TITULO, Tipo.ITEM) or not bloque.lineas:
        return
    t = bloque.tamano or 10.0
    lineas = bloque.lineas
    if all(_centrada(l, col) for l in lineas):
        bloque.alineacion = Alineacion.CENTRO
        return
    x0 = [l.caja[0] for l in lineas]
    x1 = [l.caja[2] for l in lineas]
    if (bloque.tipo != Tipo.ITEM and min(x0) - col.izq > 0.3 * col.ancho
            and all(col.der - v <= 1.0 * t for v in x1)):
        bloque.alineacion = Alineacion.DERECHA
        return
    if len(lineas) >= 2 and all(col.der - v <= 0.35 * t for v in x1[:-1]) and _estirados(lineas):
        bloque.alineacion = Alineacion.JUSTIFICADO
    resto = statistics.median(x0[1:]) if len(x0) > 1 else x0[0]
    if bloque.tipo == Tipo.ITEM:
        bloque.sangria = max(0.0, x0[0] - col.izq)
        bloque.sangria_primera = min(0.0, bloque.x_marcador - x0[0])
    else:
        bloque.sangria = max(0.0, resto - col.izq) if resto - col.izq > 0.4 * t else 0.0
        primera = x0[0] - (col.izq + bloque.sangria)
        bloque.sangria_primera = primera if abs(primera) > 0.4 * t else 0.0


def _hueco_medio(linea: Linea) -> float | None:
    p = linea.palabras
    if len(p) < 3:
        return None
    return statistics.fmean(p[i + 1].caja[0] - p[i].caja[2] for i in range(len(p) - 1)) / max(1.0, linea.tamano)


def _estirados(lineas: list[Linea]) -> bool:
    """Justificado: los espacios entre palabras de los renglones llenos quedan más anchos que
    los del último renglón (que no se estira)."""
    llenos = [h for h in (_hueco_medio(l) for l in lineas[:-1]) if h is not None]
    ultimo = _hueco_medio(lineas[-1])
    if not llenos:
        return False
    if ultimo is not None:
        return statistics.fmean(llenos) > 1.15 * ultimo
    return statistics.fmean(llenos) > 0.42           # bastante más que un espacio normal


# ---------------------------------------------------------------------- encabezado y pie
def _firma_bloque(b: Bloque) -> str:
    texto = texto_de(b) if b.tipo != Tipo.IMAGEN else f"[imagen {b.clase}]"
    if b.tipo == Tipo.TABLA:
        texto = f"[tabla {len(b.filas)}x{len(b.columnas)}] " + texto
    return re.sub(r"\d+", "#", texto.lower()).strip()


def _parecidos(a: Bloque, b: Bloque, pagina: PaginaReconstruida) -> bool:
    if a.tipo != b.tipo:
        return False
    ca, cb = a.caja, b.caja
    if abs(ca[1] - cb[1]) > 0.025 * pagina.alto or abs(ca[0] - cb[0]) > 0.05 * pagina.ancho:
        return False
    if a.tipo == Tipo.IMAGEN:
        return abs((ca[2] - ca[0]) - (cb[2] - cb[0])) <= 0.2 * max(1.0, ca[2] - ca[0])
    return difflib.SequenceMatcher(None, _firma_bloque(a), _firma_bloque(b)).ratio() >= 0.75


def _en_franja(b: Bloque, pagina: PaginaReconstruida, arriba: bool) -> bool:
    caja = b.caja
    return caja[3] <= 0.2 * pagina.alto if arriba else caja[1] >= 0.86 * pagina.alto


def separar_encabezado_pie(paginas: list[PaginaReconstruida]) -> None:
    """Pasa al encabezado y al pie lo que se repite arriba y abajo en las páginas (código del
    SOP, versión, «Página X de Y», logo). Con una sola página: lo que tiene pinta de serlo."""
    n = len(paginas)
    for arriba in (True, False):
        for pag in paginas:
            candidatos = [b for b in pag.bloques if _en_franja(b, pag, arriba)]
            elegidos = []
            for b in candidatos:
                if n >= 2:
                    otras = [q for q in paginas if q is not pag]
                    repetido = sum(any(_parecidos(b, c, q) for c in q.bloques + (q.encabezado if arriba else q.pie))
                                   for q in otras)
                    if repetido >= max(1, (len(otras) + 1) // 2):
                        elegidos.append(b)
                else:
                    texto = texto_de(b).lower() if b.tipo != Tipo.IMAGEN else ""
                    if PAGINA_X_DE_Y.search(texto) or (b.tipo == Tipo.TABLA and any(k in texto for k in
                                                                                   CLAVES_ENCABEZADO)):
                        elegidos.append(b)
            if not elegidos:
                continue
            # Lo que queda entre el borde de la hoja y lo elegido también es encabezado (o pie)
            if arriba:
                limite = max(b.caja[3] for b in elegidos)
                elegidos += [b for b in candidatos if b not in elegidos and b.caja[3] <= limite + 2]
            else:
                limite = min(b.caja[1] for b in elegidos)
                elegidos += [b for b in candidatos if b not in elegidos and b.caja[1] >= limite - 2]
            destino = pag.encabezado if arriba else pag.pie
            for b in sorted(elegidos, key=lambda b: (b.caja[1], b.caja[0])):
                pag.bloques.remove(b)
                destino.append(b)


# ---------------------------------------------------------------------- títulos
def clasificar_titulos(paginas: list[PaginaReconstruida], cuerpo: float) -> None:
    candidatos = [b for pag in paginas for b in pag.bloques
                  if b.tipo == Tipo.PARRAFO and len(b.lineas) <= 3 and es_tipo_titulo(b.lineas, cuerpo)]
    tamanos: list[float] = []
    for t in sorted({b.tamano for b in candidatos}, reverse=True):
        if not tamanos or tamanos[-1] - t > 0.75:
            tamanos.append(t)
    for b in candidatos:
        b.tipo = Tipo.TITULO
        b.nivel = min(3, 1 + next(i for i, t in enumerate(tamanos) if t - b.tamano <= 0.75))
    # Todos los títulos de un nivel con el mismo tamaño (la medida de cada renglón varía un poco)
    for nivel in {b.nivel for b in candidatos}:
        propios = [b for b in candidatos if b.nivel == nivel]
        tamano = redondear_tamano(statistics.median(b.tamano for b in propios))
        for b in propios:
            for l in b.lineas:
                l.tamano = tamano


# ---------------------------------------------------------------------- todo junto
def _percentil(valores: list[float], q: float) -> float:
    valores = sorted(valores)
    if not valores:
        return 0.0
    i = min(len(valores) - 1, max(0, round(q * (len(valores) - 1))))
    return valores[i]


def maquetar(analisis: list[AnalisisPagina], factores: dict = FACTORES) -> Reconstruccion:
    # 1. Renglones del flujo (lo que no está en tablas ni en imágenes) y de las tablas
    por_pagina = []
    todas_las_lineas: list[Linea] = []
    for a in analisis:
        en_tabla = [[po for po in a.palabras if _centro_en(po.palabra.caja, g.caja)] for g in a.grillas]
        usadas = {id(po) for grupo in en_tabla for po in grupo}
        libres = [po for po in a.palabras if id(po) not in usadas]
        lineas = armar_lineas(libres, factores) if libres else []
        lineas_tabla = [armar_lineas(g, factores) for g in en_tabla if g]
        todas_las_lineas += lineas + [l for grupo in lineas_tabla for l in grupo]
        por_pagina.append((a, lineas, en_tabla))

    marcar_negritas(todas_las_lineas, factores.get("trazo_fuente"))
    con_texto = [l for l in todas_las_lineas if l.palabras]
    pesos = [(l.tamano, sum(len(p.texto) for p in l.palabras)) for l in con_texto]
    cuerpo = _mediana_ponderada(pesos) if pesos else 11.0
    for l in con_texto:
        _mayuscula_ambigua(l, cuerpo, factores)
    # La medida de cada renglón varía un poco: lo que está cerca del cuerpo es del cuerpo
    for l in con_texto:
        if abs(l.tamano - cuerpo) <= 0.08 * cuerpo:
            l.tamano = cuerpo

    # Columna de texto del documento (márgenes izquierdo y derecho del cuerpo)
    flujo = [l for _, lineas, _ in por_pagina for l in lineas]
    if flujo:
        col = Columna(_percentil([l.caja[0] for l in flujo], 0.1), _percentil([l.caja[2] for l in flujo], 0.9),
                      analisis[0].ancho / 2)
    else:
        ancho = analisis[0].ancho if analisis else 595.0
        col = Columna(72.0, ancho - 72.0)

    # 2. Bloques por página
    paginas = []
    for a, lineas, en_tabla in por_pagina:
        bloques = armar_bloques(lineas, a.vinetas, col, cuerpo)
        for b in bloques:
            alinear(b, col)
        imagenes_en_tabla = set()
        for g, grupo in zip(a.grillas, en_tabla):
            propias = [img for img in a.imagenes if _centro_en(img.caja, g.caja)]
            imagenes_en_tabla.update(id(i) for i in propias)
            bloques.append(armar_tabla(g, grupo, propias, a.vinetas, cuerpo, factores))
        for img in a.imagenes:
            if id(img) not in imagenes_en_tabla:
                bloques.append(bloque_imagen(img, col))
        paginas.append(PaginaReconstruida(a.numero, a.ancho, a.alto, a.imagen, ordenar_bloques(bloques)))

    # 3. Encabezado y pie, títulos, márgenes y espacios
    separar_encabezado_pie(paginas)
    clasificar_titulos(paginas, cuerpo)
    resultado = Reconstruccion(paginas, tamano_cuerpo=cuerpo)
    calcular_margenes(resultado, col)
    calcular_espacios(resultado)
    numerar_listas(resultado)
    return resultado


_AMBIGUAS = set("cosuvwxz")          # la mayúscula tiene la misma forma que la minúscula


def _mayuscula_ambigua(linea: Linea, cuerpo: float, factores: dict) -> None:
    """«vence:» medido como minúscula da una letra enorme: el OCR confundió la «V» con la «v»
    (tienen la misma forma). Si con la mayúscula el renglón queda del tamaño del cuerpo, se
    corrige la letra y la palabra se marca para revisar."""
    if linea.tamano <= 1.2 * cuerpo or not linea.palabras:
        return
    primera = linea.palabras[0]
    if not primera.texto or primera.texto[0] not in _AMBIGUAS or _clase_palabra(primera.texto) not in ("x", "x+pata"):
        return
    corregida = Palabra(primera.id, primera.texto[0].upper() + primera.texto[1:], primera.caja, primera.conf)
    otras = linea.palabras[1:]
    tamano = tamano_linea([corregida] + otras, factores)
    if abs(tamano - cuerpo) <= 0.12 * cuerpo:
        primera.texto = corregida.texto
        primera.conf = min(primera.conf, UMBRAL_DUDOSA - 1)
        linea.tamano = tamano
        linea.base = linea_de_base(linea.palabras, tamano, factores)


def _mediana_ponderada(pesos: list[tuple[float, int]]) -> float:
    pesos = sorted(pesos)
    total = sum(w for _, w in pesos)
    acumulado = 0
    for valor, w in pesos:
        acumulado += w
        if acumulado >= total / 2:
            return valor
    return pesos[-1][0]


def ordenar_bloques(bloques: list[Bloque]) -> list[Bloque]:
    """Orden de lectura: de arriba a abajo. Una imagen al costado de un párrafo (un sello,
    por ejemplo) va después de ese párrafo, no en el medio."""
    texto = sorted([b for b in bloques if b.tipo != Tipo.IMAGEN], key=lambda b: (b.caja[1], b.caja[0]))
    imagenes = sorted([b for b in bloques if b.tipo == Tipo.IMAGEN], key=lambda b: (b.caja[1], b.caja[0]))
    resultado = list(texto)
    for img in imagenes:
        # Va antes del primer bloque que empieza más abajo que ella: si está al costado de
        # un párrafo que arrancó antes, queda después de ese párrafo
        pos = next((i for i, b in enumerate(resultado) if b.caja[1] >= img.caja[1]), len(resultado))
        resultado.insert(pos, img)
    return resultado


def calcular_margenes(rec: Reconstruccion, col: Columna) -> None:
    cuerpo = [b for pag in rec.paginas for b in pag.bloques]
    if not rec.paginas:
        return
    ancho, alto = rec.paginas[0].ancho, rec.paginas[0].alto
    izq = col.izq
    der = ancho - col.der
    if cuerpo:
        izq = min(izq, _percentil([b.caja[0] for b in cuerpo], 0.05))
        der = min(der, ancho - _percentil([b.caja[2] for b in cuerpo], 0.95))
        arriba = min(b.caja[1] for b in cuerpo) - 2
        abajo = alto - max(b.caja[3] for b in cuerpo) - 2
    else:
        arriba = abajo = 72.0
    encabezados = [b for pag in rec.paginas for b in pag.encabezado]
    pies = [b for pag in rec.paginas for b in pag.pie]
    if encabezados:
        rec.distancia_encabezado = max(10.0, min(b.caja[1] for b in encabezados))
        arriba = max(arriba, max(b.caja[3] for b in encabezados) + 4)
    if pies:
        rec.distancia_pie = max(10.0, alto - max(b.caja[3] for b in pies))
        # Las páginas casi nunca están llenas: el margen inferior lo marca el pie
        abajo = alto - min(b.caja[1] for b in pies) + 6
    else:
        abajo = max(36.0, min(abajo, 1.2 * izq))
    minimo = 14.0
    rec.margenes = (max(minimo, izq), max(minimo, arriba), max(minimo, der), max(minimo, abajo))


def _pasos(b: Bloque) -> list[float]:
    return [b.lineas[i + 1].base - b.lineas[i].base for i in range(len(b.lineas) - 1)]


def calcular_espacios(rec: Reconstruccion) -> None:
    """Interlineado de cada párrafo, espacio antes de cada bloque y saltos de página."""
    pasos = [p for pag in rec.paginas for b in pag.bloques if b.tipo in (Tipo.PARRAFO, Tipo.ITEM)
             for p in _pasos(b)]
    relacion = statistics.median([p / max(1.0, rec.tamano_cuerpo) for p in pasos]) if pasos else 1.2
    rec.interlineado_cuerpo = round(relacion * rec.tamano_cuerpo, 1)

    def preparar(bloques: list[Bloque], arriba: float | None, primera_pagina: bool) -> None:
        previo: Bloque | None = None
        for b in bloques:
            if b.lineas:
                propios = _pasos(b)
                b.interlineado = round(statistics.median(propios), 1) if propios else 0.0
            paso = (b.interlineado or relacion * b.tamano) if b.lineas else 0.0
            if previo is None:
                if arriba is None:
                    hueco = 0.0
                elif b.lineas:
                    hueco = b.lineas[0].base - arriba - paso
                else:
                    hueco = b.caja[1] - arriba
                b.salto_antes = not primera_pagina
            elif b.lineas and previo.lineas:
                hueco = b.lineas[0].base - previo.lineas[-1].base - paso
            elif b.lineas:
                hueco = b.lineas[0].base - previo.caja[3] - paso * 0.8
            else:
                hueco = b.caja[1] - previo.caja[3]
            b.espacio_antes = round(min(72.0, max(0.0, hueco)))
            previo = b

    for n, pag in enumerate(rec.paginas):
        preparar(pag.bloques, rec.margenes[1], n == 0)
        preparar(pag.encabezado, None, True)
        preparar(pag.pie, None, True)
        for b in pag.todos():
            for c in b.celdas:
                preparar(c.bloques, None, True)


def numerar_listas(rec: Reconstruccion) -> None:
    """Nivel de cada ítem según su sangría dentro de la lista."""
    for pag in rec.paginas:
        for bloques in [pag.bloques, pag.encabezado, pag.pie] + [c.bloques for b in pag.todos() for c in b.celdas]:
            base = None
            for b in bloques:
                if b.tipo != Tipo.ITEM:
                    base = None
                    continue
                if base is None:
                    base = b.x_marcador
                t = b.tamano or 10.0
                b.nivel = max(0, min(2, round((b.x_marcador - base) / (1.6 * t))))
