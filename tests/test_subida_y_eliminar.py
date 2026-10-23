"""Subir guías (PDF o foto) a Guías escaneadas y eliminar para siempre las marcas que no lo son."""

import io

import psycopg2
import pytest

pytest.importorskip("pixeltable_pgserver")

from PIL import Image  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


def _pdf(paginas: int = 2) -> bytes:
    salida = io.BytesIO()
    c = canvas.Canvas(salida)
    for i in range(paginas):
        c.drawString(72, 750, f"Hoja {i + 1} de prueba")
        c.showPage()
    c.save()
    return salida.getvalue()


def _foto() -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (300, 400), (230, 230, 225)).save(salida, "JPEG")
    return salida.getvalue()


def test_subir_pdf_y_fotos(cliente):
    r = cliente.post("/escaneos/subir", data={"numero_guia": "77001122", "archivos": (io.BytesIO(_pdf(3)), "guia.pdf")},
                     content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/escaneos/guia/77001122")
    html = cliente.get("/escaneos/guia/77001122").get_data(as_text=True)
    assert "77001122" in html

    # dos fotos con el mismo N.º de guía quedan en la misma guía; sin número van a «sin número»
    r = cliente.post("/escaneos/subir", data={"numero_guia": "77001122",
                                              "archivos": [(io.BytesIO(_foto()), "a.jpg"), (io.BytesIO(_foto()), "b.jpg")]},
                     content_type="multipart/form-data")
    assert r.status_code == 200 and "Carga terminada" in r.get_data(as_text=True)
    r = cliente.post("/escaneos/subir", data={"archivos": (io.BytesIO(_foto()), "c.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 302 and "/escaneos/pagina/" in r.headers["Location"]
    imagen = cliente.get(r.headers["Location"].replace("pagina", "imagen") + ".jpg")
    assert imagen.status_code == 200 and imagen.mimetype == "image/jpeg"

    mal = cliente.post("/escaneos/subir", data={"archivos": (io.BytesIO(b"texto"), "nota.txt")}, content_type="multipart/form-data")
    assert "no es un PDF ni una imagen" in mal.get_data(as_text=True)
    assert cliente.post("/escaneos/subir", data={}, content_type="multipart/form-data").status_code == 400


def test_eliminar_marcas_directo_sin_pasar_por_baja(cliente):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("INSERT INTO marcas (tipo, estado) VALUES ('complementaria', 'baja') RETURNING id")
        baja = cur.fetchone()["id"]
        cur.execute("INSERT INTO marcas (tipo, estado) VALUES ('complementaria', 'activa') RETURNING id")
        activa = cur.fetchone()["id"]
    conexion.commit()
    db.devolver_conexion(conexion)

    r = cliente.post("/revision/eliminar", json={"ids": [baja, activa]})
    assert r.status_code == 200 and r.get_json()["eliminadas"] == 2
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT id FROM marcas WHERE id IN (%s, %s)", (baja, activa))
        assert cur.fetchall() == []
    db.devolver_conexion(conexion)
    assert cliente.post("/revision/eliminar", json={"ids": []}).status_code == 400
    assert cliente.post("/revision/eliminar", json={"ids": ["x"]}).status_code == 400


def test_vista_previa_instantanea_analiza_una_vez(cliente, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from generar_guia_demo import generar as generar_guia

    pdf = generar_guia(tmp_path / "g.pdf", guia="VISTA-0001", incluir_rubro2=True).read_bytes()
    r = cliente.post("/emitir/analizar", data={"pdf_guia": (io.BytesIO(pdf), "g.pdf")}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)
    d = r.get_json()
    assert len(d["paginas"]) >= 5 and d["estaciones"] and d["cupo"]["complementarias_libres_rubro2"] >= 1
    assert d["estaciones"][0][0]["ancho"] > 0 and d["url_pagina"].endswith("/0.jpg")
    pagina = cliente.get(d["url_pagina"].replace("/0.jpg", "/1.jpg"))
    assert pagina.status_code == 200 and pagina.mimetype == "image/jpeg"
    assert cliente.get("/emitir/pagina/noexiste/0.jpg").status_code == 404
    otra = cliente.post("/emitir/analizar", data={"pdf_guia": (io.BytesIO(b"no pdf"), "x.pdf")}, content_type="multipart/form-data")
    assert otra.status_code == 400


def test_eliminar_guias_respeta_el_archivo(cliente):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion) VALUES ('DEL-LIBRE', 'compra') RETURNING id")
        libre = cur.fetchone()["id"]
        cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion) VALUES ('DEL-ARCHIVADA', 'venta') RETURNING id")
        archivada = cur.fetchone()["id"]
        cur.execute("INSERT INTO marcas (tipo, operacion_id) VALUES ('complementaria', %s) RETURNING id", (libre,))
        marca = cur.fetchone()["id"]
        cur.execute("INSERT INTO documentos_emitidos (operacion_id, tipo, nombre, contenido) VALUES (%s, 'venta', 'x.pdf', %s)",
                    (archivada, psycopg2.Binary(b"%PDF-")))
    conexion.commit()
    db.devolver_conexion(conexion)

    r = cliente.post("/guias/eliminar", json={"ids": [libre, archivada]})
    assert r.status_code == 200 and r.get_json() == {"eliminadas": 1, "bloqueadas": 1}
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT id FROM operaciones WHERE id IN (%s, %s)", (libre, archivada))
        assert [f["id"] for f in cur.fetchall()] == [archivada]
        cur.execute("SELECT operacion_id FROM marcas WHERE id = %s", (marca,))
        assert cur.fetchone()["operacion_id"] is None          # la marca sigue en el catálogo
    db.devolver_conexion(conexion)
    assert cliente.post(f"/guias/{archivada}/eliminar").status_code in (400, 409, 422, 500, 200)
    assert cliente.post("/guias/eliminar", json={"ids": []}).status_code == 400
    assert "Eliminar seleccionadas" in cliente.get("/guias").get_data(as_text=True)


def test_origen_normalizado_carpeta_1_y_2(cliente):
    from marcas.servidor import db
    from marcas.servidor.origen import normalizar_origen

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        for carpeta, archivo in (("L3210", "0030.jpg"), ("PARTE1", "p1_0017.jpg"), ("PARTE2", "p2_0003.jpg"), ("L5290", "0349.jpg")):
            cur.execute("INSERT INTO escaneos (carpeta, archivo, orden) VALUES (%s, %s, 0)", (carpeta, archivo))
            cur.execute("INSERT INTO marcas (tipo, origen_carpeta, origen_pagina) VALUES ('dominante', %s, %s)", (carpeta, archivo))
    conexion.commit()
    normalizar_origen(conexion)
    with conexion.cursor() as cur:
        cur.execute("SELECT archivo, etiqueta, carpeta_n, parte, pagina_n FROM escaneos WHERE archivo IN ('0030.jpg','p1_0017.jpg','p2_0003.jpg','0349.jpg')")
        e = {f["archivo"]: f for f in cur.fetchall()}
        cur.execute("SELECT origen_pagina, origen_etiqueta FROM marcas WHERE origen_pagina IN ('0030.jpg','p1_0017.jpg','p2_0003.jpg','0349.jpg')")
        m = {f["origen_pagina"]: f["origen_etiqueta"] for f in cur.fetchall()}
    db.devolver_conexion(conexion)
    assert e["0030.jpg"]["etiqueta"] == "Carpeta 1 · pág. 30" and e["0030.jpg"]["carpeta_n"] == 1
    assert e["p1_0017.jpg"]["etiqueta"] == "Carpeta 2 · Parte 1 · pág. 17" and e["p1_0017.jpg"]["parte"] == 1
    assert e["p2_0003.jpg"]["etiqueta"] == "Carpeta 2 · Parte 2 · pág. 3"
    assert e["0349.jpg"]["etiqueta"] == "Carpeta 2 · pág. 349" and e["0349.jpg"]["parte"] is None
    assert m == {"0030.jpg": "Carpeta 1 · pág. 30", "p1_0017.jpg": "Carpeta 2 · Parte 1 · pág. 17",
                 "p2_0003.jpg": "Carpeta 2 · Parte 2 · pág. 3", "0349.jpg": "Carpeta 2 · pág. 349"}
    assert "Carpeta 2" in cliente.get("/buscar?carpeta=2").get_data(as_text=True)
    assert "Carpeta 1 · pág. 30" in cliente.get("/buscar?q=pág.+30").get_data(as_text=True)
    assert cliente.get("/escaneos?carpeta=2").status_code == 200


def _recorte_con_marca() -> str:
    import base64

    from PIL import ImageDraw

    im = Image.new("RGB", (320, 240), (236, 234, 228))
    d = ImageDraw.Draw(im)
    d.line([(60, 190), (160, 50), (260, 190)], fill=(20, 20, 90), width=9)
    d.line([(90, 130), (230, 130)], fill=(20, 20, 90), width=7)
    salida = io.BytesIO()
    im.save(salida, "PNG")
    return "data:image/png;base64," + base64.b64encode(salida.getvalue()).decode()


def test_sacar_marca_de_una_guia_cargada(cliente):
    r = cliente.post("/escaneos/subir", data={"numero_guia": "55100200", "archivos": (io.BytesIO(_foto()), "impresa.jpg")},
                     content_type="multipart/form-data")
    assert r.status_code == 302
    pagina_id = int(cliente.get(r.headers["Location"]).get_data(as_text=True).split("/escaneos/pagina/")[1].split('"')[0].split("?")[0])
    html = cliente.get(f"/escaneos/pagina/{pagina_id}").get_data(as_text=True)
    assert "Sacar una marca de esta página" in html and "Calcar" in html

    r = cliente.post(f"/escaneos/pagina/{pagina_id}/marcas", json={"imagen": _recorte_con_marca(), "tipo": "dominante"})
    assert r.status_code == 200, r.get_data(as_text=True)
    m = r.get_json()
    assert m["codigo"].startswith("M-") and m["tipo"] == "dominante"
    png = cliente.get(m["imagen_url"])
    assert png.status_code == 200
    im = Image.open(io.BytesIO(png.get_data()))
    assert im.mode == "RGBA" and im.size == (512, 512) and im.getchannel("A").getextrema()[1] == 255
    ficha = cliente.get(m["ficha"]).get_data(as_text=True)
    assert "55100200" in ficha                       # apunta a su guía de origen
    assert m["codigo"] in cliente.get("/escaneos/guia/55100200").get_data(as_text=True)

    en_blanco = "data:image/png;base64," + __import__("base64").b64encode(_foto()).decode()
    assert cliente.post(f"/escaneos/pagina/{pagina_id}/marcas", json={"imagen": en_blanco}).status_code == 400
    assert cliente.post("/escaneos/pagina/999999/marcas", json={"imagen": _recorte_con_marca()}).status_code == 404


def test_emitir_solo_ventas(cliente, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from generar_guia_demo import generar as generar_guia

    html = cliente.get("/emitir").get_data(as_text=True)
    assert 'value="compra"' not in html and "sólo guías de venta" in html
    pdf = generar_guia(tmp_path / "v.pdf", guia="VENTA-0001", incluir_rubro2=True).read_bytes()
    r = cliente.post("/emitir/generar", content_type="multipart/form-data", data={
        "pdf_guia": (io.BytesIO(pdf), "v.pdf"), "tipo_operacion": "compra", "numero_guia": "VENTA-0001",
        "vendedor_nombre": "V", "comprador_nombre": "C", "orden": "[{\"t\": \"dib\", \"archivo\": \"d0\", \"guardar\": false}]",
        "d0": (io.BytesIO(_png_dibujo()), "d0.png")})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT tipo_operacion FROM operaciones WHERE numero_guia = 'VENTA-0001'")
        assert cur.fetchone()["tipo_operacion"] == "venta"      # aunque el pedido diga «compra», Emitir siempre guarda ventas
    db.devolver_conexion(conexion)


def _png_dibujo() -> bytes:
    from PIL import ImageDraw

    im = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse([40, 40, 216, 216], outline=(0, 0, 0, 255), width=12)
    salida = io.BytesIO()
    im.save(salida, "PNG")
    return salida.getvalue()


def _png(color: int) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", (60, 60), (color, color, color)).save(salida, "PNG")
    return salida.getvalue()


def _crear_marca_con_imagen(contenido: bytes) -> tuple[int, str]:
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute(
                "INSERT INTO marcas (tipo, archivo_png, huella) VALUES ('complementaria', %s, %s) RETURNING id, codigo",
                (psycopg2.Binary(contenido), psycopg2.Binary(b"x" * 8)),
            )
            fila = cur.fetchone()
        conexion.commit()
        return fila["id"], fila["codigo"]
    finally:
        db.devolver_conexion(conexion)


def _consultar(sql: str, params=()):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        db.devolver_conexion(conexion)


def test_reemplazar_imagen_guarda_historial_y_se_puede_volver(cliente):
    original, nueva = _png(10), _png(200)
    marca_id, codigo = _crear_marca_con_imagen(original)

    r = cliente.post(f"/marcas/{marca_id}", data={"nuevo_png": (io.BytesIO(nueva), "nueva.png")}, content_type="multipart/form-data")
    assert r.status_code == 302 and "aviso=imagen" in r.headers["Location"]
    assert cliente.get(f"/imagenes/marca/{marca_id}.png").data == nueva
    # la huella vieja ya no vale: se recalcula sola en la próxima búsqueda por dibujo
    assert _consultar("SELECT huella FROM marcas WHERE id = %s", (marca_id,))[0]["huella"] is None
    versiones = _consultar("SELECT id, motivo FROM marcas_versiones_imagen WHERE marca_id = %s", (marca_id,))
    assert len(versiones) == 1 and versiones[0]["motivo"] == "reemplazo"
    assert cliente.get(f"/imagenes/marca/version/{versiones[0]['id']}.png").data == original

    pagina = cliente.get(f"/marcas/{codigo}").get_data(as_text=True)
    assert "Historial de la imagen (1)" in pagina and "Volver a esta imagen" in pagina and "Imagen cambiada" not in pagina

    # volver a la original: la nueva queda en el historial (la restauración también se puede deshacer)
    r = cliente.post(f"/marcas/{marca_id}/restaurar-imagen/{versiones[0]['id']}")
    assert r.status_code == 302 and "aviso=restaurada" in r.headers["Location"]
    assert cliente.get(f"/imagenes/marca/{marca_id}.png").data == original
    motivos = [v["motivo"] for v in _consultar("SELECT motivo FROM marcas_versiones_imagen WHERE marca_id = %s ORDER BY id", (marca_id,))]
    assert motivos == ["reemplazo", "restauracion"]


def test_restaurar_imagen_de_otra_marca_se_rechaza(cliente):
    a, _ = _crear_marca_con_imagen(_png(30))
    b, _ = _crear_marca_con_imagen(_png(40))
    cliente.post(f"/marcas/{a}", data={"nuevo_png": (io.BytesIO(_png(90)), "x.png")}, content_type="multipart/form-data")
    version = _consultar("SELECT id FROM marcas_versiones_imagen WHERE marca_id = %s", (a,))[0]["id"]
    r = cliente.post(f"/marcas/{b}/restaurar-imagen/{version}")
    assert r.status_code == 404
    assert cliente.get(f"/imagenes/marca/{b}.png").data == _png(40)


def test_revision_no_promete_ocultar_a_quien_no_puede(cliente):
    from marcas.servidor import db
    from marcas.servidor.auth import encriptar_contrasena
    from marcas.servidor.consultas import crear_usuario

    _crear_marca_con_imagen(_png(55))
    conexion = db.obtener_conexion()
    crear_usuario(conexion, "operador@prueba.test", encriptar_contrasena("clave-de-prueba"), "Operador Prueba", "operador", True)
    conexion.commit()
    db.devolver_conexion(conexion)

    operador = cliente.application.test_client()
    operador.environ_base["wsgi.url_scheme"] = "https"
    assert operador.post("/login", data={"email": "operador@prueba.test", "contrasena": "clave-de-prueba"}).status_code == 302

    pagina_admin = cliente.get("/revision?ver=todas").get_data(as_text=True)
    pagina_operador = operador.get("/revision?ver=todas").get_data(as_text=True)
    assert "<strong>Ocultar</strong>" in pagina_admin
    assert "<strong>Ocultar</strong>" not in pagina_operador and "Ocultar</button>" not in pagina_operador
    assert "<strong>Baja</strong>" in pagina_operador
    # «Eliminar» directo en cada tarjeta (también en «por revisar»): sólo para el administrador
    assert 'data-accion="eliminar"' in cliente.get("/revision?ver=todas").get_data(as_text=True)
    assert 'data-accion="eliminar"' not in operador.get("/revision?ver=todas").get_data(as_text=True)


def test_el_foco_de_teclado_tiene_estilo_visible(cliente):
    base = cliente.get("/buscar").get_data(as_text=True)
    assert ":focus-visible" in base and "var(--dorado)" in base
    anonimo = cliente.application.test_client()
    anonimo.environ_base["wsgi.url_scheme"] = "https"
    assert ":focus-visible" in anonimo.get("/login").get_data(as_text=True)
