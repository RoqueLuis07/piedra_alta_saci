"""Emisión del PDF final de una guía o venta (vista previa, borradores y generación)."""

from __future__ import annotations

import io
import tempfile
from datetime import datetime, timezone
from flask import jsonify, redirect, render_template, request, send_file, url_for
from marcas.pdf.guia import completar_guia, completar_venta
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    borrador_venta_dominante as obtener_borrador_venta_dominante,
    borrador_venta_pdf as obtener_borrador_venta_pdf,
    eliminar_borrador_venta,
    guardar_borrador_venta,
    marcas_de_operacion,
    marcas_por_codigos,
    obtener_operacion_por_id,
    url_imagen,
)
from pathlib import Path
from marcas.servidor.utilidades import (
    _entregar_documento,
    _error_seguro,
    _mensaje_sin_imagen,
    _preparar_imagenes,
    _previsualizar_estampado,
    _registro_no_encontrado,
)


def registrar(app) -> None:
    @app.get("/guias/<int:operacion_id>/imprimir")
    @requiere_sesion
    def imprimir_guia(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")
        if operacion.get("tipo_operacion") == "venta":
            # Alguien llegó a la URL de imprimir Guía para una Venta (ej. un
            # link viejo o tipeando la URL a mano, ya que ambas comparten la
            # misma ficha de detalle) -- la pantalla de Venta es la que sabe
            # buscar marcas en todo el catálogo, así que se manda para allá
            # en vez de mostrar una pantalla vacía sin salida.
            return redirect(url_for("imprimir_venta", operacion_id=operacion_id))
        marcas = marcas_de_operacion(conexion, operacion_id)
        for m in marcas:
            m["imagen_url"] = url_imagen(m["id"], m.get("archivo_png"))
        borrador = operacion.get("borrador_venta")
        ids_elegidas_borrador = {int(v) for v in (borrador or {}).get("marcas", []) if v.isdigit()}
        return render_template(
            "guia_imprimir.html",
            activo="guias",
            operacion=operacion,
            marcas=marcas,
            tiene_borrador_pdf=bool(operacion.get("borrador_pdf")),
            borrador_pdf_nombre=operacion.get("borrador_pdf_nombre"),
            ids_elegidas_borrador=ids_elegidas_borrador,
            perfil=perfil_actual(),
        )

    @app.post("/guias/<int:operacion_id>/imprimir")
    @requiere_sesion
    def generar_guia_pdf(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") == "venta":
            return _registro_no_encontrado("No se encontró esa guía.")

        archivo_pdf = request.files.get("pdf_guia")
        if not archivo_pdf or not archivo_pdf.filename:
            return "Subí el PDF de la guía descargado de SENACSA.", 400

        ids_elegidos = {int(v) for v in request.form.getlist("marca_id")}
        if not ids_elegidos:
            return "Seleccioná al menos una marca.", 400

        marcas = [m for m in marcas_de_operacion(conexion, operacion_id) if m["id"] in ids_elegidos]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_entrada = tmp_path / "entrada.pdf"
            archivo_pdf.save(pdf_entrada)

            rutas_imagenes, sin_imagen = _preparar_imagenes(conexion, marcas, tmp_path)
            if sin_imagen:
                return _mensaje_sin_imagen(sin_imagen), 400

            salida = tmp_path / "salida.pdf"
            try:
                completar_guia(pdf_entrada, rutas_imagenes, salida)
            except Exception as exc:
                # completar_guia ya levanta mensajes propios pensados para
                # mostrarse (son los mismos que ya se ven, sin filtrar, en
                # la vista previa en vivo) -- mostrar ese detalle en vez del
                # genérico evita que haya que volver atrás a adivinar qué
                # pasó, cuando la propia app ya lo sabía.
                return _error_seguro(str(exc) or "No se pudo generar el PDF.", exc)
            contenido_pdf = salida.read_bytes()

        return _entregar_documento(conexion, operacion, "guia", marcas, contenido_pdf)

    @app.post("/guias/<int:operacion_id>/borrador")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def guardar_borrador_guia_ruta(operacion_id: int):
        """Mismo mecanismo que el borrador de Venta (guardar_borrador_venta
        es genérica por operacion_id) -- acá la lista guardada son ids de
        marcas ya cargadas en esta guía, no códigos del catálogo entero."""
        conexion = conexion_actual()
        if not obtener_operacion_por_id(conexion, operacion_id):
            return _registro_no_encontrado("No se encontró esa guía.")
        ids = [v.strip() for v in request.form.getlist("marca_id") if v.strip()]
        archivo_pdf = request.files.get("pdf_guia")
        pdf_bytes = archivo_pdf.read() if archivo_pdf and archivo_pdf.filename else None
        pdf_nombre = archivo_pdf.filename if archivo_pdf and archivo_pdf.filename else None
        guardar_borrador_venta(
            conexion, operacion_id, ids, datetime.now(timezone.utc).isoformat(),
            pdf_bytes=pdf_bytes, pdf_nombre=pdf_nombre,
        )
        return redirect(url_for("imprimir_guia", operacion_id=operacion_id))

    @app.post("/guias/<int:operacion_id>/borrador/descartar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def descartar_borrador_guia_ruta(operacion_id: int):
        conexion = conexion_actual()
        if not obtener_operacion_por_id(conexion, operacion_id):
            return _registro_no_encontrado("No se encontró esa guía.")
        eliminar_borrador_venta(conexion, operacion_id)
        return redirect(url_for("imprimir_guia", operacion_id=operacion_id))

    @app.get("/guias/<int:operacion_id>/borrador/pdf")
    @requiere_sesion
    def borrador_guia_pdf(operacion_id: int):
        conexion = conexion_actual()
        resultado = obtener_borrador_venta_pdf(conexion, operacion_id)
        if not resultado:
            return _registro_no_encontrado("Ese borrador no tiene un PDF guardado.")
        contenido, nombre = resultado
        return send_file(io.BytesIO(contenido), download_name=nombre, mimetype="application/pdf")

    @app.post("/guias/<int:operacion_id>/previsualizar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def previsualizar_guia(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion:
            return jsonify({"error": "No se encontró esa guía."}), 404

        archivo_pdf = request.files.get("pdf_guia")
        pdf_bytes = archivo_pdf.read() if archivo_pdf and archivo_pdf.filename else None
        pdf_nombre = archivo_pdf.filename if archivo_pdf and archivo_pdf.filename else None

        ids = [v.strip() for v in request.form.getlist("marca_id") if v.strip()]
        if pdf_bytes or ids != (operacion.get("borrador_venta") or {}).get("marcas", []):
            guardar_borrador_venta(
                conexion, operacion_id, ids, datetime.now(timezone.utc).isoformat(),
                pdf_bytes=pdf_bytes, pdf_nombre=pdf_nombre,
            )

        if not pdf_bytes:
            guardado = obtener_borrador_venta_pdf(conexion, operacion_id)
            if not guardado:
                return jsonify({"error": "Subí primero el PDF de la guía."}), 400
            pdf_bytes = guardado[0]

        ids_int = {int(v) for v in ids if v.isdigit()}
        marcas_elegidas = [m for m in marcas_de_operacion(conexion, operacion_id) if m["id"] in ids_int]

        try:
            resultado = _previsualizar_estampado(conexion, operacion, marcas_elegidas, pdf_bytes)
        except Exception as exc:
            print(f"previsualizar_guia: {exc}")
            return jsonify({"error": "No se pudo generar la vista previa."}), 400
        return jsonify(resultado)

    @app.get("/ventas/<int:operacion_id>/imprimir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def imprimir_venta(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")

        borrador = operacion.get("borrador_venta")
        marcas_borrador = []
        if borrador and borrador.get("marcas"):
            por_codigo = marcas_por_codigos(conexion, borrador["marcas"])
            marcas_borrador = [
                {
                    "codigo": codigo,
                    "tipo": por_codigo[codigo].get("tipo"),
                    "propietario": (por_codigo[codigo].get("propietarios") or {}).get("nombre"),
                    "imagen_url": url_imagen(por_codigo[codigo]["id"], por_codigo[codigo].get("archivo_png")),
                }
                for codigo in borrador["marcas"]
                if codigo in por_codigo
            ]
        borrador_para_plantilla = None
        if borrador:
            borrador_para_plantilla = {
                **borrador,
                "pdf_ruta": bool(operacion.get("borrador_pdf")),
                "pdf_nombre_original": operacion.get("borrador_pdf_nombre"),
                "dominante_ruta": bool(operacion.get("borrador_dominante_png")),
            }
        return render_template(
            "venta_imprimir.html",
            activo="ventas",
            operacion=operacion,
            perfil=perfil_actual(),
            borrador=borrador_para_plantilla,
            marcas_borrador=marcas_borrador,
        )

    @app.post("/ventas/<int:operacion_id>/borrador")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def guardar_borrador_venta_ruta(operacion_id: int):
        """Guarda el avance de la carga (marcas elegidas, PDF y Dominante ya
        puestos) para que la persona pueda cerrar esto y continuar más tarde
        -- desde cualquier computadora, no sólo desde este navegador."""
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")

        marcas = [c.strip() for c in request.form.getlist("marca_codigo") if c.strip()]
        archivo_pdf = request.files.get("pdf_guia")
        pdf_bytes = archivo_pdf.read() if archivo_pdf and archivo_pdf.filename else None
        pdf_nombre = archivo_pdf.filename if archivo_pdf and archivo_pdf.filename else None
        archivo_dominante = request.files.get("imagen_dominante")
        dominante_bytes = archivo_dominante.read() if archivo_dominante and archivo_dominante.filename else None

        guardar_borrador_venta(
            conexion, operacion_id, marcas, datetime.now(timezone.utc).isoformat(),
            pdf_bytes=pdf_bytes, pdf_nombre=pdf_nombre, dominante_bytes=dominante_bytes,
        )
        return redirect(url_for("imprimir_venta", operacion_id=operacion_id))

    @app.post("/ventas/<int:operacion_id>/borrador/descartar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def descartar_borrador_venta_ruta(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")
        eliminar_borrador_venta(conexion, operacion_id)
        return redirect(url_for("imprimir_venta", operacion_id=operacion_id))

    @app.get("/ventas/<int:operacion_id>/borrador/pdf")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def borrador_venta_pdf(operacion_id: int):
        conexion = conexion_actual()
        resultado = obtener_borrador_venta_pdf(conexion, operacion_id)
        if not resultado:
            return _registro_no_encontrado("Ese borrador no tiene un PDF guardado.")
        contenido, nombre = resultado
        return send_file(io.BytesIO(contenido), download_name=nombre, mimetype="application/pdf")

    @app.get("/ventas/<int:operacion_id>/borrador/dominante.png")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def borrador_venta_dominante(operacion_id: int):
        conexion = conexion_actual()
        contenido = obtener_borrador_venta_dominante(conexion, operacion_id)
        if not contenido:
            return _registro_no_encontrado("Ese borrador no tiene una Dominante guardada.")
        return send_file(io.BytesIO(contenido), download_name="dominante.png", mimetype="image/png")

    @app.post("/ventas/<int:operacion_id>/imprimir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def generar_venta_pdf(operacion_id: int):
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")

        archivo_pdf = request.files.get("pdf_guia")
        if not archivo_pdf or not archivo_pdf.filename:
            return _error_seguro("Subí el PDF de la guía descargado de SENACSA.", "sin archivo")

        codigos_elegidos = [c.strip() for c in request.form.getlist("marca_codigo") if c.strip()]
        if not codigos_elegidos:
            return _error_seguro("Elegí al menos una marca complementaria para la venta.", "sin marcas")

        por_codigo = marcas_por_codigos(conexion, codigos_elegidos)
        faltantes = [c for c in codigos_elegidos if c not in por_codigo]
        if faltantes:
            return _error_seguro(f"No se encontró la marca {faltantes[0]} en el catálogo.", "código inexistente")

        marcas_elegidas = [por_codigo[c] for c in codigos_elegidos]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_entrada = tmp_path / "entrada.pdf"
            archivo_pdf.save(pdf_entrada)

            imagen_dominante = None
            archivo_dominante = request.files.get("imagen_dominante")
            if archivo_dominante and archivo_dominante.filename:
                imagen_dominante = tmp_path / "dominante.png"
                archivo_dominante.save(imagen_dominante)

            rutas_complementarias, sin_imagen = _preparar_imagenes(conexion, marcas_elegidas, tmp_path)
            if sin_imagen:
                return _error_seguro(_mensaje_sin_imagen(sin_imagen), "marca sin imagen")

            salida = tmp_path / "salida.pdf"
            try:
                completar_venta(pdf_entrada, imagen_dominante, rutas_complementarias, salida)
            except Exception as exc:
                # Mismo criterio que en generar_guia_pdf: completar_venta ya
                # levanta mensajes propios pensados para mostrarse (son los
                # mismos que ya se ven, sin filtrar, en la vista previa en
                # vivo), así que mostrar ese detalle es más útil que el
                # genérico.
                return _error_seguro(str(exc) or "No se pudo generar el PDF de la venta.", exc)
            contenido_pdf = salida.read_bytes()

        return _entregar_documento(conexion, operacion, "venta", marcas_elegidas, contenido_pdf)

    @app.post("/ventas/<int:operacion_id>/previsualizar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def previsualizar_venta(operacion_id: int):
        """Vista previa en vivo del panel izquierdo: corre completar_venta
        sobre un archivo temporal con las marcas elegidas hasta el momento
        (nunca sobre el PDF final) y devuelve las páginas como imágenes,
        más el cupo libre y los datos leídos del encabezado. El PDF y la
        Dominante recién subidos quedan guardados como borrador para no
        tener que volver a mandarlos en cada llamada."""
        conexion = conexion_actual()
        operacion = obtener_operacion_por_id(conexion, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return jsonify({"error": "No se encontró esa venta."}), 404

        archivo_pdf = request.files.get("pdf_guia")
        pdf_bytes = archivo_pdf.read() if archivo_pdf and archivo_pdf.filename else None
        pdf_nombre = archivo_pdf.filename if archivo_pdf and archivo_pdf.filename else None
        archivo_dominante = request.files.get("imagen_dominante")
        dominante_bytes = archivo_dominante.read() if archivo_dominante and archivo_dominante.filename else None

        codigos = [c.strip() for c in request.form.getlist("marca_codigo") if c.strip()]
        if pdf_bytes or dominante_bytes or codigos != (operacion.get("borrador_venta") or {}).get("marcas", []):
            guardar_borrador_venta(
                conexion, operacion_id, codigos, datetime.now(timezone.utc).isoformat(),
                pdf_bytes=pdf_bytes, pdf_nombre=pdf_nombre, dominante_bytes=dominante_bytes,
            )
            operacion = obtener_operacion_por_id(conexion, operacion_id)

        if not pdf_bytes:
            guardado = obtener_borrador_venta_pdf(conexion, operacion_id)
            if not guardado:
                return jsonify({"error": "Subí primero el PDF de la guía."}), 400
            pdf_bytes = guardado[0]
        if not dominante_bytes:
            dominante_bytes = obtener_borrador_venta_dominante(conexion, operacion_id)

        por_codigo = marcas_por_codigos(conexion, codigos)
        marcas_elegidas = [por_codigo[c] for c in codigos if c in por_codigo]

        try:
            resultado = _previsualizar_estampado(conexion, operacion, marcas_elegidas, pdf_bytes, dominante_bytes)
        except Exception as exc:
            print(f"previsualizar_venta: {exc}")
            return jsonify({"error": "No se pudo generar la vista previa."}), 400
        return jsonify(resultado)
