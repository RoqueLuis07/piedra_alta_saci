"""Volcado completo de la base para el respaldo: todo lo necesario para recuperar el sistema en otra base.

Lo que pesa (marcas con sus imágenes, páginas escaneadas, PDF emitidos, historial de imágenes) se lee y se
entrega por tandas, sin tener nunca todo junto en memoria: el servicio de respaldo corre con poca RAM.
Quien llama pasa ``escribir(ruta, contenido_bytes)``: el script de respaldo lo sube a GitHub y las pruebas lo
guardan en una carpeta.

Archivos que genera (``carpeta`` es «respaldo» o «respaldo_render»):

    carpeta/usuarios.json, propietarios.json, operaciones.json, cambios_pendientes.json, documentos_emitidos.json
    carpeta/marcas/lote_0001.json ...                 marcas con sus imágenes (base64)
    carpeta/escaneos/lote_0001.json ...               páginas escaneadas con su imagen (base64)
    carpeta/documentos/000012.pdf ...                 el PDF de cada documento emitido, tal cual
    carpeta/marcas_versiones_imagen/lote_0001.json    historial de imágenes de las marcas
    carpeta/respaldos_operacion/lote_0001.json        documento original de cada operación
    carpeta/manifiesto.json                           cantidades, para comprobar la restauración
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from marcas.servidor.consultas import dict_a_json, exportar_tablas_chicas

Escribir = Callable[[str, bytes], None]

# Filas por archivo: con imágenes de ~250 KB (escaneos) o ~100 KB (marcas) cada archivo queda en 5-15 MB,
# lejos del límite de 100 MB de GitHub.
TANDAS = {"marcas": 100, "escaneos": 20, "marcas_versiones_imagen": 20, "respaldos_operacion": 10}


def _tandas(conexion, sql: str, clave: str, tamano: int):
    """Filas de ``sql`` de a ``tamano``, siguiendo por ``clave`` (la consulta usa %(desde)s y %(tam)s)."""
    desde = 0
    while True:
        with conexion.cursor() as cur:
            cur.execute(sql, {"desde": desde, "tam": tamano})
            filas = cur.fetchall()
        if not filas:
            return
        yield filas
        desde = filas[-1][clave]


def _volcar_json(conexion, escribir: Escribir, ruta: str, sql: str, clave: str, tamano: int) -> tuple[int, int]:
    """Escribe ``ruta/lote_NNNN.json``; devuelve (filas, lotes). Siempre deja al menos un lote (vacío)."""
    filas_total, lotes = 0, 0
    for filas in _tandas(conexion, sql, clave, tamano):
        lotes += 1
        filas_total += len(filas)
        escribir(f"{ruta}/lote_{lotes:04d}.json", dict_a_json(filas).encode("utf-8"))
    if not lotes:
        escribir(f"{ruta}/lote_0001.json", b"[]")
        lotes = 1
    return filas_total, lotes


def volcar(conexion, escribir: Escribir, carpeta: str = "respaldo", ahora: datetime | None = None) -> dict:
    """Vuelca toda la base. Devuelve el manifiesto (cantidades y lotes)."""
    ahora = ahora or datetime.now(timezone.utc)
    cantidades: dict[str, int] = {}
    lotes: dict[str, int] = {}

    for tabla, filas in exportar_tablas_chicas(conexion).items():
        escribir(f"{carpeta}/{tabla}.json", dict_a_json(filas).encode("utf-8"))
        cantidades[tabla] = len(filas)

    cantidades["marcas"], lotes["marcas"] = _volcar_json(
        conexion, escribir, f"{carpeta}/marcas",
        "SELECT * FROM marcas WHERE id > %(desde)s ORDER BY id LIMIT %(tam)s", "id", TANDAS["marcas"])
    cantidades["escaneos"], lotes["escaneos"] = _volcar_json(
        conexion, escribir, f"{carpeta}/escaneos",
        "SELECT e.*, i.imagen FROM escaneos e LEFT JOIN escaneos_imagenes i ON i.escaneo_id = e.id "
        "WHERE e.id > %(desde)s ORDER BY e.id LIMIT %(tam)s", "id", TANDAS["escaneos"])
    cantidades["marcas_versiones_imagen"], lotes["marcas_versiones_imagen"] = _volcar_json(
        conexion, escribir, f"{carpeta}/marcas_versiones_imagen",
        "SELECT * FROM marcas_versiones_imagen WHERE id > %(desde)s ORDER BY id LIMIT %(tam)s", "id",
        TANDAS["marcas_versiones_imagen"])
    cantidades["respaldos_operacion"], lotes["respaldos_operacion"] = _volcar_json(
        conexion, escribir, f"{carpeta}/respaldos_operacion",
        "SELECT * FROM respaldos_operacion WHERE operacion_id > %(desde)s ORDER BY operacion_id LIMIT %(tam)s",
        "operacion_id", TANDAS["respaldos_operacion"])

    pdfs = 0
    for filas in _tandas(
        conexion, "SELECT id, contenido FROM documentos_emitidos WHERE id > %(desde)s ORDER BY id LIMIT %(tam)s", "id", 3
    ):
        for f in filas:
            escribir(f"{carpeta}/documentos/{f['id']:06d}.pdf", bytes(f["contenido"]))
            pdfs += 1
    cantidades["documentos_pdf"] = pdfs

    manifiesto = {"generado_en": ahora, "cantidades": cantidades, "lotes": lotes, "lotes_de_marcas": lotes["marcas"]}
    escribir(f"{carpeta}/manifiesto.json", dict_a_json(manifiesto).encode("utf-8"))
    return manifiesto
