"""Selector visual: se eligen las marcas con el mouse y sale el PDF.

Aplicación mínima, pensada para correr en la máquina de la oficina
(``python -m marcas web``). No tiene usuarios ni permisos: si se publica en
una red compartida, ponerla detrás de un proxy con autenticación.
"""

from __future__ import annotations

import io
import tempfile
from pathlib import Path

from flask import (
    Flask, abort, redirect, render_template, request, send_file, url_for
)

from marcas import config, db
from marcas.pdf import ItemPlanilla, generar_planilla
from marcas.pdf.guia import completar_guia


def _ruta_imagen(fila) -> Path | None:
    if not fila["archivo_png"]:
        return None
    ruta = Path(fila["archivo_png"])
    return ruta if ruta.is_absolute() else config.RAIZ / ruta


def crear_app(ruta_db: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["RUTA_DB"] = ruta_db

    @app.get("/")
    def inicio():
        busqueda = request.args.get("q") or None
        estado = request.args.get("estado") or None
        filas = db.listar_marcas(
            busqueda=busqueda, estado=estado, ruta_db=app.config["RUTA_DB"]
        )
        return render_template(
            "index.html",
            marcas=filas,
            busqueda=busqueda or "",
            estado=estado or "",
            estadisticas=db.estadisticas(app.config["RUTA_DB"]),
            columnas_def=config.PLANILLA_COLUMNAS,
            filas_def=config.PLANILLA_FILAS,
        )

    @app.get("/imagen/<codigo>.png")
    def imagen(codigo: str):
        filas = db.obtener_marcas([codigo], app.config["RUTA_DB"])
        if not filas:
            abort(404)
        ruta = _ruta_imagen(filas[0])
        if not ruta or not ruta.exists():
            abort(404)
        return send_file(ruta, mimetype="image/png")

    @app.post("/marca/<codigo>")
    def editar(codigo: str):
        db.actualizar_marca(
            codigo,
            app.config["RUTA_DB"],
            descripcion=request.form.get("descripcion") or None,
            estado=request.form.get("estado") or "activa",
        )
        return redirect(request.referrer or url_for("inicio"))

    @app.post("/guia")
    def guia():
        """Estampa las marcas elegidas sobre la guía oficial que sube el usuario.

        El formulario se descarga de la web de SENACSA con un número de orden
        distinto en cada descarga, así que se sube en cada trámite: el sistema
        no guarda ninguna copia como plantilla.
        """
        codigos = request.form.getlist("marca")
        archivo = request.files.get("guia_pdf")
        if not codigos or not archivo or not archivo.filename:
            return redirect(url_for("inicio"))

        filas = db.obtener_marcas(codigos, app.config["RUTA_DB"])
        imagenes = [_ruta_imagen(f) for f in filas]
        imagenes = [i for i in imagenes if i and i.exists()]

        with tempfile.TemporaryDirectory() as tmp:
            entrada = Path(tmp) / "guia.pdf"
            archivo.save(entrada)
            destino = Path(tmp) / "guia_completada.pdf"
            try:
                completar_guia(entrada, imagenes, destino)
            except (ValueError, FileNotFoundError) as exc:
                return f"<p>No se pudo completar la guía: {exc}</p>", 400
            datos = destino.read_bytes()

        nombre = Path(archivo.filename).stem
        return send_file(
            io.BytesIO(datos),
            mimetype="application/pdf",
            as_attachment=True,
            download_name=f"{nombre}_con_marcas.pdf",
        )

    @app.post("/planilla")
    def planilla():
        codigos = request.form.getlist("marca")
        if not codigos:
            return redirect(url_for("inicio"))
        filas = db.obtener_marcas(codigos, app.config["RUTA_DB"])
        items = [ItemPlanilla.desde_fila(f) for f in filas]
        titulo = request.form.get("titulo") or "Registro de marcas"
        subtitulo = request.form.get("subtitulo") or None

        with tempfile.TemporaryDirectory() as tmp:
            destino = Path(tmp) / "planilla.pdf"
            generar_planilla(
                items, destino,
                titulo=titulo,
                subtitulo=subtitulo,
                columnas=int(request.form.get("columnas") or config.PLANILLA_COLUMNAS),
                filas=int(request.form.get("filas") or config.PLANILLA_FILAS),
                apaisado=bool(request.form.get("apaisado")),
                casillas_vacias=int(request.form.get("vacias") or 0),
            )
            datos = destino.read_bytes()

        if request.form.get("guardar"):
            db.guardar_planilla(titulo, codigos, subtitulo, app.config["RUTA_DB"])

        return send_file(
            io.BytesIO(datos),
            mimetype="application/pdf",
            as_attachment=True,
            download_name="planilla_marcas.pdf",
        )

    return app


if __name__ == "__main__":       # pragma: no cover
    crear_app().run(debug=True)
