"""Carga el catálogo de marcas extraído de los escaneos (``marcas_unicas.csv``
+ carpeta de PNG) en la tabla ``marcas`` de Postgres.

- Cada PNG se reduce a ``--lado`` px (512 por defecto: alcanza de sobra para
  estampar y pesa ~4 veces menos que el original de 1024).
- Es idempotente: una marca cuyo archivo de origen ya está cargado se saltea.
- No crea guías, operaciones ni propietarios: el catálogo no trae esos datos.
- Las marcas sospechosas entran en estado ``revisar``; el resto, ``activa``.

Uso (contra la base que indique DATABASE_URL):

    python scripts/cargar_catalogo.py --csv Catalogo_Final/marcas_unicas.csv --pngs Base_Marcas_PNG
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import sys
from pathlib import Path

import psycopg2
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

TIPOS = {"dominante": "dominante", "complementaria": "complementaria"}


def reducir_png(contenido: bytes, lado: int) -> bytes:
    imagen = Image.open(io.BytesIO(contenido)).convert("RGBA")
    if max(imagen.size) > lado:
        imagen = _ajustar(imagen, lado)
    salida = io.BytesIO()
    imagen.save(salida, format="PNG", optimize=True)
    return salida.getvalue()


def _ajustar(imagen: Image.Image, lado: int) -> Image.Image:
    proporcion = lado / max(imagen.size)
    return imagen.resize((max(1, round(imagen.size[0] * proporcion)), max(1, round(imagen.size[1] * proporcion))), Image.LANCZOS)


def cargar(conexion, csv_ruta: Path, carpeta_png: Path, lado: int = 512, limite: int | None = None,
           informar=print) -> dict:
    with open(csv_ruta, encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    if limite:
        filas = filas[:limite]

    with conexion.cursor() as cur:
        cur.execute("SELECT origen_archivo FROM marcas WHERE origen_archivo IS NOT NULL")
        existentes = {r["origen_archivo"] if isinstance(r, dict) else r[0] for r in cur.fetchall()}

    cuentas = {"cargadas": 0, "ya_estaban": 0, "sin_png": 0, "danadas": [], "revisar": 0, "bytes_antes": 0, "bytes_despues": 0}
    with conexion.cursor() as cur:
        for i, fila in enumerate(filas, 1):
            origen = fila["archivo_png"]
            if origen in existentes:
                cuentas["ya_estaban"] += 1
                continue
            ruta = carpeta_png / origen
            if not ruta.is_file():
                cuentas["sin_png"] += 1
                continue
            original = ruta.read_bytes()
            try:
                reducido = reducir_png(original, lado)
            except OSError:
                # archivo truncado o ilegible: se informa y se sigue con el resto
                cuentas["danadas"].append(origen)
                continue
            sospechosa = fila.get("sospechosa_calidad") == "si"
            cur.execute(
                """
                INSERT INTO marcas (tipo, descripcion, origen_archivo, archivo_png, sospechosa_calidad,
                                    motivo_calidad, estado, observaciones, borde_limpiado)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true)
                """,
                (
                    TIPOS.get(fila.get("dominante_o_complementaria")),
                    f"Escaneo {fila['carpeta_escaneo']} · {fila['archivo_origen']}",
                    origen,
                    psycopg2.Binary(reducido),
                    sospechosa,
                    fila.get("motivo_calidad") or None,
                    "revisar" if sospechosa else "activa",
                    f"Formato: {fila['formato_detectado']}",
                ),
            )
            cuentas["cargadas"] += 1
            cuentas["revisar"] += sospechosa
            cuentas["bytes_antes"] += len(original)
            cuentas["bytes_despues"] += len(reducido)
            if i % 250 == 0:
                informar(f"  {i}/{len(filas)}…")
    conexion.commit()
    return cuentas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--pngs", required=True, type=Path)
    ap.add_argument("--lado", type=int, default=512)
    ap.add_argument("--limite", type=int, default=None, help="sólo las primeras N filas (para probar)")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    from psycopg2.extras import RealDictCursor

    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        c = cargar(conexion, args.csv, args.pngs, args.lado, args.limite)
    finally:
        conexion.close()
    print(
        f"Cargadas {c['cargadas']} (a revisar: {c['revisar']}) · ya estaban {c['ya_estaban']} · "
        f"sin PNG {c['sin_png']} · dañadas {c['danadas']} · {c['bytes_antes'] / 1e6:.0f} MB -> {c['bytes_despues'] / 1e6:.0f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
