"""Consultas de las guías escaneadas: cada página escaneada (``escaneos``) con los
datos leídos por OCR, agrupadas por N.º de guía, y las marcas que salieron de ellas."""

from __future__ import annotations


POR_PAGINA_GUIAS = 24
POR_PAGINA_SIN_NUMERO = 36

FORMATOS = {
    "GE2_PRINCIPAL": "Guía GE2",
    "GE2_ANEXO": "Anexo",
    "SENACSA_CERTIFICADO": "Certificado sanitario",
    "CIEFA_GO2": "Declaración jurada CIEFA",
    "ACTA_INTERVENCION_COLCAT": "Acta COLCAT",
    "NOTA_REMISION": "Nota de remisión",
    "LOGISTICA_SIN_MARCAS": "Transporte",
    "DESCONOCIDO": "Sin clasificar",
}

_CAMPOS_EDITABLES = (
    "numero_guia", "propietario", "propietario_documento", "establecimiento",
    "cantidad_animales", "notas", "revisado",
)

_COLUMNAS_PAGINA = (
    "id, carpeta, archivo, orden, formato, numero_guia, fuente_numero, confianza_numero, numero_certificado, "
    "propietario, propietario_documento, establecimiento, cantidad_animales, fecha_texto, n_marcas, "
    "revisado, notas"
)


def _filtro_texto(texto: str | None, parametros: dict) -> str:
    """Condición sobre la tabla ``e`` (escaneos) para una búsqueda libre."""
    if not texto:
        return ""
    parametros["patron"] = f"%{texto.strip()}%"
    return (
        " AND (e.numero_guia ILIKE %(patron)s OR e.propietario ILIKE %(patron)s OR e.propietario_documento ILIKE %(patron)s"
        " OR e.numero_certificado ILIKE %(patron)s OR e.texto_ocr ILIKE %(patron)s)"
    )


