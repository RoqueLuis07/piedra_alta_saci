# Respaldo y recuperación (Railway y Render)

Todo se respalda en **un solo repositorio privado de GitHub**: `RoqueLuis07/piedra-alta-respaldos` (el que ya usa
Railway). El servicio **«Respaldo (Piedra Alta)»** de Railway (cron, todos los días 08:00 UTC) corre
`python scripts/respaldo_a_github.py` y deja:

| Carpeta del repo | De qué base |
|---|---|
| `respaldo/` | Railway (principal) |
| `respaldo_render/` | Render (sólo si el servicio de respaldo tiene la variable `RENDER_DATABASE_URL`) |

## Qué guarda cada carpeta (todo lo necesario para recuperar el sistema)

- `usuarios.json` (con las contraseñas ya encriptadas: la gente entra con las mismas), `propietarios.json`,
  `operaciones.json`, `cambios_pendientes.json`, `documentos_emitidos.json` (los datos de cada documento emitido).
- `marcas/lote_NNNN.json`: las marcas con sus imágenes.
- `escaneos/lote_NNNN.json`: las **páginas escaneadas** con su imagen y lo leído (incluido el respaldo del Rubro 4).
- `documentos/NNNNNN.pdf`: el **PDF de cada documento emitido**, tal cual.
- `marcas_versiones_imagen/lote_NNNN.json`: el **historial de imágenes** de cada marca.
- `respaldos_operacion/lote_NNNN.json`: el documento original de cada operación.
- `manifiesto.json`: cantidades de todo, para comprobar la restauración.

Sólo se suben los archivos que **cambiaron** desde la última vez (el script compara con lo que ya hay en GitHub), así
que cada día hay pocos commits y el historial de git sirve para volver a cualquier fecha. Ningún archivo pasa los
100 MB que admite GitHub.

## Activar el respaldo de Render (una vez)

1. Render → base `piedra-alta-db` → **Info** → copiá la *External Database URL* (no la pegues en ningún chat).
2. Railway → proyecto `calm-upliftment` → servicio **Respaldo (Piedra Alta)** → *Variables* → nueva variable
   `RENDER_DATABASE_URL` con ese valor.
3. En ese servicio, **Redeploy** (corre una vez ahora) o esperá a las 08:00 UTC.
4. En GitHub, repo `piedra-alta-respaldos`, debe aparecer la carpeta `respaldo_render/`.

La base de Render tiene que aceptar conexiones desde afuera (hoy permite todas las IP; es lo que necesita este
respaldo). La conexión va con usuario, contraseña y SSL.
La base **gratuita** de Render vence el 20/11/2026 y se borra: pasá a un plan pago o restaurá en otra base antes.

## Recuperar el sistema desde el respaldo

```powershell
cd "C:\Users\Roque Esteche\Downloads\Piedra Alta\piedra_alta_saci"
gh repo clone RoqueLuis07/piedra-alta-respaldos ..\respaldos            # o: git -C ..\respaldos pull
# En la base NUEVA y vacía (con el esquema ya aplicado: el arranque de la app lo hace):
$env:DATABASE_URL = (Get-Clipboard).Trim()                             # URL de la base nueva
python scripts\restaurar_respaldo.py --carpeta ..\respaldos\respaldo --en-seco
python scripts\restaurar_respaldo.py --carpeta ..\respaldos\respaldo    # o ..\respaldos\respaldo_render
```

Restaura usuarios, propietarios, operaciones, páginas escaneadas (con imagen), marcas (con imagen y vínculo a su
página), historial de imágenes, documentos emitidos con su PDF y cambios pendientes. Se niega a correr sobre una base
que ya tiene datos. Para volver a una fecha anterior: `git -C ..\respaldos checkout <commit>` y repetir.

## Mantener las dos plataformas iguales

El código de Railway sale de la rama `claude/livestock-brand-vectorization-y95k3c` y el de Render de `escaneos`.
Para publicar en las dos:

```powershell
git push origin escaneos
git push origin escaneos:claude/livestock-brand-vectorization-y95k3c     # despliega Railway (web y cron)
# Render: «Manual Deploy» en su panel (o por API); no despliega solo al hacer push.
```
