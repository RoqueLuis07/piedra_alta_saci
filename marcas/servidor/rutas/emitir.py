"""Emitir PDF: una sola pantalla para completar la Guía de Traslado de SENACSA
con las marcas (Rubro 2 y Anexo). Sólo guías de venta.

La persona sube el PDF oficial y arma la lista de marcas dibujándolas a mano,
buscándolas en el catálogo o subiendo una imagen. Nada se guarda hasta que se
genera el documento final: en ese momento se crea la operación (si no existía),
las marcas dibujadas, y el PDF queda en el Archivo."""

from __future__ import annotations

import json
import tempfile
from datetime import date
from pathlib import Path

from flask import Response, jsonify, render_template, request, url_for

from marcas.pdf.guia import analizar_con_casillas, extraer_encabezado, extraer_respaldo
from marcas.servidor import cache_guias
from marcas.servidor.auth import conexion_actual, perfil_actual, requiere_rol, requiere_sesion
from marcas.servidor.consultas import (
    actualizar_marca_campos,
    crear_marca,
    crear_operacion,
    imagen_marca,
    marcas_de_operacion,
    marcas_por_codigos,
    obtener_operacion_por_id,
    subir_imagen_marca,
    url_imagen,
)
from marcas.servidor.consultas_escaneos import codigos_a_marcas, guias_de_respaldo
from marcas.servidor.utilidades import (
    _CAMPOS_MINIMOS_GUIA,
    _CAMPOS_MINIMOS_VENTA,
    _analizar_pdf,
    _entregar_documento,
    _estampar,
    _operacion_vacia,
    _renderizar_paginas,
)

MAX_MARCAS = 150
_FIRMA_PNG = b"\x89PNG\r\n\x1a\n"


class _PedidoInvalido(ValueError):
    """Error pensado para mostrarse tal cual a quien armó el pedido."""


def _json_pedido(nombre: str, por_defecto):
    crudo = request.form.get(nombre)
    if not crudo:
        return por_defecto
    try:
        return json.loads(crudo)
    except ValueError:
        raise _PedidoInvalido("El pedido llegó mal formado. Recargá la página y volvé a intentar.")


def _resolver_marca(conexion, spec: dict, operacion_id: int | None = None) -> dict:
    """Una marca del pedido: del catálogo (por código) o dibujada/subida en
    esta sesión. Devuelve {'bytes', 'marca' (dict o None), 'guardar'}."""
    tipo = spec.get("t")
    if tipo == "cat":
        codigo = str(spec.get("codigo", "")).strip()
        marca = marcas_por_codigos(conexion, [codigo]).get(codigo)
        if not marca:
            raise _PedidoInvalido(f"No se encontró la marca {codigo} en el catálogo.")
        if not marca.get("activo") and marca.get("operacion_id") != operacion_id:
            # Una marca oculta no se ofrece en el catálogo; sólo puede volver a
            # usarse en la operación a la que pertenece.
            raise _PedidoInvalido(f"La marca {codigo} está oculta del catálogo -- elegí otra.")
        if marca.get("estado") == "baja":
            raise _PedidoInvalido(
                f"La marca {codigo} está dada de baja (no es una marca válida) -- elegí otra."
            )
        contenido = imagen_marca(conexion, marca["id"], "png")
        if not contenido:
            raise _PedidoInvalido(
                f"La marca {codigo} todavía no tiene una imagen cargada -- quitala de la lista "
                "o subile una imagen antes de generar el PDF."
            )
        return {"bytes": contenido, "marca": marca, "guardar": False}
    if tipo == "dib":
        archivo = request.files.get(str(spec.get("archivo", "")))
        contenido = archivo.read() if archivo else b""
        if not contenido.startswith(_FIRMA_PNG):
            raise _PedidoInvalido("Una de las marcas dibujadas no llegó como imagen. Volvé a dibujarla.")
        return {"bytes": contenido, "marca": None, "guardar": bool(spec.get("guardar"))}
    raise _PedidoInvalido("Una de las marcas del pedido no es válida.")


def _leer_pedido(conexion) -> tuple[bytes, dict | None, list[dict]]:
    archivo_pdf = request.files.get("pdf_guia")
    pdf_bytes = archivo_pdf.read() if archivo_pdf and archivo_pdf.filename else b""
    if not pdf_bytes.startswith(b"%PDF"):
        raise _PedidoInvalido("Subí el PDF de la Guía de Traslado tal como lo descargaste de SENACSA.")
    orden = _json_pedido("orden", [])
    if not isinstance(orden, list) or len(orden) > MAX_MARCAS:
        raise _PedidoInvalido(f"Se pueden estampar hasta {MAX_MARCAS} marcas por documento.")
    operacion_id = request.form.get("operacion_id", type=int)
    dominante_spec = _json_pedido("dominante", None)
    dominante = _resolver_marca(conexion, dominante_spec, operacion_id) if dominante_spec else None
    complementarias = [_resolver_marca(conexion, spec, operacion_id) for spec in orden]
    return pdf_bytes, dominante, complementarias


