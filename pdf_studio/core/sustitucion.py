"""Sustitución global de fuentes ("modo armónico").

Reemplaza una fuente (por ejemplo, una privada o recortada) en todo el documento
para que quede uniforme y se pueda seguir editando:

- Prioriza fuentes métricamente compatibles (mismo ancho por letra).
- Cada glifo se dibuja con la fuente sustituta EN LA POSICIÓN EXACTA del glifo
  original, y la forma de los glifos se escala horizontalmente por línea para que
  la línea ocupe exactamente el mismo ancho. Así se conservan saltos de línea,
  alineación y tablas.
- Se mantienen tamaño, color, negrita/cursiva, modo de render y posición.
- Al final se informan las líneas con un ajuste mayor a ±5%.
- La fuente sustituta se embebe una sola vez por estilo, recortada a los
  caracteres que usa el documento.
"""
from __future__ import annotations

import hashlib
import statistics
from dataclasses import dataclass, field

import pymupdf

from pdf_studio.core.documento import ErrorDocumento, Limitacion, Operacion
from pdf_studio.core.fuentes import (Embebido, FuenteSistema, IndiceFuentesSistema, clase_tipografica,
                                     clasificar_fuente, estilo_de, familia_de, separar_nombre, sin_prefijo)
from pdf_studio.core.pdfutil import independizar_recursos
from pdf_studio.core.texto import (F_ITALICA, F_MONO, F_NEGRITA, F_SERIF, Linea, LineaADibujar, Tramo,
                                   _clave_glifo, _espaciado, _franja_borrado, borrar_texto, bytes_de_fuente,
                                   codificador_de_tramo, dibujar_con_fuente_original, glifos_de_pagina,
                                   lineas_de_pagina, subconjunto)

MODO_INVISIBLE = 3        # modo de render del texto que no se ve (la capa de un OCR)

UMBRAL_AJUSTE = 0.05      # se informan las líneas con ajuste mayor a ±5%

# Familias libres incluidas con la app, por clase tipográfica
INCLUIDAS_POR_CLASE = {"sans": "Liberation Sans", "serif": "Liberation Serif", "mono": "Liberation Mono"}
OTRAS_INCLUIDAS = ["Carlito", "Caladea"]
WINDOWS_COMUNES = ["Arial", "Times New Roman", "Courier New", "Calibri", "Cambria", "Georgia", "Verdana",
                   "Segoe UI", "Tahoma", "Garamond", "Book Antiqua"]
# ---------------------------------------------------------------------- inventario
@dataclass
class FamiliaEnUso:
    clave: str                         # familia normalizada ('arial', 'timesnewroman')
    nombre: str                        # para mostrar
    nombres_pdf: set[str] = field(default_factory=set)
    embebidos: set[Embebido] = field(default_factory=set)
    paginas: set[int] = field(default_factory=set)
    fragmentos: int = 0
    flags: int = 0

    @property
    def clase(self) -> str:
        return clase_tipografica(min(self.nombres_pdf, key=len) if self.nombres_pdf else self.nombre, self.flags)

    @property
    def descripcion_embebido(self) -> str:
        return " / ".join(sorted(e.texto for e in self.embebidos)) or "—"


def familias_del_documento(doc: pymupdf.Document) -> list[FamiliaEnUso]:
    familias: dict[str, FamiliaEnUso] = {}
    tipos = {}
    for pno in range(doc.page_count):
        for xref, ext, tipo, basefont, _r, _e in doc.get_page_fonts(pno):
            if sin_prefijo(basefont) not in tipos:
                tipos[sin_prefijo(basefont)] = clasificar_fuente(doc, xref, basefont, tipo, ext)
    for pno, pag in enumerate(doc):
        for bloque in lineas_de_pagina(pag):
            for ln in bloque:
                for tr in ln.tramos:
                    if tr.modo == MODO_INVISIBLE:
                        continue        # capa de OCR: no se ve, no se reemplaza
                    clave = familia_de(tr.fuente)
                    f = familias.get(clave)
                    if f is None:
                        f = familias[clave] = FamiliaEnUso(clave, sin_prefijo(tr.fuente).split("-")[0].split(",")[0])
                    f.nombres_pdf.add(tr.fuente)
                    f.paginas.add(pno)
                    f.fragmentos += 1
                    f.flags |= tr.flags & (F_SERIF | F_MONO)
                    emb = tipos.get(sin_prefijo(tr.fuente))
                    if emb is not None:
                        f.embebidos.add(emb)
    return sorted(familias.values(), key=lambda f: -f.fragmentos)


