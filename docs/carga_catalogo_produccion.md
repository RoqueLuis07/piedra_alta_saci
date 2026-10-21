# Cargar el catálogo de marcas en producción (con un TCP proxy temporal)

> **Este procedimiento no se ejecutó. Está escrito para que lo apruebes antes.**
> Se hace **después** de fusionar el PR #1 y de comprobar que el deploy salió bien
> (ver [despliegue.md](despliegue.md)): la tabla y el código nuevos tienen que estar en producción.

## Qué se va a hacer, y qué no

- **Sí:** insertar ~2972 filas nuevas en la tabla `marcas` (imágenes PNG reducidas a 512 px, ~140 MB en total).
- **No:** no se modifica ni se borra ninguna fila existente; no se crean guías, operaciones ni propietarios
  (el catálogo no trae esos datos); no se toca ninguna otra tabla.
- La carga es **idempotente** (si una marca ya está, se saltea) y **reanudable** (confirma de a 250 marcas):
  si se corta, se vuelve a correr y sigue donde quedó.
- Quedan marcadas como `revisar` las ~42 sospechosas del catálogo; el resto entra `activa`.

## Por qué hace falta un proxy

La base de Railway sólo es accesible por la red privada (hoy no tiene ningún TCP proxy). Las imágenes
están en tu computadora (~1,1 GB de originales), así que hay que abrir **temporalmente** una puerta pública
al Postgres, cargar, y **cerrarla**.

### Lo que hay que saber antes de abrirla

1. **Crear y borrar el proxy reinicia el servicio Postgres** (Railway lo redespliega). Son unos segundos a
   un minuto sin base, **para todo lo que use ese Postgres** (revisá en Railway qué otros servicios del
   proyecto `calm-upliftment` lo usan: `gio_system`, `ERP_PRUEBA`…). Hacelo en un horario de poco uso.
2. Tras cada reinicio de la base, la web puede dar un par de errores 500 hasta que su pool de conexiones
   descarta las conexiones muertas. Si pasa: *Restart* del servicio web (no pierde datos).
3. Mientras el proxy existe, la base es alcanzable desde internet con usuario y contraseña (y SSL: la imagen
   es `postgres-ssl`). Dejalo abierto **el menor tiempo posible** (≈15–25 min) y no compartas la URL.
4. **La URL con la contraseña no se pega en el chat ni en ningún archivo del repo.** Se copia al portapapeles
   y se carga en una variable de entorno de tu terminal (paso 4).

## Antes de empezar (checklist)

