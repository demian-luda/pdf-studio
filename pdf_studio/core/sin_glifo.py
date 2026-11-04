"""Caracteres sin glifo en todo el documento: cuántas veces aparecen y qué hacer con ellos.

Es muy común en apuntes y PDFs exportados desde Word: un carácter de una fuente de símbolos que
no tiene el glifo (se ve como un rectángulo .notdef), a menudo después de los números de una lista.
Por defecto se conserva. Si se pide otra cosa, se cambia solo ese glifo en su operador: todo
lo demás queda donde estaba, y se verifica que el resto de cada página no cambie.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from pdf_studio.core.cirugia import Mostrar, Unidad, extra_de_espaciado, leer_flujo, reescribir
from pdf_studio.core.documento import Limitacion, Operacion
from pdf_studio.core.fidelidad import DPI_FIDELIDAD, comparar_paginas
from pdf_studio.core.fuentes import IndiceFuentesSistema, sin_prefijo
from pdf_studio.core.pdfutil import independizar_recursos
from pdf_studio.core.texto import (FLAGS_EXTRACCION, Glifo, Linea, _nombre_recurso, lineas_de_pagina,
                                   plan_de_reemplazo_puntual, subconjunto)

UMBRAL_CASI_SIEMPRE = 0.8    # desde esta proporción, el contexto se considera típico
VINETA = "•"


class Accion:
    CONSERVAR = "conservar"
    ESPACIO = "espacio"
    TABULACION = "tabulacion"
    VINETA = "vineta"
    QUITAR = "quitar"


TEXTO_ACCION = {
    Accion.CONSERVAR: "Conservar",
    Accion.ESPACIO: "Reemplazar por espacio",
    Accion.TABULACION: "Reemplazar por tabulación",
    Accion.VINETA: "Reemplazar por viñeta real (•)",
    Accion.QUITAR: "Quitar",
}


class Contexto:
    TRAS_NUMERO = "después de un número"
    AL_COMIENZO = "al comienzo de la línea"
    AL_FINAL = "al final de la línea"
    ENTRE_PALABRAS = "entre palabras"
    DENTRO_DE_PALABRA = "dentro de una palabra"


@dataclass
class Aparicion:
    pagina: int
    x: float
    y: float
    bbox: tuple
    tamano: float
    contexto: str
    linea: str              # el texto de la línea, para mostrarla como ejemplo


@dataclass
class PatronSinGlifo:
    caracter: str
    fuente: str
    apariciones: list[Aparicion] = field(default_factory=list)

    @property
    def codigo(self) -> str:
        return f"U+{ord(self.caracter):04X}"

    @property
    def total(self) -> int:
        return len(self.apariciones)

    @property
    def paginas(self) -> list[int]:
        return sorted({a.pagina for a in self.apariciones})

    @property
    def clave(self) -> tuple[str, str]:
        return self.caracter, self.fuente

    @property
    def contexto_tipico(self) -> str:
        """'siempre después de un número', 'casi siempre…' o, si no hay un patrón, vacío."""
        conteo: dict[str, int] = {}
        for a in self.apariciones:
            conteo[a.contexto] = conteo.get(a.contexto, 0) + 1
        if not conteo:
            return ""
        contexto, veces = max(conteo.items(), key=lambda p: p[1])
        if veces == self.total:
            return f"siempre {contexto}" if self.total > 1 else contexto
        if veces / self.total >= UMBRAL_CASI_SIEMPRE:
            return f"casi siempre {contexto} ({veces} de {self.total})"
        return ""

    def resumen(self) -> str:
        veces = "1 vez" if self.total == 1 else f"{self.total} veces"
        n = len(self.paginas)
        paginas = "en 1 página" if n == 1 else f"en {n} páginas"
        tipico = self.contexto_tipico
        return f"{self.codigo} ({self.fuente}): {veces} {paginas}" + (f", {tipico}" if tipico else "")


def _contexto(glifos: list[Glifo], n: int) -> str:
    previos = [g.c for g in glifos[:n] if g.c.strip()]
    siguientes = [g.c for g in glifos[n + 1:] if g.c.strip()]
    if not previos:
        return Contexto.AL_COMIENZO
    # '1.' y '1)' cuentan como número: es la forma de una lista numerada
    ultimo = previos[-1]
    if ultimo.isdigit() or (ultimo in ".)-:" and len(previos) >= 2 and previos[-2].isdigit()):
        return Contexto.TRAS_NUMERO
    if not siguientes:
        return Contexto.AL_FINAL
    pegado = n > 0 and n + 1 < len(glifos) and glifos[n - 1].c.strip() and glifos[n + 1].c.strip()
    return Contexto.DENTRO_DE_PALABRA if pegado else Contexto.ENTRE_PALABRAS


def inventario(doc: pymupdf.Document) -> list[PatronSinGlifo]:
    """Los caracteres sin glifo del documento, del más frecuente al menos frecuente."""
    patrones: dict[tuple[str, str], PatronSinGlifo] = {}
    for pno in range(doc.page_count):
        try:
            bloques = lineas_de_pagina(doc[pno])
        except Exception:
            continue          # una página dañada no frena el análisis del resto
        for lineas in bloques:
            for ln in lineas:
                glifos = ln.glifos
                for n, g in enumerate(glifos):
                    if not g.sin_glifo or g.c.isspace():
                        continue
                    tramo = _tramo_de(ln, g)
                    fuente = sin_prefijo(tramo.fuente)
                    patron = patrones.setdefault((g.c, fuente), PatronSinGlifo(g.c, fuente))
                    patron.apariciones.append(Aparicion(pno, g.x, g.y, g.bbox, tramo.tamano, _contexto(glifos, n),
                                                        ln.texto))
    return sorted(patrones.values(), key=lambda p: -p.total)


def _tramo_de(ln: Linea, glifo: Glifo):
    return next(t for t in ln.tramos if any(g is glifo for g in t.glifos))


class ResolverSinGlifo(Operacion):
    """Cambia los caracteres sin glifo elegidos, sin mover nada más."""
    nombre = "Caracteres sin glifo"

    def __init__(self, por_pagina: dict[int, dict], descripcion: str):
        self.por_pagina = por_pagina
        self._descripcion = descripcion

    def aplicar(self, doc: pymupdf.Document) -> None:
        for pno, datos in sorted(self.por_pagina.items()):
            independizar_recursos(doc, pno)

            def nombre_de_xref(xref: int, pagina: int = pno) -> str:
                return _nombre_recurso(doc, doc[pagina], xref)

            reescribir(doc, pno, datos["reemplazos"], {}, datos["fuentes"], nombre_de_xref)
            pag = doc[pno]
            despues: dict[tuple, list[float]] = {}
            total = 0
            for span in pag.get_texttrace():
                for c in span["chars"]:
                    if c[1] != -1:
                        despues.setdefault((c[0], round(c[2][1], 1)), []).append(c[2][0])
                        total += 1
            for ucs, x, y in datos["esperados"]:
                xs = despues.get((ucs, round(y, 1)), [])
                cerca = next((v for v in xs if abs(v - x) <= 0.03), None)
                if cerca is None:
                    raise Limitacion(f"En la página {pno + 1} el cambio habría movido otro texto. No se aplicó.")
                xs.remove(cerca)
            if total != len(datos["esperados"]) + datos["insertados"]:
                raise Limitacion(f"En la página {pno + 1} el cambio no dio el resultado esperado. No se aplicó.")

    def paginas_afectadas(self) -> set[int]:
        return set(self.por_pagina)

    def regiones_afectadas(self) -> dict[int, list[tuple]]:
        return {pno: datos["zonas"] for pno, datos in self.por_pagina.items()}

    def descripcion(self) -> str:
        return self._descripcion


@dataclass
class PlanResolucion:
    operacion: ResolverSinGlifo | None
    cambiados: int = 0
    omitidos: int = 0        # dentro de un Form XObject o en una página que no se pudo leer
    superpuestos: int = 0    # viñetas más anchas que el lugar del carácter


def planificar_resolucion(doc: pymupdf.Document, patrones: list[PatronSinGlifo], acciones: dict[tuple, str],
                          incluidas: IndiceFuentesSistema | None) -> PlanResolucion:
    """Arma la operación para las acciones elegidas ({clave del patrón: acción}). No modifica nada."""
    por_pagina: dict[int, dict] = {}
    plan = PlanResolucion(None)
    trabajo: dict[int, list[tuple[Aparicion, str, str]]] = {}
    for p in patrones:
        accion = acciones.get(p.clave, Accion.CONSERVAR)
        if accion == Accion.CONSERVAR:
            continue
        for a in p.apariciones:
            trabajo.setdefault(a.pagina, []).append((a, accion, p.caracter))
    if not trabajo:
        return plan
    datos_vineta = _fuente_de_vineta(incluidas)
    for pno, lista in sorted(trabajo.items()):
        pag = doc[pno]
        flujo = leer_flujo(doc, pag)
        if flujo is None:
            plan.omitidos += len(lista)
            continue
        fuentes: dict[str, bytes] = {}
        reemplazos = []
        zonas: list[tuple] = []
        insertados = 0
        borrados: set[int] = set()
        for a, accion, caracter in lista:
            indices = flujo.buscar(caracter, a.x, a.y)
            if not indices or any(flujo.glifos[k].mostrar is None for k in indices):
                plan.omitidos += 1
                continue

            def unidades_de(m: Mostrar, cod) -> list[Unidad]:
                return _unidades_de_accion(accion, m, cod, datos_vineta, fuentes)

            resultado = plan_de_reemplazo_puntual(doc, pno, flujo, indices, unidades_de)
            if resultado is None:
                plan.omitidos += 1
                continue
            r, n = resultado
            if accion == Accion.VINETA and _vineta_no_entra(r, a):
                plan.superpuestos += 1
            reemplazos += r
            insertados += n
            borrados.update(indices)
            zonas.append(tuple(pymupdf.Rect(a.bbox[0] - 1, a.y - 1.2 * a.tamano, a.bbox[2] + 1 + (
                0.5 * a.tamano if accion == Accion.VINETA else 0), a.y + 0.45 * a.tamano)))
            plan.cambiados += 1
        if reemplazos:
            esperados = [(g.ucs, g.x, g.y) for k, g in enumerate(flujo.glifos) if k not in borrados]
            por_pagina[pno] = {"reemplazos": reemplazos, "fuentes": fuentes, "esperados": esperados,
                               "insertados": insertados, "zonas": zonas}
    if por_pagina:
        n = plan.cambiados
        plan.operacion = ResolverSinGlifo(por_pagina, f"Cambiar {n} carácter{'es' if n != 1 else ''} sin glifo")
    return plan


def _fuente_de_vineta(incluidas: IndiceFuentesSistema | None) -> bytes | None:
    """Liberation Sans recortada a la viñeta: se usa si la fuente original no la tiene."""
    if incluidas is None:
        return None
    caras = [c for c in incluidas.familia("Liberation Sans") if c.estilo.lower() == "regular"]
    return subconjunto(caras[0].datos(), VINETA) if caras else None


def _unidades_de_accion(accion: str, m: Mostrar, cod, datos_vineta: bytes | None, fuentes: dict) -> list[Unidad]:
    if accion == Accion.ESPACIO and " " in cod.a_codigo and (cod.disponibles is None or " " in cod.disponibles):
        codigo = cod.codigo(" ")
        return [Unidad(codigo, cod.avance(" ") + extra_de_espaciado(m, codigo if len(codigo) == 1 else None),
                       cod.avance(" "))]
    if accion != Accion.VINETA:
        return []             # sin glifo: el hueco queda, y lo que sigue no se mueve
    if cod.puede(VINETA):
        return [Unidad(cod.codigo(VINETA), cod.avance(VINETA) + extra_de_espaciado(m, None), cod.avance(VINETA))]
    if datos_vineta is None:
        raise Limitacion("No hay una fuente con la viñeta para escribirla.")
    fuente = pymupdf.Font(fontbuffer=datos_vineta)
    fuentes["vineta"] = datos_vineta
    ancho = fuente.glyph_advance(ord(VINETA)) * 1000
    return [Unidad(fuente.has_glyph(ord(VINETA)).to_bytes(2, "big"), ancho + extra_de_espaciado(m, None), ancho, "vineta")]


def _vineta_no_entra(reemplazos: list, a: Aparicion) -> bool:
    ancho = sum(u.ancho for r in reemplazos for u in r.unidades) * a.tamano / 1000
    return ancho > (a.bbox[2] - a.bbox[0]) + 0.5


@dataclass
class VistaPrevia:
    antes: pymupdf.Pixmap | None = None
    despues: pymupdf.Pixmap | None = None
    pixeles_distintos: int = 0     # fuera de los caracteres cambiados: tiene que ser 0
    paginas: list[int] = field(default_factory=list)
    texto_ejemplo: str = ""


def vista_previa(doc: pymupdf.Document, plan: PlanResolucion, ejemplo: Aparicion | None) -> VistaPrevia:
    """Aplica la operación en una copia: muestra un ejemplo y compara el resto de cada página."""
    resultado = VistaPrevia()
    if plan.operacion is None:
        return resultado
    copia = pymupdf.open("pdf", doc.tobytes())
    try:
        plan.operacion.aplicar(copia)
        for pno, zonas in plan.operacion.regiones_afectadas().items():
            distintos, _total, _motivo = comparar_paginas(doc[pno], copia[pno], DPI_FIDELIDAD, zonas)
            resultado.pixeles_distintos += distintos
            resultado.paginas.append(pno)
        if ejemplo is not None:
            ancho = doc[ejemplo.pagina].rect.width
            clip = pymupdf.Rect(0, ejemplo.y - 2.2 * ejemplo.tamano, ancho, ejemplo.y + 1.2 * ejemplo.tamano)
            clip = clip * doc[ejemplo.pagina].rotation_matrix
            resultado.antes = doc[ejemplo.pagina].get_pixmap(dpi=120, clip=clip, alpha=False)
            resultado.despues = copia[ejemplo.pagina].get_pixmap(dpi=120, clip=clip, alpha=False)
            resultado.texto_ejemplo = copia[ejemplo.pagina].get_text("text", clip=clip, flags=FLAGS_EXTRACCION).strip()
    finally:
        copia.close()
    return resultado
