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
    establecimiento_codigo TEXT,
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


# Columnas agregadas después de la primera versión del esquema. SQLite acepta
# ALTER TABLE ADD COLUMN, así que una base existente se actualiza sin recrearla
# ni perder datos.
COLUMNAS_AGREGADAS = {
    "propietarios": {"establecimiento_codigo": "TEXT"},
    "marcas": {
        # "dominante" o "complementaria": para el caso de una operación con
        # varias marcas (una guía de traslado trae 1 dominante + hasta N
        # complementarias). Nula para una marca cargada suelta, sin ese
        # contexto.
        "tipo": "TEXT",
        # Número de guía/orden del documento de origen (no es el código de
        # la marca): sirve para volver a agrupar las marcas de una misma
        # operación y para trazabilidad hacia el documento original.
        "numero_guia": "TEXT",
    },
}


def _migrar(con: sqlite3.Connection) -> None:
    for tabla, columnas in COLUMNAS_AGREGADAS.items():
        existentes = {
            f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")
        }
        for nombre, tipo in columnas.items():
            if nombre not in existentes:
                con.execute(f"ALTER TABLE {tabla} ADD COLUMN {nombre} {tipo}")


def inicializar(ruta: Path | None = None) -> Path:
    ruta = Path(ruta or config.BASE_DATOS)
    with conectar(ruta) as con:
        con.executescript(ESQUEMA)
        _migrar(con)
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
    sin_imagen: bool | None = None,
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
    if sin_imagen is True:
        sql.append("AND (m.archivo_png IS NULL OR m.archivo_png = '')")
    elif sin_imagen is False:
        sql.append("AND m.archivo_png IS NOT NULL AND m.archivo_png <> ''")
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


def actualizar_marca(codigo_actual: str, ruta_db: Path | None = None, **campos) -> int:
    """Edita campos sueltos de una marca (propietario, descripción, estado).

    ``codigo_actual`` identifica la fila a editar; si ``campos`` trae a su vez
    una clave ``codigo``, es un pedido de renombrar la marca (por ejemplo, para
    reemplazar el código automático de la digitalización por el código
    oficial del registro). Los dos no pueden compartir nombre de parámetro:
    de ahí que el que identifica la fila lleve el sufijo ``_actual``.
    """
    permitidos = {"descripcion", "propietario_id", "estado", "observaciones", "codigo"}
    campos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos:
        return 0
    asignaciones = ", ".join(f"{k} = ?" for k in campos)
    with conectar(ruta_db) as con:
        cur = con.execute(
            f"UPDATE marcas SET {asignaciones}, actualizado_en = datetime('now') "
            "WHERE codigo = ?",
            (*campos.values(), codigo_actual),
        )
        return cur.rowcount


CAMPOS_PROPIETARIO = (
    "documento", "establecimiento", "establecimiento_codigo",
    "localidad", "departamento", "telefono",
)


def alta_propietario(nombre: str, ruta_db: Path | None = None, **datos) -> int:
    """Crea el propietario o devuelve el existente, completando lo que falte.

    La identidad es el documento (CI/RUC) cuando está: dos estancias pueden
    llamarse parecido, pero el RUC es único. Sin documento, se usa el nombre.
    """
    inicializar(ruta_db)
    datos = {k: v for k, v in datos.items() if k in CAMPOS_PROPIETARIO and v}
    documento = datos.get("documento")
    with conectar(ruta_db) as con:
        fila = None
        if documento:
            fila = con.execute(
                "SELECT * FROM propietarios WHERE documento = ?", (documento,)
            ).fetchone()
        if fila is None:
            fila = con.execute(
                "SELECT * FROM propietarios WHERE nombre = ? AND documento IS NULL",
                (nombre,),
            ).fetchone()

        if fila is None:
            columnas = ["nombre", *datos]
            marcadores = ",".join("?" * len(columnas))
            cur = con.execute(
                f"INSERT INTO propietarios ({','.join(columnas)}) VALUES ({marcadores})",
                [nombre, *datos.values()],
            )
            return cur.lastrowid

        # Existe: se completan sólo los campos que estaban vacíos, para no
        # pisar datos corregidos a mano con los de una planilla incompleta.
        faltantes = {k: v for k, v in datos.items() if not fila[k]}
        if faltantes:
            asignaciones = ", ".join(f"{k} = ?" for k in faltantes)
            con.execute(
                f"UPDATE propietarios SET {asignaciones} WHERE id = ?",
                [*faltantes.values(), fila["id"]],
            )
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
        sin_imagen = con.execute(
            "SELECT COUNT(*) c FROM marcas WHERE archivo_png IS NULL "
            "OR archivo_png = ''"
        ).fetchone()["c"]
    return {
        "marcas": total,
        "por_estado": por_estado,
        "propietarios": propietarios,
        "sin_propietario": sin_duenio,
        "sin_imagen": sin_imagen,
        "imagenes_duplicadas": duplicados,
        "generado": datetime.now().isoformat(timespec="seconds"),
    }
