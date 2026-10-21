"""Comprueba a qué base se está por conectar un script (sin mostrar la contraseña
ni la dirección) y cuántas filas tiene cada tabla del sistema. Sólo lee.

    python scripts/verificar_conexion.py      # usa la variable DATABASE_URL
"""

from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

TABLAS = ("usuarios", "propietarios", "operaciones", "marcas", "documentos_emitidos", "cambios_pendientes")


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    try:
        conexion = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    except psycopg2.Error as exc:
        print(f"No se pudo conectar: {str(exc).strip().splitlines()[0]}", file=sys.stderr)
        return 1
    with conexion.cursor() as cur:
        cur.execute("SELECT current_database() AS base, split_part(version(), ' ', 2) AS version, "
                    "pg_size_pretty(pg_database_size(current_database())) AS peso")
        info = cur.fetchone()
        print(f"Base: {info['base']} · PostgreSQL {info['version']} · {info['peso']}")
        for tabla in TABLAS:
            cur.execute("SELECT to_regclass(%s) AS t", (tabla,))
            if cur.fetchone()["t"] is None:
                print(f"  {tabla:22s} (no existe todavía)")
                continue
            cur.execute(f"SELECT count(*) AS n FROM {tabla}")
            print(f"  {tabla:22s} {cur.fetchone()['n']:>7d} filas")
    conexion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
