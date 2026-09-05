"""Segmentación de una hoja escaneada en marcas individuales.

Dos modos:

* ``grilla``  la hoja es una planilla impresa con casillas. Se detectan las
  líneas del recuadro por morfología y cada celda se recorta por separado.
  Es el modo recomendado: el orden fila/columna sirve para asignar el código
  de cada marca sin tipear nada.
* ``libre``   las marcas están dibujadas sueltas sobre papel en blanco. Se
  agrupan los trazos cercanos y se recorta cada grupo.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class Celda:
    """Un recorte localizado dentro de la hoja."""

    fila: int
    columna: int
    x: int
    y: int
    ancho: int
    alto: int

    @property
    def caja(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.x + self.ancho, self.y + self.alto

    def recortar(self, imagen: np.ndarray) -> np.ndarray:
        x0, y0, x1, y1 = self.caja
        return imagen[y0:y1, x0:x1]


def _binaria_inversa(gris: np.ndarray) -> np.ndarray:
    suave = cv2.GaussianBlur(gris, (5, 5), 0)
    return cv2.adaptiveThreshold(
        suave, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 35, 15
    )


def _mascaras_de_lineas(binaria: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Aísla los segmentos horizontales y verticales largos (la grilla)."""
    alto, ancho = binaria.shape
    kh = cv2.getStructuringElement(cv2.MORPH_RECT, (max(12, ancho // 30), 1))
    kv = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(12, alto // 30)))
    horiz = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, kh, iterations=1)
    vert = cv2.morphologyEx(binaria, cv2.MORPH_OPEN, kv, iterations=1)
    return horiz, vert


def enderezar(gris: np.ndarray, max_grados: float = 12.0) -> tuple[np.ndarray, float]:
    """Corrige la inclinación de la hoja usando las líneas horizontales.

    Devuelve la imagen rotada y el ángulo aplicado. Si no encuentra líneas
    confiables no toca la imagen: es preferible no rotar a rotar mal.
    """
    binaria = _binaria_inversa(gris)
    horiz, _ = _mascaras_de_lineas(binaria)
    lineas = cv2.HoughLinesP(
        horiz, 1, np.pi / 720, threshold=120,
        minLineLength=max(80, gris.shape[1] // 6), maxLineGap=20,
    )
    if lineas is None or len(lineas) == 0:
        return gris, 0.0
    # OpenCV devuelve (N,1,4) o (N,4) según la versión.
    angulos = []
    for x1, y1, x2, y2 in np.asarray(lineas).reshape(-1, 4):
        if x2 == x1:
            continue
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) <= max_grados:
            angulos.append(a)
    if not angulos:
        return gris, 0.0
    angulo = float(np.median(angulos))
    if abs(angulo) < 0.1:
        return gris, 0.0
    alto, ancho = gris.shape
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 1.0)
    rotada = cv2.warpAffine(
        gris, m, (ancho, alto), flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return rotada, round(angulo, 3)


def _ordenar_en_filas(cajas: list[tuple[int, int, int, int]]) -> list[Celda]:
    """Agrupa por bandas horizontales y numera fila/columna en orden de lectura."""
    if not cajas:
        return []
    alto_medio = float(np.median([h for _, _, _, h in cajas]))
    tolerancia = alto_medio * 0.5
    por_y = sorted(cajas, key=lambda c: c[1])
    filas: list[list[tuple[int, int, int, int]]] = [[por_y[0]]]
    for caja in por_y[1:]:
        centro = caja[1] + caja[3] / 2
        ref = filas[-1][0][1] + filas[-1][0][3] / 2
        if abs(centro - ref) <= tolerancia:
            filas[-1].append(caja)
        else:
            filas.append([caja])
    celdas: list[Celda] = []
    for i, fila in enumerate(filas, start=1):
        for j, (x, y, w, h) in enumerate(sorted(fila, key=lambda c: c[0]), start=1):
            celdas.append(Celda(fila=i, columna=j, x=x, y=y, ancho=w, alto=h))
    return celdas


def detectar_celdas(
    gris: np.ndarray,
    *,
    area_min_rel: float = 0.004,
    area_max_rel: float = 0.30,
) -> list[Celda]:
    """Encuentra las casillas de una planilla impresa."""
    alto, ancho = gris.shape
    area_hoja = alto * ancho
    binaria = _binaria_inversa(gris)
    horiz, vert = _mascaras_de_lineas(binaria)
    grilla = cv2.dilate(cv2.bitwise_or(horiz, vert), np.ones((3, 3), np.uint8))

    contornos, jerarquia = cv2.findContours(
        grilla, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE
    )
    if jerarquia is None:
        return []

    cajas: list[tuple[int, int, int, int]] = []
    for i, cnt in enumerate(contornos):
        if jerarquia[0][i][3] == -1:      # sólo los huecos internos son casillas
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if not (area_min_rel * area_hoja <= area <= area_max_rel * area_hoja):
            continue
        if cv2.contourArea(cnt) / area < 0.70:   # el hueco debe ser rectangular
            continue
        if not 0.25 <= w / h <= 4.0:
            continue
        cajas.append((x, y, w, h))
    return _ordenar_en_filas(cajas)


def detectar_grupos(
    gris: np.ndarray,
    *,
    area_min_rel: float = 0.0005,
    separacion_rel: float = 0.012,
) -> list[Celda]:
    """Modo libre: agrupa trazos cercanos en marcas, sin grilla impresa."""
    alto, ancho = gris.shape
    area_hoja = alto * ancho
    binaria = _binaria_inversa(gris)
    radio = max(3, int(min(alto, ancho) * separacion_rel))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radio, radio))
    unidos = cv2.morphologyEx(binaria, cv2.MORPH_CLOSE, k, iterations=2)
    unidos = cv2.dilate(unidos, k, iterations=1)

    n, _, stats, _ = cv2.connectedComponentsWithStats(unidos, 8)
    cajas = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w * h < area_min_rel * area_hoja or w * h > 0.30 * area_hoja:
            continue
        margen = radio
        x0, y0 = max(0, x - margen), max(0, y - margen)
        x1, y1 = min(ancho, x + w + margen), min(alto, y + h + margen)
        cajas.append((x0, y0, x1 - x0, y1 - y0))
    return _ordenar_en_filas(cajas)


def segmentar_hoja(
    imagen: np.ndarray,
    *,
    modo: str = "grilla",
    enderezar_hoja: bool = True,
) -> tuple[np.ndarray, list[Celda], float]:
    """Prepara la hoja y devuelve (gris enderezado, celdas, ángulo corregido)."""
    gris = imagen if imagen.ndim == 2 else cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)
    angulo = 0.0
    if enderezar_hoja:
        gris, angulo = enderezar(gris)
    if modo == "grilla":
        celdas = detectar_celdas(gris)
    elif modo == "libre":
        celdas = detectar_grupos(gris)
    else:
        raise ValueError(f"modo desconocido: {modo!r} (use 'grilla' o 'libre')")
    return gris, celdas, angulo
