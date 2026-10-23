-- Esquema propio en Postgres (Railway), sin depender de Supabase.
--
-- Reemplaza lo que antes vivía repartido entre auth.users/auth.identities
-- (Supabase Auth), políticas de RLS y un bucket de Storage aparte. Ahora
-- todo -- usuarios, datos y hasta las imágenes de las marcas -- vive en
-- esta misma base, para que un solo respaldo de Postgres cubra todo.
--
-- Idempotente: se puede correr de nuevo sin romper nada (CREATE ... IF NOT
-- EXISTS / OR REPLACE en todo lo que lo admite).

create extension if not exists pgcrypto;  -- gen_random_uuid()

create table if not exists usuarios (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  nombre text not null default '',
  rol text not null default 'consulta' check (rol in ('administrador', 'operador', 'consulta')),
  activo boolean not null default false,
  creado_en timestamptz not null default now()
);

create table if not exists propietarios (
  id bigserial primary key,
  nombre text not null,
  documento text,
  establecimiento text,
  establecimiento_codigo text,
  localidad text,
  departamento text,
  telefono text,
  creado_en timestamptz not null default now()
);

-- El código de marca (M-00001, M-00002, ...) lo asigna la base, secuencial,
-- para que nadie tenga que inventarlo ni se puedan pisar dos altas a la vez.
create sequence if not exists marcas_codigo_seq start 1;

create or replace function siguiente_codigo_marca() returns text as $$
  select 'M-' || lpad(nextval('marcas_codigo_seq')::text, 5, '0');
$$ language sql;

create table if not exists operaciones (
  id bigserial primary key,
  numero_guia text,
  fecha text,
  vendedor_nombre text,
  vendedor_documento text,
  vendedor_establecimiento text,
  vendedor_establecimiento_codigo text,
  comprador_nombre text,
  comprador_documento text,
  cantidad_animales integer,
  categoria_animales text,
  categoria_animales_original text,
  tipo_formulario text,
  guia_colisionada boolean not null default false,
  revisar text,
  origen text,
  origen_id bigint,
  creado_por uuid references usuarios(id),
  creado_en timestamptz not null default now(),
  -- compra: Piedra Alta compra ganado (vendedor es un tercero).
  -- venta: Piedra Alta vende (comprador es un tercero).
  tipo_operacion text not null default 'compra' check (tipo_operacion in ('compra', 'venta')),
  -- Estado guardado a pedido del usuario mientras arma la impresión de una
  -- Venta (marcas elegidas + PDF/Dominante ya subidos), para poder
  -- continuar más tarde sin perder lo cargado. Null cuando no hay borrador.
  borrador_venta jsonb,
  borrador_pdf bytea,
  borrador_pdf_nombre text,
  borrador_dominante_png bytea
);

create table if not exists marcas (
  id bigserial primary key,
  codigo text not null unique default siguiente_codigo_marca(),
  descripcion text,
  propietario_id bigint references propietarios(id),
  operacion_id bigint references operaciones(id),
  tipo text check (tipo in ('dominante', 'complementaria')),
  posicion text,
  numero_guia text,
  origen_archivo text,
  -- Antes: nombre de archivo en el bucket de Supabase Storage.
  -- Ahora: la imagen misma, directo en la base.
  archivo_png bytea,
  archivo_svg bytea,
  borde_limpiado boolean not null default false,
  sospechosa_calidad boolean not null default false,
  motivo_calidad text,
  estado text not null default 'activa' check (estado in ('activa', 'revisar', 'baja')),
  observaciones text,
  creado_por uuid references usuarios(id),
  actualizado_por uuid references usuarios(id),
  creado_en timestamptz not null default now(),
  actualizado_en timestamptz not null default now(),
  vence_en date
);

