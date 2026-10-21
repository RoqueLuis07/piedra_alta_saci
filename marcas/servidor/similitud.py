"""Búsqueda de marcas por dibujo: una huella compacta de cada imagen y su comparación.

La huella es la silueta de la tinta recortada a su caja, centrada en un cuadrado, reducida a 32x32 y
suavizada: así un trazo dibujado a mano (más grueso o más fino que el escaneado) se parece igual a la
marca del catálogo. Se compara con coseno sobre la silueta centrada, más una pequeña penalización si
la proporción alto/ancho es muy distinta.
"""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageFilter

LADO = 32
BYTES_HUELLA = 1 + LADO * LADO  # 1 byte de proporción + la silueta


def _tinta(imagen: Image.Image) -> np.ndarray:
    """Máscara de tinta (1 = tinta) de una imagen con o sin transparencia."""
    imagen = imagen.convert("RGBA")
    fondo = Image.new("RGBA", imagen.size, (255, 255, 255, 255))
    gris = np.asarray(Image.alpha_composite(fondo, imagen).convert("L"), dtype=np.float32)
    corte = min(160.0, float(gris.mean()) - 25.0) if gris.size else 128.0
    return (gris < max(corte, 40.0)).astype(np.float32)


def huella(contenido_png: bytes) -> bytes | None:
    """La huella de una imagen, o None si no tiene tinta o no se puede leer."""
    try:
        imagen = Image.open(io.BytesIO(contenido_png))
        imagen.load()
    except Exception:
        return None
    if max(imagen.size) > 600:
        imagen.thumbnail((600, 600))
    tinta = _tinta(imagen)
    filas, columnas = np.where(tinta > 0)
    if filas.size < 12:
        return None
    caja = tinta[filas.min() : filas.max() + 1, columnas.min() : columnas.max() + 1]
    alto, ancho = caja.shape
    lado = max(alto, ancho)
    cuadro = np.zeros((lado, lado), dtype=np.float32)
    cuadro[(lado - alto) // 2 : (lado - alto) // 2 + alto, (lado - ancho) // 2 : (lado - ancho) // 2 + ancho] = caja
    chico = Image.fromarray((cuadro * 255).astype(np.uint8)).resize((LADO, LADO), Image.BOX)
    chico = chico.filter(ImageFilter.GaussianBlur(1.2))
    mapa = np.asarray(chico, dtype=np.float32)
    mapa = mapa / max(mapa.max(), 1.0) * 255.0
    proporcion = min(255, int(round(ancho / alto * 64)))
    return bytes([proporcion]) + mapa.astype(np.uint8).tobytes()


def _vector(h: bytes) -> np.ndarray:
    v = np.frombuffer(h, dtype=np.uint8, offset=1).astype(np.float32)
    v -= v.mean()
    norma = float(np.linalg.norm(v))
    return v / norma if norma else v


def ordenar_por_parecido(consulta: bytes, candidatas: list[tuple[int, bytes]], limite: int = 24) -> list[tuple[int, float]]:
    """(id, puntaje 0-100) de las candidatas más parecidas a la huella de la consulta."""
    if not candidatas:
        return []
    q = _vector(consulta)
    matriz = np.stack([_vector(h) for _, h in candidatas])
    coseno = np.clip(matriz @ q, 0.0, 1.0)
    prop_q = max(consulta[0], 1) / 64.0
    props = np.array([max(h[0], 1) / 64.0 for _, h in candidatas], dtype=np.float32)
    razon = np.minimum(props, prop_q) / np.maximum(props, prop_q)
    puntaje = coseno * (0.75 + 0.25 * razon) * 100.0
    orden = np.argsort(-puntaje)[:limite]
    return [(candidatas[i][0], round(float(puntaje[i]), 1)) for i in orden]
