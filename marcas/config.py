"""Parámetros globales del sistema de marcas de ganado."""

from __future__ import annotations

import os
from pathlib import Path

# --- Rutas -----------------------------------------------------------------
RAIZ = Path(os.environ.get("MARCAS_RAIZ", Path(__file__).resolve().parent.parent))
DIR_DATOS = RAIZ / "datos"
DIR_ESCANEOS = DIR_DATOS / "escaneos"
DIR_MARCAS = DIR_DATOS / "marcas"
DIR_PNG = DIR_MARCAS / "png"
DIR_SVG = DIR_MARCAS / "svg"
DIR_SALIDA = DIR_DATOS / "salida"
BASE_DATOS = DIR_DATOS / "marcas.db"

# --- Digitalización --------------------------------------------------------
# Resolución mínima aceptable del escaneo. Por debajo de esto el trazo a mano
# se rompe al binarizar y potrace genera contornos dentados.
DPI_MINIMO = 400
DPI_RECOMENDADO = 600

# Lienzo cuadrado final del PNG normalizado (px). 1024 alcanza para imprimir
# una casilla de 5 cm a 600 dpi sin interpolar hacia arriba.
LIENZO_PX = 1024
# Margen libre alrededor del trazo, como fracción del lienzo.
MARGEN_REL = 0.06

# Binarización adaptativa.
BLOQUE_UMBRAL_REL = 0.12   # tamaño de ventana relativo al lado menor del recorte
CONSTANTE_UMBRAL = 12      # cuánto se resta a la media local

# Limpieza de manchas: se descarta todo componente conexo cuya área sea menor
# que este porcentaje del componente más grande (las marcas suelen tener 2 o 3
# trazos separados, por eso el umbral es bajo).
RUIDO_REL_MAYOR = 0.02
RUIDO_AREA_MIN_PX = 24

# Recorte previo del borde impreso de la casilla, como fracción del alto/ancho.
INSET_CASILLA = 0.045

# --- Trazado vectorial (potrace) -------------------------------------------
POTRACE_BIN = os.environ.get("POTRACE_BIN", "potrace")
POTRACE_TURDSIZE = 4       # descarta manchas de hasta N px al vectorizar
POTRACE_ALPHAMAX = 1.0     # suavizado de esquinas (0 = todo esquinas)
POTRACE_OPTTOLERANCE = 0.2

# --- Control de calidad ----------------------------------------------------
# Rangos esperados de una marca bien digitalizada. Fuera de estos valores la
# marca se marca como "revisar" en el manifiesto.
TINTA_PCT_MIN = 0.8
TINTA_PCT_MAX = 35.0
COMPONENTES_MAX = 12

# --- Planilla PDF ----------------------------------------------------------
PLANILLA_COLUMNAS = 4
PLANILLA_FILAS = 6