-- Antes lo hacía un trigger propio de Supabase -- se recrea acá para que
-- "actualizado_en" siga reflejando el último cambio real sin que cada
-- UPDATE en el código tenga que acordarse de pisarlo a mano.
create or replace function _marcas_actualizar_marca_de_tiempo() returns trigger as $$
begin
  -- clock_timestamp() (no now()): now() devuelve el mismo valor durante
  -- toda la transacción, así que dos UPDATE seguidos en la misma request
  -- quedarían con idéntico actualizado_en si se usara now().
  new.actualizado_en := clock_timestamp();
  return new;
end;
$$ language plpgsql;

drop trigger if exists marcas_actualizado_en on marcas;
create trigger marcas_actualizado_en
  before update on marcas
  for each row
  execute function _marcas_actualizar_marca_de_tiempo();

create table if not exists cambios_pendientes (
  id bigserial primary key,
  tabla text not null check (tabla in ('marcas', 'operaciones')),
  fila_id bigint not null,
  cambios jsonb not null,
  valores_anteriores jsonb not null,
  propuesto_por uuid not null references usuarios(id),
  propuesto_en timestamptz not null default now(),
  estado text not null default 'pendiente' check (estado in ('pendiente', 'aprobado', 'rechazado')),
  revisado_por uuid references usuarios(id),
  revisado_en timestamptz,
  motivo_rechazo text
);

create index if not exists idx_marcas_operacion_id on marcas(operacion_id);
create index if not exists idx_marcas_propietario_id on marcas(propietario_id);
create index if not exists idx_marcas_estado on marcas(estado);
create index if not exists idx_marcas_tipo on marcas(tipo);
create index if not exists idx_cambios_pendientes_estado on cambios_pendientes(estado);
create index if not exists idx_operaciones_tipo_operacion on operaciones(tipo_operacion);
create index if not exists idx_operaciones_creado_en on operaciones(creado_en);

-- Ocultar (nunca borrado físico -- son documentos con valor legal/SENACSA):
-- un registro oculto sigue en la base tal cual, pero desaparece de los
-- listados/búsquedas por defecto. Reversible, y sólo lo hace Administrador.
-- ALTER (no en el CREATE TABLE de arriba) para que se aplique también sobre
-- una base ya desplegada, no sólo en una instalación nueva.
alter table marcas add column if not exists activo boolean not null default true;
alter table operaciones add column if not exists activo boolean not null default true;
alter table propietarios add column if not exists activo boolean not null default true;

-- Filtros guardados y columnas configurables en los listados (Guías,
-- Ventas, Marcas): por defecto se muestran todas las columnas, así que
-- nadie ve un cambio hasta que lo configura.
create table if not exists filtros_guardados (
  id bigserial primary key,
  usuario_id uuid not null references usuarios(id) on delete cascade,
  seccion text not null check (seccion in ('guias', 'ventas', 'marcas')),
  nombre text not null,
  parametros jsonb not null default '{}'::jsonb,
  creado_en timestamptz not null default now()
);

create index if not exists idx_filtros_guardados_usuario on filtros_guardados(usuario_id, seccion);

-- preferencias.columnas.<seccion> = ["fecha", "numero_guia", ...]
alter table usuarios add column if not exists preferencias jsonb not null default '{}'::jsonb;

create index if not exists idx_marcas_activo on marcas(activo) where not activo;
create index if not exists idx_operaciones_activo on operaciones(activo) where not activo;
create index if not exists idx_propietarios_activo on propietarios(activo) where not activo;

-- Monto total en guaraníes: en una Venta se lee solo del "Monto a Pagar" de
-- la Boleta de Pago del PDF de SENACSA al generar el documento (es el único
-- valor de dinero que trae el propio documento oficial); en una Guía
-- (compra) se carga a mano, porque no hay ningún PDF con ese dato todavía
-- en ese momento del proceso.
alter table operaciones add column if not exists monto_total bigint;

