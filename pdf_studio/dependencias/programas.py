"""Programas incluidos con PDF Studio: Tesseract (con español e inglés) y Ghostscript.

Van con versión fija, se descargan de su fuente oficial por HTTPS y se verifican con
SHA-256 antes de usarlos. La app usa SIEMPRE estos, aunque haya otros instalados en el
sistema: nada se instala en Windows ni pide permisos de administrador.

Dónde se buscan, en este orden (nunca en el PATH ni en Archivos de programa):
1. Versión empaquetada: al lado de PDFStudio.exe (carpetas «tesseract» y «ghostscript»).
2. Desde el código: <repo>/programas (lo arma `python -m pdf_studio.dependencias.programas`).
3. Lo que bajó el botón «Instalar» del panel Faltantes: %LOCALAPPDATA%\\PDFStudio\\programas.

Cómo se arman (igual en el workflow de release y en el botón «Instalar»): se baja el
instalador oficial, se verifica, se abre con 7-Zip SIN ejecutarlo y se copia solo lo
necesario (el programa y las DLL que realmente importa). 7-Zip se usa el del equipo si está;
si no, se baja el oficial (también verificado) a una carpeta temporal.

Ghostscript solo hace falta para la compresión máxima: el OCR no lo usa, porque ocrmypdf
corre con `--output-type pdf` (sin PDF/A) y `--rasterizer pypdfium`.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pdf_studio import config
from pdf_studio.dependencias.instalador import (ErrorInstalacion, Instalacion, Progreso, _sin_progreso,
                                               descargar)

SIN_VENTANA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class Archivo:
    """Una descarga con su huella fija."""
    url: str
    sha256: str
    tamano: int

    @property
    def nombre(self) -> str:
        return self.url.rsplit("/", 1)[-1]


# ---------------------------------------------------------------------- versiones fijas
# Para actualizar: cambiar la versión, la URL, el SHA-256 y el tamaño (GitHub muestra el
# «digest» de cada archivo de un release), y correr la suite en GitHub.
TESSERACT_VERSION = "5.5.3"
INSTALADOR_TESSERACT = Archivo(
    "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/tesseract-ocr-w64-setup-5.5.3.20260724.exe",
    "bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4", 26573224)
# Modelos «best» (los más precisos), tag 4.1.0 del repositorio oficial
TESSDATA_VERSION = "tessdata_best 4.1.0"
IDIOMAS = {
    "spa": Archivo("https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/spa.traineddata",
                   "e2c1ffdad8b30f26c45d4017a9183d3a7f9aa69e59918be4f88b126fac99ab2c", 13570187),
    "eng": Archivo("https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/eng.traineddata",
                   "8280aed0782fe27257a68ea10fe7ef324ca0f8d85bd2fd145d1c2b560bcb66ba", 15400601),
}
LICENCIA_TESSDATA = Archivo("https://github.com/tesseract-ocr/tessdata_best/raw/4.1.0/LICENSE",
                            "a6cba85bc92e0cff7a450b1d873c0eaa2e9fc96bf472df0247a26bec77bf3ff9", 10173)

GHOSTSCRIPT_VERSION = "10.08.0"
INSTALADOR_GHOSTSCRIPT = Archivo(
    "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/gs10080/gs10080w64.exe",
    "52a91b8bf09298788d7a57b9206127026c23eacd75405f0a131e26dc381dce50", 65093120)

# 7-Zip oficial (repositorio de su autor), solo para abrir los instaladores
SIETE_ZIP_R = Archivo("https://github.com/ip7z/7zip/releases/download/26.06/7zr.exe",
                      "e80db2dad715a6c5adabe292d31ed54e11f4e9b99ca349827b50573b175891bc", 600064)
SIETE_ZIP = Archivo("https://github.com/ip7z/7zip/releases/download/26.06/7z2606-x64.exe",
                    "9b697f65ecc2d9b0152f5ee96d72dde522a7827690349d9307935dacf40f42ec", 1659704)

# Ghostscript usa el runtime de Visual C++, que un Windows 10 limpio puede no tener: se copia
# el que ya trae PDF Studio (viene con PySide6)
RUNTIME_MSVC = ("msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll")

# DLL de Windows: siempre están (para la verificación de dependencias)
DLL_DEL_SISTEMA = {
    "advapi32.dll", "bcrypt.dll", "comctl32.dll", "comdlg32.dll", "crypt32.dll", "gdi32.dll", "imm32.dll",
    "iphlpapi.dll", "kernel32.dll", "msvcrt.dll", "ntdll.dll", "ole32.dll", "oleaut32.dll", "rpcrt4.dll",
    "secur32.dll", "setupapi.dll", "shell32.dll", "shlwapi.dll", "ucrtbase.dll", "user32.dll", "userenv.dll",
    "version.dll", "winmm.dll", "winspool.drv", "wldap32.dll", "ws2_32.dll", "dnsapi.dll", "normaliz.dll",
}


@dataclass(frozen=True)
class Programa:
    clave: str                    # nombre de la carpeta
    titulo: str
    version: str
    licencia: str
    para_que: str
    ejecutable: str               # relativo a su carpeta
    necesarios: tuple[str, ...]   # lo que tiene que estar para que funcione

    def descargas(self) -> list[Archivo]:
        if self.clave == "tesseract":
            return [INSTALADOR_TESSERACT, *IDIOMAS.values(), LICENCIA_TESSDATA]
        return [INSTALADOR_GHOSTSCRIPT]

    @property
    def origen(self) -> str:
        if self.clave == "tesseract":
            return ("github.com/tesseract-ocr (instalador oficial de Tesseract y modelos tessdata_best), "
                    "verificado con SHA-256")
        return "github.com/ArtifexSoftware/ghostpdl-downloads (instalador oficial), verificado con SHA-256"


TESSERACT = Programa("tesseract", "Tesseract OCR (español e inglés)", f"{TESSERACT_VERSION} + {TESSDATA_VERSION}",
                     "Apache 2.0", "Motor de OCR: reconoce el texto de PDFs escaneados y fotos.",
                     "tesseract.exe", ("tesseract.exe", "tessdata/spa.traineddata", "tessdata/eng.traineddata",
                                       "tessdata/configs/hocr", "tessdata/configs/txt"))
GHOSTSCRIPT = Programa("ghostscript", "Ghostscript", GHOSTSCRIPT_VERSION, "AGPL-3.0",
                       "Compresión máxima de PDFs (sin él se usan los otros niveles).",
                       "gswin64c.exe", ("gswin64c.exe", "gsdll64.dll"))
PROGRAMAS = (TESSERACT, GHOSTSCRIPT)


# ---------------------------------------------------------------------- dónde están
def carpeta_del_repo() -> Path:
    return config.DIR_PAQUETE.parent / "programas"


def carpetas_base() -> list[Path]:
    """Dónde se buscan los programas, de mayor a menor prioridad."""
    if getattr(sys, "frozen", False):
        primera = Path(sys.executable).resolve().parent
    else:
        primera = carpeta_del_repo()
    return [primera, config.DIR_PROGRAMAS]


def esta_completo(carpeta: Path, programa: Programa) -> bool:
    return all((carpeta / n).is_file() for n in programa.necesarios)


def carpeta(programa: Programa) -> Path | None:
    for base in carpetas_base():
        if esta_completo(base / programa.clave, programa):
            return base / programa.clave
    return None


def ejecutable(programa: Programa) -> Path | None:
    c = carpeta(programa)
    return c / programa.ejecutable if c is not None else None


# ---------------------------------------------------------------------- dependencias de un .exe/.dll
def importaciones_pe(ruta: Path) -> list[str]:
    """Nombres de las DLL que importa un ejecutable de Windows (normales y diferidas)."""
    datos = ruta.read_bytes()
    pe = struct.unpack_from("<I", datos, 0x3C)[0]
    if datos[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{ruta.name} no es un ejecutable de Windows")
    secciones_n, = struct.unpack_from("<H", datos, pe + 6)
    opcional_tam, = struct.unpack_from("<H", datos, pe + 20)
    opcional = pe + 24
    magia, = struct.unpack_from("<H", datos, opcional)
    directorios = opcional + (112 if magia == 0x20B else 96)
    secciones = []
    for i in range(secciones_n):
        tam_virtual, dir_virtual, tam_crudo, ptr_crudo = struct.unpack_from("<IIII", datos,
                                                                            opcional + opcional_tam + 40 * i + 8)
        secciones.append((dir_virtual, max(tam_virtual, tam_crudo), ptr_crudo))

    def desplazamiento(rva: int) -> int:
        for va, tam, ptr in secciones:
            if va <= rva < va + tam:
                return rva - va + ptr
        raise ValueError("dirección fuera de las secciones")

    def cadena(rva: int) -> str:
        o = desplazamiento(rva)
        return datos[o:datos.index(b"\0", o)].decode("ascii", "replace")

    nombres = []
    for indice, tam_desc, campo in ((1, 20, 3), (13, 32, 1)):      # importaciones, importaciones diferidas
        rva, = struct.unpack_from("<I", datos, directorios + 8 * indice)
        if not rva:
            continue
        o = desplazamiento(rva)
        while True:
            campos = struct.unpack_from(f"<{tam_desc // 4}I", datos, o)
            if not any(campos):
                break
            nombres.append(cadena(campos[campo]))
            o += tam_desc
    return nombres


def _es_del_sistema(dll: str) -> bool:
    dll = dll.lower()
    return dll in DLL_DEL_SISTEMA or dll.startswith(("api-ms-win-", "ext-ms-win-"))


def dependencias(carpeta: Path, inicio: list[str]) -> tuple[set[str], set[str]]:
    """(archivos de la carpeta que hacen falta, DLL que no están ni en la carpeta ni en Windows)."""
    presentes = {p.name.lower(): p.name for p in carpeta.iterdir() if p.is_file()}
    usados: set[str] = set()
    faltan: set[str] = set()
    pendientes = [n.lower() for n in inicio]
    while pendientes:
        nombre = pendientes.pop()
        if nombre in usados:
            continue
        usados.add(nombre)
        for dll in importaciones_pe(carpeta / presentes[nombre]):
            dll = dll.lower()
            if dll in presentes:
                pendientes.append(dll)
            elif not _es_del_sistema(dll):
                faltan.add(dll)
    return {presentes[u] for u in usados}, faltan


# ---------------------------------------------------------------------- descargas verificadas
def sha256(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def bajar_verificado(archivo: Archivo, destino: Path, progreso: Progreso = _sin_progreso,
                     cache: Path | None = None) -> Path:
    """Descarga por HTTPS y verifica el SHA-256. Si la caché ya lo tiene (y coincide), lo reusa."""
    if not archivo.url.startswith("https://"):
        raise ErrorInstalacion(f"Descarga sin HTTPS: {archivo.url}")
    if cache is not None and (cache / archivo.nombre).is_file() and sha256(cache / archivo.nombre) == archivo.sha256:
        shutil.copy2(cache / archivo.nombre, destino)
        return destino
    descargar(archivo.url, destino, progreso, f"Descargando {archivo.nombre}")
    obtenido = sha256(destino)
    if obtenido != archivo.sha256:
        destino.unlink(missing_ok=True)
        raise ErrorInstalacion(f"El archivo descargado no es el esperado ({archivo.nombre}): su SHA-256 es "
                               f"{obtenido} y debía ser {archivo.sha256}. No se usó.")
    if cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destino, cache / archivo.nombre)
    return destino


def _correr(argumentos: list[str], que: str) -> None:
    r = subprocess.run(argumentos, capture_output=True, text=True, errors="replace", creationflags=SIN_VENTANA)
    if r.returncode != 0:
        raise ErrorInstalacion(f"No se pudo {que} (código {r.returncode}).\n{(r.stdout + r.stderr)[-600:]}")


def siete_zip_del_equipo() -> Path | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramW6432", ""), os.environ.get("ProgramFiles(x86)", "")):
        if base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            return Path(base) / "7-Zip" / "7z.exe"
    encontrado = shutil.which("7z")
    return Path(encontrado) if encontrado else None


def siete_zip(tmp: Path, progreso: Progreso = _sin_progreso, cache: Path | None = None,
              propio: bool = False) -> Path:
    """7z.exe para abrir los instaladores: el del equipo o, si no hay (o propio=True), el oficial
    bajado a `tmp` (7zr.exe, que solo abre .7z, extrae 7z.exe del instalador de 7-Zip)."""
    if not propio:
        encontrado = siete_zip_del_equipo()
        if encontrado is not None:
            return encontrado
    carpeta_7z = tmp / "7zip"
    carpeta_7z.mkdir(parents=True, exist_ok=True)
    r = bajar_verificado(SIETE_ZIP_R, carpeta_7z / SIETE_ZIP_R.nombre, progreso, cache)
    inst = bajar_verificado(SIETE_ZIP, carpeta_7z / SIETE_ZIP.nombre, progreso, cache)
    progreso(0, 0, "Preparando 7-Zip…")
    _correr([str(r), "x", "-y", f"-o{carpeta_7z / 'completo'}", str(inst), "7z.exe", "7z.dll"], "preparar 7-Zip")
    return carpeta_7z / "completo" / "7z.exe"


def _extraer(sz: Path, instalador: Path, destino: Path) -> None:
    """Abre el instalador (NSIS) como un archivo comprimido: no se ejecuta."""
    _correr([str(sz), "x", "-y", f"-o{destino}", str(instalador)], f"abrir {instalador.name}")


# ---------------------------------------------------------------------- armado
def _runtime_msvc() -> dict[str, Path]:
    """Las DLL del runtime de Visual C++ que trae PySide6 (también en la versión empaquetada)."""
    import importlib.util
    encontradas: dict[str, Path] = {}
    carpetas = []
    for paquete in ("PySide6", "shiboken6"):
        spec = importlib.util.find_spec(paquete)
        if spec is not None and spec.submodule_search_locations:
            carpetas += [Path(p) for p in spec.submodule_search_locations]
    carpetas += [Path(sys.base_prefix), Path(sys.executable).parent]
    for nombre in RUNTIME_MSVC:
        for c in carpetas:
            if (c / nombre).is_file():
                encontradas[nombre] = c / nombre
                break
    return encontradas


def _armar_tesseract(extraido: Path, destino: Path, progreso: Progreso, cache: Path | None) -> None:
    usados, faltan = dependencias(extraido, ["tesseract.exe"])
    if faltan:
        raise ErrorInstalacion("Al Tesseract descargado le faltan bibliotecas: " + ", ".join(sorted(faltan)))
    for nombre in sorted(usados):
        shutil.copy2(extraido / nombre, destino / nombre)
    tessdata = destino / "tessdata"
    tessdata.mkdir()
    for nombre in ("configs", "tessconfigs"):
        if (extraido / "tessdata" / nombre).is_dir():
            shutil.copytree(extraido / "tessdata" / nombre, tessdata / nombre)
    if (extraido / "tessdata" / "pdf.ttf").is_file():
        shutil.copy2(extraido / "tessdata" / "pdf.ttf", tessdata / "pdf.ttf")
    for idioma, archivo in IDIOMAS.items():
        bajar_verificado(archivo, tessdata / f"{idioma}.traineddata", progreso, cache)
    shutil.copy2(extraido / "doc" / "LICENSE", destino / "LICENSE-tesseract.txt")
    bajar_verificado(LICENCIA_TESSDATA, destino / "LICENSE-tessdata_best.txt", progreso, cache)


def _armar_ghostscript(extraido: Path, destino: Path) -> None:
    for nombre in ("gswin64c.exe", "gsdll64.dll"):
        shutil.copy2(extraido / "bin" / nombre, destino / nombre)
    shutil.copy2(extraido / "doc" / "COPYING", destino / "COPYING-ghostscript.txt")
    runtime = _runtime_msvc()
    for nombre, origen in runtime.items():
        shutil.copy2(origen, destino / nombre)
    _usados, faltan = dependencias(destino, ["gswin64c.exe", "gsdll64.dll"])
    if faltan:
        raise ErrorInstalacion("A Ghostscript le faltan bibliotecas: " + ", ".join(sorted(faltan)))


def comprobar(carpeta_programa: Path, programa: Programa) -> None:
    """Corre el programa armado para confirmar que funciona. Lanza ErrorInstalacion si no."""
    exe = carpeta_programa / programa.ejecutable
    entorno = dict(os.environ)
    if programa is TESSERACT:
        entorno["TESSDATA_PREFIX"] = str(carpeta_programa / "tessdata")
        argumentos = [str(exe), "--list-langs"]
    else:
        argumentos = [str(exe), "--version"]
    try:
        r = subprocess.run(argumentos, capture_output=True, text=True, errors="replace", timeout=60, env=entorno,
                           creationflags=SIN_VENTANA)
    except (OSError, subprocess.SubprocessError) as e:
        raise ErrorInstalacion(f"{programa.titulo} no arranca: {e}") from e
    salida = r.stdout + r.stderr
    if r.returncode != 0:
        raise ErrorInstalacion(f"{programa.titulo} no arranca (código {r.returncode}).\n{salida[-400:]}")
    if programa is TESSERACT:
        idiomas = {l.strip() for l in salida.splitlines()}
        if not {"spa", "eng"} <= idiomas:
            raise ErrorInstalacion(f"Tesseract no reconoce los idiomas spa y eng:\n{salida[-400:]}")


def preparar(programa: Programa, base: Path, progreso: Progreso = _sin_progreso, cache: Path | None = None,
             siete_zip_propio: bool = False) -> Path:
    """Baja, verifica y arma `programa` en base/<clave>. Reemplaza lo que hubiera solo si
    el nuevo funciona. Devuelve la carpeta."""
    base.mkdir(parents=True, exist_ok=True)
    final = base / programa.clave
    with tempfile.TemporaryDirectory(prefix="pdfstudio_programas_") as t:
        tmp = Path(t)
        instalador = INSTALADOR_TESSERACT if programa is TESSERACT else INSTALADOR_GHOSTSCRIPT
        archivo = bajar_verificado(instalador, tmp / instalador.nombre, progreso, cache)
        sz = siete_zip(tmp, progreso, cache, siete_zip_propio)
        progreso(0, 0, f"Extrayendo {programa.titulo}…")
        extraido = tmp / "extraido"
        _extraer(sz, archivo, extraido)
        nuevo = base / f"{programa.clave}.nuevo"
        if nuevo.exists():
            shutil.rmtree(nuevo)
        nuevo.mkdir()
        try:
            if programa is TESSERACT:
                _armar_tesseract(extraido, nuevo, progreso, cache)
            else:
                _armar_ghostscript(extraido, nuevo)
            (nuevo / "VERSION.txt").write_text(f"{programa.titulo} {programa.version}\n", encoding="utf-8")
            progreso(0, 0, f"Probando {programa.titulo}…")
            comprobar(nuevo, programa)
        except BaseException:
            shutil.rmtree(nuevo, ignore_errors=True)
            raise
    if final.exists():
        shutil.rmtree(final)
    os.replace(nuevo, final)
    return final


def tamano_descarga(programa: Programa) -> int:
    return sum(a.tamano for a in programa.descargas())


def instalacion(programa: Programa) -> Instalacion:
    """Instalación asistida desde el panel Faltantes: por HTTPS, con Python y sin otros programas del sistema,
    solo para PDF Studio y sin administrador."""
    def accion(progreso: Progreso) -> str:
        preparar(programa, config.DIR_PROGRAMAS, progreso)
        return f"{programa.titulo} quedó listo para PDF Studio."

    return Instalacion(f"programa:{programa.clave}", f"{programa.titulo} {programa.version}", programa.origen,
                       programa.licencia, f"Solo para PDF Studio, sin administrador ({config.DIR_PROGRAMAS})",
                       False, comando_preparar(), lambda: tamano_descarga(programa), accion)


# ---------------------------------------------------------------------- comando para copiar
def archivo_de_registro() -> Path:
    return config.DIR_LOCAL / "instalar-programas.log"


def comando_preparar() -> str:
    """Equivalente para copiar en PowerShell: corre este mismo código."""
    if getattr(sys, "frozen", False):
        return "\n".join([
            "# Baja Tesseract y Ghostscript oficiales (versión fija, verificados con SHA-256) para PDF Studio.",
            "# No requiere administrador.",
            f'$p = Start-Process -FilePath "{sys.executable}" -ArgumentList "--instalar-programas" -Wait -PassThru',
            f'Get-Content "{archivo_de_registro()}"',
        ])
    return f'Set-Location "{config.DIR_PAQUETE.parent}"; & "{sys.executable}" -m pdf_studio.dependencias.programas'


def main(argv: list[str] | None = None) -> int:
    """python -m pdf_studio.dependencias.programas [--destino CARPETA] [--cache CARPETA]
    [--7zip-propio] [tesseract] [ghostscript]"""
    argv = list(sys.argv[1:] if argv is None else argv)
    destino, cache, propio = carpeta_del_repo(), None, False
    if getattr(sys, "frozen", False):
        destino = config.DIR_PROGRAMAS
    if "--destino" in argv:
        i = argv.index("--destino")
        destino = Path(argv.pop(i + 1))
        argv.pop(i)
    if "--cache" in argv:
        i = argv.index("--cache")
        cache = Path(argv.pop(i + 1))
        argv.pop(i)
    if "--7zip-propio" in argv:
        argv.remove("--7zip-propio")
        propio = True
    pedidos = [p for p in PROGRAMAS if not argv or p.clave in argv]
    ultimo = [""]

    def progreso(_h: int, _t: int, mensaje: str) -> None:
        if mensaje != ultimo[0]:
            print(mensaje, flush=True)
            ultimo[0] = mensaje

    codigo = 0
    for p in pedidos:
        try:
            print(f"Listo: {preparar(p, destino, progreso, cache, propio)}", flush=True)
        except ErrorInstalacion as e:
            print(f"ERROR con {p.titulo}: {e}", flush=True)
            codigo = 1
    return codigo


if __name__ == "__main__":
    sys.exit(main())
