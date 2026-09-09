"""Sesión y permisos por rol.

La sesión de Flask guarda el token de Supabase (cookie firmada, nunca la
contraseña); cada request reconstruye un cliente de Supabase con ese token
para que las consultas respeten RLS como esa persona -- ver ``supa.py``.
"""

from __future__ import annotations

from functools import wraps

from flask import g, redirect, session, url_for

from marcas.servidor.supa import cliente_sesion


def iniciar_sesion(access_token: str, refresh_token: str, usuario_id: str, email: str) -> None:
    session["access_token"] = access_token
    session["refresh_token"] = refresh_token
    session["usuario_id"] = usuario_id
    session["email"] = email


def cerrar_sesion() -> None:
    session.clear()


def _cargar_perfil():
    """Trae el perfil (rol, activo, nombre) de la persona logueada, una vez por request."""
    if "perfil" in g:
        return g.perfil
    token = session.get("access_token")
    if not token:
        g.perfil = None
        return None
    cliente = cliente_sesion(token)
    g.cliente_supabase = cliente
    fila = (
        cliente.table("perfiles")
        .select("id, nombre, rol, activo")
        .eq("id", session["usuario_id"])
        .maybe_single()
        .execute()
    )
    g.perfil = fila.data if fila else None
    return g.perfil


def perfil_actual():
    return _cargar_perfil()


def cliente_actual():
    """El cliente de Supabase de la sesión actual, para usar en las vistas."""
    if "cliente_supabase" not in g:
        _cargar_perfil()
    return g.get("cliente_supabase")


def requiere_sesion(vista):
    @wraps(vista)
    def envoltorio(*args, **kwargs):
        if not session.get("access_token"):
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
                return "No autorizado para esta sección.", 403
            return vista(*args, **kwargs)

        return envoltorio

    return decorador
