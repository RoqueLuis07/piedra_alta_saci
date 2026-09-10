"""Genera la ficha en blanco para cargar una guía a mano (o con lápiz óptico).

Pensada para completarse en una tableta antes de pasar los datos al sistema
web: un PDF con los campos de la guía (rellenables o para escribir a mano) y
recuadros en blanco para dibujar la marca dominante y las complementarias,
prolijos desde el origen -- así lo que se carga en el catálogo ya sale
limpio, sin depender de escanear un papel.

Uso:
    python scripts/generar_ficha_carga.py [salida.pdf] [--complementarias N]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

VERDE_OSCURO = (0x1F / 255, 0x4D / 255, 0x34 / 255)
DORADO = (0xF2 / 255, 0xC2 / 255, 0x30 / 255)
GRIS_TEXTO = (0x5B / 255, 0x65 / 255, 0x60 / 255)
NEGRO = (0.08, 0.09, 0.10)

ANCHO, ALTO = A4
MARGEN = 40
EMBLEMA = Path(__file__).resolve().parent.parent / "marcas/servidor/static/logo/emblema_oscuro.png"

COMPLEMENTARIAS_POR_DEFECTO = 15


def _encabezado(c: canvas.Canvas, titulo: str, pagina: int, total_paginas: int) -> float:
    """Dibuja la franja superior con el emblema y el título de la página. Devuelve el Y donde sigue el contenido."""
    y = ALTO - MARGEN
    if EMBLEMA.exists():
        c.drawImage(
            ImageReader(str(EMBLEMA)), MARGEN, y - 34, width=34, height=34,
            mask="auto", preserveAspectRatio=True,
        )
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGEN + 44, y - 12, "PIEDRA ALTA S.A.C.I.")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(MARGEN + 44, y - 24, "ESTANCIA LA PATRICIA · PARAGUAY")

    c.setFont("Helvetica", 8)
    c.drawRightString(ANCHO - MARGEN, y - 12, f"PÁGINA {pagina} DE {total_paginas}")
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(ANCHO - MARGEN, y - 24, titulo)

    y -= 42
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(1)
    c.line(MARGEN, y, ANCHO - MARGEN, y)
    return y - 10


def _pie(c: canvas.Canvas) -> None:
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(MARGEN, 22, "Registro de marcas · Piedra Alta S.A.C.I. — completar en tableta, cargar después en el sistema.")


def _titulo_seccion(c: canvas.Canvas, x: float, y: float, ancho: float, texto: str) -> float:
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(x, y, texto)
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.75)
    c.line(x, y - 4, x + ancho, y - 4)
    return y - 22


def _campo(c: canvas.Canvas, x: float, y: float, ancho: float, etiqueta: str, nombre_campo: str) -> None:
    """Una etiqueta chica arriba y una línea rellenable (a mano o tipeada) abajo."""
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(x, y, etiqueta.upper())
    alto_linea = 18
    y_linea = y - alto_linea + 2
    c.acroForm.textfield(
        name=nombre_campo, x=x, y=y_linea, width=ancho, height=alto_linea,
        borderWidth=0, fillColor=None, forceBorder=False,
        fontName="Helvetica", fontSize=10,
    )
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.6)
    c.line(x, y_linea, x + ancho, y_linea)


def _fila_campos(c: canvas.Canvas, x: float, y: float, ancho_total: float, campos: list[tuple[str, str, float]]) -> float:
    """``campos`` es una lista de (etiqueta, nombre_interno, fracción de ancho)."""
    cursor = x
    gap = 14
    disponible = ancho_total - gap * (len(campos) - 1)
    for etiqueta, nombre, fraccion in campos:
        ancho = disponible * fraccion
        _campo(c, cursor, y, ancho, etiqueta, nombre)
        cursor += ancho + gap
    return y - 34


def _recuadro_dibujo(c: canvas.Canvas, x: float, y_top: float, ancho: float, alto: float, etiqueta: str) -> float:
    """Un recuadro en blanco para dibujar. Devuelve el Y debajo del recuadro."""
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(1)
    c.rect(x, y_top - alto, ancho, alto, stroke=1, fill=0)
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(x, y_top + 3, etiqueta.upper())
    return y_top - alto


def generar(salida: Path, complementarias: int = COMPLEMENTARIAS_POR_DEFECTO) -> Path:
    c = canvas.Canvas(str(salida), pagesize=A4)
    c.setTitle("Ficha de carga de guía y marcas — Piedra Alta S.A.C.I.")

    total_paginas = 1 + -(-complementarias // 9)  # página 1 + ceil(complementarias / 9)

    # ---- Página 1: datos de la guía + marca dominante -------------------
    y = _encabezado(c, "GUÍA Y MARCA DOMINANTE", 1, total_paginas)
    ancho_util = ANCHO - 2 * MARGEN

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Datos de la guía")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("N.º de guía", "numero_guia", 0.34),
        ("Fecha", "fecha", 0.33),
        ("Tipo de formulario", "tipo_formulario", 0.33),
    ])

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Vendedor")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Nombre y apellido / razón social", "vendedor_nombre", 0.55),
        ("CI / RUC", "vendedor_documento", 0.45),
    ])
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Establecimiento de origen", "vendedor_establecimiento", 0.65),
        ("Código de establecimiento", "vendedor_establecimiento_codigo", 0.35),
    ])

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Comprador")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Nombre y apellido / razón social", "comprador_nombre", 0.65),
        ("CI / RUC", "comprador_documento", 0.35),
    ])

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Animales")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Cantidad", "cantidad_animales", 0.3),
        ("Categoría", "categoria_animales", 0.7),
    ])

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Marca dominante")
    lado = 190
    x_recuadro = MARGEN
    y = _recuadro_dibujo(c, x_recuadro, y, lado, lado, "Dibujar acá — una sola marca")
    y -= 14
    _campo(c, MARGEN, y, ancho_util * 0.6, "Descripción (ej. círculo con cruz)", "dominante_descripcion")
    _campo(c, MARGEN + ancho_util * 0.65, y, ancho_util * 0.35, "Propietario (si difiere del vendedor)", "dominante_propietario")
    y -= 40

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Notas")
    alto_notas = 46
    y_notas = y - alto_notas
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.6)
    c.rect(MARGEN, y_notas, ancho_util, alto_notas, stroke=1, fill=0)
    c.acroForm.textfield(
        name="notas", x=MARGEN, y=y_notas, width=ancho_util, height=alto_notas,
        borderWidth=0, fillColor=None, forceBorder=False,
        fontName="Helvetica", fontSize=9, fieldFlags="multiline",
    )
    y = y_notas - 46

    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(MARGEN, y, "FIRMA DE QUIEN COMPLETA ESTA FICHA")
    c.drawString(MARGEN + ancho_util * 0.55, y, "FECHA DE CARGA")
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.6)
    c.line(MARGEN, y - 22, MARGEN + ancho_util * 0.45, y - 22)
    c.line(MARGEN + ancho_util * 0.55, y - 22, MARGEN + ancho_util, y - 22)

    _pie(c)
    c.showPage()

    # ---- Páginas siguientes: marcas complementarias ----------------------
    restantes = complementarias
    pagina = 2
    numero_marca = 1
    while restantes > 0:
        en_esta_pagina = min(9, restantes)
        y = _encabezado(c, "MARCAS COMPLEMENTARIAS", pagina, total_paginas)
        ancho_util = ANCHO - 2 * MARGEN
        columnas, filas = 3, 3
        gap_x, gap_y = 16, 34
        ancho_caja = (ancho_util - gap_x * (columnas - 1)) / columnas
        alto_caja = 178

        for i in range(en_esta_pagina):
            fila, col = divmod(i, columnas)
            x = MARGEN + col * (ancho_caja + gap_x)
            y_top = y - fila * (alto_caja + gap_y + 26)
            _recuadro_dibujo(c, x, y_top, ancho_caja, alto_caja, f"N.º {numero_marca}")
            c.acroForm.textfield(
                name=f"complementaria_{numero_marca}_descripcion",
                x=x, y=y_top - alto_caja - 16, width=ancho_caja, height=14,
                borderWidth=0, fillColor=None, forceBorder=False,
                fontName="Helvetica", fontSize=8,
            )
            c.setStrokeColorRGB(*NEGRO)
            c.setLineWidth(0.5)
            c.line(x, y_top - alto_caja - 16, x + ancho_caja, y_top - alto_caja - 16)
            numero_marca += 1

        _pie(c)
        c.showPage()
        restantes -= en_esta_pagina
        pagina += 1

    c.save()
    return salida


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("salida", nargs="?", default="ficha_carga_guia.pdf")
    parser.add_argument("--complementarias", type=int, default=COMPLEMENTARIAS_POR_DEFECTO,
                         help="Cuántos recuadros de marcas complementarias generar (por defecto %(default)s).")
    args = parser.parse_args()
    ruta = generar(Path(args.salida), args.complementarias)
    print(f"Listo: {ruta} ({ruta.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
