"""Carga del lote de digitalización masiva entregado por Cowork.

Este lote llega en un formato distinto al de la planilla manual
(``importacion.py``): trae dos entidades relacionadas -- una fila por
formulario (``operaciones``) y una fila por marca (``marcas``), vinculadas
por ``operacion_id`` en vez de por ``numero_guia``. Esto importa porque el
número de guía falla en la lectura OCR con la frecuencia suficiente como
para repetirse entre operaciones que no tienen nada que ver (18 operaciones
del primer lote real quedaron así, marcadas con ``guia_colisionada``): el
cruce marca -> formulario tiene que hacerse siempre por ``operacion_id``,
nunca por el número de guía.

La base entregada (``marcas_ganado.db``, el export SQLite) trae los datos
más los *nombres* de archivo de imagen; los PNG/SVG en sí llegan aparte, en
carpetas sueltas (a veces partidas en varios ZIP), y esta carga los busca
por nombre en cualquiera de las carpetas que se le pasen y los copia al
árbol de ``datos/marcas`` del proyecto -- a diferencia de la planilla
manual (que sólo referencia la imagen donde esté), acá conviene copiar
porque el origen es una carpeta de trabajo transitoria, no un lugar
definitivo.
"""

from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from marcas import config, db

LOTE_POR_DEFECTO = "cowork"


@dataclass
class ResultadoImportacionCowork:
    operaciones_nuevas: int = 0
    operaciones_actualizadas: int = 0
    marcas_nuevas: int = 0
    marcas_actualizadas: int = 0
    imagenes_copiadas: int = 0
    imagenes_faltantes: list[str] = field(default_factory=list)

    def resumen(self) -> dict:
        return {
            "operaciones_nuevas": self.operaciones_nuevas,
            "operaciones_actualizadas": self.operaciones_actualizadas,
            "marcas_nuevas": self.marcas_nuevas,
            "marcas_actualizadas": self.marcas_actualizadas,
            "imagenes_copiadas": self.imagenes_copiadas,
            "imagenes_faltantes": len(self.imagenes_faltantes),
        }


def _indexar_imagenes(carpetas: list[Path]) -> dict[str, Path]:
    """Mapea nombre de archivo -> ruta real, buscando en todas las carpetas.

    Las carpetas de un lote suelen venir partidas (``_parte1``, ``_parte2``,
    ...) y con subcarpetas (``marcas_svg/``); por eso se busca recursivo y
    por nombre de archivo, no por ruta relativa fija.
    """
    indice: dict[str, Path] = {}
    for carpeta in carpetas:
        for ruta in Path(carpeta).rglob("*"):
            if ruta.is_file():
                indice.setdefault(ruta.name, ruta)
    return indice


def _copiar_imagen(
    nombre: str | None,
    indice: dict[str, Path],
    destino_dir: Path,
    resultado: ResultadoImportacionCowork,
) -> str | None:
    if not nombre:
        return None
    origen_ruta = indice.get(nombre)
    if origen_ruta is None:
        resultado.imagenes_faltantes.append(nombre)
        return None
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / nombre
    if not destino.exists():
        shutil.copyfile(origen_ruta, destino)
        resultado.imagenes_copiadas += 1
    destino = destino.resolve()
    if destino.is_relative_to(config.RAIZ):
        return str(destino.relative_to(config.RAIZ))
    return str(destino)


def importar_paquete(
    ruta_sqlite_origen: Path | str,
    carpetas_imagenes: list[Path | str],
    ruta_db: Path | None = None,
    lote: str = LOTE_POR_DEFECTO,
) -> ResultadoImportacionCowork:
    """Carga operaciones + marcas desde el export SQLite entregado por Cowork.

    Es idempotente: se puede volver a correr sobre el mismo lote (por
    ejemplo, después de que Cowork corrija algunas imágenes) sin duplicar
    filas -- las operaciones se identifican por ``(lote, id-de-origen)`` y
    las marcas, dentro de cada operación, por su posición en la grilla.
    """
    db.inicializar(ruta_db)
    indice_imagenes = _indexar_imagenes([Path(c) for c in carpetas_imagenes])
    resultado = ResultadoImportacionCowork()

    origen = sqlite3.connect(Path(ruta_sqlite_origen))
    origen.row_factory = sqlite3.Row
    try:
        operaciones = origen.execute("SELECT * FROM operaciones").fetchall()
        marcas = origen.execute("SELECT * FROM marcas").fetchall()
    finally:
        origen.close()

    id_por_origen: dict[int, int] = {}
    propietario_por_operacion: dict[int, int | None] = {}
    for op in operaciones:
        campos = {c: op[c] for c in db.CAMPOS_OPERACION if c in op.keys()}
        operacion_id, es_nueva = db.alta_operacion(lote, op["id"], ruta_db, **campos)
        id_por_origen[op["id"]] = operacion_id
        if es_nueva:
            resultado.operaciones_nuevas += 1
        else:
            resultado.operaciones_actualizadas += 1

        # El propietario de las marcas es el vendedor de la operación (el
        # ganadero dueño de la marca, no Piedra Alta que es quien compra).
        # Se da de alta una sola vez por operación, no por cada marca.
        propietario_id = None
        if op["vendedor_nombre"]:
            propietario_id = db.alta_propietario(
                op["vendedor_nombre"],
                ruta_db,
                documento=op["vendedor_documento"],
                establecimiento=op["vendedor_establecimiento"],
                establecimiento_codigo=op["vendedor_establecimiento_codigo"],
            )
        propietario_por_operacion[operacion_id] = propietario_id

    for m in marcas:
        operacion_id = id_por_origen[m["operacion_id"]]
        archivo_png = _copiar_imagen(
            m["archivo_imagen"], indice_imagenes, config.DIR_PNG, resultado
        )
        archivo_svg = _copiar_imagen(
            m["archivo_svg"], indice_imagenes, config.DIR_SVG, resultado
        )
        # Código provisional: el nombre de archivo ya es único por
        # construcción (una celda de una página). Queda como identificador
        # hasta que alguien lo reemplace por el código oficial del registro.
        codigo = Path(m["archivo_imagen"]).stem if m["archivo_imagen"] else f"{lote}-{m['id']}"

        _, es_nueva = db.alta_marca_de_operacion(
            codigo, operacion_id, m["archivo_imagen"], ruta_db,
            tipo=m["tipo"],
            numero_guia=m["numero_guia"],
            posicion=m["posicion"],
            archivo_png=archivo_png,
            archivo_svg=archivo_svg,
            propietario_id=propietario_por_operacion.get(operacion_id),
            sospechosa_calidad=m["sospechosa_calidad"],
            motivo_calidad=m["motivo_calidad"],
            borde_limpiado=m["borde_limpiado"],
            estado="revisar" if m["sospechosa_calidad"] else "activa",
        )
        if es_nueva:
            resultado.marcas_nuevas += 1
        else:
            resultado.marcas_actualizadas += 1

    return resultado
