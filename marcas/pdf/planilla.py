"""Planillas PDF con las marcas ubicadas en casillas.

Tres documentos, todos con la misma grilla:

* ``generar_planilla``          el PDF general: casillas con las marcas elegidas.
* ``generar_hoja_control``      contactos para revisar el lote digitalizado.
* ``generar_plantilla_captura`` hoja en blanco para dibujar y después escanear.

Las marcas se insertan como PNG con transparencia, así el recuadro de la
casilla queda visible por detrás del trazo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Sequence

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

from marcas import config

GRIS_LINEA = 0.45
GRIS_TEXTO = 0.25


@dataclass
class ItemPlanilla:
    """Una casilla del PDF."""

    codigo: str
    imagen: Path | str | None = None
    titulo: str | None = None       # propietario o descripción
    pie: str | None = None          # línea chica adicional

    @classmethod
    def desde_fila(cls, fila) -> "ItemPlanilla":
        """Construye el item a partir de una fila de la tabla ``marcas``."""
        imagen = fila["archivo_png"]
        if imagen and not Path(imagen).is_absolute():
            imagen = config.RAIZ / imagen
        return cls(
            codigo=fila["codigo"],
            imagen=imagen,
            titulo=fila["propietario"] if "propietario" in fila.keys() else None,
            pie=fila["descripcion"] if "descripcion" in fila.keys() else None,
        )


def _encabezado(c, ancho, alto, titulo, subtitulo, pagina, paginas):
    c.setFillGray(GRIS_TEXTO)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(18 * mm, alto - 18 * mm, titulo)
    if subtitulo:
        c.setFont("Helvetica", 9.5)
        c.drawString(18 * mm, alto - 24 * mm, subtitulo)
    c.setFont("Helvetica", 8)
    c.drawRightString(
        ancho - 18 * mm, alto - 18 * mm, date.today().strftime("%d/%m/%Y")
    )
    c.setStrokeGray(GRIS_LINEA)
    c.setLineWidth(0.6)
    c.line(18 * mm, alto - 28 * mm, ancho - 18 * mm, alto - 28 * mm)

    c.setFont("Helvetica", 8)
    c.setFillGray(0.45)
    c.drawCentredString(ancho / 2, 12 * mm, f"Página {pagina} de {paginas}")
    c.setFillGray(GRIS_TEXTO)


def _dibujar_casilla(
    c, x, y, ancho, alto, item: ItemPlanilla | None,
    mostrar_codigo=True, codigo_arriba=False,
):
    """Dibuja el recuadro y, si hay imagen, la centra respetando la proporción.

    Con ``codigo_arriba`` la etiqueta se imprime por fuera del recuadro. Es lo
    que corresponde en la plantilla de captura: cualquier cosa impresa dentro
    de la casilla la levanta después el binarizado como si fuera parte del
    dibujo.
    """
    if codigo_arriba and item is not None:
        c.setFillGray(0.45)
        c.setFont('Helvetica-Bold', 7)
        c.drawString(x + 1 * mm, y + alto + 1.8 * mm, item.codigo[:28])
        c.setFillGray(GRIS_TEXTO)
        mostrar_codigo = False
    c.setStrokeGray(GRIS_LINEA)
    c.setLineWidth(0.8)
    c.rect(x, y, ancho, alto)

    alto_pie = 9 * mm if mostrar_codigo else 0
    pad = 3.5 * mm
    zona_w = ancho - 2 * pad
    zona_h = alto - alto_pie - 2 * pad
    if item is None:
        return

    if item.imagen and Path(item.imagen).exists():
        lector = ImageReader(str(item.imagen))
        iw, ih = lector.getSize()
        escala = min(zona_w / iw, zona_h / ih)
        w, h = iw * escala, ih * escala
        c.drawImage(
            lector,
            x + (ancho - w) / 2,
            y + alto_pie + pad + (zona_h - h) / 2,
            width=w,
            height=h,
            mask="auto",           # respeta el canal alfa del PNG
            preserveAspectRatio=True,
        )
    elif item.imagen:
        c.setFont("Helvetica-Oblique", 7)
        c.setFillGray(0.6)
        c.drawCentredString(x + ancho / 2, y + alto / 2, "(imagen no encontrada)")
        c.setFillGray(GRIS_TEXTO)

    if not mostrar_codigo:
        return

    c.setStrokeGray(0.75)
    c.setLineWidth(0.4)
    c.line(x + pad, y + alto_pie, x + ancho - pad, y + alto_pie)
    c.setFillGray(GRIS_TEXTO)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawCentredString(x + ancho / 2, y + alto_pie - 5.2 * mm, item.codigo[:28])
    linea2 = item.titulo or item.pie
    if linea2:
        c.setFont("Helvetica", 6.5)
        c.setFillGray(0.45)
        c.drawCentredString(x + ancho / 2, y + alto_pie - 8.2 * mm, linea2[:34])
        c.setFillGray(GRIS_TEXTO)


def _grilla(ancho, alto, columnas, filas, margen=18 * mm, tope=32 * mm, base=18 * mm,
            sep=3 * mm):
    util_w = ancho - 2 * margen
    util_h = alto - tope - base
    paso_x = util_w / columnas
    paso_y = util_h / filas
    for f in range(filas):
        for c_ in range(columnas):
            x = margen + c_ * paso_x
            y = alto - tope - (f + 1) * paso_y
            yield x, y + sep / 2, paso_x - sep, paso_y - sep


def _documento(
    items: Sequence[ItemPlanilla | None],
    salida: Path,
    titulo: str,
    subtitulo: str | None,
    columnas: int,
    filas: int,
    apaisado: bool,
    mostrar_codigo: bool = True,
) -> Path:
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    pagina = landscape(A4) if apaisado else A4
    ancho, alto = pagina
    por_pagina = columnas * filas
    paginas = max(1, -(-len(items) // por_pagina))

    c = rl_canvas.Canvas(str(salida), pagesize=pagina)
    c.setTitle(titulo)
    for p in range(paginas):
        _encabezado(c, ancho, alto, titulo, subtitulo, p + 1, paginas)
        tanda = items[p * por_pagina:(p + 1) * por_pagina]
        for item, (x, y, w, h) in zip(tanda, _grilla(ancho, alto, columnas, filas)):
            _dibujar_casilla(c, x, y, w, h, item, mostrar_codigo)
        c.showPage()
    c.save()
    return salida


def generar_planilla(
    items: Sequence[ItemPlanilla],
    salida: Path,
    *,
    titulo: str = "Registro de marcas",
    subtitulo: str | None = None,
    columnas: int | None = None,
    filas: int | None = None,
    apaisado: bool = False,
    casillas_vacias: int = 0,
) -> Path:
    """PDF general con las marcas seleccionadas, una por casilla.

    ``casillas_vacias`` agrega recuadros en blanco al final, para altas nuevas
    que se completan a mano sobre el impreso.
    """
    columnas = columnas or config.PLANILLA_COLUMNAS
    filas = filas or config.PLANILLA_FILAS
    todos: list[ItemPlanilla | None] = list(items) + [None] * casillas_vacias
    return _documento(
        todos, salida, titulo, subtitulo, columnas, filas, apaisado
    )


def generar_hoja_control(
    items: Sequence[ItemPlanilla],
    salida: Path,
    *,
    titulo: str = "Hoja de control de digitalización",
    subtitulo: str | None = None,
    columnas: int = 6,
    filas: int = 8,
) -> Path:
    """Contactos en miniatura para revisar un lote recién procesado.

    Con 48 marcas por página, un lote de 500 se revisa en 11 hojas: alcanza con
    mirar cuáles salieron cortadas, invertidas o vacías.
    """
    return _documento(
        list(items), salida, titulo, subtitulo, columnas, filas, apaisado=False
    )


def generar_plantilla_captura(
    salida: Path,
    *,
    hojas: int = 1,
    prefijo: str = "H",
    columnas: int | None = None,
    filas: int | None = None,
    titulo: str = "Planilla de captura de marcas",
    subtitulo: str = "Dibujar una marca por casilla, con marcador negro. No salirse del recuadro.",
    numero_inicial: int = 1,
) -> Path:
    """Hoja en blanco para dibujar las marcas y después escanearla.

    Es la contracara del segmentador: como las casillas ya vienen impresas y
    numeradas, ``detectar_celdas`` las encuentra solas y cada marca hereda su
    código de la posición fila/columna.
    """
    columnas = columnas or config.PLANILLA_COLUMNAS
    filas = filas or config.PLANILLA_FILAS
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    ancho, alto = A4
    c = rl_canvas.Canvas(str(salida), pagesize=A4)
    c.setTitle(titulo)
    for n in range(numero_inicial, numero_inicial + hojas):
        _encabezado(
            c, ancho, alto, f"{titulo} — hoja {n:02d}", subtitulo,
            n - numero_inicial + 1, hojas,
        )
        for i, (x, y, w, h) in enumerate(
            _grilla(ancho, alto, columnas, filas, sep=6 * mm)
        ):
            fila, col = divmod(i, columnas)
            etiqueta = f"{prefijo}{n:02d}-F{fila + 1:02d}C{col + 1:02d}"
            _dibujar_casilla(
                c, x, y, w, h,
                ItemPlanilla(codigo=etiqueta, imagen=None),
                codigo_arriba=True,
            )
        c.showPage()
    c.save()
    return salida
