"""Consultas sobre Supabase para las vistas del servidor.

Espejo, en Postgres/PostgREST, de lo que ``marcas.db`` hace contra SQLite
para el selector visual local: buscar, y armar la ficha completa (marca +
formulario de origen + marcas que la acompañan).
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from datetime import date, timedelta

from supabase import Client

BUCKET_IMAGENES = "marcas-imagenes"
POR_PAGINA_GUIAS = 40
POR_PAGINA_MARCAS = 40
POR_PAGINA_PROPIETARIOS = 40
TAMANO_LOTE_EXPORTACION = 1000

# Deben coincidir exactamente con los arrays permitidos dentro de
# resolver_cambio_pendiente() en la base -- esa función es quien de verdad
# hace cumplir el límite (defensa en profundidad), esto es sólo para
# construir los formularios y no ofrecer editar un campo que después la
# aprobación va a rechazar.
CAMPOS_MARCA_EDITABLES = [
    "codigo", "tipo", "descripcion", "estado", "observaciones", "archivo_png", "archivo_svg", "vence_en",
]
CAMPOS_PROPIETARIO_EDITABLES = [
    "telefono", "localidad", "departamento", "establecimiento", "establecimiento_codigo",
]
CAMPOS_OPERACION_EDITABLES = [
    "numero_guia", "fecha", "vendedor_nombre", "vendedor_documento",
    "vendedor_establecimiento", "vendedor_establecimiento_codigo",
    "comprador_nombre", "comprador_documento", "cantidad_animales",
    "categoria_animales", "categoria_animales_original", "tipo_formulario", "revisar",
    "tipo_operacion",
]


_CARACTERES_RESERVADOS_FILTRO = re.compile(r'([,.()\\*"])')


def _escapar_filtro(texto: str) -> str:
    """Neutraliza los caracteres que PostgREST usa como separadores dentro de
    ``or_()`` (coma, punto, paréntesis) -- si no se escapan, un texto de
    búsqueda armado a propósito podría agregar condiciones que no eran la
    intención (ej. sumar otra comparación al filtro)."""
    return _CARACTERES_RESERVADOS_FILTRO.sub(r"\\\1", texto)


def buscar_marcas(
    cliente: Client,
    texto: str | None,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA_MARCAS,
    estado: str | None = None,
    tipo: str | None = None,
) -> tuple[list[dict], int]:
    """Los resultados de la página pedida y el total real de coincidencias."""
    texto = (texto or "").strip()
    consulta = cliente.table("marcas").select(
        "id, codigo, tipo, estado, posicion, propietario_id, operacion_id, archivo_png, vence_en, "
        "propietarios(nombre, documento), operaciones(numero_guia, fecha)",
        count="exact",
    )
    if estado in ("activa", "revisar", "baja"):
        consulta = consulta.eq("estado", estado)
    if tipo in ("dominante", "complementaria"):
        consulta = consulta.eq("tipo", tipo)
    if texto:
        texto_seguro = _escapar_filtro(texto)
        propietarios_coincidentes = (
            cliente.table("propietarios")
            .select("id")
            .or_(f"nombre.ilike.%{texto_seguro}%,documento.ilike.%{texto_seguro}%")
            .execute()
            .data
        )
        # "numero_guia" en marcas es una columna heredada de la migración inicial
        # que no se completa para las marcas nuevas (se vinculan a su guía sólo
        # por operacion_id) -- buscar el N.º de guía tiene que cruzar contra la
        # guía real, del mismo modo que ya se hace para propietario.
        operaciones_coincidentes = (
            cliente.table("operaciones")
            .select("id")
            .ilike("numero_guia", f"%{texto_seguro}%")
            .execute()
            .data
        )
        filtro = f"codigo.ilike.%{texto_seguro}%,numero_guia.ilike.%{texto_seguro}%"
        if propietarios_coincidentes:
            lista = ",".join(str(p["id"]) for p in propietarios_coincidentes)
            filtro += f",propietario_id.in.({lista})"
        if operaciones_coincidentes:
            lista = ",".join(str(o["id"]) for o in operaciones_coincidentes)
            filtro += f",operacion_id.in.({lista})"
        consulta = consulta.or_(filtro)
    desde = max(0, pagina - 1) * por_pagina
    respuesta = consulta.order("codigo").range(desde, desde + por_pagina - 1).execute()
    return respuesta.data, (respuesta.count or 0)


def listar_operaciones_paginado(
    cliente: Client,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA_GUIAS,
    texto: str | None = None,
    estado: str | None = None,
    creada_desde: str | None = None,
    creada_hasta: str | None = None,
    tipo_operacion: str | None = None,
) -> tuple[list[dict], int]:
    """``estado`` filtra por lo que ya se muestra como estado en el listado:
    'revisar' (tiene notas de revisión), 'colisiona' (guía colisionada) o
    'al_dia' (ninguna de las dos). ``creada_desde``/``creada_hasta`` son
    fechas ISO (AAAA-MM-DD) sobre ``creado_en`` -- la fecha real y confiable
    de carga en el sistema, no el campo de texto libre ``fecha`` del formulario
    de origen, que en más de las tres cuartas partes de las guías migradas
    llegó vacío o en formatos dispares. ``tipo_operacion`` filtra 'compra' o
    'venta' -- las guías de Piedra Alta comprando o vendiendo, respectivamente."""
    consulta = cliente.table("operaciones").select(
        "id, numero_guia, fecha, vendedor_nombre, comprador_nombre, "
        "cantidad_animales, categoria_animales, revisar, guia_colisionada, creado_en, tipo_operacion",
        count="exact",
    )
    if tipo_operacion in ("compra", "venta"):
        consulta = consulta.eq("tipo_operacion", tipo_operacion)
    if estado == "revisar":
        consulta = consulta.not_.is_("revisar", "null").neq("revisar", "")
    elif estado == "colisiona":
        consulta = consulta.eq("guia_colisionada", True)
    elif estado == "al_dia":
        consulta = consulta.eq("guia_colisionada", False).or_("revisar.is.null,revisar.eq.")
    if creada_desde:
        consulta = consulta.gte("creado_en", creada_desde)
    if creada_hasta:
        consulta = consulta.lte("creado_en", f"{creada_hasta}T23:59:59")
    texto = (texto or "").strip()
    if texto:
        texto_seguro = _escapar_filtro(texto)
        consulta = consulta.or_(
            f"numero_guia.ilike.%{texto_seguro}%,vendedor_nombre.ilike.%{texto_seguro}%,"
            f"comprador_nombre.ilike.%{texto_seguro}%"
        )
    desde = max(0, pagina - 1) * por_pagina
    respuesta = (
        consulta.order("creado_en", desc=True).range(desde, desde + por_pagina - 1).execute()
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


def crear_marca(cliente: Client, campos: dict, creado_por: str) -> dict:
    """Alta de una marca nueva -- no es una edición, así que se aplica directo.

    No se manda ``codigo``: lo asigna la base (secuencial, M-00001, M-00002...)
    para que nadie tenga que inventarlo ni se puedan pisar dos altas a la vez.
    """
    datos = {**campos, "creado_por": creado_por}
    respuesta = cliente.table("marcas").insert(datos).execute()
    return respuesta.data[0]


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
    """Si esta fila ya tiene una modificación esperando aprobación, la trae.

    No hay ninguna restricción en la base que impida dos propuestas pendientes
    sobre la misma fila (dos Administradores podrían proponer cada uno la
    suya) -- ``.maybe_single()`` rompería en ese caso al encontrar más de una
    fila, así que se trae la más reciente con ``limit(1)`` en vez de asumir
    que siempre hay como mucho una.
    """
    filas = (
        cliente.table("cambios_pendientes")
        .select("*")
        .eq("tabla", tabla)
        .eq("fila_id", fila_id)
        .eq("estado", "pendiente")
        .order("propuesto_en", desc=True)
        .limit(1)
        .execute()
        .data
    )
    return filas[0] if filas else None


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


def desglose_marcas(cliente: Client) -> dict:
    """Cuántas marcas hay por estado y por tipo -- para el panel de estadísticas."""
    resultado = {}
    for estado in ("activa", "revisar", "baja"):
        resultado[estado] = (
            cliente.table("marcas").select("id", count="exact").eq("estado", estado).execute().count or 0
        )
    for tipo in ("dominante", "complementaria"):
        resultado[tipo] = (
            cliente.table("marcas").select("id", count="exact").eq("tipo", tipo).execute().count or 0
        )
    return resultado


def resumen_mensual(cliente: Client, meses: int = 12) -> list[dict]:
    """Guías cargadas y animales declarados por mes, según ``creado_en``.

    Se calcula sobre cuándo se cargó cada guía al sistema (dato real y
    siempre presente), no sobre el campo de texto libre ``fecha`` del
    formulario de origen -- ese llegó vacío o en formatos dispares en la
    mayoría de las guías migradas, así que no sirve para armar una serie de
    tiempo confiable. El historial migrado en bloque va a verse como un solo
    pico; la tendencia real se arma con las guías que se carguen de acá en
    adelante."""
    filas = cliente.table("operaciones").select("creado_en, cantidad_animales").execute().data
    baldes: dict[str, dict] = defaultdict(lambda: {"guias": 0, "animales": 0})
    for f in filas:
        marca_tiempo = f.get("creado_en")
        if not marca_tiempo:
            continue
        clave = marca_tiempo[:7]  # 'AAAA-MM'
        baldes[clave]["guias"] += 1
        baldes[clave]["animales"] += f.get("cantidad_animales") or 0
    claves = sorted(baldes)[-meses:]
    return [{"mes": clave, **baldes[clave]} for clave in claves]


def ranking_participantes(cliente: Client, limite: int = 8) -> tuple[list[dict], list[dict]]:
    """Los vendedores y compradores con más animales movidos, según las guías cargadas."""
    filas = cliente.table("operaciones").select("vendedor_nombre, comprador_nombre, cantidad_animales").execute().data
    vendedores: dict[str, dict] = defaultdict(lambda: {"guias": 0, "animales": 0})
    compradores: dict[str, dict] = defaultdict(lambda: {"guias": 0, "animales": 0})
    for f in filas:
        cantidad = f.get("cantidad_animales") or 0
        if f.get("vendedor_nombre"):
            v = vendedores[f["vendedor_nombre"]]
            v["guias"] += 1
            v["animales"] += cantidad
        if f.get("comprador_nombre"):
            c = compradores[f["comprador_nombre"]]
            c["guias"] += 1
            c["animales"] += cantidad
    top_vendedores = sorted(vendedores.items(), key=lambda kv: kv[1]["animales"], reverse=True)[:limite]
    top_compradores = sorted(compradores.items(), key=lambda kv: kv[1]["animales"], reverse=True)[:limite]
    return (
        [{"nombre": n, **d} for n, d in top_vendedores],
        [{"nombre": n, **d} for n, d in top_compradores],
    )


def marcas_por_vencer(cliente: Client, dias: int = 90, limite: int = 10) -> list[dict]:
    """Marcas activas con vencimiento cargado dentro de los próximos ``dias`` días
    (incluye las ya vencidas), para alertar la renovación a tiempo."""
    limite_fecha = (date.today() + timedelta(days=dias)).isoformat()
    return (
        cliente.table("marcas")
        .select("codigo, vence_en, propietarios(nombre)")
        .not_.is_("vence_en", "null")
        .lte("vence_en", limite_fecha)
        .neq("estado", "baja")
        .order("vence_en")
        .limit(limite)
        .execute()
        .data
    )


def listar_propietarios(
    cliente: Client, texto: str | None = None, pagina: int = 1, por_pagina: int = POR_PAGINA_PROPIETARIOS
) -> tuple[list[dict], int]:
    consulta = cliente.table("propietarios").select(
        "id, nombre, documento, establecimiento, localidad, departamento", count="exact"
    )
    texto = (texto or "").strip()
    if texto:
        texto_seguro = _escapar_filtro(texto)
        consulta = consulta.or_(
            f"nombre.ilike.%{texto_seguro}%,documento.ilike.%{texto_seguro}%,"
            f"establecimiento.ilike.%{texto_seguro}%"
        )
    desde = max(0, pagina - 1) * por_pagina
    respuesta = consulta.order("nombre").range(desde, desde + por_pagina - 1).execute()
    return respuesta.data, (respuesta.count or 0)


def obtener_propietario(cliente: Client, propietario_id: int) -> dict | None:
    respuesta = (
        cliente.table("propietarios").select("*").eq("id", propietario_id).maybe_single().execute()
    )
    return respuesta.data if respuesta else None


def marcas_de_propietario(cliente: Client, propietario_id: int) -> list[dict]:
    return (
        cliente.table("marcas")
        .select("codigo, tipo, estado, archivo_png, vence_en")
        .eq("propietario_id", propietario_id)
        .order("codigo")
        .execute()
        .data
    )


def operaciones_de_propietario(cliente: Client, documento: str | None) -> list[dict]:
    """Guías donde este propietario aparece como vendedor o comprador.

    Se cruza por documento (CI/RUC) porque las guías guardan vendedor y
    comprador como texto libre, no como referencia a ``propietarios`` -- no
    hay otra columna confiable para vincular ambas tablas."""
    documento = (documento or "").strip()
    if not documento:
        return []
    documento_seguro = _escapar_filtro(documento)
    return (
        cliente.table("operaciones")
        .select("id, numero_guia, fecha, vendedor_nombre, comprador_nombre, cantidad_animales, creado_en")
        .or_(f"vendedor_documento.eq.{documento_seguro},comprador_documento.eq.{documento_seguro}")
        .order("creado_en", desc=True)
        .execute()
        .data
    )


def actualizar_propietario(cliente: Client, propietario_id: int, campos: dict) -> None:
    """Los datos de contacto de un propietario se actualizan directo -- no pasan
    por modificación supervisada, esa cola es sólo para marcas y operaciones."""
    cliente.table("propietarios").update(campos).eq("id", propietario_id).execute()


def _traer_todas_las_filas(armar_consulta) -> list[dict]:
    """PostgREST devuelve como máximo 1000 filas por pedido -- para exportar
    todo lo que cumple un filtro (no sólo una página) hay que pedir en lotes.

    ``armar_consulta`` es una función que arma la consulta DE CERO en cada
    llamada (con los mismos filtros, sin ``.range()`` todavía) -- hace falta
    un builder nuevo por página porque ``.range()`` en postgrest-py no
    reemplaza el offset/límite anterior, los acumula (agrega otro par
    offset/limit al pedido en vez de pisar el que ya estaba). Reusar el mismo
    builder en el bucle hacía que la segunda página pidiera offset 0 de
    nuevo -- un bucle que nunca terminaba de traer una página más chica que
    el lote, y por lo tanto nunca cortaba."""
    filas: list[dict] = []
    inicio = 0
    while True:
        lote = armar_consulta().range(inicio, inicio + TAMANO_LOTE_EXPORTACION - 1).execute().data
        filas.extend(lote)
        if len(lote) < TAMANO_LOTE_EXPORTACION:
            return filas
        inicio += TAMANO_LOTE_EXPORTACION


def exportar_operaciones(
    cliente: Client,
    texto: str | None = None,
    estado: str | None = None,
    creada_desde: str | None = None,
    creada_hasta: str | None = None,
    tipo_operacion: str | None = None,
) -> list[dict]:
    """Todas las guías que cumplen el filtro activo (sin paginar), para el CSV."""
    texto = (texto or "").strip()

    def armar():
        consulta = cliente.table("operaciones").select(
            "numero_guia, fecha, vendedor_nombre, vendedor_documento, comprador_nombre, comprador_documento, "
            "cantidad_animales, categoria_animales, revisar, guia_colisionada, creado_en, tipo_operacion"
        )
        if tipo_operacion in ("compra", "venta"):
            consulta = consulta.eq("tipo_operacion", tipo_operacion)
        if estado == "revisar":
            consulta = consulta.not_.is_("revisar", "null").neq("revisar", "")
        elif estado == "colisiona":
            consulta = consulta.eq("guia_colisionada", True)
        elif estado == "al_dia":
            consulta = consulta.eq("guia_colisionada", False).or_("revisar.is.null,revisar.eq.")
        if creada_desde:
            consulta = consulta.gte("creado_en", creada_desde)
        if creada_hasta:
            consulta = consulta.lte("creado_en", f"{creada_hasta}T23:59:59")
        if texto:
            texto_seguro = _escapar_filtro(texto)
            consulta = consulta.or_(
                f"numero_guia.ilike.%{texto_seguro}%,vendedor_nombre.ilike.%{texto_seguro}%,"
                f"comprador_nombre.ilike.%{texto_seguro}%"
            )
        return consulta.order("creado_en", desc=True)

    return _traer_todas_las_filas(armar)


def exportar_marcas(cliente: Client, texto: str | None = None, estado: str | None = None, tipo: str | None = None) -> list[dict]:
    """Todas las marcas que cumplen el filtro activo (sin paginar), para el CSV."""
    texto = (texto or "").strip()
    propietarios_coincidentes = None
    operaciones_coincidentes = None
    if texto:
        texto_seguro = _escapar_filtro(texto)
        propietarios_coincidentes = (
            cliente.table("propietarios")
            .select("id")
            .or_(f"nombre.ilike.%{texto_seguro}%,documento.ilike.%{texto_seguro}%")
            .execute()
            .data
        )
        operaciones_coincidentes = (
            cliente.table("operaciones")
            .select("id")
            .ilike("numero_guia", f"%{texto_seguro}%")
            .execute()
            .data
        )

    def armar():
        consulta = cliente.table("marcas").select(
            "codigo, tipo, estado, vence_en, propietarios(nombre, documento), operaciones(numero_guia, fecha)"
        )
        if estado in ("activa", "revisar", "baja"):
            consulta = consulta.eq("estado", estado)
        if tipo in ("dominante", "complementaria"):
            consulta = consulta.eq("tipo", tipo)
        if texto:
            texto_seguro = _escapar_filtro(texto)
            filtro = f"codigo.ilike.%{texto_seguro}%,numero_guia.ilike.%{texto_seguro}%"
            if propietarios_coincidentes:
                lista = ",".join(str(p["id"]) for p in propietarios_coincidentes)
                filtro += f",propietario_id.in.({lista})"
            if operaciones_coincidentes:
                lista = ",".join(str(o["id"]) for o in operaciones_coincidentes)
                filtro += f",operacion_id.in.({lista})"
            consulta = consulta.or_(filtro)
        return consulta.order("codigo")

    return _traer_todas_las_filas(armar)


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


def _completar_referencias_cambios(cliente: Client, cambios: list[dict]) -> list[dict]:
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
    return _completar_referencias_cambios(cliente, cambios)


def historial_cambios_resueltos(cliente: Client, limite: int = 200) -> list[dict]:
    """La bitácora completa: cambios ya aprobados o rechazados, más recientes primero.

    Complementa a ``cambios_pendientes_detalle`` (que sólo muestra la cola en
    espera) con lo que ya se resolvió, para que quede visible quién propuso y
    quién aprobó/rechazó cada modificación pasada, no sólo la vigente."""
    nombres_ids: set[str] = set()
    cambios = (
        cliente.table("cambios_pendientes")
        .select("*")
        .neq("estado", "pendiente")
        .order("revisado_en", desc=True)
        .limit(limite)
        .execute()
        .data
    )
    cambios = _completar_referencias_cambios(cliente, cambios)
    for c in cambios:
        nombres_ids.add(c.get("propuesto_por"))
        nombres_ids.add(c.get("revisado_por"))
    nombres = nombres_usuarios(cliente, nombres_ids)
    for c in cambios:
        c["propuesto_por_nombre"] = nombres.get(c.get("propuesto_por"))
        c["revisado_por_nombre"] = nombres.get(c.get("revisado_por"))
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
