# legado/

Toolchain anterior a la migración a Postgres: selector visual local (`marcas/web`),
base SQLite (`db.py`), importación, vectorización de hojas escaneadas, CLI, scripts
de demo y sus pruebas. **No lo usa el servidor de producción** (`marcas/servidor`).

Se conserva sólo como referencia. Su código sigue importando `marcas.*` con las rutas
originales, así que para correrlo hay que copiar `legado/marcas/*` de vuelta sobre
`marcas/` (y `legado/scripts`, `legado/tests`). El catálogo ya extraído de los
escaneos (3629 marcas) se carga desde `Catalogo_Final`, no con este código.
