# PDF Studio — Editor de PDF universal y gratuito

## Contexto

Aplicación de escritorio para Windows, de uso personal y para amigos, siempre gratuita y de código abierto (licencia AGPL-3.0, compatible con PyMuPDF). El usuario programa en Python y ya hizo apps con PyQt.

Estado: las 11 fases iniciales están terminadas y hay versiones publicadas (ver «Versiones y publicación»). Lo nuevo entra como mejora o corrección, no como fase.

Idioma de la interfaz: **español rioplatense**. Comentarios del código en español.

## Entorno del usuario

- Windows, PowerShell. Carpeta del proyecto: `C:\Proyectos\pdf-studio`.
- Python 3.13 de Anaconda (la consola arranca con `(base)` activo). **No instalar nada en el entorno base de conda.** Usar un entorno virtual propio del proyecto:
  - Crear: `python -m venv .venv`
  - Activar: `.\.venv\Scripts\Activate.ps1`. Si PowerShell bloquea scripts: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
  - Instalar siempre con `python -m pip install ...` estando el `.venv` activo.
  - Si alguna dependencia no tiene soporte para 3.13, avisar antes de cambiar de versión de Python.
- Ejecutar desde el código: `python -m pdf_studio`. La copia instalada está en `%LOCALAPPDATA%\Programs\PDF Studio` y no cambia con el código.
- Git ya está configurado, con un repo en GitHub (rama `main`, remoto `origin`).
- winget está disponible.

## Flujo con Git

- Al terminar un cambio, cuando el usuario lo apruebe: `git add -A`, luego `git commit -m "<resumen>"` y `git push`.
- Para cambios riesgosos, trabajar en una rama y fusionarla al aprobarse.
- Nunca commitear `.venv`, PDFs personales del usuario, certificados (.pfx/.p12) ni firmas de `%APPDATA%`. Los PDFs de `tests/pdfs_prueba/` se commitean solo si son de ejemplo y no tienen datos reales.

## Principios innegociables

1. **El archivo original nunca se modifica.** Siempre se guarda una copia nueva (`nombre_editado.pdf`) salvo que el usuario elija explícitamente "Sobrescribir".
2. **Se toca solo lo que el usuario editó.** Usar guardado incremental cuando sea posible (`doc.save(..., incremental=True)` sobre una copia) o, si no, reescribir únicamente los objetos afectados. Nada de re-renderizar páginas enteras ni rasterizar contenido vectorial.
3. **Fidelidad visual verificable.** Después de cada guardado, se compara el render de las páginas no editadas contra el original (diferencia de píxeles = 0). Si hay diferencia, se avisa.
4. **Deshacer/Rehacer** en todas las operaciones de edición.
5. **Nunca fallar en silencio.** Si falta una fuente, librería o herramienta, se muestra qué falta y el **comando de PowerShell listo para copiar** que lo instala.
6. **Instalación asistida, siempre con confirmación explícita.** La app puede instalar dependencias por su cuenta (botón "Instalar"), pero nunca sin que el usuario confirme después de ver: nombre, origen de la descarga, licencia, tamaño y dónde se instala. Siempre se ofrece también "Copiar comando". Las fuentes se instalan solo para el usuario (sin administrador) y quedan disponibles sin reiniciar; si Windows necesita permisos de administrador (Tesseract, Ghostscript), se deja que aparezca su propio aviso.

## Stack

| Uso | Librería |
|---|---|
| Interfaz | PySide6 |
| Motor PDF (render, texto, imágenes, redacción, formularios) | PyMuPDF (`pymupdf`) |
| Guardados seguros / estructura | `pikepdf` |
| OCR | `ocrmypdf` + Tesseract (idiomas `spa` + `eng`) |
| Firma digital con certificado | `pyHanko` |
| Imágenes (quitar fondo de firmas) | `Pillow`, `numpy` |
| Análisis de fuentes e instancias variables | `fontTools` |
| Fotos y escaneados a documento editable | `opencv-python-headless`, `python-docx`, `spylls` (corrector) |
| Empaquetado | `PyInstaller`, en modo carpeta (ZIP con instalador) |