def resumen_escaneos(conexion) -> dict:
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT count(*) AS paginas,
                   count(DISTINCT numero_guia) AS guias,
                   count(*) FILTER (WHERE numero_guia IS NULL) AS sin_numero,
                   count(*) FILTER (WHERE revisado) AS revisadas
            FROM escaneos
            """
        )
        return cur.fetchone()


def listar_guias_escaneadas(
    conexion, texto: str | None = None, formato: str | None = None, pagina: int = 1
) -> tuple[list[dict], int]:
    """Una fila por N.º de guía: propietario, formatos de documento que la componen, páginas y marcas."""
    parametros: dict = {}
    filtro = _filtro_texto(texto, parametros)
    if formato in FORMATOS:
        filtro += " AND e.formato = %(formato)s"
        parametros["formato"] = formato
    # Si algún documento de la guía coincide con la búsqueda, se muestra la guía entera.
    elegidas = f"""
        e.numero_guia IS NOT NULL AND e.numero_guia IN (
            SELECT e.numero_guia FROM escaneos e WHERE e.numero_guia IS NOT NULL {filtro}
        )
    """
    with conexion.cursor() as cur:
        cur.execute(f"SELECT count(DISTINCT e.numero_guia) AS total FROM escaneos e WHERE {elegidas}", parametros)
        total = cur.fetchone()["total"]
        parametros["limite"] = POR_PAGINA_GUIAS
        parametros["desde"] = max(0, pagina - 1) * POR_PAGINA_GUIAS
        cur.execute(
            f"""
            WITH g AS (
                SELECT e.numero_guia,
                       count(*) AS paginas,
                       array_agg(DISTINCT e.formato) AS formatos,
                       (array_agg(e.propietario ORDER BY (e.formato = 'SENACSA_CERTIFICADO') DESC, e.id)
                            FILTER (WHERE e.propietario IS NOT NULL))[1] AS propietario,
                       (array_agg(e.propietario_documento ORDER BY (e.formato = 'SENACSA_CERTIFICADO') DESC, e.id)
                            FILTER (WHERE e.propietario_documento IS NOT NULL))[1] AS documento,
                       max(e.cantidad_animales) AS cantidad_animales,
                       min(e.fecha_texto) AS fecha_texto,
                       bool_and(e.revisado) AS revisada,
                       min(e.orden) AS orden,
                       array_agg(e.id ORDER BY e.orden, e.id) AS ids
                FROM escaneos e
                WHERE {elegidas}
                GROUP BY e.numero_guia
            )
            SELECT g.*,
                   (SELECT count(*) FROM marcas m
                    WHERE m.escaneo_id = ANY(g.ids) AND m.activo AND m.estado <> 'baja') AS marcas
            FROM g
            ORDER BY g.numero_guia
            LIMIT %(limite)s OFFSET %(desde)s
            """,
            parametros,
        )
        return cur.fetchall(), total


def paginas_sin_numero(conexion, texto: str | None = None, pagina: int = 1) -> tuple[list[dict], int]:
    parametros: dict = {}
    filtro = _filtro_texto(texto, parametros)
    with conexion.cursor() as cur:
        cur.execute(f"SELECT count(*) AS total FROM escaneos e WHERE e.numero_guia IS NULL {filtro}", parametros)
        total = cur.fetchone()["total"]
        parametros["limite"] = POR_PAGINA_SIN_NUMERO
        parametros["desde"] = max(0, pagina - 1) * POR_PAGINA_SIN_NUMERO
        cur.execute(
            f"""
            SELECT e.id, e.carpeta, e.archivo, e.orden, e.formato, e.propietario, e.n_marcas
            FROM escaneos e
            WHERE e.numero_guia IS NULL {filtro}
            ORDER BY e.carpeta, e.orden
            LIMIT %(limite)s OFFSET %(desde)s
            """,
            parametros,
        )
        return cur.fetchall(), total


def obtener_guia_escaneada(conexion, numero: str) -> dict | None:
    """Todas las páginas de una guía, con los datos consolidados y las marcas que salieron de ellas."""
    with conexion.cursor() as cur:
        cur.execute(
            f"SELECT {_COLUMNAS_PAGINA} FROM escaneos WHERE numero_guia = %s ORDER BY carpeta, orden, id",
            (numero,),
        )
        paginas = cur.fetchall()
        if not paginas:
            return None
        ids = [p["id"] for p in paginas]
        cur.execute(
            """
            SELECT m.id, m.codigo, m.tipo, m.estado, m.posicion, m.escaneo_id, (m.archivo_png IS NOT NULL) AS archivo_png
            FROM marcas m
            WHERE m.escaneo_id = ANY(%s) AND m.activo
            ORDER BY (m.tipo = 'dominante') DESC, m.codigo
            """,
            (ids,),
        )
        marcas = cur.fetchall()
        cur.execute(
            "SELECT id, tipo_operacion, fecha, vendedor_nombre, comprador_nombre FROM operaciones "
            "WHERE numero_guia = %s AND activo ORDER BY id",
            (numero,),
        )
        operaciones = cur.fetchall()

    def primero(campo, preferir_certificado=True):
        orden = sorted(paginas, key=lambda p: (p["formato"] != "SENACSA_CERTIFICADO", p["id"])) if preferir_certificado else paginas
        return next((p[campo] for p in orden if p.get(campo)), None)

    return {
        "numero": numero,
        "propietario": primero("propietario"),
        "documento": primero("propietario_documento"),
        "establecimiento": primero("establecimiento"),
        "cantidad_animales": max((p["cantidad_animales"] or 0 for p in paginas), default=0) or None,
        "fecha_texto": primero("fecha_texto"),
        "numeros_certificado": sorted({p["numero_certificado"] for p in paginas if p["numero_certificado"]}),
        "paginas": paginas,
        "marcas": marcas,
        "operaciones": operaciones,
    }


def obtener_escaneo(conexion, escaneo_id: int) -> dict | None:
    with conexion.cursor() as cur:
        cur.execute(f"SELECT {_COLUMNAS_PAGINA}, texto_ocr FROM escaneos WHERE id = %s", (escaneo_id,))
        pagina = cur.fetchone()
        if not pagina:
            return None
        cur.execute(
            "SELECT id FROM escaneos WHERE carpeta = %s AND orden < %s ORDER BY orden DESC LIMIT 1",
            (pagina["carpeta"], pagina["orden"]),
        )
        anterior = cur.fetchone()
        cur.execute(
            "SELECT id FROM escaneos WHERE carpeta = %s AND orden > %s ORDER BY orden LIMIT 1",
            (pagina["carpeta"], pagina["orden"]),
        )
        siguiente = cur.fetchone()
        cur.execute(
            """
            SELECT m.id, m.codigo, m.tipo, m.estado, m.posicion, (m.archivo_png IS NOT NULL) AS archivo_png
            FROM marcas m WHERE m.escaneo_id = %s AND m.activo ORDER BY (m.tipo = 'dominante') DESC, m.codigo
            """,
            (escaneo_id,),
        )
        marcas = cur.fetchall()
    pagina["anterior_id"] = anterior["id"] if anterior else None
    pagina["siguiente_id"] = siguiente["id"] if siguiente else None
    pagina["marcas"] = marcas
    return pagina


def actualizar_escaneo(conexion, escaneo_id: int, campos: dict, usuario: str | None = None) -> bool:
    """Edita los datos de una página. Si se cambia el N.º de guía, la fuente pasa a «manual»."""
    permitidos = {k: v for k, v in campos.items() if k in _CAMPOS_EDITABLES}
    if not permitidos:
        return False
    if "numero_guia" in permitidos:
        permitidos["fuente_numero"] = "manual"
        permitidos["confianza_numero"] = 1.0
    columnas = ", ".join(f"{c} = %({c})s" for c in permitidos)
    with conexion.cursor() as cur:
        cur.execute(f"UPDATE escaneos SET {columnas} WHERE id = %(_id)s", {**permitidos, "_id": escaneo_id})
        return cur.rowcount == 1


def imagen_escaneo(conexion, escaneo_id: int, miniatura: bool = False) -> bytes | None:
    columna = "miniatura" if miniatura else "imagen"
    with conexion.cursor() as cur:
        cur.execute(f"SELECT {columna} AS dato FROM escaneos_imagenes WHERE escaneo_id = %s", (escaneo_id,))
        fila = cur.fetchone()
    return bytes(fila["dato"]) if fila else None


def codigos_a_marcas(conexion, codigos: list[str]) -> list[dict]:
    """Marcas utilizables (activas y no dadas de baja) para precargar en Emitir PDF."""
    if not codigos:
        return []
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT id, codigo, descripcion, (archivo_png IS NOT NULL) AS archivo_png FROM marcas "
            "WHERE codigo = ANY(%s) AND activo AND estado <> 'baja' AND archivo_png IS NOT NULL",
            (codigos,),
        )
        por_codigo = {f["codigo"]: f for f in cur.fetchall()}
    return [por_codigo[c] for c in codigos if c in por_codigo]


def origen_de_marca(conexion, marca_id: int) -> dict | None:
    """El escaneo del que salió una marca (para verla en su contexto y confirmar que es legítima)."""
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT e.id, e.carpeta, e.archivo, e.formato, e.numero_guia FROM marcas m "
            "JOIN escaneos e ON e.id = m.escaneo_id WHERE m.id = %s",
            (marca_id,),
        )
        return cur.fetchone()

