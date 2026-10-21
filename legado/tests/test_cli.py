"""Pruebas de humo de la CLI: que los comandos nuevos estén bien conectados.

No repite las pruebas unitarias de marcas.db / marcas.importacion / el
pipeline; sólo verifica que argparse arme los Namespace que las funciones de
comando esperan, algo que una prueba unitaria de más abajo no detecta.
"""

from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

import numpy as np
import pytest

from marcas import db
from marcas.cli import main

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generar_hoja_demo import dibujar_hoja, simular_escaneo  # noqa: E402


def test_plantilla_datos_escribe_un_csv(tmp_path):
    salida = tmp_path / "plantilla.csv"
    assert main(["plantilla-datos", "--salida", str(salida)]) == 0
    assert salida.exists()
    assert salida.read_text(encoding="utf-8-sig").splitlines()[0].startswith("codigo")


def test_datos_carga_una_planilla(tmp_path, capsys):
    ruta_db = tmp_path / "t.db"
    planilla = tmp_path / "r.csv"
    with open(planilla, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo", "propietario"])
        w.writerow(["M-01", "Juan Perez"])

    assert main(["--db", str(ruta_db), "datos", str(planilla)]) == 0
    salida = capsys.readouterr().out
    assert "marcas nuevas" in salida
    assert db.estadisticas(ruta_db)["marcas"] == 1


def test_datos_con_planilla_invalida_devuelve_error(tmp_path, capsys):
    planilla = tmp_path / "r.csv"
    planilla.write_text("propietario\nJuan Perez\n", encoding="utf-8")
    assert main(["--db", str(tmp_path / "t.db"), "datos", str(planilla)]) == 1
    assert "Error" in capsys.readouterr().err


def test_cargar_encadena_datos_y_digitalizacion(tmp_path, capsys):
    """El comando 'cargar' hace en un paso lo que 'datos' + 'procesar' + 'importar'
    hacen por separado: es la respuesta directa a "cargar la base con las
    marcas que tenemos"."""
    rng = random.Random(5)
    np.random.seed(5)
    escaneos = tmp_path / "escaneos"
    escaneos.mkdir()
    import cv2
    cv2.imwrite(str(escaneos / "hoja_01.png"), simular_escaneo(dibujar_hoja(1, rng), rng))

    # códigos oficiales por posición + planilla de datos del registro,
    # como llegarían de verdad separados del escaneo.
    codigos = tmp_path / "codigos.csv"
    with open(codigos, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["hoja", "fila", "columna", "codigo"])
        for f in range(1, 6):
            for c in range(1, 5):
                w.writerow(["hoja_01", f, c, f"M-{f:02d}{c:02d}"])

    registro = tmp_path / "registro.csv"
    with open(registro, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo", "propietario", "documento"])
        for f in range(1, 6):
            for c in range(1, 5):
                w.writerow([f"M-{f:02d}{c:02d}", "PIEDRA ALTA S.A.", "80020081"])

    ruta_db = tmp_path / "t.db"
    codigo_salida = main([
        "--db", str(ruta_db), "cargar", str(escaneos),
        "--datos", str(registro), "--codigos", str(codigos), "--sin-svg",
    ])
    salida = capsys.readouterr().out
    assert codigo_salida == 0, salida

    stats = db.estadisticas(ruta_db)
    assert stats["marcas"] == 20
    assert stats["sin_imagen"] == 0, "cada marca del registro terminó con su imagen"
    assert stats["propietarios"] == 1

    filas = db.listar_marcas(ruta_db=ruta_db)
    assert all(f["propietario"] == "PIEDRA ALTA S.A." for f in filas)
    assert all(f["archivo_png"] for f in filas)


def test_listar_filtra_por_sin_imagen(tmp_path, capsys):
    ruta_db = tmp_path / "t.db"
    planilla = tmp_path / "r.csv"
    with open(planilla, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["codigo"])
        w.writerow(["M-01"])
    main(["--db", str(ruta_db), "datos", str(planilla)])
    capsys.readouterr()

    main(["--db", str(ruta_db), "listar", "--sin-imagen"])
    assert "M-01" in capsys.readouterr().out

    main(["--db", str(ruta_db), "listar", "--con-imagen"])
    assert "M-01" not in capsys.readouterr().out
