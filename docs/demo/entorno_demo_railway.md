# Entorno de demo aparte en Railway — plan y costo

> **Este plan no se ejecutó. No se creó nada en Railway.** Cada paso que crea o cambia algo se hace
> sólo con tu aprobación expresa.

## Qué se arma

Un **proyecto nuevo e independiente** (`piedra-alta-demo`), con dos servicios y sin ninguna conexión con producción:

| Servicio | Qué es | Origen |
|---|---|---|
| **Web (demo)** | La aplicación, con un cartel «ENTORNO DE DEMOSTRACIÓN» en todas las pantallas | GitHub `RoqueLuis07/piedra_alta_saci`, rama `fase1-podar` |
| **Postgres (demo)** | Su propia base y su propio volumen, con el mismo Postgres 18 que producción | imagen de Railway |

**Por qué un proyecto nuevo y no un «entorno» dentro del actual:** el proyecto `calm-upliftment` tiene otros
nueve servicios (Canvas, ERP, MySQL, Redis…). Duplicar su entorno de producción los duplicaría a todos.

Lo que **no** lleva: el cron de respaldo (no hace falta), ni datos reales, ni usuarios reales.

## Costo estimado

Precios de Railway (docs.railway.com/pricing): **RAM $10/GB/mes · CPU $20/vCPU/mes · volumen $0,15/GB/mes ·
salida de red $0,05/GB**. Los *builds* no se cobran. Plan Hobby: $5/mes que **incluyen $5 de uso**.

Consumo medido en producción durante los últimos 7 días (es la mejor referencia que hay):

| Concepto | Consumo real | Costo mensual |
|---|---|---|
| Web — memoria | 0,28 GB de promedio | ≈ **$2,80** |
| Web — CPU | ≈ 0,0002 vCPU | ≈ $0,00 |
| Postgres — memoria | 0,09 GB | ≈ **$0,94** |
| Volumen (catálogo ~0,15 GB + PDF de ensayo) | 0,5–5 GB según el tamaño que se asigne | $0,08 – $0,75 |
| Red | despreciable | ≈ $0,00 |
| **Total** | | **≈ $3,80 – $4,50 por mes** |

- **Si lo dejas encendido todo el mes:** unos **$4–5** de uso. Si tu cuenta ya consume más de $5 en total
  (el proyecto actual tiene nueve servicios), se suma entero a la factura; si consume menos, parte puede quedar
  dentro de los $5 incluidos. **No puedo ver tu plan ni tu factura**: mírala en Railway → *Usage*.
- **Si lo levantas para una semana y lo borras:** ≈ **$1**.
- **Un día de demo:** ≈ **$0,15**.
- **Costo de creación:** $0 (no hay cargo por crear proyectos ni por construir).
- **Tope de gasto:** antes de empezar, poner un *usage limit* duro (por ejemplo $10) en la configuración del
  workspace. Así un descuido no puede generar una factura grande.

## Qué necesito de ti

- [ ] **Aprobar el costo** (≈ $4–5/mes, o ≈ $1 si se borra tras la demo).
- [ ] **Confirmar tu plan** (Hobby o Pro) y poner el tope de gasto.
- [ ] **Decir cuánto tiempo** debe existir el entorno (una semana, un mes, permanente).
- [ ] **Decidir quién lo va a usar**, para repartir las cuentas de demo.
- [ ] Un «sí» explícito para que yo cree los recursos (proyecto, base, servicio, variables, dominio) con las
      herramientas de Railway que tengo conectadas. Si prefieres hacerlo tú en el panel, los pasos son los mismos.

## Pasos

### Fase 1 — Crear el proyecto y los servicios (≈ 15 min)

1. Crear el proyecto `piedra-alta-demo` en tu workspace personal.
2. Agregar **Postgres** (misma imagen que producción, `postgres-ssl:18`) con su volumen.
3. Agregar el servicio **web** desde GitHub: repo `RoqueLuis07/piedra_alta_saci`, rama `fase1-podar`.
   El arranque ya viene en el `Procfile` (aplica el esquema y levanta gunicorn).
4. Variables del servicio web (los valores secretos **no** se muestran en el chat):
   - `DATABASE_URL` = referencia al Postgres del propio proyecto (red privada).
   - `FLASK_SECRET_KEY` = una clave aleatoria de 64 caracteres, generada para este entorno y distinta de la de producción.
   - `ENTORNO_DEMO` = `1` (muestra el cartel de demo).
