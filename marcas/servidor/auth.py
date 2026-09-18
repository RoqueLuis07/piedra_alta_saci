"""Sesión y permisos por rol.

La sesión de Flask guarda únicamente el id de quien está logueado (cookie
firmada, nunca la contraseña) -- ya no hay tokens de un tercero que
renovar: la propia cookie de Flask es la sesión, y expira sola después de
``PERMANENT_SESSION_LIFETIME`` de inactividad (ver ``crear_app()``).
"""

from __future__ import annotations

from functools import wraps

import bcrypt
from flask import g, redirect, render_template, session, url_for

from marcas.servidor.db import devolver_conexion, obtener_conexion


def iniciar_sesion(usuario_id: str) -> None:
    session.permanent = True
    session["usuario_id"] = usuario_id


def cerrar_sesion() -> None:
    session.clear()


def conexion_actual():
    """Una conexión de Postgres para esta request -- la misma en todas las
    consultas de la vista, devuelta al pool sola al terminar (ver
    ``cerrar_conexion_actual``, registrada como ``teardown_appcontext``)."""
    if "conexion" not in g:
        g.conexion = obtener_conexion()
    return g.conexion


def cerrar_conexion_actual(excepcion=None) -> None:
    conexion = g.pop("conexion", None)
    if conexion is None:
        return
    try:
        if excepcion:
            conexion.rollback()
        else:
            conexion.commit()
    finally:
        devolver_conexion(conexion)


def verificar_contrasena(contrasena: str, password_hash: str) -> bool:
    return bcrypt.checkpw(contrasena.encode("utf-8"), password_hash.encode("utf-8"))


def encriptar_contrasena(contrasena: str) -> str:
    return bcrypt.hashpw(contrasena.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _cargar_perfil():
    """Trae el perfil (rol, activo, nombre) de la persona logueada, una vez por request."""
    if "perfil" in g:
        return g.perfil
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        g.perfil = None
        return None
    conexion = conexion_actual()
    with conexion.cursor() as cur:
        cur.execute(
            "select id, email, nombre, rol, activo from usuarios where id = %s", (usuario_id,)
        )
        g.perfil = cur.fetchone()
    if g.perfil is None:
        # La cuenta fue borrada -- no tiene sentido mantener la sesión.
        session.clear()
    return g.perfil


def perfil_actual():
    return _cargar_perfil()


def requiere_sesion(vista):
    @wraps(vista)
    def envoltorio(*args, **kwargs):
        if not session.get("usuario_id"):
            return redirect(url_for("login"))
        perfil = _cargar_perfil()
        if not perfil or not perfil.get("activo"):
            return redirect(url_for("cuenta_pendiente"))
        return vista(*args, **kwargs)

    return envoltorio


def requiere_rol(*roles: str):
    def decorador(vista):
        @wraps(vista)
        def envoltorio(*args, **kwargs):
            perfil = perfil_actual()
            if not perfil or perfil.get("rol") not in roles:
                return render_template(
                    "error_simple.html",
                    titulo="No autorizado",
                    mensaje="Tu rol no tiene acceso a esta sección.",
                ), 403
            return vista(*args, **kwargs)

        return envoltorio

    return decorador
