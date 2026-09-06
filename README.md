# Marcas de ganado — Piedra Alta S.A.C.I.

Sistema para **digitalizar marcas dibujadas a mano**, mantenerlas en un
catálogo y armar **planillas PDF** donde cada marca se ubica en una casilla.

El punto de partida es el problema real: hay más de 500 marcas dibujadas a
mano en papel y hay que convertirlas en imágenes limpias, reutilizables e
imprimibles.

---

## La respuesta corta a "¿cómo vectorizo 500 dibujos?"

No se dibuja cada marca de nuevo, ni se recorta a mano en un editor: se
escanean **hojas completas con varias marcas** y un pipeline por lotes hace el
resto. Para cada marca:

| Paso | Qué hace | Por qué |
|---|---|---|
| 1. Enderezado | Detecta las líneas de la grilla y corrige la inclinación | Un escaneo torcido arruina el recorte de las casillas |
| 2. Segmentación | Encuentra cada casilla y la recorta | Una pasada por hoja en lugar de 500 recortes a mano |
| 3. Fondo plano | Divide la imagen por su propio desenfoque | Elimina la sombra del lomo y la luz despareja |
| 4. Binarización adaptativa | Separa tinta de papel con umbral local | Un umbral global (Otsu) falla si la hoja tiene sombras |
| 5. Limpieza | Saca el recuadro impreso, cierra micro-cortes y descarta motas | Deja sólo el trazo de la marca |
| 6. Normalización | Recorta al trazo, lo centra y lo escala a un lienzo de 1024 px | Todas las marcas quedan del mismo tamaño relativo |
| 7. PNG RGBA | Trazo negro, **fondo transparente** | En el PDF la marca se apoya sobre la casilla sin taparla |
| 8. SVG (potrace) | Vectoriza el trazo limpio | El original queda libre de resolución: sirve para un A4 o un cartel |

Rendimiento medido en este entorno (4 núcleos): **~60 ms por marca**, es decir
unos **30 segundos para las 500**, PNG y SVG incluidos. El cuello de botella no
es la computadora sino el escaneo y la revisión visual.

La guía operativa completa —resolución, formato, cómo preparar las hojas, qué
hacer cuando una marca sale mal— está en
**[docs/guia_digitalizacion.md](docs/guia_digitalizacion.md)**.

## Estampado sobre la Guía de Traslado oficial

El formulario de SENACSA se descarga ya numerado (N° de orden, código de
barras y QR distintos en cada descarga), así que **no se regenera: se le
superponen las marcas**. El PDF descargado se sube en cada trámite; el sistema
no guarda plantillas.

```bash
python -m marcas guia --pdf GE291181750.pdf --inspeccionar   # ver qué trae
python -m marcas guia --pdf GE291181750.pdf --limite 62      # estampar
```

Las casillas **no están calibradas a mano**: se leen de los rectángulos
vectoriales del propio PDF, página por página. Eso importa, porque en la guía
real la grilla de la primera hoja de anexo está 7 pt más abajo que la de las
demás. El sistema respeta las casillas ya ocupadas (la marca dominante, las
anuladas con "X") y estampa la misma marca en la misma posición de las cuatro
copias. Detalle completo en
**[docs/guia_oficial.md](docs/guia_oficial.md)**.

### PNG y SVG, no uno u otro

Pediste PNG, y el PNG es lo que consume la planilla. Pero el pipeline genera
además un SVG por marca porque **cuesta casi nada y evita rehacer el trabajo**:
el PNG queda atado al tamaño con que se generó, mientras que del SVG se
rasteriza después cualquier PNG (más grande, con otro grosor, en otro color)
sin volver a tocar el papel. El SVG es el original de archivo; los PNG son
copias de trabajo.

---

## Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
sudo apt install potrace          # opcional: sólo para generar los SVG
```

Sin `potrace` el pipeline funciona igual y produce únicamente los PNG.

## Probarlo ahora mismo

```bash
python -m marcas demo
```

Genera hojas escaneadas sintéticas (con inclinación, sombra y ruido, como un
escaneo real), las procesa, carga el catálogo y arma los tres PDF en
`datos/salida/`. Sirve para ver el resultado antes de tener escaneos propios.

## Cargar la base con las marcas que ya tenemos

Una marca en el catálogo tiene dos mitades que llegan por separado, y **no
hace falta esperar a tener la imagen para cargar la primera**:

* los **datos** — código oficial, propietario, establecimiento — están en una
  planilla del registro, no en el escaneo;
* la **imagen** sale de digitalizar el papel (`marcas procesar`).

Las dos se unen por el código de la marca. Si el registro ya existe en Excel o
CSV, se carga tal cual, aunque todavía no haya un solo escaneo:

```bash
python -m marcas datos registro.xlsx
```

No hace falta acomodar la planilla a un formato exacto: las columnas se
reconocen por el nombre del encabezado ("Nº de Marca", "CI/RUC", "Nombre y
Apellido / Razón Social"…), tolerando tildes y mayúsculas, y las que no se
reconocen se listan y se ignoran en vez de hacer fallar la carga. Si no hay
una planilla armada, `marcas plantilla-datos` deja un CSV de ejemplo con las
columnas que se entienden.

Las marcas cargadas así quedan **sin imagen** hasta que se digitalicen; el
catálogo las distingue (`marcas estado`, o el filtro *sin digitalizar* del
selector web) para saber cuánto falta escanear. Cuando llegan los escaneos,
`marcas procesar` + `marcas importar` (o el atajo `marcas cargar`, que hace
las dos cosas de una) las completa por código, sin duplicar ni pisar los
datos ya cargados.

```bash
# Todo junto: datos del registro + digitalizar + catálogo
python -m marcas cargar datos/escaneos --datos registro.xlsx --codigos codigos.csv
```

`--codigos` es el CSV que asigna, por hoja/fila/columna, el código real de
cada casilla escaneada (lo mismo que acepta `marcas procesar`); sin él, cada
marca queda con el código automático de su posición y se renombra después,
a mano, desde el selector web.

### Cargar el resultado de un lote de extracción (varias marcas por operación)

Si las marcas ya vienen recortadas y clasificadas por otro proceso —por
ejemplo, la salida de extraer datos de guías de traslado con varias marcas por
operación (una dominante y N complementarias) más los recortes de cada una—
`marcas datos` reconoce además estas columnas:

| Columna | Qué es |
|---|---|
| `tipo` | `dominante` o `complementaria` |
| `numero_guia` | identificador de la operación de origen (no es el código de la marca; sirve para agrupar y para trazabilidad) |
| `archivo_imagen` | ruta al PNG ya recortado de esa marca, **relativa a la ubicación de la planilla** |

```bash
python -m marcas datos marcas_extraidas.csv
```

El propietario se identifica por `documento` (CI/RUC): varias marcas de la
misma operación, o de operaciones distintas del mismo vendedor con el nombre
escrito distinto, quedan bajo un solo propietario. Si una imagen referenciada
no aparece en disco, la fila se carga igual (con sus datos de texto) y queda
un aviso en vez de fallar todo el lote.

## Flujo de trabajo real

```bash
# 1. Imprimir las hojas donde se van a dibujar las marcas (100%, sin ajustar a página)
python -m marcas captura --hojas 25 --salida datos/salida/plantilla_captura.pdf

# 2. Dibujar una marca por casilla y escanear a 600 dpi, escala de grises
#    -> dejar los archivos en datos/escaneos/ (PNG, TIFF, JPG o PDF escaneado)

# 3. Digitalizar el lote: PNG + SVG + manifiesto.csv
python -m marcas procesar

# 4. Revisar: hoja de contactos con todas las marcas del lote
python -m marcas control
#    corregir en datos/marcas/manifiesto.csv los códigos y lo que haga falta

# 5. Cargar el catálogo
python -m marcas importar

# 6. Elegir marcas y generar la planilla
python -m marcas web                     # selector visual en el navegador
python -m marcas planilla --todas        # o directo por línea de comandos

# 7. Estampar las marcas sobre una guía oficial descargada
python -m marcas guia --pdf GE291181750.pdf --codigos H01-F01C01,H01-F01C02
```

### Documentos escaneados en PDF

Las marcas suelen llegar dentro de PDF escaneados, no como imágenes sueltas.
`marcas procesar` los acepta igual: de cada página saca **la imagen original
del escáner** cuando el escaneo entró como una sola imagen a página completa,
así se trabaja con los píxeles del escáner y no con una re-digitalización. Si
la página no es un escaneo (un PDF generado, una plantilla), la rasteriza a
400 dpi, ajustable con `--dpi`. Cada página se numera sola: `documento-p01`,
`documento-p02`…

### El selector visual

`python -m marcas web` abre en `http://127.0.0.1:5000` una grilla con todas las
marcas: se tildan las que van, se ajusta título, cantidad de columnas y filas y
casillas vacías, y el botón devuelve el PDF. Cada selección queda registrada en
la tabla `planillas`, así que una planilla se puede reimprimir idéntica.

