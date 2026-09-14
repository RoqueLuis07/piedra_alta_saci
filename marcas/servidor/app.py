"""Sistema multiusuario: login por Supabase Auth + búsqueda + panel de roles.

Es la app que corre en Railway (ver ``wsgi.py``). Toda consulta de datos
pasa por un cliente de Supabase con el token de la sesión, para que RLS
decida qué puede ver o tocar cada rol -- el código de acá no es la única
barrera, es la primera.
"""

from __future__ import annotations

import csv
import io
import os
import re
import tempfile
from pathlib import Path

from flask import Flask, Response, jsonify, redirect, render_template, request, send_file, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from postgrest.exceptions import APIError

from marcas.pdf.guia import completar_guia, completar_venta
from marcas.servidor.auth import (
    cerrar_sesion,
    cliente_actual,
    iniciar_sesion,
    perfil_actual,
    requiere_rol,
    requiere_sesion,
)
from marcas.servidor.consultas import (
    CAMPOS_MARCA_EDITABLES,
    CAMPOS_OPERACION_EDITABLES,
    CAMPOS_PROPIETARIO_EDITABLES,
    POR_PAGINA_MARCAS,
    POR_PAGINA_PROPIETARIOS,
    actualizar_propietario,
    buscar_marcas,
    cambio_pendiente_de,
    cambios_pendientes_detalle,
    crear_marca,
    crear_operacion,
    desglose_marcas,
    descargar_imagen_marca,
    estadisticas,
    exportar_marcas,
    exportar_operaciones,
    ficha_marca,
    historial_cambios_resueltos,
    listar_operaciones_paginado,
    listar_propietarios,
    marcas_a_revisar,
    marcas_de_operacion,
    marcas_de_propietario,
    marcas_por_codigos,
    marcas_por_vencer,
    nombres_usuarios,
    obtener_operacion_por_id,
    obtener_propietario,
    operaciones_de_propietario,
    proponer_cambio,
    ranking_participantes,
    resumen_mensual,
    subir_imagen_marca,
    ultimos_asientos,
    url_imagen,
)
from marcas.servidor.supa import cliente_anonimo

_CODIGO_VALIDO = re.compile(r"^[A-Za-z0-9._-]+$")


def _error_seguro(mensaje: str, exc: Exception):
    """El detalle real queda en los logs del servidor, nunca en la respuesta:
    puede traer nombres de columnas o de tablas que no hace falta mostrar."""
    print(f"{mensaje}: {exc}")
    return render_template("error_simple.html", titulo="No se pudo completar", mensaje=mensaje), 400


def _registro_no_encontrado(mensaje: str):
    return render_template("error_simple.html", titulo="No encontrado", mensaje=mensaje), 404


