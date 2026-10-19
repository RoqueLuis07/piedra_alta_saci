"""Detector de candidatas a 'no es una marca' (scripts/detectar_no_marcas.py)."""

import io
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from detectar_no_marcas import analizar  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def _png(dibujar) -> bytes:
    imagen = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    dibujar(ImageDraw.Draw(imagen))
    salida = io.BytesIO()
    imagen.save(salida, "PNG")
    return salida.getvalue()


def _categoria(dibujar):
    return analizar(_png(dibujar))[0]


NEGRO = (0, 0, 0, 255)


def test_vacia_y_polvo():
    assert _categoria(lambda d: None) == "vacia"
    assert _categoria(lambda d: d.point((100, 100), fill=NEGRO)) == "vacia"


def test_raya_diagonal_y_x_son_anulacion():
    assert _categoria(lambda d: d.line([(20, 30), (490, 470)], fill=NEGRO, width=8)) == "anulacion"

    def equis(d):
        d.line([(30, 30), (480, 480)], fill=NEGRO, width=8)
        d.line([(480, 30), (30, 480)], fill=NEGRO, width=8)

    assert _categoria(equis) == "anulacion"


def test_lineas_horizontales_son_linea_de_tabla():
    def tabla(d):
        d.line([(20, 120), (490, 120)], fill=NEGRO, width=6)
        d.line([(20, 380), (490, 380)], fill=NEGRO, width=6)

    assert _categoria(tabla) == "linea_de_tabla"


def test_renglon_de_letras_parejas_es_texto():
    def texto(d):
        for i in range(7):
            x = 40 + i * 62
            d.rectangle([x, 220, x + 40, 290], outline=NEGRO, width=10)

    assert _categoria(texto) == "texto_o_sello"


def test_firma_curva_no_se_marca():
    def firma(d):
        puntos = [(60 + t * 3.6, 256 + 150 * math.sin(t / 9) * math.cos(t / 23)) for t in range(0, 120)]
        d.line(puntos, fill=NEGRO, width=12, joint="curve")
        d.arc([150, 100, 400, 420], 20, 300, fill=NEGRO, width=12)

    assert _categoria(firma) is None