Usar un entorno virtual (`.venv`). Versiones fijadas en `requirements.txt` (desarrollo) y `requirements-empaquetado.txt` (lo que arma el workflow).

## Arquitectura

```
pdf_studio/
  app.py                  # punto de entrada (también procesos auxiliares: --tarea, --ocrmypdf, --autoprueba)
  instancia.py            # instancia única: un doble clic con la app abierta suma una pestaña
  posiciones.py           # última página y zoom de cada PDF
  autoprueba.py           # prueba el .exe armado, sin interfaz
  config.py, procesos.py, analisis_proceso.py
  ui/                     # ventanas, pestañas, paneles, visor, miniaturas, diálogos, tema, íconos, cinta
  core/
    documento.py          # abrir/guardar, historial deshacer/rehacer
    fidelidad.py          # comparación de píxeles
    pdfutil.py            # objetos y recursos del PDF
    texto.py              # edición y movimiento de texto: diff y planificación
    cirugia.py            # edición mínima: cambia glifos sueltos en los operadores Tj/TJ
    sin_glifo.py          # caracteres sin glifo en todo el documento
    codificacion.py       # cómo escribir con una fuente que ya está en el PDF
    fuentes.py            # índice de fuentes, instancias variables, clase tipográfica
    sustitucion.py        # reemplazar fuente en todo el documento
    imagenes.py, anotaciones.py, marcas.py, paginas.py, rangos.py, comprimir.py
    ocr.py, reconstruccion.py, redaccion.py, formularios.py, comparar.py
    escaneo.py, conversion.py, maquetado.py, exportar_editable.py, ortografia.py
    firma_digital.py, lotes.py
  dependencias/
    chequeo.py            # detector de dependencias + comandos PowerShell
    instalador.py         # instalación asistida (con confirmación)
    catalogo_fuentes.py   # fuentes libres (Google Fonts y archivos oficiales) y comerciales
  recursos/               # íconos Lucide, fuentes incluidas, diccionarios, logo
instalador/               # instalar.ps1, desinstalar.ps1, Instalar.cmd
herramientas/             # empaquetar.py, generar_iconos_app.py, catálogo de Google Fonts
tests/
  pdfs_prueba/            # PDFs de ejemplo sin datos reales (fuentes/: casos difíciles sintéticos)
  fuentes_prueba/         # fuente variable libre para las pruebas (Quicksand, OFL)
.github/workflows/release.yml   # arma y publica cada versión
```

## Arranque y pestañas

- **Arranque rápido**: lo pesado se importa dentro de las funciones que lo usan (numpy, pikepdf, fontTools,
  OpenCV, ocrmypdf, pyHanko, python-docx, spylls, urllib). `test_abrir_la_ventana_no_carga_lo_pesado` lo
  vigila. `pdf_studio/__init__.py` bloquea `pymupdf.layout` y `pymupdf4llm`, que pymupdf 2.0 importa solo y
  la app no usa (el .exe los excluye). Los íconos se dibujan bajo demanda (`_MotorIcono`).
- Lo que puede esperar (índice de fuentes, chequeo de dependencias, análisis de fuentes) se lanza con
  `_cuando_se_vea`: después de que el visor dibuja la primera página.
- Medir: `python herramientas/medir_arranque.py <pdf>` (doble clic → primera página, mediana de 5).
- **Instancia única** (`instancia.py`): mutex con nombre de Windows + `QLocalServer`. Los procesos nuevos
  pasan sus rutas a la instancia abierta y terminan sin cargar pymupdf. `PDFSTUDIO_SIN_INSTANCIA_UNICA=1`
  la desactiva. El .exe incluye QtNetwork por esto.
