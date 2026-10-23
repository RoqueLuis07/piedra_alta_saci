"""Limpieza de marcas que no son marcas (scripts/aplicar_limpieza_marcas.py): en seco, aplicar y deshacer."""

import csv
import sys
from pathlib import Path

import pytest

pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from aplicar_limpieza_marcas import aplicar, deshacer, leer_decisiones, planificar  # noqa: E402


def test_la_lista_revisada_a_ojo_es_coherente():
    decisiones = leer_decisiones()
    assert len(decisiones) > 1000
    assert set(decisiones.values()) <= {"anulacion", "linea_de_tabla", "texto_o_sello", "fragmento", "revision_visual"}
    assert all(k.endswith(".png") for k in decisiones)


def test_dar_de_baja_y_deshacer(cliente, tmp_path):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion) VALUES ('LIMP-1', 'compra') RETURNING id")
            op = cur.fetchone()["id"]
            ids = {}
            for nombre, estado, operacion in (("L_x1.png", "activa", None), ("L_x2.png", "revisar", None),
                                              ("L_x3.png", "activa", op), ("L_x4.png", "baja", None), ("L_ok.png", "activa", None)):
                cur.execute("INSERT INTO marcas (tipo, origen_archivo, estado, operacion_id) VALUES ('complementaria', %s, %s, %s) RETURNING id",
                            (nombre, estado, operacion))
                ids[nombre] = cur.fetchone()["id"]
        conexion.commit()
        lista = {"L_x1.png": "anulacion", "L_x2.png": "texto_o_sello", "L_x3.png": "fragmento", "L_x4.png": "linea_de_tabla",
                 "L_no_existe.png": "anulacion"}

        a_cambiar, cuentas = planificar(conexion, lista)
        assert cuentas == {"en_la_lista": 5, "no_estan_en_la_base": 1, "ya_de_baja": 1, "en_una_operacion": 1, "a_dar_de_baja": 2}

        def estado(nombre):
            with conexion.cursor() as cur:
                cur.execute("SELECT estado, motivo_calidad FROM marcas WHERE id = %s", (ids[nombre],))
                return cur.fetchone()

        assert estado("L_x1.png")["estado"] == "activa"          # planificar no escribe
        assert aplicar(conexion, a_cambiar) == 2
        assert estado("L_x1.png") == {"estado": "baja", "motivo_calidad": "no_es_marca:anulacion"}
        assert estado("L_x2.png")["estado"] == "baja"
        assert estado("L_x3.png")["estado"] == "activa"          # estaba en una operación: no se toca
        assert estado("L_ok.png")["estado"] == "activa"

        reporte = tmp_path / "r.csv"
        with open(reporte, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["id", "estado", "sospechosa_calidad", "motivo_calidad"], extrasaction="ignore")
            w.writeheader()
            w.writerows(a_cambiar)
        assert deshacer(conexion, list(csv.DictReader(open(reporte, encoding="utf-8")))) == 2
        assert estado("L_x1.png")["estado"] == "activa" and estado("L_x2.png")["estado"] == "revisar"
    finally:
        db.devolver_conexion(conexion)