# ---------------------------------------------------------------------- candidatas
@dataclass
class Candidata:
    familia: str
    caras: list[FuenteSistema]
    compatible: bool                  # mismo ancho por letra que la original
    origen: str                       # 'incluida' o 'sistema'
    parecido: float = 1.0             # relación de anchos medios (1.0 = igual)

    @property
    def descripcion(self) -> str:
        if self.compatible:
            detalle = "mismo ancho por letra"
        else:
            detalle = f"parecida, ancho medio {self.parecido * 100:.0f}%"
        lugar = "incluida con PDF Studio" if self.origen == "incluida" else "instalada en Windows"
        return f"{self.familia} ({detalle}; {lugar})"

    def cara(self, estilo: str) -> FuenteSistema:
        for c in self.caras:
            if separar_nombre(f"{c.familia},{c.estilo}")[1] == estilo:
                return c
        # Sin el estilo exacto: la más parecida (negrita antes que cursiva)
        for parcial in (estilo.replace("italic", ""), estilo.replace("bold", ""), ""):
            for c in self.caras:
                if separar_nombre(f"{c.familia},{c.estilo}")[1] == parcial:
                    return c
        return self.caras[0]



MUESTRA = "aeinorst"
MUESTRA_COMPATIBILIDAD = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _ancho_medio(fuente: pymupdf.Font) -> float:
    return statistics.fmean(fuente.glyph_advance(ord(c)) for c in MUESTRA)


def _ancho_medio_original(doc: pymupdf.Document, familia: FamiliaEnUso) -> tuple[float, str, dict] | None:
    """(ancho medio de algunas minúsculas, estilo, {carácter: ancho}) en la fuente original,
    según sus anchos en el PDF. Se prefiere el estilo regular, que es el que más se usa."""
    encontrados: dict[str, float] = {}
    por_letra: dict[str, dict[str, float]] = {}
    for pno in sorted(familia.paginas)[:5]:
        pag = doc[pno]
        for bloque in lineas_de_pagina(pag):
            for ln in bloque:
                for tr in ln.tramos:
                    estilo = _estilo_tramo(tr)
                    if (familia_de(tr.fuente) != familia.clave or estilo in encontrados
                            or tr.modo == MODO_INVISIBLE):
                        continue
                    cod = codificador_de_tramo(doc, pag, tr)
                    if cod is not None:
                        anchos = [cod.avance(c) / 1000 for c in MUESTRA if c in cod.a_codigo]
                        if len(anchos) == len(MUESTRA):
                            encontrados[estilo] = statistics.fmean(anchos)
                            # Para confirmar compatibilidad solo sirven letras con glifo real
                            disponibles = MUESTRA_COMPATIBILIDAD if cod.disponibles is None else \
                                "".join(c for c in MUESTRA_COMPATIBILIDAD if c in cod.disponibles)
                            por_letra[estilo] = {c: cod.avance(c) / 1000 for c in disponibles if c in cod.a_codigo}
        if "" in encontrados:
            break
    if not encontrados:
        return None
    estilo = "" if "" in encontrados else next(iter(encontrados))
    return encontrados[estilo], estilo, por_letra[estilo]


