# Guías escaneadas: cómo se arman y cómo se cargan

La sección **Documentos → Guías escaneadas** junta todas las páginas escaneadas de los documentos históricos
(guía GE2, anexo, certificado sanitario, declaración jurada CIEFA…) y las **agrupa por N.º de guía** a partir de lo
que dice cada imagen. Cada guía muestra sus documentos, su propietario, y las marcas que salieron de sus páginas.
Desde ahí se puede **estampar esas marcas** en una guía nueva (Emitir PDF).

## Cómo se agrupa

El N.º de guía sale, en este orden, de:

| Documento | De dónde se lee el número |
|---|---|
| Certificado sanitario | «…los datos **10894231** del anverso de este documento…»; también el N.º de certificado (14 dígitos bajo el código de barras), propietario, CI, establecimiento y animales |
| Declaración jurada CIEFA y Guía GE2 | El número impreso junto a «N.º de orden» |
| Anexo (no trae número legible) | Se **infiere** de la guía que le precede en el escaneo (queda anotado como «inferido») |
| Página sin nada legible | Va a **Sin número**, para asignarla a mano |

El texto completo leído de cada imagen se guarda, así que la búsqueda encuentra cualquier palabra de la hoja.

> **Es lectura automática (OCR), no una transcripción.** Puede equivocarse, sobre todo con manuscritos. Por eso
> cada dato se corrige desde la pantalla de la página, y lo corregido **no se pisa** si se vuelve a cargar el índice.

## Cómo se cargan (en tu computadora, una sola vez)

```powershell
# 1) Leer las páginas (OCR) y preparar las imágenes: ~2 h, se puede cortar y reanudar.
python scripts\indexar_escaneos.py --escaneos "<carpeta Escaneos>" --salida ..\demo\indice_escaneos --trabajadores 2 --hilos 6
# 2) Cargarlas en la base (en seco primero):
python scripts\cargar_escaneos.py --indice ..\demo\indice_escaneos --en-seco
python scripts\cargar_escaneos.py --indice ..\demo\indice_escaneos
```

Los dos pasos son idempotentes. Si el catálogo de marcas ya está cargado (`scripts/cargar_catalogo.py`),
cada marca se une automáticamente con la página de la que salió; el orden de carga da igual.

## Cómo se verifica que una marca es legítima

Cada marca guarda **de qué página y casilla salió** (formato, Dominante o Complementaria, fila y columna). La ficha de la
marca y la pantalla de **Revisión** enlazan a esa página, para confirmar *en su contexto* que es una marca y no un trazo
de la hoja. Además:

1. La extracción ya descartó fragmentos de línea, duplicados y ruido (11 % del total).
2. `scripts/detectar_no_marcas.py` marca rayas de anulación, líneas de tabla, sellos y fragmentos
   (≈ 90–95 % de acierto en lo que marca; le faltan algunos sellos grandes).
3. En producción: rayas y líneas se dan de baja; lo demás queda **por revisar**. Todo lo dado de baja se puede
   recuperar desde **Revisión → Sacadas por el detector**.

Dónde se concentra la basura (catálogo real): sobre todo en las **complementarias del formulario CIEFA**
(23–36 % por casilla, porque ahí se tachan las casillas sin usar) y en los anexos; las **dominantes** salen casi limpias.
