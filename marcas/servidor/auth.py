"""Sesión y permisos por rol.

La sesión de Flask guarda el token de Supabase (cookie firmada, nunca la
contraseña); cada request reconstruye un cliente de Supabase con ese token
para que las consultas respeten RLS como esa persona -- ver ``supa.py``.
"""

from __future__ import annotations

from functools import wraps

from flask import g, redirect, session, url_for
from postgrest.exceptions import APIError

from marcas.servidor.supa import cliente_anonimo, cliente_sesion


def iniciar_sesion(access_token: str, refresh_token: str, usuario_id: str, email: str) -> None:
    session["access_token"] = access_token
    session["refresh_token"] = refresh_token
    session["usuario_id"] = usuario_id
    session["email"] = email


def cerrar_sesion() -> None:
    session.clear()


def _renovar_sesion():
    """El token de acceso dura poco (~1 hora). Si venció, se intenta renovar
    con el refresh_token antes de mandar a la persona de nuevo al login --
    si no, cualquiera que deje la página abierta un rato se encuentra con un
    error en vez de simplemente seguir trabajando."""
    refresh_token = session.get("refresh_token")
    if not refresh_token:
        return None
    try:
        resultado = cliente_anonimo().auth.refresh_session(refresh_token)
    except Exception:
        return None
    session["access_token"] = resultado.session.access_token
    session["refresh_token"] = resultado.session.refresh_token
    return cliente_sesion(resultado.session.access_token)


def _cargar_perfil():
    """Trae el perfil (rol, activo, nombre) de la persona logueada, una vez por request."""
    if "perfil" in g:
        return g.perfil
    token = session.get("access_token")
    if not token:
        g.perfil = None
        return None
    cliente = cliente_sesion(token)
    try:
        fila = (
            cliente.table("perfiles")
            .select("id, nombre, rol, activo")
            .eq("id", session["usuario_id"])
            .maybe_single()
            .execute()
        )
    except APIError:
        cliente = _renovar_sesion()
        if cliente is None:
            session.clear()
            g.perfil = None
            return None
        try:
            fila = (
                cliente.table("perfiles")
                .select("id, nombre, rol, activo")
                .eq("id", session["usuario_id"])
                .maybe_single()
                .execute()
            )
        except APIError:
            session.clear()
            g.perfil = None
            return None
    g.cliente_supabase = cliente
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
        if not session.get("access_token"):
            # _cargar_perfil() vació la sesión: el token venció y no se
            # pudo renovar. Es un caso distinto de "cuenta sin activar".
            return redirect(url_for("login"))
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
