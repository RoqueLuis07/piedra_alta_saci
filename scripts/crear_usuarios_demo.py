"""Crea tres usuarios de DEMOSTRACIÓN (administrador, operador y consulta) con
contraseñas aleatorias que se muestran UNA sola vez en pantalla.

Sólo para un entorno de demo. Exige --confirmo-que-es-demo y se niega a correr si
la base ya tiene muchas operaciones (señal de que podría ser la de producción).

    python scripts/crear_usuarios_demo.py --confirmo-que-es-demo
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from marcas.servidor.auth import encriptar_contrasena  # noqa: E402

USUARIOS = (
    ("demo.admin@piedraalta.demo", "Demo Administrador", "administrador"),
    ("demo.operador@piedraalta.demo", "Demo Operador", "operador"),
    ("demo.consulta@piedraalta.demo", "Demo Consulta", "consulta"),
)
LIMITE_OPERACIONES = 25   # una base de demo tiene muy pocas; la de producción, más


def crear(conexion) -> list[tuple[str, str, str | None]]:
    """[(email, rol, contraseña o None si ya existía)]"""
    resultado = []
    with conexion.cursor() as cur:
        for email, nombre, rol in USUARIOS:
            cur.execute("SELECT 1 FROM usuarios WHERE email = %s", (email,))
            if cur.fetchone():
                resultado.append((email, rol, None))
                continue
            contrasena = secrets.token_urlsafe(9)
            cur.execute(
                "INSERT INTO usuarios (email, password_hash, nombre, rol, activo) VALUES (%s, %s, %s, %s, true)",
                (email, encriptar_contrasena(contrasena), nombre, rol),
            )
            resultado.append((email, rol, contrasena))
    conexion.commit()
    return resultado


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirmo-que-es-demo", action="store_true", dest="confirmo")
    args = ap.parse_args()
    if not args.confirmo:
        print("Falta --confirmo-que-es-demo. Este script es sólo para un entorno de demostración.", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    with conexion.cursor() as cur:
        cur.execute("SELECT current_database() AS base")
        base = cur.fetchone()["base"]
        cur.execute("SELECT count(*) AS n FROM operaciones")
        operaciones = cur.fetchone()["n"]
    if operaciones > LIMITE_OPERACIONES:
        print(f"La base «{base}» tiene {operaciones} operaciones: parece de producción. No se creó nada.", file=sys.stderr)
        return 3
    print(f"Base «{base}» ({operaciones} operaciones).")
    for email, rol, contrasena in crear(conexion):
        print(f"  {rol:14s} {email:34s} " + (contrasena if contrasena else "(ya existía: sin cambios)"))
    print("Anotá las contraseñas ahora: no se vuelven a mostrar.")
    conexion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
