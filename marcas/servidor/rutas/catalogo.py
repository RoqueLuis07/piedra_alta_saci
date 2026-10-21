"""Inicio y catálogo de marcas: buscar, ver, editar, ocultar, imagen."""

from __future__ import annotations

import io
from flask import jsonify, redirect, render_template, request, send_file, url_for
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    CAMPOS_MARCA_EDITABLES,
    POR_PAGINA_MARCAS,
    actualizar_marca_campos,
    buscar_marcas,
    desglose_marcas,
    ficha_marca,
    imagen_marca,
    nombres_usuarios,
    obtener_marca_por_id,
    ocultar_marca,
    restaurar_marca,
    subir_imagen_marca,
    url_imagen,
)
from marcas.servidor.consultas_escaneos import origen_de_marca
from marcas.servidor.utilidades import _CODIGO_VALIDO, _error_seguro, _registro_no_encontrado


def registrar(app) -> None:
    @app.get("/")
    @requiere_sesion
    def inicio():
        # No hay pantalla de inicio aparte: se entra directo a lo que se hace
        # todos los días (emitir un PDF) o, si sólo se consulta, al catálogo.
        perfil = perfil_actual()
        destino = "emitir" if perfil and perfil["rol"] in ("administrador", "operador") else "buscar"
        return redirect(url_for(destino))

    @app.get("/buscar")
    @requiere_sesion
    def buscar():
        conexion = conexion_actual()
        perfil = perfil_actual()
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        tipo = request.args.get("tipo") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        ver_ocultos = perfil["rol"] == "administrador" and request.args.get("ver") == "ocultos"
        resultados, total = buscar_marcas(
            conexion, texto, pagina=pagina, estado=estado, tipo=tipo, incluir_ocultos=ver_ocultos
        )
        for fila in resultados:
            fila["imagen_url"] = url_imagen(fila["id"], fila.get("archivo_png"))
        return render_template(
            "buscar.html",
            activo="buscar",
            resultados=resultados,
            total=total,
            pagina=pagina,
            por_pagina=POR_PAGINA_MARCAS,
            texto=texto or "",
            estado=estado or "",
            tipo=tipo or "",
            ver_ocultos=ver_ocultos,
            perfil=perfil,
            desglose=desglose_marcas(conexion),
        )

    @app.get("/marcas/<codigo>")
    @requiere_sesion
    def ver_marca(codigo: str):
        conexion = conexion_actual()
        ficha = ficha_marca(conexion, codigo)
        if not ficha:
            return _registro_no_encontrado("No se encontró esa marca.")
        ficha["marca"]["imagen_url"] = url_imagen(ficha["marca"]["id"], ficha["marca"].get("archivo_png"))
        for acompanante in ficha["acompanantes"]:
            acompanante["imagen_url"] = url_imagen(acompanante["id"], acompanante.get("archivo_png"))
        nombres = nombres_usuarios(
            conexion, [ficha["marca"].get("creado_por"), ficha["marca"].get("actualizado_por")]
        )
        ficha["marca"]["creado_por_nombre"] = nombres.get(str(ficha["marca"].get("creado_por")))
        ficha["marca"]["actualizado_por_nombre"] = nombres.get(str(ficha["marca"].get("actualizado_por")))
        return render_template(
            "marca_detalle.html",
            activo="buscar",
            ficha=ficha,
            origen=origen_de_marca(conexion, ficha["marca"]["id"]),
            perfil=perfil_actual(),
        )

    @app.post("/marcas/<int:marca_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_marca(marca_id: int):
        conexion = conexion_actual()
        marca_actual = obtener_marca_por_id(conexion, marca_id)
        if not marca_actual:
            return _registro_no_encontrado("No se encontró esa marca.")

        cambios: dict = {}
        for campo in CAMPOS_MARCA_EDITABLES:
            if campo in ("archivo_png", "archivo_svg"):
                continue
            if campo in request.form:
                valor = request.form.get(campo, "").strip() or None
                if campo == "codigo":
                    if not valor:
                        continue  # el código no puede quedar vacío
                    if not _CODIGO_VALIDO.match(valor):
                        return (
                            "El código sólo puede tener letras, números, puntos, "
                            "guiones y guiones bajos (se usa también como parte de la "
                            "dirección web y del nombre de archivo).",
                            400,
                        )
                if valor != marca_actual.get(campo):
                    cambios[campo] = valor

        # Las imágenes nuevas se leen acá pero recién se aplican más abajo,
        # según el rol: un Operador las escribe directo; para un
        # Administrador quedan "en espera" (codificadas en el propio cambio
        # propuesto) hasta que otra persona lo apruebe -- así una propuesta
        # rechazada nunca llega a pisar la imagen real.
        archivos_subidos: dict[str, bytes] = {}
        for campo, extension in (("nuevo_png", "png"), ("nuevo_svg", "svg")):
            archivo = request.files.get(campo)
            if archivo and archivo.filename:
                archivos_subidos[extension] = archivo.read()

        codigo_para_volver = marca_actual["codigo"]
        if cambios or archivos_subidos:
            try:
                for extension, contenido in archivos_subidos.items():
                    subir_imagen_marca(conexion, marca_id, contenido, extension)
                if cambios:
                    actualizar_marca_campos(conexion, marca_id, cambios)
                codigo_para_volver = cambios.get("codigo", codigo_para_volver)
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_marca", codigo=codigo_para_volver))

    @app.post("/marcas/<int:marca_id>/ocultar")
    @requiere_sesion
    @requiere_rol("administrador")
    def ocultar_marca_ruta(marca_id: int):
        conexion = conexion_actual()
        marca = obtener_marca_por_id(conexion, marca_id)
        if not marca:
            return _registro_no_encontrado("No se encontró esa marca.")
        ocultar_marca(conexion, marca_id)
        return redirect(url_for("ver_marca", codigo=marca["codigo"]))

    @app.post("/marcas/<int:marca_id>/restaurar")
    @requiere_sesion
    @requiere_rol("administrador")
    def restaurar_marca_ruta(marca_id: int):
        conexion = conexion_actual()
        marca = obtener_marca_por_id(conexion, marca_id)
        if not marca:
            return _registro_no_encontrado("No se encontró esa marca.")
        restaurar_marca(conexion, marca_id)
        return redirect(url_for("ver_marca", codigo=marca["codigo"]))

    @app.get("/imagenes/marca/<int:marca_id>.<extension>", endpoint="imagen_marca")
    @requiere_sesion
    def imagen_marca_ruta(marca_id: int, extension: str):
        if extension not in ("png", "svg"):
            return _registro_no_encontrado("Formato de imagen no soportado.")
        conexion = conexion_actual()
        contenido = imagen_marca(conexion, marca_id, extension)
        if not contenido:
            return _registro_no_encontrado("Esa marca no tiene esa imagen guardada.")
        mimetype = "image/svg+xml" if extension == "svg" else "image/png"
        return send_file(io.BytesIO(contenido), mimetype=mimetype, max_age=3600)

    @app.get("/marcas/buscar.json")
    @requiere_sesion
    def buscar_marcas_json():
        """Para el buscador de marcas complementarias al armar una Venta --
        busca en TODO el catálogo existente (no en una guía puntual), porque
        lo que se vende ya está cargado de antes."""
        conexion = conexion_actual()
        texto = request.args.get("q") or None
        resultados, total = buscar_marcas(conexion, texto, pagina=1, por_pagina=15, sin_baja=True)
        return jsonify([
            {
                "codigo": m["codigo"],
                "tipo": m.get("tipo"),
                "estado": m.get("estado"),
                "propietario": (m.get("propietarios") or {}).get("nombre"),
                "numero_guia": (m.get("operaciones") or {}).get("numero_guia"),
                "imagen_url": url_imagen(m["id"], m.get("archivo_png")),
            }
            for m in resultados
        ])
