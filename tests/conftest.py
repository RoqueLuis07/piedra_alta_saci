import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


# Fixtures compartidas por las pruebas que necesitan un Postgres real (embebido):
# un servidor con el esquema aplicado, un administrador y un cliente con la sesión iniciada.
@pytest.fixture(scope="module")
def cliente(tmp_path_factory, monkeypatch_module):
    pg = pytest.importorskip("pixeltable_pgserver")
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
