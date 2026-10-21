"""Búsqueda por dibujo: la huella tolera el grosor y el tamaño, y el servidor ordena por parecido."""

import base64
import io

import psycopg2
import pytest

pytest.importorskip("pixeltable_pgserver")

from PIL import Image, ImageDraw  # noqa: E402

from marcas.servidor.similitud import huella, ordenar_por_parecido  # noqa: E402

FORMAS = {
    "triangulo": lambda d, k, g: d.line([(40 * k, 200 * k), (128 * k, 40 * k), (216 * k, 200 * k), (40 * k, 200 * k)], fill=0, width=g),
    "cruz": lambda d, k, g: (d.line([(128 * k, 30 * k), (128 * k, 226 * k)], fill=0, width=g), d.line([(30 * k, 128 * k), (226 * k, 128 * k)], fill=0, width=g)),
    "circulo": lambda d, k, g: d.ellipse([40 * k, 40 * k, 216 * k, 216 * k], outline=0, width=g),
    "ele": lambda d, k, g: d.line([(60 * k, 30 * k), (60 * k, 220 * k), (200 * k, 220 * k)], fill=0, width=g),
}


def _png(forma: str, k: int = 1, grosor: int = 14, transparente: bool = False) -> bytes:
    imagen = Image.new("L", (256 * k, 256 * k), 255)
    FORMAS[forma](ImageDraw.Draw(imagen), k, grosor * k)
    if transparente:
        imagen = Image.merge("LA", (imagen.point(lambda v: 0), imagen.point(lambda v: 255 - v)))
    salida = io.BytesIO()
    imagen.save(salida, "PNG")
    return salida.getvalue()


def test_huella_tolera_grosor_y_tamano():
    base = huella(_png("triangulo"))
    otro_grosor = huella(_png("triangulo", k=2, grosor=7))
    otra_forma = huella(_png("circulo"))
    assert base and otro_grosor and otra_forma
    puntajes = dict(ordenar_por_parecido(base, [(1, otro_grosor), (2, otra_forma)]))
    assert puntajes[1] > 85 > puntajes[2] + 10


def test_huella_con_transparencia_y_vacia():
    assert huella(_png("cruz", transparente=True)) is not None
    assert huella(Image.new("RGB", (50, 50), "white").tobytes()) is None
    vacio = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(vacio, "PNG")
    assert huella(vacio.getvalue()) is None


def test_busqueda_en_el_servidor(cliente):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    ids = {}
    with conexion.cursor() as cur:
        for forma, tipo in (("triangulo", "dominante"), ("cruz", "complementaria"), ("circulo", "complementaria"), ("ele", "dominante")):
            cur.execute("INSERT INTO marcas (tipo, archivo_png) VALUES (%s, %s) RETURNING id, codigo",
                        (tipo, psycopg2.Binary(_png(forma))))
            fila = cur.fetchone()
            ids[forma] = fila["codigo"]
        cur.execute("INSERT INTO marcas (tipo, archivo_png) VALUES ('dominante', %s)", (psycopg2.Binary(b"no es una imagen"),))
    conexion.commit()
    db.devolver_conexion(conexion)

    dibujo = "data:image/png;base64," + base64.b64encode(_png("cruz", k=2, grosor=9)).decode()
    r = cliente.post("/buscar/dibujo", json={"imagen": dibujo})
    assert r.status_code == 200, r.get_data(as_text=True)
    datos = r.get_json()
    assert datos["resultados"][0]["codigo"] == ids["cruz"]
    assert datos["resultados"][0]["puntaje"] > 80
    assert datos["huellas_pendientes"] == 0

    r = cliente.post("/buscar/dibujo", json={"imagen": dibujo, "tipo": "dominante"})
    assert {m["tipo"] for m in r.get_json()["resultados"]} == {"dominante"}

    en_blanco = base64.b64encode(_png("cruz", grosor=0)).decode()
    assert cliente.post("/buscar/dibujo", json={"imagen": en_blanco}).status_code == 400
    assert cliente.post("/buscar/dibujo", json={"imagen": "###"}).status_code == 400


def test_pantallas(cliente):
    assert "Buscar por dibujo" in cliente.get("/buscar/dibujo").get_data(as_text=True)
    html = cliente.get("/buscar").get_data(as_text=True)
    assert "Dominantes (" in html and "Complementarias (" in html
    solo = cliente.get("/buscar?tipo=complementaria").get_data(as_text=True)
    assert solo.count('class="tarjeta-marca"') == 2
