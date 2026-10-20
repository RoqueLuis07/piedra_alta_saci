# Lo que falta antes de presentar la demo

Estado al 20-oct-2026. **Veredicto: el sistema es presentable, pero hoy el sitio público NO tiene lo nuevo.**
Producción (`piedra-alta-saci.up.railway.app`) sigue en la versión del 7-oct: no tiene *Emitir PDF*, el
Archivo, la Revisión ni el catálogo. Todo lo demostrable está en la rama `fase1-podar` (PR #1, sin fusionar).

## Opciones para dónde hacer la demo

| Opción | Ventajas | Riesgos |
|---|---|---|
| **A. Producción** (tras fusionar y cargar el catálogo) | Es el sistema real; lo que ven es lo que usarán. | Hay datos de prueba (`PRUEBA-QA-*`) y usuarios reales en esa base; un error en vivo toca datos reales. |
| **B. Un entorno de demo aparte** (otro servicio + otra base en Railway) | Datos controlados, se puede romper sin consecuencias. **Recomendado.** | Cuesta crear y mantener otro servicio; hay que cargarle el catálogo. |
| **C. Local en tu computadora** | Sin depender de internet. | Necesita un Postgres local (Docker Desktop hoy no está corriendo); menos "real". |

## Bloqueantes (sin esto no se debe presentar)

- [ ] **Desplegar lo nuevo** donde se vaya a presentar (fusionar el PR, o levantar el entorno de demo desde la rama). Ver [despliegue.md](../despliegue.md).
- [ ] **Cargar el catálogo** (2972 marcas) en ese entorno: [carga_catalogo_produccion.md](../carga_catalogo_produccion.md). Sin él, la galería y «buscar en el catálogo» están vacíos.
- [ ] **Limpiar la galería antes de la demo:** correr el detector con `--aplicar` y dar de baja las candidatas, para que las primeras pantallas del catálogo no muestren rayas de anulación como si fueran marcas.
- [ ] **Datos de prueba fuera de vista** (`PRUEBA-QA-*`, `QA-*`): ocultarlos, o usar el entorno B.
- [ ] **Una cuenta de demo** con rol *operador* y contraseña propia (no mostrar la cuenta de administrador ni la lista real de usuarios).
- [ ] **Guía anonimizada** ✅ lista: `Piedra Alta\demo\Guia_DEMO_anonimizada.pdf` (se regenera con `scripts/anonimizar_guia.py`).

## Ensayo obligatorio (el día anterior y 10 min antes)

- [ ] Recorrer el guion completo **tal cual**, con cronómetro.
- [ ] Emitir con la guía anonimizada: dominante dibujada + 2 dibujadas + 5 del catálogo → PDF → Archivo.
- [ ] Probar en la **tablet o teléfono reales** que se puede dibujar con el dedo (en el simulador funciona; con el dedo real no se probó).
- [ ] Probar el sitio en el **navegador y la computadora de la presentación** (se probó sólo en Chromium).
- [ ] Verificar que la proyección se lee bien (modo claro) y que no salen avisos del navegador.
- [ ] Grabar un **video del recorrido** como plan B.

## Importantes (conviene resolverlos, no impiden presentar)

- **Vista previa en el teléfono:** queda al final de la página; quien dibuja no ve el resultado hasta bajar. Mejora pendiente.
- **Sin chequeo de salud en Railway:** si un deploy falla, no hay aviso automático. Configurar `/login`.
- **Tono del texto:** la interfaz usa voseo («Subí», «Dibujá»); confirmar que es el que quiere el cliente.
- **Detector de no-marcas:** acierta ~85–95 % de lo que marca y se le escapan sellos grandes y rayas con ruido. Presentarlo como *ayuda*, no como automático.
- **Tiempos:** la vista previa tarda 1–6 s según la cantidad de marcas; generar el PDF final, unos segundos más.
- **PDF en la base:** cada documento pesa 2–7 MB y queda en el Postgres (volumen de 5 GB, 0,18 GB usados). No es problema para la demo; sí para producción a largo plazo.
- **Datos reales en capturas o videos:** revisar que no aparezcan nombres reales de compradores.

## Lo que NO existe todavía (no prometerlo)

- Visor del archivo histórico escaneado (~764 páginas) ni su asociación automática con propietarios.
- Búsqueda por similitud de marcas.
- Alta masiva de guías históricas.
- Aprobaciones de cambios (se quitó a propósito: Administrador y Operador editan directo).
- Estadísticas y montos (se quitaron a propósito).

## Riesgos honestos a tener presentes

1. **Es una versión sin usuarios reales todavía:** lo nuevo no pasó por el uso diario de las personas que cargan guías.
2. Se probó con **una sola guía oficial** (de una venta). Compras con el Rubro 2 ya ocupado, o guías con otra cantidad de hojas de anexo, no se probaron con PDF reales.
3. El sistema **no tiene pruebas de carga**: pensado para unas pocas personas a la vez.