- **Pestañas** (`ui/pestanas.py`): un solo visor y un solo juego de paneles; cada `Pestana` guarda la vista,
  la redacción pendiente, el análisis de fuentes y el aviso de su documento. `self.documento` es siempre
  el de la pestaña activa. Solo se analiza la pestaña activa. Lo que llega con un diálogo o una tarea en
  curso espera (`abrir_varios`).

## Detector de dependencias (pieza central)

Se ejecuta al iniciar la app y al abrir cada PDF. Muestra un panel "Faltantes" con:
- Qué falta y para qué sirve.
- El comando de PowerShell listo, con botón **Copiar**.

Casos a cubrir:
- **Paquetes de Python faltantes** → `pip install <paquete>` (usando el pip del .venv).
- **Tesseract** → comando `winget` con el ID `tesseract-ocr.tesseract` (verificado en octubre de 2026; volver a verificar con `winget search tesseract` si se cambia). Incluir también el comando para descargar `spa.traineddata` a la carpeta tessdata con `Invoke-WebRequest`.
- **Ghostscript** (opcional, para compresión agresiva) → no está en winget: se baja el instalador oficial de la última versión.
- **Fuentes**: por cada fuente del PDF, informar si está **embebida completa**, **embebida como subconjunto** (recortada) o **no embebida**, y si está instalada en Windows. Un subconjunto se detecta también por su cobertura real de glifos, aunque no tenga el prefijo `ABCDEF+`.
  - Si es una fuente libre (Google Fonts u otras de licencia abierta): generar un comando de PowerShell que la descargue e instale para el usuario actual (`%LOCALAPPDATA%\Microsoft\Windows\Fonts` + registro en `HKCU`), sin pedir permisos de administrador.
  - Si es una fuente comercial o desconocida: no inventar descargas; ofrecer la sustitución (ver abajo).
  - Refrescar sin reiniciar: «Ya la instalé» y los botones de refrescar llaman a `IndiceFuentesSistema.recargar()`, que ignora la caché en disco, vuelve a leer las carpetas y dice qué encontró.

## Edición de texto

### Edición mínima (`core/cirugia.py`)

- Seleccionar un bloque de texto (fragmento, línea o párrafo) y **editarlo** o **moverlo** arrastrando.
- Se hace un diff entre el texto original y el editado (`cambios_de_texto`) y se reescriben **solo los tramos que cambiaron**. No se usa la redacción de MuPDF, porque corre las letras que siguen a lo borrado, ni se dibuja el texto nuevo al final del contenido, porque rompe el orden de lectura (buscar y copiar).
- `leer_flujo` alinea el contenido de la página con la traza de MuPDF: el glifo k de la traza es el código k del recorrido. Cada glifo queda ubicado en su operador Tj/TJ, con su código exacto. Si las cuentas no coinciden, devuelve None.
- Los glifos que no cambian quedan intactos (mismos códigos, fuente y posición). Los borrados se reemplazan por un ajuste numérico del TJ que conserva el avance. El texto nuevo va en el mismo lugar del contenido, con el estado de texto que ya rige; si lleva otra fuente, se cambia con `Tf` y se vuelve a la original.
- Al escribir un carácter se reutiliza el código con el que la página ya lo dibuja (`Flujo.codigo_usado`). Con una fuente nueva, la compensación usa los anchos que realmente quedaron en el PDF, que están redondeados.
- **Excepción acordada a «mismas posiciones»:** si el texto nuevo no entra o deja un hueco de más de 0,3 em (`HUECO_TOLERADO`), lo que sigue en la línea se corre lo justo, con sus mismos códigos y fuente, sin cruzar columnas (`GAP_COLUMNA`). Un hueco mayor partiría la línea al extraer el texto.
- La operación (`EditarMinimo`) declara solo las zonas tocadas y verifica que todos los demás glifos sigan en su lugar. Si no, no se aplica.
- **Método anterior** (`EditarTexto`: redacción sin tocar imágenes ni gráficos, y texto dibujado aparte): solo para mover texto, para reacomodar los renglones de un párrafo que se sale del margen y para texto dentro de Form XObject, o cuando `leer_flujo` no alinea.

