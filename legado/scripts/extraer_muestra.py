"""Saca una muestra chica de un PDF escaneado grande, sin degradarla.

Un documento de cientos de megas no se puede compartir, pero para ajustar el
pipeline alcanzan dos o tres páginas. Este script:

* informa qué tiene el PDF adentro (resolución real del escaneo, compresión,
  peso por página) sin escribir nada;
* extrae un rango de páginas a un PDF nuevo **copiando los datos originales**,
  sin recomprimir ni rasterizar;
* opcionalmente exporta la imagen escaneada de cada página tal como está
  guardada dentro del PDF.

No necesita nada más que Python y pypdf (`pip install pypdf pillow`).

Ejemplos:

    python scripts/extraer_muestra.py documento.pdf --informe
    python scripts/extraer_muestra.py documento.pdf --paginas 1-3 --salida muestra.pdf
    python scripts/extraer_muestra.py documento.pdf --paginas 5 --imagenes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pypdf
except ImportError:                                   # pragma: no cover
    sys.exit("Falta pypdf. Instalar con:  pip install pypdf pillow")


def parsear_paginas(texto: str, total: int) -> list[int]:
    """'1-3,7' -> [0, 1, 2, 6] (índices, validados contra el total)."""
    indices: list[int] = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        if "-" in parte:
            desde, hasta = (int(v) for v in parte.split("-", 1))
        else:
            desde = hasta = int(parte)
        for n in range(desde, hasta + 1):
            if not 1 <= n <= total:
                raise ValueError(f"la página {n} no existe (el PDF tiene {total})")
            if n - 1 not in indices:
                indices.append(n - 1)
    if not indices:
        raise ValueError("no se indicó ninguna página")
    return indices


def _datos_de_pagina(pagina) -> dict:
    ancho_pt = float(pagina.mediabox.width) or 1.0
    alto_pt = float(pagina.mediabox.height) or 1.0
    info = {
        "pt": (round(ancho_pt), round(alto_pt)),
        "cm": (round(ancho_pt / 72 * 2.54, 1), round(alto_pt / 72 * 2.54, 1)),
        "imagenes": [],
        "texto": bool((pagina.extract_text() or "").strip()),
    }
    try:
        imagenes = list(pagina.images)
    except Exception as exc:                          # imagen en formato exótico
        info["error"] = str(exc)
        return info
    proporcion_pagina = ancho_pt / alto_pt
    for im in imagenes:
        try:
            w, h = im.image.size
            modo = im.image.mode
        except Exception:
            continue
        # Una imagen es "a página completa" si su proporción coincide con la de
        # la hoja: es el caso del escaneo. Un logo o una firma no lo cumplen, y
        # calcularles dpi contra el ancho de la página daría un número sin
        # sentido.
        proporcion = w / h if h else 0
        pagina_completa = abs(proporcion - proporcion_pagina) / proporcion_pagina < 0.06
        info["imagenes"].append({
            "px": (w, h),
            "dpi": round(w / (ancho_pt / 72)) if pagina_completa else None,
            "pagina_completa": pagina_completa,
            "modo": modo,
            "kb": round(len(im.data) / 1024),
        })
    return info


def informe(ruta: Path, paginas: list[int] | None, mostrar: int) -> None:
    lector = pypdf.PdfReader(str(ruta))
    total = len(lector.pages)
    peso = ruta.stat().st_size / 1024 / 1024
    print(f"Archivo : {ruta.name}")
    print(f"Peso    : {peso:.1f} MB")
    print(f"Páginas : {total}  ({peso / max(total, 1):.1f} MB por página)\n")

    indices = paginas if paginas is not None else list(range(total))[:mostrar]
    print(f"{'PÁG':<5} {'HOJA (cm)':<13} {'IMG':<4} {'MAYOR (px)':<15} {'DPI':<7} "
          f"{'MODO':<6} {'PESO':<9} TEXTO")
    dpis: list[int] = []
    for i in indices:
        d = _datos_de_pagina(lector.pages[i])
        cm = f"{d['cm'][0]} x {d['cm'][1]}"
        if d["imagenes"]:
            im = max(d["imagenes"], key=lambda x: x["px"][0] * x["px"][1])
            px = f"{im['px'][0]} x {im['px'][1]}"
            dpi = f"{im['dpi']}" if im["dpi"] else "parcial"
            if im["dpi"]:
                dpis.append(im["dpi"])
            print(f"{i + 1:<5} {cm:<13} {len(d['imagenes']):<4} {px:<15} {dpi:<7} "
                  f"{im['modo']:<6} {im['kb']:>5} kB  {'sí' if d['texto'] else 'no'}")
        else:
            print(f"{i + 1:<5} {cm:<13} {0:<4} {'(sin imagen)':<15} {'-':<7} "
                  f"{'-':<6} {'-':>8}  {'sí' if d['texto'] else 'no'}")
    if paginas is None and total > mostrar:
        print(f"... {total - mostrar} páginas más (usar --paginas para ver otras)")

    print("\nRecomendación:")
    if not dpis:
        print("  Ninguna página trae una imagen a página completa: no son escaneos")
        print("  de hoja entera. El pipeline las va a rasterizar; procesar con")
        print("  marcas procesar --dpi 400")
    else:
        dpi = max(dpis)
        if dpi >= 500:
            print(f"  Escaneo a ~{dpi} dpi: excelente para digitalizar las marcas.")
        elif dpi >= 300:
            print(f"  Escaneo a ~{dpi} dpi: alcanza, pero el trazo fino puede cortarse.")
        else:
            print(f"  Escaneo a ~{dpi} dpi: bajo. Conviene reescanear a 600 dpi")
            print("  las hojas cuyas marcas salgan rotas.")
    sugeridas = f"1-{min(3, total)}" if total > 1 else "1"
    print(f"  Para compartir una muestra:  python {Path(__file__).name} "
          f'"{ruta.name}" --paginas {sugeridas} --salida muestra.pdf')


def extraer(ruta: Path, indices: list[int], salida: Path) -> Path:
    """Copia las páginas elegidas a un PDF nuevo, sin tocar los datos de imagen."""
    lector = pypdf.PdfReader(str(ruta))
    escritor = pypdf.PdfWriter()
    for i in indices:
        escritor.add_page(lector.pages[i])
    salida.parent.mkdir(parents=True, exist_ok=True)
    with open(salida, "wb") as fh:
        escritor.write(fh)
    return salida


def exportar_imagenes(ruta: Path, indices: list[int], carpeta: Path) -> list[Path]:
    """Guarda la imagen escaneada de cada página, tal como está en el PDF."""
    lector = pypdf.PdfReader(str(ruta))
    carpeta.mkdir(parents=True, exist_ok=True)
    escritas = []
    for i in indices:
        for j, im in enumerate(lector.pages[i].images, start=1):
            destino = carpeta / f"{ruta.stem}-p{i + 1:02d}-{j}{Path(im.name).suffix or '.png'}"
            destino.write_bytes(im.data)
            escritas.append(destino)
    return escritas


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--paginas", default=None,
                    help="rango a extraer, por ejemplo 1-3 o 2,5,9")
    ap.add_argument("--salida", type=Path, default=None,
                    help="PDF de salida (por defecto <nombre>_muestra.pdf)")
    ap.add_argument("--imagenes", action="store_true",
                    help="además, exportar las imágenes escaneadas de esas páginas")
    ap.add_argument("--informe", action="store_true",
                    help="sólo mostrar qué tiene el PDF, sin escribir nada")
    ap.add_argument("--mostrar", type=int, default=8,
                    help="cuántas páginas listar en el informe (def. 8)")
    args = ap.parse_args()

    if not args.pdf.exists():
        sys.exit(f"No existe el archivo: {args.pdf}")

    lector = pypdf.PdfReader(str(args.pdf))
    total = len(lector.pages)
    indices = None
    if args.paginas:
        try:
            indices = parsear_paginas(args.paginas, total)
        except ValueError as exc:
            sys.exit(f"Error en --paginas: {exc}")

    if args.informe or not args.paginas:
        informe(args.pdf, indices, args.mostrar)
        if not args.paginas:
            return

    salida = args.salida or args.pdf.with_name(f"{args.pdf.stem}_muestra.pdf")
    if not args.informe:
        ruta = extraer(args.pdf, indices, salida)
        mb = ruta.stat().st_size / 1024 / 1024
        print(f"\nMuestra: {ruta}  ({mb:.1f} MB, {len(indices)} páginas)")
        if mb > 25:
            print("  Pesa más de 25 MB: sacar alguna página o usar --imagenes,")
            print("  que suele dar archivos más chicos.")

    if args.imagenes:
        escritas = exportar_imagenes(
            args.pdf, indices, salida.parent / f"{args.pdf.stem}_imagenes"
        )
        total_mb = sum(p.stat().st_size for p in escritas) / 1024 / 1024
        print(f"Imágenes: {len(escritas)} archivos en "
              f"{salida.parent / (args.pdf.stem + '_imagenes')} ({total_mb:.1f} MB)")


if __name__ == "__main__":
    main()
