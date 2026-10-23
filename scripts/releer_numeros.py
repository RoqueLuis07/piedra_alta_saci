"""Segunda lectura (independiente) de los N.º de guía de TODAS las páginas escaneadas.

La primera lectura (``indexar_escaneos.py``) leyó cada página una vez, a 1600 px. Acá cada página se vuelve a leer
desde el ORIGINAL con otra resolución, y cada número de 8 dígitos que aparece se relee otra vez recortado y agrandado
(2x y 3x). Al final se compara con la primera lectura: donde las lecturas coinciden el número es confiable; donde no,
queda marcado para mirarlo a ojo (se guarda el recorte de la línea).

No toca ninguna base: escribe ``lecturas.jsonl`` y los recortes en la carpeta de salida. Se puede cortar y reanudar.

    python scripts/releer_numeros.py --escaneos "<carpeta Escaneos>" --indice ..\\demo\\indice_escaneos --salida ..\\demo\\relectura --trabajadores 4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for ruta in (str(RAIZ), str(RAIZ / "scripts")):
    if ruta not in sys.path:
        sys.path.insert(0, ruta)

from indexar_escaneos import RUC_PIEDRA_ALTA, _iniciar_trabajador, cargar_imagen, listar_paginas, reducir  # noqa: E402

LADO_RELECTURA = 2200
OCHO = re.compile(r"(?<!\d)\d{8}(?!\d)")
DIGITOS_CONFUNDIDOS = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "|": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})

_motor = None


def _ocr(imagen):
    import numpy as np

    resultado, _ = _motor_global()(np.array(imagen))
    return [{"texto": r[1], "caja": [[float(x), float(y)] for x, y in r[0]], "conf": float(r[2])} for r in (resultado or [])]


def _motor_global():
    import indexar_escaneos

    return indexar_escaneos._motor


def _solo_digitos(texto: str) -> str:
    return re.sub(r"\D", "", texto.translate(DIGITOS_CONFUNDIDOS))


def _lecturas_de_recorte(im, caja, esc: float, margen: int = 10) -> list[str]:
    """Relee una línea recortada del original y agrandada 2x y 3x. Devuelve los números de 8 dígitos que salieron."""
    from PIL import ImageOps

    xs, ys = [p[0] for p in caja], [p[1] for p in caja]
    recorte = im.crop((max(0, int((min(xs) - margen) * esc)), max(0, int((min(ys) - margen) * esc)),
                       min(im.size[0], int((max(xs) + margen) * esc)), min(im.size[1], int((max(ys) + margen) * esc))))
    salida = []
    for factor in (2.0, 3.0):
        grande = recorte.resize((int(recorte.size[0] * factor), int(recorte.size[1] * factor)))
        for variante in (grande, ImageOps.autocontrast(grande.convert("L")).convert("RGB")):
            for l in _ocr(variante):
                sd = _solo_digitos(l["texto"])
                salida += OCHO.findall(sd) if len(sd) >= 8 else []
    return salida


def releer(pagina: dict, primera: dict | None, salida: str) -> dict:
    salida = Path(salida)
    im = cargar_imagen(pagina)
    grande = reducir(im, LADO_RELECTURA)
    esc = im.size[0] / grande.size[0]
    lineas = _ocr(grande)
    candidatos = []
    for l in lineas:
        for n in OCHO.findall(l["texto"].replace(" ", "")):
            if n != RUC_PIEDRA_ALTA:
                candidatos.append((n, l))
    detalle = []
    for n, l in candidatos[:6]:
        extras = _lecturas_de_recorte(im, l["caja"], esc)
        votos = Counter([n] * 2 + extras)           # la lectura de la página entera vale doble
        mejor, cuantos = votos.most_common(1)[0]
        detalle.append({"leido": n, "recorte": dict(votos), "ganador": mejor, "votos": cuantos, "conf": round(l["conf"], 3)})
        base = f"{pagina['carpeta']}_{Path(pagina['archivo']).stem}"
        xs, ys = [p[0] for p in l["caja"]], [p[1] for p in l["caja"]]
        from PIL import Image  # noqa: F401

        im.crop((max(0, int((min(xs) - 30) * esc)), max(0, int((min(ys) - 20) * esc)),
                 min(im.size[0], int((max(xs) + 30) * esc)), min(im.size[1], int((max(ys) + 20) * esc)))
                ).save(salida / "recortes" / f"{base}_{n}.png")
    primera_n = (primera or {}).get("numero_guia")
    ganadores = [d["ganador"] for d in detalle]
    return {
        "carpeta": pagina["carpeta"], "archivo": pagina["archivo"], "formato": pagina["formato"],
        "primera": primera_n, "segunda": ganadores[0] if len(ganadores) == 1 else None, "candidatos": detalle,
        "estado": ("coincide" if primera_n and primera_n in ganadores and len(set(ganadores)) == 1
                   else "sin_numero_en_ninguna" if not primera_n and not ganadores
                   else "primera_sin_segunda" if primera_n and not ganadores
                   else "encontro_numero_nuevo" if not primera_n
                   else "distinto"),
    }


def _trabajo(pagina: dict, primera: dict | None, salida: str) -> dict:
    return releer(pagina, primera, salida)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--escaneos", required=True, type=Path)
    ap.add_argument("--indice", required=True, type=Path, help="carpeta con indice.jsonl (la primera lectura)")
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--trabajadores", type=int, default=4)
    ap.add_argument("--hilos", type=int, default=2)
    ap.add_argument("--limite", type=int)
    args = ap.parse_args()

    (args.salida / "recortes").mkdir(parents=True, exist_ok=True)
    primera = {}
    for l in (args.indice / "indice.jsonl").read_text(encoding="utf-8").splitlines():
        if l.strip():
            d = json.loads(l)
            primera[(d["carpeta"], d["archivo"])] = d
    archivo = args.salida / "lecturas.jsonl"
    hechas = set()
    if archivo.exists():
        for l in archivo.read_text(encoding="utf-8").splitlines():
            if l.strip():
                d = json.loads(l)
                hechas.add((d["carpeta"], d["archivo"]))
    pendientes = [p for p in listar_paginas(args.escaneos) if (p["carpeta"], p["archivo"]) not in hechas]
    if args.limite:
        pendientes = pendientes[: args.limite]
    print(f"{len(hechas)} ya releídas · {len(pendientes)} por hacer · {args.trabajadores} trabajadores", flush=True)
    inicio, listas = time.time(), 0
    with open(archivo, "a", encoding="utf-8") as fsal, ProcessPoolExecutor(
        max_workers=args.trabajadores, initializer=_iniciar_trabajador, initargs=(args.hilos,)
    ) as pool:
        futuros = {pool.submit(_trabajo, p, primera.get((p["carpeta"], p["archivo"])), str(args.salida)): p for p in pendientes}
        for f in as_completed(futuros):
            p = futuros[f]
            try:
                d = f.result()
            except Exception as exc:
                with open(args.salida / "errores.jsonl", "a", encoding="utf-8") as fe:
                    fe.write(json.dumps({"carpeta": p["carpeta"], "archivo": p["archivo"], "error": f"{type(exc).__name__}: {exc}"[:200]}) + "\n")
                continue
            fsal.write(json.dumps(d, ensure_ascii=False) + "\n")
            fsal.flush()
            listas += 1
            if listas % 10 == 0:
                ritmo = (time.time() - inicio) / listas
                print(f"  {listas}/{len(pendientes)} · {ritmo:.1f} s/página · faltan ~{(len(pendientes) - listas) * ritmo / 60:.0f} min", flush=True)
    print("Listo.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