5. Chequeo de salud en `/login`, y generar el dominio público (`*.up.railway.app`, sin costo).

### Fase 2 — Comprobar que arrancó (≈ 5 min)

- En el log del deploy: `Esquema aplicado.` y gunicorn escuchando.
- `GET /login` → 200; `GET /` sin sesión → redirige al login.

### Fase 3 — Usuarios de demo (≈ 5 min)

Hace falta una puerta temporal a la base de demo (TCP proxy, puerto 5432), igual que en
[carga_catalogo_produccion.md](../carga_catalogo_produccion.md), pero **acá no hay datos reales**, así que el
riesgo es mucho menor. Se aprovecha la misma ventana para los usuarios y el catálogo.

```powershell
$env:DATABASE_URL = (Get-Clipboard).Trim()     # URL pública de la base DE DEMO
python scripts\verificar_conexion.py            # debe decir 0 operaciones
python scripts\crear_usuarios_demo.py --confirmo-que-es-demo
```

Crea **administrador, operador y consulta** con contraseñas aleatorias que se muestran **una sola vez**.
El script se niega a correr si la base tiene más de 25 operaciones (señal de que sería la de producción).

### Fase 4 — Catálogo (≈ 10 min)

```powershell
$base = "C:\Users\Roque Esteche\OneDrive - San Ignacio de Loyola\Escritorio\Escaneos"
python scripts\cargar_catalogo.py --csv "$base\Catalogo_Final\marcas_unicas.csv" --pngs "$base\Base_Marcas_PNG"
# Dejar el catálogo presentable: sacar las rayas y líneas de tabla (≈ 90–95 % de acierto)...
python scripts\detectar_no_marcas.py --aplicar --como baja --categorias anulacion,linea_de_tabla
# ...y dejar texto/sellos y fragmentos «por revisar»: da material real para mostrar la pantalla de Revisión.
python scripts\detectar_no_marcas.py --aplicar --como revisar --categorias texto_o_sello,fragmento
```

Resultado esperado: ≈ 2970 marcas; ≈ 460 dadas de baja (rayas y líneas) y ≈ 160 en *Por revisar* (las ~42 sospechosas del catálogo más ~120 de texto/sellos y fragmentos).

### Fase 5 — Cerrar la puerta y dejar datos de ejemplo (≈ 15 min)

1. **Borrar el TCP proxy** y comprobar que no queda ninguno.
2. Entrar como **operador** y emitir **2 o 3 documentos** con `Guia_DEMO_anonimizada.pdf` (dominante dibujada,
   algunas dibujadas, varias del catálogo). Así *Documentos → Archivo* ya muestra historial cuando empiece la demo.

### Fase 6 — Ensayo (el día anterior)

Recorrer el guion de [guion_demo.md](guion_demo.md) con cronómetro, también desde la **tablet** y el
navegador de la presentación; grabar el video de respaldo.

### Fase 7 — Después de la demo

- **Borrar el proyecto** (costo → $0) o dejarlo si se va a reutilizar. Los pasos se repiten en ~45 minutos.
- Si se deja, **no empujar cambios a la rama `fase1-podar` justo antes de una demo**: cada `push` redespliega el servicio.

## Riesgos y cuidados

- **El repositorio de GitHub es público.** Cualquiera puede leer el código y las guías de `docs/`. No hay claves
  ni datos reales dentro, pero es un sistema de negocio; conviene considerar pasarlo a **privado** (Railway sigue
  pudiendo desplegarlo, con permisos).
- **Datos:** el entorno sólo debe tener datos de ejemplo. La guía de la demo está anonimizada y se verificó que
  no queda ningún dato original.
- **Acceso:** la URL es pública, pero todo exige inicio de sesión. Contraseñas aleatorias, entregadas en privado, nunca por chat ni correo sin cifrar.
- **Rama de despliegue:** si se fusiona el PR y se borra `fase1-podar`, el servicio se queda sin rama: habría que apuntarlo a la rama final.
- **Sin respaldo:** es un entorno descartable; no se configura el cron de respaldo.

## Tiempo total

≈ **1 hora de trabajo** (45 min si ya tienes el catálogo y la guía a mano) más el ensayo.
