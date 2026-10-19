"""Archivo de documentos emitidos: contra un Postgres real (embebido).

Se salta solo si no está instalado ``pixeltable-pgserver`` (requirements-dev).
Cubre el flujo completo: login -> crear guía -> agregar marca con imagen ->
generar el PDF -> queda guardado y se puede ver/descargar desde /archivo.
"""

import io
import re
import sys
from pathlib import Path

import pytest

pg = pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generar_guia_demo import generar as generar_guia  # noqa: E402


@pytest.fixture(scope="module")
def cliente(tmp_path_factory, monkeypatch_module):
    servidor = pg.get_server(tmp_path_factory.mktemp("pgdata"))
    monkeypatch_module.setenv("FLASK_SECRET_KEY", "prueba")

    from marcas.servidor import db

    monkeypatch_module.setattr(db, "DATABASE_URL", servidor.get_uri())
    monkeypatch_module.setattr(db, "_pool", None)
    # El Postgres embebido no trae pgcrypto; gen_random_uuid() ya es nativo
    # desde Postgres 13, así que alcanza con sacar esa línea para probar.
    sql = (Path(db.__file__).parent / "schema.sql").read_text(encoding="utf-8")
    sql = sql.replace("create extension if not exists pgcrypto;", "")
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute(sql)
    conexion.commit()
    db.devolver_conexion(conexion)

    from marcas.servidor.app import crear_app
    from marcas.servidor.auth import encriptar_contrasena
    from marcas.servidor.consultas import crear_usuario

    conexion = db.obtener_conexion()
    crear_usuario(conexion, "admin@prueba.test", encriptar_contrasena("clave-de-prueba"), "Admin Prueba", "administrador", True)
    conexion.commit()
    db.devolver_conexion(conexion)

    app = crear_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    c = app.test_client()
    c.environ_base["wsgi.url_scheme"] = "https"
    r = c.post("/login", data={"email": "admin@prueba.test", "contrasena": "clave-de-prueba"})
    assert r.status_code == 302
    yield c
    servidor.cleanup()


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def _png_marca() -> bytes:
    from PIL import Image, ImageDraw

    imagen = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(40, 200), (128, 40), (216, 200)], fill=(0, 0, 0, 255), width=14)
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _crear_guia(cliente, numero: str) -> int:
    r = cliente.post(
        "/guias/nueva",
        data={"numero_guia": numero, "fecha": "20/10/2026", "vendedor_nombre": "Vendedor Prueba",
              "comprador_nombre": "Comprador Prueba", "cantidad_animales": "12"},
    )
    assert r.status_code == 302
    return int(re.search(r"/guias/(\d+)", r.headers["Location"]).group(1))


def _agregar_marca(cliente, operacion_id: int, con_imagen: bool) -> int:
    datos = {"tipo": "complementaria", "descripcion": "marca de prueba"}
    if con_imagen:
        datos["imagen"] = (io.BytesIO(_png_marca()), "marca.png")
    r = cliente.post(f"/guias/{operacion_id}/marcas", data=datos, content_type="multipart/form-data")
    assert r.status_code == 302
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT max(id) AS id FROM marcas WHERE operacion_id = %s", (operacion_id,))
            return cur.fetchone()["id"]
    finally:
        db.devolver_conexion(conexion)


def test_generar_guia_queda_archivada(cliente, tmp_path):
    operacion_id = _crear_guia(cliente, "ARCH-0001")
    marca_id = _agregar_marca(cliente, operacion_id, con_imagen=True)
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0001", incluir_rubro2=True)

    r = cliente.post(
        f"/guias/{operacion_id}/imprimir",
        data={"marca_id": str(marca_id), "pdf_guia": (io.BytesIO(pdf.read_bytes()), "guia.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert r.mimetype == "application/pdf"
    emitido = r.get_data()
    assert emitido.startswith(b"%PDF")

    lista = cliente.get("/archivo?q=ARCH-0001").get_data(as_text=True)
    assert "ARCH-0001" in lista and "Vendedor Prueba" in lista and "Comprador Prueba" in lista
    documento_id = int(re.search(r"/archivo/(\d+)/pdf", lista).group(1))

    detalle = cliente.get(f"/archivo/{documento_id}").get_data(as_text=True)
    assert "Vendedor Prueba" in detalle and "marca de prueba" in detalle

    # el PDF guardado es exactamente el que se entregó
    assert cliente.get(f"/archivo/{documento_id}/pdf").get_data() == emitido

    # filtros: por tipo y por fecha futura
    assert "ARCH-0001" not in cliente.get("/archivo?tipo=venta&q=ARCH-0001").get_data(as_text=True).split("<tbody>")[1]
    assert "/pdf" not in cliente.get("/archivo?desde=2999-01-01").get_data(as_text=True)


def test_marca_sin_imagen_no_genera_ni_archiva(cliente, tmp_path):
    operacion_id = _crear_guia(cliente, "ARCH-0002")
    marca_id = _agregar_marca(cliente, operacion_id, con_imagen=False)
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0002", incluir_rubro2=True)

    r = cliente.post(
        f"/guias/{operacion_id}/imprimir",
        data={"marca_id": str(marca_id), "pdf_guia": (io.BytesIO(pdf.read_bytes()), "guia.pdf")},
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    lista = cliente.get("/archivo?q=ARCH-0002").get_data(as_text=True)
    assert "Todavía no se emitió ningún documento." in lista and "/pdf" not in lista


def test_rutas_eliminadas_no_existen(cliente):
    assert cliente.get("/estadisticas").status_code == 404
    assert cliente.get("/aprobaciones").status_code == 404
