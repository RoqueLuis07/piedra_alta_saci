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
from marcas.servidor.consultas import (
    buscar_marcas,
    cambios_pendientes_detalle,
    estadisticas,
    ficha_marca,
    marcas_a_revisar,
    ultimos_asientos,
    url_imagen,
)
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
            return redirect(url_for("inicio"))
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
        return redirect(url_for("inicio"))

    @app.post("/logout")
    def logout():
        cerrar_sesion()
        return redirect(url_for("login"))

    @app.get("/cuenta-pendiente")
    def cuenta_pendiente():
        return render_template("cuenta_pendiente.html")

    @app.get("/recuperar")
    def recuperar():
        return render_template("recuperar.html", enviado=False, error=None)

    @app.post("/recuperar")
    def recuperar_post():
        email = request.form.get("email", "").strip()
        if email:
            destino = request.host_url.rstrip("/") + url_for("restablecer")
            try:
                cliente_anonimo().auth.reset_password_for_email(email, {"redirect_to": destino})
            except Exception:
                pass  # nunca revelar si el email existe o no
        return render_template("recuperar.html", enviado=True, error=None)

    @app.get("/restablecer")
    def restablecer():
        return render_template("restablecer.html", error=None)

    @app.post("/restablecer")
    def restablecer_post():
        access_token = request.form.get("access_token", "")
        refresh_token = request.form.get("refresh_token", "")
        nueva = request.form.get("nueva_contrasena", "")
        if not access_token or not refresh_token or len(nueva) < 6:
            return render_template(
                "restablecer.html",
                error="Datos incompletos o contraseña muy corta (mínimo 6 caracteres).",
            ), 400
        try:
            cliente = cliente_anonimo()
            cliente.auth.set_session(access_token, refresh_token)
            cliente.auth.update_user({"password": nueva})
        except Exception:
            return render_template(
                "restablecer.html", error="El enlace venció o no es válido. Pedí uno nuevo."
            ), 400
        return redirect(url_for("login"))

    @app.context_processor
    def inyectar_pendientes():
        perfil = perfil_actual()
        if not perfil or perfil.get("rol") not in ("administrador", "operador"):
            return {}
        cliente = cliente_actual()
        if not cliente:
            return {}
        try:
            n = (
                cliente.table("cambios_pendientes")
                .select("id", count="exact")
                .eq("estado", "pendiente")
                .execute()
                .count
                or 0
            )
        except Exception:
            n = 0
        return {"pendientes_nav": n}

    @app.get("/")
    @requiere_sesion
    def inicio():
        cliente = cliente_actual()
        return render_template(
            "inicio.html",
            activo="inicio",
            perfil=perfil_actual(),
            stats=estadisticas(cliente),
            asientos=ultimos_asientos(cliente),
            a_revisar=marcas_a_revisar(cliente),
        )

    @app.get("/buscar")
    @requiere_sesion
    def buscar():
        cliente = cliente_actual()
        texto = request.args.get("q") or None
        resultados, total = buscar_marcas(cliente, texto)
        for fila in resultados:
            fila["imagen_url"] = url_imagen(cliente, fila.get("archivo_png"))
        codigo_visto = request.args.get("ver")
        ficha = ficha_marca(cliente, codigo_visto) if codigo_visto else None
        if ficha:
            ficha["marca"]["imagen_url"] = url_imagen(cliente, ficha["marca"].get("archivo_png"))
            for acompanante in ficha["acompanantes"]:
                acompanante["imagen_url"] = url_imagen(cliente, acompanante.get("archivo_png"))
        return render_template(
            "buscar.html",
            activo="buscar",
            resultados=resultados,
            total=total,
            texto=texto or "",
            ficha=ficha,
            perfil=perfil_actual(),
        )

    @app.get("/aprobaciones")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def aprobaciones():
        cliente = cliente_actual()
        return render_template(
            "aprobaciones.html",
            activo="aprobaciones",
            cambios=cambios_pendientes_detalle(cliente),
            perfil=perfil_actual(),
        )

    @app.post("/aprobaciones/<int:cambio_id>")
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
        return redirect(url_for("aprobaciones"))

    @app.get("/panel")
    @requiere_sesion
    @requiere_rol("administrador")
    def panel():
        cliente = cliente_actual()
        usuarios = cliente.table("perfiles").select("*").order("creado_en").execute().data
        return render_template(
            "panel.html", activo="panel", usuarios=usuarios, perfil=perfil_actual()
        )

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

    return app


if __name__ == "__main__":       # pragma: no cover
    crear_app().run(debug=True)
