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

## Flujo de trabajo real

```bash
# 1. Imprimir las hojas donde se van a dibujar las marcas (100%, sin ajustar a página)
python -m marcas captura --hojas 25 --salida datos/salida/plantilla_captura.pdf

# 2. Dibujar una marca por casilla y escanear a 600 dpi, escala de grises, PNG o TIFF
#    -> dejar los archivos en datos/escaneos/

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
```

### El selector visual

`python -m marcas web` abre en `http://127.0.0.1:5000` una grilla con todas las
marcas: se tildan las que van, se ajusta título, cantidad de columnas y filas y
casillas vacías, y el botón devuelve el PDF. Cada selección queda registrada en
la tabla `planillas`, así que una planilla se puede reimprimir idéntica.

### Comandos

| Comando | Para qué |
|---|---|
| `marcas captura` | PDF en blanco con casillas numeradas, para dibujar |
| `marcas procesar` | Escaneos → PNG + SVG + `manifiesto.csv` |
| `marcas importar` | `manifiesto.csv` → catálogo SQLite |
| `marcas listar` | Ver el catálogo, con filtros |
| `marcas control` | Hoja de contactos para revisar un lote |
| `marcas planilla` | PDF general con las marcas en casillas |
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
├── pdf/planilla.py         planilla general, hoja de control, plantilla de captura
└── web/                    selector visual (Flask)
scripts/generar_hoja_demo.py  hojas escaneadas sintéticas para pruebas
datos/                        escaneos, imágenes generadas, base y PDF (fuera del repo)
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

Las pruebas generan hojas escaneadas sintéticas y verifican el pipeline
completo: enderezado, detección de las 20 casillas, transparencia del PNG,
trazado del SVG, importación idempotente al catálogo y paginación de los PDF.

## Qué falta

- Cargar los propietarios (hoy se editan de a uno desde el selector o por SQL);
  lo natural es un import desde planilla de cálculo.
- Búsqueda "por parecido" para detectar marcas duplicadas o muy similares al
  dar de alta una nueva: con las métricas que ya guarda el catálogo
  (aspecto, tinta, componentes) alcanza para una primera criba.
- Usuarios y permisos si el selector se publica más allá de la máquina local.
