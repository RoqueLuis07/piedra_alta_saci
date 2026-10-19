"""Carga del catálogo (scripts/cargar_catalogo.py) contra un Postgres real embebido."""

import csv
import io
import sys
from pathlib import Path

import pytest

pg = pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cargar_catalogo import cargar, reducir_png  # noqa: E402

COLUMNAS = ["id_marca", "carpeta_escaneo", "archivo_origen", "formato_detectado", "archivo_png",
            "dominante_o_complementaria", "sospechosa_calidad", "motivo_calidad"]


def _png(lado=1024):
    from PIL import Image, ImageDraw

    imagen = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(100, 900), (500, 100), (900, 900)], fill=(0, 0, 0, 255), width=60)
    b = io.BytesIO()
    imagen.save(b, "PNG")
    return b.getvalue()


def test_reducir_png_baja_a_512_y_mantiene_transparencia():
    from PIL import Image

    salida = Image.open(io.BytesIO(reducir_png(_png(), 512)))
    assert salida.size == (512, 512) and salida.mode == "RGBA"
    assert salida.getpixel((0, 0))[3] == 0  # fondo transparente


def test_cargar_es_idempotente_y_marca_sospechosas(tmp_path):
    from psycopg2.extras import RealDictCursor
    import psycopg2

    servidor = pg.get_server(tmp_path / "pg")
    try:
        conexion = psycopg2.connect(servidor.get_uri(), cursor_factory=RealDictCursor)
        sql = (Path(__file__).resolve().parent.parent / "marcas/servidor/schema.sql").read_text(encoding="utf-8")
        with conexion.cursor() as cur:
            cur.execute(sql.replace("create extension if not exists pgcrypto;", ""))
        conexion.commit()

        pngs = tmp_path / "png"
        pngs.mkdir()
        filas = []
        for i, (tipo, sosp) in enumerate([("dominante", "no"), ("complementaria", "no"), ("complementaria", "si")]):
            nombre = f"L1_{i:06d}.png"
            (pngs / nombre).write_bytes(_png())
            filas.append({"id_marca": f"L1_{i:06d}", "carpeta_escaneo": "L1", "archivo_origen": f"{i:04d}.jpg",
                          "formato_detectado": "GE2_PRINCIPAL", "archivo_png": nombre,
                          "dominante_o_complementaria": tipo, "sospechosa_calidad": sosp,
                          "motivo_calidad": "trazo_muy_chico" if sosp == "si" else ""})
        filas.append({**filas[0], "id_marca": "falta", "archivo_png": "no_existe.png"})
        (pngs / "rota.png").write_bytes(_png()[:200])  # truncada
        filas.append({**filas[0], "id_marca": "rota", "archivo_png": "rota.png"})
        ruta_csv = tmp_path / "marcas.csv"
        with open(ruta_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNAS)
            w.writeheader()
            w.writerows(filas)

        primera = cargar(conexion, ruta_csv, pngs, 512, informar=lambda *_: None)
        assert (primera["cargadas"], primera["revisar"], primera["sin_png"]) == (3, 1, 1)
        assert primera["danadas"] == ["rota.png"]
        assert primera["bytes_despues"] < primera["bytes_antes"]
        segunda = cargar(conexion, ruta_csv, pngs, 512, informar=lambda *_: None)
        assert (segunda["cargadas"], segunda["ya_estaban"]) == (0, 3)

        with conexion.cursor() as cur:
            cur.execute("SELECT tipo, estado, motivo_calidad FROM marcas ORDER BY id")
            assert [(r["tipo"], r["estado"]) for r in cur.fetchall()] == [
                ("dominante", "activa"), ("complementaria", "activa"), ("complementaria", "revisar")]
        conexion.close()
    finally:
        servidor.cleanup()
