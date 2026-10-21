"""Propietarios: listado, ficha y edición."""

from __future__ import annotations

from flask import redirect, render_template, request, url_for
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    CAMPOS_PROPIETARIO_EDITABLES,
    POR_PAGINA_PROPIETARIOS,
    actualizar_propietario,
    listar_propietarios,
    marcas_de_propietario,
    obtener_propietario,
    ocultar_propietario,
    operaciones_de_propietario,
    restaurar_propietario,
    url_imagen,
)
from marcas.servidor.utilidades import _error_seguro, _registro_no_encontrado


def registrar(app) -> None:
    @app.get("/propietarios")
    @requiere_sesion
    def propietarios():
        conexion = conexion_actual()
        perfil = perfil_actual()
        texto = request.args.get("q") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        ver_ocultos = perfil["rol"] == "administrador" and request.args.get("ver") == "ocultos"
        resultados, total = listar_propietarios(
            conexion, texto, pagina=pagina, incluir_ocultos=ver_ocultos
        )
        return render_template(
            "propietarios.html",
            activo="propietarios",
            resultados=resultados,
            total=total,
            pagina=pagina,
            por_pagina=POR_PAGINA_PROPIETARIOS,
            texto=texto or "",
            ver_ocultos=ver_ocultos,
            perfil=perfil,
        )

    @app.get("/propietarios/<int:propietario_id>")
    @requiere_sesion
    def ver_propietario(propietario_id: int):
        conexion = conexion_actual()
        propietario = obtener_propietario(conexion, propietario_id)
        if not propietario:
            return _registro_no_encontrado("No se encontró ese propietario.")
        marcas = marcas_de_propietario(conexion, propietario_id)
        for m in marcas:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        operaciones = operaciones_de_propietario(conexion, propietario.get("documento"))
        return render_template(
            "propietario_detalle.html",
            activo="propietarios",
            propietario=propietario,
            marcas=marcas,
            operaciones=operaciones,
            perfil=perfil_actual(),
        )

    @app.post("/propietarios/<int:propietario_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_propietario(propietario_id: int):
        conexion = conexion_actual()
        propietario_actual = obtener_propietario(conexion, propietario_id)
        if not propietario_actual:
            return _registro_no_encontrado("No se encontró ese propietario.")
        campos = {}
        for campo in CAMPOS_PROPIETARIO_EDITABLES:
            if campo not in request.form:
                continue
            valor = request.form.get(campo, "").strip() or None
            if valor != propietario_actual.get(campo):
                campos[campo] = valor
        if campos:
            try:
                actualizar_propietario(conexion, propietario_id, campos)
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_propietario", propietario_id=propietario_id))

    @app.post("/propietarios/<int:propietario_id>/ocultar")
    @requiere_sesion
    @requiere_rol("administrador")
    def ocultar_propietario_ruta(propietario_id: int):
        conexion = conexion_actual()
        if not obtener_propietario(conexion, propietario_id):
            return _registro_no_encontrado("No se encontró ese propietario.")
        ocultar_propietario(conexion, propietario_id)
        return redirect(url_for("ver_propietario", propietario_id=propietario_id))

    @app.post("/propietarios/<int:propietario_id>/restaurar")
    @requiere_sesion
    @requiere_rol("administrador")
    def restaurar_propietario_ruta(propietario_id: int):
        conexion = conexion_actual()
        if not obtener_propietario(conexion, propietario_id):
            return _registro_no_encontrado("No se encontró ese propietario.")
        restaurar_propietario(conexion, propietario_id)
        return redirect(url_for("ver_propietario", propietario_id=propietario_id))
