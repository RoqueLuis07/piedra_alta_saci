"""Pruebas de la carga del lote de digitalización masiva de Cowork.

El caso que importa probar es el de las 18 operaciones reales que quedaron
con el mismo número de guía (el OCR no lo pudo leer con confianza): el
cruce marca -> formulario tiene que hacerse por ``operacion_id``, nunca por
``numero_guia``, o dos operaciones sin relación terminan mezclando sus
marcas. Estos tests simulan ese escenario en chico.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from marcas import db
from marcas.importacion_cowork import importar_paquete

ESQUEMA_ORIGEN = """
CREATE TABLE operaciones (
    id INTEGER PRIMARY KEY,
    numero_guia TEXT,
    fecha TEXT,
    vendedor_nombre TEXT,
    vendedor_documento TEXT,
    vendedor_establecimiento TEXT,
    vendedor_establecimiento_codigo TEXT,
    comprador_nombre TEXT,
    comprador_documento TEXT,
    cantidad_animales INTEGER,
    categoria_animales TEXT,
    categoria_animales_original TEXT,
    tipo_formulario TEXT,
    guia_colisionada INTEGER NOT NULL DEFAULT 0,
    revisar TEXT
);
CREATE TABLE marcas (
    id INTEGER PRIMARY KEY,
    operacion_id INTEGER NOT NULL,
    numero_guia TEXT,
    vendedor_documento TEXT,
    tipo TEXT,
    posicion TEXT,
    archivo_imagen TEXT,
    archivo_svg TEXT,
    borde_limpiado INTEGER NOT NULL DEFAULT 0,
    sospechosa_calidad INTEGER NOT NULL DEFAULT 0,
    motivo_calidad TEXT
);
"""


def _crear_export_cowork(ruta: Path, operaciones: list[tuple], marcas: list[tuple]) -> Path:
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA_ORIGEN)
    con.executemany(
        """INSERT INTO operaciones
           (id, numero_guia, vendedor_nombre, vendedor_documento, guia_colisionada)
           VALUES (?,?,?,?,?)""",
        operaciones,
    )
    con.executemany(
        """INSERT INTO marcas
           (id, operacion_id, numero_guia, tipo, posicion, archivo_imagen,
            archivo_svg, sospechosa_calidad, motivo_calidad)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        marcas,
    )
    con.commit()
    con.close()
    return ruta


@pytest.fixture
def lote_colisionado(tmp_path, monkeypatch):
    """Dos operaciones con el mismo número de guía (colisión real de OCR)."""
    from marcas import config
    monkeypatch.setattr(config, "RAIZ", tmp_path)
    monkeypatch.setattr(config, "DIR_PNG", tmp_path / "datos" / "marcas" / "png")
    monkeypatch.setattr(config, "DIR_SVG", tmp_path / "datos" / "marcas" / "svg")

    origen_db = _crear_export_cowork(
        tmp_path / "export.db",
        operaciones=[
            (1, "90904503", "Juan Perez", "1111111", 1),
            (2, "90904503", "Maria Gomez", "2222222", 1),
        ],
        marcas=[
            (1, 1, "90904503", "dominante", "F01C01", "pag_001_F01C01.png", "pag_001_F01C01.svg", 0, None),
            (2, 1, "90904503", "complementaria", "F01C02", "pag_001_F01C02.png", "pag_001_F01C02.svg", 0, None),
            (3, 2, "90904503", "dominante", "F01C01", "pag_009_F01C01.png", "pag_009_F01C01.svg", 1, "mancha solida"),
        ],
    )

    imagenes = tmp_path / "imagenes"
    imagenes.mkdir()
    for nombre in [
        "pag_001_F01C01.png", "pag_001_F01C02.png", "pag_009_F01C01.png",
        "pag_001_F01C01.svg", "pag_001_F01C02.svg",
        # pag_009_F01C01.svg falta a propósito: simula una marca sin vectorizar.
    ]:
        (imagenes / nombre).write_bytes(b"contenido de prueba")

    ruta_db = tmp_path / "marcas.db"
    return origen_db, [imagenes], ruta_db


def test_importa_operaciones_y_marcas(lote_colisionado):
    origen_db, carpetas, ruta_db = lote_colisionado
    r = importar_paquete(origen_db, carpetas, ruta_db)
    assert r.resumen() == {
        "operaciones_nuevas": 2, "operaciones_actualizadas": 0,
        "marcas_nuevas": 3, "marcas_actualizadas": 0,
        "imagenes_copiadas": 5, "imagenes_faltantes": 1,
    }
    assert r.imagenes_faltantes == ["pag_009_F01C01.svg"]


