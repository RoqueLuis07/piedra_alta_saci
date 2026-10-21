"""Restaura en una base NUEVA el respaldo que deja ``scripts/respaldo_a_github.py``
(usuarios con sus contraseñas, propietarios, operaciones, marcas con sus imágenes y
cambios pendientes). Sirve para pasar el sistema a otro proveedor o recuperarlo.

Antes hay que aplicar el esquema (``flask --app wsgi crear-esquema``, que el arranque del
servicio ya hace). Se niega a correr sobre una base que ya tiene datos, salvo --forzar.

    git clone https://github.com/RoqueLuis07/piedra-alta-respaldos      # repo privado
    python scripts/restaurar_respaldo.py --carpeta piedra-alta-respaldos/respaldo --en-seco
    python scripts/restaurar_respaldo.py --carpeta piedra-alta-respaldos/respaldo

Usa la variable DATABASE_URL. Los PDF de los documentos emitidos NO están en el respaldo
(sólo sus datos), así que no se restauran.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, RealDictCursor

ORDEN = ("usuarios", "propietarios", "operaciones", "marcas", "cambios_pendientes")
BYTEA = {"marcas": ("archivo_png", "archivo_svg"), "operaciones": ("borrador_pdf", "borrador_dominante_png")}


def leer_respaldo(carpeta: Path) -> dict[str, list[dict]]:
    def leer(ruta: Path) -> list[dict]:
        return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else []

    datos = {t: leer(carpeta / f"{t}.json") for t in ORDEN if t != "marcas"}
    marcas: list[dict] = []
    for lote in sorted((carpeta / "marcas").glob("lote_*.json")):
        marcas += leer(lote)
    datos["marcas"] = marcas
    return datos


def _columnas(cur, tabla: str) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (tabla,))
    return {r["column_name"] for r in cur.fetchall()}


def restaurar(conexion, datos: dict[str, list[dict]], escribir: bool = True, forzar: bool = False) -> dict:
    cuentas = {t: len(datos[t]) for t in ORDEN}
    with conexion.cursor() as cur:
        for t in ORDEN:
            cur.execute("SELECT to_regclass(%s) AS t", (t,))
            if cur.fetchone()["t"] is None:
                raise RuntimeError(f"Falta la tabla «{t}»: aplicá primero el esquema (flask --app wsgi crear-esquema).")
        cur.execute("SELECT (SELECT count(*) FROM usuarios) + (SELECT count(*) FROM operaciones) + (SELECT count(*) FROM marcas) AS n")
        existentes = cur.fetchone()["n"]
        if existentes and not forzar:
            raise RuntimeError(f"La base ya tiene {existentes} filas (usuarios/operaciones/marcas). No se restaura sobre datos "
                               "existentes; usá --forzar sólo si estás seguro.")
        if not escribir:
            return {**cuentas, "en_seco": True}
        for tabla in ORDEN:
            columnas = _columnas(cur, tabla)
            for fila in datos[tabla]:
                valores = {}
                for k, v in fila.items():
                    if k not in columnas:
                        continue
                    if k in BYTEA.get(tabla, ()) and v is not None:
                        v = psycopg2.Binary(base64.b64decode(v))
                    elif isinstance(v, (dict, list)):
                        v = Json(v)
                    valores[k] = v
                cols = list(valores)
                sql = f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)})"
                cur.execute(sql + (" ON CONFLICT DO NOTHING" if forzar else ""), valores)
        # Las filas se insertaron con su id original: las secuencias tienen que seguir desde el máximo.
        for tabla in ("propietarios", "operaciones", "marcas", "cambios_pendientes"):
            cur.execute(
                f"SELECT setval(pg_get_serial_sequence('{tabla}', 'id'), greatest((SELECT coalesce(max(id), 0) FROM {tabla}), 1), "
                f"(SELECT count(*) > 0 FROM {tabla}))"
            )
        cur.execute("SELECT coalesce(max(substring(codigo from '[0-9]+$')::int), 0) AS n FROM marcas")
        maximo = cur.fetchone()["n"]
        cur.execute("SELECT setval('marcas_codigo_seq', greatest(%s, 1), %s)", (maximo, maximo > 0))
    conexion.commit()
    return cuentas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--carpeta", required=True, type=Path, help="carpeta «respaldo» del repositorio de respaldos")
    ap.add_argument("--en-seco", action="store_true", help="leer y contar sin escribir nada")
    ap.add_argument("--forzar", action="store_true", help="permitir restaurar sobre una base que ya tiene datos")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    datos = leer_respaldo(args.carpeta)
    manifiesto = args.carpeta / "manifiesto.json"
    esperado = json.loads(manifiesto.read_text(encoding="utf-8")).get("cantidades", {}) if manifiesto.is_file() else {}
    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=20)
    try:
        r = restaurar(conexion, datos, escribir=not args.en_seco, forzar=args.forzar)
    except RuntimeError as exc:
        print(f"No se restauró nada: {exc}", file=sys.stderr)
        return 3
    finally:
        conexion.close()
    print(("[EN SECO, no se escribió nada] " if r.get("en_seco") else "Restaurado: ")
          + " · ".join(f"{t} {r[t]}" for t in ORDEN))
    faltan = {t: (esperado[t], r[t]) for t in ORDEN if t in esperado and esperado[t] != r[t]}
    if faltan:
        print("OJO: no coincide con el manifiesto del respaldo (esperado, leído):", faltan, file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
