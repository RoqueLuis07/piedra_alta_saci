"""Exportación a Excel de operaciones y marcas."""

from __future__ import annotations

from flask import render_template, request, send_file
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_sesion
from marcas.servidor.consultas import (
    LIMITE_EXPORTAR,
    buscar_marcas,
    exportar_marcas,
    exportar_operaciones,
    listar_operaciones_paginado,
)
from marcas.servidor.utilidades import _error_seguro, _fecha_sin_tz, _libro_excel


def registrar(app) -> None:
    @app.get("/exportar")
    @requiere_sesion
    def exportar():
        conexion = conexion_actual()
        seccion = request.args.get("seccion") or "operaciones"
        if seccion not in ("operaciones", "marcas"):
            seccion = "operaciones"
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        resultados = []
        total = 0
        if seccion == "marcas":
            tipo = request.args.get("tipo") or None
            carpeta = request.args.get("carpeta", type=int)
            resultados, total = buscar_marcas(
                conexion, texto, pagina=1, por_pagina=LIMITE_EXPORTAR, estado=estado, tipo=tipo,
                carpeta=carpeta if carpeta in (1, 2) else None,
            )
            filtros = {"q": texto or "", "estado": estado or "", "tipo": tipo or ""}
        else:
            tipo_operacion = request.args.get("tipo_operacion") or None
            if tipo_operacion not in ("compra", "venta"):
                tipo_operacion = None
            desde = request.args.get("desde") or None
            hasta = request.args.get("hasta") or None
            resultados, total = listar_operaciones_paginado(
                conexion, pagina=1, por_pagina=LIMITE_EXPORTAR, texto=texto, estado=estado,
                creada_desde=desde, creada_hasta=hasta, tipo_operacion=tipo_operacion,
            )
            filtros = {
                "q": texto or "", "estado": estado or "", "tipo_operacion": tipo_operacion or "",
                "desde": desde or "", "hasta": hasta or "",
            }
        return render_template(
            "exportar.html",
            activo="exportar",
            seccion=seccion,
            resultados=resultados,
            total=total,
            limite=LIMITE_EXPORTAR,
            filtros=filtros,
            perfil=perfil_actual(),
        )

    @app.post("/exportar/operaciones.xlsx")
    @requiere_sesion
    def exportar_operaciones_xlsx():
        conexion = conexion_actual()
        ids = [int(v) for v in request.form.getlist("ids") if v.isdigit()]
        if not ids:
            return _error_seguro("Marcá al menos un registro para exportar.", ValueError("sin ids"))
        try:
            filas = exportar_operaciones(conexion, ids=ids)
        except Exception as exc:
            return _error_seguro("No se pudo generar la planilla.", exc)
        etiquetas_tipo = {"compra": "Compra", "venta": "Venta"}
        cuerpo = [
            [
                etiquetas_tipo.get(o.get("tipo_operacion"), o.get("tipo_operacion") or ""),
                o.get("numero_guia") or "", o.get("fecha") or "", o.get("vendedor_nombre") or "",
                o.get("vendedor_documento") or "", o.get("comprador_nombre") or "",
                o.get("comprador_documento") or "",
                o.get("cantidad_animales") if o.get("cantidad_animales") is not None else "",
                o.get("categoria_animales") or "",
                o.get("revisar") or "",
                "Sí" if o.get("guia_colisionada") else "No", _fecha_sin_tz(o.get("creado_en")),
            ]
            for o in filas
        ]
        buffer = _libro_excel(
            "Guías y ventas",
            ["Tipo", "N° de guía", "Fecha", "Vendedor", "Doc. vendedor", "Comprador", "Doc. comprador",
             "Cantidad", "Categoría", "Revisar", "Colisiona", "Creado en"],
            cuerpo,
        )
        return send_file(
            buffer, as_attachment=True, download_name="guias_y_ventas.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.post("/exportar/marcas.xlsx")
    @requiere_sesion
    def exportar_marcas_xlsx():
        conexion = conexion_actual()
        ids = [int(v) for v in request.form.getlist("ids") if v.isdigit()]
        if not ids:
            return _error_seguro("Marcá al menos un registro para exportar.", ValueError("sin ids"))
        try:
            filas = exportar_marcas(conexion, ids=ids)
        except Exception as exc:
            return _error_seguro("No se pudo generar la planilla.", exc)
        etiquetas_tipo = {"dominante": "Dominante", "complementaria": "Complementaria"}
        etiquetas_estado = {"activa": "Al día", "revisar": "Revisar", "baja": "De baja"}
        cuerpo = []
        for m in filas:
            propietario = m.get("propietarios") or {}
            operacion = m.get("operaciones") or {}
            cuerpo.append([
                m.get("codigo") or "", etiquetas_tipo.get(m.get("tipo"), m.get("tipo") or ""),
                etiquetas_estado.get(m.get("estado"), m.get("estado") or ""), m.get("vence_en") or "",
                propietario.get("nombre") or "", propietario.get("documento") or "",
                operacion.get("numero_guia") or "", operacion.get("fecha") or "",
            ])
        buffer = _libro_excel(
            "Marcas",
            ["Código", "Tipo", "Estado", "Vence", "Propietario", "Documento", "N° de guía", "Fecha"],
            cuerpo,
        )
        return send_file(
            buffer, as_attachment=True, download_name="marcas.xlsx",
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
