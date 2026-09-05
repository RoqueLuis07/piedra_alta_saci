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
from reportlab.pdfgen import canvas as rl_canvas

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


def generar(salida: Path, guia: str = "91181750", anuladas: int = 18) -> Path:
    salida.parent.mkdir(parents=True, exist_ok=True)
    c = rl_canvas.Canvas(str(salida), pagesize=PAGINA)
    for copia in COPIAS:
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(PAGINA[0] / 2, PAGINA[1] - 90, "GUIA DE TRASLADO Y TRANSFERENCIA DE GANADO")
        c.setFont("Helvetica", 9)
        c.drawString(40, PAGINA[1] - 120, f"N° de Orden: {guia}")
        c.drawString(40, 40, f"Fecha de Impresión: {copia} - documento de prueba")
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
