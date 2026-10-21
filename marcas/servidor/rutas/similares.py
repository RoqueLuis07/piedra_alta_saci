"""Búsqueda de marcas por dibujo: se dibuja (o se sube una foto) y se ordena el catálogo por parecido."""

from __future__ import annotations

import base64
import binascii

import psycopg2
from flask import jsonify, render_template, request

from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_sesion
from marcas.servidor.consultas import desglose_marcas, url_imagen
from marcas.servidor.similitud import huella, ordenar_por_parecido

_LOTE_HUELLAS = 250  # cuántas huellas pendientes se calculan por búsqueda (el resto, en la siguiente)
_MAX_BYTES = 6 * 1024 * 1024


def calcular_huellas_pendientes(conexion, maximo: int | None = _LOTE_HUELLAS) -> tuple[int, int]:
    """Calcula y guarda la huella de las marcas que no la tienen. Devuelve (calculadas, sin_tinta)."""
    calculadas = sin_tinta = 0
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT id, archivo_png FROM marcas WHERE huella IS NULL AND archivo_png IS NOT NULL ORDER BY id"
            + (" LIMIT %s" if maximo else ""),
            (maximo,) if maximo else None,
        )
        filas = cur.fetchall()
        for fila in filas:
            h = huella(bytes(fila["archivo_png"]))
            if h is None:
                # una marca sin tinta (imagen en blanco) no puede compararse: se guarda vacía para no reintentar
                h = b""
                sin_tinta += 1
            else:
                calculadas += 1
            cur.execute("UPDATE marcas SET huella = %s WHERE id = %s", (psycopg2.Binary(h), fila["id"]))
    return calculadas, sin_tinta


def _imagen_del_pedido() -> bytes | None:
    archivo = request.files.get("imagen")
    if archivo:
        return archivo.read(_MAX_BYTES + 1)[:_MAX_BYTES]
    texto = (request.get_json(silent=True) or {}).get("imagen") or request.form.get("imagen") or ""
    if "," in texto:
        texto = texto.split(",", 1)[1]
    try:
        return base64.b64decode(texto, validate=True)[:_MAX_BYTES] if texto else None
    except (binascii.Error, ValueError):
        return None


def registrar(app) -> None:
    @app.get("/buscar/dibujo")
    @requiere_sesion
    def buscar_por_dibujo():
        return render_template(
            "buscar_dibujo.html", activo="buscar", perfil=perfil_actual(), desglose=desglose_marcas(conexion_actual())
        )

    @app.post("/buscar/dibujo")
    @requiere_sesion
    def buscar_por_dibujo_resultados():
        conexion = conexion_actual()
        contenido = _imagen_del_pedido()
        consulta = huella(contenido) if contenido else None
        if consulta is None:
            return jsonify({"error": "No se ve ningún trazo en el dibujo. Dibujá la marca más grande o con más fuerza."}), 400
        tipo = request.values.get("tipo") or (request.get_json(silent=True) or {}).get("tipo")
        incluir_bajas = bool(request.values.get("bajas") or (request.get_json(silent=True) or {}).get("bajas"))
        try:
            calcular_huellas_pendientes(conexion)
            condiciones = ["activo", "huella IS NOT NULL", "length(huella) > 0"]
            parametros: dict = {}
            if tipo in ("dominante", "complementaria"):
                condiciones.append("tipo = %(tipo)s")
                parametros["tipo"] = tipo
            if not incluir_bajas:
                condiciones.append("estado <> 'baja'")
            with conexion.cursor() as cur:
                cur.execute(f"SELECT id, huella FROM marcas WHERE {' AND '.join(condiciones)}", parametros)
                candidatas = [(f["id"], bytes(f["huella"])) for f in cur.fetchall()]
                pendientes = _contar_pendientes(cur)
            ordenadas = ordenar_por_parecido(consulta, candidatas, limite=24)
            detalles = _detalles(conexion, [i for i, _ in ordenadas])
            conexion.commit()
        except Exception as exc:
            conexion.rollback()
            print(f"buscar_por_dibujo: {exc}")
            return jsonify({"error": "No se pudo hacer la búsqueda."}), 500
        resultados = []
        for marca_id, puntaje in ordenadas:
            m = detalles.get(marca_id)
            if m:
                resultados.append({**m, "puntaje": puntaje, "imagen_url": url_imagen(marca_id, True)})
        return jsonify({"resultados": resultados, "comparadas": len(candidatas), "huellas_pendientes": pendientes})


def _contar_pendientes(cur) -> int:
    cur.execute("SELECT count(*) AS n FROM marcas WHERE huella IS NULL AND archivo_png IS NOT NULL")
    return cur.fetchone()["n"]


def _detalles(conexion, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    with conexion.cursor() as cur:
        cur.execute(
            """
            SELECT m.id, m.codigo, m.tipo, m.estado, p.nombre AS propietario
            FROM marcas m LEFT JOIN propietarios p ON p.id = m.propietario_id
            WHERE m.id = ANY(%s)
            """,
            (ids,),
        )
        return {f["id"]: {k: f[k] for k in ("codigo", "tipo", "estado", "propietario")} for f in cur.fetchall()}
