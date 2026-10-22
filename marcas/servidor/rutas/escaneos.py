"""Guías escaneadas: todas las páginas de los documentos históricos, agrupadas por N.º de guía,
con lo leído por OCR (editable), el texto completo para buscar y las marcas que salieron de cada una."""

from __future__ import annotations

from flask import Response, jsonify, redirect, render_template, request, url_for

import base64
import binascii

from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import crear_marca, subir_imagen_marca, url_imagen
from marcas.servidor.extraer_marca import normalizar_marca
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
from marcas.servidor.subida_guias import MAX_PAGINAS, ErrorSubida, guardar_paginas, nuevo_lote, preparar
from marcas.servidor.utilidades import _registro_no_encontrado

MAX_MARCAS_A_EMITIR = 150


def _entero_o_none(valor: str | None) -> int | None:
    valor = (valor or "").strip()
    return int(valor) if valor.isdigit() else None


def registrar(app) -> None:
    @app.get("/escaneos/subir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def subir_guias_pantalla():
        return render_template("escaneo_subir.html", activo="escaneos", perfil=perfil_actual(), formatos=FORMATOS, max_paginas=MAX_PAGINAS)

    @app.post("/escaneos/subir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def subir_guias():
        """Sube uno o varios PDF / fotos. Cada PDF es una guía (su N.º de orden se lee solo); las fotos
        sueltas usan el N.º de guía escrito en el formulario."""
        conexion = conexion_actual()
        numero_forzado = (request.form.get("numero_guia") or "").strip()[:40] or None
        formato = request.form.get("formato") or None
        if formato not in FORMATOS:
            formato = None
        archivos = [a for a in request.files.getlist("archivos") if a and a.filename]
        if not archivos:
            return render_template("escaneo_subir.html", activo="escaneos", perfil=perfil_actual(), formatos=FORMATOS,
                                   max_paginas=MAX_PAGINAS, error="Elegí al menos un archivo."), 400
        resultados, errores, restante = [], [], MAX_PAGINAS
        try:
            for archivo in archivos:
                if restante <= 0:
                    errores.append(f"«{archivo.filename}»: se llegó al máximo de {MAX_PAGINAS} páginas por carga; subilo aparte.")
                    continue
                try:
                    paginas, detectado = preparar(archivo.filename, archivo.read(), restante)
                except ErrorSubida as exc:
                    errores.append(str(exc))
                    continue
                numero = numero_forzado or detectado
                fuente = "manual" if numero_forzado else ("pdf" if detectado else None)
                ids = guardar_paginas(conexion, paginas, numero, fuente, formato, nuevo_lote(), 0, archivo.filename)
                restante -= len(paginas)
                resultados.append({"nombre": archivo.filename, "paginas": len(ids), "numero": numero, "primero": ids[0]})
            conexion.commit()
        except Exception as exc:
            conexion.rollback()
            print(f"subir_guias: {exc}")
            return render_template("escaneo_subir.html", activo="escaneos", perfil=perfil_actual(), formatos=FORMATOS,
                                   max_paginas=MAX_PAGINAS, error="No se pudo guardar la carga. No quedó nada a medias; probá de nuevo."), 500
        if len(resultados) == 1 and not errores:
            r = resultados[0]
            return redirect(url_for("ver_guia_escaneada", numero=r["numero"]) if r["numero"] else url_for("ver_escaneo", escaneo_id=r["primero"]))
        return render_template("escaneo_subir.html", activo="escaneos", perfil=perfil_actual(), formatos=FORMATOS,
                               max_paginas=MAX_PAGINAS, resultados=resultados, errores=errores)

    @app.get("/escaneos")
    @requiere_sesion
    def escaneos():
        conexion = conexion_actual()
        vista = "sin_numero" if request.args.get("vista") == "sin_numero" else "guias"
        texto = (request.args.get("q") or "").strip() or None
        formato = request.args.get("formato") or None
        pagina = max(1, request.args.get("pagina", 1, type=int))
        carpeta = request.args.get("carpeta", type=int)
        carpeta = carpeta if carpeta in (1, 2) else None
        if vista == "sin_numero":
            filas, total = paginas_sin_numero(conexion, texto, pagina, carpeta)
            por_pagina = POR_PAGINA_SIN_NUMERO
        else:
            filas, total = listar_guias_escaneadas(conexion, texto, formato, pagina, carpeta)
            por_pagina = POR_PAGINA_GUIAS
        return render_template(
            "escaneos.html", activo="escaneos", perfil=perfil_actual(), vista=vista, filas=filas, total=total,
            pagina=pagina, por_pagina=por_pagina, texto=texto or "", formato=formato or "", carpeta=carpeta or "",
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

    @app.post("/escaneos/pagina/<int:escaneo_id>/marcas")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def guardar_marca_de_pagina(escaneo_id: int):
        """Guarda como marca un recorte o un calco hecho sobre esta página: {imagen: dataURL PNG, tipo}.
        Queda en el catálogo, vinculada a la página y a la guía de origen."""
        conexion = conexion_actual()
        datos = request.get_json(silent=True) or {}
        tipo = datos.get("tipo") if datos.get("tipo") in ("dominante", "complementaria") else "complementaria"
        texto = (datos.get("imagen") or "").split(",", 1)[-1]
        try:
            crudo = base64.b64decode(texto, validate=True)
        except (binascii.Error, ValueError):
            crudo = b""
        png = normalizar_marca(crudo[: 12 * 1024 * 1024]) if crudo else None
        if png is None:
            return jsonify({"error": "No se ve ningún trazo en lo que elegiste. Marcá un recuadro más grande o dibujá con más fuerza."}), 400
        pagina = obtener_escaneo(conexion, escaneo_id)
        if not pagina:
            return jsonify({"error": "No se encontró la página."}), 404
        try:
            nueva = crear_marca(conexion, {
                "tipo": tipo, "estado": "activa", "escaneo_id": escaneo_id, "numero_guia": pagina.get("numero_guia"),
                "origen_etiqueta": pagina.get("etiqueta"), "descripcion": "Sacada de una guía cargada",
            }, perfil_actual()["id"])
            subir_imagen_marca(conexion, nueva["id"], png, "png")
            with conexion.cursor() as cur:
                cur.execute("UPDATE escaneos SET n_marcas = n_marcas + 1 WHERE id = %s", (escaneo_id,))
        except Exception as exc:
            conexion.rollback()
            print(f"guardar_marca_de_pagina: {exc}")
            return jsonify({"error": "No se pudo guardar la marca."}), 500
        return jsonify({"codigo": nueva["codigo"], "id": nueva["id"], "tipo": tipo,
                        "imagen_url": url_imagen(nueva["id"], True), "ficha": url_for("ver_marca", codigo=nueva["codigo"])})

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