- [ ] El PR #1 está fusionado y el deploy de la web terminó bien (el menú muestra *Emitir PDF / Marcas / Documentos / Administración*).
- [ ] Hay un **respaldo de hoy** en `piedra-alta-respaldos` (ejecutá *Respaldo (Piedra Alta)* con *Run now* y mirá el commit).
- [ ] Sabés qué base usa la web: en Railway → servicio web → *Variables* → `DATABASE_URL` (debe apuntar al servicio **Postgres**, base `railway`).
- [ ] Tenés la carpeta `Escaneos` completa en tu computadora (`Catalogo_Final\marcas_unicas.csv` y `Base_Marcas_PNG\` con 3629 PNG).
- [ ] Python 3.11+ en tu computadora.

## Pasos

### 1. Preparar el entorno (una sola vez, en tu computadora)

```powershell
cd "C:\Users\Roque Esteche\Downloads\Piedra Alta\piedra_alta_saci"
git fetch origin; git checkout claude/livestock-brand-vectorization-y95k3c; git pull   # ya con el PR fusionado
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

### 2. Abrir el proxy (Railway)

En Railway: proyecto `calm-upliftment` → servicio **Postgres** → *Settings* → *Networking* → *TCP Proxy*
→ puerto de la aplicación **5432** → *Add*. (Equivale a `create-tcp-proxy` con `applicationPort: 5432`.)

Esperá a que el servicio vuelva a estar *Active*. En *Variables* del Postgres debería aparecer `DATABASE_PUBLIC_URL` (si no aparece, usá la dirección y el puerto que muestra el proxy con las credenciales de `DATABASE_URL`).

### 3. Copiar la URL pública

En *Variables* del Postgres, copiá el valor de `DATABASE_PUBLIC_URL` (botón de copiar). **No lo pegues en ningún chat.**

### 4. Cargarla en tu terminal (queda sólo en esa ventana)

```powershell
$env:DATABASE_URL = (Get-Clipboard).Trim()
if ($env:DATABASE_URL -notmatch 'sslmode=') { $env:DATABASE_URL += '?sslmode=require' }
Set-Clipboard -Value ''        # borra la contraseña del portapapeles
```

### 5. Verificar que es LA base correcta (obligatorio)

```powershell
.\.venv\Scripts\python.exe scripts\verificar_conexion.py
```

Debe mostrar el nombre de la base que usa la web (normalmente `railway`), PostgreSQL 18, y cifras como las de producción hoy: **operaciones ≈ 12**,
**marcas ≈ 11**, **usuarios = 4**, y `documentos_emitidos` **existente** (si dice "no existe todavía", el
deploy nuevo no corrió: **parar** y revisar). Si las cifras no coinciden con lo que conocés, **parar**.

### 6. Prueba en seco contra esa base

```powershell
$base = "C:\Users\Roque Esteche\OneDrive - San Ignacio de Loyola\Escritorio\Escaneos"
.\.venv\Scripts\python.exe scripts\cargar_catalogo.py --en-seco --csv "$base\Catalogo_Final\marcas_unicas.csv" --pngs "$base\Base_Marcas_PNG"
```

Debe decir `[EN SECO, no se escribió nada] Cargadas 2972 … ya estaban 0 … dañadas ['L3210_000627.png']`.

### 7. Carga real

```powershell
.\.venv\Scripts\python.exe scripts\cargar_catalogo.py --csv "$base\Catalogo_Final\marcas_unicas.csv" --pngs "$base\Base_Marcas_PNG"
```

Tarda unos 3 minutos de procesamiento más la subida de ~140 MB. Muestra el avance cada 250. Si se corta,
**volver a correr el mismo comando**: sigue donde quedó (`ya estaban N`).

### 8. Comprobar

```powershell
.\.venv\Scripts\python.exe scripts\verificar_conexion.py
```

`marcas` debe ser **11 + 2972 = 2983** (las 11 anteriores intactas); `operaciones` y `usuarios` **sin cambios**.

### 9. (Opcional, separado) Marcar candidatas a "no es una marca"

Primero sólo informa:

```powershell
.\.venv\Scripts\python.exe scripts\detectar_no_marcas.py
```

Esperado: ~580 candidatas (≈236 anulaciones, 224 líneas de tabla, 56 texto/sello, 66 fragmentos). Si el
resumen te convence: repetir con `--aplicar`. Las deja en estado *revisar* con su motivo; **no da de baja nada**.
Se puede hacer más tarde con otro proxy o desde otra vía; no es necesario para usar el catálogo.

### 10. Cerrar el proxy (no te lo saltees)

Railway → Postgres → *Settings* → *Networking* → borrar el TCP Proxy (`delete-tcp-proxy`, puerto 5432).
Esperá a que el Postgres vuelva a *Active* y confirmá que **ya no hay ningún proxy**. Cerrá la terminal
(o `Remove-Item Env:DATABASE_URL`). Si la web da errores 500, *Restart* del servicio web.

### 11. Verificar en la web

- **Marcas** muestra la galería (≈2983 marcas) y **Marcas → Revisión** los pendientes.
- **Emitir PDF**: subí una guía, elegí marcas del catálogo y generá un PDF; debe aparecer en **Documentos → Archivo**.
- El cron *Respaldo* de la mañana siguiente va a ser más pesado (~190 MB en lotes de 100 marcas): es normal.

## Si algo sale mal

| Situación | Qué hacer |
|---|---|
| El paso 5 no muestra lo esperado | **Parar.** No cargar. Cerrar el proxy y revisar a qué base apunta `DATABASE_PUBLIC_URL`. |
| La carga se corta | Volver a correr el mismo comando. Es idempotente. |
| Se cargó pero algo no convence | Las marcas del catálogo se reconocen por `origen_archivo` (`L3210_….png`). Se pueden **ocultar** desde la pantalla de Revisión; nada se borra. |
| Hay que deshacer toda la carga | Restaurar desde el respaldo de hoy del repo `piedra-alta-respaldos`, o (último recurso, sólo con tu OK expreso) borrar por `origen_archivo` las marcas que ninguna operación usa. |
| Quedó el proxy abierto | Borrarlo ya (paso 10). Si hubo cualquier duda sobre la URL, rotar la contraseña de Postgres desde Railway. |

## Qué se probó

- La carga completa del catálogo real contra un Postgres local: 2972 de 2973 marcas, 1037 MB → 139 MB, con los
  PNG de 512 px, y la galería, Emitir PDF y Revisión funcionando encima.
- Idempotencia, reanudación por lotes y modo `--en-seco` con pruebas automáticas.
- La migración del esquema con el respaldo real de producción (ver despliegue.md).

**No se probó** contra la base de Railway ni a través de un TCP proxy real.
