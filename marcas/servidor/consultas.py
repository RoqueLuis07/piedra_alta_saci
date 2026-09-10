"""Consultas sobre Supabase para las vistas del servidor.

Espejo, en Postgres/PostgREST, de lo que ``marcas.db`` hace contra SQLite
para el selector visual local: buscar, y armar la ficha completa (marca +
formulario de origen + marcas que la acompañan).
"""

from __future__ import annotations

import time

from supabase import Client

BUCKET_IMAGENES = "marcas-imagenes"
POR_PAGINA_GUIAS = 40
POR_PAGINA_MARCAS = 40

# Deben coincidir exactamente con los arrays permitidos dentro de
# resolver_cambio_pendiente() en la base -- esa función es quien de verdad
# hace cumplir el límite (defensa en profundidad), esto es sólo para
# construir los formularios y no ofrecer editar un campo que después la
# aprobación va a rechazar.
CAMPOS_MARCA_EDITABLES = ["codigo", "tipo", "descripcion", "estado", "observaciones", "archivo_png", "archivo_svg"]
CAMPOS_OPERACION_EDITABLES = [
    "numero_guia", "fecha", "vendedor_nombre", "vendedor_documento",
    "vendedor_establecimiento", "vendedor_establecimiento_codigo",
    "comprador_nombre", "comprador_documento", "cantidad_animales",
    "categoria_animales", "categoria_animales_original", "tipo_formulario", "revisar",
]


def buscar_marcas(
    cliente: Client, texto: str | None, pagina: int = 1, por_pagina: int = POR_PAGINA_MARCAS
) -> tuple[list[dict], int]:
    """Los resultados de la página pedida y el total real de coincidencias."""
    texto = (texto or "").strip()
    consulta = cliente.table("marcas").select(
        "id, codigo, tipo, estado, posicion, propietario_id, operacion_id, archivo_png, "
        "propietarios(nombre, documento), operaciones(numero_guia, fecha)",
        count="exact",
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
    desde = max(0, pagina - 1) * por_pagina
    respuesta = consulta.order("codigo").range(desde, desde + por_pagina - 1).execute()
    return respuesta.data, (respuesta.count or 0)


def listar_operaciones_paginado(
    cliente: Client, pagina: int = 1, por_pagina: int = POR_PAGINA_GUIAS
) -> tuple[list[dict], int]:
    desde = max(0, pagina - 1) * por_pagina
    respuesta = (
        cliente.table("operaciones")
        .select(
            "id, numero_guia, fecha, vendedor_nombre, comprador_nombre, "
            "cantidad_animales, categoria_animales, revisar, guia_colisionada",
            count="exact",
        )
        .order("creado_en", desc=True)
        .range(desde, desde + por_pagina - 1)
        .execute()
    )
    return respuesta.data, (respuesta.count or 0)


def obtener_operacion_por_id(cliente: Client, operacion_id: int) -> dict | None:
    respuesta = cliente.table("operaciones").select("*").eq("id", operacion_id).maybe_single().execute()
    return respuesta.data if respuesta else None


def marcas_de_operacion(cliente: Client, operacion_id: int) -> list[dict]:
    return (
        cliente.table("marcas")
        .select("id, codigo, tipo, posicion, archivo_png, estado")
        .eq("operacion_id", operacion_id)
        .order("tipo")
        .order("posicion")
        .execute()
        .data
    )


def crear_operacion(cliente: Client, campos: dict, creado_por: str) -> int:
    """Alta de una guía nueva -- no es una edición, así que se aplica directo."""
    datos = {**campos, "origen": "manual", "creado_por": creado_por}
    respuesta = cliente.table("operaciones").insert(datos).execute()
    return respuesta.data[0]["id"]


def crear_marca(cliente: Client, campos: dict, creado_por: str) -> int:
    """Alta de una marca nueva -- no es una edición, así que se aplica directo."""
    datos = {**campos, "creado_por": creado_por}
    respuesta = cliente.table("marcas").insert(datos).execute()
    return respuesta.data[0]["id"]


def proponer_cambio(
    cliente: Client, tabla: str, fila_id: int, cambios: dict, valores_anteriores: dict, propuesto_por: str
) -> None:
    cliente.table("cambios_pendientes").insert(
        {
            "tabla": tabla,
            "fila_id": fila_id,
            "cambios": cambios,
            "valores_anteriores": valores_anteriores,
            "propuesto_por": propuesto_por,
            "estado": "pendiente",
        }
    ).execute()


def nombres_usuarios(cliente: Client, ids) -> dict[str, str]:
    """Resuelve id de usuario -> nombre, para mostrar quién cargó o tocó un registro."""
    ids_validos = {i for i in ids if i}
    if not ids_validos:
        return {}
    filas = cliente.table("perfiles").select("id, nombre").in_("id", list(ids_validos)).execute().data
    return {f["id"]: f["nombre"] for f in filas}


def cambio_pendiente_de(cliente: Client, tabla: str, fila_id: int) -> dict | None:
    """Si esta fila ya tiene una modificación esperando aprobación, la trae."""
    respuesta = (
        cliente.table("cambios_pendientes")
        .select("*")
        .eq("tabla", tabla)
        .eq("fila_id", fila_id)
        .eq("estado", "pendiente")
        .maybe_single()
        .execute()
    )
    return respuesta.data if respuesta else None


def descargar_imagen_marca(cliente: Client, ruta: str | None) -> bytes | None:
    """El contenido binario de una imagen del bucket -- para incrustarla en un PDF."""
    if not ruta:
        return None
    try:
        return cliente.storage.from_(BUCKET_IMAGENES).download(ruta)
    except Exception as exc:
        print(f"descargar_imagen_marca: no se pudo bajar {ruta!r}: {exc}")
        return None


def subir_imagen_marca(cliente: Client, codigo: str, contenido: bytes, extension: str) -> str:
    """Sube una imagen con nombre nuevo (no pisa la anterior) y devuelve el nombre de archivo."""
    tipo_contenido = "image/svg+xml" if extension == "svg" else f"image/{extension}"
    nombre = f"{codigo}_{int(time.time())}.{extension}"
    cliente.storage.from_(BUCKET_IMAGENES).upload(
        nombre, contenido, {"content-type": tipo_contenido, "upsert": "true"}
    )
    return nombre


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
            for campo_imagen in ("archivo_png", "archivo_svg"):
                if campo_imagen in c["cambios"]:
                    c[f"{campo_imagen}_anterior_url"] = url_imagen(
                        cliente, c["valores_anteriores"].get(campo_imagen)
                    )
                    c[f"{campo_imagen}_nueva_url"] = url_imagen(cliente, c["cambios"].get(campo_imagen))
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
    except Exception as exc:
        print(f"url_imagen: no se pudo firmar {ruta!r}: {exc}")
        return None
    return resultado.get("signedURL") or resultado.get("signed_url") or resultado.get("signedUrl")
