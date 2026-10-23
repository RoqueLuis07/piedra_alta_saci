"""Inicio y cierre de sesión, cuenta propia y manejo de errores HTTP."""

from __future__ import annotations

from flask import redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError
from marcas.servidor.auth import (
    cerrar_sesion,
    conexion_actual,
    encriptar_contrasena,
    iniciar_sesion,
    perfil_actual,
    requiere_sesion,
    verificar_contrasena,
)
from marcas.servidor.consultas import actualizar_usuario_campos, obtener_usuario_por_email


def registrar(app, limiter) -> None:
    @app.get("/login")
    def login():
        if session.get("usuario_id"):
            return redirect(url_for("inicio"))
        return render_template("login.html", error=None)

    @app.post("/login")
    @limiter.limit("8 per minute; 30 per hour")
    def login_post():
        email = request.form.get("email", "").strip().lower()
        contrasena = request.form.get("contrasena", "")
        conexion = conexion_actual()
        usuario = obtener_usuario_por_email(conexion, email)
        if not usuario or not verificar_contrasena(contrasena, usuario["password_hash"]):
            return render_template("login.html", error="Usuario o contraseña incorrectos."), 401
        iniciar_sesion(str(usuario["id"]))
        return redirect(url_for("inicio"))

    @app.post("/logout")
    def logout():
        cerrar_sesion()
        return redirect(url_for("login"))

    @app.get("/cuenta-pendiente")
    def cuenta_pendiente():
        return render_template("cuenta_pendiente.html")

    @app.get("/mi-cuenta")
    @requiere_sesion
    def mi_cuenta():
        return render_template("mi_cuenta.html", activo="mi_cuenta", perfil=perfil_actual(), error=None)

    @app.post("/mi-cuenta")
    @requiere_sesion
    def mi_cuenta_post():
        conexion = conexion_actual()
        perfil = perfil_actual()
        actual = request.form.get("actual", "")
        nueva = request.form.get("nueva", "")
        repetir = request.form.get("repetir", "")
        usuario = obtener_usuario_por_email(conexion, perfil["email"])
        error = None
        if not verificar_contrasena(actual, usuario["password_hash"]):
            error = "La contraseña actual no es correcta."
        elif len(nueva) < 6:
            error = "La contraseña nueva debe tener al menos 6 caracteres."
        elif nueva != repetir:
            error = "Las dos contraseñas nuevas no coinciden."
        if error:
            return render_template("mi_cuenta.html", activo="mi_cuenta", perfil=perfil, error=error), 400
        actualizar_usuario_campos(conexion, perfil["id"], {"password_hash": encriptar_contrasena(nueva)})
        return redirect(url_for("mi_cuenta"))

    @app.errorhandler(429)
    def demasiados_intentos(_exc):
        return render_template(
            "error_simple.html",
            titulo="Demasiados intentos",
            mensaje="Probaste demasiadas veces en poco tiempo. Esperá unos minutos y volvé a intentar.",
        ), 429

    @app.errorhandler(413)
    def archivo_demasiado_grande(_exc):
        return render_template(
            "error_simple.html",
            titulo="El archivo es demasiado grande",
            mensaje="El límite es 20 MB por archivo. Probá con una imagen o un PDF más liviano.",
        ), 413

    @app.errorhandler(CSRFError)
    def token_invalido(_exc):
        return render_template(
            "error_simple.html",
            titulo="La página venció",
            mensaje="El formulario tardó demasiado o se abrió en otra pestaña. Volvé atrás y probá de nuevo.",
        ), 400

    @app.errorhandler(404)
    def no_encontrado(_exc):
        # Cubre tanto una URL que no matchea ninguna ruta (ej. /guias/abc con
        # un id que no es numérico) como un abort(404) explícito -- antes de
        # esto, el primer caso mostraba la página cruda de Flask, en inglés.
        return render_template(
            "error_simple.html",
            titulo="No encontrado",
            mensaje="No existe la página o el registro que buscás. Puede que el enlace esté mal escrito o ya no exista.",
        ), 404

    @app.errorhandler(500)
    def error_interno(_exc):
        return render_template(
            "error_simple.html",
            titulo="Algo salió mal",
            mensaje="No pudimos completar la acción y no se guardó nada a medias. Volvé atrás y probá de nuevo; si sigue pasando, avisá al administrador.",
        ), 500

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon/favicon-32.png")

    @app.errorhandler(405)
    def metodo_no_permitido(_exc):
        return render_template(
            "error_simple.html",
            titulo="Esa acción no se puede hacer así",
            mensaje="Probaste acceder directo a una dirección pensada para un formulario. Usá los botones y enlaces del sitio en vez de escribir la URL a mano.",
        ), 405