def candidatas(doc: pymupdf.Document, familia: FamiliaEnUso, incluidas: IndiceFuentesSistema | None,
               sistema: IndiceFuentesSistema | None) -> list[Candidata]:
    """Fuentes de reemplazo, la recomendada primero."""
    from pdf_studio.dependencias.catalogo_fuentes import buscar_comercial

    resultado: list[Candidata] = []
    vistos: set[str] = set()

    def agregar(nombre_familia: str, compatible: bool) -> None:
        for indice, origen in ((incluidas, "incluida"), (sistema, "sistema")):
            if indice is None:
                continue
            caras = indice.familia(nombre_familia)
            if caras and nombre_familia.lower() not in vistos:
                vistos.add(nombre_familia.lower())
                resultado.append(Candidata(nombre_familia, caras, compatible, origen))
                return

    comercial = buscar_comercial(familia.nombre) or buscar_comercial(next(iter(familia.nombres_pdf), ""))
    if comercial is not None:
        agregar(comercial.alternativa, comercial.metrica_compatible)
    agregar(INCLUIDAS_POR_CLASE[familia.clase], False)
    for nombre in list(INCLUIDAS_POR_CLASE.values()) + OTRAS_INCLUIDAS + WINDOWS_COMUNES:
        agregar(nombre, False)
    # No ofrecer la misma familia que se quiere reemplazar
    resultado = [c for c in resultado if familia_de(c.familia) != familia.clave]
    medido = _ancho_medio_original(doc, familia)
    if medido and medido[0] > 0:      # una fuente con anchos en 0 no sirve para comparar
        original, estilo, por_letra = medido
        for c in resultado:
            try:
                cara = c.cara(estilo)
                fuente = pymupdf.Font(fontbuffer=bytes_de_fuente(cara.archivo, cara.indice))
            except Exception:
                continue
            c.parecido = _ancho_medio(fuente) / original
            # Compatible = cada letra tiene el mismo ancho (p. ej. Arial frente a Helvetica)
            if len(por_letra) >= 20 and all(abs(fuente.glyph_advance(ord(ch)) - w) <= 0.005 * max(w, 0.1)
                                            for ch, w in por_letra.items()):
                c.compatible = True
    # Nunca se cambia de clase: una sans se reemplaza por una sans, una serif por una serif
    resultado = [c for c in resultado if clase_tipografica(c.familia) == familia.clase]
    # Orden: la métricamente compatible primero; después, misma clase (serif/sans/mono)
    # y ancho medio más parecido; las incluidas antes que las de Windows a igual parecido
    resultado.sort(key=lambda c: (not c.compatible, False,
                                  round(abs(c.parecido - 1), 2), c.origen != "incluida"))
    return resultado


# ---------------------------------------------------------------------- operación
@dataclass
class AjusteLinea:
    pagina: int
    texto: str
    escala: float           # 1.0 = sin ajuste
    y: float = 0.0          # línea base (coordenadas sin rotar), para ir a la línea

    @property
    def porcentaje(self) -> float:
        return (self.escala - 1) * 100


@dataclass
class TramoReemplazo:
    rect: tuple                         # franja a borrar
    zona: tuple                         # zona que puede cambiar (fidelidad)
    glifos: list[tuple[str, float]]     # (carácter, x) sin espacios
    y: float
    tamano: float
    color: tuple
    modo: int
    opacidad: float
    escala: float
    estilo: str                         # clave del estilo de la cara sustituta
    objetivo: list[tuple]               # claves de glifos que se borran


