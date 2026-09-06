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


def _detectar_celdas_por_huecos(
    gris: np.ndarray,
    binaria: np.ndarray,
    *,
    area_min_rel: float,
    area_max_rel: float,
) -> list[tuple[int, int, int, int]]:
    """Casillas dibujadas como rectángulos independientes, con espacio entre sí.

    Es el estilo de `marcas/pdf/planilla.py::generar_plantilla_captura`: cada
    casilla es su propio contorno cerrado, así que alcanza con pedir los
    contornos que son "huecos" (tienen padre) dentro de la máscara de líneas.
    Falla en una tabla continua real, donde el borde exterior puede estar
    interrumpido (ver `_detectar_celdas_por_lineas`).
    """
    alto, ancho = gris.shape
    area_hoja = alto * ancho
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
    return cajas


def _agrupar_picos(indices: list[int], separacion_min: int) -> list[int]:
    """Reduce una lista de posiciones de píxel a un centro por racha contigua.

    ``separacion_min`` tolera el "doble borde" que deja el JPEG de una foto de
    celular alrededor de una línea impresa (ringing de la compresión).
    """
    if not indices:
        return []
    grupos = [[indices[0]]]
    for i in indices[1:]:
        if i - grupos[-1][-1] <= separacion_min:
            grupos[-1].append(i)
        else:
            grupos.append([i])
    return [int(np.mean(g)) for g in grupos]


def _lineas_regulares(posiciones: list[int], tolerancia_rel: float = 0.15) -> list[int]:
    """De todas las posiciones candidatas, la corrida más larga con paso parejo.

    Un formulario real trae más de una tabla (el encabezado con el N° de guía,
    la grilla de marcas). Sus líneas se mezclan en el mismo perfil de
    proyección, pero sólo la grilla de marcas tiene muchas filas/columnas
    igual de espaciadas: es la corrida que se busca, sin necesidad de saber de
    antemano cuántas filas o columnas tiene.
    """
    if len(posiciones) < 3:
        return posiciones
    posiciones = sorted(posiciones)
    gaps = [b - a for a, b in zip(posiciones, posiciones[1:])]
    mejor = (0, 0)
    i = 0
    while i < len(gaps):
        j = i
        base = gaps[i]
        while j + 1 < len(gaps) and abs(gaps[j + 1] - base) <= tolerancia_rel * base:
            j += 1
        if (j - i) > (mejor[1] - mejor[0]):
            mejor = (i, j)
        i = j + 1
    return posiciones[mejor[0]:mejor[1] + 2]


def _posiciones_de_lineas(mascara: np.ndarray, eje: int) -> list[int]:
    """Posiciones de las líneas de una grilla, tolerando tramos borrados.

    Se prueba con varios umbrales (relativos a la fracción de píxeles blancos
    por fila/columna) y se queda con el que arma la corrida más larga y
    pareja: una línea real casi siempre sigue siendo la corrida más larga
    aunque un anillo de carpeta, una mancha o una esquina rota le borren un
    tramo, porque conserva señal en el resto de su longitud.
    """
    perfil = (mascara > 0).mean(axis=eje)
    mejor: list[int] = []
    for umbral in (0.10, 0.08, 0.07, 0.06, 0.05):
        candidatos = np.where(perfil > umbral)[0]
        grupos = _agrupar_picos(list(candidatos), separacion_min=25)
        regulares = _lineas_regulares(grupos)
        if len(regulares) > len(mejor):
            mejor = regulares
    return mejor


def _detectar_celdas_por_lineas(
    binaria: np.ndarray,
    *,
    area_min_rel: float,
    area_max_rel: float,
) -> list[tuple[int, int, int, int]]:
    """Casillas de una tabla continua, como la de un formulario oficial.

    Acá las celdas comparten el borde con sus vecinas, así que el borde
    exterior de la tabla no tiene por qué estar intacto para reconocer cada
    celda (al revés que en ``_detectar_celdas_por_huecos``): se ubican
    directamente las líneas de la grilla por su posición, y las celdas salen
    de cruzar cada línea vertical con cada horizontal.
    """
    alto, ancho = binaria.shape
    area_hoja = alto * ancho
    horiz, vert = _mascaras_de_lineas(binaria)

    xs = _posiciones_de_lineas(vert, eje=0)
    ys = _posiciones_de_lineas(horiz, eje=1)
    if len(xs) < 2 or len(ys) < 2:
        return []

    cajas = []
    for y0, y1 in zip(ys, ys[1:]):
        for x0, x1 in zip(xs, xs[1:]):
            w, h = x1 - x0, y1 - y0
            area = w * h
            if not (area_min_rel * area_hoja <= area <= area_max_rel * area_hoja):
                continue
            # Acá la celda va "de línea a línea": el borde compartido con la
            # vecina queda a mitad de camino, con su propio grosor más el
            # halo de compresión de una foto. Sin este margen, un resto de
            # línea sobrevive dentro del recorte y limpiar_marca lo toma
            # como si fuera parte del trazo.
            margen = max(6, round(0.035 * min(w, h)))
            cajas.append((
                x0 + margen, y0 + margen,
                w - 2 * margen, h - 2 * margen,
            ))
    return cajas


def _mas_completa(a: list[tuple[int, int, int, int]], b: list[tuple[int, int, int, int]]):
    """Prefiere la lista con más celdas y, a igualdad, la de tamaños más parejos."""
    def puntaje(cajas):
        if not cajas:
            return (0, 0.0)
        anchos = [c[2] for c in cajas]
        return (len(cajas), -float(np.std(anchos)) / max(1.0, np.mean(anchos)))
    return a if puntaje(a) >= puntaje(b) else b


def detectar_celdas(
    gris: np.ndarray,
    *,
    area_min_rel: float = 0.004,
    area_max_rel: float = 0.30,
) -> list[Celda]:
    """Encuentra las casillas de una planilla impresa.

    Prueba primero el método de huecos (casillas dibujadas como rectángulos
    independientes: la plantilla de captura propia). Si el resultado tiene
    huecos irregulares —señal de que en realidad es una tabla continua con el
    borde exterior dañado, como una foto de un formulario ya impreso—, se
    compara contra el método de líneas y se usa el que arme la grilla más
    completa.
    """
    binaria = _binaria_inversa(gris)
    por_huecos = _detectar_celdas_por_huecos(
        gris, binaria, area_min_rel=area_min_rel, area_max_rel=area_max_rel
    )
    filas_huecos = _ordenar_en_filas(por_huecos)
    conteos = {}
    for c in filas_huecos:
        conteos[c.fila] = conteos.get(c.fila, 0) + 1
    grilla_pareja = len(set(conteos.values())) <= 1 and len(filas_huecos) >= 4

    if grilla_pareja:
        return filas_huecos

    por_lineas = _detectar_celdas_por_lineas(
        binaria, area_min_rel=area_min_rel, area_max_rel=area_max_rel
    )
    return _ordenar_en_filas(_mas_completa(por_huecos, por_lineas))


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
