"""Migra el catálogo local (SQLite) a Supabase: datos + imágenes.

Se corre una vez (o de nuevo cada vez que haga falta volver a sincronizar)
con las credenciales del proyecto como variables de entorno -- nunca por
línea de comandos ni en el historial de la terminal:

    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \\
        python scripts/migrar_a_supabase.py

Usa la clave ``service_role`` a propósito (se salta RLS): es un script de
administración que corre una persona con acceso directo al proyecto, no una
request de un usuario del sistema -- por eso vive aparte del código de la
aplicación web (``marcas.servidor``), que jamás debe usar esta clave.

Es idempotente: se puede volver a correr sin duplicar filas (upsert por la
misma clave natural que ya usa el catálogo local) ni volver a subir una
imagen que ya está en el bucket.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from marcas import config  # noqa: E402
from marcas.servidor.consultas import BUCKET_IMAGENES  # noqa: E402
from marcas.servidor.supa import cliente_administrador  # noqa: E402

ORIGEN = "local_sqlite"
TAM_LOTE = 200


def migrar_propietarios(con_local: sqlite3.Connection, supabase) -> dict[int, int]:
    campos = [
        "nombre", "documento", "establecimiento", "establecimiento_codigo",
        "localidad", "departamento", "telefono",
    ]
    filas = con_local.execute(
        """SELECT DISTINCT p.* FROM propietarios p
           JOIN marcas m ON m.propietario_id = p.id
           WHERE m.operacion_id IS NOT NULL"""
    ).fetchall()
    mapa: dict[int, int] = {}
    for fila in filas:
        datos = {c: fila[c] for c in campos}
        respuesta = (
            supabase.table("propietarios")
            .upsert(datos, on_conflict="nombre,documento")
            .execute()
        )
        if respuesta.data:
            mapa[fila["id"]] = respuesta.data[0]["id"]
    print(f"propietarios: {len(mapa)}")
    return mapa


def migrar_operaciones(con_local: sqlite3.Connection, supabase) -> dict[int, int]:
    campos = [
        "numero_guia", "fecha", "vendedor_nombre", "vendedor_documento",
        "vendedor_establecimiento", "vendedor_establecimiento_codigo",
        "comprador_nombre", "comprador_documento", "cantidad_animales",
        "categoria_animales", "categoria_animales_original", "tipo_formulario",
        "guia_colisionada", "revisar",
    ]
    filas = con_local.execute("SELECT * FROM operaciones WHERE origen='cowork'").fetchall()
    lote, origen_ids = [], []
    for fila in filas:
        datos = {c: fila[c] for c in campos}
        datos["guia_colisionada"] = bool(datos["guia_colisionada"])
        datos["origen"] = ORIGEN
        datos["origen_id"] = fila["id"]
        lote.append(datos)
        origen_ids.append(fila["id"])

    for i in range(0, len(lote), TAM_LOTE):
        supabase.table("operaciones").upsert(
            lote[i:i + TAM_LOTE], on_conflict="origen,origen_id"
        ).execute()

    # origen_id es único (viene del id local): releer para armar el mapa sin
    # ambigüedad, a diferencia de propietarios (nombre+documento puede
    # repetirse con documento NULL).
    mapa: dict[int, int] = {}
    for i in range(0, len(origen_ids), TAM_LOTE):
        sub = origen_ids[i:i + TAM_LOTE]
        filas_pg = (
            supabase.table("operaciones")
            .select("id, origen_id")
            .eq("origen", ORIGEN)
            .in_("origen_id", sub)
            .execute()
            .data
        )
        for f in filas_pg:
            mapa[f["origen_id"]] = f["id"]
    print(f"operaciones: {len(mapa)}")
    return mapa


def migrar_marcas(
    con_local: sqlite3.Connection, supabase,
    mapa_operaciones: dict[int, int], mapa_propietarios: dict[int, int],
) -> None:
    campos_directos = [
        "tipo", "numero_guia", "posicion", "origen_archivo",
        "borde_limpiado", "sospechosa_calidad", "motivo_calidad", "estado",
    ]
    filas = con_local.execute("SELECT * FROM marcas WHERE operacion_id IS NOT NULL").fetchall()
    lote = []
    for fila in filas:
        datos = {c: fila[c] for c in campos_directos}
        datos["borde_limpiado"] = bool(datos["borde_limpiado"])
        datos["sospechosa_calidad"] = bool(datos["sospechosa_calidad"])
        datos["codigo"] = fila["codigo"]
        datos["descripcion"] = fila["descripcion"]
        datos["observaciones"] = fila["observaciones"]
        datos["operacion_id"] = mapa_operaciones.get(fila["operacion_id"])
        datos["propietario_id"] = (
            mapa_propietarios.get(fila["propietario_id"]) if fila["propietario_id"] else None
        )
        lote.append(datos)

    for i in range(0, len(lote), TAM_LOTE):
        supabase.table("marcas").upsert(lote[i:i + TAM_LOTE], on_conflict="codigo").execute()
    print(f"marcas: {len(lote)}")


def subir_imagenes(supabase) -> None:
    """Sube los PNG/SVG ya digitalizados al bucket y actualiza cada marca."""
    subidos_png: set[str] = set()
    subidos_svg: set[str] = set()

    for carpeta, destino_set, tipo_contenido in (
        (config.DIR_PNG, subidos_png, "image/png"),
        (config.DIR_SVG, subidos_svg, "image/svg+xml"),
    ):
        if not carpeta.exists():
            continue
        for archivo in sorted(carpeta.iterdir()):
            if not archivo.is_file():
                continue
            with open(archivo, "rb") as fh:
                try:
                    supabase.storage.from_(BUCKET_IMAGENES).upload(
                        archivo.name, fh.read(),
                        {"content-type": tipo_contenido, "upsert": "true"},
                    )
                    destino_set.add(archivo.name)
                except Exception as exc:                     # pragma: no cover
                    print(f"  no se pudo subir {archivo.name}: {exc}")

    print(f"imágenes subidas: {len(subidos_png)} png, {len(subidos_svg)} svg")

    marcas = supabase.table("marcas").select("id, origen_archivo").execute().data
    for m in marcas:
        if not m["origen_archivo"]:
            continue
        base = Path(m["origen_archivo"]).stem
        campos = {}
        if f"{base}.png" in subidos_png:
            campos["archivo_png"] = f"{base}.png"
        if f"{base}.svg" in subidos_svg:
            campos["archivo_svg"] = f"{base}.svg"
        if campos:
            supabase.table("marcas").update(campos).eq("id", m["id"]).execute()


def main() -> None:
    con_local = sqlite3.connect(config.BASE_DATOS)
    con_local.row_factory = sqlite3.Row
    supabase = cliente_administrador()

    mapa_propietarios = migrar_propietarios(con_local, supabase)
    mapa_operaciones = migrar_operaciones(con_local, supabase)
    migrar_marcas(con_local, supabase, mapa_operaciones, mapa_propietarios)
    subir_imagenes(supabase)
    print("Listo.")


if __name__ == "__main__":
    main()
