"""Consultas SQL sobre la base propia (Postgres en Railway) para las vistas
del servidor.

Reemplaza el esquema anterior basado en supabase-py (``.table().select()...``)
por SQL parametrizado directo. Cada función toma una **conexión de
psycopg2** (ver ``db.py``/``auth.conexion_actual``) en vez de un cliente de
Supabase; el cursor usa ``RealDictCursor``, así que cada fila sigue
saliendo como ``dict``, igual que antes.
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from datetime import date, datetime, timedelta

import psycopg2
from flask import url_for
from psycopg2.extras import Json

POR_PAGINA_GUIAS = 40
POR_PAGINA_MARCAS = 40
POR_PAGINA_PROPIETARIOS = 40
# Tope de filas que se muestran para elegir con checkbox en /exportar --
# suficiente para el tamaño real de este registro y evita cargar una tabla
# gigante en el navegador si alguien no filtra nada.
LIMITE_EXPORTAR = 1000

# Deben coincidir exactamente con lo que ``resolver_cambio_pendiente()``
# vuelve a chequear al aplicar un cambio aprobado (defensa en profundidad) --
# esto es sólo para construir los formularios y no ofrecer editar un campo
# que después la aprobación va a rechazar.
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

_CAMPOS_IMAGEN = ("archivo_png", "archivo_svg")


class ErrorResolverCambio(Exception):
    """Mensaje pensado para mostrarse tal cual a quien aprueba/rechaza -- nunca
    nombres de columna ni detalle interno sin filtrar."""


def _anidar_relacion(fila: dict, id_campo: str, prefijo: str, alias: str, campos: list[str]) -> None:
    """Empaqueta columnas ``{prefijo}_{campo}`` en ``fila[alias]`` como un
    dict anidado (o ``None`` si no hay relación) -- imita el formato que
    devolvía el ``select("propietarios(nombre, documento)")`` de
    supabase-py, para no tener que tocar los templates que ya esperan
    ``m.propietarios.nombre`` / ``m.operaciones.numero_guia``."""
    tiene_relacion = bool(fila.get(id_campo))
    valores = {c: fila.pop(f"{prefijo}_{c}", None) for c in campos}
    fila[alias] = valores if tiene_relacion else None


def url_imagen(marca_id: int | None, tiene_imagen: bool, extension: str = "png") -> str | None:
    """URL (dentro de la propia app) que sirve la imagen -- ya no hay que
    firmar nada, es una ruta común servida desde la base."""
    if not marca_id or not tiene_imagen:
        return None
    return url_for("imagen_marca", marca_id=marca_id, extension=extension)


def buscar_marcas(
    conexion,
    texto: str | None,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA_MARCAS,
    estado: str | None = None,
    tipo: str | None = None,
    incluir_ocultos: bool = False,
) -> tuple[list[dict], int]:
    """Los resultados de la página pedida y el total real de coincidencias.

    ``incluir_ocultos`` no mezcla ocultas con activas -- son dos vistas
    separadas (como "activos" y "papelera"): en False (default) sólo trae
    lo activo, en True sólo lo oculto, para el link "Ver ocultos" del
    Administrador."""
    texto = (texto or "").strip()
    condiciones = [f"m.activo = {'false' if incluir_ocultos else 'true'}"]
    parametros: dict = {}
    if estado in ("activa", "revisar", "baja"):
        condiciones.append("m.estado = %(estado)s")
        parametros["estado"] = estado
    if tipo in ("dominante", "complementaria"):
        condiciones.append("m.tipo = %(tipo)s")
        parametros["tipo"] = tipo
    if texto:
        parametros["patron"] = f"%{texto}%"
        condiciones.append(
            "(m.codigo ILIKE %(patron)s OR o.numero_guia ILIKE %(patron)s "
            "OR p.nombre ILIKE %(patron)s OR p.documento ILIKE %(patron)s)"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    desde = max(0, pagina - 1) * por_pagina
    origen = f"""
        FROM marcas m
        LEFT JOIN propietarios p ON p.id = m.propietario_id
        LEFT JOIN operaciones o ON o.id = m.operacion_id
        {where}
    """
    with conexion.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total {origen}", parametros)
        total = cur.fetchone()["total"]
        cur.execute(
            f"""
            SELECT m.id, m.codigo, m.tipo, m.estado, m.posicion, m.propietario_id, m.operacion_id,
                   (m.archivo_png IS NOT NULL) AS archivo_png, m.vence_en,
                   p.nombre AS propietario_nombre, p.documento AS propietario_documento,
                   o.numero_guia AS operacion_numero_guia, o.fecha AS operacion_fecha
            {origen}
            ORDER BY m.codigo
            LIMIT %(por_pagina)s OFFSET %(desde)s
            """,
            {**parametros, "por_pagina": por_pagina, "desde": desde},
        )
        filas = cur.fetchall()
    for f in filas:
        _anidar_relacion(f, "propietario_id", "propietario", "propietarios", ["nombre", "documento"])
        _anidar_relacion(f, "operacion_id", "operacion", "operaciones", ["numero_guia", "fecha"])
    return filas, total


def listar_operaciones_paginado(
    conexion,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA_GUIAS,
    texto: str | None = None,
    estado: str | None = None,
    creada_desde: str | None = None,
    creada_hasta: str | None = None,
    tipo_operacion: str | None = None,
    incluir_ocultos: bool = False,
) -> tuple[list[dict], int]:
    """``estado`` filtra por lo que ya se muestra como estado en el listado:
    'revisar' (tiene notas de revisión), 'colisiona' (guía colisionada) o
    'al_dia' (ninguna de las dos). ``creada_desde``/``creada_hasta`` son
    fechas ISO (AAAA-MM-DD) sobre ``creado_en`` -- la fecha real y confiable
    de carga en el sistema, no el campo de texto libre ``fecha`` del
    formulario de origen, que en más de las tres cuartas partes de las guías
    migradas llegó vacío o en formatos dispares. ``tipo_operacion`` filtra
    'compra' o 'venta'. ``incluir_ocultos`` (ver ``buscar_marcas``): vista
    separada, no mezclada."""
    condiciones = [f"activo = {'false' if incluir_ocultos else 'true'}"]
    parametros: dict = {}
    if tipo_operacion in ("compra", "venta"):
        condiciones.append("tipo_operacion = %(tipo_operacion)s")
        parametros["tipo_operacion"] = tipo_operacion
    if estado == "revisar":
        condiciones.append("(revisar IS NOT NULL AND revisar != '')")
    elif estado == "colisiona":
        condiciones.append("guia_colisionada = true")
    elif estado == "al_dia":
        condiciones.append("guia_colisionada = false AND (revisar IS NULL OR revisar = '')")
    if creada_desde:
        condiciones.append("creado_en >= %(creada_desde)s")
        parametros["creada_desde"] = creada_desde
    if creada_hasta:
        condiciones.append("creado_en <= %(creada_hasta)s")
        parametros["creada_hasta"] = f"{creada_hasta}T23:59:59"
    texto = (texto or "").strip()
    if texto:
        parametros["patron"] = f"%{texto}%"
        condiciones.append(
            "(numero_guia ILIKE %(patron)s OR vendedor_nombre ILIKE %(patron)s "
            "OR comprador_nombre ILIKE %(patron)s)"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    desde = max(0, pagina - 1) * por_pagina
    with conexion.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM operaciones {where}", parametros)
        total = cur.fetchone()["total"]
        cur.execute(
            f"""
            SELECT id, numero_guia, fecha, vendedor_nombre, comprador_nombre,
                   cantidad_animales, categoria_animales, revisar, guia_colisionada, creado_en, tipo_operacion
            FROM operaciones
            {where}
            ORDER BY creado_en DESC
            LIMIT %(por_pagina)s OFFSET %(desde)s
            """,
            {**parametros, "por_pagina": por_pagina, "desde": desde},
        )
        filas = cur.fetchall()
    return filas, total


def obtener_operacion_por_id(conexion, operacion_id: int) -> dict | None:
    with conexion.cursor() as cur:
        cur.execute("SELECT * FROM operaciones WHERE id = %s", (operacion_id,))
        return cur.fetchone()


def marcas_de_operacion(conexion, operacion_id: int) -> list[dict]:
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT id, codigo, tipo, posicion, (archivo_png IS NOT NULL) AS archivo_png, estado
            FROM marcas
            WHERE operacion_id = %s AND activo = true
            ORDER BY tipo, posicion
            """,
            (operacion_id,),
        )
        return cur.fetchall()


