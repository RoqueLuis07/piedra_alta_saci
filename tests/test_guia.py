"""Pruebas del estampado sobre la guía de traslado oficial.

Se usa una guía sintética con la misma estructura que la real (hojas de anexo
con grilla vectorial, cuatro copias, casillas anuladas con una X): así las
pruebas no dependen de un documento descargado, que además lleva datos de una
operación concreta.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generar_guia_demo import generar as generar_guia  # noqa: E402

from marcas.pdf.guia import (  # noqa: E402
    completar_guia, detectar_casillas, extraer_encabezado, hojas_de_anexo, planificar,
)

pdfium = pytest.importorskip("pypdfium2")


@pytest.fixture(scope="module")
def guia(tmp_path_factory):
    return generar_guia(tmp_path_factory.mktemp("guia") / "guia.pdf")


@pytest.fixture(scope="module")
def marcas(tmp_path_factory):
    """Cinco PNG de marca, con transparencia, como los que da el pipeline."""
    import cv2
    carpeta = tmp_path_factory.mktemp("marcas")
    rutas = []
    for i in range(5):
        img = np.zeros((256, 256, 4), np.uint8)
        cv2.circle(img, (128, 128), 70 + i * 8, (0, 0, 0, 255), 12)
        ruta = carpeta / f"M{i:02d}.png"
        cv2.imwrite(str(ruta), img)
        rutas.append(ruta)
    return rutas


def _pagina(pdf, indice, escala=1.0):
    return np.array(
        pdfium.PdfDocument(str(pdf))[indice].render(scale=escala).to_pil().convert("L")
    )


def test_detecta_las_casillas_de_una_hoja(guia):
    casillas = detectar_casillas(guia, 1)
    assert len(casillas) == 40
    assert {c.fila for c in casillas} == set(range(1, 9))
    assert {c.columna for c in casillas} == set(range(1, 6))
    # Orden de lectura: la primera casilla es la de arriba a la izquierda.
    assert casillas[0].fila == 1 and casillas[0].columna == 1
    assert casillas[0].y > casillas[-1].y


def test_las_casillas_anuladas_cuentan_como_ocupadas(guia):
    """Una casilla tachada con X no se puede reutilizar para otra marca."""
    hoja1 = detectar_casillas(guia, 1)
    hoja2 = detectar_casillas(guia, 2)
    assert sum(c.ocupada for c in hoja1) == 0
    assert sum(c.ocupada for c in hoja2) == 18
    assert len(hoja2) == 40, "la X no debe hacer desaparecer la casilla"


def test_reconoce_las_cuatro_copias(guia):
    hojas = hojas_de_anexo(guia)
    assert len(hojas) == 8
    assert sorted({h.copia for h in hojas}) == [
        "Cuadruplicado", "Duplicado", "Original", "Triplicado"
    ]
    assert sorted(h.orden for h in hojas) == [0, 0, 0, 0, 1, 1, 1, 1]


def test_el_plan_reparte_igual_en_todas_las_copias(guia, marcas):
    hojas = hojas_de_anexo(guia)
    plan = planificar(hojas, marcas)
    assert len(plan) == 4, "las 5 marcas entran en la primera hoja de cada copia"
    posiciones = {
        pagina: [(round(c.x), round(c.y), img.name) for c, img in asignaciones]
        for pagina, asignaciones in plan.items()
    }
    unicas = {tuple(v) for v in posiciones.values()}
    assert len(unicas) == 1, "cada copia debe recibir las mismas marcas en el mismo lugar"


def test_completar_guia_conserva_el_documento(guia, marcas, tmp_path):
    salida = tmp_path / "completada.pdf"
    r = completar_guia(guia, marcas, salida)

    original = pdfium.PdfDocument(str(guia))
    resultado = pdfium.PdfDocument(str(salida))
    assert len(resultado) == len(original)
    assert r["marcas"] == 5
    assert r["copias"] == ["Cuadruplicado", "Duplicado", "Original", "Triplicado"]

    # Las páginas de la guía (no anexo) quedan intactas.
    assert 0 not in r["paginas_modificadas"]
    assert np.array_equal(_pagina(guia, 0), _pagina(salida, 0))

    # La hoja de anexo suma tinta donde antes no había nada.
    antes, despues = _pagina(guia, 1), _pagina(salida, 1)
    assert (despues < 128).sum() > (antes < 128).sum() + 1000


def test_no_toca_las_casillas_anuladas(guia, marcas, tmp_path):
    """Con pocas marcas, la segunda hoja de anexo no debería cambiar."""
    salida = completar_guia(guia, marcas, tmp_path / "c.pdf")["salida"]
    assert np.array_equal(_pagina(guia, 2), _pagina(salida, 2))


def test_avisa_cuando_no_entran(guia, marcas):
    with pytest.raises(ValueError, match="casillas libres"):
        completar_guia(guia, marcas * 20, "/tmp/no_se_escribe.pdf")


def test_avisa_si_falta_una_imagen(guia, marcas, tmp_path):
    with pytest.raises(FileNotFoundError):
        completar_guia(guia, [*marcas, tmp_path / "no_existe.png"], tmp_path / "c.pdf")


def test_rechaza_un_pdf_que_no_es_una_guia(tmp_path, marcas):
    from marcas.pdf import generar_plantilla_captura
    otro = generar_plantilla_captura(tmp_path / "otro.pdf", hojas=1)
    with pytest.raises(ValueError, match="hojas de anexo"):
        completar_guia(otro, marcas, tmp_path / "c.pdf")


def test_el_dpi_define_la_resolucion_incrustada(marcas):
    """La marca se reduce al tamaño con que se va a imprimir, no más."""
    from marcas.pdf.guia import _lector

    grande = _lector(marcas[0], lado_pt=113, dpi=600, cache={})
    chica = _lector(marcas[0], lado_pt=113, dpi=100, cache={})
    assert chica.getSize()[0] < grande.getSize()[0]
    assert chica.getSize()[0] == pytest.approx(113 / 72 * 100, abs=2)
    # A 600 dpi el destino supera al original: no se amplía, se deja como está.
    assert grande.getSize() == (256, 256)


def test_la_imagen_reducida_se_reutiliza(marcas):
    """La misma marca en las cuatro copias se prepara una sola vez."""
    from marcas.pdf.guia import _lector

    cache: dict = {}
    a = _lector(marcas[0], lado_pt=113, dpi=300, cache=cache)
    b = _lector(marcas[0], lado_pt=113, dpi=300, cache=cache)
    assert a is b and len(cache) == 1


def _generar_pagina_con_encabezado(
    salida: Path,
    *,
    numero_orden="91181756",
    vendedor_ci_ruc="80020081",
    vendedor_dv="0",
    vendedor_nombre="PIEDRA ALTA S.A. INMOBILIARIA",
    comprador_nombre="GANADERA MADREJON S.A.",
    comprador_ci_ruc="80083402",
    comprador_dv="0",
) -> Path:
    """Una sola página con el mismo layout de etiquetas del Rubro 1 y el
    Rubro 7 que la Guía real -- no hace falta la grilla de casillas ni las
    cuatro copias, sólo lo que lee ``extraer_encabezado``.

    Etiqueta y valor de una misma fila van a la MISMA altura (approx.),
    como en el formulario real, para que se agrupen en la misma línea al
    reordenar por posición -- eso es justamente lo que se está probando."""
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(str(salida), pagesize=(612, 1008))
    c.setFont("Helvetica-Bold", 12)
    c.drawString(100, 950, "GUIA DE TRASLADO Y TRANSFERENCIA DE GANADO")
    c.setFont("Helvetica", 9)
    c.drawString(80, 900, "N° de Orden")
    c.drawString(250, 900, "CI / RUC")
    c.drawString(400, 900, "DV")
    c.drawString(80, 885, numero_orden)
    c.drawString(250, 885, vendedor_ci_ruc)
    c.drawString(400, 885, vendedor_dv)
    c.drawString(80, 865, "Nombre y Apellido / Razón Social")
    c.drawString(40, 850, vendedor_nombre)
    c.drawString(40, 300, "Rubro 7 - Identificación del Adquiriente")
    c.drawString(120, 280, "Nombre y Apellido / Razón Social")
    c.drawString(400, 280, "CI / RUC")
    c.drawString(480, 280, "DV")
    linea_comprador = f"{comprador_nombre} {comprador_ci_ruc}"
    if comprador_dv:
        linea_comprador += f" {comprador_dv}"
    c.drawString(40, 265, linea_comprador)
    c.save()
    return Path(salida)


def test_extraer_encabezado_lee_vendedor_y_comprador(tmp_path):
    pdf = _generar_pagina_con_encabezado(tmp_path / "guia.pdf")
    datos = extraer_encabezado(pdf)
    assert datos["numero_orden"] == "91181756"
    assert datos["vendedor_nombre"] == "PIEDRA ALTA S.A. INMOBILIARIA"
    assert datos["vendedor_ci_ruc"] == "80020081"
    assert datos["vendedor_dv"] == "0"
    assert datos["comprador_nombre"] == "GANADERA MADREJON S.A."
    assert datos["comprador_ci_ruc"] == "80083402"
    assert datos["comprador_dv"] == "0"


def test_extraer_encabezado_tolera_comprador_sin_dv(tmp_path):
    """Cuando el DV del comprador viene vacío, el nombre puede tener varias
    palabras y no se lo tiene que confundir con el CI/RUC (caso real: una
    guía donde comprador_dv nunca se imprimió)."""
    pdf = _generar_pagina_con_encabezado(
        tmp_path / "guia.pdf",
        comprador_nombre="CLEDSON DAL TOE MARCELINO",
        comprador_ci_ruc="3712507",
        comprador_dv="",
    )
    datos = extraer_encabezado(pdf)
    assert datos["comprador_nombre"] == "CLEDSON DAL TOE MARCELINO"
    assert datos["comprador_ci_ruc"] == "3712507"
    assert datos["comprador_dv"] is None


def test_extraer_encabezado_rechaza_un_pdf_que_no_es_una_guia(tmp_path):
    from marcas.pdf import generar_plantilla_captura

    otro = generar_plantilla_captura(tmp_path / "otro.pdf", hojas=1)
    with pytest.raises(ValueError, match="Rubro 2"):
        extraer_encabezado(otro)
