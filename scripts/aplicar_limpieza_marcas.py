"""Da de BAJA las marcas del catálogo que no son marcas (X de anulación, líneas, sellos, firmas, manchas),
según la revisión hecha a ojo (``datos/limpieza_marcas_2026-10-23.json``). Nada se borra: pasan a estado «baja»
con el motivo ``no_es_marca:<categoría>`` y se pueden volver a poner desde Marcas → Revisión, o con ``--deshacer``.

    # en seco (cuenta lo que haría, no escribe):
    python scripts/aplicar_limpieza_marcas.py
    # aplicar (guarda un reporte con el estado anterior de cada marca, para poder deshacer):
    python scripts/aplicar_limpieza_marcas.py --aplicar --reporte reporte_limpieza.csv
    # deshacer todo lo que hizo esa corrida:
    python scripts/aplicar_limpieza_marcas.py --deshacer reporte_limpieza.csv

Usa DATABASE_URL. No toca las marcas que ya están en una operación (guía/venta), ni las que ya están de baja.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "datos" / "limpieza_marcas_2026-10-23.json"
LOTE = 500


def leer_decisiones(ruta: Path = ARCHIVO) -> dict[str, str]:
    return json.loads(ruta.read_text(encoding="utf-8"))["descartar"]


def planificar(conexion, descartar: dict[str, str]) -> tuple[list[dict], dict]:
    """Las marcas a dar de baja y por qué algunas se saltean."""
    cuentas = {"en_la_lista": len(descartar), "no_estan_en_la_base": 0, "ya_de_baja": 0, "en_una_operacion": 0, "a_dar_de_baja": 0}
    a_cambiar: list[dict] = []
    encontrados = set()
    with conexion.cursor() as cur:
        archivos = list(descartar)
        for i in range(0, len(archivos), LOTE):
            cur.execute(
                "SELECT id, codigo, origen_archivo, estado, sospechosa_calidad, motivo_calidad, operacion_id "
                "FROM marcas WHERE origen_archivo = ANY(%s)", (archivos[i:i + LOTE],))
            for f in cur.fetchall():
                encontrados.add(f["origen_archivo"])
                if f["estado"] == "baja":
                    cuentas["ya_de_baja"] += 1
                elif f["operacion_id"] is not None:
                    cuentas["en_una_operacion"] += 1
                else:
                    f["categoria"] = descartar[f["origen_archivo"]]
                    a_cambiar.append(f)
    cuentas["no_estan_en_la_base"] = len(set(descartar) - encontrados)
    cuentas["a_dar_de_baja"] = len(a_cambiar)
    return a_cambiar, cuentas


def aplicar(conexion, a_cambiar: list[dict]) -> int:
    with conexion.cursor() as cur:
        for f in a_cambiar:
            cur.execute(
                "UPDATE marcas SET estado = 'baja', sospechosa_calidad = true, motivo_calidad = %s WHERE id = %s AND estado <> 'baja'",
                (f"no_es_marca:{f['categoria']}", f["id"]))
    conexion.commit()
    return len(a_cambiar)


def deshacer(conexion, filas: list[dict]) -> int:
    n = 0
    with conexion.cursor() as cur:
        for f in filas:
            cur.execute(
                "UPDATE marcas SET estado = %s, sospechosa_calidad = %s, motivo_calidad = %s WHERE id = %s AND estado = 'baja' "
                "AND motivo_calidad LIKE 'no_es_marca:%%'",
                (f["estado"], f["sospechosa_calidad"] in (True, "True", "true", "1"), f["motivo_calidad"] or None, int(f["id"])))
            n += cur.rowcount
    conexion.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archivo", type=Path, default=ARCHIVO)
    ap.add_argument("--aplicar", action="store_true", help="escribir (sin esto sólo cuenta)")
    ap.add_argument("--reporte", type=Path, help="CSV donde guardar el estado anterior de cada marca cambiada")
    ap.add_argument("--deshacer", type=Path, help="restaurar el estado anterior desde un reporte")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=30)
    try:
        if args.deshacer:
            filas = list(csv.DictReader(open(args.deshacer, encoding="utf-8")))
            print(f"Deshecho: {deshacer(conexion, filas)} de {len(filas)} marcas vueltas a su estado anterior.")
            return 0
        a_cambiar, cuentas = planificar(conexion, leer_decisiones(args.archivo))
        print(("[EN SECO] " if not args.aplicar else "") + " · ".join(f"{k.replace('_', ' ')}: {v}" for k, v in cuentas.items()))
        if not args.aplicar:
            return 0
        reporte = args.reporte or Path("reporte_limpieza_marcas.csv")
        with open(reporte, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "codigo", "origen_archivo", "estado", "sospechosa_calidad", "motivo_calidad", "categoria"],
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(a_cambiar)
        print(f"Reporte para deshacer: {reporte}")
        print(f"Dadas de baja: {aplicar(conexion, a_cambiar)}")
    finally:
        conexion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
