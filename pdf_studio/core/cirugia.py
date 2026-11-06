"""Edición quirúrgica del contenido de una página: cambia solo los glifos pedidos.

La redacción de MuPDF reescribe los operadores y puede correr lo que sigue a lo borrado, y
dibujar el texto nuevo al final del contenido rompe el orden de lectura (buscar y copiar).
Acá se recorre el contenido, se ubica cada glifo en su operador Tj/TJ y:
- los glifos que no cambian quedan intactos (mismos códigos, misma fuente, misma posición);
- los borrados se reemplazan por un ajuste numérico del TJ que conserva el avance;
- el texto nuevo va en el mismo lugar del contenido, con el estado de texto que ya rige.

Para ubicar los glifos se aprovecha que la traza de MuPDF los da en el orden del contenido: el
glifo k de la traza es el código k del recorrido. Si las cuentas no coinciden (operadores no
contemplados, capas ocultas), `leer_flujo` devuelve None y se usa el método anterior.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pymupdf

from pdf_studio.core.codificacion import CodificadorFuente, construir_codificador
from pdf_studio.core.fuentes import clave_fuente
from pdf_studio.core.pdfutil import Ref, parsear_objeto, valor_de_clave

GLIFO_CONTINUACION = -1      # MuPDF: segundo carácter de una ligadura, sin glifo propio
MAX_ANIDADO = 12


@dataclass
class Mostrar:
    """Un operador que dibuja texto, con el estado de texto que lo afecta."""
    indice: int                  # posición entre las instrucciones de la página
    operador: str                # Tj, TJ, ' o "
    operandos: list
    items: list                  # bytes (cadenas) y números, en orden
    fuente: str                  # nombre de recurso
    xref: int
    bytes_codigo: int
    tamano: float                # el de Tf (sin matrices)
    tc: float
    tw: float
    tz: float
    reposiciona: bool            # hubo Td, Tm, BT... desde el operador anterior


@dataclass
class GlifoFlujo:
    mostrar: Mostrar | None      # None: está dentro de un Form XObject (no se edita acá)
    item: int
    codigo: bytes
    xref: int
    ucs: int = 0
    gid: int = 0
    x: float = 0.0
    y: float = 0.0


@dataclass
class _Estado:
    fuente: str = ""
    tamano: float = 0.0
    tc: float = 0.0
    tw: float = 0.0
    tz: float = 100.0


def _numero(v) -> float:
    return float(v)


def _fuentes_por_recurso(doc: pymupdf.Document, pag: pymupdf.Page) -> dict[tuple[int, str], tuple[int, int]]:
    """{(referente, nombre de recurso): (xref, bytes por código)}; referente 0 = la página."""
    fuentes = {}
    for xref, _ext, tipo, _base, nombre, _enc, referente in pag.get_fonts(full=True):
        fuentes[(referente, nombre)] = (xref, 2 if tipo == "Type0" else 1)
    return fuentes


def _xobjects(doc: pymupdf.Document, recursos) -> dict[str, int]:
    if isinstance(recursos, Ref):
        recursos = parsear_objeto(doc.xref_object(int(recursos), compressed=True))
    if not isinstance(recursos, dict):
        return {}
    xobj = recursos.get("XObject")
    if isinstance(xobj, Ref):
        xobj = parsear_objeto(doc.xref_object(int(xobj), compressed=True))
    return {k: int(v) for k, v in xobj.items() if isinstance(v, Ref)} if isinstance(xobj, dict) else {}


class _NoSePuedeLeer(Exception):
    pass


def _recorrer(doc: pymupdf.Document, instrucciones: list, fuentes: dict, referente: int, recursos,
              glifos: list[GlifoFlujo], en_forma: bool, profundidad: int = 0) -> None:
    import pikepdf   # diferido: acelera el arranque
    if profundidad > MAX_ANIDADO:
        raise _NoSePuedeLeer
    estado = _Estado()
    pila: list[_Estado] = []
    reposiciona = True
    xobjects = _xobjects(doc, recursos)
    for indice, ins in enumerate(instrucciones):
        if not isinstance(ins, pikepdf.ContentStreamInstruction):
            continue
        op, operandos = str(ins.operator), list(ins.operands)
        if op == "q":
            pila.append(_Estado(**vars(estado)))
        elif op == "Q":
            if pila:
                estado = pila.pop()
        elif op == "Tf" and len(operandos) == 2:
            estado.fuente, estado.tamano = str(operandos[0]).lstrip("/"), _numero(operandos[1])
        elif op == "Tc" and operandos:
            estado.tc = _numero(operandos[0])
        elif op == "Tw" and operandos:
            estado.tw = _numero(operandos[0])
        elif op == "Tz" and operandos:
            estado.tz = _numero(operandos[0])
        elif op in ("BT", "Td", "TD", "Tm", "T*"):
            reposiciona = True
        elif op == "gs" and operandos and _gs_cambia_fuente(doc, recursos, str(operandos[0]).lstrip("/")):
            raise _NoSePuedeLeer
        elif op == "Do" and operandos:
            xref = xobjects.get(str(operandos[0]).lstrip("/"))
            if xref is not None and doc.xref_get_key(xref, "Subtype")[1] == "/Form":
                with pikepdf.Pdf.new() as aux:
                    internas = list(pikepdf.parse_content_stream(pikepdf.Stream(aux, doc.xref_stream(xref) or b"")))
                    _recorrer(doc, internas, fuentes, xref, valor_de_clave(doc, xref, "Resources"), glifos, True,
                              profundidad + 1)
        elif op in ("Tj", "TJ", "'", '"'):
            if op in ("'", '"'):
                reposiciona = True
            if op == '"' and len(operandos) == 3:
                estado.tw, estado.tc = _numero(operandos[0]), _numero(operandos[1])
            if (referente, estado.fuente) not in fuentes:
                raise _NoSePuedeLeer
            xref, por_codigo = fuentes[(referente, estado.fuente)]
            ultimo = operandos[-1] if operandos else None
            if op == "TJ":
                items = [bytes(e) if isinstance(e, pikepdf.String) else _numero(e) for e in ultimo]
            else:
                items = [bytes(ultimo)]
            mostrar = None if en_forma else Mostrar(indice, op, operandos, items, estado.fuente, xref, por_codigo,
                                                    estado.tamano, estado.tc, estado.tw, estado.tz, reposiciona)
            for n, item in enumerate(items):
                if isinstance(item, bytes):
                    for i in range(0, len(item), por_codigo):
                        glifos.append(GlifoFlujo(mostrar, n, item[i:i + por_codigo], xref))
            reposiciona = False


def _gs_cambia_fuente(doc: pymupdf.Document, recursos, nombre: str) -> bool:
    """Un ExtGState con /Font cambia la fuente sin Tf: el recorrido no lo sigue."""
    try:
        if isinstance(recursos, Ref):
            recursos = parsear_objeto(doc.xref_object(int(recursos), compressed=True))
        estados = recursos.get("ExtGState") if isinstance(recursos, dict) else None
        if isinstance(estados, Ref):
            estados = parsear_objeto(doc.xref_object(int(estados), compressed=True))
        estado = estados.get(nombre) if isinstance(estados, dict) else None
        if isinstance(estado, Ref):
            estado = parsear_objeto(doc.xref_object(int(estado), compressed=True))
        return isinstance(estado, dict) and "Font" in estado
    except Exception:
        return True


@dataclass
class Flujo:
    """Los glifos de la página en el orden del contenido, con su operador y su código."""
    glifos: list[GlifoFlujo]
    por_posicion: dict[tuple[float, float], list[int]] = field(default_factory=dict)

    def buscar(self, c: str, x: float, y: float) -> list[int]:
        """Índices del glifo con ese origen; varios si se dibuja encima de sí mismo (negrita falsa)."""
        indices = self.por_posicion.get((round(x, 1), round(y, 1)), [])
        iguales = [i for i in indices if self.glifos[i].ucs == ord(c)]
        return iguales or indices[:1]

    def codigo_usado(self, xref: int, c: str) -> bytes | None:
        """El código con el que la página ya dibuja ese carácter en esa fuente: es seguro que tiene
        glifo. Puede haber otros que mapeen al mismo carácter, sin glifo o sin ancho."""
        for g in self.glifos:
            if g.xref == xref and g.ucs == ord(c) and g.gid > 0:
                return g.codigo
        return None


def leer_flujo(doc: pymupdf.Document, pag: pymupdf.Page) -> Flujo | None:
    """Alinea el contenido de la página con la traza de MuPDF. None si no coinciden."""
    import pikepdf   # diferido: acelera el arranque
    try:
        glifos: list[GlifoFlujo] = []
        with pikepdf.Pdf.new() as aux:
            instrucciones = list(pikepdf.parse_content_stream(pikepdf.Stream(aux, pag.read_contents())))
            _recorrer(doc, instrucciones, _fuentes_por_recurso(doc, pag), 0,
                      valor_de_clave(doc, pag.xref, "Resources"), glifos, False)
        traza = sorted(pag.get_texttrace(), key=lambda s: s.get("seqno", 0))
    except Exception:
        return None
    flujo = Flujo(glifos)
    k = -1
    for span in traza:
        for c in span["chars"]:
            continuacion = c[1] == GLIFO_CONTINUACION and k >= 0
            if not continuacion:
                k += 1
                if k >= len(glifos):
                    return None
                g = glifos[k]
                g.ucs, g.gid, g.x, g.y = c[0], c[1], c[2][0], c[2][1]
            flujo.por_posicion.setdefault((round(c[2][0], 1), round(c[2][1], 1)), []).append(k)
    return flujo if k + 1 == len(glifos) and _fuentes_coinciden(doc, pag, traza, glifos) else None


def _fuentes_coinciden(doc: pymupdf.Document, pag: pymupdf.Page, traza: list, glifos: list[GlifoFlujo]) -> bool:
    """Segundo control de la alineación: cada glifo tiene que estar en la fuente que dice la traza."""
    nombres = {f[0]: clave_fuente(f[3]) for f in pag.get_fonts(full=True) if f[3]}
    k = -1
    for span in traza:
        clave = clave_fuente(span["font"])
        for c in span["chars"]:
            if c[1] == GLIFO_CONTINUACION and k >= 0:
                continue
            k += 1
            esperada = nombres.get(glifos[k].xref)
            if span["type"] != 3 and esperada and clave and esperada != clave:
                return False
    return True


def avance_codigo(cod: CodificadorFuente, codigo: int) -> float:
    """Ancho de un código, en milésimas del tamaño de letra."""
    if codigo in cod.anchos:
        return cod.anchos[codigo] * cod.escala_anchos
    if cod._metricas_base14 is not None:
        for ch, c in cod.a_codigo.items():
            if c == codigo:
                return cod.avance(ch)
    return cod.ancho_defecto * cod.escala_anchos


def extra_de_espaciado(m: Mostrar, codigo: bytes | None) -> float:
    """Lo que Tc y Tw le suman al avance de un glifo, en las unidades de un número de TJ."""
    if not m.tamano:
        return 0.0
    tw = m.tw if codigo == b" " else 0.0
    return (m.tc + tw) * 1000 / m.tamano


def avance_en_flujo(m: Mostrar, cod: CodificadorFuente, codigo: bytes) -> float:
    return avance_codigo(cod, int.from_bytes(codigo, "big")) + extra_de_espaciado(m, codigo)


# ---------------------------------------------------------------------- reescritura
@dataclass
class Unidad:
    """Un glifo a escribir. Sin código, es solo avance (un espacio en una fuente que no lo tiene)."""
    codigo: bytes | None
    avance: float                 # lo que avanza, en las unidades de un número de TJ
    ancho: float = 0.0            # la parte que es ancho del glifo (sin Tc ni Tw)
    fuente: int | str | None = None     # None = la del operador; xref del PDF; o clave de una fuente nueva

    def __post_init__(self) -> None:
        self.ancho = self.ancho or self.avance


@dataclass
class Reemplazo:
    borrar: list[int]             # índices de glifos en el flujo
    despues_de: int | None = None # si no se borra nada: el glifo tras el que se inserta
    antes_de: int | None = None   # o, al comienzo, antes de cuál
    unidades: list[Unidad] = field(default_factory=list)
    ajuste: float = 0.0           # número de TJ que deja lo que sigue donde corresponde


def reescribir(doc: pymupdf.Document, pagina: int, reemplazos: list[Reemplazo], adelantos: dict[int, float],
               fuentes_nuevas: dict[str, bytes], nombre_de_xref) -> None:
    """Aplica los reemplazos al contenido de la página, sin tocar ningún otro operador.

    adelantos: {índice de instrucción: número de TJ al comienzo} para correr lo que sigue en la
    línea cuando el texto nuevo no entra. nombre_de_xref: da el nombre de recurso de una fuente del PDF."""
    import pikepdf   # diferido: acelera el arranque
    pag = doc[pagina]
    nombres_nuevos: dict[str, str] = {}
    corregir: dict[str, CodificadorFuente | None] = {}
    for clave, datos in fuentes_nuevas.items():
        nombre = f"PDFSm{clave}"
        xref = pag.insert_font(fontname=nombre, fontbuffer=datos)
        nombres_nuevos[clave] = nombre
        corregir[clave] = construir_codificador(doc, xref, usados=set())

    def ajuste_real(r: Reemplazo) -> float:
        """El PDF guarda los anchos de una fuente nueva redondeados: hay que compensar con esos,
        o lo que sigue queda corrido unas centésimas."""
        ajuste = r.ajuste
        for u in r.unidades:
            cod = corregir.get(u.fuente) if isinstance(u.fuente, str) else None
            if cod is not None and u.codigo is not None:
                ajuste += avance_codigo(cod, int.from_bytes(u.codigo, "big")) - u.ancho
        return ajuste

    def nombre_de(fuente, actual: str) -> str:
        if fuente is None:
            return actual
        return nombres_nuevos[fuente] if isinstance(fuente, str) else nombre_de_xref(fuente)

    with pikepdf.Pdf.new() as aux:
        instrucciones = list(pikepdf.parse_content_stream(pikepdf.Stream(aux, pag.read_contents())))
        flujo_glifos: list[GlifoFlujo] = []
        _recorrer(doc, instrucciones, _fuentes_por_recurso(doc, pag), 0,
                  valor_de_clave(doc, pag.xref, "Resources"), flujo_glifos, False)
        # Cada operador tocado se pasa a átomos: ('g', índice, código), ('n', número) o ('u', unidad)
        atomos: dict[int, list] = {}
        posicion: dict[int, tuple[int, int]] = {}
        mostrar_de: dict[int, Mostrar] = {}
        tocados = {r_i for r in reemplazos for r_i in r.borrar}
        tocados |= {r.despues_de for r in reemplazos if r.despues_de is not None}
        tocados |= {r.antes_de for r in reemplazos if r.antes_de is not None}
        instr_tocadas = {flujo_glifos[i].mostrar.indice for i in tocados} | set(adelantos)
        for k, g in enumerate(flujo_glifos):
            if g.mostrar is None or g.mostrar.indice not in instr_tocadas or g.mostrar.indice in atomos:
                continue
            m = g.mostrar
            mostrar_de[m.indice] = m
            lista: list = []
            j = k
            for item in m.items:
                if isinstance(item, bytes):
                    for i in range(0, len(item), m.bytes_codigo):
                        lista.append(["g", j, item[i:i + m.bytes_codigo]])
                        posicion[j] = (m.indice, 0)
                        j += 1
                else:
                    lista.append(["n", item])
            atomos[m.indice] = lista
        for indice in adelantos:
            if indice not in atomos:
                raise ValueError("Instrucción de texto inexistente.")
        for r in reemplazos:
            insertado = False
            for i in r.borrar:
                lista = atomos[posicion[i][0]]
                p = next(n for n, a in enumerate(lista) if a[0] == "g" and a[1] == i)
                nuevos = []
                if not insertado:
                    nuevos = [["u", u] for u in r.unidades] + [["n", ajuste_real(r)]]
                    insertado = True
                lista[p:p + 1] = nuevos
            if insertado:
                continue
            ancla = r.despues_de if r.despues_de is not None else r.antes_de
            lista = atomos[posicion[ancla][0]]
            p = next(n for n, a in enumerate(lista) if a[0] == "g" and a[1] == ancla)
            nuevos = [["u", u] for u in r.unidades] + [["n", ajuste_real(r)]]
            donde = p + 1 if r.despues_de is not None else p
            lista[donde:donde] = nuevos
        for indice, adelanto in adelantos.items():
            atomos[indice].insert(0, ["n", adelanto])

        salida: list = []
        for indice, ins in enumerate(instrucciones):
            if indice in atomos:
                salida.extend(_instrucciones_de(mostrar_de[indice], atomos[indice], nombre_de))
            else:
                salida.append(ins)
        contenido = pikepdf.unparse_content_stream(salida)
    nuevo = doc.get_new_xref()
    doc.update_object(nuevo, "<<>>")
    doc.update_stream(nuevo, contenido)
    doc.xref_set_key(pag.xref, "Contents", f"{nuevo} 0 R")


def _instrucciones_de(m: Mostrar, atomos: list, nombre_de) -> list:
    """Vuelve a armar un operador a partir de sus átomos, cambiando de fuente solo si hace falta."""
    import pikepdf   # diferido: acelera el arranque
    salida: list = []
    if m.operador == '"' and len(m.operandos) == 3:
        salida.append(_instruccion("Tw", [m.operandos[0]]))
        salida.append(_instruccion("Tc", [m.operandos[1]]))
    if m.operador in ("'", '"'):
        salida.append(_instruccion("T*", []))
    arreglo: list = []
    cadena = bytearray()
    fuente_actual = None

    def cerrar_cadena() -> None:
        if cadena:
            arreglo.append(pikepdf.String(bytes(cadena)))
            cadena.clear()

    def cerrar_arreglo() -> None:
        cerrar_cadena()
        if arreglo:
            salida.append(_instruccion("TJ", [pikepdf.Array(list(arreglo))]))
            arreglo.clear()

    def poner_numero(valor: float) -> None:
        if abs(valor) < 1e-6:
            return
        cerrar_cadena()
        if arreglo and isinstance(arreglo[-1], Decimal):
            arreglo[-1] = _decimal(float(arreglo[-1]) + valor)
        else:
            arreglo.append(_decimal(valor))

    def cambiar_fuente(fuente) -> None:
        nonlocal fuente_actual
        if fuente == fuente_actual:
            return
        cerrar_arreglo()
        nombre = nombre_de(fuente, m.fuente)
        salida.append(_instruccion("Tf", [pikepdf.Name(f"/{nombre}"), _decimal(m.tamano)]))
        fuente_actual = fuente

    for a in atomos:
        if a[0] == "g":
            cambiar_fuente(None)
            cadena.extend(a[2])
        elif a[0] == "n":
            poner_numero(a[1])
        else:
            unidad: Unidad = a[1]
            if unidad.codigo is None:
                poner_numero(-unidad.avance)
            else:
                cambiar_fuente(unidad.fuente)
                cadena.extend(unidad.codigo)
    cambiar_fuente(None)
    cerrar_arreglo()
    return salida


def _decimal(valor: float) -> Decimal:
    return Decimal(f"{valor:.5f}")


def _instruccion(operador: str, operandos: list) -> pikepdf.ContentStreamInstruction:
    import pikepdf   # diferido: acelera el arranque
    return pikepdf.ContentStreamInstruction(operandos, pikepdf.Operator(operador))


def codificador_de(doc: pymupdf.Document, xref: int, cache: dict | None = None) -> CodificadorFuente | None:
    cache = cache if cache is not None else {}
    if xref not in cache:
        try:
            cache[xref] = construir_codificador(doc, xref)
        except Exception:
            cache[xref] = None
    return cache[xref]
