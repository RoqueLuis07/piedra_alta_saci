"""Carga el catálogo de marcas extraído de los escaneos (``marcas_unicas.csv``
+ carpeta de PNG) en la tabla ``marcas`` de Postgres.

- Cada PNG se reduce a ``--lado`` px (512 por defecto: alcanza de sobra para
  estampar y pesa ~4 veces menos que el original de 1024).
- Es idempotente: una marca cuyo archivo de origen ya está cargado se saltea.
- Guarda de a ``--lote`` marcas (250): si se corta, lo ya cargado queda y al
  volver a correrlo sigue donde quedó.
- ``--en-seco`` hace todo (lee, valida, reduce) pero NO escribe en la base.
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


def _posicion(fila: dict) -> str | None:
    """De qué sección de la hoja salió: la casilla (fila/columna) y si era Dominante o Complementaria."""
    partes = []
    if fila.get("dominante_o_complementaria"):
        partes.append(fila["dominante_o_complementaria"].capitalize())
    if fila.get("fila") not in (None, "") and fila.get("columna") not in (None, ""):
        partes.append(f"casilla {int(fila['fila']) + 1}·{int(fila['columna']) + 1}")
    return " · ".join(partes) or None


def cargar(conexion, csv_ruta: Path, carpeta_png: Path, lado: int = 512, limite: int | None = None,
           informar=print, lote: int = 250, escribir: bool = True) -> dict:
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
            if escribir:
                cur.execute(
                    """
                    INSERT INTO marcas (tipo, descripcion, origen_archivo, archivo_png, sospechosa_calidad,
                                        motivo_calidad, estado, observaciones, borde_limpiado,
                                        origen_carpeta, origen_pagina, posicion)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s)
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
                        fila["carpeta_escaneo"],
                        fila["archivo_origen"],
                        _posicion(fila),
                    ),
                )
            cuentas["cargadas"] += 1
            cuentas["revisar"] += sospechosa
            cuentas["bytes_antes"] += len(original)
            cuentas["bytes_despues"] += len(reducido)
            if escribir and cuentas["cargadas"] % lote == 0:
                conexion.commit()          # lo ya cargado queda aunque después se corte
            if i % 250 == 0:
                informar(f"  {i}/{len(filas)}…")
    if escribir:
        conexion.commit()
        # Si las páginas escaneadas ya están cargadas, se une cada marca con la suya.
        with conexion.cursor() as cur:
            cur.execute("SELECT to_regclass('escaneos') AS t")
            if cur.fetchone()["t"]:
                cur.execute(
                    "UPDATE marcas m SET escaneo_id = e.id FROM escaneos e "
                    "WHERE m.escaneo_id IS NULL AND m.origen_carpeta = e.carpeta AND m.origen_pagina = e.archivo"
                )
                cuentas["vinculadas"] = cur.rowcount
        conexion.commit()
        from marcas.servidor.origen import normalizar_origen

        normalizar_origen(conexion)
    return cuentas


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", required=True, type=Path)
    ap.add_argument("--pngs", required=True, type=Path)
    ap.add_argument("--lado", type=int, default=512)
    ap.add_argument("--limite", type=int, default=None, help="sólo las primeras N filas (para probar)")
    ap.add_argument("--lote", type=int, default=250, help="cada cuántas marcas se confirma en la base")
    ap.add_argument("--en-seco", action="store_true", help="validar y contar sin escribir nada en la base")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    from psycopg2.extras import RealDictCursor

    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor)
    try:
        c = cargar(conexion, args.csv, args.pngs, args.lado, args.limite, lote=args.lote, escribir=not args.en_seco)
    finally:
        conexion.close()
    print(
        f"{'[EN SECO, no se escribió nada] ' if args.en_seco else ''}Cargadas {c['cargadas']} (a revisar: {c['revisar']}) · ya estaban {c['ya_estaban']} · "
        f"sin PNG {c['sin_png']} · dañadas {c['danadas']} · {c['bytes_antes'] / 1e6:.0f} MB -> {c['bytes_despues'] / 1e6:.0f} MB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