def crear_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not app.secret_key:
        raise RuntimeError("Falta FLASK_SECRET_KEY (variable de entorno) para firmar la sesión.")
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    # Sin este límite, cualquier sesión logueada podría mandar un archivo
    # enorme (imagen de marca o PDF de guía) y agotar memoria/disco del
    # servidor. 20 MB alcanza de sobra para una imagen o un PDF de guía.
    app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

    # Token oculto obligatorio en todo formulario POST -- protege contra
    # peticiones falsificadas desde otro sitio (CSRF). Queda disponible en
    # los templates como {{ csrf_token() }}, Flask-WTF lo registra solo.
    CSRFProtect(app)

    # Sin esto, el login no tiene freno: alguien podría probar contraseñas
    # sin límite. El almacenamiento en memoria alcanza para un solo proceso;
    # si el día de mañana corremos varios workers en paralelo, pasar a un
    # backend compartido (Redis) para que el límite sea real entre todos.
    limiter = Limiter(get_remote_address, app=app, default_limits=[])

    @app.get("/login")
    def login():
        if session.get("access_token"):
            return redirect(url_for("inicio"))
        return render_template("login.html", error=None)

    @app.post("/login")
    @limiter.limit("8 per minute; 30 per hour")
    def login_post():
        email = request.form.get("email", "").strip()
        contrasena = request.form.get("contrasena", "")
        try:
            resultado = cliente_anonimo().auth.sign_in_with_password(
                {"email": email, "password": contrasena}
            )
        except Exception:
            return render_template("login.html", error="Usuario o contraseña incorrectos."), 401
        iniciar_sesion(
            resultado.session.access_token,
            resultado.session.refresh_token,
            resultado.user.id,
            resultado.user.email,
        )
        return redirect(url_for("inicio"))

    @app.post("/logout")
    def logout():
        cerrar_sesion()
        return redirect(url_for("login"))

    @app.get("/cuenta-pendiente")
    def cuenta_pendiente():
        return render_template("cuenta_pendiente.html")

    @app.get("/recuperar")
    def recuperar():
        return render_template("recuperar.html", enviado=False, error=None)

    @app.post("/recuperar")
    @limiter.limit("5 per hour")
    def recuperar_post():
        email = request.form.get("email", "").strip()
        if email:
            destino = request.host_url.rstrip("/") + url_for("restablecer")
            try:
                cliente_anonimo().auth.reset_password_for_email(email, {"redirect_to": destino})
            except Exception:
                pass  # nunca revelar si el email existe o no
        return render_template("recuperar.html", enviado=True, error=None)

    @app.get("/restablecer")
    def restablecer():
        return render_template("restablecer.html", error=None)

    @app.post("/restablecer")
    @limiter.limit("10 per hour")
    def restablecer_post():
        access_token = request.form.get("access_token", "")
        refresh_token = request.form.get("refresh_token", "")
        nueva = request.form.get("nueva_contrasena", "")
        if not access_token or not refresh_token or len(nueva) < 6:
            return render_template(
                "restablecer.html",
                error="Datos incompletos o contraseña muy corta (mínimo 6 caracteres).",
            ), 400
        try:
            cliente = cliente_anonimo()
            cliente.auth.set_session(access_token, refresh_token)
            cliente.auth.update_user({"password": nueva})
        except Exception:
            return render_template(
                "restablecer.html", error="El enlace venció o no es válido. Pedí uno nuevo."
            ), 400
        return redirect(url_for("login"))

    @app.errorhandler(429)
    def demasiados_intentos(_exc):
        return render_template(
            "error_simple.html",
            titulo="Demasiados intentos",
            mensaje="Probaste demasiadas veces en poco tiempo. Esperá unos minutos y volvé a intentar.",
        ), 429

    @app.errorhandler(413)
    def archivo_demasiado_grande(_exc):
        return render_template(
            "error_simple.html",
            titulo="El archivo es demasiado grande",
            mensaje="El límite es 20 MB por archivo. Probá con una imagen o un PDF más liviano.",
        ), 413

    @app.errorhandler(CSRFError)
    def token_invalido(_exc):
        return render_template(
            "error_simple.html",
            titulo="La página venció",
            mensaje="El formulario tardó demasiado o se abrió en otra pestaña. Volvé atrás y probá de nuevo.",
        ), 400

    @app.errorhandler(404)
    def no_encontrado(_exc):
        # Cubre tanto una URL que no matchea ninguna ruta (ej. /guias/abc con
        # un id que no es numérico) como un abort(404) explícito -- antes de
        # esto, el primer caso mostraba la página cruda de Flask, en inglés.
        return render_template(
            "error_simple.html",
            titulo="No encontrado",
            mensaje="No existe la página o el registro que buscás. Puede que el enlace esté mal escrito o ya no exista.",
        ), 404

    @app.errorhandler(405)
    def metodo_no_permitido(_exc):
        return render_template(
            "error_simple.html",
            titulo="Esa acción no se puede hacer así",
            mensaje="Probaste acceder directo a una dirección pensada para un formulario. Usá los botones y enlaces del sitio en vez de escribir la URL a mano.",
        ), 405

    @app.context_processor
    def inyectar_pendientes():
        perfil = perfil_actual()
        if not perfil or perfil.get("rol") not in ("administrador", "operador"):
            return {}
        cliente = cliente_actual()
        if not cliente:
            return {}
        try:
            n = (
                cliente.table("cambios_pendientes")
                .select("id", count="exact")
                .eq("estado", "pendiente")
                .execute()
                .count
                or 0
            )
        except Exception:
            n = 0
        return {"pendientes_nav": n}

    @app.get("/")
    @requiere_sesion
    def inicio():
        cliente = cliente_actual()
        return render_template(
            "inicio.html",
            activo="inicio",
            perfil=perfil_actual(),
            stats=estadisticas(cliente),
            asientos=ultimos_asientos(cliente),
            a_revisar=marcas_a_revisar(cliente),
            a_vencer=marcas_por_vencer(cliente),
        )

    @app.get("/buscar")
    @requiere_sesion
    def buscar():
        cliente = cliente_actual()
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        tipo = request.args.get("tipo") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        resultados, total = buscar_marcas(cliente, texto, pagina=pagina, estado=estado, tipo=tipo)
        for fila in resultados:
            fila["imagen_url"] = url_imagen(cliente, fila.get("archivo_png"))
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
            perfil=perfil_actual(),
        )

    @app.get("/marcas/exportar.csv")
    @requiere_sesion
    def exportar_marcas_csv():
        cliente = cliente_actual()
        try:
            filas = exportar_marcas(
                cliente,
                texto=request.args.get("q") or None,
                estado=request.args.get("estado") or None,
                tipo=request.args.get("tipo") or None,
            )
        except Exception as exc:
            return _error_seguro("No se pudo generar el archivo.", exc)
        buffer = io.StringIO()
        escritor = csv.writer(buffer)
        escritor.writerow(["codigo", "tipo", "estado", "vence_en", "propietario", "documento", "numero_guia", "fecha"])
        for m in filas:
            propietario = m.get("propietarios") or {}
            operacion = m.get("operaciones") or {}
            escritor.writerow([
                m.get("codigo"), m.get("tipo"), m.get("estado"), m.get("vence_en") or "",
                propietario.get("nombre") or "", propietario.get("documento") or "",
                operacion.get("numero_guia") or "", operacion.get("fecha") or "",
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=marcas.csv"},
        )

    @app.get("/marcas/<codigo>")
    @requiere_sesion
    def ver_marca(codigo: str):
        cliente = cliente_actual()
        ficha = ficha_marca(cliente, codigo)
        if not ficha:
            return _registro_no_encontrado("No se encontró esa marca.")
        ficha["marca"]["imagen_url"] = url_imagen(cliente, ficha["marca"].get("archivo_png"))
        for acompanante in ficha["acompanantes"]:
            acompanante["imagen_url"] = url_imagen(cliente, acompanante.get("archivo_png"))
        nombres = nombres_usuarios(
            cliente, [ficha["marca"].get("creado_por"), ficha["marca"].get("actualizado_por")]
        )
        ficha["marca"]["creado_por_nombre"] = nombres.get(ficha["marca"].get("creado_por"))
        ficha["marca"]["actualizado_por_nombre"] = nombres.get(ficha["marca"].get("actualizado_por"))
        cambio_pendiente = cambio_pendiente_de(cliente, "marcas", ficha["marca"]["id"])
        return render_template(
            "marca_detalle.html",
            activo="buscar",
            ficha=ficha,
            cambio_pendiente=cambio_pendiente,
            perfil=perfil_actual(),
        )

    @app.post("/marcas/<int:marca_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_marca(marca_id: int):
        cliente = cliente_actual()
        perfil = perfil_actual()
        actual = cliente.table("marcas").select("*").eq("id", marca_id).maybe_single().execute()
        marca_actual = actual.data if actual else None
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

        for campo, extension in (("nuevo_png", "png"), ("nuevo_svg", "svg")):
            archivo = request.files.get(campo)
            if archivo and archivo.filename:
                nombre = subir_imagen_marca(cliente, marca_actual["codigo"], archivo.read(), extension)
                cambios[f"archivo_{extension}"] = nombre

        codigo_para_volver = marca_actual["codigo"]
        if cambios:
            try:
                if perfil["rol"] == "operador":
                    cliente.table("marcas").update(cambios).eq("id", marca_id).execute()
                    codigo_para_volver = cambios.get("codigo", codigo_para_volver)
                else:  # administrador -- pasa por modificación supervisada
                    valores_anteriores = {k: marca_actual.get(k) for k in cambios}
                    proponer_cambio(cliente, "marcas", marca_id, cambios, valores_anteriores, perfil["id"])
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_marca", codigo=codigo_para_volver))

    @app.get("/guias")
    @requiere_sesion
    def guias():
        cliente = cliente_actual()
        pagina = max(1, request.args.get("pagina", 1, type=int))
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        creada_desde = request.args.get("desde") or None
        creada_hasta = request.args.get("hasta") or None
        operaciones, total = listar_operaciones_paginado(
            cliente, pagina=pagina, texto=texto, estado=estado,
            creada_desde=creada_desde, creada_hasta=creada_hasta, tipo_operacion="compra",
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
            perfil=perfil_actual(),
        )

    @app.get("/ventas")
    @requiere_sesion
    def ventas():
        cliente = cliente_actual()
        pagina = max(1, request.args.get("pagina", 1, type=int))
        texto = request.args.get("q") or None
        estado = request.args.get("estado") or None
        creada_desde = request.args.get("desde") or None
        creada_hasta = request.args.get("hasta") or None
        operaciones, total = listar_operaciones_paginado(
            cliente, pagina=pagina, texto=texto, estado=estado,
            creada_desde=creada_desde, creada_hasta=creada_hasta, tipo_operacion="venta",
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
            perfil=perfil_actual(),
        )

    @app.get("/guias/exportar.csv")
    @requiere_sesion
    def exportar_guias_csv():
        cliente = cliente_actual()
        try:
            filas = exportar_operaciones(
                cliente,
                texto=request.args.get("q") or None,
                estado=request.args.get("estado") or None,
                creada_desde=request.args.get("desde") or None,
                creada_hasta=request.args.get("hasta") or None,
                tipo_operacion="compra",
            )
        except Exception as exc:
            return _error_seguro("No se pudo generar el archivo.", exc)
        buffer = io.StringIO()
        escritor = csv.writer(buffer)
        escritor.writerow([
            "numero_guia", "fecha", "vendedor_nombre", "vendedor_documento", "comprador_nombre",
            "comprador_documento", "cantidad_animales", "categoria_animales", "revisar",
            "guia_colisionada", "creado_en",
        ])
        for o in filas:
            escritor.writerow([
                o.get("numero_guia") or "", o.get("fecha") or "", o.get("vendedor_nombre") or "",
                o.get("vendedor_documento") or "", o.get("comprador_nombre") or "",
                o.get("comprador_documento") or "", o.get("cantidad_animales") if o.get("cantidad_animales") is not None else "",
                o.get("categoria_animales") or "", o.get("revisar") or "",
                "si" if o.get("guia_colisionada") else "no", o.get("creado_en") or "",
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=guias.csv"},
        )

    @app.get("/ventas/exportar.csv")
    @requiere_sesion
    def exportar_ventas_csv():
        cliente = cliente_actual()
        try:
            filas = exportar_operaciones(
                cliente,
                texto=request.args.get("q") or None,
                estado=request.args.get("estado") or None,
                creada_desde=request.args.get("desde") or None,
                creada_hasta=request.args.get("hasta") or None,
                tipo_operacion="venta",
            )
        except Exception as exc:
            return _error_seguro("No se pudo generar el archivo.", exc)
        buffer = io.StringIO()
        escritor = csv.writer(buffer)
        escritor.writerow([
            "numero_guia", "fecha", "vendedor_nombre", "vendedor_documento", "comprador_nombre",
            "comprador_documento", "cantidad_animales", "categoria_animales", "revisar",
            "guia_colisionada", "creado_en",
        ])
        for o in filas:
            escritor.writerow([
                o.get("numero_guia") or "", o.get("fecha") or "", o.get("vendedor_nombre") or "",
                o.get("vendedor_documento") or "", o.get("comprador_nombre") or "",
                o.get("comprador_documento") or "", o.get("cantidad_animales") if o.get("cantidad_animales") is not None else "",
                o.get("categoria_animales") or "", o.get("revisar") or "",
                "si" if o.get("guia_colisionada") else "no", o.get("creado_en") or "",
            ])
        return Response(
            buffer.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=ventas.csv"},
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
        cliente = cliente_actual()
        perfil = perfil_actual()
        campos = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            valor = request.form.get(campo, "").strip() or None
            if campo == "cantidad_animales" and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    valor = None
            campos[campo] = valor
        campos["tipo_operacion"] = "compra"
        try:
            operacion_id = crear_operacion(cliente, campos, perfil["id"])
        except Exception as exc:
            return _error_seguro("No se pudo crear la guía.", exc)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/ventas/nueva")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def crear_venta():
        cliente = cliente_actual()
        perfil = perfil_actual()
        campos = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            valor = request.form.get(campo, "").strip() or None
            if campo == "cantidad_animales" and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    valor = None
            campos[campo] = valor
        campos["tipo_operacion"] = "venta"
        try:
            operacion_id = crear_operacion(cliente, campos, perfil["id"])
        except Exception as exc:
            return _error_seguro("No se pudo crear la venta.", exc)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.get("/guias/<int:operacion_id>")
    @requiere_sesion
    def ver_guia(operacion_id: int):
        cliente = cliente_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")
        marcas = marcas_de_operacion(cliente, operacion_id)
        for m in marcas:
            m["imagen_url"] = url_imagen(cliente, m.get("archivo_png"))
        nombres = nombres_usuarios(cliente, [operacion.get("creado_por")])
        operacion["creado_por_nombre"] = nombres.get(operacion.get("creado_por"))
        cambio_pendiente = cambio_pendiente_de(cliente, "operaciones", operacion_id)
        return render_template(
            "guia_detalle.html",
            activo="ventas" if operacion.get("tipo_operacion") == "venta" else "guias",
            operacion=operacion,
            marcas=marcas,
            cambio_pendiente=cambio_pendiente,
            perfil=perfil_actual(),
        )

    @app.post("/guias/<int:operacion_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_guia(operacion_id: int):
        cliente = cliente_actual()
        perfil = perfil_actual()
        operacion_actual = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion_actual:
            return _registro_no_encontrado("No se encontró esa guía.")

        cambios: dict = {}
        for campo in CAMPOS_OPERACION_EDITABLES:
            if campo not in request.form:
                continue
            valor = request.form.get(campo, "").strip() or None
            if campo == "cantidad_animales" and valor is not None:
                try:
                    valor = int(valor)
                except ValueError:
                    continue
            if valor != operacion_actual.get(campo):
                cambios[campo] = valor

        if cambios:
            try:
                if perfil["rol"] == "operador":
                    cliente.table("operaciones").update(cambios).eq("id", operacion_id).execute()
                else:  # administrador -- pasa por modificación supervisada
                    valores_anteriores = {k: operacion_actual.get(k) for k in cambios}
                    proponer_cambio(cliente, "operaciones", operacion_id, cambios, valores_anteriores, perfil["id"])
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.post("/guias/<int:operacion_id>/marcas")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def agregar_marca_a_guia(operacion_id: int):
        cliente = cliente_actual()
        perfil = perfil_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")

        tipo = request.form.get("tipo") or "complementaria"
        descripcion = request.form.get("descripcion", "").strip() or None
        # Con "multiple" en el campo de archivo se puede cargar de una vez
        # todo un grupo de marcas complementarias (una por imagen); sin
        # ninguna imagen se agrega una sola marca en blanco, como antes.
        archivos = [a for a in request.files.getlist("imagen") if a and a.filename]

        try:
            for archivo in archivos or [None]:
                nueva = crear_marca(
                    cliente, {"tipo": tipo, "descripcion": descripcion, "operacion_id": operacion_id}, perfil["id"]
                )
                if archivo:
                    extension = "svg" if archivo.filename.lower().endswith(".svg") else "png"
                    nombre = subir_imagen_marca(cliente, nueva["codigo"], archivo.read(), extension)
                    cliente.table("marcas").update({f"archivo_{extension}": nombre}).eq("id", nueva["id"]).execute()
        except Exception as exc:
            return _error_seguro("No se pudo agregar la marca.", exc)

        return redirect(url_for("ver_guia", operacion_id=operacion_id))

    @app.get("/guias/<int:operacion_id>/imprimir")
    @requiere_sesion
    def imprimir_guia(operacion_id: int):
        cliente = cliente_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")
        marcas = marcas_de_operacion(cliente, operacion_id)
        for m in marcas:
            m["imagen_url"] = url_imagen(cliente, m.get("archivo_png"))
        return render_template(
            "guia_imprimir.html",
            activo="guias",
            operacion=operacion,
            marcas=marcas,
            perfil=perfil_actual(),
        )

    @app.post("/guias/<int:operacion_id>/imprimir")
    @requiere_sesion
    def generar_guia_pdf(operacion_id: int):
        cliente = cliente_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion:
            return _registro_no_encontrado("No se encontró esa guía.")

        archivo_pdf = request.files.get("pdf_guia")
        if not archivo_pdf or not archivo_pdf.filename:
            return "Subí el PDF de la guía descargado de SENACSA.", 400

        ids_elegidos = {int(v) for v in request.form.getlist("marca_id")}
        if not ids_elegidos:
            return "Seleccioná al menos una marca.", 400

        marcas = [m for m in marcas_de_operacion(cliente, operacion_id) if m["id"] in ids_elegidos]

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_entrada = tmp_path / "entrada.pdf"
            archivo_pdf.save(pdf_entrada)

            rutas_imagenes = []
            for m in marcas:
                contenido = descargar_imagen_marca(cliente, m.get("archivo_png"))
                if not contenido:
                    continue
                destino = tmp_path / f"{m['codigo']}.png"
                destino.write_bytes(contenido)
                rutas_imagenes.append(destino)

            if not rutas_imagenes:
                return "Ninguna de las marcas seleccionadas tiene una imagen disponible.", 400

            salida = tmp_path / "salida.pdf"
            try:
                completar_guia(pdf_entrada, rutas_imagenes, salida)
            except Exception as exc:
                return _error_seguro("No se pudo generar el PDF.", exc)
            contenido_pdf = salida.read_bytes()

        nombre_descarga = f"guia_{operacion.get('numero_guia') or operacion_id}.pdf"
        return send_file(
            io.BytesIO(contenido_pdf),
            as_attachment=True,
            download_name=nombre_descarga,
            mimetype="application/pdf",
        )

    @app.get("/marcas/buscar.json")
    @requiere_sesion
    def buscar_marcas_json():
        """Para el buscador de marcas complementarias al armar una Venta --
        busca en TODO el catálogo existente (no en una guía puntual), porque
        lo que se vende ya está cargado de antes."""
        cliente = cliente_actual()
        texto = request.args.get("q") or None
        resultados, total = buscar_marcas(cliente, texto, pagina=1, por_pagina=15)
        return jsonify([
            {
                "codigo": m["codigo"],
                "tipo": m.get("tipo"),
                "estado": m.get("estado"),
                "propietario": (m.get("propietarios") or {}).get("nombre"),
                "numero_guia": (m.get("operaciones") or {}).get("numero_guia"),
                "imagen_url": url_imagen(cliente, m.get("archivo_png")),
            }
            for m in resultados
        ])

    @app.get("/ventas/<int:operacion_id>/imprimir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def imprimir_venta(operacion_id: int):
        cliente = cliente_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")
        return render_template(
            "venta_imprimir.html", activo="ventas", operacion=operacion, perfil=perfil_actual(),
        )

    @app.post("/ventas/<int:operacion_id>/imprimir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def generar_venta_pdf(operacion_id: int):
        cliente = cliente_actual()
        operacion = obtener_operacion_por_id(cliente, operacion_id)
        if not operacion or operacion.get("tipo_operacion") != "venta":
            return _registro_no_encontrado("No se encontró esa venta.")

        archivo_pdf = request.files.get("pdf_guia")
        if not archivo_pdf or not archivo_pdf.filename:
            return _error_seguro("Subí el PDF de la guía descargado de SENACSA.", "sin archivo")

        codigos_elegidos = [c.strip() for c in request.form.getlist("marca_codigo") if c.strip()]
        if not codigos_elegidos:
            return _error_seguro("Elegí al menos una marca complementaria para la venta.", "sin marcas")

        por_codigo = marcas_por_codigos(cliente, codigos_elegidos)
        faltantes = [c for c in codigos_elegidos if c not in por_codigo]
        if faltantes:
            return _error_seguro(f"No se encontró la marca {faltantes[0]} en el catálogo.", "código inexistente")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pdf_entrada = tmp_path / "entrada.pdf"
            archivo_pdf.save(pdf_entrada)

            imagen_dominante = None
            archivo_dominante = request.files.get("imagen_dominante")
            if archivo_dominante and archivo_dominante.filename:
                imagen_dominante = tmp_path / "dominante.png"
                archivo_dominante.save(imagen_dominante)

            rutas_complementarias = []
            for codigo in codigos_elegidos:
                marca = por_codigo[codigo]
                contenido = descargar_imagen_marca(cliente, marca.get("archivo_png"))
                if not contenido:
                    continue
                destino = tmp_path / f"{codigo}.png"
                destino.write_bytes(contenido)
                rutas_complementarias.append(destino)

            if not rutas_complementarias:
                return _error_seguro("Ninguna de las marcas elegidas tiene una imagen PNG disponible.", "sin imágenes")

            salida = tmp_path / "salida.pdf"
            try:
                completar_venta(pdf_entrada, imagen_dominante, rutas_complementarias, salida)
            except Exception as exc:
                return _error_seguro("No se pudo generar el PDF de la venta.", exc)
            contenido_pdf = salida.read_bytes()

        nombre_descarga = f"venta_{operacion.get('numero_guia') or operacion_id}.pdf"
        return send_file(
            io.BytesIO(contenido_pdf),
            as_attachment=True,
            download_name=nombre_descarga,
            mimetype="application/pdf",
        )

    @app.get("/propietarios")
    @requiere_sesion
    def propietarios():
        cliente = cliente_actual()
        texto = request.args.get("q") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        resultados, total = listar_propietarios(cliente, texto, pagina=pagina)
        return render_template(
            "propietarios.html",
            activo="propietarios",
            resultados=resultados,
            total=total,
            pagina=pagina,
            por_pagina=POR_PAGINA_PROPIETARIOS,
            texto=texto or "",
            perfil=perfil_actual(),
        )

    @app.get("/propietarios/<int:propietario_id>")
    @requiere_sesion
    def ver_propietario(propietario_id: int):
        cliente = cliente_actual()
        propietario = obtener_propietario(cliente, propietario_id)
        if not propietario:
            return "Propietario no encontrado", 404
        marcas = marcas_de_propietario(cliente, propietario_id)
        for m in marcas:
            m["imagen_url"] = url_imagen(cliente, m.get("archivo_png"))
        operaciones = operaciones_de_propietario(cliente, propietario.get("documento"))
        return render_template(
            "propietario_detalle.html",
            activo="propietarios",
            propietario=propietario,
            marcas=marcas,
            operaciones=operaciones,
            perfil=perfil_actual(),
        )

    @app.post("/propietarios/<int:propietario_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def editar_propietario(propietario_id: int):
        cliente = cliente_actual()
        propietario_actual = obtener_propietario(cliente, propietario_id)
        if not propietario_actual:
            return "Propietario no encontrado", 404
        campos = {}
        for campo in CAMPOS_PROPIETARIO_EDITABLES:
            if campo not in request.form:
                continue
            valor = request.form.get(campo, "").strip() or None
            if valor != propietario_actual.get(campo):
                campos[campo] = valor
        if campos:
            try:
                actualizar_propietario(cliente, propietario_id, campos)
            except Exception as exc:
                return _error_seguro("No se pudo guardar el cambio.", exc)
        return redirect(url_for("ver_propietario", propietario_id=propietario_id))

    @app.get("/estadisticas")
    @requiere_sesion
    def estadisticas_vista():
        cliente = cliente_actual()
        top_vendedores, top_compradores = ranking_participantes(cliente)
        return render_template(
            "estadisticas.html",
            activo="estadisticas",
            desglose=desglose_marcas(cliente),
            mensual=resumen_mensual(cliente),
            top_vendedores=top_vendedores,
            top_compradores=top_compradores,
            perfil=perfil_actual(),
        )

    @app.get("/aprobaciones")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def aprobaciones():
        cliente = cliente_actual()
        vista = request.args.get("vista") or "pendientes"
        return render_template(
            "aprobaciones.html",
            activo="aprobaciones",
            vista=vista,
            cambios=cambios_pendientes_detalle(cliente) if vista == "pendientes" else [],
            historial=historial_cambios_resueltos(cliente) if vista == "historial" else [],
            perfil=perfil_actual(),
        )

    @app.post("/aprobaciones/<int:cambio_id>")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def resolver_cambio(cambio_id: int):
        cliente = cliente_actual()
        decision = request.form.get("decision")
        motivo = request.form.get("motivo") or None
        try:
            cliente.rpc(
                "resolver_cambio_pendiente",
                {"p_cambio_id": cambio_id, "p_decision": decision, "p_motivo": motivo},
            ).execute()
        except APIError as exc:
            # resolver_cambio_pendiente() sólo levanta textos pensados para
            # mostrarse tal cual (nunca nombres de columna sin filtrar) --
            # por ejemplo "quien propone el cambio no puede aprobarlo". Esconder
            # ese motivo detrás de un mensaje genérico no protege nada y sólo
            # confunde a quien está tratando de aprobar o rechazar el cambio.
            print(f"No se pudo resolver el cambio {cambio_id}: {exc}")
            return render_template(
                "error_simple.html", titulo="No se pudo resolver el cambio", mensaje=exc.message
            ), 400
        except Exception as exc:
            return _error_seguro("No se pudo resolver el cambio.", exc)
        return redirect(url_for("aprobaciones"))

    @app.get("/panel")
    @requiere_sesion
    @requiere_rol("administrador")
    def panel():
        cliente = cliente_actual()
        usuarios = cliente.table("perfiles").select("*").order("creado_en").execute().data
        return render_template(
            "panel.html", activo="panel", usuarios=usuarios, perfil=perfil_actual()
        )

    @app.post("/panel/usuarios/<usuario_id>")
    @requiere_sesion
    @requiere_rol("administrador")
    def actualizar_usuario(usuario_id: str):
        cliente = cliente_actual()
        campos = {}
        if request.form.get("rol"):
            campos["rol"] = request.form["rol"]
        if "activo" in request.form:
            campos["activo"] = request.form["activo"] == "1"
        if campos:
            cliente.table("perfiles").update(campos).eq("id", usuario_id).execute()
        return redirect(url_for("panel"))

    return app


if __name__ == "__main__":       # pragma: no cover
    crear_app().run(debug=True)