class SustituirFuentes(Operacion):
    nombre = "Reemplazar fuente"

    def __init__(self, por_pagina: dict[int, list[TramoReemplazo]], fuentes: dict[str, bytes], nombres: dict[str, str],
                 descripcion: str):
        self.por_pagina = por_pagina
        self.fuentes = fuentes            # estilo -> bytes de la sustituta (recortada)
        self.nombres = nombres            # estilo -> nombre PostScript para /BaseFont
        self._descripcion = descripcion

    def aplicar(self, doc: pymupdf.Document) -> None:
        xrefs: dict[str, int] = {}
        for pno, tramos in sorted(self.por_pagina.items()):
            pag = doc[pno]
            independizar_recursos(doc, pno)
            antes = glifos_de_pagina(pag)
            objetivo = {tuple(g) for t in tramos for g in t.objetivo}
            borrar_texto(doc, pno, [pymupdf.Rect(t.rect) for t in tramos])
            pag = doc[pno]
            despues = glifos_de_pagina(pag)
            if (antes - objetivo) - despues:
                raise Limitacion(f"En la página {pno + 1} el reemplazo habría borrado otro texto. No se aplicó.")
            for estilo in {t.estilo for t in tramos}:
                xref = pag.insert_font(fontname=f"PDFSr{estilo or 'regular'}", fontbuffer=self.fuentes[estilo])
                if estilo not in xrefs:
                    xrefs[estilo] = xref
                    _etiquetar_subconjunto(doc, xref, self.nombres[estilo], self.fuentes[estilo])
            for t in tramos:
                if not t.glifos:
                    continue
                ln = LineaADibujar(list(t.glifos), t.y, t.tamano, tuple(t.color), t.modo, t.opacidad, t.escala,
                                   fuente_xref=xrefs[t.estilo])
                dibujar_con_fuente_original(doc, pag, ln)

    def paginas_afectadas(self) -> set[int]:
        return set(self.por_pagina)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return {p: [t.zona for t in tramos] for p, tramos in self.por_pagina.items()}

    def descripcion(self) -> str:
        return self._descripcion


def _etiquetar_subconjunto(doc: pymupdf.Document, xref: int, nombre: str, datos: bytes) -> None:
    """Pone la etiqueta estándar de subconjunto ('ABCDEF+Nombre') en la fuente embebida."""
    h = hashlib.md5(datos).digest()
    etiqueta = "".join(chr(ord("A") + b % 26) for b in h[:6])
    base = "/" + etiqueta + "+" + "".join(c for c in nombre if c.isalnum() or c in "-_")
    doc.xref_set_key(xref, "BaseFont", base)
    t, v = doc.xref_get_key(xref, "DescendantFonts")
    if t == "array":
        desc = int(v.strip("[]").split()[0])
        doc.xref_set_key(desc, "BaseFont", base)
        t2, v2 = doc.xref_get_key(desc, "FontDescriptor")
        if t2 == "xref":
            doc.xref_set_key(int(v2.split()[0]), "FontName", base)


@dataclass
class PlanSustitucion:
    operacion: SustituirFuentes | None
    ajustes: list[AjusteLinea]                    # líneas con ajuste mayor al umbral
    reemplazados: int                             # fragmentos reemplazados
    omitidos: list[str]                           # motivos de lo que no se pudo
    paginas: set[int]

    def resumen(self) -> str:
        if not self.reemplazados:
            texto = "No hay fragmentos para reemplazar con las fuentes elegidas."
            if self.omitidos:
                texto += f" {len(self.omitidos)} caso(s) no se pueden reemplazar."
            return texto
        texto = f"Se reemplazarán {self.reemplazados} fragmento(s) en {len(self.paginas)} página(s)."
        if self.ajustes:
            texto += (f" {len(self.ajustes)} línea(s) necesitan un ajuste de ancho mayor a "
                      f"±{UMBRAL_AJUSTE * 100:.0f}%.")
        else:
            texto += " Ninguna línea necesita un ajuste de ancho mayor a ±5%."
        if self.omitidos:
            texto += f" {len(self.omitidos)} caso(s) no se pueden reemplazar."
        return texto


