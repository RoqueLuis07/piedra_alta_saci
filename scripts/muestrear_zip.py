"""Saca una muestra chica de un ZIP grande, sin descomprimirlo entero.

Pensado para el caso de "tengo cientos de formularios escaneados en un ZIP de
varios GB y necesito compartir sólo unos pocos". `zipfile` permite listar y
extraer miembros de a uno, así que nunca hace falta bajar el archivo completo
a disco descomprimido para elegir la muestra.

Uso:

    # 1. Ver qué hay adentro, sin extraer nada
    python scripts/muestrear_zip.py formularios.zip --listar

    # 2. Extraer una muestra al azar (reproducible con --semilla)
    python scripts/muestrear_zip.py formularios.zip --extraer 6 --salida muestra/

    # 3. Extraer archivos puntuales por nombre (o por sufijo de nombre)
    python scripts/muestrear_zip.py formularios.zip --nombres "GE2911.*,GE2934.*" --salida muestra/
"""

from __future__ import annotations

import argparse
import fnmatch
import random
import sys
import zipfile
from collections import Counter
from pathlib import Path

# Carpetas/archivos que un ZIP de Windows o Mac suele traer y que no son
# documentos: no cuentan para el muestreo ni se listan.
IGNORAR = {"__MACOSX", ".DS_Store", "Thumbs.db"}


def _es_relevante(nombre: str) -> bool:
    partes = Path(nombre).parts
    return not any(p in IGNORAR for p in partes) and not nombre.endswith("/")


def listar(ruta: Path, mostrar: int) -> None:
    with zipfile.ZipFile(ruta) as z:
        miembros = [i for i in z.infolist() if _es_relevante(i.filename)]
        peso_total = sum(i.file_size for i in miembros)          # descomprimido
        peso_zip = ruta.stat().st_size

        print(f"Archivo    : {ruta.name}")
        print(f"Peso del ZIP        : {peso_zip / 1024 / 1024:.1f} MB")
        print(f"Peso descomprimido  : {peso_total / 1024 / 1024:.1f} MB")
        print(f"Archivos            : {len(miembros)}\n")

        extensiones = Counter(Path(m.filename).suffix.lower() for m in miembros)
        print("Por tipo de archivo:")
        for ext, cantidad in extensiones.most_common():
            print(f"  {ext or '(sin extensión)':<10} {cantidad}")

        carpetas = Counter(str(Path(m.filename).parent) for m in miembros)
        if len(carpetas) > 1:
            print(f"\nCarpetas ({len(carpetas)}):")
            for carpeta, cantidad in carpetas.most_common(10):
                print(f"  {carpeta:<40} {cantidad} archivos")
            if len(carpetas) > 10:
                print(f"  ... {len(carpetas) - 10} carpetas más")

        print(f"\nPrimeros {min(mostrar, len(miembros))} archivos:")
        for m in miembros[:mostrar]:
            print(f"  {m.filename:<50} {m.file_size / 1024:>8.0f} kB")
        if len(miembros) > mostrar:
            print(f"  ... {len(miembros) - mostrar} más (usar --mostrar para ver otros)")

        print(
            f"\nPara sacar una muestra al azar:\n"
            f"  python {Path(__file__).name} \"{ruta.name}\" --extraer 6 --salida muestra/"
        )


def _elegir_muestra(miembros: list, cantidad: int, semilla: int) -> list:
    """Reparte la muestra entre carpetas si el ZIP viene organizado en ellas,
    en vez de sacar todo de la primera: es lo que da variedad real."""
    por_carpeta: dict[str, list] = {}
    for m in miembros:
        por_carpeta.setdefault(str(Path(m.filename).parent), []).append(m)

    rng = random.Random(semilla)
    carpetas = list(por_carpeta.values())
    rng.shuffle(carpetas)
    for grupo in carpetas:
        rng.shuffle(grupo)

    elegidos: list = []
    i = 0
    while len(elegidos) < cantidad and any(carpetas):
        grupo = carpetas[i % len(carpetas)]
        if grupo:
            elegidos.append(grupo.pop())
        i += 1
        if i > 10 * max(1, cantidad) + len(carpetas):    # a salvo de listas vacías
            break
    return elegidos[:cantidad]


