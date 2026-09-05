# Arquitectura propuesta

Este documento responde a los requisitos que surgieron de la entrevista:
búsqueda por similitud, PDF oficial del gobierno como base, múltiples usuarios
con roles, disponibilidad 24/7 y uso desde cualquier dispositivo.

Lo que ya está construido (pipeline de digitalización, catálogo, planillas) es
la **etapa 0**: funciona hoy en una máquina y es la base sobre la que se monta
todo lo demás.

---

## 1. Búsqueda por similitud (imagen cargada o panel de dibujo)

La búsqueda no la resuelve una base de datos: la resuelve un **descriptor** de
cada marca, más una distancia. La ventaja de este caso es que la consulta y el
catálogo **viven en el mismo dominio**: los dos son dibujos de línea en blanco
y negro. No hay que salvar la brecha "foto real → dibujo", que es la parte
difícil de la búsqueda visual.

El truco es que la consulta pase **por el mismo pipeline** que las marcas del
catálogo (`limpiar_marca`): así lo que se compara son dos lienzos de 1024 px,
centrados, escalados igual y con el mismo grosor relativo.

Tres capas, en orden de costo:

**Capa 1 — filtro rápido, sobre todo el catálogo.** Descriptor concatenado y
normalizado:

- rejilla de densidad de tinta 16×16 sobre el lienzo normalizado (256 valores);
- histograma de orientaciones del trazo (HOG, 8 direcciones × 4×4 celdas);
- momentos de Hu en escala logarítmica (7 valores);
- proporción, porcentaje de tinta y cantidad de trazos (los que ya guarda el
  catálogo).

Distancia coseno. Con 500 a 5.000 marcas esto es **fuerza bruta en NumPy en
menos de un milisegundo**: no hace falta índice vectorial todavía.

**Capa 2 — reordenamiento fino, sólo sobre los 50 mejores.** *Chamfer
matching*: distancia media de cada punto del trazo consultado al punto más
cercano del trazo candidato, probando un puñado de alineaciones (rotaciones de
90°, espejo, pequeños desplazamientos). Es el método clásico para dibujos de
línea, es determinista y se puede explicar: "coincide en un 87 % del trazo".

**Capa 3 — sólo si las dos anteriores no alcanzan.** Un *embedding* de red
neuronal entrenado por contraste con aumentaciones sintéticas de las propias
marcas (rotación, grosor, deformación elástica, ruido). Es factible con 500
marcas justamente porque las aumentaciones son gratis. Pero **no empezar por
acá**: agrega GPU, entrenamiento y una caja negra para un problema que las
capas 1 y 2 probablemente ya resuelven.

Cómo decidir: armar un conjunto de prueba de 50 consultas (redibujar a mano
marcas que ya están en el catálogo) y medir **cuántas veces la correcta aparece
entre las 5 primeras**. Si la capa 1+2 pasa el 90 %, la capa 3 no se justifica.

Dos usos distintos, misma maquinaria:

- **Alta de una marca nueva:** avisar si se parece demasiado a una existente
  (evitar duplicados en el registro).
- **Consulta de campo:** alguien dibuja lo que vio en el animal y el sistema
  ofrece los candidatos.

En los dos casos el sistema **propone y ordena; decide la persona**. Nunca
debería declarar por sí solo que dos marcas son la misma.

**Panel de dibujo:** un `<canvas>` con *pointer events* (anda con dedo, lápiz y
mouse), grosor fijo, botón de deshacer y de limpiar. Exporta PNG y entra por el
mismo pipeline.

## 2. El PDF oficial como base — **resuelto**

El formulario se descarga del sitio del gobierno con un número de orden que
cambia en cada descarga, así que no se puede regenerar: se conserva tal cual y
se le **superponen** las marcas.

Está implementado en `marcas/pdf/guia.py` y probado sobre una guía real. El
flujo:

1. El usuario descarga el formulario del sitio oficial y lo **sube** al sistema
   (uno por trámite; no se guarda como plantilla).
2. El sistema **lee las casillas del propio PDF**: los recuadros del anexo son
   rectángulos vectoriales, así que no hace falta calibrar coordenadas a mano
   ni mantener una plantilla por versión del formulario.
3. Se descartan las casillas ya ocupadas (la marca dominante impresa, las
   anuladas con "X") y las marcas se reparten entre las libres, **iguales en
   las cuatro copias**.
4. Se fusiona una capa transparente sobre las páginas originales: número de
   orden, código de barras y QR quedan intactos.

El detalle del formulario y lo que queda por confirmar está en
[guia_oficial.md](guia_oficial.md).

## 3. Base de datos

**PostgreSQL** (16 o superior), gestionado por un proveedor (Supabase, Neon,
RDS, Cloud SQL) para tener el 24/7 sin administrar servidores.