def posiciones_sustitucion(tr: Tramo, cod, fuente: pymupdf.Font, ajustar: bool) -> tuple[list[tuple[str, float]], float]:
    """Posiciones de los glifos con la fuente nueva y la escala horizontal de la línea.

    Cada palabra empieza donde empezaba (más lo que se corrió la anterior) y se arma con
    los avances naturales de la fuente nueva; la escala se elige para que la suma de los
    anchos de las palabras sea igual a la original. Como los espacios no cambian, el tramo
    termina exactamente donde terminaba: se conservan márgenes, alineación y tablas."""
    escala_orig, letras, _ = _espaciado(tr, cod) if cod is not None else (1.0, 0.0, 0.0)

    def avance_orig(c: str) -> float:
        if cod is not None:
            return cod.avance(c) / 1000 * tr.tamano * escala_orig
        return 0.0

    # Palabras: listas de glifos no-espacio consecutivos
    palabras: list[list] = []
    for g in tr.glifos:
        if g.c.strip():
            if not palabras or palabras[-1][-1] is None:
                palabras.append([g])
            else:
                palabras[-1].append(g)
        elif palabras and palabras[-1][-1] is not None:
            palabras[-1].append(None)       # marca de fin de palabra
    palabras = [[g for g in p if g is not None] for p in palabras]
    palabras = [p for p in palabras if p]
    if not palabras:
        return [], 1.0

    def ancho_original(p) -> float:
        ultimo = p[-1]
        extra = avance_orig(ultimo.c) if cod is not None else (ultimo.bbox[2] - ultimo.bbox[0])
        return ultimo.x - p[0].x + extra

    total_orig = sum(ancho_original(p) for p in palabras)
    total_nuevo = sum(fuente.glyph_advance(ord(g.c)) * tr.tamano for p in palabras for g in p)
    total_letras = sum((len(p) - 1) * letras for p in palabras)
    if ajustar and total_nuevo > 0:
        escala = (total_orig - total_letras) / total_nuevo
    else:
        escala = escala_orig
    tokens: list[tuple[str, float]] = []
    corrimiento = 0.0
    for p in palabras:
        x = p[0].x + corrimiento
        for g in p:
            tokens.append((g.c, x))
            x += fuente.glyph_advance(ord(g.c)) * tr.tamano * escala + letras
        x -= letras
        corrimiento += (x - (p[0].x + corrimiento)) - ancho_original(p)
    return tokens, escala


def _estilo_tramo(tr: Tramo) -> str:
    return estilo_de(tr.fuente, tr.flags)


