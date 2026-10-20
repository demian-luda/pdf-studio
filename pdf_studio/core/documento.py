"""Documento abierto: copia de trabajo, historial deshacer/rehacer y guardado seguro.

Garantías:
- El archivo original nunca se abre para escritura: al abrir se copia a una
  carpeta temporal y todo se hace sobre esa copia ("base").
- Cada edición es una Operacion determinista. El estado visible es siempre
  "base + operaciones activas". Deshacer reconstruye desde la base; rehacer
  vuelve a aplicar la operación. (El journalling de PyMuPDF no se usa porque
  se corrompe al combinarlo con el guardado incremental.)
- Guardar parte de una copia fresca de la base, aplica las operaciones y guarda
  en modo incremental: los bytes del original quedan intactos al principio del
  archivo y solo se agregan los objetos modificados. Antes de entregar el
  archivo se verifica la fidelidad visual de las páginas no editadas.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pymupdf

from pdf_studio.core.fidelidad import Progreso, ResultadoFidelidad, comparar_documentos


class ErrorDocumento(Exception):
    """Error con mensaje listo para mostrar al usuario."""


class ErrorContrasena(ErrorDocumento):
    pass


class ErrorFidelidad(ErrorDocumento):
    def __init__(self, guardado: "Guardado"):
        super().__init__(guardado.resumen())
        self.guardado = guardado


class Operacion:
    """Una edición deshacible. Las subclases deben ser deterministas: aplicar la
    misma operación sobre el mismo documento tiene que dar el mismo resultado."""

    nombre = "Operación"

    def aplicar(self, doc: pymupdf.Document) -> None:
        raise NotImplementedError

    def paginas_afectadas(self) -> set[int]:
        """Páginas (índices del documento en ese momento) que esta operación modifica."""
        return set()

    def actualizar_mapa(self, mapa: dict[int, int]) -> dict[int, int]:
        """Recibe {página actual: página original} de las páginas aún sin editar y
        devuelve el mapa después de aplicar la operación. Por defecto saca las
        páginas afectadas; las operaciones que mueven páginas lo redefinen."""
        afectadas = self.paginas_afectadas()
        return {k: v for k, v in mapa.items() if k not in afectadas}

    def regiones_afectadas(self) -> dict[int, list[tuple]] | None:
        """Zonas (rects sin rotar) que la operación puede haber cambiado en cada página.
        Si las declara, la verificación de fidelidad compara el resto de esas páginas.
        None = la página entera puede cambiar."""
        return None

    def descripcion(self) -> str:
        return self.nombre


@dataclass
class Guardado:
    """Resultado de preparar un guardado, antes de copiarlo al destino."""

    ruta_temporal: Path
    incremental: bool
    bytes_originales_intactos: bool
    estructura_ok: bool
    fidelidad: ResultadoFidelidad
    avisos: list[str] = field(default_factory=list)

    @property
    def es_fiel(self) -> bool:
        return self.fidelidad.ok and self.estructura_ok

    def resumen(self) -> str:
        lineas = [self.fidelidad.resumen()]
        if self.incremental:
            if self.bytes_originales_intactos:
                lineas.append("Guardado incremental: el contenido original quedó intacto y solo se "
                              "agregaron los cambios.")
            else:
                lineas.append("¡Atención! El guardado incremental alteró bytes del original.")
        if not self.estructura_ok:
            lineas.append("¡Atención! La verificación de estructura del PDF guardado falló.")
        lineas.extend(self.avisos)
        return "\n".join(lineas)


Oyente = Callable[[], None]


class Documento:
    def __init__(self, ruta: str | Path, contrasena: str | None = None):
        self.ruta_original = Path(ruta).resolve()
        if not self.ruta_original.is_file():
            raise ErrorDocumento(f"No existe el archivo: {self.ruta_original}")
        self._contrasena = contrasena
        self._dir_trabajo = Path(tempfile.mkdtemp(prefix="pdfstudio_"))
        self._base = self._dir_trabajo / "original.pdf"
        self._historial: list[Operacion] = []
        self._posicion = 0
        self._posicion_guardada: int | None = 0
        self._contador_salidas = 0
        self._oyentes: list[Oyente] = []
        self.ultimo_guardado: Path | None = None
        try:
            shutil.copy2(self.ruta_original, self._base)
            self.doc = self._abrir_base()
        except BaseException:
            shutil.rmtree(self._dir_trabajo, ignore_errors=True)
            raise
        self.paginas_original = self.doc.page_count

    # ------------------------------------------------------------------ apertura
    def _abrir(self, ruta: Path) -> pymupdf.Document:
        try:
            doc = pymupdf.open(ruta)
        except Exception as e:  # PyMuPDF lanza varios tipos según el daño del archivo
            raise ErrorDocumento(f"No se pudo abrir el PDF: {e}") from e
        if not doc.is_pdf:
            doc.close()
            raise ErrorDocumento("El archivo no es un PDF.")
        if doc.needs_pass and not doc.authenticate(self._contrasena or ""):
            doc.close()
            raise ErrorContrasena("El PDF está protegido con contraseña.")
        return doc

    def _abrir_base(self) -> pymupdf.Document:
        return self._abrir(self._base)

    @property
    def ruta_base(self) -> Path:
        """Copia intacta del original tal como estaba al abrirlo."""
        return self._base

    @property
    def contrasena(self) -> str | None:
        return self._contrasena

    @property
    def tiene_contrasena(self) -> bool:
        return self._contrasena is not None

    # ------------------------------------------------------------------ oyentes
    def al_cambiar(self, funcion: Oyente) -> None:
        self._oyentes.append(funcion)

    def _avisar(self) -> None:
        for f in list(self._oyentes):
            f()

    # ------------------------------------------------------------------ historial
    def ejecutar(self, op: Operacion) -> None:
        del self._historial[self._posicion:]
        if self._posicion_guardada is not None and self._posicion_guardada > self._posicion:
            self._posicion_guardada = None  # el estado guardado ya no se puede alcanzar
        try:
            op.aplicar(self.doc)
        except Exception:
            # Descarta cambios a medio aplicar
            self._reconstruir()
            raise
        self._historial.append(op)
        self._posicion += 1
        self._avisar()

    @property
    def puede_deshacer(self) -> bool:
        return self._posicion > 0

    @property
    def puede_rehacer(self) -> bool:
        return self._posicion < len(self._historial)

    @property
    def texto_deshacer(self) -> str:
        return self._historial[self._posicion - 1].descripcion() if self.puede_deshacer else ""

    @property
    def texto_rehacer(self) -> str:
        return self._historial[self._posicion].descripcion() if self.puede_rehacer else ""

    def deshacer(self) -> None:
        if not self.puede_deshacer:
            return
        self._posicion -= 1
        self._reconstruir()
        self._avisar()

    def rehacer(self) -> None:
        if not self.puede_rehacer:
            return
        self._historial[self._posicion].aplicar(self.doc)
        self._posicion += 1
        self._avisar()

    def _reconstruir(self) -> None:
        self.doc.close()
        self.doc = self._abrir_base()
        for op in self._historial[:self._posicion]:
            op.aplicar(self.doc)

    @property
    def operaciones_activas(self) -> list[Operacion]:
        return self._historial[:self._posicion]

    @property
    def modificado(self) -> bool:
        return self._posicion != self._posicion_guardada

    def mapa_paginas(self) -> dict[int, int]:
        """{página actual: página original} de las páginas que no se editaron."""
        mapa = {i: i for i in range(self.paginas_original)}
        for op in self.operaciones_activas:
            mapa = op.actualizar_mapa(mapa)
        return mapa

    def paginas_editadas(self) -> set[int]:
        return set(range(self.doc.page_count)) - set(self.mapa_paginas())

    def regiones_editadas(self) -> dict[int, list[tuple]]:
        """{página: zonas editadas} para las páginas donde TODAS las operaciones declararon
        sus zonas. El resto de esas páginas debe quedar idéntico al original."""
        regiones: dict[int, list[tuple]] = {}
        completas: set[int] = set()
        for op in self.operaciones_activas:
            r = op.regiones_afectadas()
            for p in op.paginas_afectadas():
                if r is None or p not in r:
                    completas.add(p)
                else:
                    regiones.setdefault(p, []).extend(r[p])
        return {p: v for p, v in regiones.items() if p not in completas}

    # ------------------------------------------------------------------ guardado
    def ruta_sugerida(self) -> Path:
        return self.ruta_original.with_name(f"{self.ruta_original.stem}_editado.pdf")

    def preparar_guardado(self, progreso: Progreso | None = None) -> Guardado:
        """Arma el archivo final en la carpeta temporal y lo verifica.
        No toca ningún archivo del usuario."""
        self._contador_salidas += 1
        salida = self._dir_trabajo / f"salida_{self._contador_salidas}.pdf"
        shutil.copy2(self._base, salida)
        ops = self.operaciones_activas
        avisos: list[str] = []
        incremental = False
        if ops:
            doc = self._abrir(salida)
            try:
                for op in ops:
                    op.aplicar(doc)
                if doc.can_save_incrementally():
                    doc.save(salida, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                    incremental = True
                else:
                    completo = salida.with_suffix(".completo.pdf")
                    doc.save(completo, garbage=0, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                    avisos.append("El PDF original tenía errores internos y no admite guardado "
                                  "incremental: se reescribió completo (sin re-renderizar ni "
                                  "rasterizar nada).")
            finally:
                doc.close()
            if not incremental:
                os.replace(completo, salida)

        intactos = _empieza_con(salida, self._base) if (incremental or not ops) else False
        estructura_ok, error = _verificar_estructura(salida, self._contrasena)
        if error:
            avisos.append(f"Detalle de estructura: {error}")
        parciales = {p: (p, zonas) for p, zonas in self.regiones_editadas().items()
                     if p < self.paginas_original}
        fidelidad = comparar_documentos(self._base, salida, self.mapa_paginas(),
                                        contrasena=self._contrasena, progreso=progreso,
                                        parciales=parciales)
        return Guardado(salida, incremental, intactos, estructura_ok, fidelidad, avisos)

    def confirmar_guardado(self, guardado: Guardado, destino: str | Path,
                           sobrescribir_original: bool = False) -> Path:
        """Copia el archivo preparado al destino elegido por el usuario."""
        destino = Path(destino).resolve()
        if destino == self.ruta_original and not sobrescribir_original:
            raise ErrorDocumento("Para reemplazar el archivo original usá «Sobrescribir original».")
        temporal = destino.with_name(destino.name + ".pdfstudio-tmp")
        try:
            shutil.copy2(guardado.ruta_temporal, temporal)
            os.replace(temporal, destino)
        except PermissionError as e:
            temporal.unlink(missing_ok=True)
            raise ErrorDocumento(f"No se pudo escribir {destino}. ¿Está abierto en otro programa?") from e
        except OSError as e:
            temporal.unlink(missing_ok=True)
            raise ErrorDocumento(f"No se pudo escribir {destino}: {e}") from e
        self._posicion_guardada = self._posicion
        self.ultimo_guardado = destino
        self._avisar()
        return destino

    def guardar(self, destino: str | Path, sobrescribir_original: bool = False,
                forzar: bool = False, progreso: Progreso | None = None) -> Guardado:
        """Prepara, verifica y guarda. Si la verificación falla y no se fuerza,
        lanza ErrorFidelidad y no escribe nada."""
        guardado = self.preparar_guardado(progreso)
        if not guardado.es_fiel and not forzar:
            raise ErrorFidelidad(guardado)
        self.confirmar_guardado(guardado, destino, sobrescribir_original)
        return guardado

    # ------------------------------------------------------------------ cierre
    def cerrar(self) -> None:
        try:
            self.doc.close()
        finally:
            shutil.rmtree(self._dir_trabajo, ignore_errors=True)


def _empieza_con(ruta: Path, prefijo: Path) -> bool:
    """True si el archivo `ruta` comienza exactamente con los bytes de `prefijo`."""
    tam = prefijo.stat().st_size
    if ruta.stat().st_size < tam:
        return False
    with open(ruta, "rb") as a, open(prefijo, "rb") as b:
        while True:
            bloque = b.read(1 << 20)
            if not bloque:
                return True
            if a.read(len(bloque)) != bloque:
                return False


def _verificar_estructura(ruta: Path, contrasena: str | None) -> tuple[bool, str]:
    """Abre el PDF con pikepdf (un parser distinto de MuPDF) para detectar daños."""
    try:
        import pikepdf
    except ImportError:
        return True, "pikepdf no está instalado: se omitió la verificación de estructura."
    try:
        with pikepdf.open(ruta, password=contrasena or "") as pdf:
            _ = len(pdf.pages)
        return True, ""
    except Exception as e:
        return False, str(e)
