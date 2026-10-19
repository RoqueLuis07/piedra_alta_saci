"""Administración: usuarios, panel y respaldo completo."""

from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from flask import redirect, render_template, request, send_file, url_for
from marcas.servidor.auth import (
    conexion_actual,
    encriptar_contrasena,
    perfil_actual,
    requiere_rol,
    requiere_sesion,
)
from marcas.servidor.consultas import (
    actualizar_usuario_campos,
    crear_usuario,
    dict_a_json,
    exportar_todo_para_respaldo,
    listar_usuarios,
    obtener_usuario_por_email,
    obtener_usuario_por_id,
)
from marcas.servidor.utilidades import _error_seguro, _registro_no_encontrado


def registrar(app) -> None:
    @app.get("/panel")
    @requiere_sesion
    @requiere_rol("administrador")
    def panel():
        conexion = conexion_actual()
        usuarios = listar_usuarios(conexion)
        return render_template(
            "panel.html", activo="panel", usuarios=usuarios, perfil=perfil_actual(), error=None
        )

    @app.get("/panel/respaldo.zip")
    @requiere_sesion
    @requiere_rol("administrador")
    def respaldo_completo():
        conexion = conexion_actual()
        datos = exportar_todo_para_respaldo(conexion)
        ahora = datetime.now(timezone.utc)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for tabla, filas in datos.items():
                zf.writestr(f"{tabla}.json", dict_a_json(filas))
            zf.writestr(
                "manifiesto.json",
                dict_a_json({
                    "generado_en": ahora,
                    "generado_por": perfil_actual()["email"],
                    "cantidades": {tabla: len(filas) for tabla, filas in datos.items()},
                }),
            )
        buffer.seek(0)
        nombre = f"respaldo_piedra_alta_{ahora.strftime('%Y%m%d_%H%M%S')}.zip"
        return send_file(buffer, as_attachment=True, download_name=nombre, mimetype="application/zip")

    @app.post("/panel/usuarios")
    @requiere_sesion
    @requiere_rol("administrador")
    def crear_usuario_ruta():
        conexion = conexion_actual()
        email = request.form.get("email", "").strip().lower()
        nombre = request.form.get("nombre", "").strip()
        rol = request.form.get("rol") or "consulta"
        contrasena = request.form.get("contrasena", "")
        error = None
        if not email or not nombre:
            error = "Completá el email y el nombre."
        elif len(contrasena) < 6:
            error = "La contraseña debe tener al menos 6 caracteres."
        elif rol not in ("administrador", "operador", "consulta"):
            error = "Rol inválido."
        elif obtener_usuario_por_email(conexion, email):
            error = "Ya existe una cuenta con ese email."
        if error:
            return render_template(
                "panel.html", activo="panel", usuarios=listar_usuarios(conexion), perfil=perfil_actual(), error=error
            ), 400
        crear_usuario(conexion, email, encriptar_contrasena(contrasena), nombre, rol, True)
        return redirect(url_for("panel"))

    @app.post("/panel/usuarios/<usuario_id>")
    @requiere_sesion
    @requiere_rol("administrador")
    def actualizar_usuario(usuario_id: str):
        conexion = conexion_actual()
        if not obtener_usuario_por_id(conexion, usuario_id):
            return _registro_no_encontrado("No se encontró ese usuario.")
        campos = {}
        if request.form.get("rol"):
            campos["rol"] = request.form["rol"]
        if "activo" in request.form:
            campos["activo"] = request.form["activo"] == "1"
        nueva_contrasena = request.form.get("nueva_contrasena", "")
        if nueva_contrasena:
            if len(nueva_contrasena) < 6:
                return _error_seguro("La contraseña debe tener al menos 6 caracteres.", "contraseña corta")
            campos["password_hash"] = encriptar_contrasena(nueva_contrasena)
        if campos:
            actualizar_usuario_campos(conexion, usuario_id, campos)
        return redirect(url_for("panel"))
