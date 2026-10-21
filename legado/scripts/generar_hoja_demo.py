"""Genera hojas de prueba que imitan una planilla escaneada.

Sirve para probar el pipeline completo sin tener escaneos reales a mano:
dibuja marcas con trazo tembloroso dentro de una grilla impresa y después
"ensucia" la hoja como lo haría un escáner (inclinación, sombra, ruido, foco).

Uso:
    python scripts/generar_hoja_demo.py --hojas 2 --salida datos/escaneos
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

import cv2
import numpy as np

ANCHO, ALTO = 2480, 3508          # A4 a 300 dpi
COLUMNAS, FILAS = 4, 5


def trazo_tembloroso(
    lienzo: np.ndarray, puntos: list[tuple[float, float]], grosor: int, rng: random.Random
) -> None:
    """Dibuja una polilínea con desvíos suaves, como un pulso humano."""
    densos: list[tuple[int, int]] = []
    for (x1, y1), (x2, y2) in zip(puntos, puntos[1:]):
        largo = math.hypot(x2 - x1, y2 - y1)
        pasos = max(2, int(largo / 6))
        for i in range(pasos + 1):
            t = i / pasos
            dx = rng.gauss(0, 1.6)
            dy = rng.gauss(0, 1.6)
            densos.append((int(x1 + (x2 - x1) * t + dx), int(y1 + (y2 - y1) * t + dy)))
    for a, b in zip(densos, densos[1:]):
        g = max(2, grosor + rng.randint(-1, 1))
        cv2.line(lienzo, a, b, 40, g, cv2.LINE_AA)


def arco_tembloroso(
    lienzo, centro, radios, ang_ini, ang_fin, grosor, rng: random.Random
) -> None:
    cx, cy = centro
    rx, ry = radios
    puntos = []
    pasos = 60
    for i in range(pasos + 1):
        a = math.radians(ang_ini + (ang_fin - ang_ini) * i / pasos)
        puntos.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    trazo_tembloroso(lienzo, puntos, grosor, rng)


def dibujar_marca(lienzo, caja, rng: random.Random) -> None:
    """Dibuja una marca al azar dentro de la casilla (letras, barras, arcos)."""
    x, y, w, h = caja
    m = int(min(w, h) * 0.22)
    x0, y0, x1, y1 = x + m, y + m, x + w - m, y + h - m
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    grosor = rng.randint(5, 9)
    figura = rng.choice(
        ["A", "V", "T", "H", "L", "circulo", "circulo_barra", "corazon", "ancla", "Z"]
    )

    if figura == "A":
        trazo_tembloroso(lienzo, [(x0, y1), (cx, y0), (x1, y1)], grosor, rng)
        trazo_tembloroso(
            lienzo, [(x0 + w * 0.15, cy + h * 0.10), (x1 - w * 0.15, cy + h * 0.10)],
            grosor, rng,
        )
    elif figura == "V":
        trazo_tembloroso(lienzo, [(x0, y0), (cx, y1), (x1, y0)], grosor, rng)
    elif figura == "T":
        trazo_tembloroso(lienzo, [(x0, y0), (x1, y0)], grosor, rng)
        trazo_tembloroso(lienzo, [(cx, y0), (cx, y1)], grosor, rng)
    elif figura == "H":
        trazo_tembloroso(lienzo, [(x0, y0), (x0, y1)], grosor, rng)
        trazo_tembloroso(lienzo, [(x1, y0), (x1, y1)], grosor, rng)
        trazo_tembloroso(lienzo, [(x0, cy), (x1, cy)], grosor, rng)
    elif figura == "L":
        trazo_tembloroso(lienzo, [(x0, y0), (x0, y1), (x1, y1)], grosor, rng)
    elif figura == "Z":
        trazo_tembloroso(lienzo, [(x0, y0), (x1, y0), (x0, y1), (x1, y1)], grosor, rng)
    elif figura == "circulo":
        arco_tembloroso(lienzo, (cx, cy), ((x1 - x0) / 2, (y1 - y0) / 2), 0, 360, grosor, rng)
    elif figura == "circulo_barra":
        arco_tembloroso(lienzo, (cx, cy), ((x1 - x0) / 2, (y1 - y0) / 2), 0, 360, grosor, rng)
        trazo_tembloroso(lienzo, [(x0, cy), (x1, cy)], grosor, rng)
    elif figura == "corazon":
        r = (x1 - x0) / 4
        arco_tembloroso(lienzo, (cx - r, cy - r * 0.5), (r, r), 180, 360, grosor, rng)
        arco_tembloroso(lienzo, (cx + r, cy - r * 0.5), (r, r), 180, 360, grosor, rng)
        trazo_tembloroso(lienzo, [(cx - 2 * r, cy - r * 0.5), (cx, y1)], grosor, rng)
        trazo_tembloroso(lienzo, [(cx + 2 * r, cy - r * 0.5), (cx, y1)], grosor, rng)
    else:  # ancla
        trazo_tembloroso(lienzo, [(cx, y0), (cx, y1)], grosor, rng)
        trazo_tembloroso(lienzo, [(x0 + w * 0.1, y0 + h * 0.18), (x1 - w * 0.1, y0 + h * 0.18)], grosor, rng)
        arco_tembloroso(lienzo, (cx, cy + h * 0.15), ((x1 - x0) / 2.4, (y1 - y0) / 3), 20, 160, grosor, rng)


def dibujar_hoja(numero: int, rng: random.Random) -> np.ndarray:
    hoja = np.full((ALTO, ANCHO), 250, np.uint8)

    cv2.putText(hoja, "PIEDRA ALTA S.A.C.I. - REGISTRO DE MARCAS", (180, 190),
                cv2.FONT_HERSHEY_SIMPLEX, 2.0, 90, 4, cv2.LINE_AA)
    cv2.putText(hoja, f"HOJA {numero:02d}", (180, 270),
                cv2.FONT_HERSHEY_SIMPLEX, 1.4, 110, 3, cv2.LINE_AA)

    margen_x, margen_y = 200, 380
    ancho_util = ANCHO - 2 * margen_x
    alto_util = ALTO - margen_y - 220
    paso_x = ancho_util // COLUMNAS
    paso_y = alto_util // FILAS
    lado_x, lado_y = int(paso_x * 0.86), int(paso_y * 0.78)

    for f in range(FILAS):
        for c in range(COLUMNAS):
            x = margen_x + c * paso_x + (paso_x - lado_x) // 2
            y = margen_y + f * paso_y + (paso_y - lado_y) // 2
            cv2.rectangle(hoja, (x, y), (x + lado_x, y + lado_y), 120, 3)
            cv2.putText(hoja, f"H{numero:02d}-{f + 1}{c + 1}", (x, y - 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, 130, 2, cv2.LINE_AA)
            if rng.random() < 0.92:          # alguna casilla queda vacía, a propósito
                dibujar_marca(hoja, (x, y, lado_x, lado_y), rng)
    return hoja


def simular_escaneo(hoja: np.ndarray, rng: random.Random) -> np.ndarray:
    """Inclinación, sombra de lomo, grano del papel y foco imperfecto."""
    alto, ancho = hoja.shape
    angulo = rng.uniform(-1.8, 1.8)
    m = cv2.getRotationMatrix2D((ancho / 2, alto / 2), angulo, 1.0)
    salida = cv2.warpAffine(hoja, m, (ancho, alto), flags=cv2.INTER_CUBIC,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=248)

    gx = np.linspace(rng.uniform(0.80, 0.95), 1.0, ancho)
    gy = np.linspace(1.0, rng.uniform(0.88, 1.0), alto)
    sombra = np.outer(gy, gx)
    salida = np.clip(salida.astype(np.float32) * sombra, 0, 255)

    salida += np.random.normal(0, 4.0, salida.shape)
    salida = np.clip(salida, 0, 255).astype(np.uint8)
    return cv2.GaussianBlur(salida, (3, 3), 0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hojas", type=int, default=2)
    ap.add_argument("--salida", type=Path, default=Path("datos/escaneos"))
    ap.add_argument("--semilla", type=int, default=7)
    args = ap.parse_args()

    args.salida.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.semilla)
    np.random.seed(args.semilla)
    for n in range(1, args.hojas + 1):
        ruta = args.salida / f"hoja_{n:02d}.png"
        cv2.imwrite(str(ruta), simular_escaneo(dibujar_hoja(n, rng), rng))
        print(f"escrita {ruta}")


if __name__ == "__main__":
    main()
