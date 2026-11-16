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

# ---------------------------------------------------------------------- licencias de las DLL
@dataclass(frozen=True)
class Texto:
    """Un texto de licencia: el archivo descargado o, si es un tarball, uno de sus miembros."""
    archivo: Archivo
    destino: str                        # nombre en licencias/<carpeta>/
    miembro: str | None = None          # ruta dentro del tarball
    sha256_miembro: str | None = None


@dataclass(frozen=True)
class Licencia:
    carpeta: str                        # licencias/<carpeta> dentro del programa
    proyecto: str                       # proyecto y etiqueta de donde sale el texto
    spdx: str
    textos: tuple[Texto, ...]


_GH = "https://raw.githubusercontent.com/"


def _t(url: str, sha: str, tamano: int, destino: str) -> Texto:
    return Texto(Archivo(url, sha, tamano), destino)


def _lic(carpeta: str, proyecto: str, spdx: str, *textos: Texto) -> Licencia:
    return Licencia(carpeta, proyecto, spdx, textos)


# Tarballs oficiales de GNU y JBIG-KIT: el texto de la licencia se extrae (no se ejecuta nada)
_ICONV = Archivo("https://ftp.gnu.org/gnu/libiconv/libiconv-1.19.tar.gz",
                 "88dd96a8c0464eca144fc791ae60cd31cd8ee78321e67397e25fc095c4a19aa6", 5921103)
_UNISTRING = Archivo("https://ftp.gnu.org/gnu/libunistring/libunistring-1.4.2.tar.xz",
                     "5b46e74377ed7409c5b75e7a96f95377b095623b689d8522620927964a41499c", 2786084)
_GETTEXT = Archivo("https://ftp.gnu.org/gnu/gettext/gettext-0.26.tar.xz",
                   "d1fb86e260cfe7da6031f94d2e44c0da55903dbae0a2fa0fae78c91ae1b56f00", 10061740)
_JBIGKIT = Archivo("https://www.cl.cam.ac.uk/~mgk25/jbigkit/download/jbigkit-2.1.tar.gz",
                   "de7106b6bfaf495d6865c7dd7ac6ca1381bd12e0d81405ea81e7f2167263d932", 438710)
_GPL3 = "8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903"

_GCC = _lic("gcc-15.2.0", "GCC 15.2.0 (runtime)", "GPL-3.0-or-later WITH GCC-exception-3.1",
            _t("https://gcc.gnu.org/git/?p=gcc.git;a=blob_plain;f=COPYING3;hb=refs/tags/releases/gcc-15.2.0",
               _GPL3, 35147, "COPYING3.txt"),
            _t("https://gcc.gnu.org/git/?p=gcc.git;a=blob_plain;f=COPYING.RUNTIME;hb=refs/tags/releases/gcc-15.2.0",
               "9d6b43ce4d8de0c878bf16b54d8e7a10d9bd42b75178153e3af6a815bdc90f74", 3324, "COPYING.RUNTIME.txt"))
_BROTLI = _lic("brotli-1.2.0", "Brotli 1.2.0", "MIT",
               _t(_GH + "google/brotli/v1.2.0/LICENSE",
                  "3d180008e36922a4e8daec11c34c7af264fed5962d07924aea928c38e8663c94", 1084, "LICENSE.txt"))
_WEBP = _lic("libwebp-1.6.0", "libwebp 1.6.0", "BSD-3-Clause",
             _t(_GH + "webmproject/libwebp/v1.6.0/COPYING",
                "5aec868f669e384a22372a4e8a1a6cd7d44c64cd451f960ca69cc170d1e13acf", 1496, "COPYING.txt"),
             _t(_GH + "webmproject/libwebp/v1.6.0/PATENTS",
                "cc3273e0694ea5896145e0677699b53471b03ea43021ddc50e7923fbb9f5023c", 1436, "PATENTS.txt"))