Por qué, concretamente para este caso:

- **Concurrencia real (MVCC):** varios usuarios cargando y consultando sin
  bloquearse. SQLite serializa las escrituras: sirve para una máquina, no para
  una oficina conectada.
- **Roles y permisos** a nivel de base, además de los de la aplicación.
- **Respaldo continuo (PITR):** se puede volver al estado de "ayer a las 14:30".
  En un registro de marcas, perder datos no es una opción.
- **`pgvector`** para guardar los descriptores de similitud junto a los datos,
  con índice HNSW cuando el catálogo crezca.
- **`pg_trgm` + `unaccent`** para buscar por nombre de propietario tolerando
  errores de tipeo y tildes.

**Las imágenes no van adentro de la base.** Van a almacenamiento de objetos
(S3, Cloudflare R2, Supabase Storage) y en la base queda la ruta más el
`sha256`. La base se mantiene chica, los respaldos rápidos y los archivos
siguen siendo utilizables por fuera del sistema.

SQLite sigue siendo útil: desarrollo local y, si hiciera falta, un modo de
consulta sin conexión.

## 4. Arquitectura y lenguaje

**Backend: Python con Django + Django REST Framework.**

- Python porque **todo el procesamiento de imágenes ya está en Python**
  (OpenCV, potrace, NumPy). Partir el sistema en dos lenguajes obligaría a
  mantener dos entornos y a mover imágenes entre procesos sin ganancia alguna.
- Django y no un framework mínimo porque trae **de fábrica** exactamente lo que
  pide el requisito: usuarios, roles y permisos, panel de administración,
  migraciones y sesiones. Construir eso a mano son semanas de trabajo que no
  aportan nada propio del negocio.

**Tareas pesadas en segundo plano: Celery + Redis.** Procesar un lote de
escaneos o recalcular los descriptores de 500 marcas no puede bloquear una
petición web.

**Frontend: aplicación web responsiva instalable (PWA).** Es lo que cumple
"usarse en cualquier lugar y dispositivo" sin publicar apps en las tiendas ni
mantener versiones para Android e iOS: entra por el navegador, se instala en la
pantalla de inicio y funciona en PC, tablet y celular. Para la primera versión,
**plantillas de Django + HTMX + un `<canvas>` para el panel de dibujo**: una
sola base de código. Si más adelante la interfaz se vuelve muy rica, se migra
esa capa a React sin tocar el backend.

**Despliegue: Docker.** Una imagen con la aplicación, OpenCV y potrace, que
corre igual en un VPS, en una PaaS o en el servidor de la empresa. Base de
datos y almacenamiento gestionados aparte.

**Roles sugeridos:**

| Rol | Puede |
|---|---|
| Administrador | Todo, incluido gestionar usuarios |
| Registrador | Digitalizar lotes, dar de alta y editar marcas y propietarios |
| Operador | Buscar, seleccionar y generar PDF |
| Consulta | Sólo ver y buscar |

**Auditoría desde el primer día:** quién creó o modificó cada marca y cuándo, y
qué marcas entraron en cada PDF generado. En un registro de marcas esto tiene
peso legal, y agregarlo después obliga a reescribir historia que no se tiene.

## 5. Etapas

| Etapa | Qué entrega | Estado |
|---|---|---|
| 0 | Pipeline de digitalización, catálogo, planillas PDF, selector | **hecho** |
| 1 | PostgreSQL + Django, migración del catálogo, usuarios y roles | pendiente |
| 2 | Búsqueda por similitud (capas 1 y 2) + panel de dibujo | pendiente |
| 3 | PDF oficial: carga del formulario, detección de casillas, superposición | **hecho** (falta definir el Rubro 2) |
| 4 | Auditoría, importación de propietarios, PWA, respaldos | pendiente |

El pipeline de la etapa 0 no se tira: `marcas/vectorizacion/` es una biblioteca
independiente de la base de datos y de la interfaz, y se usa igual desde
Django, desde Celery o desde la línea de comandos.

## 6. Qué falta definir

1. **Un ejemplo real de las marcas escaneadas.** Cambia decisiones concretas:
   resolución de escaneo, si vienen en grilla o sueltas en documentos, qué tan
   finos son los trazos y si hay texto junto al dibujo. La marca dominante de
   la guía oficial ya es digital y sirve de referencia de destino: trazo negro
   parejo sobre lienzo cuadrado, que es exactamente lo que produce el
   pipeline.
2. **Si el Rubro 2 de la guía también lleva marcas** además del anexo, y qué
   corresponde poner ahí (ver [guia_oficial.md](guia_oficial.md)).
3. **Volumen y crecimiento:** 500 marcas hoy, ¿cuántas por año?
4. **Cuántas personas usan el sistema a la vez** y desde dónde (oficina, campo
   con señal intermitente).
