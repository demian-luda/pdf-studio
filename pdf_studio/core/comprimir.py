"""Compresión de PDFs con niveles y verificación del resultado.

- Bajo (sin pérdida): limpia objetos sin uso, comprime los que no lo estaban y agrupa
  objetos (garbage=4, deflate, object streams). El aspecto es idéntico: se exige
  diferencia de píxeles = 0 en todas las páginas.
- Medio y Alto: además recomprimen las imágenes (JPEG y, si tienen mucha resolución, menos
  dpi). Solo se reemplazan los objetos de imagen, nunca el contenido de las páginas: fuera de
  las imágenes la página tiene que quedar idéntica; dentro, cambian a propósito.
- Máxima (Ghostscript, opcional): Ghostscript reescribe todo el archivo. Puede cambiar el
  aspecto: se informa cuánto, y el usuario decide si guarda el resultado.

La compresión no forma parte del historial de edición: genera una copia nueva a partir
del estado actual del documento y nunca toca el original.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pymupdf

from pdf_studio.core.documento import ErrorDocumento
from pdf_studio.core.fidelidad import ResultadoFidelidad, comparar_documentos

DPI_VERIFICACION = 100


class NivelCompresion(str, Enum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"
    MAXIMO = "maximo"

    @property
    def texto(self) -> str:
        return {"bajo": "Baja (sin pérdida)", "medio": "Media", "alto": "Alta",
                "maximo": "Máxima (Ghostscript)"}[self.value]

    @property
    def descripcion(self) -> str:
        return {
            "bajo": "Limpia y comprime la estructura del archivo. Se ve exactamente igual.",
            "medio": "Además, recomprime las imágenes (JPEG calidad 80; las de más de 225 dpi, a 150 dpi). "
                     "El texto y los dibujos no cambian.",
            "alto": "Además, recomprime las imágenes (JPEG calidad 60; las de más de 150 dpi, a 110 dpi). "
                    "El texto y los dibujos no cambian; las imágenes pierden detalle.",
            "maximo": "Ghostscript reescribe todo el archivo. Es la que más reduce, pero puede cambiar "
                      "el aspecto (fuentes, colores): se te muestra la diferencia antes de guardar.",
        }[self.value]

    @property
    def con_perdida(self) -> bool:
        return self != NivelCompresion.BAJO


# (umbral: se tocan las imágenes con más dpi que esto, dpi final, calidad JPEG)
PERFILES = {
    NivelCompresion.MEDIO: (225, 150, 80),
    NivelCompresion.ALTO: (150, 110, 60),
}


@dataclass
class ResultadoCompresion:
    nivel: NivelCompresion
    tamano_antes: int
    tamano_despues: int
    fidelidad: ResultadoFidelidad
    imagenes_excluidas: bool        # la verificación no comparó el interior de las imágenes

    @property
    def reduccion(self) -> float:
        """Fracción ahorrada (0.4 = 40 % más chico; negativo si creció)."""
        return 1 - self.tamano_despues / self.tamano_antes if self.tamano_antes else 0.0

    @property
    def reduce(self) -> bool:
        return self.tamano_despues < self.tamano_antes

    def resumen(self) -> str:
        lineas = [f"Antes: {formato_tamano(self.tamano_antes)} → después: {formato_tamano(self.tamano_despues)} "
                  f"({-self.reduccion:+.0%})." if self.reduce else
                  f"No se pudo reducir: el resultado pesa {formato_tamano(self.tamano_despues)} y el original "
                  f"{formato_tamano(self.tamano_antes)}. Conviene quedarse con el original."]
        if self.fidelidad.ok:
            if self.imagenes_excluidas:
                lineas.append("Verificado: fuera de las imágenes, todas las páginas son idénticas al original "
                              "(las imágenes se recomprimieron a propósito).")
            else:
                lineas.append(f"Verificado: las {len(self.fidelidad.comparadas)} páginas son idénticas al "
                              f"original (diferencia de píxeles = 0 a {self.fidelidad.dpi} dpi).")
        else:
            distintas = sorted({d.pagina + 1 for d in self.fidelidad.diferencias})
            peor = max((d.porcentaje for d in self.fidelidad.diferencias), default=0.0)
            lineas.append(f"¡Atención! El aspecto cambió en {len(distintas)} página(s) "
                          f"({', '.join(map(str, distintas[:12]))}{'…' if len(distintas) > 12 else ''}); "
                          f"como máximo, {_porcentaje(peor)} de los píxeles de una página.")
        return "\n".join(lineas)

    def a_dict(self) -> dict:
        f = self.fidelidad
        return {"nivel": self.nivel.value, "antes": self.tamano_antes, "despues": self.tamano_despues,
                "imagenes_excluidas": self.imagenes_excluidas,
                "fidelidad": {"dpi": f.dpi, "comparadas": f.comparadas, "parciales": f.parciales,
                              "enteras": f.enteras, "completa": f.completa,
                              "diferencias": [(d.pagina, d.pagina_original, d.pixeles_distintos,
                                               d.pixeles_totales, d.motivo) for d in f.diferencias]}}

    @classmethod
    def desde_dict(cls, d: dict) -> "ResultadoCompresion":
        from pdf_studio.core.fidelidad import DiferenciaPagina
        f = d["fidelidad"]
        fid = ResultadoFidelidad(dpi=f["dpi"], comparadas=f["comparadas"], parciales=f["parciales"],
                                 enteras=f["enteras"], completa=f["completa"],
                                 diferencias=[DiferenciaPagina(*x) for x in f["diferencias"]])
        return cls(NivelCompresion(d["nivel"]), d["antes"], d["despues"], fid, d["imagenes_excluidas"])


def _porcentaje(valor: float) -> str:
    return "menos del 0,1 %" if valor < 0.1 else "el " + f"{valor:.1f} %".replace(".", ",")


def formato_tamano(n: int) -> str:
    for unidad in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n} bytes" if unidad == "bytes" else f"{n:.1f} {unidad}".replace(".", ",")
        n /= 1024
    return str(n)


def _abrir(ruta: Path, contrasena: str | None) -> pymupdf.Document:
    doc = pymupdf.open(ruta)
    if doc.needs_pass and not doc.authenticate(contrasena or ""):
        doc.close()
        raise ErrorDocumento("Contraseña incorrecta.")
    return doc


def comprimir(entrada: Path, salida: Path, nivel: NivelCompresion, contrasena: str | None = None,
              ghostscript: Path | None = None) -> None:
    """Escribe en `salida` la versión comprimida de `entrada` (nunca modifica `entrada`)."""
    entrada, salida = Path(entrada), Path(salida)
    if entrada.resolve() == salida.resolve():
        raise ErrorDocumento("La compresión no puede escribir sobre el archivo de entrada.")
    if nivel == NivelCompresion.MAXIMO:
        _comprimir_ghostscript(entrada, salida, contrasena, ghostscript)
        return
    doc = _abrir(entrada, contrasena)
    try:
        if nivel in PERFILES:
            recomprimir_imagenes(doc, *PERFILES[nivel])
        doc.save(salida, garbage=4, deflate=True, deflate_images=True, deflate_fonts=True, use_objstms=1,
                 encryption=pymupdf.PDF_ENCRYPT_KEEP)
    finally:
        doc.close()


def _dpi_efectivo(doc: pymupdf.Document) -> dict[int, float]:
    """{xref de imagen: mayor resolución con que se dibuja en alguna página}."""
    dpi: dict[int, float] = {}
    for pag in doc:
        try:
            infos = pag.get_image_info(xrefs=True)
        except Exception:
            continue
        for info in infos:
            xref, caja = info.get("xref"), pymupdf.Rect(info["bbox"])
            if not xref or caja.is_empty or not info.get("width"):
                continue
            lado = max(caja.width, caja.height) / 72
            pixeles = max(info["width"], info["height"])
            dpi[xref] = max(dpi.get(xref, 0.0), pixeles / lado if lado else 0.0)
    return dpi


def _espacio_compatible(doc: pymupdf.Document, xref: int) -> int | None:
    """Cantidad de componentes (1 o 3) si el espacio de color se puede conservar con JPEG
    (DeviceGray, DeviceRGB o ICCBased de 1 o 3 componentes). None para el resto."""
    import re

    tipo, valor = doc.xref_get_key(xref, "ColorSpace")
    if tipo == "name":
        return {"/DeviceGray": 1, "/DeviceRGB": 3}.get(valor)
    texto = doc.xref_object(int(valor.split()[0]), compressed=True) if tipo == "xref" else valor
    m = re.search(r"/ICCBased\s+(\d+)\s+0\s+R", texto)
    if m:
        n = doc.xref_get_key(int(m.group(1)), "N")[1]
        return int(n) if n in ("1", "3") else None
    return None


def recomprimir_imagenes(doc: pymupdf.Document, umbral: float, dpi: float, calidad: int) -> int:
    """Recomprime en JPEG (calidad `calidad`) las imágenes, bajando a `dpi` las que se dibujan
    con más de `umbral` dpi. Solo se reemplaza el objeto de cada imagen: el contenido de las
    páginas no se reescribe, así que todo lo que no es imagen queda idéntico. Se saltean las
    imágenes en blanco y negro, con máscaras, CMYK u otros espacios de color (cambiarían de
    aspecto) y las que no achican al menos un 10 %. Devuelve cuántas se recomprimieron."""
    import io

    from PIL import Image

    hechas = 0
    for xref, resolucion in _dpi_efectivo(doc).items():
        try:
            if doc.xref_get_key(xref, "ImageMask")[1] == "true":
                continue
            if doc.xref_get_key(xref, "Mask")[0] != "null" or doc.xref_get_key(xref, "Decode")[0] != "null":
                continue
            if doc.xref_get_key(xref, "BitsPerComponent")[1] != "8":
                continue
            componentes = _espacio_compatible(doc, xref)
            if componentes is None:
                continue
            pix = pymupdf.Pixmap(doc, xref)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            if pix.n != componentes:
                continue
            modo = "L" if componentes == 1 else "RGB"
            img = Image.frombytes(modo, (pix.width, pix.height), pix.samples)
            nuevo = (pix.width, pix.height)
            if resolucion > umbral:
                escala = dpi / resolucion
                nuevo = (max(1, round(pix.width * escala)), max(1, round(pix.height * escala)))
                img = img.resize(nuevo, Image.Resampling.LANCZOS)
            salida = io.BytesIO()
            img.save(salida, "JPEG", quality=calidad, optimize=True)
            datos = salida.getvalue()
            if len(datos) >= 0.9 * len(doc.xref_stream_raw(xref)):
                continue
            doc.update_stream(xref, datos, compress=False)
            doc.xref_set_key(xref, "Filter", "/DCTDecode")
            doc.xref_set_key(xref, "DecodeParms", "null")
            doc.xref_set_key(xref, "Width", str(nuevo[0]))
            doc.xref_set_key(xref, "Height", str(nuevo[1]))
            hechas += 1
        except Exception:
            continue        # una imagen rara se deja como estaba
    return hechas


def _comprimir_ghostscript(entrada: Path, salida: Path, contrasena: str | None, exe: Path | None) -> None:
    if exe is None:
        from pdf_studio.dependencias.chequeo import buscar_ghostscript
        exe = buscar_ghostscript()
    if exe is None:
        raise ErrorDocumento("La compresión máxima necesita Ghostscript, que no está instalado. "
                             "Instalalo desde el panel Faltantes o elegí otro nivel.")
    argumentos = [str(exe), "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.6", "-dPDFSETTINGS=/ebook",
                  "-dNOPAUSE", "-dBATCH", "-dQUIET", "-dSAFER", f"-sOutputFile={salida}"]
    if contrasena:
        argumentos.append(f"-sPDFPassword={contrasena}")
    argumentos.append(str(entrada))
    try:
        r = subprocess.run(argumentos, capture_output=True, text=True, errors="replace", timeout=1800,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise ErrorDocumento("Ghostscript tardó demasiado y se canceló.") from e
    if r.returncode != 0 or not salida.is_file() or salida.stat().st_size == 0:
        ultima = next((l for l in reversed((r.stderr or r.stdout).splitlines()) if l.strip()), "")
        raise ErrorDocumento(f"Ghostscript no pudo comprimir el archivo (código {r.returncode}). {ultima[:200]}")


def zonas_de_imagenes(pag: pymupdf.Page) -> list[tuple]:
    """Cajas donde se dibujan imágenes en la página (get_image_info ya las da sin rotar)."""
    try:
        infos = pag.get_image_info()
    except Exception:
        return [tuple(pag.rect * pag.derotation_matrix)]      # ante la duda, toda la página
    return [tuple(pymupdf.Rect(i["bbox"])) for i in infos if not pymupdf.Rect(i["bbox"]).is_empty]


def verificar(original: Path, comprimido: Path, nivel: NivelCompresion,
              contrasena: str | None = None, dpi: int = DPI_VERIFICACION) -> ResultadoCompresion:
    """Compara cada página del comprimido contra el original."""
    with _abrir(original, contrasena) as d:
        total = d.page_count
        excluir = nivel in PERFILES
        zonas = {i: zonas_de_imagenes(d[i]) for i in range(total)} if excluir else {}
    if excluir:
        # Las páginas sin imágenes se comparan enteras; las otras, fuera de sus imágenes
        mapa = {i: i for i in range(total) if not zonas[i]}
        parciales = {i: (i, z) for i, z in zonas.items() if z}
    else:
        mapa, parciales = {i: i for i in range(total)}, None
    fidelidad = comparar_documentos(original, comprimido, mapa, dpi=dpi, contrasena=contrasena,
                                    parciales=parciales)
    return ResultadoCompresion(nivel, Path(original).stat().st_size, Path(comprimido).stat().st_size,
                               fidelidad, excluir and any(zonas.values()))


def comprimir_y_verificar(entrada: Path, salida: Path, nivel: NivelCompresion,
                          contrasena: str | None = None) -> ResultadoCompresion:
    comprimir(entrada, salida, nivel, contrasena)
    try:
        with _abrir(salida, contrasena) as d:
            if d.page_count != _contar(entrada, contrasena):
                raise ErrorDocumento("La compresión cambió la cantidad de páginas: el resultado se descartó.")
    except ErrorDocumento:
        raise
    except Exception as e:
        raise ErrorDocumento(f"El archivo comprimido quedó dañado ({type(e).__name__}): se descartó.") from e
    return verificar(entrada, salida, nivel, contrasena)


def _contar(ruta: Path, contrasena: str | None) -> int:
    with _abrir(ruta, contrasena) as d:
        return d.page_count
