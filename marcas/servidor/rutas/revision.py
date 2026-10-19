"""Revisión rápida del catálogo: aprobar, dar de baja u ocultar marcas desde
una galería grande, sin entrar a la ficha de cada una."""

from __future__ import annotations

from flask import jsonify, render_template, request

from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    buscar_marcas,
    cambiar_revision_marca,
    contar_pendientes_revision,
    url_imagen,
)

POR_PAGINA_REVISION = 48
_ESTADOS = ("activa", "revisar", "baja")


def registrar(app) -> None:
    @app.get("/revision")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def revision():
        conexion = conexion_actual()
        ver = "todas" if request.args.get("ver") == "todas" else "pendientes"
        tipo = request.args.get("tipo") or None
        texto = request.args.get("q") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        marcas, total = buscar_marcas(
            conexion, texto, pagina=pagina, por_pagina=POR_PAGINA_REVISION,
            estado="revisar" if ver == "pendientes" else None, tipo=tipo,
        )
        for m in marcas:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        return render_template(
            "revision.html", activo="buscar", perfil=perfil_actual(), marcas=marcas, total=total,
            pagina=pagina, por_pagina=POR_PAGINA_REVISION, ver=ver, tipo=tipo or "", texto=texto or "",
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
