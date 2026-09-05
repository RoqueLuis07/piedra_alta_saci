"""Pruebas del pipeline de digitalización."""

import random
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generar_hoja_demo import dibujar_hoja, dibujar_marca, simular_escaneo  # noqa: E402

from marcas.vectorizacion import detectar_celdas, enderezar, limpiar_marca  # noqa: E402
from marcas.vectorizacion.limpiar import bbox_tinta, quitar_ruido  # noqa: E402
from marcas.vectorizacion.pipeline import procesar_lote, resumen, slug  # noqa: E402
from marcas.vectorizacion.trazar import potrace_disponible, trazar_svg  # noqa: E402


@pytest.fixture(scope="module")
def hoja_escaneada():
    rng = random.Random(3)
    np.random.seed(3)
    return simular_escaneo(dibujar_hoja(1, rng), rng)


def _casilla_sintetica(lado=400):
    """Casilla con recuadro impreso, una cruz dibujada y motas de suciedad."""
    img = np.full((lado, lado), 245, np.uint8)
    cv2.rectangle(img, (6, 6), (lado - 6, lado - 6), 120, 3)     # borde impreso
    cv2.line(img, (120, 200), (280, 200), 40, 8)                 # trazo
    cv2.line(img, (200, 110), (200, 300), 40, 8)
    rng = np.random.default_rng(1)
    for _ in range(40):                                          # motas
        y, x = rng.integers(20, lado - 20, 2)
        img[y:y + 2, x:x + 2] = 90
    return img


def test_limpiar_deja_solo_el_trazo():
    limpio = limpiar_marca(_casilla_sintetica())
    assert limpio.componentes == 1, "la cruz es un único trazo conexo"
    assert not limpio.revisar, limpio.observaciones
    # El recuadro impreso no debe sobrevivir: el trazo mide ~160x190 px.
    assert 140 <= limpio.ancho_px <= 210
    assert 160 <= limpio.alto_px <= 230


def test_png_tiene_fondo_transparente():
    limpio = limpiar_marca(_casilla_sintetica())
    png = limpio.png_rgba
    assert png.shape == (1024, 1024, 4)
    assert png[0, 0, 3] == 0, "la esquina del lienzo debe ser transparente"
    assert png[..., 3].max() > 200, "el trazo debe quedar opaco"
    # El color del trazo es negro plano; sólo el alfa cambia.
    assert png[..., :3].max() == 0


def test_casilla_vacia_se_reporta():
    vacia = np.full((300, 300), 246, np.uint8)
    cv2.rectangle(vacia, (5, 5), (295, 295), 120, 3)
    limpio = limpiar_marca(vacia)
    assert limpio.ancho_px == 0
    assert limpio.revisar


def test_quitar_ruido_conserva_trazos_multiples():
    img = np.zeros((200, 200), np.uint8)
    cv2.circle(img, (60, 100), 40, 255, 6)
    cv2.circle(img, (150, 100), 30, 255, 6)
    img[10, 10] = 255                       # mota suelta
    limpia, componentes = quitar_ruido(img)
    assert componentes == 2
    assert limpia[10, 10] == 0


def test_enderezar_corrige_la_inclinacion(hoja_escaneada):
    """Tras enderezar, un segundo pase no debería tener nada que corregir."""
    recta, angulo = enderezar(hoja_escaneada)
    assert abs(angulo) <= 3.0
    _, residuo = enderezar(recta)
    assert abs(residuo) < 0.35

    inclinada = cv2.warpAffine(
        hoja_escaneada,
        cv2.getRotationMatrix2D((1240, 1754), 2.5, 1.0),
        hoja_escaneada.shape[::-1],
        borderMode=cv2.BORDER_REPLICATE,
    )
    corregida, aplicado = enderezar(inclinada)
    assert abs(aplicado - (angulo - 2.5)) < 0.6, "debe medir la inclinación agregada"
    _, residuo_2 = enderezar(corregida)
    assert abs(residuo_2) < 0.35