def _operacion_para_emitir(conexion, perfil: dict) -> dict:
    """La operación existente (``operacion_id``) o una nueva con los datos que
    se leyeron del PDF y se confirmaron en pantalla."""
    existente = request.form.get("operacion_id", type=int)
    if existente:
        operacion = obtener_operacion_por_id(conexion, existente)
        if not operacion:
            raise _PedidoInvalido("No se encontró la operación a la que pertenece este documento.")
        return operacion

    tipo = "venta"      # Emitir PDF sólo completa guías de venta: las compras se cargan como guías escaneadas
    numero = request.form.get("numero_guia", "").strip()
    if numero:
        # Una guía de SENACSA tiene un número único: si ya se cargó, el nuevo PDF
        # se archiva en esa misma operación en vez de duplicarla.
        with conexion.cursor() as cur:
            cur.execute(
                "SELECT id FROM operaciones WHERE numero_guia = %s AND tipo_operacion = %s AND activo "
                "ORDER BY id LIMIT 1", (numero, tipo))
            fila = cur.fetchone()
        if fila:
            return obtener_operacion_por_id(conexion, fila["id"])
    campos: dict = {"tipo_operacion": tipo, "fecha": date.today().strftime("%d/%m/%Y")}
    for campo in ("numero_guia", "vendedor_nombre", "comprador_nombre"):
        campos[campo] = request.form.get(campo, "").strip() or None
    cantidad = request.form.get("cantidad_animales", "").strip()
    campos["cantidad_animales"] = int(cantidad) if cantidad.isdigit() else None
    minimos = _CAMPOS_MINIMOS_VENTA if tipo == "venta" else _CAMPOS_MINIMOS_GUIA
    if _operacion_vacia(campos, minimos):
        raise _PedidoInvalido("Completá al menos el N.º de guía, el vendedor o el comprador.")
    return obtener_operacion_por_id(conexion, crear_operacion(conexion, campos, perfil["id"]))


