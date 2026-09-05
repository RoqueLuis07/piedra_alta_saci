"""Limpieza y normalización de una marca recortada.

Entrada: recorte en escala de grises de una sola marca (tal como sale del
escáner, con sombras, borde de casilla y motas de lápiz).
Salida: máscara binaria de tinta a resolución original (para potrace) y un PNG
RGBA de lienzo fijo, trazo negro y fondo transparente (para el PDF).

El fondo transparente no es un detalle estético: al componer la planilla, cada
marca se dibuja encima de la casilla impresa, y un PNG con fondo blanco taparía
el recuadro y el sombreado de la grilla.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

import cv2
import numpy as np

from marcas import config


@dataclass
class ResultadoLimpieza:
    """Máscaras resultantes y métricas de control de calidad."""

    mascara: np.ndarray          # binaria (0/255), resolución original, tinta=255
    png_rgba: np.ndarray         # RGBA LIENZO_PX x LIENZO_PX, trazo negro
    tinta_pct: float             # % de píxeles de tinta dentro del bounding box
    componentes: int             # trazos separados que quedaron
    ancho_px: int                # ancho del trazo en la hoja original
    alto_px: int
    aspecto: float
    observaciones: list[str]     # motivos por los que conviene revisar a mano

    @property
    def revisar(self) -> bool:
        return bool(self.observaciones)

    def metricas(self) -> dict:
        d = asdict(self)
        d.pop("mascara")
        d.pop("png_rgba")
        d["revisar"] = self.revisar
        d["observaciones"] = "; ".join(self.observaciones)
        return d


def normalizar_iluminacion(gris: np.ndarray) -> np.ndarray:
    """Aplana el fondo del papel dividiendo por una estimación del mismo.

    Corrige la sombra del lomo del cuaderno y la caída de luz de las fotos con
    celular, que es lo que hace fallar a un umbral global tipo Otsu.
    """
    lado = max(gris.shape)
    k = max(15, (lado // 8) | 1)  # impar
    fondo = cv2.GaussianBlur(gris, (k, k), 0)
    plano = cv2.divide(gris, fondo, scale=255)
    return plano.astype(np.uint8)


def binarizar(gris: np.ndarray) -> np.ndarray:
    """Umbral adaptativo gaussiano. Devuelve tinta=255, papel=0."""
    lado = min(gris.shape)
    bloque = max(11, int(lado * config.BLOQUE_UMBRAL_REL) | 1)
    return cv2.adaptiveThreshold(
        gris,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        bloque,
        config.CONSTANTE_UMBRAL,
    )


def quitar_lineas_de_casilla(binaria: np.ndarray) -> np.ndarray:
    """Elimina restos del recuadro impreso que hayan quedado en el recorte.

    Se descartan los componentes que tocan el borde y que además son
    "lineales": ocupan casi todo el ancho o el alto del recorte pero son muy
    finos. Un trazo de marca puede tocar el borde, pero rara vez cumple las dos
    condiciones a la vez.
    """
    alto, ancho = binaria.shape
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(binaria, 8)
    salida = binaria.copy()
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        toca_borde = x <= 1 or y <= 1 or x + w >= ancho - 1 or y + h >= alto - 1
        if not toca_borde:
            continue
        largo_h = w >= 0.85 * ancho and h <= 0.10 * alto
        largo_v = h >= 0.85 * alto and w <= 0.10 * ancho
        if largo_h or largo_v:
            salida[etiquetas == i] = 0
    return salida


def quitar_ruido(binaria: np.ndarray) -> tuple[np.ndarray, int]:
    """Descarta motas y devuelve la cantidad de trazos que sobrevivieron."""
    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(binaria, 8)
    if n <= 1:
        return binaria, 0
    areas = stats[1:, cv2.CC_STAT_AREA]
    mayor = int(areas.max())
    umbral = max(config.RUIDO_AREA_MIN_PX, int(mayor * config.RUIDO_REL_MAYOR))
    salida = np.zeros_like(binaria)
    conservados = 0
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= umbral:
            salida[etiquetas == i] = 255
            conservados += 1
    return salida, conservados


def cerrar_trazos(binaria: np.ndarray, radio: int = 2) -> np.ndarray:
    """Cierra micro-cortes del trazo (lápiz que salta sobre el grano del papel)."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radio * 2 + 1, radio * 2 + 1))
    return cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, k)


def bbox_tinta(binaria: np.ndarray) -> tuple[int, int, int, int] | None:
    cols = np.where(binaria.any(axis=0))[0]
    filas = np.where(binaria.any(axis=1))[0]
    if cols.size == 0 or filas.size == 0:
        return None
    return int(cols[0]), int(filas[0]), int(cols[-1]) + 1, int(filas[-1]) + 1