### Elección de la fuente

- Solo importa lo que se dibuja de nuevo (`analizar_edicion`): lo que no cambia no puede bloquear la edición.
- Si la fuente embebida está recortada y faltan letras para el texto nuevo:
  1. Usar la fuente completa instalada en el sistema, si existe.
  2. Si no, ofrecer instalarla (detector de dependencias).
  3. Si no es posible, ofrecer la sustitución, siempre de la misma clase (sans, serif o mono), peso y estilo.
- Con otra fuente se cambia la palabra entera, para no mezclar dos fuentes dentro de una palabra.
- Un código con ancho 0 en `/Widths` no cuenta como disponible: es un código que el subconjunto no usa.
- **Clase tipográfica** (`clase_tipografica`), en orden de confianza: nombre de la familia, lo que declara el programa de fuente (ancho fijo, PANOSE), la forma real de los glifos (relación entre el pie y el asta) y, por último, los flags. Los flags valen poco: MuPDF marca como serif a casi toda fuente que no se declare sans.

### Fuentes variables

- El índice guarda una entrada por instancia (`FuenteSistema.variacion`): «Quicksand-Bold» es `Quicksand.ttf` con `wght=700`. Fuentes, de mayor a menor autoridad: instancias con nombre de `fvar`, pesos de `STAT` y pesos estándar dentro del rango del eje. No se inventan pesos fuera del rango ni cursivas que el archivo no tenga, y si también está la versión estática, se prefiere esa.
- Al escribir se genera la instancia estática (`instanciar_variable`) con su propio nombre interno y se embebe recortada. Puede tardar unos segundos con fuentes grandes, así que se hace recién al aplicar; para ver qué letras cubre alcanza con el archivo variable.
- La conversión a Word y PDF editable usa archivos tal cual: ignora las variables.

### Caracteres sin glifo

- Son los que el PDF muestra como un rectángulo `.notdef`. Muy común en listas exportadas desde Word, a menudo después de un número. Se detectan por posición con la traza (glifo 0) y se excluyen los espacios. Un código sin mapa se lee como U+FFFD (sin `TEXT_CID_FOR_UNKNOWN_UNICODE`), nunca como una letra inventada.
- **Marcas atómicas:** en el cuadro de edición cada uno se ve como `⟦U+F0B7⟧` en otro color (`EditorConMarcas`). El cursor no entra, una selección la toma entera y se borra entera o no se borra. Al aplicar se conserva el código original en la fuente original, sin pedir glifo nuevo y sin bloquear «Aplicar». Si el párrafo se reacomoda o el texto se mueve, se reescribe con ese mismo código.
- **Todo el documento** (`core/sin_glifo.py`, Editar → Sin glifo): cuántas veces aparece cada uno, su contexto típico y Conservar (por defecto) / espacio / tabulación / viñeta real / Quitar, con vista previa. Cambia solo cada carácter, sin mover nada, y si cambia otra cosa no deja aplicar. Lo que está dentro de un Form XObject se informa como omitido.

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
- **Fotos y escaneados**: importar fotos como PDF escaneado (detecta el papel) y convertir un escaneado a Word o PDF editable, con títulos, listas, tablas, encabezado y pie, y revisión de palabras dudosas.
- **Herramientas de lectura**: búsqueda, resaltado, notas y zoom.

## Versiones y publicación

- Versión en `pyproject.toml` y `pdf_studio/__init__.py` (van siempre iguales). `CHANGELOG.md` en español con lo agregado en cada una.
- Para publicar: subir la versión, actualizar el changelog, commit + push y crear el tag `vX.Y.Z`. El workflow `.github/workflows/release.yml` corre las pruebas, arma el `.exe` (con `--autoprueba`) y publica el release con el ZIP y `SHA256SUMS.txt`. Verificar que quede publicado con los dos archivos.
- El instalador reemplaza la versión anterior y conserva lo que hay en `%APPDATA%\PDFStudio`.
- Firma con SignPath Foundation: preparada y desactivada hasta que aprueben el proyecto (ver `CODE_SIGNING.md`).