def test_detectar_celdas_encuentra_la_grilla(hoja_escaneada):
    recta, _ = enderezar(hoja_escaneada)
    celdas = detectar_celdas(recta)
    assert len(celdas) == 20, f"se esperaban 4x5 casillas, se detectaron {len(celdas)}"
    assert [c.fila for c in celdas[:4]] == [1, 1, 1, 1]
    assert [c.columna for c in celdas[:4]] == [1, 2, 3, 4]
    anchos = [c.ancho for c in celdas]
    assert max(anchos) - min(anchos) < 0.1 * max(anchos), "casillas de tamaño parejo"


def test_bbox_tinta_de_mascara_vacia():
    assert bbox_tinta(np.zeros((10, 10), np.uint8)) is None


def test_slug_normaliza_nombres():
    assert slug("Hoja 03 – Estancia Ñandú") == "HOJA-03-ESTANCIA-NANDU"


@pytest.mark.skipif(not potrace_disponible(), reason="potrace no instalado")
def test_trazar_svg(tmp_path):
    mascara = np.zeros((200, 200), np.uint8)
    cv2.circle(mascara, (100, 100), 60, 255, 8)
    svg = trazar_svg(mascara, tmp_path / "m.svg")
    contenido = svg.read_text()
    assert contenido.startswith("<?xml")
    assert "<path" in contenido
    assert not (tmp_path / "m.pbm").exists(), "el PBM temporal se borra"


def test_lote_completo(tmp_path, hoja_escaneada):
    entrada = tmp_path / "escaneos"
    entrada.mkdir()
    cv2.imwrite(str(entrada / "hoja_01.png"), hoja_escaneada)

    resultados, manifiesto = procesar_lote(
        entrada, tmp_path / "png", tmp_path / "svg", manifiesto=tmp_path / "m.csv"
    )
    r = resumen(resultados)
    assert r["hojas_con_error"] == 0
    assert r["marcas_detectadas"] == 20
    assert r["marcas_generadas"] >= 18      # alguna casilla queda vacía a propósito
    assert manifiesto.exists()

    import csv
    filas = list(csv.DictReader(manifiesto.open(encoding="utf-8")))
    assert len(filas) == 20
    con_imagen = [f for f in filas if f["png"]]
    assert all(Path(f["png"]).exists() for f in con_imagen)
    assert all(f["codigo"].startswith("HOJA-01-F") for f in filas)
    if potrace_disponible():
        assert all(Path(f["svg"]).exists() for f in con_imagen)


def test_la_plantilla_de_captura_es_compatible_con_el_segmentador(tmp_path):
    """La hoja que se imprime tiene que poder volver a entrar por el pipeline.

    Se rasteriza la plantilla real, se dibuja una marca en cada casilla, se
    simula el escaneo y se procesa. Si alguna vez se imprime algo dentro del
    recuadro (un código, una línea de pie), esta prueba lo detecta: la tinta
    impresa entraría al PNG como si fuera parte del dibujo.
    """
    pdfium = pytest.importorskip("pypdfium2")
    from marcas.pdf import generar_plantilla_captura
    from marcas.vectorizacion import segmentar_hoja

    pdf = generar_plantilla_captura(tmp_path / "captura.pdf", hojas=1)
    hoja = np.array(
        pdfium.PdfDocument(str(pdf))[0].render(scale=300 / 72).to_pil().convert("L")
    )

    _, celdas, _ = segmentar_hoja(hoja, enderezar_hoja=False)
    assert len(celdas) == 24, "la plantilla impresa debe dar 4x6 casillas"

    rng = random.Random(21)
    np.random.seed(21)
    for celda in celdas:
        dibujar_marca(hoja, (celda.x, celda.y, celda.ancho, celda.alto), rng)

    entrada = tmp_path / "escaneos"
    entrada.mkdir()
    cv2.imwrite(str(entrada / "H01.png"), simular_escaneo(hoja, rng))

    resultados, _ = procesar_lote(
        entrada, tmp_path / "png", None, manifiesto=tmp_path / "m.csv"
    )
    r = resumen(resultados)
    assert r["marcas_generadas"] == 24
    assert r["a_revisar"] == 0, [
        m["observaciones"] for m in resultados[0].marcas if m["revisar"]
    ]