def recortar_a_tinta(binaria: np.ndarray, margen_rel: float = 0.06) -> np.ndarray:
    """Recorta la máscara al trazo y le deja un margen proporcional.

    Se usa antes de vectorizar: así el SVG queda con el mismo encuadre que el
    PNG y se puede insertar en la planilla sin recalcular márgenes.
    """
    caja = bbox_tinta(binaria)
    if caja is None:
        return binaria
    x0, y0, x1, y1 = caja
    lado = max(x1 - x0, y1 - y0)
    margen = max(1, int(lado * margen_rel))
    alto, ancho = binaria.shape
    return binaria[
        max(0, y0 - margen):min(alto, y1 + margen),
        max(0, x0 - margen):min(ancho, x1 + margen),
    ]


def encajar_en_lienzo(binaria: np.ndarray, lienzo: int, margen_rel: float) -> np.ndarray:
    """Centra el trazo en un lienzo cuadrado conservando la proporción.

    Devuelve una máscara en escala de grises: el suavizado del reescalado
    (INTER_AREA) es lo que después da el canal alfa con bordes antialiasados.
    """
    caja = bbox_tinta(binaria)
    if caja is None:
        return np.zeros((lienzo, lienzo), np.uint8)
    x0, y0, x1, y1 = caja
    recorte = binaria[y0:y1, x0:x1]
    util = int(lienzo * (1 - 2 * margen_rel))
    h, w = recorte.shape
    escala = util / max(h, w)
    nw, nh = max(1, round(w * escala)), max(1, round(h * escala))
    interp = cv2.INTER_AREA if escala < 1 else cv2.INTER_LINEAR
    chico = cv2.resize(recorte, (nw, nh), interpolation=interp)
    salida = np.zeros((lienzo, lienzo), np.uint8)
    ox, oy = (lienzo - nw) // 2, (lienzo - nh) // 2
    salida[oy:oy + nh, ox:ox + nw] = chico
    return salida


def a_rgba(alfa: np.ndarray, color: tuple[int, int, int] = (0, 0, 0)) -> np.ndarray:
    """Compone el PNG final: color plano + alfa = cobertura de tinta."""
    alto, ancho = alfa.shape
    rgba = np.zeros((alto, ancho, 4), np.uint8)
    rgba[:, :, 0] = color[2]  # OpenCV escribe BGRA
    rgba[:, :, 1] = color[1]
    rgba[:, :, 2] = color[0]
    rgba[:, :, 3] = alfa
    return rgba


def limpiar_marca(
    recorte_gris: np.ndarray,
    *,
    inset: float | None = None,
    lienzo: int | None = None,
    margen_rel: float | None = None,
) -> ResultadoLimpieza:
    """Pipeline completo de una marca: gris sucio -> máscara + PNG normalizado."""
    inset = config.INSET_CASILLA if inset is None else inset
    lienzo = config.LIENZO_PX if lienzo is None else lienzo
    margen_rel = config.MARGEN_REL if margen_rel is None else margen_rel

    if recorte_gris.ndim == 3:
        recorte_gris = cv2.cvtColor(recorte_gris, cv2.COLOR_BGR2GRAY)

    # 1. Se descarta un anillo del borde: ahí vive el recuadro impreso.
    alto, ancho = recorte_gris.shape
    dy, dx = int(alto * inset), int(ancho * inset)
    interior = recorte_gris[dy:alto - dy, dx:ancho - dx] if dy and dx else recorte_gris

    # 2. Fondo plano -> 3. umbral adaptativo -> 4. limpieza.
    plano = normalizar_iluminacion(interior)
    binaria = binarizar(plano)
    binaria = quitar_lineas_de_casilla(binaria)
    binaria = cerrar_trazos(binaria)
    binaria, componentes = quitar_ruido(binaria)

    observaciones: list[str] = []
    caja = bbox_tinta(binaria)
    if caja is None:
        return ResultadoLimpieza(
            mascara=binaria,
            png_rgba=np.zeros((lienzo, lienzo, 4), np.uint8),
            tinta_pct=0.0,
            componentes=0,
            ancho_px=0,
            alto_px=0,
            aspecto=0.0,
            observaciones=["casilla vacía o trazo no detectado"],
        )

    x0, y0, x1, y1 = caja
    w, h = x1 - x0, y1 - y0
    tinta_pct = float(binaria[y0:y1, x0:x1].sum() / 255 / (w * h) * 100)
    aspecto = w / h

    if tinta_pct < config.TINTA_PCT_MIN:
        observaciones.append(f"muy poca tinta ({tinta_pct:.1f}%): trazo débil o ruido")
    if tinta_pct > config.TINTA_PCT_MAX:
        observaciones.append(f"demasiada tinta ({tinta_pct:.1f}%): sombra o borrón")
    if componentes > config.COMPONENTES_MAX:
        observaciones.append(f"{componentes} trazos sueltos: posible suciedad")
    if min(w, h) < 40:
        observaciones.append(f"trazo diminuto ({w}x{h} px): revisar resolución")

    alfa = encajar_en_lienzo(binaria, lienzo, margen_rel)
    return ResultadoLimpieza(
        mascara=binaria,
        png_rgba=a_rgba(alfa),
        tinta_pct=round(tinta_pct, 2),
        componentes=componentes,
        ancho_px=w,
        alto_px=h,
        aspecto=round(aspecto, 3),
        observaciones=observaciones,
    )
