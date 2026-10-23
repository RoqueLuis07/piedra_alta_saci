"""Guías y ventas: listados, alta, ficha, edición y marcas de cada una."""

from __future__ import annotations

import io
from flask import jsonify, redirect, render_template, request, send_file, url_for
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    CAMPOS_OPERACION_EDITABLES,
    actualizar_operacion_campos,
    crear_marca,
    crear_operacion,
    guardar_respaldo_operacion,
    eliminar_operaciones,
    listar_operaciones_paginado,
    marcas_de_operacion,
    nombres_usuarios,
    obtener_operacion_por_id,
    obtener_respaldo_operacion,
    ocultar_operacion,
    restaurar_operacion,
    subir_imagen_marca,
    tiene_respaldo_operacion,
    url_imagen,
)
from marcas.servidor.utilidades import (
    _CAMPOS_MINIMOS_VENTA,
    _error_seguro,
    _operacion_vacia,
    _preparar_respaldo,
    _registro_no_encontrado,
)


def registrar(app) -> None:
    @app.get("/guias")
    @requiere_sesion
    def guias():
        conexion = conexion_actual()
        perfil = perfil_actual()
        pagina = max(1, request.args.get("pagina", 1, type=int))
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        creada_desde = request.args.get("desde") or None
        creada_hasta = request.args.get("hasta") or None
        ver_ocultos = perfil["rol"] == "administrador" and request.args.get("ver") == "ocultos"
        operaciones, total = listar_operaciones_paginado(
            conexion, pagina=pagina, texto=texto, estado=estado,
            creada_desde=creada_desde, creada_hasta=creada_hasta, tipo_operacion="compra",
            incluir_ocultos=ver_ocultos,
        )
        return render_template(
            "guias.html",
            activo="guias",
            modo="compra",
            operaciones=operaciones,
            pagina=pagina,
            total=total,
            texto=texto or "",
            estado=estado or "",
            desde=creada_desde or "",
            hasta=creada_hasta or "",
            ver_ocultos=ver_ocultos,
            perfil=perfil,
        )

    @app.get("/ventas")
    @requiere_sesion
    def ventas():
        conexion = conexion_actual()
        perfil = perfil_actual()
        pagina = max(1, request.args.get("pagina", 1, type=int))
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        creada_desde = request.args.get("desde") or None
        creada_hasta = request.args.get("hasta") or None
        ver_ocultos = perfil["rol"] == "administrador" and request.args.get("ver") == "ocultos"
        operaciones, total = listar_operaciones_paginado(
            conexion, pagina=pagina, texto=texto, estado=estado,
            creada_desde=creada_desde, creada_hasta=creada_hasta, tipo_operacion="venta",
            incluir_ocultos=ver_ocultos,
        )
        return render_template(
            "guias.html",
            activo="ventas",
            modo="venta",
            operaciones=operaciones,
            pagina=pagina,
            total=total,
            texto=texto or "",
            estado=estado or "",
            desde=creada_desde or "",
            hasta=creada_hasta or "",
            ver_ocultos=ver_ocultos,
            perfil=perfil,
        )

    @app.get("/guias/nueva")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def nueva_guia():
        return render_template("guia_nueva.html", activo="guias", modo="compra", perfil=perfil_actual())

    @app.get("/ventas/nueva")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def nueva_venta():
        return render_template("guia_nueva.html", activo="ventas", modo="venta", perfil=perfil_actual())

    @app.post("/guias/nueva")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def crear_guia():
        conexion = conexion_actual()
        perfil = perfil_actual()

        campos = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            valor = request.form.get(campo, "").strip() or None
            if campo in ("cantidad_animales",) and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    valor = None
            campos[campo] = valor
        campos["tipo_operacion"] = "compra"
        if campos.get("cantidad_animales") is not None and campos["cantidad_animales"] < 0:
            return _error_seguro("La cantidad de animales no puede ser negativa.", ValueError("cantidad negativa"))
        if campos.get("numero_guia"):
            with conexion.cursor() as cur:
                cur.execute("SELECT id FROM operaciones WHERE numero_guia = %s AND tipo_operacion = 'compra' AND activo LIMIT 1",
                            (campos["numero_guia"],))
                repetida = cur.fetchone()
            if repetida:
                return _error_seguro(
                    f"Ya hay una guía con el número {campos['numero_guia']}. Abrila desde el listado para completarla o corregir el número.",
                    ValueError("guía duplicada"),
                )
        if _operacion_vacia(campos):
            return _error_seguro(
                "Completá al menos el número de guía, el vendedor o la cantidad de animales antes de guardar.",
                ValueError("formulario vacío"),
            )

        # Se arma el respaldo ANTES de crear la guía: si el archivo no sirve
        # (un PDF mezclado con fotos, una imagen dañada que no se puede
        # abrir), es mejor avisar y no crear nada, a crear una guía "a
        # medias" sin el respaldo y que nadie se entere.
        archivos_respaldo = [a for a in request.files.getlist("respaldo") if a and a.filename]
        respaldo_preparado = None
        if archivos_respaldo:
            try:
                respaldo_preparado = _preparar_respaldo(archivos_respaldo)
            except Exception as exc:
                return _error_seguro(
                    "No se pudo procesar el documento de respaldo -- subí un único PDF, "
                    "o una o varias fotos/imágenes (no se pueden mezclar ambos tipos).",
                    exc,
                )

        try:
            operacion_id = crear_operacion(conexion, campos, perfil["id"])
        except Exception as exc:
            return _error_seguro("No se pudo crear la guía.", exc)

        if respaldo_preparado:
            contenido, nombre = respaldo_preparado
            try:
                guardar_respaldo_operacion(conexion, operacion_id, contenido, nombre)
            except Exception as exc:
                print(f"crear_guia: no se pudo guardar el respaldo de {operacion_id}: {exc}")

        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/ventas/nueva")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def crear_venta():
        conexion = conexion_actual()
        perfil = perfil_actual()
        campos = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            valor = request.form.get(campo, "").strip() or None
            if campo in ("cantidad_animales",) and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    valor = None
            campos[campo] = valor
        campos["tipo_operacion"] = "venta"
        if _operacion_vacia(campos, _CAMPOS_MINIMOS_VENTA):
            return _error_seguro(
                "Completá al menos el número de guía, el comprador o la cantidad de animales antes de guardar.",
                ValueError("formulario vacío"),
            )
        try:
            operacion_id = crear_operacion(conexion, campos, perfil["id"])
        except Exception as exc:
            return _error_seguro("No se pudo crear la venta.", exc)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.get("/guias/<int:operacion_id>")
    @requiere_sesion
    def ver_guia(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")
        marcas = marcas_de_operacion(conexion, operacion_id, incluir_ocultas=True)
        for m in marcas:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        nombres = nombres_usuarios(conexion, [operacion.get("creado_por")])
        operacion["creado_por_nombre"] = nombres.get(str(operacion.get("creado_por")))
        operacion["tiene_respaldo"] = tiene_respaldo_operacion(conexion, operacion_id)
        return render_template(
            "guia_detalle.html",
            activo="ventas" if operacion.get("tipo_operacion") == "venta" else "guias",
            operacion=operacion,
            marcas=marcas,
            perfil=perfil_actual(),
        )

    @app.get("/guias/<int:operacion_id>/respaldo.pdf")
    @requiere_sesion
    def respaldo_operacion_pdf(operacion_id: int):
        conexion = conexion_actual()
        resultado = obtener_respaldo_operacion(conexion, operacion_id)
        if not resultado:
            return _registro_no_encontrado("Esta guía no tiene un documento de respaldo cargado.")
        contenido, nombre = resultado
        return send_file(io.BytesIO(contenido), download_name=nombre, mimetype="application/pdf")

    @app.post("/guias/<int:operacion_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_guia(operacion_id: int):
        conexion = conexion_actual()
        operacion_actual = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion_actual:
            return _registro_no_encontrado("No se encontró esa guía.")

        cambios: dict = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            if campo not in request.form:
                continue
            valor = request.form.get(campo, "").strip() or None
            if campo in ("cantidad_animales",) and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    continue
            if valor != operacion_actual.get(campo):
                cambios[campo] = valor

        if cambios:
            try:
                actualizar_operacion_campos(conexion, operacion_id, cambios)
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/guias/<int:operacion_id>/ocultar")
    @requiere_sesion
    @requiere_rol("administrador")
    def ocultar_guia_ruta(operacion_id: int):
        conexion = conexion_actual()
        if not obtener_operacion_por_id(conexion, operacion_id):
            return _registro_no_encontrado("No se encontró esa guía.")
        ocultar_operacion(conexion, operacion_id)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/guias/eliminar")
    @requiere_sesion
    @requiere_rol("administrador")
    def eliminar_guias_ruta():
        """Borra para siempre guías/ventas elegidas ({ids: [...]}). Las que tienen documentos en el Archivo se saltean."""
        conexion = conexion_actual()
        ids = (request.get_json(silent=True) or {}).get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(i, int) for i in ids):
            return jsonify({"error": "Pedido inválido."}), 400
        try:
            borradas, bloqueadas = eliminar_operaciones(conexion, ids)
        except Exception as exc:
            conexion.rollback()
            print(f"eliminar_guias: {exc}")
            return jsonify({"error": "No se pudo eliminar."}), 500
        return jsonify({"eliminadas": len(borradas), "bloqueadas": len(bloqueadas)})

    @app.post("/guias/<int:operacion_id>/eliminar")
    @requiere_sesion
    @requiere_rol("administrador")
    def eliminar_guia_ruta(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")
        try:
            borradas, _ = eliminar_operaciones(conexion, [operacion_id])
        except Exception as exc:
            conexion.rollback()
            return _error_seguro("No se pudo eliminar.", exc)
        if not borradas:
            return _error_seguro(
                "Esta guía ya tiene documentos emitidos en el Archivo y no se puede borrar. Usá «Ocultar».",
                ValueError("operación con documentos emitidos"),
            )
        return redirect(url_for("ventas" if operacion.get("tipo_operacion") == "venta" else "guias"))

    @app.post("/guias/<int:operacion_id>/restaurar")
    @requiere_sesion
    @requiere_rol("administrador")
    def restaurar_guia_ruta(operacion_id: int):
        conexion = conexion_actual()
        if not obtener_operacion_por_id(conexion, operacion_id):
            return _registro_no_encontrado("No se encontró esa guía.")
        restaurar_operacion(conexion, operacion_id)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/guias/<int:operacion_id>/marcas")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def agregar_marca_a_guia(operacion_id: int):
        conexion = conexion_actual()
        perfil = perfil_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")

        tipo = request.form.get("tipo") or "complementaria"
        descripcion = request.form.get("descripcion", "").strip() or None
        # Con "multiple" en el campo de archivo se puede cargar de una vez
        # todo un grupo de marcas complementarias (una por imagen); sin
        # ninguna imagen se agrega una sola marca con sólo la descripción
        # (para completar la imagen más tarde) -- nunca ambas cosas vacías.
        archivos = [a for a in request.files.getlist("imagen") if a and a.filename]

        if not archivos and not descripcion:
            return _error_seguro(
                "Subí una imagen (o dibujá la marca a mano) o escribí al menos una descripción antes de agregar.",
                ValueError("marca vacía"),
            )

        if tipo not in ("dominante", "complementaria"):
            tipo = "complementaria"
        contenidos = []
        for archivo in archivos:
            crudo = archivo.read()
            extension = "svg" if archivo.filename.lower().endswith(".svg") else "png"
            if extension == "png":
                try:
                    from PIL import Image
                    import io as _io

                    Image.open(_io.BytesIO(crudo)).verify()
                except Exception:
                    return _error_seguro(
                        f"«{archivo.filename}» no es una imagen válida. No se agregó ninguna marca.", ValueError("imagen inválida")
                    )
            contenidos.append((crudo, extension))
        if tipo == "dominante":
            ya = [m for m in marcas_de_operacion(conexion, operacion_id, incluir_ocultas=True) if m.get("tipo") == "dominante"]
            if ya or len(archivos) > 1:
                return _error_seguro("Una guía tiene una sola marca dominante.", ValueError("dominante repetida"))
        try:
            for crudo, extension in contenidos or [(None, None)]:
                nueva = crear_marca(
                    conexion, {"tipo": tipo, "descripcion": descripcion, "operacion_id": operacion_id}, perfil["id"]
                )
                if crudo:
                    subir_imagen_marca(conexion, nueva["id"], crudo, extension)
        except Exception as exc:
            # Si falla la imagen N de un lote de varias, este except ya
            # evitaba que el error se propagara -- pero eso significaba
            # que Flask, al no ver ninguna excepción llegar hasta el
            # final de la request, hacía commit igual al cerrar la
            # conexión: las marcas creadas ANTES del fallo quedaban
            # guardadas de verdad, pero sin su imagen (así quedó M-00009
            # en producción). El rollback explícito hace que sea todo el
            # lote o nada -- nunca una marca a medias por el fallo de otra.
            conexion.rollback()
            return _error_seguro("No se pudo agregar la marca.", exc)

        return redirect(url_for("ver_guia", operacion_id=operacion_id))
