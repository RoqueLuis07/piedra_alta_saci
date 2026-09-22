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
    completar_guia, completar_venta, detectar_casillas, detectar_rubro2, extraer_encabezado,
    hojas_de_anexo, paginas_principales, planificar,
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


@pytest.fixture(scope="module")
def guia_venta(tmp_path_factory):
    """Guía sintética con el Rubro 2 real -- Dominante en una celda y una
    grilla de 2 filas x 3 columnas para Complementarias (armada con líneas
    finas, no un único recuadro) -- además de las hojas de anexo. Reproduce
    lo que se comprobó contra una guía real de SENACSA, incluyendo el Rubro 3
    a un costado para probar que el detector no lo confunda con la grilla."""
    return generar_guia(
        tmp_path_factory.mktemp("guia_venta") / "guia_venta.pdf", incluir_rubro2=True
    )


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


@pytest.mark.parametrize("anuladas", [0, 1, 5, 39, 40])
def test_detecta_cualquier_cantidad_de_casillas_anuladas(tmp_path, anuladas):
    """Comprobado contra guías reales de SENACSA (el mismo formulario trae
    18 X en unos documentos y 20 en otros -- nunca una cantidad fija): la
    detección no asume de antemano cuántas casillas van a estar tachadas,
    las evalúa una por una. Se prueban además los dos extremos -- la hoja
    sin ninguna X y la hoja completa, sin ninguna casilla libre -- que no
    aparecieron en ninguna de las guías reales que se usaron para probar."""
    pdf = generar_guia(tmp_path / f"guia_{anuladas}.pdf", anuladas=anuladas)
    casillas = detectar_casillas(pdf, 2)  # segunda hoja de anexo: la que trae las anuladas
    assert len(casillas) == 40, "la cantidad de casillas visibles no cambia, estén o no marcadas"
    assert sum(c.ocupada for c in casillas) == anuladas
    assert sum(not c.ocupada for c in casillas) == 40 - anuladas


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


def test_detecta_rubro2_dominante_y_cinco_complementarias(guia_venta):
    """Comprobado contra una guía real de SENACSA: el Rubro 2 NO es una
    Dominante más un único recuadro de 2x2 para Complementarias -- es una
    grilla de 2 filas x 3 columnas donde la Dominante ocupa una sola celda y
    las otras CINCO quedan libres para Complementarias, antes de pasar al
    Anexo. Se prueba en las cuatro copias, no sólo la primera."""
    principales = paginas_principales(guia_venta)
    assert len(principales) == 4
    for i, _copia in principales:
        dominante, complementarias = detectar_rubro2(guia_venta, i)
        assert dominante is not None and dominante.ocupada, "la Dominante ya viene puesta en la guía de prueba"
        assert len(complementarias) == 5
        assert all(not c.ocupada for c in complementarias)


def test_detecta_rubro2_con_dominante_libre(tmp_path_factory):
    guia_libre = generar_guia(
        tmp_path_factory.mktemp("guia_libre") / "guia.pdf",
        incluir_rubro2=True, dominante_ocupada=False,
    )
    dominante, complementarias = detectar_rubro2(guia_libre, 0)
    assert dominante is not None and not dominante.ocupada
    assert len(complementarias) == 5


def test_completar_venta_llena_las_cinco_del_rubro2_antes_del_anexo(guia_venta, marcas, tmp_path):
    """Bug real encontrado en producción: las marcas complementarias de una
    Venta se estaban estampando en el Rubro 3 (Especie Bovina) en vez de en
    la grilla de Complementarias -- quedaban "aceptadas" por el sistema pero
    invisibles en el PDF final. Con 5 marcas, las cinco deben entrar en el
    Rubro 2 de las cuatro copias, ninguna al Anexo."""
    salida = tmp_path / "venta_5.pdf"
    info = completar_venta(guia_venta, None, marcas, salida)
    assert info["complementarias_en_rubro2"] == 5
    assert info["complementarias_en_anexo"] == 0
    assert sorted(info["paginas_modificadas"]) == sorted(info["paginas_principales"])


def test_completar_venta_desborda_la_sexta_marca_al_anexo(guia_venta, marcas, tmp_path):
    """Regla de negocio explícita: con cinco marcas complementarias alcanza
    la primera página (se repite en las cuatro copias); recién la SEXTA
    marca en adelante pasa al Anexo."""
    seis = [*marcas, marcas[0]]
    salida = tmp_path / "venta_6.pdf"
    info = completar_venta(guia_venta, None, seis, salida)
    assert info["complementarias_en_rubro2"] == 5
    assert info["complementarias_en_anexo"] == 1
    assert set(info["paginas_principales"]).issubset(set(info["paginas_modificadas"]))
    assert len(info["paginas_modificadas"]) > len(info["paginas_principales"])


def test_completar_venta_no_pisa_una_dominante_ya_puesta(guia_venta, marcas, tmp_path):
    """La Dominante de la guía de prueba ya viene puesta -- si se le pasa una
    imagen de Dominante de todos modos, no se debe reemplazar."""
    salida = tmp_path / "venta_dominante_ocupada.pdf"
    info = completar_venta(guia_venta, marcas[0], marcas[1:3], salida)
    assert info["dominante_completada"] is False


def test_completar_venta_no_toca_el_rubro3(guia_venta, marcas, tmp_path):
    """Regresión directa del bug: el área de Rubro 3 (a la derecha de
    Complementarias) no debe cambiar ni un píxel al estampar marcas."""
    salida = tmp_path / "venta_rubro3.pdf"
    completar_venta(guia_venta, None, marcas, salida)
    escala = 2.0
    antes = _pagina(guia_venta, 0, escala=escala)
    despues = _pagina(salida, 0, escala=escala)
    # Rubro 3 (ver _rubro2 en generar_guia_demo.py): x 414-596, y 544-732 en
    # coordenadas PDF (origen abajo-izquierda) -- se convierte a filas/columnas
    # de la imagen (origen arriba-izquierda) para recortar esa zona.
    alto_pagina = 1008
    x0, x1 = round(414 * escala), round(596 * escala)
    y0, y1 = round((alto_pagina - 732) * escala), round((alto_pagina - 544) * escala)
    recorte_antes = antes[y0:y1, x0:x1]
    recorte_despues = despues[y0:y1, x0:x1]
    assert np.array_equal(recorte_antes, recorte_despues)
