# Cambios de la rama `fase1-podar` (respecto de `9a723bb`)

Unos 70 archivos, 11 commits. Pensada para fusionarse a `claude/livestock-brand-vectorization-y95k3c`
(ver [despliegue.md](despliegue.md)).

## Qué se agrega

- **Emitir PDF** (`/emitir`): una sola pantalla que reemplaza las de imprimir Guía y Venta y sus
  borradores. Dibujo a mano como eje, catálogo y subida de PNG como alternativas, vista previa en
  vivo con el cupo de Rubro 2 y Anexo. Lee del PDF el N.º de guía, vendedor, comprador, cantidad de
  animales y las marcas que declara la guía. Nada se guarda hasta *Generar*; si algo falla, no queda nada a medias.
  Si la guía ya existe (mismo número y tipo) el PDF se archiva en esa operación en vez de duplicarla.
- **Archivo de documentos emitidos** (`/archivo`): cada PDF generado queda guardado con la fecha, quién lo
  emitió, todos los datos de la operación y las marcas (con el papel con que se estamparon), y se puede
  ver y volver a descargar. Tabla nueva `documentos_emitidos`.
- **Marcas como galería** y **Revisión** (`/revision`): aprobar / dar de baja / ocultar, individual o en
  lote, con filtro por motivo y deshacer.
- **Menú de cuatro entradas** con pestañas: Emitir PDF · Marcas · Documentos · Administración.
- `scripts/cargar_catalogo.py`: carga del catálogo extraído de los escaneos (PNG a 512 px, idempotente).
- `scripts/detectar_no_marcas.py`: marca candidatas a "no es una marca" (anulaciones, líneas de tabla,
  texto/sello, fragmentos); sólo ayuda, nunca da de baja.

## Qué se quita

- Finanzas y estadísticas (pantalla Estadísticas, resumen de Inicio, campo y columna *Monto*, lectura del monto del PDF).
- Cola de **Aprobaciones**: Administrador y Operador editan directo.
- Filtros guardados y columnas configurables; aviso de marcas por vencer; pantalla de Inicio (`/` lleva a Emitir PDF, o a Marcas para el rol Consulta).
- Todo el toolchain anterior (SQLite, vectorización, CLI, scripts de demo) pasó a `legado/`.
- **No se borra ninguna tabla ni columna de la base.**

## Mejoras internas

- `app.py` (1670 líneas) → fábrica de 100 líneas + módulos por área en `marcas/servidor/rutas/`.
- PDF más liviano: las capas de las cuatro copias se arman una vez y se quita el ASCII85 (40 marcas: 8,9 MB → 4,4 MB, misma calidad).
- El cupo del Anexo ahora es **por copia** (60), no la suma de las cuatro (240).
- OpenCV sale de las dependencias de producción; se agrega `tzdata` (necesario en Windows).
- El respaldo a GitHub incluye los datos del archivo emitido y usa lotes de 100 marcas.

## Revisión final (hallazgos corregidos antes de subir)

- Una marca **dada de baja** ya no se ofrece en el buscador de Emitir ni se puede estampar.
- Al fallar el archivado se hace *rollback* explícito (Flask hacía commit aunque se hubiera capturado el error).
- El archivo guarda el **papel** con que se estampó cada marca, no el tipo del catálogo.
- Cada endpoint nuevo exige sesión y rol; los POST llevan CSRF; todo el SQL va parametrizado; la interfaz
  arma el DOM con `textContent` (nada de HTML de la base se inyecta).

## Verificación

- 46 pruebas en verde (incluyen un Postgres real embebido): emisión, archivo, revisión en lote, carga del
  catálogo, detector, guía oficial.
- Ensayo de migración con el respaldo real de producción (ver despliegue.md).
- Recorrido completo en el navegador con el catálogo real y con una guía oficial de SENACSA.

## Límites conocidos

- El detector de no-marcas es una ayuda: en muestras a ojo acertó ~85-95 % de lo que marca, y se le escapan
  sellos grandes y rayas con mucho ruido. Por eso todo pasa por revisión humana.
- Sin probar en teléfono; sin probar con el volumen real de Railway.
- Un PNG del catálogo (`L3210_000627.png`) está truncado en origen y no se carga.
- La guía oficial usada para probar es de una sola operación; otras variantes de formulario (compra con Rubro 2
  ocupado, guías con distinto número de hojas de anexo) no se probaron con PDF reales.
