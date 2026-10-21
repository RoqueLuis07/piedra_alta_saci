# Despliegue en Railway sin riesgo para los datos

## Cómo está armado hoy (leído de Railway, sólo lectura)

Proyecto `calm-upliftment` (entorno `production`), con otros sistemas además de éste.
Los que importan acá:

| Servicio | Qué es | De dónde despliega |
|---|---|---|
| **PIEDRA ALTAS S.A.C.I Inmobiliaria (Web service)** | La aplicación (`piedra-alta-saci.up.railway.app`) | rama `claude/livestock-brand-vectorization-y95k3c` de `RoqueLuis07/piedra_alta_saci` |
| **Respaldo (Piedra Alta)** | Cron diario 08:00 UTC: `python scripts/respaldo_a_github.py` | **la misma rama** |
| **Postgres** (v18) | Base de datos; volumen de 5 GB, ~0,18 GB en uso | imagen de Railway |

> **Ojo:** esa rama es la que despliega la web **y** el cron. Subir una rama con otro
> nombre no despliega nada; **fusionar un PR a esa rama sí despliega los dos servicios**.

El arranque (`Procfile`) aplica `schema.sql` y recién después levanta gunicorn.

## Qué cambia en la base (todo aditivo)

- Se **crea** la tabla `documentos_emitidos` y dos índices (`create ... if not exists`).
- **No se borra ni se modifica** ninguna tabla ni columna. Las columnas que la app ya no
  usa (`monto_total`, `borrador_*`) y las tablas `cambios_pendientes` y `filtros_guardados`
  quedan como están, con sus datos.
- El código anterior ignora la tabla nueva: **volver atrás es seguro**.

Se ensayó con el respaldo real de producción (4 usuarios, 12 operaciones, 11 marcas, 4 cambios
pendientes): se cargó con el esquema viejo, se aplicó el nuevo dos veces, las cantidades no
cambiaron, y las 37 páginas (incluida cada guía y cada marca) respondieron sin error.

## Antes de fusionar

1. **Respaldo fresco.** El del cron ya existe cada día; antes de fusionar, ejecutá el servicio
   *Respaldo (Piedra Alta)* a mano (Run now) y confirmá en el repo `piedra-alta-respaldos` que el
   commit es de hoy. Si tu plan de Railway tiene backups de volumen, sacá uno del Postgres.
2. **Pruebas en verde** (`pytest`: 46 al momento de escribir esto).
3. Elegí un momento de poco uso: el deploy reinicia la web unos segundos.

## Fusionar

1. `git push -u origin fase1-podar` y abrir un PR contra `claude/livestock-brand-vectorization-y95k3c`.
2. Fusionar. Railway construye y despliega la web y el cron.
3. Mirar el log del deploy: debe decir `Esquema aplicado.` y luego arrancar gunicorn.

## Después de fusionar (en este orden)

1. Entrar a `https://piedra-alta-saci.up.railway.app`: el menú muestra **Emitir PDF / Marcas /
   Documentos / Administración**.
2. Emitir un PDF de prueba con una guía real y mirar que aparezca en **Documentos → Archivo**.
3. Ocultar las operaciones de prueba (`PRUEBA-QA-*`, `QA-*`) desde su ficha (se ocultan, no se borran).
4. **Cargar el catálogo** (2972 marcas, ~140 MB con PNG de 512 px) — procedimiento completo en [carga_catalogo_produccion.md](carga_catalogo_produccion.md):
   - La base de Railway **no tiene acceso público** (no hay TCP proxy). Hay dos caminos:
     a) crear un TCP proxy **temporal** al Postgres, correr el script desde tu computadora y
        **borrar el proxy al terminar**; b) subir las imágenes a un lugar al que Railway tenga acceso
        y correr el script como tarea única dentro de Railway.
   - `DATABASE_URL=<url> python scripts/cargar_catalogo.py --csv Catalogo_Final/marcas_unicas.csv --pngs Base_Marcas_PNG`
   - Es idempotente: si se corta, se vuelve a correr y sigue donde quedó.
   - Luego `python scripts/detectar_no_marcas.py` (sólo informa) y, si el resumen convence,
     `--aplicar` para dejarlas en **Marcas → Revisión**.
5. Revisar en **Marcas → Revisión**.

## Si algo sale mal

- **La web no arranca / falla el deploy:** en Railway, *Deployments* → el deploy anterior → *Rollback*.
  Como los cambios de base son aditivos, el código viejo funciona igual.
- **Se cargó algo de más:** las marcas del catálogo tienen `origen_archivo` (`L3210_xxxxxx.png`);
  se pueden dar de baja u ocultar desde Revisión. Nada se borra.
- **Se cortó el cron de respaldo:** el servicio *Respaldo* usa el mismo código; ahora también
  guarda los **datos** de los documentos emitidos (no los PDF).

## Cosas a vigilar

- **Espacio:** cada PDF emitido pesa ~2 MB (8 marcas) a ~7 MB (65 marcas) y queda en la base.
  El volumen es de 5 GB y hoy usa 0,18 GB; el catálogo suma ~0,15 GB. Alcanza para cientos de
  documentos; conviene mirar el uso cada tanto (Railway → Postgres → Metrics).
- **Los PDF archivados no van al respaldo de GitHub** (pesan demasiado); sólo viven en el
  Postgres de Railway. Si importan como evidencia, conviene activar backups del volumen.
- **Primer respaldo con el catálogo cargado:** pasa a pesar ~190 MB repartidos en lotes de 100 marcas
  (~6 MB cada uno). Es normal que tarde más ese día.
- **Sin chequeo de salud:** el web service no tiene *healthcheck* configurado. Recomendado: path `/login`.
