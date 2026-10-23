"""Restaura en una base NUEVA el respaldo que deja ``scripts/respaldo_a_github.py``
(usuarios con sus contraseñas, propietarios, operaciones, marcas con sus imágenes y
cambios pendientes). Sirve para pasar el sistema a otro proveedor o recuperarlo.

Antes hay que aplicar el esquema (``flask --app wsgi crear-esquema``, que el arranque del
servicio ya hace). Se niega a correr sobre una base que ya tiene datos, salvo --forzar.

    git clone https://github.com/RoqueLuis07/piedra-alta-respaldos      # repo privado
    python scripts/restaurar_respaldo.py --carpeta piedra-alta-respaldos/respaldo --en-seco
    python scripts/restaurar_respaldo.py --carpeta piedra-alta-respaldos/respaldo

Usa la variable DATABASE_URL. Restaura también (si están en la carpeta) las páginas escaneadas con su imagen,
el PDF de cada documento emitido, el historial de imágenes de las marcas y el documento original de cada operación.
Para el respaldo de la base de Render usá la carpeta «respaldo_render».
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

# Orden de restauración (las que dependen de otras van después). «escaneos» va antes de «marcas» porque las
# marcas apuntan a su página; «documentos_emitidos» recibe además su PDF.
ORDEN = ("usuarios", "propietarios", "operaciones", "escaneos", "marcas", "cambios_pendientes",
         "marcas_versiones_imagen", "respaldos_operacion", "documentos_emitidos")
BASICAS = ("usuarios", "propietarios", "operaciones", "marcas", "cambios_pendientes")      # las del respaldo original
BYTEA = {
    "marcas": ("archivo_png", "archivo_svg", "huella"),
    "operaciones": ("borrador_pdf", "borrador_dominante_png"),
    "marcas_versiones_imagen": ("archivo_png", "archivo_svg"),
    "respaldos_operacion": ("contenido",),
    "documentos_emitidos": ("contenido",),
}
CON_LOTES = ("marcas", "escaneos", "marcas_versiones_imagen", "respaldos_operacion")
SECUENCIAS = ("propietarios", "operaciones", "escaneos", "marcas", "cambios_pendientes", "marcas_versiones_imagen",
              "documentos_emitidos")


def leer_respaldo(carpeta: Path) -> dict[str, list[dict]]:
    def leer(ruta: Path) -> list[dict]:
        return json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else []

    datos: dict[str, list[dict]] = {}
    for t in ORDEN:
        if t in CON_LOTES:
            filas: list[dict] = []
            for lote in sorted((carpeta / t).glob("lote_*.json")):
                filas += leer(lote)
            datos[t] = filas
        else:
            datos[t] = leer(carpeta / f"{t}.json")
    # El PDF de cada documento emitido es un archivo aparte: se vuelve a unir a su fila.
    sin_pdf = []
    for fila in datos["documentos_emitidos"]:
        pdf = carpeta / "documentos" / f"{fila['id']:06d}.pdf"
        if pdf.is_file():
            fila["contenido"] = base64.b64encode(pdf.read_bytes()).decode("ascii")
        else:
            sin_pdf.append(fila["id"])
    if sin_pdf:
        print(f"OJO: {len(sin_pdf)} documento(s) emitido(s) sin su PDF en el respaldo (no se restauran): {sin_pdf[:10]}", file=sys.stderr)
        datos["documentos_emitidos"] = [f for f in datos["documentos_emitidos"] if f["id"] not in sin_pdf]
    return datos


def _miniatura(imagen: bytes) -> bytes:
    import io

    from PIL import Image

    pil = Image.open(io.BytesIO(imagen)).convert("RGB")
    pil.thumbnail((320, 320))
    salida = io.BytesIO()
    pil.save(salida, "JPEG", quality=70, optimize=True)
    return salida.getvalue()


def _columnas(cur, tabla: str) -> set[str]:
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name = %s", (tabla,))
    return {r["column_name"] for r in cur.fetchall()}


def restaurar(conexion, datos: dict[str, list[dict]], escribir: bool = True, forzar: bool = False) -> dict:
    cuentas = {t: len(datos.get(t, [])) for t in ORDEN}
    with conexion.cursor() as cur:
        for t in BASICAS:
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
        hay_escaneos = bool(datos.get("escaneos"))
        for tabla in ORDEN:
            filas = datos.get(tabla, [])
            if not filas:
                continue
            cur.execute("SELECT to_regclass(%s) AS t", (tabla,))
            if cur.fetchone()["t"] is None:
                raise RuntimeError(f"Falta la tabla «{tabla}»: aplicá primero el esquema (flask --app wsgi crear-esquema).")
            columnas = _columnas(cur, tabla)
            for fila in filas:
                valores = {}
                imagen = None
                for k, v in fila.items():
                    if tabla == "escaneos" and k == "imagen":      # va a su tabla aparte (escaneos_imagenes)
                        imagen = base64.b64decode(v) if v else None
                        continue
                    # Sin las páginas escaneadas en el respaldo, las marcas no pueden apuntar a ellas.
                    if k not in columnas or (tabla == "marcas" and k == "escaneo_id" and not hay_escaneos):
                        continue
                    if k in BYTEA.get(tabla, ()) and v is not None:
                        v = psycopg2.Binary(base64.b64decode(v))
                    elif isinstance(v, (dict, list)):
                        v = Json(v)
                    valores[k] = v
                cols = list(valores)
                sql = f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join('%(' + c + ')s' for c in cols)})"
                cur.execute(sql + (" ON CONFLICT DO NOTHING" if forzar else ""), valores)
                if tabla == "escaneos" and imagen:
                    cur.execute(
                        "INSERT INTO escaneos_imagenes (escaneo_id, imagen, miniatura) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        (fila["id"], psycopg2.Binary(imagen), psycopg2.Binary(_miniatura(imagen))),
                    )
        # Las filas se insertaron con su id original: las secuencias tienen que seguir desde el máximo.
        for tabla in SECUENCIAS:
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
          + " · ".join(f"{t} {r[t]}" for t in ORDEN if r[t] or t in BASICAS))
    faltan = {t: (esperado[t], r[t]) for t in ORDEN if t in esperado and esperado[t] != r[t]}
    if faltan:
        print("OJO: no coincide con el manifiesto del respaldo (esperado, leído):", faltan, file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
