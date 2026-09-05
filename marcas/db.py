"""Base de datos del catálogo (SQLite).

SQLite es suficiente para este volumen (miles de marcas), no necesita servidor
y el archivo .db se respalda copiándolo. Si algún día hay varios usuarios
escribiendo a la vez, el mismo esquema se migra a PostgreSQL sin cambios de
fondo.

Las imágenes NO se guardan dentro de la base: se guarda la ruta al PNG y al
SVG. Así la base queda liviana y los archivos siguen siendo utilizables por
fuera del sistema.
"""

from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from marcas import config

ESQUEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS propietarios (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL,
    documento       TEXT,
    establecimiento TEXT,
    localidad       TEXT,
    departamento    TEXT,
    telefono        TEXT,
    creado_en       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (nombre, documento)
);

CREATE TABLE IF NOT EXISTS marcas (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo         TEXT NOT NULL UNIQUE,
    descripcion    TEXT,
    propietario_id INTEGER REFERENCES propietarios(id) ON DELETE SET NULL,
    archivo_png    TEXT,
    archivo_svg    TEXT,
    hoja           TEXT,
    fila           INTEGER,
    columna        INTEGER,
    ancho_px       INTEGER,
    alto_px        INTEGER,
    aspecto        REAL,
    tinta_pct      REAL,
    componentes    INTEGER,
    sha1           TEXT,
    estado         TEXT NOT NULL DEFAULT 'activa'
                   CHECK (estado IN ('activa', 'revisar', 'baja')),
    observaciones  TEXT,
    creado_en      TEXT NOT NULL DEFAULT (datetime('now')),
    actualizado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS ix_marcas_estado      ON marcas(estado);
CREATE INDEX IF NOT EXISTS ix_marcas_propietario ON marcas(propietario_id);
CREATE INDEX IF NOT EXISTS ix_marcas_sha1        ON marcas(sha1);

CREATE TABLE IF NOT EXISTS planillas (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    titulo    TEXT NOT NULL,
    subtitulo TEXT,
    creado_en TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS planilla_items (
    planilla_id INTEGER NOT NULL REFERENCES planillas(id) ON DELETE CASCADE,
    marca_id    INTEGER NOT NULL REFERENCES marcas(id)    ON DELETE CASCADE,
    posicion    INTEGER NOT NULL,
    PRIMARY KEY (planilla_id, marca_id)
);
"""


@contextmanager
def conectar(ruta: Path | None = None) -> Iterator[sqlite3.Connection]:
    ruta = Path(ruta or config.BASE_DATOS)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def inicializar(ruta: Path | None = None) -> Path:
    ruta = Path(ruta or config.BASE_DATOS)
    with conectar(ruta) as con:
        con.executescript(ESQUEMA)
    return ruta


def _bool(valor) -> bool:
    return str(valor).strip().lower() in {"1", "true", "verdadero", "si", "sí"}


def importar_manifiesto(
    ruta_csv: Path, ruta_db: Path | None = None
) -> dict[str, int]:
    """Carga (o actualiza) el catálogo a partir del manifiesto del pipeline.

    La clave es el ``codigo``: reprocesar una hoja y volver a importar
    actualiza las filas existentes en lugar de duplicarlas, y no pisa los datos
    cargados a mano (propietario, descripción).
    """
    inicializar(ruta_db)
    nuevas = actualizadas = omitidas = 0
    with open(ruta_csv, newline="", encoding="utf-8-sig") as fh, \
            conectar(ruta_db) as con:
        for fila in csv.DictReader(fh):
            if not fila.get("png"):
                omitidas += 1          # casilla vacía: no hay imagen que catalogar
                continue
            estado = "revisar" if _bool(fila.get("revisar")) else "activa"
            datos = (
                fila["codigo"], fila.get("png") or None, fila.get("svg") or None,
                fila.get("hoja"), int(fila["fila"]), int(fila["columna"]),
                int(fila["ancho_px"]), int(fila["alto_px"]),
                float(fila["aspecto"]), float(fila["tinta_pct"]),
                int(fila["componentes"]), fila.get("sha1"), estado,
                fila.get("observaciones") or None,
            )
            existe = con.execute(
                "SELECT id FROM marcas WHERE codigo = ?", (fila["codigo"],)
            ).fetchone()
            if existe:
                con.execute(
                    """UPDATE marcas SET archivo_png=?, archivo_svg=?, hoja=?,
                           fila=?, columna=?, ancho_px=?, alto_px=?, aspecto=?,
                           tinta_pct=?, componentes=?, sha1=?, estado=?,
                           observaciones=?, actualizado_en=datetime('now')
                       WHERE codigo=?""",
                    (*datos[1:], fila["codigo"]),
                )
                actualizadas += 1
            else:
                con.execute(
                    """INSERT INTO marcas (codigo, archivo_png, archivo_svg, hoja,
                           fila, columna, ancho_px, alto_px, aspecto, tinta_pct,
                           componentes, sha1, estado, observaciones)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    datos,
                )
                nuevas += 1
    return {"nuevas": nuevas, "actualizadas": actualizadas, "omitidas": omitidas}


def listar_marcas(
    *,
    busqueda: str | None = None,
    estado: str | None = None,
    propietario_id: int | None = None,
    limite: int | None = None,
    ruta_db: Path | None = None,
) -> list[sqlite3.Row]:
    sql = [
        """SELECT m.*, p.nombre AS propietario
             FROM marcas m LEFT JOIN propietarios p ON p.id = m.propietario_id
            WHERE 1=1"""
    ]
    params: list = []
    if busqueda:
        sql.append(
            "AND (m.codigo LIKE ? OR m.descripcion LIKE ? OR p.nombre LIKE ?)"
        )
        params += [f"%{busqueda}%"] * 3
    if estado:
        sql.append("AND m.estado = ?")
        params.append(estado)
    if propietario_id:
        sql.append("AND m.propietario_id = ?")
        params.append(propietario_id)
    sql.append("ORDER BY m.codigo")
    if limite:
        sql.append("LIMIT ?")
        params.append(limite)
    with conectar(ruta_db) as con:
        return con.execute(" ".join(sql), params).fetchall()


def obtener_marcas(codigos: Iterable[str], ruta_db: Path | None = None) -> list[sqlite3.Row]:
    """Devuelve las marcas pedidas respetando el orden de ``codigos``."""
    codigos = list(codigos)
    if not codigos:
        return []
    marcadores = ",".join("?" * len(codigos))
    with conectar(ruta_db) as con:
        filas = con.execute(
            f"""SELECT m.*, p.nombre AS propietario
                  FROM marcas m LEFT JOIN propietarios p ON p.id = m.propietario_id
                 WHERE m.codigo IN ({marcadores})""",
            codigos,
        ).fetchall()
    por_codigo = {f["codigo"]: f for f in filas}
    return [por_codigo[c] for c in codigos if c in por_codigo]


def actualizar_marca(codigo: str, ruta_db: Path | None = None, **campos) -> int:
    """Edita campos sueltos de una marca (propietario, descripción, estado)."""
    permitidos = {"descripcion", "propietario_id", "estado", "observaciones", "codigo"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos:
        return 0
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    with conectar(ruta_db) as con:
        cur = con.execute(
            f"UPDATE marcas SET {asignaciones}, actualizado_en = datetime('now') "
            "WHERE codigo = ?",
            (*campos.values(), codigo),
        )
        return cur.rowcount


def alta_propietario(nombre: str, ruta_db: Path | None = None, **datos) -> int:
    columnas = ["nombre"] + [k for k in datos if k in {
        "documento", "establecimiento", "localidad", "departamento", "telefono"
    }]
    valores = [nombre] + [datos[k] for k in columnas[1:]]
    marcadores = ",".join("?" * len(columnas))
    with conectar(ruta_db) as con:
        cur = con.execute(
            f"INSERT OR IGNORE INTO propietarios ({','.join(columnas)}) "
            f"VALUES ({marcadores})",
            valores,
        )
        if cur.lastrowid:
            return cur.lastrowid
        fila = con.execute(
            "SELECT id FROM propietarios WHERE nombre = ?", (nombre,)
        ).fetchone()
        return fila["id"]


def guardar_planilla(
    titulo: str,
    codigos: Iterable[str],
    subtitulo: str | None = None,
    ruta_db: Path | None = None,
) -> int:
    """Deja registrada la selección usada para un PDF, para poder reimprimirlo."""
    codigos = list(codigos)
    with conectar(ruta_db) as con:
        cur = con.execute(
            "INSERT INTO planillas (titulo, subtitulo) VALUES (?, ?)",
            (titulo, subtitulo),
        )
        planilla_id = cur.lastrowid
        for pos, codigo in enumerate(codigos, start=1):
            fila = con.execute(
                "SELECT id FROM marcas WHERE codigo = ?", (codigo,)
            ).fetchone()
            if fila:
                con.execute(
                    "INSERT INTO planilla_items (planilla_id, marca_id, posicion) "
                    "VALUES (?,?,?)",
                    (planilla_id, fila["id"], pos),
                )
    return planilla_id


def estadisticas(ruta_db: Path | None = None) -> dict:
    with conectar(ruta_db) as con:
        total = con.execute("SELECT COUNT(*) c FROM marcas").fetchone()["c"]
        por_estado = {
            f["estado"]: f["c"]
            for f in con.execute(
                "SELECT estado, COUNT(*) c FROM marcas GROUP BY estado"
            )
        }
        propietarios = con.execute(
            "SELECT COUNT(*) c FROM propietarios"
        ).fetchone()["c"]
        sin_duenio = con.execute(
            "SELECT COUNT(*) c FROM marcas WHERE propietario_id IS NULL"
        ).fetchone()["c"]
        duplicados = con.execute(
            "SELECT COUNT(*) c FROM (SELECT sha1 FROM marcas WHERE sha1 IS NOT NULL "
            "GROUP BY sha1 HAVING COUNT(*) > 1)"
        ).fetchone()["c"]
    return {
        "marcas": total,
        "por_estado": por_estado,
        "propietarios": propietarios,
        "sin_propietario": sin_duenio,
        "imagenes_duplicadas": duplicados,
        "generado": datetime.now().isoformat(timespec="seconds"),
    }
