# Guion de la demo (≈ 15 minutos)

**Idea central a transmitir:** *completar una Guía de Traslado con las marcas ya no es redibujar a mano
en papel: se dibuja (o se elige) una vez, el sistema las ubica en las casillas libres de las cuatro
copias, y todo lo que se emite queda archivado y consultable.*

> Material: `Guia_DEMO_anonimizada.pdf` (carpeta `Piedra Alta\demo\`). Es una guía oficial real con el
> comprador, los números y los códigos de barras/QR reemplazados; la **Dominante está libre** a propósito
> para poder dibujarla. Cuenta de demo con rol **operador**. No uses guías reales delante del público.

## Antes de empezar (10 min antes)

1. Abrir el sitio de demo ya con la sesión iniciada y probar **una emisión completa** (ver
   [lista_pendientes_demo.md](lista_pendientes_demo.md), sección *Ensayo*).
2. Tener a mano: la guía anonimizada en el escritorio, y una tablet o teléfono con la sesión iniciada.
3. Cerrar pestañas con datos reales. Modo claro (se ve mejor proyectado).

## Guion

| # | Minutos | Qué mostrar | Qué decir / hacer |
|---|---|---|---|
| 1 | 1 | **El problema** (sin pantalla) | Cada guía de SENACSA exige las marcas del ganado en el Rubro 2 y, si sobran, en un Anexo, **en las cuatro copias**. Hoy es manual, lento y con errores de casilla. |
| 2 | 5 | **Emitir PDF** (el corazón) | Arrastrar `Guia_DEMO_anonimizada.pdf`. **Señalar:** se rellenaron solos N.º de guía, vendedor, comprador, cantidad (52) y la *Compra/Venta*. Los chips muestran el cupo real: *Rubro 2: 0/5 · Anexo: 0/60* y que la guía declara **66 marcas**. |
| | | Dibujar la **dominante** | En el cuadro, dibujar una marca simple → *Usar como dominante*. Aparece estampada en la vista previa al instante. |
| | | Dibujar 2 **complementarias** | Dibujar y *Agregar*. Cada una entra en la siguiente casilla libre. Mostrar el chip *Marcas: x de 66*. |
| | | **Catálogo** | *Buscar en el catálogo* → buscar por código (p. ej. `M-00332`) y sumar 4–5 marcas reales. Ver cómo pasan del Rubro 2 al **Anexo** al llenarse. |
| | | **Generar PDF** | Descarga el PDF. Abrirlo y mostrar página principal y Anexo. Remarcar que se aplicó **igual en las cuatro copias**. |
| 3 | 2 | **Documentos → Archivo** | El documento recién emitido ya está: fecha, quién lo emitió, todos los datos y las marcas con las que se estampó. *Descargar PDF* de nuevo. Mostrar filtros por tipo y fecha. Si se edita la operación después, el archivo conserva lo que se emitió. |
| 4 | 3 | **Marcas → galería** | Catálogo visual de ~3000 marcas extraídas de los escaneos. Buscar por código; abrir una ficha. |
| | | **Marcas → Revisión** | *Por revisar*: el sistema detectó rayas de anulación, líneas de tabla y sellos que **no son marcas**. Filtrar por motivo → *Dar de baja las visibles* → mostrar **Deshacer**. Decir: *«es una ayuda; la decisión es de una persona, y nada se borra»*. |
| 5 | 1 | **Roles** | Tres roles: administrador, operador (emite y edita) y consulta (solo lee). Ocultar ≠ borrar: son documentos con valor legal. |
| 6 | 1 | **Tablet / teléfono** | Dibujar una marca con el dedo en la tablet. *(Ensayar antes: ver pendientes.)* |
| 7 | 2 | **Cierre** | Lo que sigue (ver abajo) y preguntas. |

## Lo que sigue (hoja de ruta honesta)

Se puede mencionar como **próximos pasos**, no como funcionalidades existentes:

- Cargar el histórico de ~764 páginas escaneadas como archivo consultable y asociar cada documento con su propietario (el QR del Certificado Sanitario permite hacerlo).
- Búsqueda por similitud: dibujar una marca y encontrar las parecidas del catálogo.
- Guardar de forma permanente las marcas dibujadas durante el uso real para ir armando el catálogo reutilizable.

## Si algo falla en vivo

| Problema | Qué hacer |
|---|---|
| La vista previa tarda | Con muchas marcas puede tardar unos segundos; decirlo y seguir hablando. |
| El PDF no se lee | Usar el PDF de respaldo en la misma carpeta. Si no, mostrar las capturas que tomes en el ensayo. |
| Se cae la conexión | Plan B: el video grabado del recorrido (grabarlo en el ensayo) o las capturas. |
| Pregunta incómoda de datos reales | *«Esta demo usa una guía anonimizada; los datos reales están en un entorno aparte.»* |

## Preguntas que probablemente hagan

- **¿Dónde se guardan los PDF?** En la base de datos del sistema; el respaldo diario guarda los datos de cada emisión (los PDF viven en la base).
- **¿Se puede usar desde el teléfono?** Sí, con el menú adaptado; dibujar con el dedo funciona. Falta ensayarlo en más dispositivos.
- **¿Qué pasa si la guía trae más marcas que casillas?** El sistema avisa y bloquea: «sobran N marcas; repartir en dos guías».
- **¿Y si el formulario cambia?** Detecta las casillas por las líneas reales del PDF, no por posiciones fijas; se probó con una guía oficial real. Un formulario muy distinto requeriría ajustar.
