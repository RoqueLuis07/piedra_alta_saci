"""Servidor web de Piedra Alta: fábrica de la aplicación Flask.

La configuración y los comandos viven acá; las rutas están en ``marcas.servidor.rutas``."""

from __future__ import annotations

import os
from datetime import timedelta

import click
from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from marcas.servidor.auth import cerrar_conexion_actual, conexion_actual, encriptar_contrasena
from marcas.servidor.consultas import crear_usuario, obtener_usuario_por_email
from marcas.servidor.db import ejecutar_schema
from marcas.servidor.rutas import sesion, catalogo, operaciones, emitir, archivo, propietarios, exportacion, admin, revision
from marcas.servidor.utilidades import _etiqueta_campo, _formatear_fecha_hora


def crear_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not app.secret_key:
        raise RuntimeError("Falta FLASK_SECRET_KEY (variable de entorno) para firmar la sesión.")
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # La sesión ya no depende de un token de un tercero que vence solo --
    # es la propia cookie de Flask, así que le ponemos nosotros un límite
    # (se cierra sola tras 12 horas de inactividad).
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)

    # Sin este límite, cualquier sesión logueada podría mandar un archivo
    # enorme (imagen de marca o PDF de guía) y agotar memoria/disco del
    # servidor. 20 MB alcanza de sobra para una imagen o un PDF de guía.
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    # Token oculto obligatorio en todo formulario POST -- protege contra
    # peticiones falsificadas desde otro sitio (CSRF). Queda disponible en
    # los templates como {{ csrf_token() }}, Flask-WTF lo registra solo.
    CSRFProtect(app)
    app.jinja_env.filters["fecha_hora"] = _formatear_fecha_hora
    app.jinja_env.filters["etiqueta_campo"] = _etiqueta_campo

    # Sin esto, el login no tiene freno: alguien podría probar contraseñas
    # sin límite. El almacenamiento en memoria alcanza para un solo proceso;
    # si el día de mañana corremos varios workers en paralelo, pasar a un
    # backend compartido (Redis) para que el límite sea real entre todos.
    limiter = Limiter(get_remote_address, app=app, default_limits=[])

    app.teardown_appcontext(cerrar_conexion_actual)

    @app.cli.command("crear-esquema")
    def crear_esquema_cli():
        """Aplica schema.sql -- idempotente, se puede correr en cada deploy."""
        ejecutar_schema()
        print("Esquema aplicado.")

    @app.cli.command("crear-admin")
    @click.option(
        "--password", "password_opcion", default=None,
        help="Si no se pasa, se pide por consola (visible -- la consola web de Railway "
             "no siempre soporta bien la entrada oculta de contraseñas).",
    )
    def crear_admin_cli(password_opcion):
        """Da de alta la primera cuenta administradora (o cualquier otra,
        a mano) -- para cuando todavía no hay nadie que pueda entrar al Panel."""
        email = input("Email: ").strip().lower()
        nombre = input("Nombre: ").strip()
        contrasena = password_opcion if password_opcion is not None else input("Contraseña (queda visible): ")
        if len(contrasena) < 6:
            print("La contraseña debe tener al menos 6 caracteres.")
            return
        with app.app_context():
            conexion = conexion_actual()
            if obtener_usuario_por_email(conexion, email):
                print(f"Ya existe una cuenta con ese email ({email}).")
                return
            crear_usuario(conexion, email, encriptar_contrasena(contrasena), nombre, "administrador", True)
            conexion.commit()
        print(f"Cuenta administradora creada: {email}")

    sesion.registrar(app, limiter)
    catalogo.registrar(app)
    operaciones.registrar(app)
    emitir.registrar(app)
    archivo.registrar(app)
    propietarios.registrar(app)
    exportacion.registrar(app)
    admin.registrar(app)
    revision.registrar(app)

    return app
