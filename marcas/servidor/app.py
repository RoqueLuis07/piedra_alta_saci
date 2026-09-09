"""Sistema multiusuario: login por Supabase Auth + búsqueda + panel de roles.

Es la app que corre en Railway (ver ``wsgi.py``). Toda consulta de datos
pasa por un cliente de Supabase con el token de la sesión, para que RLS
decida qué puede ver o tocar cada rol -- el código de acá no es la única
barrera, es la primera.
"""

from __future__ import annotations

import os

from flask import Flask, redirect, render_template, request, session, url_for

from marcas.servidor.auth import (
    cerrar_sesion,
    cliente_actual,
    iniciar_sesion,
    perfil_actual,
    requiere_rol,
    requiere_sesion,
)
from marcas.servidor.consultas import buscar_marcas, ficha_marca, url_imagen
from marcas.servidor.supa import cliente_anonimo


def crear_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not app.secret_key:
        raise RuntimeError("Falta FLASK_SECRET_KEY (variable de entorno) para firmar la sesión.")
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    @app.get("/login")
    def login():
        if session.get("access_token"):
            return redirect(url_for("buscar"))
        return render_template("login.html", error=None)

    @app.post("/login")
    def login_post():
        email = request.form.get("email", "").strip()
        contrasena = request.form.get("contrasena", "")
        try:
            resultado = cliente_anonimo().auth.sign_in_with_password(
                {"email": email, "password": contrasena}
            )
        except Exception:
            return render_template("login.html", error="Usuario o contraseña incorrectos."), 401
        iniciar_sesion(
            resultado.session.access_token,
            resultado.session.refresh_token,
            resultado.user.id,
            resultado.user.email,
        )
        return redirect(url_for("buscar"))

    @app.post("/logout")
    def logout():
        cerrar_sesion()
        return redirect(url_for("login"))

    @app.get("/cuenta-pendiente")
    def cuenta_pendiente():
        return render_template("cuenta_pendiente.html")

    @app.get("/")
    @requiere_sesion
    def buscar():
        cliente = cliente_actual()
        texto = request.args.get("q") or None
        resultados = buscar_marcas(cliente, texto)
        codigo_visto = request.args.get("ver")
        ficha = ficha_marca(cliente, codigo_visto) if codigo_visto else None
        if ficha:
            ficha["marca"]["imagen_url"] = url_imagen(cliente, ficha["marca"].get("archivo_png"))
            for acompanante in ficha["acompanantes"]:
                acompanante["imagen_url"] = url_imagen(cliente, acompanante.get("archivo_png"))
        return render_template(
            "buscar.html",
            resultados=resultados,
            texto=texto or "",
            ficha=ficha,
            perfil=perfil_actual(),
        )

    @app.get("/panel")
    @requiere_sesion
    @requiere_rol("administrador")
    def panel():
        cliente = cliente_actual()
        usuarios = cliente.table("perfiles").select("*").order("creado_en").execute().data
        cambios = (
            cliente.table("cambios_pendientes")
            .select("*")
            .eq("estado", "pendiente")
            .order("propuesto_en")
            .execute()
            .data
        )
        return render_template("panel.html", usuarios=usuarios, cambios=cambios)

    @app.post("/panel/usuarios/<usuario_id>")
    @requiere_sesion
    @requiere_rol("administrador")
    def actualizar_usuario(usuario_id: str):
        cliente = cliente_actual()
        campos = {}
        if request.form.get("rol"):
            campos["rol"] = request.form["rol"]
        if "activo" in request.form:
            campos["activo"] = request.form["activo"] == "1"
        if campos:
            cliente.table("perfiles").update(campos).eq("id", usuario_id).execute()
        return redirect(url_for("panel"))

    @app.post("/panel/cambios/<int:cambio_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def resolver_cambio(cambio_id: int):
        cliente = cliente_actual()
        decision = request.form.get("decision")
        motivo = request.form.get("motivo") or None
        try:
            cliente.rpc(
                "resolver_cambio_pendiente",
                {"p_cambio_id": cambio_id, "p_decision": decision, "p_motivo": motivo},
            ).execute()
        except Exception as exc:
            return f"No se pudo resolver el cambio: {exc}", 400
        return redirect(url_for("panel"))

    return app


if __name__ == "__main__":       # pragma: no cover
    crear_app().run(debug=True)
