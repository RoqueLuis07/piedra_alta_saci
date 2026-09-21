"""Genera una guía de traslado ficticia con la misma estructura que la oficial.

Sirve para probar el estampado de marcas sin depender de un documento real
descargado del sitio de SENACSA (que además lleva datos de una operación
concreta). Reproduce lo que le importa al detector: hojas de anexo con una
grilla de casillas vectoriales, varias copias del mismo juego de hojas y
casillas anuladas con una "X".

Uso:
    python scripts/generar_guia_demo.py --salida datos/salida/guia_demo.pdf
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from PIL import Image, ImageDraw

PAGINA = (612, 1008)          # oficio, igual que el formulario real
COLUMNAS, FILAS = 5, 8
COPIAS = ["Original", "Duplicado", "Triplicado", "Cuadruplicado"]


def _grilla(c, anuladas: int) -> None:
    margen, tope, base = 26, 290, 60
    ancho_util = PAGINA[0] - 2 * margen
    alto_util = PAGINA[1] - tope - base
    paso_x, paso_y = ancho_util / COLUMNAS, alto_util / FILAS
    total = COLUMNAS * FILAS
    for i in range(total):
        f, col = divmod(i, COLUMNAS)
        x = margen + col * paso_x
        y = PAGINA[1] - tope - (f + 1) * paso_y
        c.setLineWidth(0.5)
        c.setStrokeGray(0.35)
        c.rect(x, y, paso_x, paso_y)
        if i >= total - anuladas:            # casillas sobrantes, anuladas
            c.setLineWidth(3)
            c.setStrokeGray(0)
            m = min(paso_x, paso_y) * 0.12
            c.line(x + m, y + m, x + paso_x - m, y + paso_y - m)
            c.line(x + m, y + paso_y - m, x + paso_x - m, y + m)


def _rubro2(c, *, dominante_ocupada: bool) -> None:
    """El Rubro 2 real (comprobado contra una guía de SENACSA de verdad): NO
    es un único recuadro grande para "Complementarias" como se asumía antes
    -- es una tabla de 2 filas x 3 columnas armada con líneas finas, donde la
    Dominante ocupa una sola celda (arriba a la izquierda) y las otras cinco
    quedan libres para Complementarias. Un poco más a la derecha, separado,
    va el Rubro 3 (con su propio recuadro grande) -- se dibuja también acá
    para probar que el detector no lo confunda con la grilla."""
    x0, y0 = 42, 544
    ancho_col, alto_fila = 118, 102
    columnas, filas = 3, 2
    c.setLineWidth(1)
    c.setStrokeGray(0)
    for fi in range(filas + 1):
        y = y0 + fi * alto_fila
        c.line(x0, y, x0 + columnas * ancho_col, y)
    for ci in range(columnas + 1):
        x = x0 + ci * ancho_col
        c.line(x, y0, x, y0 + filas * alto_fila)

    # La Dominante: un recuadro inset dentro de la celda de arriba a la
    # izquierda (con margen, como en el formulario real).
    margen = 8
    dom_x, dom_y = x0 + margen, y0 + alto_fila + margen
    dom_w, dom_h = ancho_col - 2 * margen, alto_fila - 2 * margen
    c.setLineWidth(1.5)
    c.rect(dom_x, dom_y, dom_w, dom_h)
    if dominante_ocupada:
        # Una marca Dominante real es siempre una imagen incrustada (PNG
        # subido o dibujado a mano), nunca líneas vectoriales -- por eso se
        # simula acá con una imagen y no con un tache dibujado a mano: un
        # tache vectorial cae dentro del mismo filtro de tamaño/proporción
        # que usa el detector para encontrar la propia casilla Dominante, y
        # como su recuadro es más chico, terminaría eligiéndose a él en vez
        # de la casilla real.
        img = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        trazo = ImageDraw.Draw(img)
        trazo.ellipse([16, 16, 112, 112], outline=(0, 0, 0, 255), width=10)
        margen_img = min(dom_w, dom_h) * 0.12
        c.drawImage(
            ImageReader(img), dom_x + margen_img, dom_y + margen_img,
            width=dom_w - 2 * margen_img, height=dom_h - 2 * margen_img,
            mask="auto", preserveAspectRatio=True,
        )

    # Rubro 3, separado, más ancho y alto que una celda -- si el detector
    # agarra este recuadro en vez de la grilla de arriba, el bug ya está de
    # vuelta.
    rubro3_x = x0 + columnas * ancho_col + 18
    c.setLineWidth(1)
    c.rect(rubro3_x, y0, 182, 188)
    c.setFont("Helvetica", 8)
    c.drawString(rubro3_x + 6, y0 + 170, "Rubro 3 (no tocar)")


def generar(
    salida: Path, guia: str = "91181750", anuladas: int = 18, *, incluir_rubro2: bool = False,
    dominante_ocupada: bool = True,
) -> Path:
    salida.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(salida), pagesize=PAGINA)
    for copia in COPIAS:
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(PAGINA[0] / 2, PAGINA[1] - 90, "GUIA DE TRASLADO Y TRANSFERENCIA DE GANADO")
        c.setFont("Helvetica", 9)
        c.drawString(40, PAGINA[1] - 120, f"N° de Orden: {guia}")
        c.drawString(40, 40, f"Fecha de Impresión: {copia} - documento de prueba")
        if incluir_rubro2:
            _rubro2(c, dominante_ocupada=dominante_ocupada)
        c.showPage()
        for hoja, anular in enumerate((0, anuladas)):
            c.setFont("Helvetica-Bold", 12)
            c.drawCentredString(PAGINA[0] / 2, PAGINA[1] - 90, "ANEXO - GUIA DE TRASLADO")
            c.setFont("Helvetica", 9)
            c.drawString(40, PAGINA[1] - 130, f"Guía N° {guia}   hoja {hoja + 1}")
            c.drawString(40, 40, f"{copia} - documento de prueba")
            _grilla(c, anular)
            c.showPage()
    c.save()
    return salida


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--salida", type=Path, default=Path("datos/salida/guia_demo.pdf"))
    ap.add_argument("--anuladas", type=int, default=18,
                    help="casillas tachadas en la segunda hoja de anexo")
    args = ap.parse_args()
    ruta = generar(args.salida, anuladas=args.anuladas)
    print(f"Guía de prueba: {ruta}")


if __name__ == "__main__":
    main()
