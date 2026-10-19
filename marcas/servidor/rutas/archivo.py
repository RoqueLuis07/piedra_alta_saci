"""Archivo de documentos emitidos: lista, detalle y descarga."""

from __future__ import annotations

import io
from flask import render_template, request, send_file
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_sesion
from marcas.servidor.consultas import (
    POR_PAGINA_ARCHIVO,
    contenido_documento_emitido,
    listar_documentos_emitidos,
    obtener_documento_emitido,
)
from marcas.servidor.utilidades import _error_seguro, _registro_no_encontrado


def registrar(app) -> None:
    @app.get("/archivo")
    @requiere_sesion
    def archivo():
        conexion = conexion_actual()
        texto = request.args.get("q") or None
        tipo = request.args.get("tipo") or None
        desde = request.args.get("desde") or None
        hasta = request.args.get("hasta") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        try:
            documentos, total = listar_documentos_emitidos(conexion, texto, tipo, desde, hasta, pagina)
        except Exception as exc:
            return _error_seguro("No se pudo cargar el archivo.", exc)
        return render_template(
            "archivo.html",
            activo="archivo",
            documentos=documentos,
            total=total,
            pagina=pagina,
            por_pagina=POR_PAGINA_ARCHIVO,
            texto=texto or "",
            tipo=tipo or "",
            desde=desde or "",
            hasta=hasta or "",
            perfil=perfil_actual(),
        )

    @app.get("/archivo/<int:documento_id>")
    @requiere_sesion
    def ver_documento(documento_id: int):
        conexion = conexion_actual()
        documento = obtener_documento_emitido(conexion, documento_id)
        if not documento:
            return _registro_no_encontrado("No se encontró ese documento.")
        return render_template(
            "archivo_detalle.html", activo="archivo", documento=documento, perfil=perfil_actual(),
        )

    @app.get("/archivo/<int:documento_id>/pdf")
    @requiere_sesion
    def documento_pdf(documento_id: int):
        conexion = conexion_actual()
        resultado = contenido_documento_emitido(conexion, documento_id)
        if not resultado:
            return _registro_no_encontrado("No se encontró ese documento.")
        contenido, nombre = resultado
        descargar = request.args.get("descargar") == "1"
        return send_file(
            io.BytesIO(contenido), mimetype="application/pdf", download_name=nombre, as_attachment=descargar,
        )
