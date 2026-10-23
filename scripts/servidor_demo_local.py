"""Levanta el sistema completo en tu PC con una base de prueba (Postgres embebido) y datos de ejemplo,
para probarlo o mostrarlo SIN tocar producción.

    pip install -r requirements-dev.txt
    python scripts/servidor_demo_local.py [guia_oficial.pdf] [--puerto 5055]

Entra en http://127.0.0.1:5055 con  admin@demo.test / clave-de-prueba  (también operador@demo.test y consulta@demo.test).
Carga: 40 marcas de ejemplo (dominantes y complementarias, algunas «a revisar»), si se pasa un PDF oficial de SENACSA
sus páginas como guía escaneada, y una venta. Al cerrar con Ctrl+C se borra la base de prueba.
"""

from __future__ import annotations

import argparse
import io
import random
import shutil
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

CLAVE = "clave-de-prueba"
LETRAS = "ABCDEFGHJKLMNPRSTUVZ"


def _marca(i: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    rnd = random.Random(i)
    im = Image.new("RGBA", (512, 512), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    cx, cy = 256, 256
    forma = i % 5
    if forma == 0:
        d.ellipse([90, 90, 420, 420], outline=(0, 0, 0, 255), width=18)
    elif forma == 1:
        d.line([(100, 410), (256, 100), (412, 410)], fill=(0, 0, 0, 255), width=18, joint="curve")
    elif forma == 2:
        d.rectangle([110, 130, 400, 380], outline=(0, 0, 0, 255), width=18)
    elif forma == 3:
        d.line([(100, 256), (412, 256)], fill=(0, 0, 0, 255), width=18)
        d.line([(256, 100), (256, 412)], fill=(0, 0, 0, 255), width=18)
    else:
        d.polygon([(256, 90), (420, 400), (92, 400)], outline=(0, 0, 0, 255), width=18)
    try:
        fuente = ImageFont.truetype("arialbd.ttf", 150)
    except Exception:
        fuente = ImageFont.load_default()
    d.text((cx - 40 + rnd.randint(-10, 10), cy - 60 + rnd.randint(-10, 10)), LETRAS[i % len(LETRAS)] + str(i % 7), fill=(0, 0, 0, 255), font=fuente)
    salida = io.BytesIO()
    im.save(salida, "PNG")
    return salida.getvalue()


def preparar_base(pdf: Path | None):
    import os

    import pixeltable_pgserver as pg
    import psycopg2

    carpeta = Path(tempfile.mkdtemp(prefix="pgdemo_"))
    servidor = pg.get_server(carpeta)
    os.environ.setdefault("FLASK_SECRET_KEY", "demo-local")
    from marcas.servidor import db

    db.DATABASE_URL = servidor.get_uri()
    db._pool = None
    sql = (Path(db.__file__).parent / "schema.sql").read_text(encoding="utf-8").replace("create extension if not exists pgcrypto;", "")
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute(sql)
    conexion.commit()

    from marcas.servidor.auth import encriptar_contrasena
    from marcas.servidor.consultas import crear_usuario

    for email, nombre, rol in (("admin@demo.test", "Admin Demo", "administrador"), ("operador@demo.test", "Operador Demo", "operador"),
                               ("consulta@demo.test", "Consulta Demo", "consulta")):
        crear_usuario(conexion, email, encriptar_contrasena(CLAVE), nombre, rol, True)
    with conexion.cursor() as cur:
        for i in range(40):
            estado = "revisar" if i % 9 == 4 else "activa"
            cur.execute(
                "INSERT INTO marcas (tipo, estado, archivo_png, origen_carpeta, origen_pagina) VALUES (%s, %s, %s, %s, %s)",
                ("dominante" if i % 4 == 0 else "complementaria", estado, psycopg2.Binary(_marca(i)),
                 "L3210" if i % 2 == 0 else "PARTE1", f"{i + 1:04d}.jpg" if i % 2 == 0 else f"p1_{i + 1:04d}.jpg"),
            )
        cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion, vendedor_nombre, comprador_nombre, cantidad_animales) "
                    "VALUES ('90906711', 'venta', 'Piedra Alta S.A.C.I.', 'Comprador de Ejemplo', 25)")
    conexion.commit()
    if pdf and pdf.is_file():
        from marcas.servidor.subida_guias import guardar_paginas, nuevo_lote, preparar

        paginas, numero = preparar(pdf.name, pdf.read_bytes(), 60)
        guardar_paginas(conexion, paginas, numero, "pdf" if numero else None, None, nuevo_lote(), 0, pdf.name)
        conexion.commit()
    from marcas.servidor.origen import normalizar_origen

    normalizar_origen(conexion)
    db.devolver_conexion(conexion)
    return servidor, carpeta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", nargs="?", type=Path, help="guía oficial de SENACSA (opcional) para cargarla como guía escaneada")
    ap.add_argument("--puerto", type=int, default=5055)
    args = ap.parse_args()
    servidor, carpeta = preparar_base(args.pdf)
    from flask import Response

    from marcas.servidor.app import crear_app

    app = crear_app()
    app.config["SESSION_COOKIE_SECURE"] = False
    if args.pdf and args.pdf.is_file():
        contenido = args.pdf.read_bytes()

        @app.get("/__pdf")
        def _pdf():
            return Response(contenido, mimetype="application/pdf")

    print(f"LISTO · http://127.0.0.1:{args.puerto} · admin@demo.test / {CLAVE}", flush=True)
    try:
        app.run(port=args.puerto, threaded=True)
    finally:
        servidor.cleanup()
        shutil.rmtree(carpeta, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
