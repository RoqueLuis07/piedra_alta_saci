"""Conexión a la base propia (Postgres en Railway).

Reemplaza a ``supa.py``. Ya no hay RLS ni distintos "clientes" según el rol
de quien está logueado -- todas las consultas van con la misma conexión, y
``requiere_sesion``/``requiere_rol`` (en ``auth.py``) son la barrera real.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg2
import psycopg2.pool
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")

_pool: psycopg2.pool.SimpleConnectionPool | None = None


def _obtener_pool() -> psycopg2.pool.SimpleConnectionPool:
    global _pool
    if not DATABASE_URL:
        raise RuntimeError("Falta DATABASE_URL (variable de entorno) para conectar a Postgres.")
    if _pool is None:
        # min 1 / max 5 por worker de gunicorn -- alcanza de sobra para el
        # tráfico de unas pocas personas usando el sistema a la vez.
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL, cursor_factory=RealDictCursor)
    return _pool


def obtener_conexion():
    """Una conexión nueva del pool -- quien la pide es responsable de
    devolverla con :func:`devolver_conexion` (``conexion_actual()``/el
    ``teardown_appcontext`` de la app ya lo hacen solos)."""
    return _obtener_pool().getconn()


def devolver_conexion(conexion) -> None:
    _obtener_pool().putconn(conexion)


def ejecutar_schema(conexion=None) -> None:
    """Aplica schema.sql -- idempotente, se puede correr en cada deploy."""
    ruta = Path(__file__).parent / "schema.sql"
    sql = ruta.read_text(encoding="utf-8")
    propia = conexion is None
    conexion = conexion or obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute(sql)
        conexion.commit()
    finally:
        if propia:
            devolver_conexion(conexion)
