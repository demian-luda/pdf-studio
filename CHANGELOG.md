# Cambios de PDF Studio

Todos los cambios importantes de cada versión. Las versiones siguen [SemVer](https://semver.org/lang/es/).

## [0.3.1] - 2026-11-06

### Corregido

- **Memoria al cerrar pestañas.** Una pestaña cerrada quedaba retenida con su documento: con 20 PDFs
  abiertos y cerrados tres veces quedaban 57 documentos en memoria y la app crecía unos 8 MB por
  vuelta. Ahora se liberan todos y, al cerrar la última pestaña, también el caché de MuPDF.

## [0.3.0] - 2026-11-06

### Agregado

- **Pestañas.** Varios PDFs abiertos en la misma ventana. Con PDF Studio abierto, un doble clic en otro
  PDF lo suma como pestaña nueva, en lugar de abrir otra ventana; abrir varios seleccionados o soltarlos
  sobre la ventana también. Las pestañas se cierran con su botón o Ctrl+W (con aviso si hay cambios sin
  guardar), se reordenan arrastrándolas y se recorren con Ctrl+Tab y Ctrl+Shift+Tab.
- **Retomar la lectura.** Al volver a abrir un PDF, aparece en la última página y con el zoom que tenía.
  Se recuerdan los últimos 300 archivos, solo en esta PC (`%APPDATA%\PDFStudio\posiciones.json`).

### Cambiado

- **Arranque más rápido.** Desde el doble clic hasta ver la primera página: de 1,65 s a 0,8 s. Un segundo
  PDF con la app abierta se ve en 0,3 s. Los íconos se dibujan recién cuando hacen falta, numpy, pikepdf y
  fontTools se cargan al usarlos, y el análisis de fuentes, el chequeo de dependencias y el índice de
  fuentes del sistema empiezan después de mostrar la primera página.
- **Notas de cada versión.** Las notas del release salen solas de la sección de esta versión en el
  CHANGELOG, más los pasos de instalación.

### Corregido

- Al arrancar con un PDF, el análisis de fuentes se lanzaba dos veces, y el segundo no mostraba el panel
  Faltantes aunque faltara algo importante.

## [0.2.0] - 2026-11-03

### Agregado

- **Edición mínima de texto.** Al editar un fragmento, una línea o un párrafo, se compara el texto
  original con el nuevo y se cambian solo los tramos distintos, en sus propios operadores. Lo que no
  cambia conserva sus códigos, su fuente y su posición, y el texto nuevo queda en su lugar de lectura:
  buscar y copiar siguen funcionando.
- **Caracteres sin glifo conservados.** Los que el PDF muestra como un rectángulo (muy común en listas
  exportadas desde Word) se ven en el cuadro de edición como una marca, por ejemplo `⟦U+F0B7⟧`, que se
  borra entera o no se borra. Si no la tocás, se conserva con su código y su fuente originales, y ya no
  bloquea «Aplicar». También se ofrece «Reemplazar por espacio» o «Quitar».
- **Caracteres sin glifo en todo el documento** (Editar → Sin glifo). Muestra cuántas veces aparece cada
  uno y su contexto típico (por ejemplo, «siempre después de un número»). Se puede elegir Conservar,
  Reemplazar por espacio, tabulación o viñeta real, o Quitar, con vista previa antes y después. Solo deja
  aplicar si el resto de cada página queda idéntico.
- **Fuentes variables.** Una fuente como `Quicksand.ttf` (con eje de peso) se reconoce por cada uno de sus
  pesos: «Quicksand-Bold» es esa fuente con `wght=700`. Al escribir con ella se genera y se embebe la
  versión estática de ese peso.
- **Refrescar las fuentes sin reiniciar.** «Ya la instalé» y los botones de refrescar (cuadro de edición,
  paneles Fuentes y Faltantes) vuelven a leer las fuentes instaladas y dicen qué encontraron.
- **Publicación automática.** Un workflow de GitHub arma, prueba y publica cada versión desde el código
  del tag. La firma con SignPath Foundation quedó preparada, pero sigue desactivada hasta que aprueben
  el proyecto.

### Cambiado

- Si el texto nuevo no entra o deja un hueco de más de 0,3 em, lo que sigue en la línea se corre lo justo,
  con sus mismos códigos y fuente, sin cruzar columnas. Si el cambio es menor, no se mueve nada.
- Para elegir la fuente solo cuenta lo que se dibuja de nuevo: lo que no cambió ya no puede bloquear la edición.
- Si la fuente original no tiene una letra nueva, se cambia la palabra entera, para no mezclar dos fuentes
  dentro de una misma palabra. El resto de la línea sigue con su fuente original.
- Al mover texto, un carácter que el PDF no mapea se conserva con su código original, en vez de impedir
  la operación.
- «Aplicar» sin haber cambiado el texto ya no toca el documento.

### Corregido

- Una fuente sans sin clase declarada (como Quicksand) podía recibir una sustituta serif. Ahora, si la
  fuente no declara nada, se mide la forma real de sus letras.
- Un solo carácter sin código hacía descartar la fuente original del PDF.
- Un código sin mapa se leía como una letra inventada (por ejemplo, «É»), que podía terminar escrita en
  el documento. Ahora se lee como U+FFFD.
- Un código con ancho 0 en la fuente se daba por disponible, aunque el subconjunto no lo usara.
- Para escribir un carácter se podía elegir un código sin glifo cuando varios mapeaban al mismo. Ahora se
  reutiliza el que la página ya usa.
- El comando de instalación de fuentes ya no pide reiniciar la app.

## [0.1.0] - 2026-10-21

Primera versión pública: edición y movimiento de texto, reemplazo de fuentes en todo el documento, firmas
en imagen y firma digital con certificado, OCR, conversión de escaneados a Word editable, páginas,
compresión, marca de agua, numeración, redacción real, formularios, comparar versiones y procesamiento
por lotes.

[0.3.1]: https://github.com/demian-luda/pdf-studio/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/demian-luda/pdf-studio/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/demian-luda/pdf-studio/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/demian-luda/pdf-studio/releases/tag/v0.1.0
