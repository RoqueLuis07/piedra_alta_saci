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


def test_correcciones_del_informe_qa(cliente):
    import io

    import psycopg2
    from PIL import Image

    from marcas.servidor import db

    # D6: fechas inválidas no rompen los listados
    assert cliente.get("/guias?desde=bad").status_code == 200
    assert cliente.get("/ventas?hasta=nada").status_code == 200
    # D1: el respaldo completo se descarga aunque haya columnas bytea (imagen, huella)
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        buf = io.BytesIO()
        Image.new("RGBA", (40, 40), (0, 0, 0, 255)).save(buf, "PNG")
        cur.execute("INSERT INTO marcas (tipo, archivo_png, huella) VALUES ('complementaria', %s, %s) RETURNING id",
                    (psycopg2.Binary(buf.getvalue()), psycopg2.Binary(b"\x01\x02\x03")))
        cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion) VALUES ('QA-D4', 'venta') RETURNING id")
        op = cur.fetchone()["id"]
    conexion.commit()
    db.devolver_conexion(conexion)
    assert cliente.get("/panel/respaldo.zip").status_code == 200
    # D4: no se agregan marcas con archivos inválidos ni una segunda dominante
    r = cliente.post(f"/guias/{op}/marcas", data={"tipo": "complementaria", "imagen": (io.BytesIO(b"no soy una imagen"), "x.png")},
                     content_type="multipart/form-data")
    assert r.status_code >= 400
    ok = cliente.post(f"/guias/{op}/marcas", data={"tipo": "dominante", "imagen": (io.BytesIO(buf.getvalue()), "d.png")},
                      content_type="multipart/form-data")
    assert ok.status_code == 302
    dup = cliente.post(f"/guias/{op}/marcas", data={"tipo": "dominante", "imagen": (io.BytesIO(buf.getvalue()), "d2.png")},
                       content_type="multipart/form-data")
    assert dup.status_code >= 400
    assert "Marcas de esta guía (1)" in cliente.get(f"/guias/{op}").get_data(as_text=True)
    # D5: «Sacadas por el detector» no muestra las bajas hechas a mano
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("INSERT INTO marcas (tipo, estado) VALUES ('complementaria', 'baja') RETURNING codigo")
        a_mano = cur.fetchone()["codigo"]
        cur.execute("INSERT INTO marcas (tipo, estado, motivo_calidad) VALUES ('complementaria', 'baja', 'no_es_marca:anulacion (99%)') RETURNING codigo")
        detector = cur.fetchone()["codigo"]
    conexion.commit()
    db.devolver_conexion(conexion)
    html = cliente.get("/revision?ver=bajas").get_data(as_text=True)
    assert detector in html and a_mano not in html
    assert a_mano in cliente.get("/revision?ver=todas").get_data(as_text=True)


def test_validaciones_de_entrada(cliente):
    # D8: guía con cantidad negativa o duplicada se rechaza con un mensaje claro
    base = {"numero_guia": "UX-100", "vendedor_nombre": "V", "comprador_nombre": "C", "cantidad_animales": "5"}
    assert cliente.post("/guias/nueva", data=base).status_code == 302
    dup = cliente.post("/guias/nueva", data=base)
    assert dup.status_code >= 400 and "Ya hay una guía" in dup.get_data(as_text=True)
    neg = cliente.post("/guias/nueva", data={**base, "numero_guia": "UX-101", "cantidad_animales": "-3"})
    assert neg.status_code >= 400 and "negativa" in neg.get_data(as_text=True)
    # D9: email inválido al crear usuario
    r = cliente.post("/panel/usuarios", data={"email": "noesmail", "nombre": "X", "contrasena": "clave-larga-1", "rol": "consulta"})
    assert "email no es válido" in r.get_data(as_text=True) or r.status_code >= 400
    # D10: número de guía con «/»
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("INSERT INTO escaneos (carpeta, archivo, orden, numero_guia) VALUES ('subidas', 'barra.jpg', 0, '12/34')")
    conexion.commit()
    db.devolver_conexion(conexion)
    assert cliente.get("/escaneos/guia/12/34").status_code == 200
    # D13: página fuera de rango vuelve a la última; favicon y página de error en español
    assert cliente.get("/buscar?pagina=99", follow_redirects=True).status_code == 200
    assert cliente.get("/favicon.ico").status_code == 200
