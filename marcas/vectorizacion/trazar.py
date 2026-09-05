"""Trazado vectorial: máscara de tinta -> SVG, con potrace.

El SVG es el original de archivo. Un PNG queda atado a la resolución con que
se generó; el SVG se rasteriza después al tamaño que pida cada impresión
(planilla A4, cartel, marcador a fuego) sin escalones ni pérdida.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import numpy as np

from marcas import config


def potrace_disponible() -> bool:
    return shutil.which(config.POTRACE_BIN) is not None


def _escribir_pbm(mascara: np.ndarray, destino: Path) -> None:
    """Guarda la máscara como PBM binario (P4), el formato que come potrace.

    En PBM el bit 1 es negro, y en nuestra máscara la tinta vale 255, así que
    se empaqueta directamente sin invertir.
    """
    alto, ancho = mascara.shape
    bits = (mascara > 127).astype(np.uint8)
    empaquetado = np.packbits(bits, axis=1)
    with open(destino, "wb") as fh:
        fh.write(f"P4\n{ancho} {alto}\n".encode("ascii"))
        fh.write(empaquetado.tobytes())


def trazar_svg(
    mascara: np.ndarray,
    destino_svg: Path,
    *,
    turdsize: int | None = None,
    alphamax: float | None = None,
    opttolerance: float | None = None,
) -> Path:
    """Vectoriza la máscara y deja el SVG en ``destino_svg``."""
    if not potrace_disponible():
        raise RuntimeError(
            "No se encontró 'potrace'. Instalar con: sudo apt install potrace "
            "(o brew install potrace)."
        )
    destino_svg = Path(destino_svg)
    destino_svg.parent.mkdir(parents=True, exist_ok=True)
    pbm = destino_svg.with_suffix(".pbm")
    _escribir_pbm(mascara, pbm)
    cmd = [
        config.POTRACE_BIN,
        "--svg",
        "--turdsize", str(config.POTRACE_TURDSIZE if turdsize is None else turdsize),
        "--alphamax", str(config.POTRACE_ALPHAMAX if alphamax is None else alphamax),
        "--opttolerance",
        str(config.POTRACE_OPTTOLERANCE if opttolerance is None else opttolerance),
        "--output", str(destino_svg),
        str(pbm),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        pbm.unlink(missing_ok=True)
    return destino_svg


_RE_VIEWBOX = re.compile(r'viewBox="([-\d.\s]+)"')


def contar_curvas(svg: Path) -> int:
    """Cantidad de subtrazos del SVG. Sirve como métrica de calidad rápida."""
    texto = Path(svg).read_text(encoding="utf-8", errors="ignore")
    return texto.count("M")