def obtener_marca_por_id(conexion, marca_id: int) -> dict | None:
    """Como ``ficha_marca`` pero sin el join a propietario/operación -- para
    cuando sólo hace falta la fila para comparar ediciones (no arrastra el
    contenido de las imágenes: son bytea, pesan)."""
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT id, codigo, descripcion, propietario_id, operacion_id, tipo, posicion, numero_guia,
                   origen_archivo, (archivo_png IS NOT NULL) AS archivo_png,
                   (archivo_svg IS NOT NULL) AS archivo_svg, borde_limpiado, sospechosa_calidad,
                   motivo_calidad, estado, observaciones, creado_por, actualizado_por, creado_en,
                   actualizado_en, vence_en
            FROM marcas
            WHERE id = %s
            """,
            (marca_id,),
        )
        return cur.fetchone()


def marcas_por_codigos(conexion, codigos: list[str]) -> dict[str, dict]:
    """Para una Venta: las marcas elegidas no pertenecen a esta operación
    (son del catálogo existente, se transfieren), así que se buscan por
    código en vez de por operacion_id. Devuelve un dict código -> marca
    para que el llamador pueda armar la lista en el orden que el usuario
    eligió."""
    if not codigos:
        return {}
    codigos_unicos = list(dict.fromkeys(codigos))
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.codigo, m.tipo, m.estado, m.propietario_id,
                   (m.archivo_png IS NOT NULL) AS archivo_png,
                   p.nombre AS propietario_nombre
            FROM marcas m
            LEFT JOIN propietarios p ON p.id = m.propietario_id
            WHERE m.codigo = ANY(%s)
            """,
            (codigos_unicos,),
        )
        filas = cur.fetchall()
    for f in filas:
        _anidar_relacion(f, "propietario_id", "propietario", "propietarios", ["nombre"])
    return {f["codigo"]: f for f in filas}


