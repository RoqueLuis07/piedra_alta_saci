# La Guía de Traslado oficial (SENACSA)

Notas sobre el formulario real y cómo el sistema le estampa las marcas. Todo
lo que sigue sale de analizar una guía descargada del sitio oficial
(GE2, N° de orden 91181750).

## Qué trae el documento

13 páginas de tamaño oficio (612 × 1008 pt), generadas con JasperReports:

| Páginas | Contenido |
|---|---|
| 1 a 4 | La guía en sus cuatro copias: **Original** (Unidad Zonal), **Duplicado** (comprador), **Triplicado** (inspección sanitaria) y **Cuadruplicado** (propietario) |
| 5 | Boleta de pago (original y duplicado en la misma hoja) |
| 6 a 13 | **Anexos**: dos hojas de marcas por cada una de las cuatro copias |

En el Rubro 2 de la guía ("Diseño de Marca del Ganado a Trasladar y
Transferir") hay una grilla de 3 × 2 recuadros: el primero es la **marca
dominante** y viene ya impresa por SENACSA como imagen (en la muestra, un
JPEG de 3334 × 3334 px, dibujo digital de trazo parejo). Los demás quedan
vacíos.

Cada hoja de anexo trae una grilla de **5 columnas × 8 filas = 40 casillas** de
113 × 90 pt. Las casillas sobrantes vienen **anuladas con una "X"**. En la
muestra: 40 casillas libres en la primera hoja y 22 en la segunda, o sea
**62 marcas por copia**.

Dos detalles que condicionan el diseño:

1. **La grilla no está siempre en el mismo lugar.** En la muestra, la primera
   hoja de anexo tiene su grilla 7 pt más abajo que las otras siete. Una
   plantilla de coordenadas fija habría desplazado todas las marcas de esa
   página.
2. **El documento se descarga numerado.** El N° de orden, el código de barras
   y el QR cambian en cada descarga. El PDF no se puede regenerar: hay que
   trabajar sobre el archivo descargado.

## Cómo lo resuelve el sistema

`marcas/pdf/guia.py` no usa ninguna plantilla calibrada a mano. Los recuadros
del anexo son **rectángulos vectoriales dentro del propio PDF**, así que se
leen de cada página con `pypdfium2`:

1. Se toman los objetos de trazo cuyo tamaño cae en el rango de una casilla.
2. El tamaño de casilla es el que **se repite**: las tablas de datos del
   formulario también dejan rectángulos, pero no cuarenta iguales.
3. Se descartan los duplicados (cada recuadro viene dibujado dos veces, borde
   y relleno) y se ordenan en filas y columnas, en orden de lectura.
4. Una casilla está **ocupada** si contiene un dibujo o una imagen que cubra
   al menos el 20 % de su superficie: así se respetan tanto la "X" de
   anulación como una marca ya impresa.
5. Las marcas se reparten entre las casillas libres y **la misma marca va a la
   misma posición en las cuatro copias**. Si no coincidieran, las copias no
   servirían como el mismo documento.
6. Se arma una capa transparente con ReportLab y se fusiona sobre la página
   original con `pypdf`. **No se toca nada más**: número de orden, código de
   barras, QR y textos quedan intactos.

Si SENACSA reacomoda el formulario, el detector lo sigue sin cambios de código.
Si algún día cambia tanto que no se reconocen las hojas de anexo, el sistema
falla con un mensaje claro en lugar de estampar marcas en cualquier lado.

## Uso

Por línea de comandos:

```bash
# Ver qué trae el PDF descargado antes de tocarlo
python -m marcas guia --pdf GE291181750.pdf --inspeccionar

# Estampar las marcas elegidas
python -m marcas guia --pdf GE291181750.pdf --codigos H01-F01C01,H01-F01C02
python -m marcas guia --pdf GE291181750.pdf --buscar "LA PATRICIA" --limite 62
```

Desde el navegador (`python -m marcas web`): se tildan las marcas, se adjunta
la guía descargada en el campo *Guía oficial descargada* y el botón
**Estampar en la guía** devuelve el PDF completo.

El PDF de entrada **no se guarda como plantilla**: se sube en cada trámite,
porque cada descarga trae un número de orden distinto.

Opciones útiles:

- `--dpi 200` reduce el peso del archivo. Las marcas se incrustan una vez por
  página y la guía trae cuatro copias, así que la resolución pesa; 300 dpi es
  calidad de imprenta y 200 dpi sigue siendo más que suficiente para el trazo.
- El sistema avisa y no escribe nada si las marcas no entran en las casillas
  libres: hay que descargar la guía con más hojas de anexo o repartir en dos.

## Pendiente de definir

**¿Las casillas del Rubro 2 (páginas 1 a 4) también llevan marcas?** En la
muestra, la casilla de la marca dominante viene impresa y las otras cinco
quedan vacías. Hoy el sistema **sólo llena los anexos**, que es donde va el
grueso. Llenar además el Rubro 2 es poco trabajo —esos recuadros están
dibujados con cuatro segmentos en lugar de un rectángulo, así que hay que
reconstruirlos— pero antes hay que saber **qué corresponde poner ahí**: es un
documento legal y una marca en el lugar equivocado es peor que una casilla
vacía.

Otras dudas para confirmar con quien tramita las guías:

- ¿El orden de las marcas en el anexo tiene algún significado (por categoría,
  por número de boleta) o es libre?
- ¿Hay que anular con "X" las casillas que queden libres después de estampar?