# Clave: nombre de la DLL sin versión ni extensión, en minúsculas («libtiff-6.dll» → «libtiff»).
# Si la DLL dice su versión, el texto es de esa etiqueta; si no la dice, de la etiqueta indicada.
# Si una versión nueva de Tesseract trae otra DLL, el armado falla hasta registrarla acá.
LICENCIAS_DLL: dict[str, Licencia | None] = {
    "libarchive": _lic("libarchive-3.8.8", "libarchive 3.8.8", "BSD-2-Clause",
                       _t(_GH + "libarchive/libarchive/v3.8.8/COPYING",
                          "30e556b3959e3985d66efefec5eaac51d4995053caa1d3cffe6eb916f146f229", 3089, "COPYING.txt")),
    "libb2": _lic("libb2-0.98.1", "libb2 (BLAKE2) 0.98.1", "CC0-1.0",
                  _t(_GH + "BLAKE2/libb2/v0.98.1/COPYING",
                     "6a1ee543e5282cd9061881edf462e6fdab181f328da71fc2c9a6950a80e94d01", 6556, "COPYING.txt")),
    "libbrotlicommon": _BROTLI,
    "libbrotlidec": _BROTLI,
    "libbz2": _lic("bzip2-1.0.8", "bzip2 1.0.8", "bzip2-1.0.6",
                   _t("https://sourceware.org/git/?p=bzip2.git;a=blob_plain;f=LICENSE;hb=bzip2-1.0.8",
                      "c6dbbf828498be844a89eaa3b84adbab3199e342eb5cb2ed2f0d4ba7ec0f38a3", 1896, "LICENSE.txt")),
    "libcurl": _lic("curl-8.21.0", "curl 8.21.0", "curl",
                    _t(_GH + "curl/curl/curl-8_21_0/COPYING",
                       "82f2f4427d6545ee5aaac4f0b80428da6cc8ba41c2cf5da3a03680ec327b9681", 1088, "COPYING.txt")),
    "libdeflate": _lic("libdeflate-1.25", "libdeflate 1.25", "MIT",
                       _t(_GH + "ebiggers/libdeflate/v1.25/COPYING",
                          "4ad69099cb4374836fd27583d9991e2838cd86b6e4666ab26b2d32582c91e73a", 1078, "COPYING.txt")),
    "libexpat": _lic("expat-2.8.2", "Expat 2.8.2", "MIT",
                     _t(_GH + "libexpat/libexpat/R_2_8_2/expat/COPYING",
                        "31b15de82aa19a845156169a17a5488bf597e561b2c318d159ed583139b25e87", 1144, "COPYING.txt")),
    "libgcc_s_seh": _GCC,
    "libstdc++": _GCC,
    "libgif": _lic("giflib-5.2.2", "giflib 5.2.2", "MIT",
                   _t("https://sourceforge.net/p/giflib/code/ci/5.2.2/tree/COPYING?format=raw",
                      "0c9b7990ecdca88b676db232c226548ac408b279f550d424d996f0d83591dd8e", 1088, "COPYING.txt")),
    "libiconv": _lic("libiconv-1.19", "GNU libiconv 1.19", "LGPL-2.1-or-later",
                     Texto(_ICONV, "COPYING.LIB.txt", "libiconv-1.19/COPYING.LIB",
                           "20e50fe7aae3e56378ebf0417d9de904f55a0e61e4df315333e632a4d3555d95")),
    "libidn2": _lic("libidn2-2.3.8", "libidn2 2.3.8", "LGPL-3.0-or-later OR GPL-2.0-or-later",
                    _t("https://gitlab.com/libidn/libidn2/-/raw/v2.3.8/COPYING",
                       "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986", 35149, "COPYING.txt"),
                    _t("https://gitlab.com/libidn/libidn2/-/raw/v2.3.8/COPYING.LESSERv3",
                       "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118", 7652,
                       "COPYING.LESSERv3.txt"),
                    _t("https://gitlab.com/libidn/libidn2/-/raw/v2.3.8/COPYINGv2",
                       "edaef632cbb643e4e7a221717a6c441a4c1a7c918e6e4d56debc3d8739b233f6", 17984, "COPYINGv2.txt"),
                    _t("https://gitlab.com/libidn/libidn2/-/raw/v2.3.8/COPYING.unicode",
                       "01d621eef165cf4d3d3dbb737aa0699178d94c6f18cf87e9dde6db3ca7790f46", 8782,
                       "COPYING.unicode.txt")),
    "libintl": _lic("gettext-0.26", "gettext 0.26 (libintl)", "LGPL-2.1-or-later",
                    Texto(_GETTEXT, "COPYING.LIB.txt", "gettext-0.26/gettext-runtime/intl/COPYING.LIB",
                          "20e50fe7aae3e56378ebf0417d9de904f55a0e61e4df315333e632a4d3555d95")),
    "libjbig": _lic("jbigkit-2.1", "JBIG-KIT 2.1", "GPL-2.0-or-later",
                    Texto(_JBIGKIT, "COPYING.txt", "jbigkit-2.1/COPYING",
                          "91df39d1816bfb17a4dda2d3d2c83b1f6f2d38d53e53e41e8f97ad5ac46a0cad")),
    "libjpeg": _lic("libjpeg-turbo-3.2.0", "libjpeg-turbo 3.2.0", "IJG AND BSD-3-Clause AND Zlib",
                    _t(_GH + "libjpeg-turbo/libjpeg-turbo/3.2.0/LICENSE.md",
                       "ba6bceebcba0fdd35488477c2cca8c4632ce82c74dbfbc87d886ce6fc4433579", 6077, "LICENSE.md.txt"),
                    _t(_GH + "libjpeg-turbo/libjpeg-turbo/3.2.0/README.ijg",
                       "75815e3bf6484201a3c3d17a1bbf10f2e8e3237f84df10a2357ea896db2a81d6", 12799, "README.ijg.txt")),
    "libleptonica": _lic("leptonica-1.86.0", "Leptonica 1.86.0", "BSD-2-Clause",
                         _t(_GH + "DanBloomberg/leptonica/1.86.0/leptonica-license.txt",
                            "87829abb5bbb00b55a107365da89e9a33f86c4250169e5a1e5588505be7d5806", 1521,
                            "leptonica-license.txt")),
    "liblerc": _lic("lerc-4.0.0", "LERC 4.0.0", "Apache-2.0",
                    _t(_GH + "Esri/lerc/v4.0.0/LICENSE",
                       "77a8b761727c75e2167b15bfdf61b2c0bcf8792271228bebe80779106ad00671", 9198, "LICENSE.txt")),
    "liblz4": _lic("lz4-1.10.0", "LZ4 1.10.0 (biblioteca)", "BSD-2-Clause",
                   _t(_GH + "lz4/lz4/v1.10.0/lib/LICENSE",
                      "8b58c446121a109ccf32edc094bba3010a3d85e4ee3702950db55e4d3e87736c", 1311, "LICENSE.txt")),
    "liblzma": _lic("xz-5.8.3", "XZ Utils 5.8.3 (liblzma)", "0BSD",
                    _t(_GH + "tukaani-project/xz/v5.8.3/COPYING",
                       "616a3ad264ce29b8f1cb97e53037b139d406899ca8d1f799651e17bfa09830b8", 3119, "COPYING.txt"),
                    _t(_GH + "tukaani-project/xz/v5.8.3/COPYING.0BSD",
                       "0b01625d853911cd0e2e088dcfb743261034a091bb379246cb25a14cc4c74bf1", 607, "COPYING.0BSD.txt")),
    "libopenjp2": _lic("openjpeg-2.5.4", "OpenJPEG 2.5.4", "BSD-2-Clause",
                       _t(_GH + "uclouvain/openjpeg/v2.5.4/LICENSE",
                          "a6af136f3e15038a666b61f376612a07d9a4e48cb7c01adbf3e33b3f14ab49b6", 2112, "LICENSE.txt")),
    "libpng16": _lic("libpng-1.6.58", "libpng 1.6.58", "Libpng-2.0",
                     _t(_GH + "pnggroup/libpng/v1.6.58/LICENSE",
                        "bdb0a645ea18c60507d0368379b1ac5474b92255fcc2d115e07486a7672ba526", 5345, "LICENSE.txt")),
    "libpsl": _lic("libpsl-0.21.5", "libpsl 0.21.5", "MIT",
                   _t(_GH + "rockdaboot/libpsl/0.21.5/COPYING",
                      "322392e6001b2b3b0fca705dc9f11f26e12441188ecde50eb1d22909980b2925", 1060, "COPYING.txt")),
    "libsharpyuv": _WEBP,
    "libwebp": _WEBP,
    "libwebpmux": _WEBP,
    "libssh2": _lic("libssh2-1.11.1", "libssh2 1.11.1", "BSD-3-Clause",
                    _t(_GH + "libssh2/libssh2/libssh2-1.11.1/COPYING",
                       "f7f9633cf9ff2f1333f3d7ce46973a8716a4d2a2815ad56f30d437d5fea7bafe", 1959, "COPYING.txt")),
    "libtesseract": None,      # es Tesseract mismo: su licencia va aparte (LICENSE-tesseract.txt)
    "libtiff": _lic("libtiff-4.7.2", "LibTIFF 4.7.2", "libtiff",
                    _t("https://gitlab.com/libtiff/libtiff/-/raw/v4.7.2/LICENSE.md",
                       "0e27c2382d7b8147972bbb746e04059a1152c8d0fda9d03ef1399d1a433c4ade", 2416, "LICENSE.md.txt")),
    "libunistring": _lic("libunistring-1.4.2", "GNU libunistring 1.4.2", "LGPL-3.0-or-later OR GPL-2.0-or-later",
                         Texto(_UNISTRING, "COPYING.LIB.txt", "libunistring-1.4.2/COPYING.LIB",
                               "a853c2ffec17057872340eee242ae4d96cbf2b520ae27d903e1b2fef1a5f9d1c"),
                         Texto(_UNISTRING, "COPYING.txt", "libunistring-1.4.2/COPYING",
                               "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986")),
    "libwinpthread": _lic("winpthreads-13.0.0", "mingw-w64 13.0.0 (winpthreads)", "MIT AND BSD-3-Clause",
                          _t(_GH + "mingw-w64/mingw-w64/v13.0.0/mingw-w64-libraries/winpthreads/COPYING",
                             "63263614cdd29f2f93cba85e992f041b31f9fc7b4033692f31269489a8a1b177", 2883,
                             "COPYING.txt")),
    "libzstd": _lic("zstd-1.5.7", "Zstandard 1.5.7", "BSD-3-Clause",
                    _t(_GH + "facebook/zstd/v1.5.7/LICENSE",
                       "7055266497633c9025b777c78eb7235af13922117480ed5c674677adc381c9d8", 1549, "LICENSE.txt")),
    "zlib1": _lic("zlib-1.3.2", "zlib 1.3.2", "Zlib",
                  _t(_GH + "madler/zlib/v1.3.2/LICENSE",
                     "e32ff4e00d9d94930537635291da39e7e612703334bf6fde8c7f1686fe8a45a2", 1002, "LICENSE.txt")),
}

