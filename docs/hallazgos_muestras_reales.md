# Hallazgos sobre las fotos reales (muestras/marcas_escaneadas/)

Notas técnicas de analizar los 5 documentos reales subidos: 2 operaciones
completas (guía + certificado, una con anexo). Todo lo de acá ya se probó
contra archivo real, no es teoría.

## Cómo son los documentos

- **Fotos de tablet** (Xiaomi Redmi Pad Pro), no escáner plano. ~2000×2900 px,
  JPG, sin capa de texto. Equivale a ~240-250 dpi efectivos: por debajo de los
  400+ recomendados, pero **el trazo sobrevive** a la limpieza (verificado).
- **Papel encarpetado, no suelto.** Se ven los anillos de la carpeta al margen
  izquierdo, curvatura de página, manchas, una esquina rota. Los anillos
  llegan a **tapar el borde de la tabla en algunas filas** — ver más abajo,
  esto rompía la detección de grilla.
- **Distorsión de perspectiva real** en al menos 2 de las 5 fotos (páginas de
  Certificado): el rectángulo de la hoja no es un rectángulo en la foto, tiene
  forma de trapecio leve. El enderezado actual (`enderezar`, basado en
  Hough) sólo corrige rotación en el plano, no esto. Con qué frecuencia pasa
  en el lote completo, no lo sé todavía.
- **Tres documentos por operación, no dos:**
  1. **Guía de Traslado** (Rubro 1 a 7): vendedor, Rubro 2 con grilla de
     marcas (2×3: 1 dominante + 5 complementarias, siempre en ese orden fijo
     — columna 1 fila 1 es la dominante, las otras 5 son complementarias,
     aunque el encabezado "Dominante" sólo rotule visualmente esa celda),
     comprador.
  2. **Anexo** — grilla de 5×8 = 40 casillas, para cuando las marcas superan
     las 6 de Rubro 2. Las casillas sobrantes vienen anuladas con "X". No
     todas las guías tienen anexo (si entran en las 6 de Rubro 2, no hace
     falta).
  3. **Certificado Sanitario** (otro membrete SENACSA, "S-E-N-A-C-S-A" en
     letras separadas): datos de vacunación, georreferenciación, categoría de
     animales, y la marca dominante sola (sin complementarias). Redundante
     con la Guía para el catálogo de marcas — prioridad baja.
- **El comprador se repite**: en las dos operaciones de la muestra, PIEDRA
  ALTA S.A. INMOBILIARIA. Confirma el caso de uso: se acumulan marcas de
  distintos vendedores con los que la empresa opera.
- **El total de marcas cuadra**: Rubro 2 (6) + Anexo (9) = 15, y el
  formulario dice "SON: ... QUINCE MARCAS". Sirve como chequeo de integridad
  al procesar un lote: si no cierra, algo se perdió.

## Bug de detección de grilla: encontrado y corregido

`detectar_celdas` (en `marcas/vectorizacion/segmentar.py`) buscaba casillas
como "huecos" cerrados dentro de la máscara de líneas. Funciona perfecto
para una plantilla propia (`generar_plantilla_captura`: cada casilla es un
rectángulo independiente, con espacio alrededor). **Falla en una tabla
continua real**, donde las celdas comparten el borde: si el borde exterior
de la tabla se interrumpe en algún tramo —un anillo de carpeta tapándolo,
una mancha, una esquina rota—, esa fila o columna deja de ser un hueco
cerrado y se pierde entera.

Verificado sobre la foto real del Anexo (`IMG_20260830_212933.jpg`): la
primera columna (2 marcas reales, "ZE" y "SS") y la última fila se perdían
por completo — 32 de 40 casillas, con el resto corrido de columna.

**Arreglado.** Se agregó un segundo método (`_detectar_celdas_por_lineas`)
que ubica las líneas de la grilla por su *posición* (proyección de la
máscara, agrupando picos y quedándose con la corrida más larga de líneas
parejas) en vez de depender de que el hueco esté perfectamente cerrado. Una
línea rota en un tramo igual deja señal en el resto de su largo, así que
sobrevive. `detectar_celdas` prueba primero el método de huecos (más
simple) y sólo recurre al de líneas si el resultado da una grilla dispareja.

Con el arreglo: **40/40 casillas**, exactas, incluida la columna y fila que
antes se perdían. Verificado también que el recorte de cada celda ya no
arrastra un resto del borde compartido (antes aparecía como un segmento
espurio arriba del trazo real, que además le sumaba un componente conexo de
más a `limpiar_marca`).