-- Respaldo permanente del documento que se recibe del vendedor al cargar
-- una Compra (Guía): el PDF tal cual, o -- si se subieron fotos/escaneos
-- sueltos -- todas combinadas en un solo PDF de varias páginas. Se guarda
-- sin modificar (no se estampa ni se completa, a diferencia del PDF de
-- SENACSA que se sube al generar el documento final) como segundo
-- comprobante junto a los datos que se cargan a mano en el formulario.
--
-- Tabla aparte (no una columna más en "operaciones"): la ficha de una
-- guía/venta trae toda la fila de "operaciones" de una sola vez
-- (SELECT *), así que un archivo de varios MB ahí adentro viajaría en
-- cada carga de esa página aunque nadie vaya a verlo. Acá sólo se trae
-- cuando alguien pide efectivamente descargarlo.
create table if not exists respaldos_operacion (
  operacion_id bigint primary key references operaciones(id) on delete cascade,
  contenido bytea not null,
  nombre text,
  creado_en timestamptz not null default now()
);

-- Si esta base ya tenía las columnas de una versión anterior de este mismo
-- cambio (respaldo_pdf/respaldo_pdf_nombre en "operaciones"), migra lo que
-- hubiera cargado a la tabla nueva antes de borrarlas -- en una base recién
-- creada esas columnas no existen y este bloque no hace nada.
do $$
begin
  if exists (
    select 1 from information_schema.columns
    where table_name = 'operaciones' and column_name = 'respaldo_pdf'
  ) then
    insert into respaldos_operacion (operacion_id, contenido, nombre)
    select id, respaldo_pdf, respaldo_pdf_nombre
    from operaciones
    where respaldo_pdf is not null
    on conflict (operacion_id) do nothing;

    alter table operaciones drop column respaldo_pdf;
    alter table operaciones drop column respaldo_pdf_nombre;
  end if;
end $$;

-- Archivo de documentos emitidos: cada vez que se genera el PDF final de una
-- Guía o de una Venta, queda guardada una copia exacta junto con la fecha y
-- una "foto" de todos los datos de la operación y de las marcas estampadas en
-- ese momento (así el archivo sigue siendo fiel aunque después se edite la
-- operación). Tabla aparte: el PDF pesa y no debe viajar en cada SELECT * de
-- "operaciones". Nunca se borra.
create table if not exists documentos_emitidos (
  id bigserial primary key,
  operacion_id bigint not null references operaciones(id),
  tipo text not null check (tipo in ('guia', 'venta')),
  numero_guia text,
  emitido_en timestamptz not null default now(),
  emitido_por uuid references usuarios(id),
  datos jsonb not null default '{}'::jsonb,
  marcas jsonb not null default '[]'::jsonb,
  nombre text not null,
  contenido bytea not null
);

create index if not exists idx_documentos_emitidos_fecha on documentos_emitidos(emitido_en desc);
create index if not exists idx_documentos_emitidos_operacion on documentos_emitidos(operacion_id);

-- Guías escaneadas: cada página escaneada de los documentos históricos (guía GE2, anexo,
-- certificado sanitario, declaración jurada CIEFA...), con lo que se pudo leer de ella
-- (N.º de guía, propietario, cantidades) y el texto completo del OCR para buscar. Las páginas
-- con el mismo N.º de guía forman una guía. Las imágenes van en una tabla aparte para que
-- los listados no las arrastren. Nada de esto se borra: se corrige a mano desde la pantalla.
create table if not exists escaneos (
  id bigserial primary key,
  carpeta text not null,
  archivo text not null,
  orden integer not null default 0,
  formato text,
  numero_guia text,
  fuente_numero text,
  confianza_numero real,
  numero_certificado text,
  propietario text,
  propietario_documento text,
  establecimiento text,
  cantidad_animales integer,
  fecha_texto text,
  n_marcas integer not null default 0,
  texto_ocr text,
  ancho integer,
  alto integer,
  revisado boolean not null default false,
  notas text,
  creado_en timestamptz not null default now(),
  unique (carpeta, archivo)
);

create table if not exists escaneos_imagenes (
  escaneo_id bigint primary key references escaneos(id) on delete cascade,
  imagen bytea not null,
  miniatura bytea not null
);

create index if not exists idx_escaneos_numero on escaneos(numero_guia);
create index if not exists idx_escaneos_orden on escaneos(carpeta, orden);