def planificar_sustitucion(doc: pymupdf.Document, elecciones: dict[str, Candidata],
                           ajustar_ancho: bool = True, paginas: set[int] | None = None) -> PlanSustitucion:
    """elecciones: {clave de familia: candidata elegida}."""
    tramos_por_pagina: dict[int, list[tuple[Tramo, str, str]]] = {}
    caracteres: dict[str, set[str]] = {}
    caras: dict[str, FuenteSistema] = {}
    omitidos: list[str] = []
    for pno, pag in enumerate(doc):
        if paginas is not None and pno not in paginas:
            continue
        for bloque in lineas_de_pagina(pag):
            for ln in bloque:
                for tr in ln.tramos:
                    cand = elecciones.get(familia_de(tr.fuente))
                    # El texto invisible (capa de OCR) no se reemplaza: no cambia el aspecto
                    if cand is None or not tr.texto.strip() or tr.modo == MODO_INVISIBLE:
                        continue
                    estilo = _estilo_tramo(tr)
                    cara = cand.cara(estilo)
                    clave = f"{cara.archivo}#{cara.indice}"
                    caras[clave] = cara
                    caracteres.setdefault(clave, set()).update(tr.texto)
                    tramos_por_pagina.setdefault(pno, []).append((tr, clave, ln.texto))

    # Subconjunto de cada cara con todos los caracteres que usa el documento
    fuentes: dict[str, bytes] = {}
    metricas: dict[str, pymupdf.Font] = {}
    nombres: dict[str, str] = {}
    for clave, cara in caras.items():
        completa = pymupdf.Font(fontbuffer=bytes_de_fuente(cara.archivo, cara.indice))
        faltan = {c for c in caracteres[clave] if c.strip() and not completa.has_glyph(ord(c))}
        texto = "".join(sorted(caracteres[clave] - faltan))
        fuentes[clave] = subconjunto(bytes_de_fuente(cara.archivo, cara.indice), texto)
        metricas[clave] = pymupdf.Font(fontbuffer=fuentes[clave])
        nombres[clave] = cara.postscript or cara.nombre_visible.replace(" ", "")

    por_pagina: dict[int, list[TramoReemplazo]] = {}
    ajustes: list[AjusteLinea] = []
    reemplazados = 0
    cache: dict = {}
    for pno, lista in tramos_por_pagina.items():
        pag = doc[pno]
        reemplazos = []
        ajustes_pagina: list[AjusteLinea] = []
        for tr, clave, texto_linea in lista:
            fuente = metricas[clave]
            faltan = [c for c in tr.texto if c.strip() and not fuente.has_glyph(ord(c))]
            if faltan:
                omitidos.append(f"Página {pno + 1}: «{tr.texto[:30]}» (la sustituta no tiene «{faltan[0]}»)")
                continue
            cod = codificador_de_tramo(doc, pag, tr, cache)
            visibles = [g for g in tr.glifos if g.c.strip()]
            tokens, escala = posiciones_sustitucion(tr, cod, fuente, ajustar_ancho)
            if abs(escala - 1) > UMBRAL_AJUSTE:
                ajustes_pagina.append(AjusteLinea(pno, texto_linea.strip()[:80], escala, tr.glifos[0].y))
            reemplazos.append(TramoReemplazo(
                rect=tuple(_franja_borrado(Linea([tr]))), zona=_zona_tramo(tr),
                glifos=tokens, y=tr.glifos[0].y, tamano=tr.tamano,
                color=tr.color, modo=tr.modo, opacidad=tr.opacidad, escala=escala, estilo=clave,
                objetivo=[_clave_glifo(g.c, g.x, g.y) for g in visibles if ord(g.c) > 32]))
        if not reemplazos:
            continue
        # Prueba en una copia de la página: si el borrado no se puede hacer, la página se omite
        # entera (y sus líneas no se cuentan ni se listan entre los ajustes)
        temporal = pymupdf.open()
        temporal.insert_pdf(doc, from_page=pno, to_page=pno)
        try:
            borrar_texto(temporal, 0, [pymupdf.Rect(t.rect) for t in reemplazos])
        except ErrorDocumento as e:
            omitidos.append(f"Página {pno + 1}: {e}")
            continue
        finally:
            temporal.close()
        por_pagina[pno] = reemplazos
        reemplazados += len(reemplazos)
        ajustes += ajustes_pagina

    # Un ajuste por línea en el informe (la mayor desviación)
    unicos: dict[tuple[int, str], AjusteLinea] = {}
    for a in ajustes:
        k = (a.pagina, a.texto)
        if k not in unicos or abs(a.escala - 1) > abs(unicos[k].escala - 1):
            unicos[k] = a
    ajustes = sorted(unicos.values(), key=lambda a: (a.pagina, -abs(a.escala - 1)))

    familias = ", ".join(sorted({c.familia for c in elecciones.values()}))
    op = SustituirFuentes(por_pagina, fuentes, nombres, f"Reemplazar fuentes por {familias}") if por_pagina else None
    return PlanSustitucion(op, ajustes, reemplazados, omitidos, set(por_pagina))



def _zona_tramo(tr: Tramo) -> tuple:
    caja = tr.bbox
    y = tr.glifos[0].y
    return tuple(pymupdf.Rect(caja.x0 - 1, y - 1.2 * tr.tamano, caja.x1 + 1, y + 0.45 * tr.tamano))


def vista_previa(doc: pymupdf.Document, plan: PlanSustitucion) -> pymupdf.Document:
    """Copia en memoria del documento con la sustitución aplicada (para mostrar lado a lado)."""
    copia = pymupdf.open("pdf", doc.tobytes())
    if plan.operacion is not None:
        plan.operacion.aplicar(copia)
    return copia
