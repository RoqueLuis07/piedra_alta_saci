"""Convierte un recorte de una página escaneada (o un dibujo hecho a mano) en una marca normalizada:
fondo transparente, sólo el trazo, recortado a su caja, centrado y de 512 px, igual que las del catálogo."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter

LADO = 512
MIN_TINTA = 30      # píxeles de trazo por debajo de los cuales se considera vacío


def normalizar_marca(contenido: bytes) -> bytes | None:
    """PNG normalizado de la marca, o None si no hay trazo."""
    try:
        imagen = Image.open(io.BytesIO(contenido))
        imagen.load()
    except Exception:
        return None
    if max(imagen.size) > 2400:
        imagen.thumbnail((2400, 2400))
    rgba = imagen.convert("RGBA")
    fondo = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    gris = np.asarray(Image.alpha_composite(fondo, rgba).convert("L"), dtype=np.float32)
    papel = float(np.percentile(gris, 90))
    umbral = max(60.0, min(190.0, papel - 40.0))
    tinta = np.clip((umbral + 30.0 - gris) / 60.0, 0.0, 1.0)      # borde suave: 0 = papel, 1 = trazo
    tinta[gris > umbral + 30.0] = 0.0
    solido = tinta > 0.5
    if int(solido.sum()) < MIN_TINTA:
        return None
    filas, columnas = np.where(solido)
    y0, y1, x0, x1 = filas.min(), filas.max() + 1, columnas.min(), columnas.max() + 1
    alto, ancho = y1 - y0, x1 - x0
    lado = int(max(alto, ancho) * 1.16) + 2
    alfa = np.zeros((lado, lado), dtype=np.float32)
    oy, ox = (lado - alto) // 2, (lado - ancho) // 2
    alfa[oy:oy + alto, ox:ox + ancho] = tinta[y0:y1, x0:x1]
    mascara = Image.fromarray((alfa * 255).astype(np.uint8)).resize((LADO, LADO), Image.LANCZOS)
    mascara = mascara.filter(ImageFilter.GaussianBlur(0.4))
    salida = Image.new("RGBA", (LADO, LADO), (0, 0, 0, 0))
    salida.putalpha(mascara)
    buffer = io.BytesIO()
    salida.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()
