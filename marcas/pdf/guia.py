"""Estampado de marcas sobre la Guía de Traslado oficial (SENACSA).

El formulario se descarga del sitio oficial ya numerado (N° de orden, código
de barras y QR), así que **no se puede regenerar**: hay que conservarlo tal
cual y superponerle una capa con las marcas.

Cómo encuentra las casillas: no hace falta una plantilla calibrada a mano.
Los recuadros del anexo son rectángulos vectoriales dentro del propio PDF, así
que se leen directamente de la página. Si SENACSA reacomoda el formulario, el
detector lo sigue sin tocar código.

Reglas que respeta:

* Sólo se llenan las casillas **libres**: una casilla que ya trae la marca
  dominante o la "X" de anulación no se toca.
* La guía viene en cuatro copias (original, duplicado, triplicado y
  cuadruplicado) y cada copia repite las mismas hojas de anexo. La misma marca
  se estampa en la misma posición de las cuatro: si no, las copias no coinciden.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

import pypdf
import pypdfium2 as pdfium
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

# Tolerancias de la detección de casillas, en puntos PDF.
ANCHO_MIN, ANCHO_MAX = 80.0, 220.0
ALTO_MIN, ALTO_MAX = 60.0, 220.0
# Proporción de la casilla que debe cubrir un objeto para considerarla ocupada.
COBERTURA_OCUPADA = 0.20
# Margen interno al dibujar la marca, como fracción del lado menor.
MARGEN_CASILLA = 0.10
# Resolución con la que se incrustan las marcas. La guía se imprime en papel;
# 300 dpi es calidad de imprenta y evita que el PDF pese de más: cada marca se
# incrusta una vez por página y la guía trae cuatro copias.
DPI_SALIDA = 300

COPIAS = ("Original", "Duplicado", "Triplicado", "Cuadruplicado")
_RE_COPIA = re.compile("|".join(COPIAS), re.IGNORECASE)


@dataclass
class Casilla:
    """Un recuadro del formulario, en coordenadas PDF (origen abajo-izquierda)."""

    pagina: int          # índice 0
    x: float
    y: float
    ancho: float
    alto: float
    fila: int
    columna: int
    ocupada: bool = False

    @property
    def centro(self) -> tuple[float, float]:
        return self.x + self.ancho / 2, self.y + self.alto / 2


@dataclass
class HojaAnexo:
    """Una hoja de anexo, con su copia y su orden dentro de esa copia."""

    pagina: int
    copia: str
    orden: int                      # 0 = primera hoja de anexo de esa copia
    casillas: list[Casilla] = field(default_factory=list)

    @property
    def libres(self) -> list[Casilla]:
        return [c for c in self.casillas if not c.ocupada]


def _objetos(pagina_pdfium) -> list[tuple[int, tuple[float, float, float, float]]]:
    salida = []
    for obj in pagina_pdfium.get_objects(max_depth=8):
        try:
            salida.append((obj.type, tuple(obj.get_bounds())))
        except Exception:                      # objeto sin geometría utilizable
            continue
    return salida


def _agrupar_en_filas(cajas: list[tuple[float, float, float, float]]) -> list[Casilla]:
    """Ordena las casillas en filas de arriba hacia abajo y columnas de izquierda a derecha."""
    if not cajas:
        return []
    alto_medio = sorted(c[3] for c in cajas)[len(cajas) // 2]
    tolerancia = alto_medio * 0.5
    por_y = sorted(cajas, key=lambda c: -c[1])          # y crece hacia arriba en PDF
    filas: list[list] = [[por_y[0]]]
    for caja in por_y[1:]:
        if abs(caja[1] - filas[-1][0][1]) <= tolerancia:
            filas[-1].append(caja)
        else:
            filas.append([caja])
    casillas = []
    for i, fila in enumerate(filas, start=1):
        for j, (x, y, w, h) in enumerate(sorted(fila, key=lambda c: c[0]), start=1):
            casillas.append(Casilla(-1, x, y, w, h, fila=i, columna=j))
    return casillas


def detectar_casillas(pdf: Path | str, pagina: int) -> list[Casilla]:
    """Lee del PDF los recuadros donde va una marca, y si están libres."""
    doc = pdfium.PdfDocument(str(pdf))
    try:
        objetos = _objetos(doc[pagina])
    finally:
        doc.close()

    candidatos = [
        b for tipo, b in objetos
        if tipo == 2
        and ANCHO_MIN <= b[2] - b[0] <= ANCHO_MAX
        and ALTO_MIN <= b[3] - b[1] <= ALTO_MAX
    ]
    if not candidatos:
        return []

    # El tamaño de casilla es el que se repite: las tablas de datos del
    # formulario también dejan rectángulos, pero no cuarenta iguales.
    tamanos: dict[tuple[int, int], int] = {}
    for x0, y0, x1, y1 in set(candidatos):
        tamanos[(round(x1 - x0), round(y1 - y0))] = tamanos.get(
            (round(x1 - x0), round(y1 - y0)), 0
        ) + 1
    (ancho_ref, alto_ref), repeticiones = max(
        tamanos.items(), key=lambda kv: (kv[1], kv[0][0] * kv[0][1])
    )
    if repeticiones < 4:
        return []

    # Cada recuadro suele venir dibujado dos veces (borde y relleno). Se filtra
    # primero por tamaño y recién después se juntan los que comparten centro:
    # al revés, una "X" de anulación dibujada dentro de la casilla desplazaría
    # al propio recuadro y la casilla se perdería.
    por_centro: dict[tuple[int, int], tuple] = {}
    for x0, y0, x1, y1 in candidatos:
        if abs((x1 - x0) - ancho_ref) > 3 or abs((y1 - y0) - alto_ref) > 3:
            continue
        por_centro[(round((x0 + x1) / 4), round((y0 + y1) / 4))] = (x0, y0, x1, y1)

    cajas = [(x0, y0, x1 - x0, y1 - y0) for x0, y0, x1, y1 in por_centro.values()]
    casillas = _agrupar_en_filas(cajas)

    # Ocupación: cualquier dibujo o imagen contenido en la casilla que cubra
    # una parte apreciable de su superficie (la "X" de anulación, la marca
    # dominante ya impresa).
    for casilla in casillas:
        casilla.pagina = pagina
        area_casilla = casilla.ancho * casilla.alto
        for tipo, (x0, y0, x1, y1) in objetos:
            ancho, alto = x1 - x0, y1 - y0
            if ancho * alto >= area_casilla * 0.9:
                continue                        # es la propia casilla
            dentro = (
                x0 >= casilla.x - 2 and y0 >= casilla.y - 2
                and x1 <= casilla.x + casilla.ancho + 2
                and y1 <= casilla.y + casilla.alto + 2
            )
            if dentro and ancho * alto >= area_casilla * COBERTURA_OCUPADA:
                casilla.ocupada = True
                break
    return casillas


def hojas_de_anexo(pdf: Path | str) -> list[HojaAnexo]:
    """Encuentra las hojas de anexo, con su copia y su orden dentro de la copia."""
    lector = pypdf.PdfReader(str(pdf))
    contadores: dict[str, int] = {}
    hojas: list[HojaAnexo] = []
    for i, pagina in enumerate(lector.pages):
        texto = pagina.extract_text() or ""
        if "ANEXO" not in texto.upper():
            continue
        encontrada = _RE_COPIA.search(texto)
        copia = encontrada.group(0).capitalize() if encontrada else "Original"
        orden = contadores.get(copia, 0)
        contadores[copia] = orden + 1
        casillas = detectar_casillas(pdf, i)
        if casillas:
            hojas.append(HojaAnexo(pagina=i, copia=copia, orden=orden, casillas=casillas))
    return hojas


def _lector(
    imagen: Path, lado_pt: float, dpi: int, cache: dict, transparencia: bool = True
) -> ImageReader:
    """Devuelve la imagen ya reducida al tamaño con que se va a imprimir.

    Sin esto se incrusta el PNG de 1024 px entero, una vez por casilla y por
    copia: para una guía de 60 marcas son 240 imágenes y el archivo se va a
    varios megabytes sin ninguna ganancia visible en papel.
    """
    lado_px = max(64, int(lado_pt / 72 * dpi))
    clave = (str(imagen), lado_px, transparencia)
    if clave not in cache:
        im = Image.open(imagen)
        if max(im.size) > lado_px:
            im = im.resize(
                (max(1, round(im.width * lado_px / max(im.size))),
                 max(1, round(im.height * lado_px / max(im.size)))),
                Image.LANCZOS,
            )
        if not transparencia:
            # Las casillas del anexo son blancas, así que aplanar contra blanco
            # da el mismo resultado impreso sin arrastrar el canal alfa, que
            # pesa tanto como la imagen misma.
            fondo = Image.new("RGBA", im.size, (255, 255, 255, 255))
            im = Image.alpha_composite(fondo, im.convert("RGBA")).convert("L")
        cache[clave] = ImageReader(im)
    return cache[clave]


def _capa(
    ancho: float,
    alto: float,
    asignaciones: list[tuple[Casilla, Path]],
    dpi: int = DPI_SALIDA,
    cache: dict | None = None,
    transparencia: bool = True,
) -> pypdf.PageObject:
    """Arma una página transparente con las marcas ubicadas en sus casillas."""
    cache = {} if cache is None else cache
    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=(ancho, alto))
    for casilla, imagen in asignaciones:
        lector = _lector(
            imagen, max(casilla.ancho, casilla.alto), dpi, cache, transparencia
        )
        iw, ih = lector.getSize()
        margen = min(casilla.ancho, casilla.alto) * MARGEN_CASILLA
        disponible_w = casilla.ancho - 2 * margen
        disponible_h = casilla.alto - 2 * margen
        escala = min(disponible_w / iw, disponible_h / ih)
        w, h = iw * escala, ih * escala
        c.drawImage(
            lector,
            casilla.x + (casilla.ancho - w) / 2,
            casilla.y + (casilla.alto - h) / 2,
            width=w, height=h, mask="auto", preserveAspectRatio=True,
        )
    c.save()
    buffer.seek(0)
    return pypdf.PdfReader(buffer).pages[0]


def planificar(hojas: Sequence[HojaAnexo], imagenes: Sequence[Path]) -> dict[int, list]:
    """Reparte las marcas entre las casillas libres, igual en todas las copias.

    Devuelve ``{página: [(casilla, imagen), ...]}``. Las hojas de anexo con el
    mismo ``orden`` reciben exactamente las mismas marcas, en la misma posición.
    """
    por_orden: dict[int, list[HojaAnexo]] = {}
    for hoja in hojas:
        por_orden.setdefault(hoja.orden, []).append(hoja)

    plan: dict[int, list] = {}
    pendientes = list(imagenes)
    for orden in sorted(por_orden):
        grupo = por_orden[orden]
        cupo = min(len(h.libres) for h in grupo)
        tanda, pendientes = pendientes[:cupo], pendientes[cupo:]
        if not tanda:
            break
        for hoja in grupo:
            plan[hoja.pagina] = list(zip(hoja.libres, tanda))
    if pendientes:
        raise ValueError(
            f"No hay casillas libres suficientes: sobran {len(pendientes)} marcas. "
            "Descargar la guía con más hojas de anexo o repartir en dos guías."
        )
    return plan


def completar_guia(
    pdf_entrada: Path | str,
    imagenes: Iterable[Path | str],
    salida: Path | str,
    *,
    dpi: int = DPI_SALIDA,
    transparencia: bool = True,
) -> dict:
    """Estampa las marcas sobre la guía oficial y guarda el PDF resultante.

    No se toca ningún otro elemento del documento: el número de orden, el
    código de barras y el QR quedan intactos.
    """
    imagenes = [Path(i) for i in imagenes]
    faltantes = [i for i in imagenes if not i.exists()]
    if faltantes:
        raise FileNotFoundError(f"No se encontraron las imágenes: {faltantes[:3]}")

    hojas = hojas_de_anexo(pdf_entrada)
    if not hojas:
        raise ValueError(
            "El PDF no tiene hojas de anexo reconocibles. "
            "¿Es una Guía de Traslado descargada del sitio oficial?"
        )
    plan = planificar(hojas, imagenes)

    # Se clona el documento en el escritor y se fusiona sobre sus páginas:
    # fusionar sobre las páginas de un lector suelto está en desuso en pypdf.
    escritor = pypdf.PdfWriter(clone_from=str(pdf_entrada))
    cache: dict = {}
    for i, pagina in enumerate(escritor.pages):
        asignaciones = plan.get(i)
        if not asignaciones:
            continue
        caja = pagina.mediabox
        pagina.merge_page(
            _capa(float(caja.width), float(caja.height), asignaciones,
                  dpi=dpi, cache=cache, transparencia=transparencia)
        )

    # La misma marca se incrusta en las cuatro copias; esto deja una sola
    # copia del objeto imagen y referencia las demás.
    try:
        escritor.compress_identical_objects(images=True, forms=False)
    except Exception:                       # versión de pypdf sin deduplicación
        pass

    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    with open(salida, "wb") as fh:
        escritor.write(fh)

    return {
        "salida": salida,
        "peso_kb": round(salida.stat().st_size / 1024),
        "marcas": len(imagenes),
        "hojas_anexo": len(hojas),
        "copias": sorted({h.copia for h in hojas}),
        "casillas_libres": sum(len(h.libres) for h in hojas),
        "paginas_modificadas": sorted(plan),
    }