def crear_operacion(conexion, campos: dict, creado_por: str) -> int:
    """Alta de una guía nueva -- no es una edición, así que se aplica directo."""
    datos = {**campos, "origen": "manual", "creado_por": creado_por}
    columnas = list(datos.keys())
    with conexion.cursor() as cur:
        cur.execute(
            f"INSERT INTO operaciones ({', '.join(columnas)}) "
            f"VALUES ({', '.join('%(' + c + ')s' for c in columnas)}) RETURNING id",
            datos,
        )
        return cur.fetchone()["id"]


def crear_marca(conexion, campos: dict, creado_por: str) -> dict:
    """Alta de una marca nueva -- no es una edición, así que se aplica directo.

    No se manda ``codigo``: lo asigna la base (secuencial, M-00001, M-00002...)
    para que nadie tenga que inventarlo ni se puedan pisar dos altas a la vez.
    """
    datos = {**campos, "creado_por": creado_por}
    columnas = list(datos.keys())
    with conexion.cursor() as cur:
        cur.execute(
            f"INSERT INTO marcas ({', '.join(columnas)}) "
            f"VALUES ({', '.join('%(' + c + ')s' for c in columnas)}) RETURNING id, codigo",
            datos,
        )
        return cur.fetchone()


def subir_imagen_marca(conexion, marca_id: int, contenido: bytes, extension: str) -> None:
    """Aplica una imagen directo sobre la marca (edición de un Operador, o
    alta de una nueva marca complementaria) -- no pasa por aprobación."""
    if extension not in ("png", "svg"):
        raise ValueError(f"extensión de imagen inválida: {extension!r}")
    with conexion.cursor() as cur:
        cur.execute(
            f"UPDATE marcas SET archivo_{extension} = %s WHERE id = %s",
            (psycopg2.Binary(contenido), marca_id),
        )


def imagen_marca(conexion, marca_id: int, extension: str) -> bytes | None:
    """El contenido binario de la imagen -- para servirla por HTTP o
    incrustarla en un PDF."""
    if extension not in ("png", "svg"):
        raise ValueError(f"extensión de imagen inválida: {extension!r}")
    with conexion.cursor() as cur:
        cur.execute(f"SELECT archivo_{extension} AS contenido FROM marcas WHERE id = %s", (marca_id,))
        fila = cur.fetchone()
    contenido = fila["contenido"] if fila else None
    return bytes(contenido) if contenido is not None else None


def actualizar_marca_campos(conexion, marca_id: int, campos: dict) -> None:
    """Aplica una edición directo (Operador) -- las claves de ``campos``
    siempre vienen filtradas contra ``CAMPOS_MARCA_EDITABLES`` antes de
    llegar acá."""
    if not campos:
        return
    columnas = ", ".join(f"{c} = %({c})s" for c in campos)
    with conexion.cursor() as cur:
        cur.execute(f"UPDATE marcas SET {columnas} WHERE id = %(_id)s", {**campos, "_id": marca_id})


def actualizar_operacion_campos(conexion, operacion_id: int, campos: dict) -> None:
    if not campos:
        return
    columnas = ", ".join(f"{c} = %({c})s" for c in campos)
    with conexion.cursor() as cur:
        cur.execute(
            f"UPDATE operaciones SET {columnas} WHERE id = %(_id)s", {**campos, "_id": operacion_id}
        )


def ocultar_marca(conexion, marca_id: int) -> None:
    """Ocultar/restaurar es acción directa de Administrador -- a diferencia
    de editar, no pasa por la cola de Aprobaciones (nunca borra la fila:
    son documentos con valor legal/SENACSA, siempre reversible)."""
    with conexion.cursor() as cur:
        cur.execute("UPDATE marcas SET activo = false WHERE id = %s", (marca_id,))


