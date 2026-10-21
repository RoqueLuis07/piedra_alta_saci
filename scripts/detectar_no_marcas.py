"""Detecta, entre las marcas del catálogo, las que PROBABLEMENTE no son una marca
dibujada a mano: trazos de anulación (la raya o la X con que se tachan las
casillas sin usar), líneas de tabla, sellos o texto impreso, fragmentos y manchas.

Es sólo una ayuda para revisar: nunca da de baja nada. Las candidatas se marcan
``sospechosa_calidad`` + estado ``revisar`` con el motivo ``no_es_marca:<categoria>``
y quedan en la pantalla de Revisión para que una persona decida.

    python scripts/detectar_no_marcas.py            # sólo informa (no escribe)
    python scripts/detectar_no_marcas.py --aplicar  # marca las candidatas en la base
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

LADO = 512
CATEGORIAS = {
    "anulacion": "trazo de anulación (raya o X que tacha la casilla)",
    "linea_de_tabla": "línea o borde de tabla",
    "texto_o_sello": "texto impreso o sello",
    "fragmento": "fragmento o mancha sin forma de marca",
    "vacia": "casi sin tinta",
}
PREFIJO = "no_es_marca:"


def mascara_tinta(png: bytes) -> np.ndarray:
    imagen = Image.open(io.BytesIO(png)).convert("RGBA")
    if imagen.size != (LADO, LADO):
        imagen = imagen.resize((LADO, LADO), Image.LANCZOS)
    return (np.array(imagen)[..., 3] > 96).astype(np.uint8) * 255


MIN_PIEZA = 40      # px de área: por debajo es polvo del escaneo, no trazo
MIN_PUNTO = 8       # px: los puntos de una trama de impresión (entre este y MIN_PIEZA)


def caracteristicas(mascara: np.ndarray) -> dict:
    total_bruto = int((mascara > 0).sum())
    f: dict = {"cobertura": total_bruto / mascara.size}
    vacio = {"n_comp": 0, "n_puntos": 0, "mayor": 0.0, "lineas": 0.0, "largo": 0.0, "diagonal": 0.0,
             "altura_uniforme": 0.0, "altura_rel": 0.0, "filas": 0, "max_alto": 0.0, "max_ancho": 0.0,
             "vertices": 0}
    if total_bruto == 0:
        return {**f, **vacio}

    n, etiquetas, stats, _ = cv2.connectedComponentsWithStats(mascara, connectivity=8)
    areas_todas = stats[1:, cv2.CC_STAT_AREA]
    f["n_puntos"] = int(((areas_todas >= MIN_PUNTO) & (areas_todas < MIN_PIEZA)).sum())
    # Se analiza sólo lo que tiene cuerpo de trazo; el polvo no cuenta.
    grandes = [i + 1 for i, a in enumerate(areas_todas) if a >= MIN_PIEZA]
    if not grandes:
        return {**f, **{k: v for k, v in vacio.items() if k != "n_puntos"}}
    limpia = np.where(np.isin(etiquetas, grandes), 255, 0).astype(np.uint8)
    total = int((limpia > 0).sum())
    comps = [stats[i] for i in grandes]
    areas = sorted((int(s[cv2.CC_STAT_AREA]) for s in comps), reverse=True)
    f["n_comp"] = len(comps)
    f["mayor"] = areas[0] / total

    # ¿Qué parte de la tinta cae sobre segmentos rectos largos?
    dt = cv2.distanceTransform(limpia, cv2.DIST_L2, 3)
    grosor = max(3.0, 2.0 * float(np.percentile(dt[limpia > 0], 90)))
    segmentos = cv2.HoughLinesP(limpia, 1, np.pi / 180, threshold=30,
                                minLineLength=int(0.28 * LADO), maxLineGap=14)
    capa = np.zeros_like(limpia)
    largo, diag_peso = 0.0, 0.0
    f["lineas"] = 0.0
    if segmentos is not None:
        segmentos = np.asarray(segmentos).reshape(-1, 4)   # (N,1,4) en cv2 4.x, (N,4) en 5.x
        todo = 0.0
        for x1, y1, x2, y2 in segmentos:
            cv2.line(capa, (int(x1), int(y1)), (int(x2), int(y2)), 255, 1)
            ln = math.hypot(x2 - x1, y2 - y1)
            todo += ln
            largo = max(largo, ln)
            ang = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 90
            if min(ang, 90 - ang) > 14:          # inclinada: ni horizontal ni vertical
                diag_peso += ln
        diag_peso = diag_peso / todo if todo else 0.0
        a_linea = cv2.distanceTransform(255 - capa, cv2.DIST_L2, 3)
        f["lineas"] = float(((a_linea <= grosor * 0.6 + 3) & (limpia > 0)).sum() / total)
    f["largo"] = largo / LADO
    f["diagonal"] = diag_peso

    # Tamaño de la pieza más grande y cuán "quebrada" es su silueta: una raya o
    # una V tienen pocos vértices; una firma o un monograma, muchos.
    f["max_alto"] = max(int(s[cv2.CC_STAT_HEIGHT]) for s in comps) / LADO
    f["max_ancho"] = max(int(s[cv2.CC_STAT_WIDTH]) for s in comps) / LADO
    contornos, _ = cv2.findContours(limpia, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    mayor = max(contornos, key=cv2.contourArea)
    f["vertices"] = len(cv2.approxPolyDP(mayor, 0.012 * cv2.arcLength(mayor, True), True))

    # Texto impreso: varias piezas de altura parecida, chicas, en pocas filas.
    f["altura_uniforme"], f["altura_rel"], f["filas"] = 0.0, 0.0, 0
    if len(comps) >= 5:
        alturas = np.array([s[cv2.CC_STAT_HEIGHT] for s in comps], dtype=float)
        centros = sorted(float(s[cv2.CC_STAT_TOP] + s[cv2.CC_STAT_HEIGHT] / 2) for s in comps)
        med = float(np.median(alturas))
        f["altura_uniforme"] = float(np.mean((alturas > 0.65 * med) & (alturas < 1.5 * med)))
        f["altura_rel"] = med / LADO
        f["filas"] = 1 + sum(1 for a, b in zip(centros, centros[1:]) if b - a > 0.7 * med)
    return f


def clasificar(f: dict) -> tuple[str | None, str]:
    """(categoría o None, razón corta). Reglas simples y legibles, calibradas
    mirando muestras de cada categoría sobre el catálogo real."""
    if f["cobertura"] < 0.0012 or f["n_comp"] == 0:
        return "vacia", "casi sin tinta"
    if f["n_puntos"] >= 120:
        return "fragmento", f"trama de puntos ({f['n_puntos']} puntitos)"
    # Rayas, X y V: casi toda la tinta es recta. Segunda vía, más tolerante al
    # polvo del escaneo, pero exigiendo una silueta simple (pocos vértices) para
    # no confundir una firma con trazos rectos con una raya.
    recta = (f["lineas"] >= 0.82 and f["largo"] >= 0.30 and f["n_comp"] <= 6) or (
        f["lineas"] >= 0.65 and f["largo"] >= 0.40 and f["vertices"] <= 10 and f["n_comp"] <= 8
    )
    if recta:
        if f["diagonal"] >= 0.5:
            return "anulacion", f"{f['lineas']:.0%} de la tinta es recta e inclinada"
        return "linea_de_tabla", f"{f['lineas']:.0%} de la tinta es una línea recta"
    if f["n_comp"] >= 5 and f["altura_uniforme"] >= 0.8 and f["max_alto"] < 0.30 and f["filas"] <= 3:
        return "texto_o_sello", f"{f['n_comp']} letras parejas en {f['filas']} fila(s)"
    if f["cobertura"] < 0.004:
        return "fragmento", "muy poca tinta"
    return None, ""


def analizar(png: bytes) -> tuple[str | None, str, dict]:
    f = caracteristicas(mascara_tinta(png))
    categoria, razon = clasificar(f)
    return categoria, razon, f


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--aplicar", action="store_true", help="escribir las marcas en la base (por defecto sólo informa)")
    ap.add_argument("--como", choices=("revisar", "baja"), default="revisar",
                    help="estado que se les pone: 'revisar' (por defecto, para decidir a mano) o 'baja' "
                         "(sólo para un entorno de demo: las saca del catálogo sin revisarlas)")
    ap.add_argument("--categorias", default=",".join(CATEGORIAS),
                    help="categorías a aplicar, separadas por coma (por defecto, todas)")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    import psycopg2
    from psycopg2.extras import RealDictCursor

    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor)
    cuentas: Counter = Counter()
    candidatas: list[tuple[int, str, str]] = []
    with conexion.cursor() as cur:
        cur.execute("SELECT id FROM marcas WHERE activo AND estado = 'activa' AND archivo_png IS NOT NULL ORDER BY id")
        ids = [r["id"] for r in cur.fetchall()]
    for marca_id in ids:
        with conexion.cursor() as cur:
            cur.execute("SELECT archivo_png FROM marcas WHERE id = %s", (marca_id,))
            png = bytes(cur.fetchone()["archivo_png"])
        categoria, razon, _ = analizar(png)
        if categoria:
            candidatas.append((marca_id, categoria, razon))
            cuentas[categoria] += 1
    print(f"Revisadas {len(ids)} marcas activas. Candidatas a 'no es una marca': {len(candidatas)}")
    for categoria, n in cuentas.most_common():
        print(f"  {n:5d}  {categoria}: {CATEGORIAS[categoria]}")
    elegidas = {c.strip() for c in args.categorias.split(",") if c.strip()}
    candidatas = [c for c in candidatas if c[1] in elegidas]
    if args.aplicar and candidatas:
        with conexion.cursor() as cur:
            for marca_id, categoria, razon in candidatas:
                cur.execute(
                    "UPDATE marcas SET estado = %s, sospechosa_calidad = true, motivo_calidad = %s "
                    "WHERE id = %s AND estado = 'activa'",
                    (args.como, f"{PREFIJO}{categoria} ({razon})", marca_id),
                )
        conexion.commit()
        print(f"Marcadas {len(candidatas)} como «{args.como}»." + (" Ninguna se dio de baja." if args.como == "revisar" else ""))
    elif candidatas:
        print("(No se escribió nada: usá --aplicar para marcarlas.)")
    conexion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
