"""Carga en Postgres las páginas escaneadas que dejó ``scripts/indexar_escaneos.py``
(tabla ``escaneos`` + sus imágenes) y las vincula con las marcas del catálogo.

- Es idempotente y se puede correr varias veces (por ejemplo, a medida que el índice
  va creciendo): las páginas se actualizan y las imágenes ya cargadas se saltean.
- **No pisa lo corregido a mano:** una página con ``fuente_numero = 'manual'`` o marcada
  como revisada conserva sus datos.
- A las páginas que no traen el N.º de guía (típicamente los anexos) se les infiere el de
  la página vecina, y queda anotado como «inferido»: se puede corregir desde la pantalla.
- Guarda de a ``--lote`` páginas; ``--en-seco`` hace todo menos escribir.

    python scripts/cargar_escaneos.py --indice ../demo/indice_escaneos
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def leer_indice(carpeta: Path) -> list[dict]:
    paginas = []
    for linea in (carpeta / "indice.jsonl").read_text(encoding="utf-8").splitlines():
        if not linea.strip():
            continue
        d = json.loads(linea)
        if "error" in d:
            continue
        paginas.append(d)
    return paginas


def inferir_numeros(paginas: list[dict]) -> int:
    """Completa el N.º de guía de las páginas que no lo traen, a partir de las vecinas
    (mismo lote de escaneo y orden contiguo). Devuelve cuántas completó."""
    por_carpeta: dict[str, list[dict]] = defaultdict(list)
    for p in paginas:
        por_carpeta[p["carpeta"]].append(p)
    inferidas = 0
    for lista in por_carpeta.values():
        lista.sort(key=lambda p: p["orden"])
        for i, p in enumerate(lista):
            if p.get("numero_guia"):
                continue
            anterior = lista[i - 1] if i > 0 else None
            siguiente = lista[i + 1] if i + 1 < len(lista) else None
            n_ant = anterior.get("numero_guia") if anterior else None
            n_sig = siguiente.get("numero_guia") if siguiente else None
            if p["formato"] == "GE2_ANEXO" and n_ant and anterior["formato"] in ("GE2_PRINCIPAL", "GE2_ANEXO"):
                p["numero_guia"], p["fuente_numero"], p["conf_numero"] = n_ant, "inferido:anexo", 0.5
                inferidas += 1
            elif n_ant and n_ant == n_sig:
                p["numero_guia"], p["fuente_numero"], p["conf_numero"] = n_ant, "inferido:vecinas", 0.4
                inferidas += 1
    return inferidas


from marcas.servidor.origen import normalizar_origen  # noqa: E402


def cargar(conexion, carpeta: Path, lote: int = 50, escribir: bool = True, informar=print) -> dict:
    paginas = leer_indice(carpeta)
    inferidas = inferir_numeros(paginas)
    cuentas = {"paginas": len(paginas), "inferidas": inferidas, "nuevas": 0, "actualizadas": 0, "con_numero": 0,
               "protegidas": 0, "imagenes": 0, "marcas_vinculadas": 0}
    with conexion.cursor() as cur:
        for i, p in enumerate(paginas, 1):
            cuentas["con_numero"] += bool(p.get("numero_guia"))
            if not escribir:
                continue
            cur.execute("SELECT id, fuente_numero, revisado FROM escaneos WHERE carpeta = %s AND archivo = %s",
                        (p["carpeta"], p["archivo"]))
            existente = cur.fetchone()
            valores = {
                "carpeta": p["carpeta"], "archivo": p["archivo"], "orden": p["orden"], "formato": p.get("formato"),
                "numero_guia": p.get("numero_guia"), "fuente_numero": p.get("fuente_numero") if p.get("numero_guia") else None,
                "confianza_numero": p.get("conf_numero") or p.get("confianza_numero"),
                "numero_certificado": p.get("numero_certificado"), "propietario": p.get("propietario"),
                "propietario_documento": p.get("propietario_documento"), "establecimiento": p.get("establecimiento"),
                "cantidad_animales": p.get("cantidad_animales"), "fecha_texto": p.get("fecha_texto"),
                "n_marcas": p.get("n_marcas") or 0, "texto_ocr": p.get("texto_ocr"),
                "ancho": p.get("ancho"), "alto": p.get("alto"),
            }
            if existente:
                if existente["fuente_numero"] == "manual" or existente["revisado"]:
                    cuentas["protegidas"] += 1
                    escaneo_id = existente["id"]
                else:
                    sets = ", ".join(f"{c} = %({c})s" for c in valores if c not in ("carpeta", "archivo"))
                    cur.execute(f"UPDATE escaneos SET {sets} WHERE id = %(_id)s", {**valores, "_id": existente["id"]})
                    cuentas["actualizadas"] += 1
                    escaneo_id = existente["id"]
            else:
                columnas = list(valores)
                cur.execute(
                    f"INSERT INTO escaneos ({', '.join(columnas)}) VALUES ({', '.join('%(' + c + ')s' for c in columnas)}) RETURNING id",
                    valores,
                )
                escaneo_id = cur.fetchone()["id"]
                cuentas["nuevas"] += 1
            cur.execute("SELECT 1 FROM escaneos_imagenes WHERE escaneo_id = %s", (escaneo_id,))
            if not cur.fetchone() and p.get("imagen"):
                ruta_img, ruta_min = carpeta / p["imagen"], carpeta / p["miniatura"]
                if ruta_img.is_file() and ruta_min.is_file():
                    cur.execute(
                        "INSERT INTO escaneos_imagenes (escaneo_id, imagen, miniatura) VALUES (%s, %s, %s)",
                        (escaneo_id, psycopg2.Binary(ruta_img.read_bytes()), psycopg2.Binary(ruta_min.read_bytes())),
                    )
                    cuentas["imagenes"] += 1
            if i % lote == 0:
                conexion.commit()
                informar(f"  {i}/{len(paginas)}…")
        if escribir:
            cuentas["marcas_vinculadas"] = vincular_marcas(conexion)
            conexion.commit()
            normalizar_origen(conexion)
    return cuentas


def vincular_marcas(conexion) -> int:
    """Une cada marca del catálogo con la página de la que salió (por carpeta y archivo de origen)."""
    with conexion.cursor() as cur:
        cur.execute(
            """
            UPDATE marcas m SET escaneo_id = e.id
            FROM escaneos e
            WHERE m.escaneo_id IS NULL AND m.origen_carpeta = e.carpeta AND m.origen_pagina = e.archivo
            """
        )
        return cur.rowcount


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--indice", required=True, type=Path, help="carpeta que dejó indexar_escaneos.py")
    ap.add_argument("--lote", type=int, default=50)
    ap.add_argument("--en-seco", action="store_true", help="validar y contar sin escribir nada")
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL.", file=sys.stderr)
        return 2
    conexion = psycopg2.connect(url, cursor_factory=RealDictCursor, connect_timeout=15)
    try:
        c = cargar(conexion, args.indice, args.lote, escribir=not args.en_seco)
    finally:
        conexion.close()
    print(
        f"{'[EN SECO, no se escribió nada] ' if args.en_seco else ''}{c['paginas']} páginas del índice · "
        f"{c['con_numero']} con N.º de guía ({c['inferidas']} inferidas por vecindad) · nuevas {c['nuevas']} · "
        f"actualizadas {c['actualizadas']} · protegidas (corregidas a mano) {c['protegidas']} · "
        f"imágenes cargadas {c['imagenes']} · marcas vinculadas {c['marcas_vinculadas']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