def extraer_muestra(ruta: Path, cantidad: int, semilla: int, salida: Path) -> list[Path]:
    with zipfile.ZipFile(ruta) as z:
        miembros = [i for i in z.infolist() if _es_relevante(i.filename)]
        if not miembros:
            raise ValueError("El ZIP no tiene archivos (o sólo metadata del sistema).")
        elegidos = _elegir_muestra(miembros, min(cantidad, len(miembros)), semilla)

        salida.mkdir(parents=True, exist_ok=True)
        escritos = []
        for m in elegidos:
            # Aplanar el nombre: si el ZIP trae subcarpetas, un mismo nombre de
            # archivo en dos carpetas no debe pisarse al extraer a un solo lugar.
            plano = Path(m.filename).as_posix().replace("/", "__")
            destino = salida / plano
            with z.open(m) as origen, open(destino, "wb") as fh:
                fh.write(origen.read())
            escritos.append(destino)
        return escritos


def extraer_por_nombre(ruta: Path, patrones: list[str], salida: Path) -> list[Path]:
    with zipfile.ZipFile(ruta) as z:
        miembros = [i for i in z.infolist() if _es_relevante(i.filename)]
        elegidos = [
            m for m in miembros
            if any(fnmatch.fnmatch(Path(m.filename).name, p) for p in patrones)
        ]
        if not elegidos:
            raise ValueError(
                f"Ningún archivo coincide con: {', '.join(patrones)}. "
                "Revisar con --listar los nombres exactos."
            )
        salida.mkdir(parents=True, exist_ok=True)
        escritos = []
        for m in elegidos:
            plano = Path(m.filename).as_posix().replace("/", "__")
            destino = salida / plano
            with z.open(m) as origen, open(destino, "wb") as fh:
                fh.write(origen.read())
            escritos.append(destino)
        return escritos


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("zip", type=Path)
    ap.add_argument("--listar", action="store_true", help="mostrar el contenido, sin extraer")
    ap.add_argument("--mostrar", type=int, default=15, help="cuántos nombres listar (def. 15)")
    ap.add_argument("--extraer", type=int, default=None, metavar="N",
                    help="extraer N archivos al azar")
    ap.add_argument("--semilla", type=int, default=0, help="para repetir la misma muestra")
    ap.add_argument("--nombres", default=None,
                    help="extraer por patrón de nombre, separados por coma (admite *)")
    ap.add_argument("--salida", type=Path, default=Path("muestra_zip"))
    args = ap.parse_args()

    if not args.zip.exists():
        sys.exit(f"No existe el archivo: {args.zip}")
    if not zipfile.is_zipfile(args.zip):
        sys.exit(f"No es un ZIP válido: {args.zip}")

    try:
        if args.nombres:
            escritos = extraer_por_nombre(args.zip, args.nombres.split(","), args.salida)
        elif args.extraer:
            escritos = extraer_muestra(args.zip, args.extraer, args.semilla, args.salida)
        else:
            listar(args.zip, args.mostrar)
            return
    except ValueError as exc:
        sys.exit(f"Error: {exc}")

    peso = sum(p.stat().st_size for p in escritos) / 1024 / 1024
    print(f"Extraídos {len(escritos)} archivos a {args.salida}/  ({peso:.1f} MB)")
    for p in escritos:
        print(f"  {p.name}")
    if peso > 25:
        print(
            "\nPesa más de 25 MB en total: subir de a partes, o si son PDF de "
            "muchas páginas, pasarlos por scripts/extraer_muestra.py para recortarlos."
        )


if __name__ == "__main__":
    main()