## Pruebas

`tests/pdfs_prueba/` tiene un set variado sin datos reales: escaneado, fuentes recortadas, formulario, tablas, imágenes de fondo, y en `fuentes/` casos difíciles sintéticos (Type1, Type3, sin cmap, lista numerada de Word con un símbolo sin glifo). Todo cambio lleva tests automáticos (pytest) y el principio 3 (fidelidad visual) se valida automáticamente.

`tests/robustez_pdfs_reales.py` corre todas las operaciones sobre copias de los PDFs de `C:\Proyectos\pdfs-reales`. Esos PDFs nunca van al repo, y sus informes solo muestran el nombre del archivo, la prueba y el tipo de error, sin contenido.

## Forma de trabajar

- Al terminar un cambio, resumir qué se hizo y cómo probarlo, y esperar la confirmación del usuario.
- Antes de instalar algo, mostrar el comando y explicar para qué sirve.
- Si una función no puede garantizar la fidelidad en un caso concreto, avisarle al usuario en la interfaz en vez de guardar un resultado defectuoso.

## Diseño de interfaz

Reglas para todas las fases (implementadas en `ui/tema.py`, `ui/iconos.py`, `ui/cinta.py`):

- **Estética moderna y minimalista.** Superficies planas, esquinas redondeadas, separadores finos, sin decoraciones innecesarias. Un solo color de acento (azul) para selección, foco y acción principal.
- **Temas claro y oscuro**, con selector y opción "según Windows" (sigue el cambio en vivo). Los colores salen SOLO de las paletas de `ui/tema.py`; ningún widget hardcodea colores.
- **Contraste WCAG AA** verificado por tests: texto ≥ 4.5:1 sobre cualquier superficie; contornos de controles y anillo de foco ≥ 3:1.
- **Íconos en todas las acciones**, del set Lucide (SVG en `pdf_studio/recursos/iconos`, licencia ISC), coloreados según el tema. Si una acción nueva necesita un ícono que no está, bajarlo del mismo set (tag fijo) y agregarlo al proyecto. Nunca mezclar otros sets.
- **Tooltips con nombre + atajo de teclado** (y la descripción si ayuda). Deshacer/Rehacer muestran en el tooltip qué acción deshacen.
- **Organización por pestañas** (Ver, Editar, Firmar, Páginas, Herramientas) con íconos y texto debajo; acceso rápido fijo a Abrir, Guardar, Más opciones, Deshacer y Rehacer. Cada acción nueva va a la pestaña que corresponda, nunca en una fila larga de texto.
- **Barra lateral** con selector de íconos (Páginas, Fuentes, Faltantes, Formulario) y **barra de estado discreta** (texto chico y suave; los faltantes como indicador clickeable).
- **Paleta de comandos (Ctrl+K):** toda acción nueva se crea con `_accion(...)` para que aparezca ahí, con su grupo.
- **Accesibilidad:** todo usable con teclado (Tab, Alt+1…5 para pestañas, F6 entre regiones, atajos), foco siempre visible, nombres accesibles en botones con solo ícono, y tamaño de interfaz escalable (Ctrl+Alt+= / - / 0) que afecta texto e íconos.
- **Botón principal** de cada diálogo con `setProperty("primario", True)`; lo destructivo o irreversible nunca es el botón por defecto.
- **Anotaciones discretas:** lo que la app dibuja sobre la página (notas, marcas) no debe tapar el contenido.
- **Logo:** `recursos/logo.svg` (fuente) → `logo.ico` multi-resolución y PNGs con `herramientas/generar_iconos_app.py`. Se usa en la ventana, la barra de tareas y "Acerca de".
