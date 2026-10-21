"""Orquestador por lotes: carpeta de escaneos -> PNG + SVG + manifiesto.

Pensado para tandas grandes (500+ marcas). Cada hoja se procesa de forma
independiente, así que un escaneo malo no arruina el lote: queda anotado en el
manifiesto con el motivo y se vuelve a escanear sólo esa hoja.

Acepta imágenes sueltas y **PDF escaneados**, que es como suelen llegar los
documentos con las marcas. De un PDF se extrae la imagen original de cada
página cuando el escaneo entró como una sola imagen a página completa: así se
trabaja con los píxeles del escáner y no con una re-digitalización.
"""

from __future__ import annotations

import csv
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from marcas import config
from marcas.vectorizacion.limpiar import limpiar_marca, recortar_a_tinta
from marcas.vectorizacion.segmentar import segmentar_hoja
from marcas.vectorizacion.trazar import potrace_disponible, trazar_svg

EXTENSIONES = {".tif", ".tiff", ".png", ".jpg", ".jpeg", ".bmp", ".pdf"}

# Al rasterizar una página de PDF que no trae una imagen escaneada entera.
DPI_RASTERIZADO = 400
# Por debajo de esto, la imagen embebida no sirve y conviene rasterizar.
DPI_MINIMO_EMBEBIDO = 200

CAMPOS_MANIFIESTO = [
    "codigo", "hoja", "fila", "columna", "png", "svg",
    "ancho_px", "alto_px", "aspecto", "tinta_pct", "componentes",
    "sha1", "revisar", "observaciones",
]


@dataclass
class ResultadoHoja:
    hoja: str
    celdas: int = 0
    marcas: list[dict] = field(default_factory=list)
    angulo: float = 0.0
    error: str | None = None

    @property
    def a_revisar(self) -> int:
        return sum(1 for m in self.marcas if m["revisar"])


def slug(texto: str) -> str:
    """Nombre de archivo seguro: sin tildes, sin espacios, en mayúsculas."""
    normal = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    normal = re.sub(r"[^A-Za-z0-9]+", "-", normal).strip("-")
    return normal.upper() or "HOJA"


def _sha1(ruta: Path) -> str:
    h = hashlib.sha1()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def _paginas_de_pdf(ruta: Path, dpi: int) -> Iterator[tuple[str, np.ndarray]]:
    """Devuelve una imagen en gris por página del PDF.

    Si la página es un escaneo (una sola imagen que cubre la hoja), se usa esa
    imagen tal cual, a su resolución original. Si no —un PDF generado, o una
    página con varias imágenes— se rasteriza a ``dpi``.
    """
    import pypdf
    import pypdfium2 as pdfium

    lector = pypdf.PdfReader(str(ruta))
    documento = pdfium.PdfDocument(str(ruta))
    try:
        for i, pagina in enumerate(lector.pages, start=1):
            nombre = f"{ruta.stem}-p{i:02d}"
            gris = None
            try:
                imagenes = list(pagina.images)
            except Exception:
                imagenes = []
            if len(imagenes) == 1:
                pil = imagenes[0].image
                ancho_pt = float(pagina.mediabox.width) or 1.0
                if pil.width / (ancho_pt / 72) >= DPI_MINIMO_EMBEBIDO:
                    gris = np.array(pil.convert("L"))
            if gris is None:
                pil = documento[i - 1].render(scale=dpi / 72).to_pil().convert("L")
                gris = np.array(pil)
            yield nombre, gris
    finally:
        documento.close()


def cargar_paginas(ruta: Path, dpi: int = DPI_RASTERIZADO) -> Iterator[tuple[str, np.ndarray]]:
    """Abre un archivo de entrada y entrega sus páginas en escala de grises."""
    ruta = Path(ruta)
    if ruta.suffix.lower() == ".pdf":
        yield from _paginas_de_pdf(ruta, dpi)
        return
    imagen = cv2.imread(str(ruta), cv2.IMREAD_GRAYSCALE)
    if imagen is None:
        raise ValueError(f"no se pudo leer la imagen: {ruta.name}")
    yield ruta.stem, imagen


def cargar_codigos(ruta_csv: Path | None) -> dict[tuple[str, int, int], str]:
    """Lee el mapeo opcional hoja/fila/columna -> código de marca."""
    if not ruta_csv:
        return {}
    mapa: dict[tuple[str, int, int], str] = {}
    with open(ruta_csv, newline="", encoding="utf-8-sig") as fh:
        for fila in csv.DictReader(fh):
            clave = (fila["hoja"].strip(), int(fila["fila"]), int(fila["columna"]))
            mapa[clave] = fila["codigo"].strip()
    return mapa


