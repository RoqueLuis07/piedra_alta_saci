"""Digitalización de marcas dibujadas a mano: hoja escaneada -> PNG + SVG."""

from marcas.vectorizacion.limpiar import limpiar_marca, recortar_a_tinta, ResultadoLimpieza
from marcas.vectorizacion.segmentar import detectar_celdas, enderezar, segmentar_hoja
from marcas.vectorizacion.trazar import trazar_svg, potrace_disponible

__all__ = [
    "limpiar_marca",
    "recortar_a_tinta",
    "ResultadoLimpieza",
    "detectar_celdas",
    "enderezar",
    "segmentar_hoja",
    "trazar_svg",
    "potrace_disponible",
]