-- De qué página salió cada marca (para verla en su contexto y comprobar que es una marca de verdad).
alter table marcas add column if not exists escaneo_id bigint references escaneos(id);
-- Huella de la imagen para la búsqueda por dibujo (ver marcas/servidor/similitud.py); se calcula sola.
alter table marcas add column if not exists huella bytea;
alter table marcas add column if not exists origen_carpeta text;
alter table marcas add column if not exists origen_pagina text;
create index if not exists idx_marcas_escaneo on marcas(escaneo_id);

-- Origen normalizado: Carpeta 1 = «Epson L3210»; Carpeta 2 = «Epson L5290» (Parte 1.pdf, Parte 2.pdf e imágenes sueltas).
-- La página es el número de imagen del archivo. Se calcula solo (idempotente) y se muestra como «Carpeta 2 · Parte 1 · pág. 17».
alter table escaneos add column if not exists carpeta_n smallint;
alter table escaneos add column if not exists parte smallint;
alter table escaneos add column if not exists pagina_n integer;
alter table escaneos add column if not exists etiqueta text;
alter table marcas add column if not exists origen_carpeta_n smallint;
alter table marcas add column if not exists origen_parte smallint;
alter table marcas add column if not exists origen_pagina_n integer;
alter table marcas add column if not exists origen_etiqueta text;

update escaneos set
  carpeta_n = case when carpeta = 'L3210' then 1 else 2 end,
  parte = case carpeta when 'PARTE1' then 1 when 'PARTE2' then 2 end,
  pagina_n = nullif(substring(archivo from '([0-9]+)\.[A-Za-z]+$'), '')::int,
  etiqueta = 'Carpeta ' || case when carpeta = 'L3210' then '1' else '2' end
             || case carpeta when 'PARTE1' then ' · Parte 1' when 'PARTE2' then ' · Parte 2' else '' end
             || ' · pág. ' || coalesce(nullif(substring(archivo from '([0-9]+)\.[A-Za-z]+$'), '')::int::text, archivo)
where etiqueta is null and carpeta in ('L3210', 'L5290', 'PARTE1', 'PARTE2');

update marcas set
  origen_carpeta_n = case when origen_carpeta = 'L3210' then 1 else 2 end,
  origen_parte = case origen_carpeta when 'PARTE1' then 1 when 'PARTE2' then 2 end,
  origen_pagina_n = nullif(substring(origen_pagina from '([0-9]+)\.[A-Za-z]+$'), '')::int,
  origen_etiqueta = 'Carpeta ' || case when origen_carpeta = 'L3210' then '1' else '2' end
             || case origen_carpeta when 'PARTE1' then ' · Parte 1' when 'PARTE2' then ' · Parte 2' else '' end
             || ' · pág. ' || coalesce(nullif(substring(origen_pagina from '([0-9]+)\.[A-Za-z]+$'), '')::int::text, origen_pagina)
where origen_etiqueta is null and origen_carpeta in ('L3210', 'L5290', 'PARTE1', 'PARTE2');

create index if not exists idx_marcas_origen on marcas(origen_carpeta_n, origen_parte, origen_pagina_n);
create index if not exists idx_escaneos_origen on escaneos(carpeta_n, parte, pagina_n);

-- Historial de la imagen de cada marca: cada vez que se reemplaza (o se vuelve a una versión
-- anterior) la imagen de una marca, la que estaba puesta queda guardada acá. Nunca se borra
-- sola: sirve para corregir una marca mal trazada y poder volver atrás. Tabla aparte para que
-- los listados de marcas no arrastren las imágenes.
create table if not exists marcas_versiones_imagen (
  id bigserial primary key,
  marca_id bigint not null references marcas(id) on delete cascade,
  archivo_png bytea,
  archivo_svg bytea,
  motivo text not null default 'reemplazo' check (motivo in ('reemplazo', 'restauracion')),
  creado_por uuid references usuarios(id),
  creado_en timestamptz not null default now()
);

create index if not exists idx_marcas_versiones_marca on marcas_versiones_imagen(marca_id, creado_en desc);