Dos pruebas de regresión nuevas en `tests/test_vectorizacion.py`, con una
tabla continua sintética (no con las fotos reales, para no atar la suite a
documentos con datos de terceros).

## `limpiar_marca` sobre trazos reales: bien

Las tres marcas reales probadas ("ZE", "R", "SS") salen limpias, centradas,
sin ruido, con un solo componente conexo cada una — pese a ser cursiva real
(trazos curvos, conectados, mucho más irregulares que mis formas sintéticas
de prueba). El pipeline de limpieza no necesitó cambios para esto una vez
resuelto el problema de origen (el resto de borde).

Las casillas anuladas con "X" quedan con más tinta (13-15%) y **un solo
componente** (la X es un trazo continuo que se cruza a sí mismo), contra
6-8% y 1 componente en las marcas reales de esta muestra. Es un punto de
partida para diferenciar "X de anulación" de "marca real", pero con sólo 2
X y 3 marcas de referencia no alcanza para fijar un umbral — hace falta más
muestra.

## Códigos de barras y QR: hallazgo a medias, no bloqueante

- El **QR de las páginas de Certificado decodifica perfecto** con
  `zxing-cpp` (no con el detector nativo de OpenCV, que no encontró nada):
  trae guía, código de propietario, de establecimiento, cantidad de
  animales y coordenadas, todo en un string estructurado. Sería la fuente
  más confiable para identificar cada operación, mejor que OCR.
- El **QR del Anexo** decodifica *casi* — encuentra la estructura pero el
  checksum falla por unos pocos módulos mal leídos ("Guia:9094w260" en vez
  de "90947260"). Muy cerca; puede que mejore con más resolución en el
  lote real, o con una corrección de nitidez más agresiva antes de decodificar.
- El **código de barras 1D de la Guía principal** ("GE290947260") no
  decodificó con ningún ajuste probado (recorte, escala, binarizado). No es
  bloqueante: el mismo texto está impreso debajo del código de barras, en
  fuente limpia — buen candidato para OCR directo, sin depender del código
  de barras.

No urgente de resolver ahora. Cuando se construya el extractor por lotes,
usar QR cuando decodifique (Certificado, a veces Anexo) y OCR como
respaldo en todos los casos.

## Ubicar la grilla del Rubro 2: necesita acotar la región primero

A diferencia del Anexo (una sola tabla grande en toda la página, sin
competencia), la página de la Guía principal tiene **varias tablas chicas**
cerca de Rubro 2 (el encabezado con N° de guía/CI-RUC, Rubro 3, Rubro 4).
`detectar_celdas` sobre la página completa se engancha con la tabla que
tenga más filas parejas —no necesariamente Rubro 2—, porque el criterio es
"la corrida más larga y pareja de líneas", y una tabla de datos con muchas
filas gana esa competencia aunque no sea la que importa.

**Antes de extraer Rubro 2 hace falta localizar esa región primero** (lo más
robusto: buscar el texto "Rubro 2", "Dominante" o "Complementarias" por OCR
y acotar el recorte a partir de ahí, no adivinar coordenadas fijas — la
guía real y la oficial (GE291181750) ya mostraron que el layout se corre
unos puntos entre páginas).

## Qué falta para el extractor por lotes

En orden de lo que bloquea a lo que no:

1. **Ubicar la región de Rubro 2** en la página de la Guía (recién explicado).
2. **OCR** para los campos de texto: N° de Orden, CI/RUC, nombre del
   vendedor, del comprador, establecimiento — ninguna de estas páginas
   trae texto seleccionable.
3. **Clasificador "X anulada" vs. "marca real"** en una celda, con más
   muestra para fijar el umbral (tinta% + componentes ya dan una pista, pero
   2 ejemplos no alcanzan).
4. **Agrupar páginas sueltas en una operación**: dado que el ZIP trae
   imágenes sueltas en orden (anverso/reverso), la agrupación por posición
   en la secuencia es más confiable que only-OCR; cruzar contra el N° de
   guía (leído por OCR o QR) como verificación.
5. Corrección de perspectiva, si el volumen real de fotos "en trapecio" lo
   justifica (no lo sé todavía con esta muestra de 5).

Nada de esto es especulativo: cada punto sale de haber corrido código contra
estos documentos reales, no de suponer cómo serían.
