"""Procesamiento por lotes: aplica OCR, compresión, marca de agua, numeración y/o la
conversión a Word editable a todos los PDFs de una carpeta.

Cada archivo se procesa sobre una copia y el resultado va a la carpeta de destino (nunca se
pisa un archivo que ya exista). Cada guardado pasa por la misma verificación de fidelidad
que en la interfaz: si falla, ese archivo no se guarda y queda en el registro. Un archivo
con error no frena el lote. El registro se escribe a medida que avanza, así queda aunque
se cancele.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from pdf_studio.core.comprimir import NivelCompresion, comprimir_y_verificar, formato_tamano
from pdf_studio.core.documento import Documento, ErrorContrasena, ErrorDocumento, ErrorFidelidad, Limitacion
from pdf_studio.core.marcas import MarcaDeAgua, NumerarPaginas, OpcionesMarca, OpcionesNumeracion, Posicion

CARPETA_RESULTADOS = "procesados"
SUFIJO = "_procesado"

OK, AVISO, ERROR, OMITIDO = "ok", "aviso", "error", "omitido"
TEXTO_ESTADO = {OK: "OK", AVISO: "OK con aviso", ERROR: "Error", OMITIDO: "Omitido"}

# avance(hechos, total, archivo actual o None al terminar, último resultado o None)
Avance = Callable[[int, int, str | None, "ResultadoArchivo | None"], None]


@dataclass
class OpcionesLote:
    carpeta: str
    destino: str = ""                        # vacío = <carpeta>\procesados
    subcarpetas: bool = False
    ocr: bool = False                        # modo «Idéntico» (capa invisible) en las páginas sin texto
    comprimir: str | None = None             # NivelCompresion
    marca: dict | None = None                # OpcionesMarca (texto)
    numeracion: dict | None = None           # OpcionesNumeracion
    editable: bool = False                   # además, un .docx de las páginas escaneadas

    @property
    def carpeta_destino(self) -> Path:
        return Path(self.destino) if self.destino else Path(self.carpeta) / CARPETA_RESULTADOS

    @property
    def modifica_pdf(self) -> bool:
        return bool(self.ocr or self.comprimir or self.marca or self.numeracion)

    def tareas(self) -> list[str]:
        nombres = []
        if self.ocr:
            nombres.append("OCR")
        if self.marca:
            nombres.append("marca de agua")
        if self.numeracion:
            nombres.append("numeración")
        if self.comprimir:
            nombres.append(f"compresión {NivelCompresion(self.comprimir).texto.lower()}")
        if self.editable:
            nombres.append("conversión a Word editable")
        return nombres

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "OpcionesLote":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


def opciones_marca(d: dict) -> OpcionesMarca:
    o = OpcionesMarca(**{k: v for k, v in d.items() if k in OpcionesMarca.__dataclass_fields__ and k != "imagen"})
    o.posicion = Posicion(o.posicion)
    return o


def opciones_numeracion(d: dict) -> OpcionesNumeracion:
    o = OpcionesNumeracion(**{k: v for k, v in d.items() if k in OpcionesNumeracion.__dataclass_fields__})
    o.posicion = Posicion(o.posicion)
    return o


@dataclass
class ResultadoArchivo:
    archivo: str                             # relativo a la carpeta del lote
    estado: str = OK
    salidas: list[str] = field(default_factory=list)
    mensajes: list[str] = field(default_factory=list)
    segundos: float = 0.0

    def linea(self) -> str:
        detalle = "; ".join(self.mensajes)
        return f"[{TEXTO_ESTADO[self.estado]}] {self.archivo}" + (f" — {detalle}" if detalle else "")

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, d: dict) -> "ResultadoArchivo":
        return cls(d["archivo"], d.get("estado", OK), list(d.get("salidas", [])), list(d.get("mensajes", [])),
                   float(d.get("segundos", 0.0)))


@dataclass
class InformeLote:
    resultados: list[ResultadoArchivo] = field(default_factory=list)
    registro: str = ""                       # ruta del archivo de registro
    destino: str = ""
    cancelado: bool = False

    def cuenta(self, estado: str) -> int:
        return sum(1 for r in self.resultados if r.estado == estado)

    def resumen(self) -> str:
        partes = [f"{len(self.resultados)} archivo{'s' if len(self.resultados) != 1 else ''}"]
        for estado in (OK, AVISO, ERROR, OMITIDO):
            n = self.cuenta(estado)
            if n:
                partes.append(f"{n} {TEXTO_ESTADO[estado].lower()}")
        texto = ", ".join(partes) + "."
        return ("Cancelado: " if self.cancelado else "") + texto

    def a_dict(self) -> dict:
        return {"resultados": [r.a_dict() for r in self.resultados], "registro": self.registro,
                "destino": self.destino, "cancelado": self.cancelado}

    @classmethod
    def desde_dict(cls, d: dict) -> "InformeLote":
        return cls([ResultadoArchivo.desde_dict(r) for r in d.get("resultados", [])], d.get("registro", ""),
                   d.get("destino", ""), bool(d.get("cancelado", False)))


# ---------------------------------------------------------------------- archivos
def listar_pdfs(carpeta: Path | str, subcarpetas: bool = False, excluir: Path | None = None) -> list[Path]:
    """Los PDFs de la carpeta (sin la de resultados, para no volver a procesarlos)."""
    carpeta = Path(carpeta)
    if not carpeta.is_dir():
        raise Limitacion(f"No existe la carpeta {carpeta}.")
    patron = "**/*" if subcarpetas else "*"
    excluir = excluir.resolve() if excluir is not None else None
    archivos = []
    for ruta in carpeta.glob(patron):
        if not ruta.is_file() or ruta.suffix.lower() != ".pdf" or ruta.name.endswith(".pdfstudio-tmp"):
            continue
        if excluir is not None and (ruta.resolve() == excluir or excluir in ruta.resolve().parents):
            continue
        archivos.append(ruta)
    return sorted(archivos, key=lambda p: str(p).lower())


def _ruta_libre(ruta: Path) -> Path:
    """Si ya existe, «nombre (2).pdf», «nombre (3).pdf»…: nunca se pisa nada."""
    if not ruta.exists():
        return ruta
    n = 2
    while True:
        candidata = ruta.with_name(f"{ruta.stem} ({n}){ruta.suffix}")
        if not candidata.exists():
            return candidata
        n += 1


def _destino_de(ruta: Path, opciones: OpcionesLote, extension: str = ".pdf") -> Path:
    carpeta = Path(opciones.carpeta)
    relativa = ruta.relative_to(carpeta) if ruta.is_relative_to(carpeta) else Path(ruta.name)
    destino = opciones.carpeta_destino / relativa.parent
    nombre = relativa.stem
    if destino.resolve() == ruta.parent.resolve():
        nombre += SUFIJO                       # en la misma carpeta, con otro nombre
    return _ruta_libre(destino / f"{nombre}{extension}")


# ---------------------------------------------------------------------- un archivo
def procesar_archivo(ruta: Path, opciones: OpcionesLote, trabajo: Path) -> ResultadoArchivo:
    """Procesa una copia de `ruta`; el original nunca se toca."""
    inicio = time.monotonic()
    carpeta = Path(opciones.carpeta)
    nombre = str(ruta.relative_to(carpeta)) if ruta.is_relative_to(carpeta) else ruta.name
    resultado = ResultadoArchivo(nombre)
    trabajo.mkdir(parents=True, exist_ok=True)
    copia = trabajo / "entrada.pdf"
    try:
        shutil.copy2(ruta, copia)
    except OSError as e:
        resultado.estado, resultado.mensajes = ERROR, [f"no se pudo leer ({e.strerror or type(e).__name__})"]
        return resultado
    try:
        if opciones.modifica_pdf:
            _procesar_pdf(copia, ruta, opciones, trabajo, resultado)
        if opciones.editable:
            _convertir(copia, ruta, opciones, trabajo, resultado)
    except ErrorContrasena:
        resultado.estado, resultado.mensajes = OMITIDO, ["protegido con contraseña"]
    except ErrorFidelidad:
        resultado.estado = ERROR
        resultado.mensajes.append("no se guardó: la verificación de fidelidad visual falló")
    except Limitacion as e:
        resultado.estado = AVISO if resultado.salidas else OMITIDO
        resultado.mensajes.append(str(e))
    except ErrorDocumento as e:
        resultado.estado = ERROR
        resultado.mensajes.append(str(e))
    except Exception as e:                    # un archivo raro no frena el lote
        resultado.estado = ERROR
        resultado.mensajes.append(f"error inesperado ({type(e).__name__})")
    if resultado.estado == OK and not resultado.salidas:
        resultado.estado = OMITIDO
        resultado.mensajes.append("sin cambios que guardar")
    resultado.segundos = round(time.monotonic() - inicio, 1)
    return resultado


def _procesar_pdf(copia: Path, ruta: Path, opciones: OpcionesLote, trabajo: Path,
                  resultado: ResultadoArchivo) -> None:
    from pdf_studio.core import ocr

    documento = Documento(copia)
    try:
        doc = documento.doc
        total = doc.page_count
        cambios = False
        if opciones.ocr:
            sin_texto = ocr.paginas_sin_texto(doc)
            if sin_texto:
                capas = ocr.reconocer(doc, sin_texto, trabajo)
                if capas:
                    documento.ejecutar(ocr.AgregarCapaOCR(capas))
                    cambios = True
                    resultado.mensajes.append(f"OCR en {len(capas)} pág.")
                else:
                    resultado.mensajes.append("OCR: no se reconoció texto")
                    resultado.estado = AVISO
            else:
                resultado.mensajes.append("OCR: ya tenía texto")
        if opciones.marca:
            documento.ejecutar(MarcaDeAgua(list(range(total)), opciones_marca(opciones.marca)))
            cambios = True
            resultado.mensajes.append("marca de agua")
        if opciones.numeracion:
            try:
                documento.ejecutar(NumerarPaginas(list(range(total)), opciones_numeracion(opciones.numeracion)))
            except ValueError as e:
                raise Limitacion(f"numeración: {e}") from e
            cambios = True
            resultado.mensajes.append("numeración")
        if cambios:
            guardado = documento.preparar_guardado()
            if not guardado.es_fiel:
                raise ErrorFidelidad(guardado)
            intermedio = trabajo / "procesado.pdf"
            documento.confirmar_guardado(guardado, intermedio)
        else:
            intermedio = copia
    finally:
        documento.cerrar()
    final = intermedio
    if opciones.comprimir:
        comprimido = trabajo / "comprimido.pdf"
        r = comprimir_y_verificar(intermedio, comprimido, NivelCompresion(opciones.comprimir))
        if not r.fidelidad.ok:
            resultado.estado = AVISO
            resultado.mensajes.append("compresión descartada: cambiaba el aspecto")
        elif r.reduce:
            final = comprimido
            resultado.mensajes.append(f"comprimido {formato_tamano(r.tamano_antes)} → {formato_tamano(r.tamano_despues)}")
        else:
            resultado.mensajes.append("compresión: no achicaba el archivo")
    if final is copia:
        return                                 # nada cambió: no se escribe una copia igual
    destino = _destino_de(ruta, opciones)
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
    try:
        shutil.copy2(final, temporal)
        temporal.replace(destino)
    except OSError as e:
        temporal.unlink(missing_ok=True)
        raise ErrorDocumento(f"no se pudo escribir el resultado ({e.strerror or type(e).__name__})") from e
    resultado.salidas.append(str(destino))


def _convertir(copia: Path, ruta: Path, opciones: OpcionesLote, trabajo: Path, resultado: ResultadoArchivo) -> None:
    import pymupdf

    from pdf_studio.core import conversion, ocr
    from pdf_studio.core.exportar_editable import exportar_docx
    with pymupdf.open(copia) as doc:
        if doc.needs_pass:
            raise ErrorContrasena("protegido")
        paginas = ocr.paginas_sin_texto(doc)
    if not paginas:
        resultado.mensajes.append("Word: no tiene páginas escaneadas")
        return
    conversion.verificar_dependencias()
    exe, tessdata = conversion.herramientas_ocr()
    paginas = paginas[:conversion.MAXIMO_PAGINAS]
    rec = conversion.convertir(conversion.paginas_de_pdf(copia, None, paginas), exe, tessdata,
                               trabajo / "conversion", len(paginas))
    destino = _destino_de(ruta, opciones, ".docx")
    destino.parent.mkdir(parents=True, exist_ok=True)
    avisos = exportar_docx(rec, destino, rec.fuente.familia if rec.fuente else None)
    resultado.salidas.append(str(destino))
    dudosas = len(rec.dudosas())
    resultado.mensajes.append(f"Word con {len(paginas)} pág." + (f", {dudosas} palabras para revisar" if dudosas
                                                                    else ""))
    if avisos:
        resultado.estado = AVISO
        resultado.mensajes += avisos


# ---------------------------------------------------------------------- el lote
def procesar_lote(opciones: OpcionesLote, avance: Avance | None = None,
                  cancelado: Callable[[], bool] | None = None) -> InformeLote:
    if not (opciones.modifica_pdf or opciones.editable):
        raise Limitacion("Elegí al menos una tarea para el lote.")
    destino = opciones.carpeta_destino
    archivos = listar_pdfs(opciones.carpeta, opciones.subcarpetas, excluir=destino
                           if destino.resolve() != Path(opciones.carpeta).resolve() else None)
    if not archivos:
        raise Limitacion(f"No hay PDFs en {opciones.carpeta}.")
    try:
        destino.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ErrorDocumento(f"No se pudo crear la carpeta de destino {destino}: {e.strerror or e}") from e
    marca_tiempo = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    registro = _ruta_libre(destino / f"registro_lote_{marca_tiempo}.txt")
    informe = InformeLote(registro=str(registro), destino=str(destino))
    encabezado = [f"Procesamiento por lotes — {_dt.datetime.now():%d/%m/%Y %H:%M}",
                  f"Carpeta: {opciones.carpeta}" + (" (con subcarpetas)" if opciones.subcarpetas else ""),
                  f"Destino: {destino}", f"Tareas: {', '.join(opciones.tareas())}", f"Archivos: {len(archivos)}", ""]
    _escribir_registro(registro, encabezado)
    with tempfile.TemporaryDirectory(prefix="pdfstudio_lote_") as tmp:
        for i, ruta in enumerate(archivos):
            if cancelado is not None and cancelado():
                informe.cancelado = True
                break
            if avance is not None:
                avance(i, len(archivos), ruta.name, informe.resultados[-1] if informe.resultados else None)
            trabajo = Path(tmp) / f"{i:05d}"
            r = procesar_archivo(ruta, opciones, trabajo)
            shutil.rmtree(trabajo, ignore_errors=True)
            informe.resultados.append(r)
            _escribir_registro(registro, [r.linea()] + [f"    → {s}" for s in r.salidas])
    _escribir_registro(registro, ["", informe.resumen()])
    if avance is not None:
        avance(len(informe.resultados), len(archivos), None, informe.resultados[-1] if informe.resultados else None)
    return informe


def _escribir_registro(ruta: Path, lineas: list[str]) -> None:
    try:
        with open(ruta, "a", encoding="utf-8") as f:
            f.write("\n".join(lineas) + "\n")
    except OSError:
        pass                                   # el informe final igual se muestra en pantalla
