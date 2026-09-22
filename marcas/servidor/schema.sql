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
alter table operaciones add column if not exists respaldo_pdf bytea;
alter table operaciones add column if not exists respaldo_pdf_nombre text;