def restaurar_marca(conexion, marca_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE marcas SET activo = true WHERE id = %s", (marca_id,))


def ocultar_operacion(conexion, operacion_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE operaciones SET activo = false WHERE id = %s", (operacion_id,))


def restaurar_operacion(conexion, operacion_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE operaciones SET activo = true WHERE id = %s", (operacion_id,))


def proponer_cambio(
    conexion, tabla: str, fila_id: int, cambios: dict, valores_anteriores: dict, propuesto_por: str
) -> None:
    with conexion.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cambios_pendientes (tabla, fila_id, cambios, valores_anteriores, propuesto_por, estado)
            VALUES (%s, %s, %s, %s, %s, 'pendiente')
            """,
            (tabla, fila_id, Json(cambios), Json(valores_anteriores), propuesto_por),
        )


def resolver_cambio_pendiente(
    conexion, cambio_id: int, decision: str, revisado_por: str, motivo: str | None = None
) -> None:
    """Aprueba o rechaza un cambio en cola -- la regla de fondo (quien
    propone no puede aprobar) vive acá, en código versionado, en vez de en
    una función guardada sólo dentro de la base."""
    if decision not in ("aprobado", "rechazado"):
        raise ErrorResolverCambio("Decisión inválida.")
    with conexion.cursor() as cur:
        cur.execute("SELECT * FROM cambios_pendientes WHERE id = %s FOR UPDATE", (cambio_id,))
        cambio = cur.fetchone()
        if not cambio:
            raise ErrorResolverCambio("Ese cambio ya no existe.")
        if cambio["estado"] != "pendiente":
            raise ErrorResolverCambio("Ese cambio ya fue resuelto.")
        if str(cambio["propuesto_por"]) == str(revisado_por):
            raise ErrorResolverCambio("Quien propone el cambio no puede aprobarlo.")
        if cambio["tabla"] not in ("marcas", "operaciones"):
            raise ErrorResolverCambio("No se pudo resolver el cambio.")

        if decision == "aprobado":
            permitidos = CAMPOS_MARCA_EDITABLES if cambio["tabla"] == "marcas" else CAMPOS_OPERACION_EDITABLES
            cambios_aplicar = {}
            for campo, valor in cambio["cambios"].items():
                if campo not in permitidos:
                    continue  # defensa en profundidad: nunca aplicar una columna fuera de la lista blanca
                if campo in _CAMPOS_IMAGEN and valor is not None:
                    valor = psycopg2.Binary(base64.b64decode(valor))
                cambios_aplicar[campo] = valor
            if cambios_aplicar:
                columnas = ", ".join(f"{c} = %({c})s" for c in cambios_aplicar)
                cur.execute(
                    f"UPDATE {cambio['tabla']} SET {columnas} WHERE id = %(_id)s",
                    {**cambios_aplicar, "_id": cambio["fila_id"]},
                )

        cur.execute(
            """
            UPDATE cambios_pendientes
            SET estado = %s, revisado_por = %s, revisado_en = now(), motivo_rechazo = %s
            WHERE id = %s
            """,
            (decision, revisado_por, motivo, cambio_id),
        )


def nombres_usuarios(conexion, ids) -> dict[str, str]:
    """Resuelve id de usuario -> nombre, para mostrar quién cargó o tocó un registro."""
    ids_validos = list({i for i in ids if i})
    if not ids_validos:
        return {}
    with conexion.cursor() as cur:
        # el cast explícito hace falta porque el array llega como texto --
        # sin él, Postgres no sabe compararlo contra la columna uuid.
        cur.execute("SELECT id, nombre FROM usuarios WHERE id = ANY(%s::uuid[])", (ids_validos,))
        return {str(f["id"]): f["nombre"] for f in cur.fetchall()}


def cambio_pendiente_de(conexion, tabla: str, fila_id: int) -> dict | None:
    """Si esta fila ya tiene una modificación esperando aprobación, la trae.

    No hay ninguna restricción que impida dos propuestas pendientes sobre la
    misma fila (dos Administradores podrían proponer cada uno la suya) --
    se trae la más reciente con ``LIMIT 1`` en vez de asumir que siempre hay
    como mucho una.
    """
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM cambios_pendientes
            WHERE tabla = %s AND fila_id = %s AND estado = 'pendiente'
            ORDER BY propuesto_en DESC
            LIMIT 1
            """,
            (tabla, fila_id),
        )
        return cur.fetchone()


def guardar_borrador_venta(
    conexion,
    operacion_id: int,
    marcas: list[str],
    guardado_en: str,
    pdf_bytes: bytes | None = None,
    pdf_nombre: str | None = None,
    dominante_bytes: bytes | None = None,
) -> None:
    """Guarda el avance de la carga de una Venta. Un archivo que no se
    reenvía en este llamado se deja como estaba (``COALESCE``) -- así
    guardar de nuevo sólo las marcas elegidas no borra un PDF ya subido
    antes."""
    with conexion.cursor() as cur:
        cur.execute(
            """
            UPDATE operaciones SET
                borrador_venta = %(borrador)s,
                borrador_pdf = COALESCE(%(pdf_bytes)s, borrador_pdf),
                borrador_pdf_nombre = COALESCE(%(pdf_nombre)s, borrador_pdf_nombre),
                borrador_dominante_png = COALESCE(%(dominante_bytes)s, borrador_dominante_png)
            WHERE id = %(operacion_id)s
            """,
            {
                "borrador": Json({"marcas": marcas, "guardado_en": guardado_en}),
                "pdf_bytes": psycopg2.Binary(pdf_bytes) if pdf_bytes else None,
                "pdf_nombre": pdf_nombre,
                "dominante_bytes": psycopg2.Binary(dominante_bytes) if dominante_bytes else None,
                "operacion_id": operacion_id,
            },
        )


def eliminar_borrador_venta(conexion, operacion_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute(
            """
            UPDATE operaciones
            SET borrador_venta = NULL, borrador_pdf = NULL, borrador_pdf_nombre = NULL,
                borrador_dominante_png = NULL
            WHERE id = %s
            """,
            (operacion_id,),
        )


def borrador_venta_pdf(conexion, operacion_id: int) -> tuple[bytes, str] | None:
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT borrador_pdf, borrador_pdf_nombre FROM operaciones WHERE id = %s", (operacion_id,)
        )
        fila = cur.fetchone()
    if not fila or fila["borrador_pdf"] is None:
        return None
    return bytes(fila["borrador_pdf"]), (fila["borrador_pdf_nombre"] or "borrador.pdf")


def borrador_venta_dominante(conexion, operacion_id: int) -> bytes | None:
    with conexion.cursor() as cur:
        cur.execute("SELECT borrador_dominante_png FROM operaciones WHERE id = %s", (operacion_id,))
        fila = cur.fetchone()
    if not fila or fila["borrador_dominante_png"] is None:
        return None
    return bytes(fila["borrador_dominante_png"])


def ficha_marca(conexion, codigo: str) -> dict | None:
    """La marca, su formulario de origen y las marcas que la acompañan."""
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.codigo, m.descripcion, m.propietario_id, m.operacion_id, m.tipo, m.posicion,
                   m.numero_guia, m.origen_archivo, (m.archivo_png IS NOT NULL) AS archivo_png,
                   (m.archivo_svg IS NOT NULL) AS archivo_svg, m.borde_limpiado, m.sospechosa_calidad,
                   m.motivo_calidad, m.estado, m.observaciones, m.creado_por, m.actualizado_por,
                   m.creado_en, m.actualizado_en, m.vence_en, m.activo,
                   p.nombre AS propietario_nombre, p.documento AS propietario_documento,
                   p.establecimiento AS propietario_establecimiento
            FROM marcas m
            LEFT JOIN propietarios p ON p.id = m.propietario_id
            WHERE m.codigo = %s
            """,
            (codigo,),
        )
        marca = cur.fetchone()
        if not marca:
            return None
        _anidar_relacion(
            marca, "propietario_id", "propietario", "propietarios", ["nombre", "documento", "establecimiento"]
        )

        operacion = None
        acompanantes: list[dict] = []
        if marca.get("operacion_id"):
            cur.execute("SELECT * FROM operaciones WHERE id = %s", (marca["operacion_id"],))
            operacion = cur.fetchone()
            cur.execute(
                """
                SELECT codigo, tipo, posicion, (archivo_png IS NOT NULL) AS archivo_png,
                       (archivo_svg IS NOT NULL) AS archivo_svg, estado
                FROM marcas
                WHERE operacion_id = %s
                ORDER BY tipo, posicion
                """,
                (marca["operacion_id"],),
            )
            acompanantes = [h for h in cur.fetchall() if h["codigo"] != codigo]

    return {"marca": marca, "operacion": operacion, "acompanantes": acompanantes}


def estadisticas(conexion) -> dict:
    with conexion.cursor() as cur:
        cur.execute("SELECT count(*) AS total FROM marcas")
        total = cur.fetchone()["total"]
        cur.execute("SELECT count(*) AS total FROM marcas WHERE estado = 'revisar'")
        a_revisar = cur.fetchone()["total"]
        cur.execute("SELECT count(*) AS total FROM cambios_pendientes WHERE estado = 'pendiente'")
        pendientes = cur.fetchone()["total"]
    return {"total": total, "a_revisar": a_revisar, "pendientes": pendientes}


def desglose_marcas(conexion) -> dict:
    """Cuántas marcas hay por estado y por tipo -- para el panel de estadísticas."""
    resultado = {}
    with conexion.cursor() as cur:
        for estado in ("activa", "revisar", "baja"):
            cur.execute("SELECT count(*) AS total FROM marcas WHERE estado = %s", (estado,))
            resultado[estado] = cur.fetchone()["total"]
        for tipo in ("dominante", "complementaria"):
            cur.execute("SELECT count(*) AS total FROM marcas WHERE tipo = %s", (tipo,))
            resultado[tipo] = cur.fetchone()["total"]
    return resultado


def resumen_mensual(conexion, meses: int = 12) -> list[dict]:
    """Guías cargadas y animales declarados por mes, según ``creado_en``.

    Se calcula sobre cuándo se cargó cada guía al sistema (dato real y
    siempre presente), no sobre el campo de texto libre ``fecha`` del
    formulario de origen -- ese llegó vacío o en formatos dispares en la
    mayoría de las guías migradas, así que no sirve para armar una serie de
    tiempo confiable."""
    with conexion.cursor() as cur:
        cur.execute("SELECT creado_en, cantidad_animales FROM operaciones")
        filas = cur.fetchall()
    baldes: dict[str, dict] = defaultdict(lambda: {"guias": 0, "animales": 0})
    for f in filas:
        marca_tiempo = f.get("creado_en")
        if not marca_tiempo:
            continue
        clave = marca_tiempo.isoformat()[:7]  # 'AAAA-MM'
        baldes[clave]["guias"] += 1
        baldes[clave]["animales"] += f.get("cantidad_animales") or 0
    claves = sorted(baldes)[-meses:]
    return [{"mes": clave, **baldes[clave]} for clave in claves]


def ranking_participantes(conexion, limite: int = 8) -> tuple[list[dict], list[dict]]:
    """Los vendedores y compradores con más animales movidos, según las guías cargadas."""
    with conexion.cursor() as cur:
        cur.execute("SELECT vendedor_nombre, comprador_nombre, cantidad_animales FROM operaciones")
        filas = cur.fetchall()
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


def marcas_por_vencer(conexion, dias: int = 90, limite: int = 10) -> list[dict]:
    """Marcas activas con vencimiento cargado dentro de los próximos ``dias`` días
    (incluye las ya vencidas), para alertar la renovación a tiempo."""
    limite_fecha = date.today() + timedelta(days=dias)
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT m.codigo, m.vence_en, m.propietario_id, p.nombre AS propietario_nombre
            FROM marcas m
            LEFT JOIN propietarios p ON p.id = m.propietario_id
            WHERE m.vence_en IS NOT NULL AND m.vence_en <= %s AND m.estado != 'baja'
            ORDER BY m.vence_en
            LIMIT %s
            """,
            (limite_fecha, limite),
        )
        filas = cur.fetchall()
    for f in filas:
        _anidar_relacion(f, "propietario_id", "propietario", "propietarios", ["nombre"])
    return filas


def listar_propietarios(
    conexion,
    texto: str | None = None,
    pagina: int = 1,
    por_pagina: int = POR_PAGINA_PROPIETARIOS,
    incluir_ocultos: bool = False,
) -> tuple[list[dict], int]:
    condiciones = [f"activo = {'false' if incluir_ocultos else 'true'}"]
    parametros: dict = {}
    texto = (texto or "").strip()
    if texto:
        parametros["patron"] = f"%{texto}%"
        condiciones.append(
            "(nombre ILIKE %(patron)s OR documento ILIKE %(patron)s OR establecimiento ILIKE %(patron)s)"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    desde = max(0, pagina - 1) * por_pagina
    with conexion.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM propietarios {where}", parametros)
        total = cur.fetchone()["total"]
        cur.execute(
            f"""
            SELECT id, nombre, documento, establecimiento, localidad, departamento
            FROM propietarios
            {where}
            ORDER BY nombre
            LIMIT %(por_pagina)s OFFSET %(desde)s
            """,
            {**parametros, "por_pagina": por_pagina, "desde": desde},
        )
        filas = cur.fetchall()
    return filas, total


def obtener_propietario(conexion, propietario_id: int) -> dict | None:
    with conexion.cursor() as cur:
        cur.execute("SELECT * FROM propietarios WHERE id = %s", (propietario_id,))
        return cur.fetchone()


def marcas_de_propietario(conexion, propietario_id: int) -> list[dict]:
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT id, codigo, tipo, estado, (archivo_png IS NOT NULL) AS archivo_png, vence_en
            FROM marcas
            WHERE propietario_id = %s AND activo = true
            ORDER BY codigo
            """,
            (propietario_id,),
        )
        return cur.fetchall()


def operaciones_de_propietario(conexion, documento: str | None) -> list[dict]:
    """Guías donde este propietario aparece como vendedor o comprador.

    Se cruza por documento (CI/RUC) porque las guías guardan vendedor y
    comprador como texto libre, no como referencia a ``propietarios`` -- no
    hay otra columna confiable para vincular ambas tablas."""
    documento = (documento or "").strip()
    if not documento:
        return []
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT id, numero_guia, fecha, vendedor_nombre, comprador_nombre, cantidad_animales, creado_en
            FROM operaciones
            WHERE (vendedor_documento = %(doc)s OR comprador_documento = %(doc)s) AND activo = true
            ORDER BY creado_en DESC
            """,
            {"doc": documento},
        )
        return cur.fetchall()


def actualizar_propietario(conexion, propietario_id: int, campos: dict) -> None:
    """Los datos de contacto de un propietario se actualizan directo -- no pasan
    por modificación supervisada, esa cola es sólo para marcas y operaciones."""
    if not campos:
        return
    columnas = ", ".join(f"{c} = %({c})s" for c in campos)
    with conexion.cursor() as cur:
        cur.execute(
            f"UPDATE propietarios SET {columnas} WHERE id = %(_id)s", {**campos, "_id": propietario_id}
        )


def ocultar_propietario(conexion, propietario_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE propietarios SET activo = false WHERE id = %s", (propietario_id,))


def restaurar_propietario(conexion, propietario_id: int) -> None:
    with conexion.cursor() as cur:
        cur.execute("UPDATE propietarios SET activo = true WHERE id = %s", (propietario_id,))


def exportar_operaciones(
    conexion,
    texto: str | None = None,
    estado: str | None = None,
    creada_desde: str | None = None,
    creada_hasta: str | None = None,
    tipo_operacion: str | None = None,
    ids: list[int] | None = None,
    incluir_ocultos: bool = False,
) -> list[dict]:
    """Todas las guías/ventas que cumplen el filtro activo (sin paginar), para
    la planilla. ``ids``, si se manda, restringe a exactamente esos
    registros -- es lo que arma el paso de "elegir los ítems" en /exportar."""
    condiciones = [f"activo = {'false' if incluir_ocultos else 'true'}"]
    parametros: dict = {}
    if ids is not None:
        condiciones.append("id = ANY(%(ids)s::bigint[])")
        parametros["ids"] = ids
    if tipo_operacion in ("compra", "venta"):
        condiciones.append("tipo_operacion = %(tipo_operacion)s")
        parametros["tipo_operacion"] = tipo_operacion
    if estado == "revisar":
        condiciones.append("(revisar IS NOT NULL AND revisar != '')")
    elif estado == "colisiona":
        condiciones.append("guia_colisionada = true")
    elif estado == "al_dia":
        condiciones.append("guia_colisionada = false AND (revisar IS NULL OR revisar = '')")
    if creada_desde:
        condiciones.append("creado_en >= %(creada_desde)s")
        parametros["creada_desde"] = creada_desde
    if creada_hasta:
        condiciones.append("creado_en <= %(creada_hasta)s")
        parametros["creada_hasta"] = f"{creada_hasta}T23:59:59"
    texto = (texto or "").strip()
    if texto:
        parametros["patron"] = f"%{texto}%"
        condiciones.append(
            "(numero_guia ILIKE %(patron)s OR vendedor_nombre ILIKE %(patron)s "
            "OR comprador_nombre ILIKE %(patron)s)"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with conexion.cursor() as cur:
        cur.execute(
            f"""
            SELECT numero_guia, fecha, vendedor_nombre, vendedor_documento, comprador_nombre, comprador_documento,
                   cantidad_animales, categoria_animales, revisar, guia_colisionada, creado_en, tipo_operacion
            FROM operaciones
            {where}
            ORDER BY creado_en DESC
            """,
            parametros,
        )
        return cur.fetchall()


def exportar_marcas(
    conexion,
    texto: str | None = None,
    estado: str | None = None,
    tipo: str | None = None,
    ids: list[int] | None = None,
    incluir_ocultos: bool = False,
) -> list[dict]:
    """Todas las marcas que cumplen el filtro activo (sin paginar), para la
    planilla. ``ids``, si se manda, restringe a exactamente esos registros."""
    condiciones = [f"m.activo = {'false' if incluir_ocultos else 'true'}"]
    parametros: dict = {}
    if ids is not None:
        condiciones.append("m.id = ANY(%(ids)s::bigint[])")
        parametros["ids"] = ids
    if estado in ("activa", "revisar", "baja"):
        condiciones.append("m.estado = %(estado)s")
        parametros["estado"] = estado
    if tipo in ("dominante", "complementaria"):
        condiciones.append("m.tipo = %(tipo)s")
        parametros["tipo"] = tipo
    texto = (texto or "").strip()
    if texto:
        parametros["patron"] = f"%{texto}%"
        condiciones.append(
            "(m.codigo ILIKE %(patron)s OR o.numero_guia ILIKE %(patron)s "
            "OR p.nombre ILIKE %(patron)s OR p.documento ILIKE %(patron)s)"
        )
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""
    with conexion.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.codigo, m.tipo, m.estado, m.vence_en, m.propietario_id, m.operacion_id,
                   p.nombre AS propietario_nombre, p.documento AS propietario_documento,
                   o.numero_guia AS operacion_numero_guia, o.fecha AS operacion_fecha
            FROM marcas m
            LEFT JOIN propietarios p ON p.id = m.propietario_id
            LEFT JOIN operaciones o ON o.id = m.operacion_id
            {where}
            ORDER BY m.codigo
            """,
            parametros,
        )
        filas = cur.fetchall()
    for f in filas:
        _anidar_relacion(f, "propietario_id", "propietario", "propietarios", ["nombre", "documento"])
        _anidar_relacion(f, "operacion_id", "operacion", "operaciones", ["numero_guia", "fecha"])
        del f["propietario_id"], f["operacion_id"]
    return filas


def ultimos_asientos(conexion, limite: int = 6) -> list[dict]:
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT id, numero_guia, fecha, vendedor_nombre, creado_en
            FROM operaciones
            ORDER BY creado_en DESC
            LIMIT %s
            """,
            (limite,),
        )
        return cur.fetchall()


def marcas_a_revisar(conexion, limite: int = 6) -> list[dict]:
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT codigo, motivo_calidad
            FROM marcas
            WHERE estado = 'revisar'
            ORDER BY actualizado_en DESC
            LIMIT %s
            """,
            (limite,),
        )
        return cur.fetchall()


def _completar_referencias_cambios(conexion, cambios: list[dict]) -> list[dict]:
    with conexion.cursor() as cur:
        for c in cambios:
            c["referencia"] = c["fila_id"]
            if c["tabla"] == "marcas":
                cur.execute("SELECT codigo FROM marcas WHERE id = %s", (c["fila_id"],))
                fila = cur.fetchone()
                if fila:
                    c["referencia"] = fila["codigo"]
            elif c["tabla"] == "operaciones":
                cur.execute("SELECT numero_guia FROM operaciones WHERE id = %s", (c["fila_id"],))
                fila = cur.fetchone()
                if fila:
                    c["referencia"] = fila["numero_guia"] or c["fila_id"]
    return cambios


def cambios_pendientes_detalle(conexion) -> list[dict]:
    """Los cambios en cola de aprobación, con el código de la marca/operación."""
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT * FROM cambios_pendientes WHERE estado = 'pendiente' ORDER BY propuesto_en"
        )
        cambios = cur.fetchall()
    return _completar_referencias_cambios(conexion, cambios)


def historial_cambios_resueltos(conexion, limite: int = 200) -> list[dict]:
    """La bitácora completa: cambios ya aprobados o rechazados, más recientes primero."""
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM cambios_pendientes
            WHERE estado != 'pendiente'
            ORDER BY revisado_en DESC
            LIMIT %s
            """,
            (limite,),
        )
        cambios = cur.fetchall()
    cambios = _completar_referencias_cambios(conexion, cambios)
    nombres_ids: set[str] = set()
    for c in cambios:
        nombres_ids.add(c.get("propuesto_por"))
        nombres_ids.add(c.get("revisado_por"))
    nombres = nombres_usuarios(conexion, nombres_ids)
    for c in cambios:
        c["propuesto_por_nombre"] = nombres.get(str(c.get("propuesto_por")))
        c["revisado_por_nombre"] = nombres.get(str(c.get("revisado_por")))
    return cambios


def listar_usuarios(conexion) -> list[dict]:
    with conexion.cursor() as cur:
        cur.execute("SELECT id, email, nombre, rol, activo, creado_en FROM usuarios ORDER BY creado_en")
        return cur.fetchall()


def obtener_usuario_por_email(conexion, email: str) -> dict | None:
    with conexion.cursor() as cur:
        cur.execute("SELECT * FROM usuarios WHERE email = %s", (email,))
        return cur.fetchone()


def obtener_usuario_por_id(conexion, usuario_id: str) -> dict | None:
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT id, email, nombre, rol, activo, creado_en FROM usuarios WHERE id = %s", (usuario_id,)
        )
        return cur.fetchone()


