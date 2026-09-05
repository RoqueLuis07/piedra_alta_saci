"""Pruebas del extractor de muestras para documentos escaneados grandes."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extraer_muestra import (  # noqa: E402
    _datos_de_pagina, exportar_imagenes, extraer, informe, parsear_paginas,
)

pypdf = pytest.importorskip("pypdf")


@pytest.fixture(scope="module")
def pdf_escaneado(tmp_path_factory):
    """PDF de 4 páginas, cada una una imagen a página completa."""
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    ruta = tmp_path_factory.mktemp("doc") / "documento.pdf"
    c = rl_canvas.Canvas(str(ruta), pagesize=A4)
    rng = np.random.default_rng(0)
    for _ in range(4):
        hoja = np.full((1754, 1240), 245, np.uint8)      # A4 a 150 dpi
        hoja[400:500, 300:900] = 30
        hoja += rng.integers(0, 6, hoja.shape, dtype=np.uint8)
        c.drawImage(ImageReader(Image.fromarray(hoja)), 0, 0, width=A4[0], height=A4[1])
        c.showPage()
    c.save()
    return ruta


def test_parsear_paginas():
    assert parsear_paginas("1-3", 10) == [0, 1, 2]
    assert parsear_paginas("2,5,9", 10) == [1, 4, 8]
    assert parsear_paginas("1-2,2,4", 10) == [0, 1, 3], "sin repetir"


def test_parsear_paginas_fuera_de_rango():
    with pytest.raises(ValueError, match="no existe"):
        parsear_paginas("1-5", 3)
    with pytest.raises(ValueError):
        parsear_paginas("", 3)


def test_datos_de_pagina_reconoce_el_escaneo(pdf_escaneado):
    pagina = pypdf.PdfReader(str(pdf_escaneado)).pages[0]
    d = _datos_de_pagina(pagina)
    assert len(d["imagenes"]) == 1
    imagen = d["imagenes"][0]
    assert imagen["pagina_completa"] is True
    assert imagen["dpi"] == pytest.approx(150, abs=3)
    assert d["cm"] == (21.0, 29.7)


def test_no_calcula_dpi_de_una_imagen_parcial(tmp_path):
    """Un logo o una firma no son un escaneo: darles dpi sería inventar."""
    from PIL import Image
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    ruta = tmp_path / "con_logo.pdf"
    c = rl_canvas.Canvas(str(ruta), pagesize=A4)
    logo = Image.fromarray(np.full((80, 600), 128, np.uint8))
    c.drawImage(ImageReader(logo), 40, 700, width=300, height=40)
    c.save()

    d = _datos_de_pagina(pypdf.PdfReader(str(ruta)).pages[0])
    assert d["imagenes"][0]["pagina_completa"] is False
    assert d["imagenes"][0]["dpi"] is None


def test_extraer_conserva_los_datos_originales(pdf_escaneado, tmp_path):
    salida = extraer(pdf_escaneado, [0, 2], tmp_path / "muestra.pdf")
    lector = pypdf.PdfReader(str(salida))
    assert len(lector.pages) == 2

    # La imagen sale idéntica: no se recomprime ni se rasteriza de nuevo.
    original = list(pypdf.PdfReader(str(pdf_escaneado)).pages[0].images)[0]
    copia = list(lector.pages[0].images)[0]
    assert copia.data == original.data

    # Y la muestra pesa bastante menos que el documento entero.
    assert salida.stat().st_size < pdf_escaneado.stat().st_size


def test_exportar_imagenes(pdf_escaneado, tmp_path):
    escritas = exportar_imagenes(pdf_escaneado, [0, 1], tmp_path / "imgs")
    assert len(escritas) == 2
    assert all(p.exists() and p.stat().st_size > 0 for p in escritas)
    assert escritas[0].name.startswith("documento-p01")


def test_informe_no_escribe_nada(pdf_escaneado, tmp_path, capsys):
    antes = set(tmp_path.iterdir())
    informe(pdf_escaneado, None, mostrar=8)
    salida = capsys.readouterr().out
    assert "Páginas : 4" in salida
    assert "Recomendación" in salida
    assert set(tmp_path.iterdir()) == antes
