# Guía de digitalización de marcas

Cómo pasar de más de 500 marcas dibujadas a mano a archivos PNG (y SVG)
limpios, parejos y listos para imprimir. Está escrita para quien va a hacer el
trabajo, no sólo para quien programa.

---

## 1. Antes de escanear: preparar las hojas

Lo que más cuesta en un lote grande no es procesar las imágenes: es **saber qué
marca es cada archivo**. Por eso conviene que el papel ya venga ordenado.

```bash
python -m marcas captura --hojas 25
```

Esto imprime hojas A4 con 24 casillas numeradas (`H01-F01C01`, `H01-F01C02`…).
Se copia una marca por casilla, y el código de cada marca sale solo de su
posición: **nadie tipea nombres de archivo**.

Al imprimir: **100 %, sin "ajustar a página"**. Si la impresora escala la hoja,
la grilla se deforma y el detector de casillas trabaja peor.

Reglas para el dibujo:

- Marcador o fibra **negra**, punta de 1 a 2 mm. El lápiz gris claro se pierde
  al binarizar; el bolígrafo azul funciona pero con menos contraste.
- **No tocar ni cruzar el recuadro** de la casilla. El pipeline descarta el
  borde impreso, pero si el trazo se apoya encima se puede recortar.
- Una sola marca por casilla, centrada, lo más grande que entre sin invadir el
  borde.
- Casilla que quede vacía, vacía: el pipeline la reporta y no genera archivo.

Si las marcas **ya están dibujadas** en libretas o fichas viejas y no se pueden
volver a copiar, existe el modo libre (§6).

## 2. Escaneo

| Parámetro | Valor | Por qué |
|---|---|---|
| Resolución | **600 dpi** (mínimo 400) | A 300 dpi un trazo fino se corta al binarizar y potrace lo devuelve dentado |
| Modo | Escala de grises | El color no aporta nada y triplica el peso |
| Formato | PNG o TIFF sin compresión | El JPEG agrega halos alrededor del trazo, justo donde se decide tinta/papel |
| Contraste automático | **Apagado** | El pipeline aplana el fondo mejor y de forma reproducible |
| Enderezado del escáner | Se puede dejar activado | El pipeline igual corrige hasta ~12° |

Nombrar los archivos por hoja: `hoja_01.png`, `hoja_02.png`… El nombre se usa
como prefijo de los códigos. Todo va a `datos/escaneos/`.

**¿Foto de celular en vez de escáner?** Funciona, con cuidado: hoja apoyada en
una superficie plana, luz pareja sin sombra del cuerpo, cámara paralela al
papel y a unos 40 cm, resolución máxima, HDR apagado. La corrección de fondo
del pipeline resuelve la iluminación despareja; lo que no resuelve es la
**perspectiva** (foto en diagonal), que deforma las casillas.

## 3. Procesar el lote

```bash
python -m marcas procesar                 # lee datos/escaneos/, escribe datos/marcas/
python -m marcas procesar --sin-svg       # más rápido, sólo PNG
python -m marcas procesar --modo libre    # hojas sin grilla impresa
```

Qué le pasa a cada marca, en orden, y por qué:

1. **Enderezado.** Se aíslan los segmentos horizontales largos con morfología y
   se mide la inclinación mediana por transformada de Hough. Si no hay líneas
   confiables no se rota nada: es preferible no corregir a corregir mal.
2. **Detección de casillas.** Con dos aperturas morfológicas (un kernel ancho y
   uno alto) quedan sólo las líneas de la grilla. Las casillas son los *huecos*
   de esa máscara: se filtran por área, por qué tan rectangulares son y por
   proporción, y se numeran en orden de lectura.
3. **Fondo plano.** Cada recorte se divide por su propio desenfoque gaussiano.
   Es lo que neutraliza la sombra del lomo y la caída de luz, y lo que permite
   usar el mismo umbral en toda la hoja.
4. **Binarización adaptativa** (gaussiana, ventana ≈ 12 % del lado). Un umbral
   global tipo Otsu tiñe de negro media hoja cuando hay una sombra; el
   adaptativo decide barrio por barrio.
5. **Borrado del recuadro.** Se eliminan los componentes que tocan el borde *y*
   son lineales (ocupan ≥ 85 % del ancho o del alto y son finos). Un trazo de
   marca puede tocar el borde, pero rara vez cumple las dos condiciones.
6. **Cierre de micro-cortes** con una clausura de radio 2: el marcador que
   salta sobre el grano del papel deja huecos de 1 o 2 px que después se ven
   como agujeros en el contorno vectorizado.
7. **Descarte de motas.** Se tiran los componentes menores al 2 % del más
   grande. El umbral es bajo a propósito: muchas marcas tienen dos o tres
   trazos separados (una letra y una barra) y hay que conservarlos.