def crear_usuario(conexion, email: str, password_hash: str, nombre: str, rol: str, activo: bool) -> str:
    with conexion.cursor() as cur:
        cur.execute(
            """
            INSERT INTO usuarios (email, password_hash, nombre, rol, activo)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (email, password_hash, nombre, rol, activo),
        )
        return cur.fetchone()["id"]


def actualizar_usuario_campos(conexion, usuario_id: str, campos: dict) -> None:
    if not campos:
        return
    columnas = ", ".join(f"{c} = %({c})s" for c in campos)
    with conexion.cursor() as cur:
        cur.execute(f"UPDATE usuarios SET {columnas} WHERE id = %(_id)s", {**campos, "_id": usuario_id})


_CAMPOS_BYTEA_MARCAS = ("archivo_png", "archivo_svg")
_CAMPOS_BYTEA_OPERACIONES = ("borrador_pdf", "borrador_dominante_png")


def exportar_todo_para_respaldo(conexion) -> dict:
    """Todo el contenido de la base, listo para guardar como respaldo.

    Incluye ``password_hash`` de cada usuario -- sin eso, un respaldo sirve
    para mirar datos viejos pero no para de verdad recuperar el sistema
    (nadie podría loguearse). Las columnas ``bytea`` (imágenes de marca,
    PDF/Dominante de un borrador de venta) se codifican en base64 para
    poder ir en JSON.
    """
    with conexion.cursor() as cur:
        cur.execute("SELECT * FROM usuarios ORDER BY creado_en")
        usuarios = cur.fetchall()
        cur.execute("SELECT * FROM propietarios ORDER BY id")
        propietarios = cur.fetchall()
        cur.execute("SELECT * FROM operaciones ORDER BY id")
        operaciones = cur.fetchall()
        cur.execute("SELECT * FROM marcas ORDER BY id")
        marcas = cur.fetchall()
        cur.execute("SELECT * FROM cambios_pendientes ORDER BY id")
        cambios_pendientes = cur.fetchall()

    for m in marcas:
        for campo in _CAMPOS_BYTEA_MARCAS:
            if m.get(campo) is not None:
                m[campo] = base64.b64encode(bytes(m[campo])).decode("ascii")
    for o in operaciones:
        for campo in _CAMPOS_BYTEA_OPERACIONES:
            if o.get(campo) is not None:
                o[campo] = base64.b64encode(bytes(o[campo])).decode("ascii")

    return {
        "usuarios": usuarios,
        "propietarios": propietarios,
        "operaciones": operaciones,
        "marcas": marcas,
        "cambios_pendientes": cambios_pendientes,
    }


def _json_default(valor):
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    raise TypeError(f"No se puede convertir a JSON: {type(valor)!r}")


def dict_a_json(datos) -> str:
    """``json.dumps`` con las fechas de Postgres ya resueltas -- lo usan
    tanto la descarga manual del Panel como el script de respaldo a GitHub."""
    return json.dumps(datos, ensure_ascii=False, indent=2, default=_json_default)
