# Desplegar en Render (alternativa a Railway)

Render aloja el sistema completo con lo mismo que usa hoy en Railway: un servicio web, una base Postgres y un
respaldo diario a GitHub. Todo está descrito en [`render.yaml`](../render.yaml) (un *Blueprint*), así que se crea con
unos clics. **El repositorio ya está listo: no hay que cambiar código.**

> Estado de este documento: la configuración se validó contra la documentación de Render y el restaurador de datos
> se probó con el respaldo real de producción. **No se creó nada en Render**: lo creas tú desde tu cuenta.

## Qué se crea y cuánto cuesta

| Recurso | Plan (`render.yaml`) | Costo aprox. |
|---|---|---|
| Servicio web `piedra-alta` (512 MB, 0,5 CPU) | `0.5c-512mb` (≈ Starter) | **US$ 7 / mes** |
| Postgres `piedra-alta-db` (256 MB, 5 GB de disco) | `0.1c-256mb` (≈ Basic-256mb) | **US$ 6 / mes** + disco (≈ US$ 0,30 por GB/mes según su lista de precios) |
| Respaldo diario `piedra-alta-respaldo` (cron) | `0.5c-512mb` | centavos al mes (se factura por minuto de ejecución, con un mínimo) |
| **Total** | | **≈ US$ 14 – 16 / mes** (Railway te costaba ≈ US$ 4 de consumo) |

Render muestra el total exacto **antes** de confirmar el Blueprint; confírmalo ahí. Precios de
[render.com/pricing](https://render.com/pricing). Si más adelante la base se nota lenta o la web se reinicia por memoria
al emitir PDF grandes: `0.5c-1g` (≈ US$ 19) para la base y `1c-2g` (≈ US$ 25) para la web.

**No uses los planes gratuitos:** el servicio gratis se duerme a los 15 minutos (tarda ~1 minuto en despertar) y la base
gratuita **se borra a los 30 días**.

## Pasos

### 1. Preparar (5 min)
1. Crea tu cuenta en [render.com](https://render.com) e inicia sesión con GitHub. Agrega una tarjeta (Billing).
2. Crea un **token de GitHub** para el respaldo: GitHub → Settings → Developer settings → *Fine-grained tokens* →
   solo el repo `piedra-alta-respaldos`, permiso **Contents: Read and write**. Guárdalo (lo pedirá Render; no lo pegues en ningún chat).

### 2. Crear todo con el Blueprint (10 min)
1. En Render: **New → Blueprint** → conecta el repositorio `RoqueLuis07/piedra_alta_saci` → rama **`escaneos`**
   (es la que tiene todo lo nuevo; cuando se fusione el PR #2 se puede apuntar a la rama principal).
2. Render lee `render.yaml` y muestra: la base, la web y el cron, con su costo. Pega el `GITHUB_TOKEN` cuando lo pida.
3. **Apply**. Render construye (≈ 3–5 min). En los *Logs* de `piedra-alta` debe aparecer **`Esquema aplicado.`** y luego gunicorn
   escuchando. La URL queda como `https://piedra-alta-XXXX.onrender.com`.

### 3. Traer tus datos desde el respaldo de GitHub (5 min)
Hoy Railway tiene su panel caído, pero el respaldo diario está en GitHub (el último: 21-oct 09:25 UTC).

```powershell
cd "C:\Users\Roque Esteche\Downloads\Piedra Alta\piedra_alta_saci"
gh repo clone RoqueLuis07/piedra-alta-respaldos ..\respaldos          # repo privado
# En Render → piedra-alta-db → Info → copia «External Database URL» y cárgala SOLO en tu terminal:
$env:DATABASE_URL = (Get-Clipboard).Trim() + "?sslmode=require"
python scripts\verificar_conexion.py                                   # debe mostrar 0 usuarios/operaciones/marcas
python scripts\restaurar_respaldo.py --carpeta ..\respaldos\respaldo --en-seco
python scripts\restaurar_respaldo.py --carpeta ..\respaldos\respaldo   # usuarios 4 · operaciones 12 · marcas 11 …
```

Se restauran **usuarios con sus contraseñas** (entran con las mismas de siempre), operaciones, marcas con sus imágenes y los
cambios pendientes. El script se niega a correr sobre una base que ya tiene datos. Los PDF emitidos nunca se respaldaron
(solo sus datos): no hay ninguno todavía porque la función es nueva.

### 4. Comprobar (2 min)
Abre la URL de Render, entra con tu usuario y revisa: menú *Emitir PDF · Marcas · Documentos · Administración*, y que
*Marcas* y *Documentos → Archivo* carguen.

### 5. Cargar el catálogo y las guías escaneadas (con la misma `DATABASE_URL` en esa terminal)
Render permite conectarse desde afuera con la URL externa (a diferencia de Railway no hace falta abrir ningún proxy).

```powershell
$base = "C:\Users\Roque Esteche\OneDrive - San Ignacio de Loyola\Escritorio\Escaneos"
python scripts\cargar_catalogo.py --csv "$base\Catalogo_Final\marcas_unicas.csv" --pngs "$base\Base_Marcas_PNG" --en-seco
python scripts\cargar_catalogo.py --csv "$base\Catalogo_Final\marcas_unicas.csv" --pngs "$base\Base_Marcas_PNG"
python scripts\cargar_escaneos.py --indice ..\demo\indice_escaneos      # las guías escaneadas (ver guias_escaneadas.md)
python scripts\detectar_no_marcas.py                                   # sólo informa
python scripts\detectar_no_marcas.py --aplicar --como baja --categorias anulacion,linea_de_tabla
python scripts\detectar_no_marcas.py --aplicar --categorias texto_o_sello,fragmento
```

### 6. Cerrar la puerta de la base (importante)
Por defecto la base acepta conexiones desde cualquier IP (con usuario y contraseña). Al terminar las cargas:
Render → `piedra-alta-db` → **Access Control** → deja solo tu IP o quita todo acceso externo. La web usa la URL interna y no se ve afectada.
Cierra la terminal (o `Remove-Item Env:DATABASE_URL`).

## Cuidados

- **No escribas en los dos sistemas a la vez.** Hasta que decidas cambiarte, Railway sigue siendo el sistema vivo y recibe cambios
  (esta mañana apareció un cambio pendiente nuevo). Si pruebas en Render, hazlo con el respaldo de ese momento. Cuando
  decidas cambiar de verdad: usa el respaldo más reciente y avisa a las 4 personas de que usen solo la URL nueva.
- **Sesiones:** `FLASK_SECRET_KEY` se genera nueva en Render; quien estaba conectado tendrá que volver a iniciar sesión.
- **Dominio propio:** Render → `piedra-alta` → Settings → Custom Domains.
- **Respaldos de la base:** el cron sube los datos a GitHub como antes. Las bases de pago de Render guardan además sus propias
  copias (revísalas en el panel); los PDF emitidos solo viven en esa base.
- **Memoria:** si la web se reinicia sola al emitir PDF de muchas marcas, sube a `1c-2g` (cambia `plan` en `render.yaml` y haz push).
- **Si algo falla al arrancar:** Render → `piedra-alta` → Logs. Lo más común es el `DATABASE_URL` mal enlazado (debe venir del bloque
  `fromDatabase`) o un error del esquema; pega el final del log y se resuelve.
- **Volver atrás:** Render → *Events/Deploys* → *Rollback* al deploy anterior. Los datos de la base no se tocan.