8. **Normalización.** Recorte al trazo, centrado y escalado a un lienzo
   cuadrado de 1024 px con 6 % de margen. Al reducir con `INTER_AREA` el borde
   queda suavizado, y ese suavizado es justamente el canal alfa.
9. **PNG RGBA** con trazo negro y fondo transparente.
10. **SVG** con potrace sobre la máscara a resolución original (no sobre el PNG
    reducido): el trazado siempre parte del mejor material disponible.

Salida:

```
datos/marcas/png/HOJA-01-F01C01.png     imagen normalizada, 1024x1024, transparente
datos/marcas/svg/HOJA-01-F01C01.svg     original vectorial
datos/marcas/manifiesto.csv             una fila por casilla, con métricas
```

## 4. Control de calidad

El manifiesto trae, por marca: tamaño del trazo, proporción, porcentaje de
tinta, cantidad de trazos sueltos, `sha1` de la imagen y una columna `revisar`
con el motivo. Se marca para revisar cuando:

- hay **muy poca tinta** (< 0,8 %): trazo demasiado claro, o lo que quedó es ruido;
- hay **demasiada tinta** (> 35 %): una sombra o un borrón entró como trazo;
- quedaron **más de 12 trazos sueltos**: papel sucio o casilla con anotaciones;
- el trazo mide **menos de 40 px**: la hoja se escaneó con poca resolución.

Para revisar a ojo:

```bash
python -m marcas control     # PDF de contactos, 48 marcas por página
```

Con 500 marcas son 11 páginas. Se buscan tres cosas: marcas cortadas, casillas
que salieron vacías y marcas que no coinciden con su código. Lo que esté mal se
vuelve a escanear —sólo esa hoja— y se reprocesa; volver a importar actualiza
las filas existentes, no duplica.

## 5. Cargar el catálogo

```bash
python -m marcas importar
python -m marcas estado
```

`manifiesto.csv` se puede editar antes de importar: por ejemplo reemplazar los
códigos automáticos por los códigos reales de registro. También se puede pasar
el mapeo desde otro archivo:

```bash
python -m marcas procesar --codigos codigos.csv   # columnas: hoja,fila,columna,codigo
```

## 6. Casos que se salen del molde

**Hojas sin grilla impresa** (libretas, fichas antiguas):

```bash
python -m marcas procesar --modo libre
```

Agrupa los trazos cercanos y recorta cada grupo. Anda bien si las marcas están
bien separadas; si están apretadas o se tocan, va a unir dos marcas en una.
Ahí conviene aceptar el trabajo manual: recortar cada marca en un archivo y
procesar esa carpeta en modo libre, una marca por archivo.

**Marcas muy claras (lápiz).** Bajar `CONSTANTE_UMBRAL` en `marcas/config.py`
(de 12 a 6-8) hace el umbral más permisivo. Si sube mucho el ruido, se compensa
subiendo `RUIDO_REL_MAYOR`.

**Trazos gruesos que se empastan.** Subir `CONSTANTE_UMBRAL` y, si aparecen
agujeros, bajar el radio del cierre en `cerrar_trazos`.

**El detector no encuentra las casillas.** Casi siempre es una de tres: la hoja
se imprimió escalada, el escaneo está muy torcido (> 12°) o el recuadro es
demasiado tenue. Se verifica rápido: si `marcas procesar` reporta 0 casillas,
probar `--modo libre` para descartar que sea un problema de la grilla.

## 7. Por qué no otras opciones

- **Umbral global (Otsu) para toda la hoja.** Es más simple y anda bien con
  escaneos perfectos; con una sombra de lomo pinta de negro un cuarto de hoja.
  El adaptativo cuesta lo mismo y no falla ahí.
- **Adelgazar el trazo (esqueletización).** Da una línea de 1 px muy prolija,
  pero **pierde el grosor**, que en una marca de ganado es parte del dibujo
  —es lo que se va a marcar a fuego—. Se conserva el trazo tal cual se dibujó.
- **Vectorizar con IA / reconocer la marca como símbolo.** Para 500 marcas
  únicas no hay nada que "reconocer": no son un alfabeto cerrado. Potrace sigue
  el contorno real del trazo, que es exactamente lo que se quiere conservar.
- **Guardar las imágenes dentro de la base de datos.** Infla el archivo,
  complica los respaldos y no aporta: con la ruta y el `sha1` alcanza para
  detectar un archivo faltante o cambiado.

## 8. Cuánto tarda

Medido en esta máquina (4 núcleos), con hojas A4 a 300 dpi:

| Tarea | Tiempo |
|---|---|
| Procesar una marca (PNG + SVG) | ~60 ms |
| Procesar 500 marcas | ~30 segundos |
| Escanear 25 hojas a 600 dpi | 20 a 40 minutos (según el escáner) |
| Revisar 500 marcas en la hoja de control | 20 a 30 minutos |

El trabajo de computadora es despreciable. Planificar el tiempo del **papel**:
copiar las marcas a las hojas de captura y revisar el resultado.
