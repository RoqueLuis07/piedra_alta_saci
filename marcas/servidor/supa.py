"""Clientes de Supabase.

Hay dos formas muy distintas de hablar con Supabase, y mezclarlas es
exactamente el tipo de error que expondría datos de un rol a otro:

* :func:`cliente_sesion` -- el que usa la aplicación en cada request normal.
  Lleva el token de acceso de la persona que inició sesión, así que
  PostgREST aplica las políticas de RLS **como esa persona**: un Consulta
  nunca puede leer más de lo que su rol permite, pase lo que pase en el
  código de arriba.
* :func:`cliente_administrador` -- la clave ``service_role``, que se salta
  RLS por completo. Sólo la usa el script de migración inicial
  (``scripts/migrar_a_supabase.py``), nunca una request de un usuario. Si
  algún día hace falta acá, hay que poder justificar por qué ese pedido
  puntual necesita saltarse los roles.
"""

from __future__ import annotations

import os

from supabase import Client, create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def _verificar_configuracion() -> None:
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_ANON_KEY como variables de entorno."
        )


def cliente_anonimo() -> Client:
    """Sin sesión: lo único que se puede hacer con esto es iniciar sesión."""
    _verificar_configuracion()
    return create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def cliente_sesion(access_token: str) -> Client:
    """Cliente con las políticas de RLS aplicadas como el usuario logueado."""
    _verificar_configuracion()
    cliente = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    cliente.postgrest.auth(access_token)
    return cliente


def cliente_administrador() -> Client:
    """Se salta RLS por completo -- sólo para el script de migración."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY como variables de entorno."
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
