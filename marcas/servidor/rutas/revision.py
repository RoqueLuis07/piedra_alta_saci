"""Revisión rápida del catálogo: aprobar, dar de baja u ocultar marcas desde
una galería grande, sin entrar a la ficha de cada una."""

from __future__ import annotations

from flask import jsonify, render_template, request

from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    buscar_marcas,
    cambiar_revision_lote,
    cambiar_revision_marca,
    contar_bajas_automaticas,
    contar_pendientes_revision,
    eliminar_marcas_en_baja,
    motivos_pendientes,
    restaurar_revision_lote,
    url_imagen,
)

POR_PAGINA_REVISION = 48
_ESTADOS = ("activa", "revisar", "baja")


def registrar(app) -> None:
    registrar_eliminacion(app)

    @app.get("/revision")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def revision():
        conexion = conexion_actual()
        ver = request.args.get("ver") if request.args.get("ver") in ("todas", "bajas") else "pendientes"
        tipo = request.args.get("tipo") or None
        texto = request.args.get("q") or None
        motivo = request.args.get("motivo") or None
        carpeta = request.args.get("carpeta", type=int)
        carpeta = carpeta if carpeta in (1, 2) else None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        if ver == "bajas" and not motivo:
            motivo = "no_es_marca:"           # sólo las que sacó el detector; las bajas hechas a mano se ven en «Todas»
        marcas, total = buscar_marcas(
            conexion, texto, pagina=pagina, por_pagina=POR_PAGINA_REVISION,
            estado={"pendientes": "revisar", "bajas": "baja"}.get(ver), tipo=tipo,
            motivo=motivo, carpeta=carpeta,
        )
        for m in marcas:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        return render_template(
            "revision.html", activo="buscar", perfil=perfil_actual(), marcas=marcas, total=total,
            pagina=pagina, por_pagina=POR_PAGINA_REVISION, ver=ver, tipo=tipo or "", texto=texto or "",
            motivo="" if motivo == "no_es_marca:" else (motivo or ""), carpeta=carpeta or "", motivos=motivos_pendientes(conexion, "baja" if ver == "bajas" else "revisar"),
            bajas_auto=contar_bajas_automaticas(conexion),
            pendientes=contar_pendientes_revision(conexion),
        )

    @app.post("/marcas/<int:marca_id>/revision")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def revisar_marca_ruta(marca_id: int):
        """{accion: aprobar | baja | ocultar | deshacer [, estado, activo]}.
        Ocultar es sólo de Administrador (igual que en la ficha)."""
        perfil = perfil_actual()
        datos = request.get_json(silent=True) or {}
        accion = datos.get("accion")
        cambios: dict
        if accion == "aprobar":
            cambios = {"estado": "activa"}
        elif accion == "baja":
            cambios = {"estado": "baja"}
        elif accion == "ocultar":
            if perfil["rol"] != "administrador":
                return jsonify({"error": "Sólo un Administrador puede ocultar marcas."}), 403
            cambios = {"activo": False}
        elif accion == "deshacer":
            estado, activo = datos.get("estado"), datos.get("activo")
            if estado not in _ESTADOS or not isinstance(activo, bool):
                return jsonify({"error": "Pedido inválido."}), 400
            if activo is False and perfil["rol"] != "administrador":
                return jsonify({"error": "Sólo un Administrador puede ocultar marcas."}), 403
            cambios = {"estado": estado, "activo": activo}
        else:
            return jsonify({"error": "Acción no válida."}), 400

        conexion = conexion_actual()
        try:
            resultado = cambiar_revision_marca(conexion, marca_id, usuario_id=perfil["id"], **cambios)
        except Exception as exc:
            conexion.rollback()
            print(f"revisar_marca_ruta: {exc}")
            return jsonify({"error": "No se pudo guardar el cambio."}), 500
        if resultado is None:
            return jsonify({"error": "No se encontró esa marca."}), 404
        resultado["pendientes"] = contar_pendientes_revision(conexion)
        return jsonify(resultado)

    @app.post("/revision/lote")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def revision_lote():
        """{accion: aprobar | baja, ids: [...]} o {accion: restaurar, items: [{id, estado, activo}]}.
        Pensado para decidir de una vez todas las marcas visibles; siempre se puede deshacer."""
        perfil = perfil_actual()
        datos = request.get_json(silent=True) or {}
        accion = datos.get("accion")
        conexion = conexion_actual()
        try:
            if accion in ("aprobar", "baja"):
                ids = datos.get("ids")
                if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
                    return jsonify({"error": "Pedido inválido."}), 400
                anteriores = cambiar_revision_lote(conexion, ids, "activa" if accion == "aprobar" else "baja", perfil["id"])
                respuesta = {"cantidad": len(anteriores), "anteriores": anteriores}
            elif accion == "restaurar":
                items = datos.get("items")
                valido = isinstance(items, list) and 0 < len(items) <= 500 and all(
                    isinstance(i, dict) and isinstance(i.get("id"), int) and i.get("estado") in _ESTADOS
                    and isinstance(i.get("activo"), bool) and (i["activo"] or perfil["rol"] == "administrador")
                    for i in items
                )
                if not valido:
                    return jsonify({"error": "Pedido inválido."}), 400
                restaurar_revision_lote(conexion, items, perfil["id"])
                respuesta = {"cantidad": len(items)}
            else:
                return jsonify({"error": "Acción no válida."}), 400
        except Exception as exc:
            conexion.rollback()
            print(f"revision_lote: {exc}")
            return jsonify({"error": "No se pudo guardar el cambio."}), 500
        respuesta["pendientes"] = contar_pendientes_revision(conexion)
        return jsonify(respuesta)


def registrar_eliminacion(app) -> None:
    @app.post("/revision/eliminar")
    @requiere_sesion
    @requiere_rol("administrador")
    def revision_eliminar():
        """Borra para siempre marcas que ya están de baja (lo que no es una marca). {ids: [...]}"""
        conexion = conexion_actual()
        ids = (request.get_json(silent=True) or {}).get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 1000 or not all(isinstance(i, int) for i in ids):
            return jsonify({"error": "Pedido inválido."}), 400
        try:
            borradas = eliminar_marcas_en_baja(conexion, ids)
        except Exception as exc:
            conexion.rollback()
            print(f"revision_eliminar: {exc}")
            return jsonify({"error": "No se pudo eliminar."}), 500
        return jsonify({"eliminadas": borradas, "pendientes": contar_pendientes_revision(conexion)})