### Comandos

| Comando | Para qué |
|---|---|
| `marcas captura` | PDF en blanco con casillas numeradas, para dibujar |
| `marcas datos` | Planilla del registro (CSV/Excel) → catálogo |
| `marcas plantilla-datos` | CSV de ejemplo con las columnas que se reconocen |
| `marcas procesar` | Escaneos → PNG + SVG + `manifiesto.csv` |
| `marcas importar` | `manifiesto.csv` → catálogo SQLite |
| `marcas cargar` | Datos + digitalización + catálogo, en un paso |
| `marcas listar` | Ver el catálogo, con filtros (`--sin-imagen`, `--estado`…) |
| `marcas control` | Hoja de contactos para revisar un lote |
| `marcas planilla` | PDF general con las marcas en casillas |
| `marcas guia` | Estampar marcas sobre la Guía de Traslado oficial |
| `marcas web` | Selector visual + generación del PDF |
| `marcas estado` | Resumen del catálogo |
| `marcas demo` | Recorrido completo con hojas sintéticas |

Cada uno acepta `--help`.

---

## Estructura

```
marcas/
├── config.py               parámetros (resolución, umbrales, tamaño del lienzo)
├── cli.py                  línea de comandos
├── db.py                   catálogo SQLite
├── vectorizacion/
│   ├── segmentar.py        enderezado y detección de casillas
│   ├── limpiar.py          binarización, limpieza y normalización
│   ├── trazar.py           PNG → SVG con potrace
│   └── pipeline.py         procesamiento por lotes + manifiesto
├── pdf/
│   ├── planilla.py         planilla general, hoja de control, plantilla de captura
│   └── guia.py             estampado sobre la Guía de Traslado oficial
└── web/                    selector visual (Flask)
scripts/
├── generar_hoja_demo.py     hojas escaneadas sintéticas para pruebas
├── generar_guia_demo.py     guía de traslado sintética para pruebas
├── extraer_muestra.py       recorta una muestra de un PDF escaneado grande
└── muestrear_zip.py         saca una muestra de un ZIP grande, sin descomprimirlo
datos/                        escaneos, imágenes generadas, base y PDF (fuera del repo)
muestras/                     documentos reales de ejemplo (ver muestras/README.md)
```

## Datos

SQLite (`datos/marcas.db`). Las imágenes **no** se guardan dentro de la base:
se guarda la ruta al PNG y al SVG, así la base queda liviana y los archivos
siguen sirviendo por fuera del sistema.

- **`marcas`** — código, descripción, propietario, rutas de las imágenes, origen
  (hoja/fila/columna), métricas de calidad, `sha1` y estado
  (`activa` / `revisar` / `baja`).
- **`propietarios`** — dueño, documento, establecimiento, localidad.
- **`planillas` / `planilla_items`** — qué marcas entraron en cada PDF y en qué
  orden.

Reprocesar una hoja y volver a importar **actualiza** las filas por código: no
duplica marcas ni pisa el propietario ni la descripción cargados a mano.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Las pruebas generan hojas escaneadas y guías de traslado sintéticas, así que
no dependen de documentos reales. Verifican el pipeline completo: enderezado,
detección de casillas, transparencia del PNG, trazado del SVG, importación
idempotente al catálogo, paginación de los PDF y el estampado sobre la guía
(casillas anuladas respetadas, las cuatro copias iguales, el resto del
documento intacto).

## Qué falta

- **Confirmar si las casillas del Rubro 2 de la guía también llevan marcas.**
  Hoy se llenan sólo los anexos; ver *Pendiente de definir* en
  [docs/guia_oficial.md](docs/guia_oficial.md).
- **Búsqueda por similitud** (marca dibujada o cargada → marcas parecidas):
  el enfoque está en [docs/arquitectura.md](docs/arquitectura.md).
- Cargar los propietarios (hoy se editan de a uno desde el selector o por SQL);
  lo natural es un import desde planilla de cálculo.
- Búsqueda "por parecido" para detectar marcas duplicadas o muy similares al
  dar de alta una nueva: con las métricas que ya guarda el catálogo
  (aspecto, tinta, componentes) alcanza para una primera criba.
- Usuarios y permisos si el selector se publica más allá de la máquina local.
