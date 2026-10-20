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

    # Redacción real: el contenido borrado no puede quedar en una versión anterior del archivo,
    # así que el guardado reescribe el PDF completo (sin objetos huérfanos) en vez de agregar
    requiere_guardado_completo = False

    def paginas_afectadas(self) -> set[int]:
        """Páginas cuyo contenido cambia, con los índices que tienen DESPUÉS de aplicar la
        operación (para una inserción: las páginas nuevas)."""
        return set()

    def reubicar(self, total: int) -> tuple[dict[int, int], int] | None:
        """Si la operación mueve, borra o inserta páginas: ({índice antes: índice después}
        de las páginas que siguen estando, cantidad de páginas después). None si la
        estructura de páginas no cambia. `total` es la cantidad de páginas antes."""
        return None

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
        validar_archivo(self.ruta_original)
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
            doc = pymupdf.open(ruta, filetype="pdf")
        except Exception as e:  # PyMuPDF lanza varios tipos según el daño del archivo
            raise ErrorDocumento(f"«{self.ruta_original.name}» está dañado y no se puede abrir "
                                 f"({type(e).__name__}).") from e
        if not doc.is_pdf:
            doc.close()
            raise ErrorDocumento(f"«{self.ruta_original.name}» no es un PDF.")
        if doc.needs_pass and not doc.authenticate(self._contrasena or ""):
            doc.close()
            raise ErrorContrasena("El PDF está protegido con contraseña.")
        if doc.page_count == 0:
            doc.close()
            raise ErrorDocumento(f"«{self.ruta_original.name}» no tiene páginas (está dañado o incompleto).")
        return doc

    def _abrir_base(self) -> pymupdf.Document:
        return self._abrir(self._base)

    @property
    def ruta_base(self) -> Path:
        """Copia intacta del original tal como estaba al abrirlo."""
        return self._base

    @property
    def carpeta_trabajo(self) -> Path:
        """Carpeta temporal privada del documento (se borra al cerrarlo)."""
        return self._dir_trabajo

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

    def _seguimiento(self) -> tuple[dict[int, int | None], dict[int, list[tuple] | None]]:
        """Sigue cada página a través de las operaciones. Devuelve:
        - origen: {página actual: página original} (None si es una página nueva);
        - zonas: {página actual: zonas editadas} de las páginas tocadas (None = entera)."""
        origen: dict[int, int | None] = {i: i for i in range(self.paginas_original)}
        zonas: dict[int, list[tuple] | None] = {}
        total = self.paginas_original
        for op in self.operaciones_activas:
            reubicacion = op.reubicar(total)
            if reubicacion is not None:
                movidas, total = reubicacion
                origen = {movidas[k]: v for k, v in origen.items() if k in movidas}
                zonas = {movidas[k]: v for k, v in zonas.items() if k in movidas}
                for i in range(total):
                    origen.setdefault(i, None)      # páginas insertadas
            regiones = op.regiones_afectadas()
            for p in op.paginas_afectadas():
                if origen.get(p) is None or regiones is None or p not in regiones:
                    zonas[p] = None
                elif p not in zonas:
                    zonas[p] = list(regiones[p])
                elif zonas[p] is not None:
                    zonas[p].extend(regiones[p])
        return origen, zonas

    def mapa_paginas(self) -> dict[int, int]:
        """{página actual: página original} de las páginas que no se editaron."""
        origen, zonas = self._seguimiento()
        return {p: o for p, o in origen.items() if o is not None and p not in zonas}

    def paginas_editadas(self) -> set[int]:
        return set(range(self.doc.page_count)) - set(self.mapa_paginas())

    def regiones_editadas(self) -> dict[int, tuple[int, list[tuple]]]:
        """{página actual: (página original, zonas editadas)} de las páginas editadas donde
        TODAS las operaciones declararon sus zonas: el resto de la página debe quedar
        idéntico a la página original."""
        origen, zonas = self._seguimiento()
        return {p: (origen[p], z) for p, z in zonas.items() if z is not None and origen.get(p) is not None}

    def paginas_nuevas(self) -> list[int]:
        """Páginas que no vienen del original (insertadas en blanco o desde otro PDF)."""
        origen, _ = self._seguimiento()
        return sorted(p for p, o in origen.items() if o is None)

    @property
    def requiere_guardado_completo(self) -> bool:
        return any(op.requiere_guardado_completo for op in self.operaciones_activas)

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
                if self.requiere_guardado_completo:
                    completo = salida.with_suffix(".completo.pdf")
                    # garbage=4: se descartan los objetos que ya nadie usa (el contenido eliminado)
                    doc.save(completo, garbage=4, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                    avisos.append("Por la redacción, el archivo se reescribió completo: así el contenido "
                                  "eliminado no queda guardado en ninguna parte del PDF (sin re-renderizar "
                                  "ni rasterizar nada).")
                elif doc.can_save_incrementally():
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
        parciales = self.regiones_editadas()
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


def validar_archivo(ruta: Path) -> None:
    """Chequeos previos con mensajes claros: que exista, que no esté vacío y que sea un PDF."""
    if not ruta.exists():
        raise ErrorDocumento(f"No existe el archivo: {ruta}")
    if not ruta.is_file():
        raise ErrorDocumento(f"«{ruta.name}» no es un archivo.")
    try:
        tamano = ruta.stat().st_size
        with open(ruta, "rb") as f:
            inicio = f.read(1024)
    except OSError as e:
        raise ErrorDocumento(f"No se pudo leer «{ruta.name}»: {e.strerror or e}") from e
    if tamano == 0:
        raise ErrorDocumento(f"«{ruta.name}» está vacío (0 bytes). Puede que la descarga o la copia "
                             "no haya terminado: probá bajarlo de nuevo.")
    if b"%PDF-" not in inicio:
        raise ErrorDocumento(f"«{ruta.name}» no es un PDF (su contenido no tiene el formato de un PDF).")


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