# El runtime de Visual C++ que va con Ghostscript: el aviso de Microsoft Distributable Code que
# publica CPython (que redistribuye las mismas DLL)
LICENCIA_MSVC = _lic("msvc-runtime", "Microsoft Visual C++ runtime 14.x (aviso publicado por CPython 3.13.13)",
                     "Microsoft Distributable Code",
                     _t(_GH + "python/cpython/v3.13.13/PC/crtlicense.txt",
                        "3c71b3ae243121f6f227374cd1ef7c59ed07b1969b330d0e947f91a3a1b3fd73", 1738, "crtlicense.txt"))


def base_dll(nombre: str) -> str:
    """«libtiff-6.dll» → «libtiff»; «libLerc.dll» → «liblerc»."""
    import re
    return re.sub(r"(-[\d.]+)+$", "", nombre[:-4]).lower() if nombre.lower().endswith(".dll") else nombre.lower()


def licencias_de(carpeta_programa: Path) -> list[tuple[str, Licencia]]:
    """(dll, licencia) de cada DLL de un programa armado. KeyError si una no está registrada."""
    resultado = []
    for dll in sorted(p.name for p in carpeta_programa.glob("*.dll")):
        if (carpeta_programa / "gsdll64.dll").is_file():          # Ghostscript
            if dll.lower() in RUNTIME_MSVC:
                resultado.append((dll, LICENCIA_MSVC))
            continue                                   # gsdll64.dll: COPYING-ghostscript.txt
        lic = LICENCIAS_DLL[base_dll(dll)]
        if lic is not None:
            resultado.append((dll, lic))
    return resultado


