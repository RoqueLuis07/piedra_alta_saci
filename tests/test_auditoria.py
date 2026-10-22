"""Auditoría general: ninguna pantalla falla y cada rol sólo puede lo que le toca."""

import pytest

pytest.importorskip("pixeltable_pgserver")


def _rutas_get(app):
    for r in app.url_map.iter_rules():
        if "GET" in r.methods and not r.arguments and r.endpoint != "static":
            yield r.rule


def test_todas_las_pantallas_responden(cliente):
    from flask import current_app  # noqa: F401

    app = cliente.application
    fallas = []
    for ruta in sorted(set(_rutas_get(app))):
        if ruta in ("/logout",):
            continue
        r = cliente.get(ruta)
        if r.status_code >= 500 or r.status_code == 404:
            fallas.append((ruta, r.status_code))
    assert not fallas, fallas


def test_pantallas_con_datos_reales(cliente):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT id, codigo FROM marcas ORDER BY id LIMIT 1")
        marca = cur.fetchone()
        cur.execute("SELECT id FROM escaneos ORDER BY id LIMIT 1")
        escaneo = cur.fetchone()
        cur.execute("SELECT id FROM operaciones ORDER BY id LIMIT 1")
        operacion = cur.fetchone()
        cur.execute("SELECT numero_guia FROM escaneos WHERE numero_guia IS NOT NULL LIMIT 1")
        guia = cur.fetchone()
    db.devolver_conexion(conexion)
    urls = []
    if marca:
        urls += [f"/marcas/{marca['codigo']}", f"/marcas/{marca['id']}/imagen.png"]
    if escaneo:
        urls += [f"/escaneos/pagina/{escaneo['id']}", f"/escaneos/imagen/{escaneo['id']}.jpg", f"/escaneos/miniatura/{escaneo['id']}.jpg"]
    if operacion:
        urls += [f"/guias/{operacion['id']}"]
    if guia:
        urls += [f"/escaneos/guia/{guia['numero_guia']}"]
    for u in urls:
        assert cliente.get(u).status_code < 500, u


def _cliente_con_rol(rol):
    from marcas.servidor import db
    from marcas.servidor.auth import encriptar_contrasena
    from marcas.servidor.consultas import crear_usuario

    conexion = db.obtener_conexion()
    email = f"{rol}@prueba.test"
    crear_usuario(conexion, email, encriptar_contrasena("clave-de-prueba"), rol.title(), rol, True)
    conexion.commit()
    db.devolver_conexion(conexion)
    return email


@pytest.mark.parametrize("rol", ["consulta", "operador"])
def test_permisos_por_rol(cliente, rol):
    app = cliente.application
    email = _cliente_con_rol(rol)
    c = app.test_client()
    c.environ_base["wsgi.url_scheme"] = "https"
    assert c.post("/login", data={"email": email, "contrasena": "clave-de-prueba"}).status_code == 302
    # lo que sólo hace el administrador
    for ruta in ("/revision/eliminar", "/guias/eliminar"):
        assert c.post(ruta, json={"ids": [1]}).status_code in (401, 403, 302), (rol, ruta)
    assert c.post("/guias/1/eliminar").status_code in (401, 403, 302)
    if rol == "consulta":
        # sólo lectura: no emite, no sube, no edita ni guarda marcas
        assert c.get("/emitir").status_code in (302, 401, 403)
        assert c.get("/escaneos/subir").status_code in (302, 401, 403)
        assert c.post("/escaneos/pagina/1/marcas", json={"imagen": "x"}).status_code in (302, 401, 403)
        assert c.post("/emitir/analizar").status_code in (302, 401, 403)
    else:
        assert c.get("/emitir").status_code == 200
        assert c.get("/escaneos/subir").status_code == 200
    # y todos pueden ver el catálogo y las guías
    for ruta in ("/buscar", "/buscar/dibujo", "/escaneos", "/guias", "/archivo"):
        assert c.get(ruta).status_code == 200, (rol, ruta)
