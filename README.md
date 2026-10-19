# Piedra Alta S.A.C.I. — Registro de marcas de ganado

Aplicación web (Flask + Postgres, desplegada en Railway) para completar las
**Guías de Traslado de SENACSA** con las marcas de ganado y guardar un archivo de
todo lo emitido.

## Qué hace

| Pantalla | Para qué sirve |
|---|---|
| **Emitir PDF** | Se sube la guía oficial, se dibujan las marcas a mano (o se eligen del catálogo / se sube un PNG), se ve la vista previa en vivo y se genera el PDF. Las marcas ocupan primero las casillas libres del Rubro 2 y, si sobran, el Anexo, en las cuatro copias. |
| **Marcas** | Galería del catálogo (buscar, ver, editar, ocultar) y **Revisión**: aprobar, dar de baja u ocultar marcas, una por una o en lote. |
| **Documentos** | **Archivo** de todo lo emitido (fecha, datos de la operación, marcas y el PDF exacto), más los listados de Guías y Ventas. |
| **Administración** | Propietarios, exportar a Excel, usuarios y cuenta propia. |

Roles: `administrador`, `operador` (edita y emite) y `consulta` (sólo lectura).

## Estructura

```
marcas/
  pdf/guia.py            detección del Rubro 2 / Anexo y estampado sobre el PDF oficial
  servidor/
    app.py               fábrica de la aplicación (configuración y comandos)
    rutas/               rutas por área: sesion, catalogo, operaciones, emitir,
                         archivo, revision, propietarios, exportacion, admin
    consultas.py         todo el SQL
    utilidades.py        helpers compartidos (estampado temporal, errores, Excel)
    schema.sql           esquema (idempotente: se aplica en cada deploy)
    templates/, static/  interfaz
scripts/
  cargar_catalogo.py     carga marcas_unicas.csv + PNG (reduce a 512 px)
  detectar_no_marcas.py  marca candidatas a "no es una marca" para revisar
  respaldo_a_github.py   respaldo diario a un repositorio privado (cron en Railway)
legado/                  herramientas anteriores (SQLite, vectorización, CLI); no se usan en producción
docs/                    despliegue.md, cambios_fase1-3.md, guia_oficial.md
tests/                   pytest (incluye pruebas contra un Postgres real embebido)
```

## Desarrollo local

```bash
python -m venv .venv && . .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt                  # incluye un Postgres embebido para las pruebas
pytest                                               # ~25 s
```

Para correr la app hace falta un Postgres (`DATABASE_URL`) y `FLASK_SECRET_KEY`;
`flask --app wsgi crear-esquema` aplica el esquema y `flask --app wsgi crear-admin`
crea el primer administrador. El esquema usa `pgcrypto`: los Postgres de Railway lo
traen; el embebido de las pruebas no, y las pruebas lo omiten.

## Despliegue y cambios

- Cómo desplegar sin riesgo para los datos: [docs/despliegue.md](docs/despliegue.md)
- Qué cambió respecto de la versión anterior: [docs/cambios_fase1-3.md](docs/cambios_fase1-3.md)
