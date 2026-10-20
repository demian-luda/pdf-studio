"""Prueba de robustez con PDFs reales (no se commitean: tienen datos sensibles).

Por cada PDF de la carpeta: abre una COPIA temporal, analiza fuentes, renderiza todas
las páginas, edita un fragmento de texto, guarda y verifica la fidelidad. Los
originales nunca se modifican (se verifica su hash al final).

El informe muestra SOLO el nombre del archivo, la etapa y el tipo de error: nada de
contenido, textos extraídos ni datos del documento.

Uso:  .\\.venv\\Scripts\\python.exe tests\\robustez_pdfs_reales.py [carpeta]
      (por defecto C:\\Proyectos\\pdfs-reales)
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

CARPETA_POR_DEFECTO = Path(r"C:\Proyectos\pdfs-reales")
MAX_LINEAS_A_PROBAR = 8

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
]


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


def probar(ruta_original: Path, temporal: Path) -> tuple[str, str]:
    """Devuelve (resultado, nota). resultado: 'OK', 'OK (aviso)' o 'FALLA'."""
    import pymupdf

    from pdf_studio.core.documento import Documento, ErrorDocumento, ErrorFidelidad
    from pdf_studio.core.fuentes import analizar_fuentes, indice_incluidas, indice_sistema
    from pdf_studio.core.texto import Nivel, analizar_fuente, bloque_en, lineas_de_pagina, planificar
    from pdf_studio.dependencias.chequeo import faltantes_de_fuentes

    copia = temporal / "copia.pdf"
    shutil.copy2(ruta_original, copia)
    try:
        documento = _etapa("abrir", lambda: Documento(copia))
    except Fallo as f:
        if isinstance(f.error, ErrorDocumento):
            return "OK (aviso)", "no se abre, con mensaje claro: " + _motivo_apertura(str(f.error))
        raise
    try:
        doc = documento.doc
        fuentes = _etapa("fuentes", lambda: analizar_fuentes(doc, indice_sistema(), indice_incluidas()))
        _etapa("fuentes", lambda: faltantes_de_fuentes(fuentes))
        no_analizadas = sum(1 for f in fuentes if f.embebido.value == "no_analizada")

        def renderizar():
            for pag in doc:
                pag.get_pixmap(dpi=40)
        _etapa("render", renderizar)

        # Editar: la primera línea que se deje editar (hasta MAX_LINEAS_A_PROBAR)
        candidatas = []
        for pno in range(doc.page_count):
            for bloque_lineas in _etapa("extraer texto", lambda p=pno: lineas_de_pagina(doc[p])):
                for ln in bloque_lineas:
                    if ln.texto.strip():
                        candidatas.append((pno, ln))
            if len(candidatas) >= MAX_LINEAS_A_PROBAR:
                break
        if not candidatas:
            return "OK", "sin texto editable (¿escaneado?)" + _nota_fuentes(no_analizadas)
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
        destino = temporal / "salida.pdf"
        try:
            guardado = _etapa("guardar", lambda: documento.guardar(destino))
        except Fallo as f:
            if isinstance(f.error, ErrorFidelidad):
                raise Fallo("fidelidad", f.error) from f.error
            raise
        if editado and not guardado.incremental:
            nota_guardado = "; guardado completo (el PDF no admite incremental)"
        elif not editado and not guardado.bytes_originales_intactos:
            raise Fallo("guardar", AssertionError("la copia sin cambios no es idéntica"))
        else:
            nota_guardado = ""
        def releer():
            with pymupdf.open(destino) as d:
                for p in d:
                    p.get_pixmap(dpi=30)
        _etapa("releer guardado", releer)
        if editado:
            return "OK", "editado y verificado" + nota_guardado + _nota_fuentes(no_analizadas)
        motivos = ", ".join(sorted(set(rechazos))) or "sin fragmentos seleccionables"
        return "OK (aviso)", f"edición rechazada con aviso ({motivos})" + nota_guardado + _nota_fuentes(no_analizadas)
    finally:
        documento.cerrar()


def _nota_fuentes(n: int) -> str:
    return f"; {n} fuente(s) no se pudieron analizar" if n else ""


def _motivo_apertura(mensaje: str) -> str:
    for clave, texto in (("vacío", "archivo vacío"), ("no es un PDF", "no es un PDF"),
                         ("dañado", "PDF dañado"), ("contraseña", "protegido con contraseña"),
                         ("no tiene páginas", "sin páginas")):
        if clave in mensaje:
            return texto
    return "otro motivo"


def main(argv: list[str]) -> int:
    carpeta = Path(argv[1]) if len(argv) > 1 else CARPETA_POR_DEFECTO
    archivos = sorted(p for p in carpeta.glob("*") if p.suffix.lower() == ".pdf" and p.is_file())
    if not archivos:
        print(f"No hay PDFs en {carpeta}")
        return 1
    hashes = {p: _hash(p) for p in archivos}
    filas = []
    for ruta in archivos:
        inicio = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="pdfstudio_robustez_") as tmp:
            try:
                resultado, nota = probar(ruta, Path(tmp))
            except Fallo as f:
                resultado, nota = "FALLA", f"etapa «{f.etapa}»: {type(f.error).__name__}"
        filas.append((ruta.name, resultado, nota, time.monotonic() - inicio))
        print(f"[{resultado:10}] {ruta.name} — {nota} ({filas[-1][3]:.1f} s)", flush=True)
    intactos = all(_hash(p) == h for p, h in hashes.items())
    fallas = sum(1 for f in filas if f[1] == "FALLA")
    print()
    print(f"Archivos: {len(filas)} · OK: {sum(1 for f in filas if f[1] == 'OK')} · "
          f"OK con aviso: {sum(1 for f in filas if f[1] == 'OK (aviso)')} · Fallas: {fallas}")
    print("Originales intactos: " + ("sí" if intactos else "¡NO!"))
    return 0 if fallas == 0 and intactos else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
