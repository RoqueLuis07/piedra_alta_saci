"""Origen normalizado de escaneos y marcas: «Carpeta 1 · pág. 30», «Carpeta 2 · Parte 1 · pág. 17».

Carpeta 1 = Epson L3210; Carpeta 2 = Epson L5290 (Parte 1.pdf, Parte 2.pdf e imágenes sueltas). La página es el
número de imagen. El SQL vive en schema.sql (se aplica solo en cada arranque); los cargadores lo vuelven a correr.
"""

from __future__ import annotations

from pathlib import Path

_MARCA = "alter table escaneos add column if not exists carpeta_n"


def normalizar_origen(conexion) -> None:
    """Completa carpeta/parte/página/etiqueta de lo recién cargado (idempotente)."""
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with conexion.cursor() as cur:
        cur.execute(_MARCA + sql.split(_MARCA, 1)[1])
    conexion.commit()
