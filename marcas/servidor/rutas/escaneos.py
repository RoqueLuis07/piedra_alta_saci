"""Guías escaneadas: todas las páginas de los documentos históricos, agrupadas por N.º de guía,
con lo leído por OCR (editable), el texto completo para buscar y las marcas que salieron de cada una."""

from __future__ import annotations

from flask import Response, jsonify, redirect, render_template, request, url_for

from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import url_imagen
from marcas.servidor.consultas_escaneos import (
    FORMATOS,
    POR_PAGINA_GUIAS,
    POR_PAGINA_SIN_NUMERO,
    actualizar_escaneo,
    imagen_escaneo,
    listar_guias_escaneadas,
    obtener_escaneo,
    obtener_guia_escaneada,
    paginas_sin_numero,
    resumen_escaneos,
)
from marcas.servidor.utilidades import _registro_no_encontrado

MAX_MARCAS_A_EMITIR = 150


def _entero_o_none(valor: str | None) -> int | None:
    valor = (valor or "").strip()
    return int(valor) if valor.isdigit() else None


def registrar(app) -> None:
    @app.get("/escaneos")
    @requiere_sesion
    def escaneos():
        conexion = conexion_actual()
        vista = "sin_numero" if request.args.get("vista") == "sin_numero" else "guias"
        texto = (request.args.get("q") or "").strip() or None
        formato = request.args.get("formato") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        if vista == "sin_numero":
            filas, total = paginas_sin_numero(conexion, texto, pagina)
            por_pagina = POR_PAGINA_SIN_NUMERO
        else:
            filas, total = listar_guias_escaneadas(conexion, texto, formato, pagina)
            por_pagina = POR_PAGINA_GUIAS
        return render_template(
            "escaneos.html", activo="escaneos", perfil=perfil_actual(), vista=vista, filas=filas, total=total,
            pagina=pagina, por_pagina=por_pagina, texto=texto or "", formato=formato or "",
            formatos=FORMATOS, resumen=resumen_escaneos(conexion),
        )

    @app.get("/escaneos/guia/<numero>")
    @requiere_sesion
    def ver_guia_escaneada(numero: str):
        conexion = conexion_actual()
        guia = obtener_guia_escaneada(conexion, numero)
        if not guia:
            return _registro_no_encontrado("No se encontró esa guía escaneada.")
        for m in guia["marcas"]:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        utiles = [m["codigo"] for m in guia["marcas"] if m.get("archivo_png") and m["estado"] != "baja"]
        return render_template(
            "escaneo_guia.html", activo="escaneos", perfil=perfil_actual(), guia=guia, formatos=FORMATOS,
            codigos_emitir=",".join(utiles[:MAX_MARCAS_A_EMITIR]), cantidad_emitir=min(len(utiles), MAX_MARCAS_A_EMITIR),
        )

    @app.get("/escaneos/pagina/<int:escaneo_id>")
    @requiere_sesion
    def ver_escaneo(escaneo_id: int):
        conexion = conexion_actual()
        pagina = obtener_escaneo(conexion, escaneo_id)
        if not pagina:
            return _registro_no_encontrado("No se encontró esa página escaneada.")
        for m in pagina["marcas"]:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        return render_template(
            "escaneo_pagina.html", activo="escaneos", perfil=perfil_actual(), pagina=pagina, formatos=FORMATOS,
        )

    @app.post("/escaneos/pagina/<int:escaneo_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_escaneo(escaneo_id: int):
        """Corrige a mano lo que el OCR leyó mal. Acepta formulario o JSON ({numero_guia: "..."})."""
        conexion = conexion_actual()
        datos = request.get_json(silent=True) if request.is_json else request.form
        datos = datos or {}
        campos: dict = {}
        for campo in ("numero_guia", "propietario", "propietario_documento", "establecimiento", "notas"):
            if campo in datos:
                valor = (datos.get(campo) or "").strip()
                campos[campo] = valor or None
        if "cantidad_animales" in datos:
            campos["cantidad_animales"] = _entero_o_none(str(datos.get("cantidad_animales")))
        if "revisado" in datos:
            campos["revisado"] = str(datos.get("revisado")).lower() in ("1", "true", "on", "si", "sí")
        if "numero_guia" in campos and campos["numero_guia"] and len(campos["numero_guia"]) > 40:
            respuesta = {"error": "El N.º de guía es demasiado largo."}
            return (jsonify(respuesta), 400) if request.is_json else (respuesta["error"], 400)
        try:
            ok = actualizar_escaneo(conexion, escaneo_id, campos)
        except Exception as exc:
            conexion.rollback()
            print(f"editar_escaneo: {exc}")
            respuesta = {"error": "No se pudo guardar el cambio."}
            return (jsonify(respuesta), 500) if request.is_json else (respuesta["error"], 500)
        if not ok:
            return (jsonify({"error": "No se encontró la página."}), 404) if request.is_json else _registro_no_encontrado("No se encontró la página.")
        if request.is_json:
            return jsonify({"ok": True})
        return redirect(url_for("ver_escaneo", escaneo_id=escaneo_id))

    def _servir(escaneo_id: int, miniatura: bool):
        conexion = conexion_actual()
        contenido = imagen_escaneo(conexion, escaneo_id, miniatura=miniatura)
        if not contenido:
            return _registro_no_encontrado("No se encontró la imagen.")
        return Response(contenido, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/escaneos/imagen/<int:escaneo_id>.jpg", endpoint="imagen_escaneo")
    @requiere_sesion
    def imagen_escaneo_ruta(escaneo_id: int):
        return _servir(escaneo_id, miniatura=False)

    @app.get("/escaneos/miniatura/<int:escaneo_id>.jpg", endpoint="miniatura_escaneo")
    @requiere_sesion
    def miniatura_escaneo_ruta(escaneo_id: int):
        return _servir(escaneo_id, miniatura=True)
