"""Consultas sobre Supabase para las vistas del servidor.

Espejo, en Postgres/PostgREST, de lo que ``marcas.db`` hace contra SQLite
para el selector visual local: buscar, y armar la ficha completa (marca +
formulario de origen + marcas que la acompañan).
"""

from __future__ import annotations

from supabase import Client

BUCKET_IMAGENES = "marcas-imagenes"


def buscar_marcas(cliente: Client, texto: str | None, limite: int = 50) -> list[dict]:
    texto = (texto or "").strip()
    consulta = cliente.table("marcas").select(
        "id, codigo, tipo, estado, posicion, propietario_id, operacion_id, "
        "propietarios(nombre, documento), operaciones(numero_guia, fecha)"
    )
    if texto:
        coincidencias = (
            cliente.table("propietarios")
            .select("id")
            .or_(f"nombre.ilike.%{texto}%,documento.ilike.%{texto}%")
            .execute()
            .data
        )
        filtro = f"codigo.ilike.%{texto}%,numero_guia.ilike.%{texto}%"
        if coincidencias:
            lista = ",".join(str(p["id"]) for p in coincidencias)
            filtro += f",propietario_id.in.({lista})"
        consulta = consulta.or_(filtro)
    return consulta.order("codigo").limit(limite).execute().data


def ficha_marca(cliente: Client, codigo: str) -> dict | None:
    """La marca, su formulario de origen y las marcas que la acompañan."""
    respuesta = (
        cliente.table("marcas")
        .select("*, propietarios(nombre, documento, establecimiento)")
        .eq("codigo", codigo)
        .maybe_single()
        .execute()
    )
    marca = respuesta.data if respuesta else None
    if not marca:
        return None

    operacion = None
    acompanantes: list[dict] = []
    if marca.get("operacion_id"):
        operacion = (
            cliente.table("operaciones")
            .select("*")
            .eq("id", marca["operacion_id"])
            .maybe_single()
            .execute()
            .data
        )
        hermanas = (
            cliente.table("marcas")
            .select("codigo, tipo, posicion, archivo_png, archivo_svg, estado")
            .eq("operacion_id", marca["operacion_id"])
            .order("tipo")
            .order("posicion")
            .execute()
            .data
        )
        acompanantes = [h for h in hermanas if h["codigo"] != codigo]

    return {"marca": marca, "operacion": operacion, "acompanantes": acompanantes}


def estadisticas(cliente: Client) -> dict:
    total = cliente.table("marcas").select("id", count="exact").execute().count or 0
    a_revisar = (
        cliente.table("marcas").select("id", count="exact").eq("estado", "revisar").execute().count
        or 0
    )
    pendientes = (
        cliente.table("cambios_pendientes")
        .select("id", count="exact")
        .eq("estado", "pendiente")
        .execute()
        .count
        or 0
    )
    return {"total": total, "a_revisar": a_revisar, "pendientes": pendientes}


def ultimos_asientos(cliente: Client, limite: int = 6) -> list[dict]:
    return (
        cliente.table("operaciones")
        .select("id, numero_guia, fecha, vendedor_nombre, creado_en")
        .order("creado_en", desc=True)
        .limit(limite)
        .execute()
        .data
    )


def marcas_a_revisar(cliente: Client, limite: int = 6) -> list[dict]:
    return (
        cliente.table("marcas")
        .select("codigo, motivo_calidad")
        .eq("estado", "revisar")
        .order("actualizado_en", desc=True)
        .limit(limite)
        .execute()
        .data
    )


def cambios_pendientes_detalle(cliente: Client) -> list[dict]:
    """Los cambios en cola de aprobación, con el código de la marca/operación."""
    cambios = (
        cliente.table("cambios_pendientes")
        .select("*")
        .eq("estado", "pendiente")
        .order("propuesto_en")
        .execute()
        .data
    )
    for c in cambios:
        c["referencia"] = c["fila_id"]
        if c["tabla"] == "marcas":
            fila = (
                cliente.table("marcas").select("codigo").eq("id", c["fila_id"]).maybe_single().execute()
            )
            if fila and fila.data:
                c["referencia"] = fila.data["codigo"]
        elif c["tabla"] == "operaciones":
            fila = (
                cliente.table("operaciones")
                .select("numero_guia")
                .eq("id", c["fila_id"])
                .maybe_single()
                .execute()
            )
            if fila and fila.data:
                c["referencia"] = fila.data["numero_guia"] or c["fila_id"]
    return cambios


def url_imagen(cliente: Client, ruta: str | None, expira_seg: int = 3600) -> str | None:
    """URL firmada y temporal hacia el bucket privado -- nunca una URL pública fija."""
    if not ruta:
        return None
    try:
        resultado = cliente.storage.from_(BUCKET_IMAGENES).create_signed_url(ruta, expira_seg)
    except Exception:
        return None
    return resultado.get("signedURL") or resultado.get("signed_url") or resultado.get("signedUrl")
