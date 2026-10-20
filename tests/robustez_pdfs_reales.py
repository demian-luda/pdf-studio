"""Prueba de robustez con PDFs reales (no se commitean: tienen datos sensibles).

Por cada PDF de la carpeta se corren varias pruebas, cada una sobre una COPIA temporal:
abrir, analizar fuentes y renderizar; editar texto; OCR (si hay páginas escaneadas); y
las que suman las fases siguientes. Cada prueba guarda y verifica la fidelidad. Los
originales nunca se modifican (se verifica su hash al final).

El informe muestra SOLO el nombre del archivo, la prueba, la etapa y el tipo de error:
nada de contenido, textos extraídos ni datos del documento.

Uso:  .\\.venv\\Scripts\\python.exe tests\\robustez_pdfs_reales.py [carpeta] [--sin-ocr]
      (por defecto C:\\Proyectos\\pdfs-reales)
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

CARPETA_POR_DEFECTO = Path(r"C:\Proyectos\pdfs-reales")
MAX_LINEAS_A_PROBAR = 8
MAX_PAGINAS_OCR = 2

# Rechazos controlados: la app se negó a editar con un aviso claro (no es un fallo).
# Se informan por categoría para no mostrar el mensaje, que puede incluir texto del PDF.
CATEGORIAS = [
    ("XObject", "texto dentro de un objeto compartido (XObject)"),
    ("vecino", "la edición habría borrado texto vecino"),
    ("codificación", "la fuente no permite releer el texto"),
    ("letras", "ninguna fuente disponible tiene las letras"),
    ("horizontal", "texto no horizontal"),
    ("no se pudo borrar", "no se pudo borrar el texto original"),
    ("reutilizar", "la fuente original no se puede reutilizar"),
    ("No se reconoció texto", "el OCR no encontró letras"),
    ("imagen escaneada está dentro", "escaneo dentro de un objeto compartido"),
]

OK, AVISO, FALLA, OMITIDA = "OK", "OK (aviso)", "FALLA", "—"


def _hash(ruta: Path) -> str:
    return hashlib.sha256(ruta.read_bytes()).hexdigest()


def _categoria(mensaje: str) -> str:
    for clave, texto in CATEGORIAS:
        if clave.lower() in mensaje.lower():
            return texto
    return "rechazo controlado"


class Fallo(Exception):
    def __init__(self, etapa: str, error: BaseException):
        super().__init__(etapa)
        self.etapa = etapa
        self.error = error


def _etapa(nombre: str, funcion):
    try:
        return funcion()
    except Fallo:
        raise
    except BaseException as e:      # noqa: BLE001 — se informa el tipo, nunca el contenido
        raise Fallo(nombre, e) from e


def _motivo_apertura(mensaje: str) -> str:
    for clave, texto in (("vacío", "archivo vacío"), ("no es un PDF", "no es un PDF"),
                         ("dañado", "PDF dañado"), ("contraseña", "protegido con contraseña"),
                         ("no tiene páginas", "sin páginas")):
        if clave in mensaje:
            return texto
    return "otro motivo"


def _guardar_y_verificar(documento, destino: Path, se_edito: bool) -> str:
    """Guarda, verifica la fidelidad y relee el resultado. Devuelve una nota (o '')."""
    import pymupdf

    from pdf_studio.core.documento import ErrorFidelidad
    try:
        guardado = documento.guardar(destino)
    except ErrorFidelidad as e:
        raise Fallo("fidelidad", e) from e
    except Exception as e:
        raise Fallo("guardar", e) from e
    if not se_edito and not guardado.bytes_originales_intactos:
        raise Fallo("guardar", AssertionError("la copia sin cambios no es idéntica"))

    def releer():
        with pymupdf.open(destino) as d:
            for p in d:
                p.get_pixmap(dpi=30)
    _etapa("releer guardado", releer)
    return "; guardado completo" if se_edito and not guardado.incremental else ""


# ---------------------------------------------------------------------- pruebas
# Cada prueba recibe (documento abierto sobre una copia, carpeta temporal) y devuelve
# (resultado, nota). Lanza Fallo si algo falla de verdad.

def prueba_abrir(documento, tmp: Path) -> tuple[str, str]:
    from pdf_studio.core.fuentes import analizar_fuentes, indice_incluidas, indice_sistema
    from pdf_studio.dependencias.chequeo import faltantes_de_fuentes
    doc = documento.doc
    fuentes = _etapa("fuentes", lambda: analizar_fuentes(doc, indice_sistema(), indice_incluidas()))
    _etapa("fuentes", lambda: faltantes_de_fuentes(fuentes))
    no_analizadas = sum(1 for f in fuentes if f.embebido.value == "no_analizada")

    def renderizar():
        for pag in doc:
            pag.get_pixmap(dpi=40)
    _etapa("render", renderizar)
    nota = f"{doc.page_count} pág."
    if no_analizadas:
        nota += f", {no_analizadas} fuente(s) sin analizar"
    return OK, nota


def prueba_editar_texto(documento, tmp: Path) -> tuple[str, str]:
    import pymupdf

    from pdf_studio.core.documento import ErrorDocumento
    from pdf_studio.core.fuentes import indice_incluidas, indice_sistema
    from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en, lineas_de_pagina, planificar
    doc = documento.doc
    candidatas = []
    for pno in range(doc.page_count):
        for bloque_lineas in _etapa("extraer texto", lambda p=pno: lineas_de_pagina(doc[p])):
            for ln in bloque_lineas:
                if ln.texto.strip():
                    candidatas.append((pno, ln))
        if len(candidatas) >= MAX_LINEAS_A_PROBAR:
            break
    if not candidatas:
        return OMITIDA, "sin texto (¿escaneado?)"
    rechazos = []
    editado = False
    for pno, ln in candidatas[:MAX_LINEAS_A_PROBAR]:
        # Tras un rechazo el documento se reconstruye: siempre usar el objeto actual
        doc = documento.doc
        caja = ln.bbox
        punto = pymupdf.Point(caja.x0 + min(2.0, caja.width / 2), (caja.y0 + caja.y1) / 2)
        bloque = _etapa("editar", lambda: bloque_en(doc[pno], pno, punto, Nivel.FRAGMENTO))
        if bloque is None:
            continue
        nuevo = bloque.texto.rstrip() + " 1"
        try:
            analisis = _etapa("editar", lambda: analizar_fuente(doc, doc[pno], bloque.estilo, nuevo,
                                                                indice_sistema(), indice_incluidas()))
            plan = planificar(doc, bloque, nuevo, analisis.recomendada)
            documento.ejecutar(plan.operacion)
            editado = True
            break
        except ErrorDocumento as e:
            rechazos.append(_categoria(str(e)))
        except Exception as e:
            raise Fallo("editar", e) from e
    nota = _guardar_y_verificar(documento, tmp / "editado.pdf", editado)
    if editado:
        return OK, "editado y verificado" + nota
    motivos = ", ".join(sorted(set(rechazos))) or "sin fragmentos seleccionables"
    return AVISO, f"rechazada con aviso ({motivos})" + nota


def prueba_ocr(documento, tmp: Path) -> tuple[str, str]:
    from pdf_studio.core import ocr
    from pdf_studio.core.documento import ErrorDocumento
    doc = documento.doc
    sin_texto = _etapa("detectar escaneo", lambda: ocr.paginas_sin_texto(doc))
    if not sin_texto:
        return OMITIDA, "no hay páginas escaneadas"
    paginas = sin_texto[:MAX_PAGINAS_OCR]
    try:
        capas = ocr.reconocer(doc, paginas, tmp, timeout=900)
        documento.ejecutar(ocr.AgregarCapaOCR(capas))
    except ErrorDocumento as e:
        return AVISO, f"OCR rechazado con aviso ({_categoria(str(e))})"
    except Exception as e:
        raise Fallo("ocr", e) from e
    reconocidas = [p for p in paginas if documento.doc[p].get_text().strip()]
    if not reconocidas:
        raise Fallo("ocr", AssertionError("la capa de texto no quedó extraíble"))
    guardado = _etapa("guardar", lambda: documento.preparar_guardado())
    if not guardado.es_fiel:
        raise Fallo("fidelidad", AssertionError("la página con OCR cambió de aspecto"))
    if not set(capas) <= set(guardado.fidelidad.enteras):
        raise Fallo("fidelidad", AssertionError("no se compararon enteras las páginas con OCR"))
    nota = _guardar_y_verificar(documento, tmp / "ocr.pdf", True)
    return OK, (f"texto reconocido en {len(reconocidas)} de {len(paginas)} pág., aspecto idéntico "
                f"(píxeles = 0)" + nota)


PRUEBAS: list[tuple[str, Callable]] = [
    ("abrir", prueba_abrir),
    ("editar texto", prueba_editar_texto),
    ("OCR", prueba_ocr),
]


def probar_archivo(ruta_original: Path, pruebas) -> list[tuple[str, str, str]]:
    """[(prueba, resultado, nota)] para un archivo."""
    from pdf_studio.core.documento import Documento, ErrorDocumento
    filas = []
    for nombre, prueba in pruebas:
        with tempfile.TemporaryDirectory(prefix="pdfstudio_robustez_") as tmp:
            tmp = Path(tmp)
            copia = tmp / "copia.pdf"
            shutil.copy2(ruta_original, copia)
            try:
                documento = Documento(copia)
            except ErrorDocumento as e:
                return [("abrir", AVISO, "no se abre, con mensaje claro: " + _motivo_apertura(str(e)))]
            except Exception as e:
                return [("abrir", FALLA, f"etapa «abrir»: {type(e).__name__}")]
            try:
                resultado, nota = prueba(documento, tmp)
            except Fallo as f:
                resultado, nota = FALLA, f"etapa «{f.etapa}»: {type(f.error).__name__}"
            finally:
                documento.cerrar()
            filas.append((nombre, resultado, nota))
    return filas


def main(argv: list[str]) -> int:
    opciones = [a for a in argv[1:] if a.startswith("--")]
    posicionales = [a for a in argv[1:] if not a.startswith("--")]
    carpeta = Path(posicionales[0]) if posicionales else CARPETA_POR_DEFECTO
    pruebas = [(n, p) for n, p in PRUEBAS if not ("--sin-ocr" in opciones and n == "OCR")]
    archivos = sorted(p for p in carpeta.glob("*") if p.suffix.lower() == ".pdf" and p.is_file())
    if not archivos:
        print(f"No hay PDFs en {carpeta}")
        return 1
    hashes = {p: _hash(p) for p in archivos}
    totales = {OK: 0, AVISO: 0, FALLA: 0, OMITIDA: 0}
    for ruta in archivos:
        inicio = time.monotonic()
        filas = probar_archivo(ruta, pruebas)
        peor = FALLA if any(r == FALLA for _, r, _ in filas) else (
            AVISO if any(r == AVISO for _, r, _ in filas) else OK)
        print(f"[{peor:10}] {ruta.name} ({time.monotonic() - inicio:.1f} s)", flush=True)
        for nombre, resultado, nota in filas:
            totales[resultado] += 1
            print(f"      {nombre:14} {resultado:10} {nota}", flush=True)
    intactos = all(_hash(p) == h for p, h in hashes.items())
    print()
    print(f"Archivos: {len(archivos)} · Pruebas OK: {totales[OK]} · OK con aviso: {totales[AVISO]} · "
          f"No aplicables: {totales[OMITIDA]} · Fallas: {totales[FALLA]}")
    print("Originales intactos: " + ("sí" if intactos else "¡NO!"))
    return 0 if totales[FALLA] == 0 and intactos else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
