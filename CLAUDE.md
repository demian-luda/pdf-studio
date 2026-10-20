# PDF Studio — Editor de PDF universal y gratuito

## Contexto

Aplicación de escritorio para Windows, de uso personal y para amigos, siempre gratuita y de código abierto (licencia AGPL-3.0, compatible con PyMuPDF). El usuario programa en Python y ya hizo apps con PyQt.

Idioma de la interfaz: **español rioplatense**. Comentarios del código en español.

## Entorno del usuario

- Windows, PowerShell. Carpeta del proyecto: `C:\Proyectos\pdf-studio`.
- Python 3.13 de Anaconda (la consola arranca con `(base)` activo). **No instalar nada en el entorno base de conda.** Usar un entorno virtual propio del proyecto:
  - Crear: `python -m venv .venv`
  - Activar: `.\.venv\Scripts\Activate.ps1`. Si PowerShell bloquea scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
  - Instalar siempre con `python -m pip install ...` estando el `.venv` activo.
  - Si alguna dependencia no tiene soporte para 3.13, avisar antes de cambiar de versión de Python.
- Git ya está configurado, con un repo en GitHub (rama `main`, remoto `origin`).
- winget está disponible.

## Flujo con Git

- Al terminar cada fase, cuando el usuario la apruebe: `git add -A`, luego `git commit -m "Fase N: <resumen>"` y `git push`.
- Para cambios riesgosos dentro de una fase, trabajar en una rama (`fase-N-...`) y fusionarla al aprobarse.
- Nunca commitear `.venv`, PDFs personales del usuario, certificados (.pfx/.p12) ni firmas de `%APPDATA%`. Los PDFs de `tests/pdfs_prueba/` se commitean solo si son de ejemplo y no tienen datos reales.

## Principios innegociables

1. **El archivo original nunca se modifica.** Siempre se guarda una copia nueva (`nombre_editado.pdf`) salvo que el usuario elija explícitamente "Sobrescribir".
2. **Se toca solo lo que el usuario editó.** Usar guardado incremental cuando sea posible (`doc.save(..., incremental=True)` sobre una copia) o, si no, reescribir únicamente los objetos afectados. Nada de re-renderizar páginas enteras ni rasterizar contenido vectorial.
3. **Fidelidad visual verificable.** Después de cada guardado, se compara el render de las páginas no editadas contra el original (diferencia de píxeles = 0). Si hay diferencia, se avisa.
4. **Deshacer/Rehacer** en todas las operaciones de edición.
5. **Nunca fallar en silencio.** Si falta una fuente, librería o herramienta, se muestra qué falta y el **comando de PowerShell listo para copiar** que lo instala.

## Stack

| Uso | Librería |
|---|---|
| Interfaz | PySide6 (o PyQt5 si ya está instalado; elegir uno y mantenerlo) |
| Motor PDF (render, texto, imágenes, redacción, formularios) | PyMuPDF (`pymupdf`) |
| Guardados seguros / estructura | `pikepdf` |
| OCR | `ocrmypdf` + Tesseract (idiomas `spa` + `eng`) |
| Firma digital con certificado | `pyHanko` |
| Imágenes (quitar fondo de firmas) | `Pillow`, `numpy` |
| Empaquetado | `PyInstaller` (un .exe para compartir) |

Usar un entorno virtual (`.venv`) y un `requirements.txt` con versiones fijadas.

## Arquitectura sugerida

```
pdf_studio/
  app.py                  # punto de entrada
  ui/                     # ventanas, paneles, visor, miniaturas
  core/
    documento.py          # abrir/guardar, historial deshacer/rehacer
    texto.py              # edición y movimiento de texto
    fuentes.py            # análisis de fuentes, sustitución
    imagenes.py           # insertar imágenes y firmas
    paginas.py            # unir, separar, rotar, reordenar
    ocr.py
    redaccion.py
    formularios.py
    comparar.py
    firma_digital.py
    comprimir.py
    lotes.py
  dependencias/
    chequeo.py            # detector de dependencias + comandos PowerShell
  tests/
    pdfs_prueba/          # PDFs de ejemplo variados
```

## Detector de dependencias (pieza central)

Se ejecuta al iniciar la app y al abrir cada PDF. Muestra un panel "Faltantes" con:
- Qué falta y para qué sirve.
- El comando de PowerShell listo, con botón **Copiar**.

Casos a cubrir:
- **Paquetes de Python faltantes** → `pip install <paquete>` (usando el pip del .venv).
- **Tesseract** → comando `winget`. **Verificar el ID real con `winget search tesseract` antes de hardcodearlo** (esperado: `UB-Mannheim.TesseractOCR`). Incluir también el comando para descargar `spa.traineddata` a la carpeta tessdata con `Invoke-WebRequest`.
- **Ghostscript** (opcional, para compresión agresiva) → comando `winget`; verificar el ID con `winget search ghostscript`.
- **Fuentes**: por cada fuente del PDF, informar si está **embebida completa**, **embebida como subconjunto** (recortada) o **no embebida**, y si está instalada en Windows.
  - Si es una fuente libre (Google Fonts u otras de licencia abierta): generar un comando de PowerShell que la descargue e instale para el usuario actual (`%LOCALAPPDATA%\Microsoft\Windows\Fonts` + registro en `HKCU`), sin pedir permisos de administrador.
  - Si es una fuente comercial o desconocida: no inventar descargas; ofrecer la sustitución (ver abajo).

## Edición de texto