def registrar(app) -> None:
    @app.get("/emitir")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def emitir():
        conexion = conexion_actual()
        operacion = None
        marcas_previas: list[dict] = []
        operacion_id = request.args.get("operacion", type=int)
        if operacion_id:
            operacion = obtener_operacion_por_id(conexion, operacion_id)
            if operacion:
                for m in marcas_de_operacion(conexion, operacion_id, incluir_ocultas=True):
                    if m.get("tipo") == "dominante" or m.get("estado") == "baja":
                        continue
                    marcas_previas.append({
                        "codigo": m["codigo"], "descripcion": m.get("descripcion"),
                        "imagen_url": url_imagen(m["id"], m.get("archivo_png")),
                    })
        codigos = [c.strip() for c in (request.args.get("marcas") or "").split(",") if c.strip()][:MAX_MARCAS]
        for m in codigos_a_marcas(conexion, codigos):
            marcas_previas.append({
                "codigo": m["codigo"], "descripcion": m.get("descripcion"),
                "imagen_url": url_imagen(m["id"], m.get("archivo_png")),
            })
        return render_template(
            "emitir.html", activo="emitir", perfil=perfil_actual(),
            operacion=operacion, marcas_previas=marcas_previas,
        )

    @app.post("/emitir/analizar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def emitir_analizar():
        """Lee la guía UNA vez: cupo, encabezado y dónde cae cada marca. La pantalla arma después la vista
        previa sola, sin volver al servidor por cada marca."""
        archivo = request.files.get("pdf_guia")
        pdf_bytes = archivo.read() if archivo else b""
        if not pdf_bytes:
            return jsonify({"error": "Subí el PDF de la guía."}), 400
        clave = cache_guias.clave_de(pdf_bytes)
        analisis = cache_guias.obtener_analisis(clave)
        if analisis is None:
            with tempfile.TemporaryDirectory() as tmp:
                ruta = Path(tmp) / "guia.pdf"
                ruta.write_bytes(pdf_bytes)
                try:
                    analisis = analizar_con_casillas(ruta)
                except ValueError as exc:
                    return jsonify({"error": str(exc)}), 400
                except Exception:
                    return jsonify({"error": "El PDF está dañado o no es una Guía de Traslado de SENACSA."}), 400
                try:
                    analisis["encabezado"] = extraer_encabezado(ruta)
                except Exception:
                    analisis["encabezado"] = None
                try:
                    analisis["respaldo"] = extraer_respaldo(ruta)
                except Exception:
                    analisis["respaldo"] = []
            cache_guias.guardar(clave, pdf_bytes, analisis)
        # El respaldo del Rubro 4 se valida cada vez (no se guarda en la caché): lo cargado puede haber cambiado.
        respaldo = guias_de_respaldo(conexion_actual(), analisis.get("respaldo") or [])
        for fila in respaldo:
            if fila.get("primer_escaneo"):
                fila["url_guia"] = url_for("ver_guia_escaneada", numero=fila["numero"])
            for m in fila["marcas"]:
                m["imagen_url"] = url_imagen(m["id"], True)
                m["url_ficha"] = url_for("ver_marca", codigo=m["codigo"])
        return jsonify({**analisis, "respaldo_validado": respaldo, "clave": clave, "url_pagina": url_for("emitir_pagina", clave=clave, indice=0)})

    @app.get("/emitir/pagina/<clave>/<int:indice>.jpg")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def emitir_pagina(clave: str, indice: int):
        contenido = cache_guias.pagina_jpeg(clave, indice) if clave.isalnum() else None
        if contenido is None:
            return ("", 404)
        return Response(contenido, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/emitir/previsualizar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def emitir_previsualizar():
        """Vista previa en vivo: estampa sobre un archivo temporal (nunca se
        guarda nada) y devuelve las páginas tocadas, el cupo libre y los datos
        leídos del encabezado del PDF."""
        conexion = conexion_actual()
        try:
            pdf_bytes, dominante, complementarias = _leer_pedido(conexion)
        except _PedidoInvalido as exc:
            return jsonify({"error": str(exc)}), 400

        analisis = _analizar_pdf(pdf_bytes)
        error = analisis.pop("error", None)
        resultado = {"cupo": analisis.get("cupo"), "encabezado": analisis.get("encabezado"), "error": error}
        fuente, indices = pdf_bytes, [0]
        if (dominante or complementarias) and not error:
            try:
                fuente, info = _estampar(
                    pdf_bytes, dominante["bytes"] if dominante else None, [c["bytes"] for c in complementarias]
                )
                indices = info.get("paginas_modificadas") or [0]
                resultado["resumen"] = {
                    "rubro2": info.get("complementarias_en_rubro2", 0),
                    "anexo": info.get("complementarias_en_anexo", 0),
                    "dominante": bool(info.get("dominante_completada")),
                }
            except Exception as exc:
                resultado["error"] = str(exc) or "No se pudo estampar con esas marcas."
        resultado["paginas"] = _renderizar_paginas(fuente, indices)
        return jsonify(resultado)

    @app.post("/emitir/generar")
    @requiere_sesion
    @requiere_rol("administrador", "operador")
    def emitir_generar():
        conexion = conexion_actual()
        perfil = perfil_actual()
        try:
            pdf_bytes, dominante, complementarias = _leer_pedido(conexion)
            if not dominante and not complementarias:
                raise _PedidoInvalido("Agregá al menos una marca (dibujada, del catálogo o subida) antes de generar.")
            try:
                contenido_pdf, _info = _estampar(
                    pdf_bytes, dominante["bytes"] if dominante else None, [c["bytes"] for c in complementarias]
                )
            except Exception as exc:
                # completar_venta ya levanta mensajes pensados para mostrarse.
                raise _PedidoInvalido(str(exc) or "No se pudo generar el PDF.")

            operacion = _operacion_para_emitir(conexion, perfil)
            marcas_documento = []
            for tipo, item in [("dominante", dominante)] + [("complementaria", c) for c in complementarias]:
                if item is None:
                    continue
                if item["marca"] is None:
                    nueva = crear_marca(
                        conexion,
                        {"tipo": tipo, "operacion_id": operacion["id"], "descripcion": "Dibujada al emitir el PDF"},
                        perfil["id"],
                    )
                    subir_imagen_marca(conexion, nueva["id"], item["bytes"], "png")
                    if not item["guardar"]:
                        # Queda registrada en el documento, pero fuera del
                        # catálogo (oculta) salvo que se haya pedido guardarla.
                        actualizar_marca_campos(conexion, nueva["id"], {"activo": False})
                    item["marca"] = {**nueva, "tipo": tipo, "descripcion": "Dibujada al emitir el PDF"}
                # "tipo" en el documento es el papel con el que se estampó (una marca
                # del catálogo catalogada como dominante puede ir de complementaria).
                marcas_documento.append({**item["marca"], "tipo": tipo})
        except _PedidoInvalido as exc:
            conexion.rollback()
            return jsonify({"error": str(exc)}), 400
        except Exception as exc:
            conexion.rollback()
            print(f"emitir_generar: {exc}")
            return jsonify({"error": "No se pudo generar el documento. No se guardó nada."}), 500

        tipo_documento = "venta" if operacion.get("tipo_operacion") == "venta" else "guia"
        return _entregar_documento(conexion, operacion, tipo_documento, marcas_documento, contenido_pdf)