def test_operaciones_con_mismo_numero_de_guia_no_mezclan_sus_marcas(lote_colisionado):
    """El caso real de las 18 operaciones colisionadas: el vínculo es operacion_id."""
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)

    ficha = db.obtener_ficha_marca("pag_001_F01C01", ruta_db)
    assert ficha["marca"]["tipo"] == "dominante"
    assert ficha["operacion"]["vendedor_nombre"] == "Juan Perez"
    assert [a["codigo"] for a in ficha["acompanantes"]] == ["pag_001_F01C02"]

    ficha_otra = db.obtener_ficha_marca("pag_009_F01C01", ruta_db)
    assert ficha_otra["operacion"]["vendedor_nombre"] == "Maria Gomez"
    assert ficha_otra["acompanantes"] == []
    assert ficha_otra["marca"]["estado"] == "revisar"
    assert ficha_otra["marca"]["motivo_calidad"] == "mancha solida"


def test_reimportar_el_mismo_lote_no_duplica(lote_colisionado):
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)
    r2 = importar_paquete(origen_db, carpetas, ruta_db)
    assert r2.operaciones_nuevas == 0 and r2.operaciones_actualizadas == 2
    assert r2.marcas_nuevas == 0 and r2.marcas_actualizadas == 3
    assert db.estadisticas(ruta_db)["marcas"] == 3

    with db.conectar(ruta_db) as con:
        total_operaciones = con.execute("SELECT COUNT(*) c FROM operaciones").fetchone()["c"]
    assert total_operaciones == 2


def test_reimportar_no_pisa_el_codigo_renombrado_a_mano(lote_colisionado):
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)
    db.actualizar_marca("pag_001_F01C01", ruta_db, codigo="90904503-D")

    importar_paquete(origen_db, carpetas, ruta_db)

    assert db.obtener_marcas(["90904503-D"], ruta_db) != []
    assert db.obtener_marcas(["pag_001_F01C01"], ruta_db) == []


def test_propietario_es_el_vendedor_no_el_comprador(lote_colisionado):
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)
    marca = db.obtener_marcas(["pag_001_F01C01"], ruta_db)[0]
    assert marca["propietario"] == "Juan Perez"


def test_imagen_copiada_queda_en_el_arbol_del_proyecto(lote_colisionado):
    from marcas import config
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)
    marca = db.obtener_marcas(["pag_001_F01C01"], ruta_db)[0]
    assert (config.RAIZ / marca["archivo_png"]).exists()
    assert marca["archivo_png"].startswith("datos")


def test_marca_sin_svg_queda_sin_ese_campo_pero_no_falla(lote_colisionado):
    origen_db, carpetas, ruta_db = lote_colisionado
    importar_paquete(origen_db, carpetas, ruta_db)
    marca = db.obtener_marcas(["pag_009_F01C01"], ruta_db)[0]
    assert marca["archivo_svg"] is None
    assert marca["archivo_png"]


@pytest.fixture
def operacion_con_hoja_de_anexo(tmp_path, monkeypatch):
    """Una operación con 2 páginas (hoja principal + Anexo): ambas repiten
    'F01C01' como posición de su propia grilla. La identidad de la marca no
    puede ser (operacion_id, posicion) -- tiene que ser el archivo."""
    from marcas import config
    monkeypatch.setattr(config, "RAIZ", tmp_path)
    monkeypatch.setattr(config, "DIR_PNG", tmp_path / "datos" / "marcas" / "png")
    monkeypatch.setattr(config, "DIR_SVG", tmp_path / "datos" / "marcas" / "svg")

    origen_db = _crear_export_cowork(
        tmp_path / "export.db",
        operaciones=[(1, "90800001", "Pedro Ramirez", "3333333", 0)],
        marcas=[
            (1, 1, "90800001", "dominante", "F01C01", "pag_050_F01C01.png", "pag_050_F01C01.svg", 0, None),
            (2, 1, "90800001", "complementaria", "F01C01", "pag_051_F01C01.png", "pag_051_F01C01.svg", 0, None),
        ],
    )
    imagenes = tmp_path / "imagenes"
    imagenes.mkdir()
    for nombre in [
        "pag_050_F01C01.png", "pag_050_F01C01.svg",
        "pag_051_F01C01.png", "pag_051_F01C01.svg",
    ]:
        (imagenes / nombre).write_bytes(b"contenido de prueba")
    return origen_db, [imagenes], tmp_path / "marcas.db"


def test_misma_posicion_en_paginas_distintas_de_una_operacion_no_se_pisa(
    operacion_con_hoja_de_anexo,
):
    origen_db, carpetas, ruta_db = operacion_con_hoja_de_anexo
    r = importar_paquete(origen_db, carpetas, ruta_db)
    assert r.marcas_nuevas == 2, "las dos marcas de F01C01 (una por página) deben quedar"

    ficha = db.obtener_ficha_marca("pag_050_F01C01", ruta_db)
    assert [a["codigo"] for a in ficha["acompanantes"]] == ["pag_051_F01C01"]
