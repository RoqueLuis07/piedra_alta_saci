"""Audita el índice de guías escaneadas (``indice.jsonl``): detecta los N.º de guía dudosos con pruebas cruzadas,
para revisarlos a ojo en lugar de confiar a ciegas en la lectura automática (OCR).

No escribe nada: sólo lee el índice y muestra / guarda un informe.

    python scripts/auditar_escaneos.py --indice ..\\demo\\indice_escaneos --salida informe_auditoria.csv

Pruebas que aplica a cada página:
 - formato del número: 8 dígitos y un prefijo conocido de las guías (10, 90, 91, 80...);
 - repetición: el mismo número en dos certificados distintos (N.º de certificado diferente);
 - casi igual a otro número (un dígito de diferencia o dos dígitos cambiados de lugar): posible error de lectura;
 - guías GE2: el número suelto y el impreso como «GE2<número>» tienen que ser el mismo;
 - número citado en el «Respaldo» (Rubro 4) de alguna guía GE2 pero no asignado a ninguna página;
 - sin número, o número inferido por vecindad.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

PREFIJOS_CONOCIDOS = ("10", "90", "91", "80")
RUC_PIEDRA_ALTA = "80020081"


def leer_indice(carpeta: Path) -> list[dict]:
    with open(carpeta / "indice.jsonl", encoding="utf-8") as f:
        filas = [json.loads(l) for l in f if l.strip()]
    filas.sort(key=lambda p: (p["carpeta"], p["orden"]))
    return filas


def _casi_iguales(a: str, b: str) -> bool:
    """Un dígito distinto, o dos dígitos contiguos intercambiados."""
    if a == b or len(a) != len(b):
        return False
    difs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    if len(difs) == 1:
        return True
    return len(difs) == 2 and difs[1] == difs[0] + 1 and a[difs[0]] == b[difs[1]] and a[difs[1]] == b[difs[0]]


def numeros_citados_en_respaldos(filas: list[dict]) -> set[str]:
    """Todos los números de 8 dígitos que aparecen en el texto de las guías GE2 (donde está el Rubro 4)."""
    citados: set[str] = set()
    for p in filas:
        if p.get("formato") in ("GE2_PRINCIPAL", "GE2_ANEXO"):
            citados |= {n for n in re.findall(r"(?<!\d)\d{8}(?!\d)", p.get("texto_ocr") or "") if n != RUC_PIEDRA_ALTA}
    return citados


def auditar(filas: list[dict]) -> list[dict]:
    asignados = Counter(p.get("numero_guia") for p in filas if p.get("numero_guia"))
    por_numero_cert: dict[str, set] = defaultdict(set)
    for p in filas:
        if p.get("numero_guia"):
            por_numero_cert[p["numero_guia"]].add(p.get("numero_certificado") or p["archivo"])
    numeros = sorted(asignados)
    por_prefijo: dict[str, list[str]] = defaultdict(list)
    for n in numeros:
        por_prefijo[n[:4]].append(n)           # sólo se comparan números con los mismos 4 primeros dígitos (rápido y suficiente)
    vecinos: dict[str, list[str]] = {}
    for n in numeros:
        cercanos = [m for m in por_prefijo[n[:4]] if _casi_iguales(n, m)]
        # un dígito distinto es normal entre guías consecutivas (10861148 / 10861149): sólo importa si el OCR dudaba
        if cercanos:
            vecinos[n] = cercanos
    citados = numeros_citados_en_respaldos(filas)

    informe = []
    for i, p in enumerate(filas):
        n = p.get("numero_guia")
        motivos = []
        if not n:
            motivos.append("sin número")
        else:
            if not re.fullmatch(r"\d{8}", n):
                motivos.append("no tiene 8 dígitos")
            elif n[:2] not in PREFIJOS_CONOCIDOS:
                motivos.append(f"prefijo raro ({n[:2]})")
            if p.get("formato") == "SENACSA_CERTIFICADO" and len(por_numero_cert[n]) > 1:
                motivos.append("el mismo número en certificados distintos")
            if (p.get("fuente_numero") or "").startswith("inferido"):
                motivos.append("número inferido por vecindad")
            if p.get("conf_numero") is not None and 0 < p["conf_numero"] < 0.6:
                motivos.append(f"lectura poco segura ({p['conf_numero']:.2f})")
            if n in vecinos and (p.get("conf_numero") or 0) < 0.75:
                motivos.append("casi igual a " + ", ".join(vecinos[n][:3]))
            if p.get("formato") == "GE2_PRINCIPAL":
                impresos = set(re.findall(r"GE2\s*(\d{8})", p.get("texto_ocr") or ""))
                if impresos and n not in impresos:
                    motivos.append("el «GE2…» impreso dice " + "/".join(sorted(impresos)))
        if motivos:
            informe.append({
                "carpeta": p["carpeta"], "archivo": p["archivo"], "formato": p["formato"], "numero_guia": n or "",
                "fuente": p.get("fuente_numero") or "", "conf": p.get("conf_numero") or "",
                "propietario": p.get("propietario") or "", "motivos": "; ".join(motivos), "imagen": p.get("imagen") or "",
            })
    # números citados en algún Respaldo que no están asignados a ninguna página
    for n in sorted(citados - set(asignados)):
        cerca = [m for m in numeros if _casi_iguales(n, m)]
        informe.append({
            "carpeta": "", "archivo": "", "formato": "CITADO_EN_RESPALDO", "numero_guia": n, "fuente": "", "conf": "",
            "propietario": "", "imagen": "",
            "motivos": "citado en el Respaldo de una guía y no está cargado" + (f" (¿{', '.join(cerca)}?)" if cerca else ""),
        })
    return informe


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--indice", required=True, type=Path)
    ap.add_argument("--salida", type=Path, help="guardar el informe como CSV")
    args = ap.parse_args()
    filas = leer_indice(args.indice)
    informe = auditar(filas)
    cuenta = Counter(m.strip().split(" (")[0].split(" a ")[0] for r in informe for m in r["motivos"].split(";"))
    print(f"{len(filas)} páginas · {len(informe)} con algo para revisar")
    for motivo, n in cuenta.most_common():
        print(f"  {n:4d}  {motivo}")
    if args.salida:
        with open(args.salida, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(informe[0].keys()) if informe else ["vacio"])
            w.writeheader()
            w.writerows(informe)
        print(f"Informe: {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