def _descargas_de_licencias(licencias) -> list[Archivo]:
    vistos: dict[str, Archivo] = {}
    for lic in licencias:
        for t in lic.textos:
            vistos[t.archivo.sha256] = t.archivo
    return list(vistos.values())


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
            licencias = [lic for lic in LICENCIAS_DLL.values() if lic is not None]
            return [INSTALADOR_TESSERACT, *IDIOMAS.values(), LICENCIA_TESSDATA, *_descargas_de_licencias(licencias)]
        return [INSTALADOR_GHOSTSCRIPT, *_descargas_de_licencias([LICENCIA_MSVC])]

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
    en_cache = cache / archivo.sha256 if cache is not None else None      # por huella: sin choques de nombre
    if en_cache is not None and en_cache.is_file() and sha256(en_cache) == archivo.sha256:
        shutil.copy2(en_cache, destino)
        return destino
    descargar(archivo.url, destino, progreso, f"Descargando {archivo.nombre}")
    obtenido = sha256(destino)
    if obtenido != archivo.sha256:
        destino.unlink(missing_ok=True)
        raise ErrorInstalacion(f"El archivo descargado no es el esperado ({archivo.nombre}): su SHA-256 es "
                               f"{obtenido} y debía ser {archivo.sha256}. No se usó.")
    if en_cache is not None:
        cache.mkdir(parents=True, exist_ok=True)
        shutil.copy2(destino, en_cache)
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


