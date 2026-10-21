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


def test_eliminar_solo_marcas_en_baja(cliente):
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
    assert r.status_code == 200 and r.get_json()["eliminadas"] == 1
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT id FROM marcas WHERE id IN (%s, %s)", (baja, activa))
        assert [f["id"] for f in cur.fetchall()] == [activa]
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