def procesar_pagina(
    nombre: str,
    imagen: np.ndarray,
    dir_png: Path,
    dir_svg: Path | None,
    *,
    modo: str = "grilla",
    codigos: dict[tuple[str, int, int], str] | None = None,
    lienzo: int | None = None,
) -> ResultadoHoja:
    """Segmenta una hoja, limpia cada marca y escribe PNG (y SVG si se pide)."""
    codigos = codigos or {}
    resultado = ResultadoHoja(hoja=nombre)

    gris, celdas, angulo = segmentar_hoja(imagen, modo=modo)
    resultado.angulo = angulo
    resultado.celdas = len(celdas)
    if not celdas:
        resultado.error = (
            "no se detectaron casillas: revisar el modo (--modo libre) "
            "o la calidad del escaneo"
        )
        return resultado

    dir_png.mkdir(parents=True, exist_ok=True)
    if dir_svg:
        dir_svg.mkdir(parents=True, exist_ok=True)

    base = slug(nombre)
    for celda in celdas:
        limpio = limpiar_marca(celda.recortar(gris), lienzo=lienzo)
        codigo = codigos.get((nombre, celda.fila, celda.columna)) or (
            f"{base}-F{celda.fila:02d}C{celda.columna:02d}"
        )
        registro = {
            "codigo": codigo,
            "hoja": nombre,
            "fila": celda.fila,
            "columna": celda.columna,
            "png": "",
            "svg": "",
            "ancho_px": limpio.ancho_px,
            "alto_px": limpio.alto_px,
            "aspecto": limpio.aspecto,
            "tinta_pct": limpio.tinta_pct,
            "componentes": limpio.componentes,
            "sha1": "",
            "revisar": limpio.revisar,
            "observaciones": "; ".join(limpio.observaciones),
        }

        if limpio.ancho_px == 0:
            # Casilla vacía: se registra para que el operador sepa que existió,
            # pero no se genera archivo.
            resultado.marcas.append(registro)
            continue

        destino_png = dir_png / f"{codigo}.png"
        cv2.imwrite(str(destino_png), limpio.png_rgba)
        registro["png"] = str(destino_png.relative_to(config.RAIZ)) \
            if destino_png.is_relative_to(config.RAIZ) else str(destino_png)
        registro["sha1"] = _sha1(destino_png)

        if dir_svg is not None and potrace_disponible():
            destino_svg = dir_svg / f"{codigo}.svg"
            try:
                trazar_svg(recortar_a_tinta(limpio.mascara), destino_svg)
                registro["svg"] = str(destino_svg.relative_to(config.RAIZ)) \
                    if destino_svg.is_relative_to(config.RAIZ) else str(destino_svg)
            except Exception as exc:                     # pragma: no cover
                registro["observaciones"] = (
                    f"{registro['observaciones']}; potrace falló: {exc}".strip("; ")
                )
                registro["revisar"] = True

        resultado.marcas.append(registro)

    return resultado


def procesar_hoja(
    ruta: Path,
    dir_png: Path,
    dir_svg: Path | None,
    *,
    modo: str = "grilla",
    codigos: dict[tuple[str, int, int], str] | None = None,
    lienzo: int | None = None,
    dpi: int = DPI_RASTERIZADO,
) -> list[ResultadoHoja]:
    """Procesa un archivo de entrada. Un PDF devuelve un resultado por página."""
    ruta = Path(ruta)
    try:
        paginas = list(cargar_paginas(ruta, dpi))
    except Exception as exc:
        return [ResultadoHoja(hoja=ruta.stem, error=f"no se pudo abrir: {exc}")]
    return [
        procesar_pagina(
            nombre, imagen, dir_png, dir_svg,
            modo=modo, codigos=codigos, lienzo=lienzo,
        )
        for nombre, imagen in paginas
    ]


def procesar_lote(
    entrada: Path,
    dir_png: Path | None = None,
    dir_svg: Path | None = None,
    *,
    modo: str = "grilla",
    ruta_codigos: Path | None = None,
    manifiesto: Path | None = None,
    lienzo: int | None = None,
    dpi: int = DPI_RASTERIZADO,
    al_avanzar=None,
) -> tuple[list[ResultadoHoja], Path]:
    """Procesa todas las hojas de una carpeta (o un único archivo)."""
    dir_png = dir_png or config.DIR_PNG
    dir_svg = config.DIR_SVG if dir_svg is None else dir_svg
    entrada = Path(entrada)
    hojas = (
        [entrada]
        if entrada.is_file()
        else sorted(p for p in entrada.rglob("*") if p.suffix.lower() in EXTENSIONES)
    )
    codigos = cargar_codigos(ruta_codigos)

    resultados: list[ResultadoHoja] = []
    inicio = time.time()
    for i, hoja in enumerate(hojas, start=1):
        for res in procesar_hoja(
            hoja, dir_png, dir_svg,
            modo=modo, codigos=codigos, lienzo=lienzo, dpi=dpi,
        ):
            resultados.append(res)
            if al_avanzar:
                al_avanzar(i, len(hojas), res)

    manifiesto = manifiesto or (dir_png.parent / "manifiesto.csv")
    manifiesto.parent.mkdir(parents=True, exist_ok=True)
    with open(manifiesto, "w", newline="", encoding="utf-8") as fh:
        escritor = csv.DictWriter(fh, fieldnames=CAMPOS_MANIFIESTO)
        escritor.writeheader()
        for res in resultados:
            for marca in res.marcas:
                escritor.writerow(marca)

    duracion = time.time() - inicio
    for res in resultados:
        res.duracion_total = duracion  # type: ignore[attr-defined]
    return resultados, manifiesto


def resumen(resultados: list[ResultadoHoja]) -> dict:
    total = sum(len(r.marcas) for r in resultados)
    con_png = sum(1 for r in resultados for m in r.marcas if m["png"])
    return {
        "hojas": len(resultados),
        "hojas_con_error": sum(1 for r in resultados if r.error),
        "marcas_detectadas": total,
        "marcas_generadas": con_png,
        "a_revisar": sum(r.a_revisar for r in resultados),
    }