def escribir_licencias(carpeta_programa: Path, tmp: Path, progreso: Progreso = _sin_progreso,
                       cache: Path | None = None) -> None:
    """Baja (verificados) los textos completos de licencia de cada DLL del programa y los deja en
    <programa>/licencias/<proyecto>/. De un tarball se extrae solo el texto, y se verifica también."""
    import tarfile
    descargados: dict[str, Path] = {}
    for _dll, lic in licencias_de(carpeta_programa):
        carpeta_lic = carpeta_programa / "licencias" / lic.carpeta
        carpeta_lic.mkdir(parents=True, exist_ok=True)
        for texto in lic.textos:
            final = carpeta_lic / texto.destino
            if final.is_file():
                continue                                   # la misma licencia para varias DLL
            if texto.archivo.sha256 not in descargados:
                ruta = tmp / f"lic-{texto.archivo.sha256[:16]}"
                descargados[texto.archivo.sha256] = bajar_verificado(texto.archivo, ruta, progreso, cache)
            origen = descargados[texto.archivo.sha256]
            if texto.miembro is None:
                shutil.copy2(origen, final)
                continue
            with tarfile.open(origen) as t:
                datos = t.extractfile(texto.miembro).read()
            if hashlib.sha256(datos).hexdigest() != texto.sha256_miembro:
                raise ErrorInstalacion(f"El texto de licencia {texto.miembro} no es el esperado.")
            final.write_bytes(datos)


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
    try:
        escribir_licencias(destino, extraido.parent, progreso, cache)
    except KeyError as e:
        raise ErrorInstalacion(f"La biblioteca {e} de Tesseract no tiene su licencia registrada "
                               "(LICENCIAS_DLL en dependencias/programas.py).") from e


def _armar_ghostscript(extraido: Path, destino: Path, progreso: Progreso, cache: Path | None) -> None:
    for nombre in ("gswin64c.exe", "gsdll64.dll"):
        shutil.copy2(extraido / "bin" / nombre, destino / nombre)
    shutil.copy2(extraido / "doc" / "COPYING", destino / "COPYING-ghostscript.txt")
    runtime = _runtime_msvc()
    for nombre, origen in runtime.items():
        shutil.copy2(origen, destino / nombre)
    _usados, faltan = dependencias(destino, ["gswin64c.exe", "gsdll64.dll"])
    if faltan:
        raise ErrorInstalacion("A Ghostscript le faltan bibliotecas: " + ", ".join(sorted(faltan)))
    escribir_licencias(destino, extraido.parent, progreso, cache)


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
                _armar_ghostscript(extraido, nuevo, progreso, cache)
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