- Seleccionar un bloque de texto (span/línea/párrafo) y **editarlo** o **moverlo** arrastrando.
- Técnica: redactar (borrar) solo el área del texto original **sin tocar imágenes ni gráficos de fondo** (`apply_redactions(images=PDF_REDACT_IMAGE_NONE, graphics=...)` con cuidado), y reinsertar el texto con la misma fuente, tamaño, color y posición de la línea base.
- Si la fuente embebida está recortada y faltan letras para el texto nuevo:
  1. Usar la fuente completa instalada en el sistema, si existe.
  2. Si no, ofrecer instalarla (detector de dependencias).
  3. Si no es posible, ofrecer la sustitución.

## Sustitución global de fuentes (modo "armónico")

Para PDFs con fuente privada: opción **"Reemplazar fuente en todo el documento"**, así el documento queda uniforme después de editar.

- Mostrar una vista previa lado a lado antes de aplicar.
- Priorizar **fuentes métricamente compatibles**, que tienen el mismo ancho por letra que la original, para que no se corran las líneas:
  - Arial / Helvetica → Liberation Sans o Arimo
  - Times New Roman → Liberation Serif o Tinos
  - Courier New → Liberation Mono o Cousine
  - Calibri → Carlito
  - Cambria → Caladea
- Si no hay una compatible, elegir la más parecida por familia (serif / sans / mono), peso y proporciones, y **ajustar el escalado horizontal de cada línea** para que ocupe exactamente el mismo ancho que la original. Así se mantienen los saltos de línea, la alineación y las tablas.
- Mantener tamaños, colores, negritas/cursivas y posiciones.
- Reportar al final las líneas donde el ajuste fue mayor a ±5%.

## Firmas en imagen

- Biblioteca de firmas guardada en `%APPDATA%\PDFStudio\firmas`.
- Insertar un PNG con transparencia, arrastrarlo, escalarlo manteniendo la proporción y rotarlo.
- Botón **"Quitar fondo"**: convierte una firma escaneada (JPG/PNG con fondo blanco) en PNG transparente, con umbral ajustable y vista previa.
- Insertar como imagen en la página, sin rasterizar el resto.

## OCR

- **Modo por defecto: "Idéntico"** → `ocrmypdf` con capa de texto invisible. Se ve igual al original, pero el texto se puede buscar, seleccionar y copiar. Idioma: `spa+eng`.
- **Modo opcional: "Reconstruido (experimental)"** → reemplaza la imagen por texto real editable. Advertir que es aproximado y que funciona mejor en documentos simples.
- Detectar automáticamente si un PDF es de imagen (páginas sin texto extraíble) y sugerir el OCR.

## Extras

- **Páginas**: unir, separar, rotar, eliminar y reordenar arrastrando miniaturas; insertar páginas en blanco o desde otro PDF.
- **Comprimir**: niveles bajo/medio/alto (recompresión de imágenes, limpieza de objetos con `garbage=4, deflate=True`); Ghostscript opcional. Mostrar el tamaño antes y después.
- **Redacción real**: marcar áreas o buscar texto (por ejemplo, DNI o CUIT con expresiones regulares) y **eliminar el contenido del archivo**, no solo taparlo. Verificar después que el texto ya no se pueda extraer.
- **Formularios**: detectar campos, completarlos y opcionalmente "aplanarlos".
- **Marca de agua y numeración**: texto o imagen, opacidad, posición, rango de páginas; formatos de numeración como "Página X de Y".
- **Comparar versiones**: dos PDFs lado a lado; diferencias de texto (palabras agregadas en verde y quitadas en rojo) y diferencias visuales por píxeles. Exportar un informe de diferencias en PDF.
- **Firma digital con certificado** (`pyHanko`): firmar con un archivo .pfx/.p12, firma visible opcional con imagen, y validar firmas existentes.
- **Procesamiento por lotes**: elegir una carpeta y aplicar OCR, compresión, marca de agua, numeración o conversión a todos los archivos, con barra de progreso y log.
- **Herramientas de lectura**: búsqueda, resaltado, notas y zoom.

## Fases (completar y probar cada una antes de seguir)

1. **Base**: estructura, .venv, detector de dependencias, visor (zoom, miniaturas, búsqueda), abrir/guardar copia, deshacer/rehacer, verificación de fidelidad.
2. **Firmas en imagen** + quitar fondo + biblioteca.
3. **Edición y movimiento de texto** + análisis de fuentes + comandos de instalación de fuentes.
4. **Sustitución global de fuentes** (modo armónico).
5. **OCR** (idéntico + reconstruido experimental).
6. **Páginas, compresión, marca de agua y numeración.**
7. **Redacción real y formularios.**
8. **Comparar versiones.**
9. **Firma digital con certificado.**
10. **Procesamiento por lotes.**
11. **Empaquetado** con PyInstaller (.exe) + README de instalación.

## Pruebas

Armar en `tests/pdfs_prueba/` un set variado: un PDF exportado desde Word, uno escaneado, uno con fuentes recortadas, uno con formulario, uno con tablas y uno con imágenes de fondo. Cada fase debe tener tests automáticos (pytest), y en cada fase el principio 3 (fidelidad visual) se valida automáticamente.

## Forma de trabajar

- Avanzar fase por fase. Al terminar cada fase, resumir qué se hizo y cómo probarlo, y esperar la confirmación del usuario.
- Antes de instalar algo, mostrar el comando y explicar para qué sirve.
- Si una función no puede garantizar la fidelidad en un caso concreto, avisarle al usuario en la interfaz en vez de guardar un resultado defectuoso.
