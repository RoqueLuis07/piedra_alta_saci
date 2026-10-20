"""Anonimiza una Guía de Traslado oficial de SENACSA para usarla en una demo.

Reemplaza, en TODAS las páginas y copias:
- el nombre y el CI/RUC del comprador (un tercero),
- el N.º de guía y su código de barras / QR (que lo llevan dentro),
- los números y fechas de las guías anteriores que figuran en el Rubro 4.

Deja tal cual lo que es de la propia empresa vendedora (nombre, RUC, establecimiento)
y toda la estructura del formulario, así que el sistema la reconoce igual
(Rubro 2, Anexo, cantidades). Al final **verifica** que ningún dato original
sobreviva (ni en el texto, ni en los flujos del PDF, ni en los metadatos) y falla si
queda alguno.

    python scripts/anonimizar_guia.py guia_original.pdf guia_demo.pdf
"""

from __future__ import annotations

import argparse
import ctypes
import io
import re
import sys
from pathlib import Path

import pypdf
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.pdfgen import canvas as rl_canvas

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from marcas.pdf.guia import extraer_encabezado  # noqa: E402

NUMERO_FALSO = "90000001"
CI_FALSO = "1234567"
NOMBRE_FALSO = "COMPRADOR DE EJEMPLO S.A."
FECHA_RE = re.compile(r"\d{2}/\d{2}/\d{4}")


def _escribir(objeto, texto: str) -> None:
    datos = (texto + "\0").encode("utf-16-le")
    buffer = ctypes.create_string_buffer(datos)
    if not pdfium_c.FPDFText_SetText(objeto, ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ushort))):
        raise RuntimeError(f"No se pudo reescribir el texto: {texto!r}")


class Sustituciones:
    """Mismo valor original -> mismo valor falso, en todas las páginas."""

    def __init__(self, encabezado: dict, fecha_propia: str | None):
        self.numero = encabezado.get("numero_orden")
        self.ci_comprador = encabezado.get("comprador_ci_ruc")
        self.nombre_comprador = encabezado.get("comprador_nombre")
        self.ruc_vendedor = encabezado.get("vendedor_ci_ruc")
        self.fecha_propia = fecha_propia
        self._otros: dict[str, str] = {}

    def _falso_numero(self, original: str) -> str:
        if original not in self._otros:
            self._otros[original] = f"9000{len(self._otros) + 2:04d}"
        return self._otros[original]

    def sobre(self, texto: str) -> str:
        nuevo = texto
        if self.nombre_comprador and self.nombre_comprador in nuevo:
            nuevo = nuevo.replace(self.nombre_comprador, NOMBRE_FALSO)
        if self.numero:
            nuevo = nuevo.replace(self.numero, NUMERO_FALSO)
        if self.ci_comprador:
            nuevo = nuevo.replace(self.ci_comprador, CI_FALSO)

        def numero_suelto(m: re.Match) -> str:
            valor = m.group(0)
            if valor in (NUMERO_FALSO, CI_FALSO, self.ruc_vendedor):
                return valor
            return self._falso_numero(valor)

        nuevo = re.sub(r"(?<!\d)\d{8}(?!\d)", numero_suelto, nuevo)          # guías anteriores
        nuevo = re.sub(r"(?<![A-Za-z])[A-Z]{3}\d{3}(?!\d)", "DEM001", nuevo)  # códigos de boleta
        if self.fecha_propia:
            nuevo = FECHA_RE.sub(lambda m: m.group(0) if m.group(0) == self.fecha_propia else self.fecha_propia, nuevo)
        return nuevo


def _codigo_de_barras(c: rl_canvas.Canvas, caja, texto: str) -> None:
    x0, y0, x1, y1 = caja
    # Sin recuadro blanco de fondo: el original ya se quitó, y un rectángulo de este tamaño
    # lo confunde el detector de casillas con la casilla de la Dominante.
    c.setFillColorRGB(0, 0, 0)
    barras = code128.Code128(texto, barHeight=(y1 - y0) * 0.55, barWidth=0.55, humanReadable=False)
    escala = min(1.0, (x1 - x0 - 4) / barras.width)
    c.saveState()
    c.translate(x0 + 2, y0 + (y1 - y0) * 0.30)
    c.scale(escala, 1)
    barras.drawOn(c, 0, 0)
    c.restoreState()
    # La cifra legible va como imagen (en el original también lo es): si fuera texto,
    # el lector del formulario la mezclaría con la línea del N.º de orden.
    from PIL import Image, ImageDraw, ImageFont

    imagen = Image.new("L", (360, 40), 255)
    ImageDraw.Draw(imagen).text((180, 20), texto, fill=0, font=ImageFont.load_default(size=26), anchor="mm")
    from reportlab.lib.utils import ImageReader

    c.drawImage(ImageReader(imagen), x0 + 2, y0 + 1, width=x1 - x0 - 4, height=(x1 - x0 - 4) * 40 / 360, mask=None)


def _qr(c: rl_canvas.Canvas, caja, texto: str) -> None:
    x0, y0, x1, y1 = caja
    # Sin recuadro blanco de fondo: el original ya se quitó, y un rectángulo de este tamaño
    # lo confunde el detector de casillas con la casilla de la Dominante.
    widget = QrCodeWidget(texto)
    b = widget.getBounds()
    ancho, alto = b[2] - b[0], b[3] - b[1]
    dibujo = Drawing(x1 - x0, y1 - y0, transform=[(x1 - x0) / ancho, 0, 0, (y1 - y0) / alto, 0, 0])
    dibujo.add(widget)
    renderPDF.draw(dibujo, c, x0, y0)


def _dentro(bounds, caja) -> bool:
    l, b, r, t = bounds
    x0, y0, x1, y1 = caja
    return l >= x0 and r <= x1 and b >= y0 and t <= y1


def anonimizar(origen: Path, destino: Path) -> dict:
    enc = extraer_encabezado(origen)
    if not enc.get("numero_orden"):
        raise ValueError("No se pudo leer el N.º de guía: ¿es una Guía de Traslado de SENACSA?")
    originales = [v for v in (enc.get("numero_orden"), enc.get("comprador_nombre"), enc.get("comprador_ci_ruc")) if v]

    doc = pdfium.PdfDocument(str(origen))
    # la fecha propia del documento (la de la firma del vendedor), para no tocarla
    texto0 = doc[0].get_textpage().get_text_range()
    fechas = FECHA_RE.findall(texto0)
    fecha_propia = max(set(fechas), key=lambda f: f[6:] + f[3:5] + f[:2]) if fechas else None
    sus = Sustituciones(enc, fecha_propia)

    cajas: list[tuple[int, str, tuple[float, float, float, float]]] = []   # (página, tipo, caja)
    cambiados = 0
    dominantes = 0
    for i in range(len(doc)):
        pagina = doc[i]
        tp = pagina.get_textpage()
        ancho = pagina.get_width()
        for obj in list(pagina.get_objects(textpage=tp)):
            if obj.type == pdfium_c.FPDF_PAGEOBJ_TEXT:
                original = obj.extract()
                nuevo = sus.sobre(original)
                if nuevo != original:
                    _escribir(obj, nuevo)
                    cambiados += 1
            elif obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
                l, b, r, t = obj.get_bounds()
                # El banner de logos ocupa todo el ancho; el código de barras y el QR son cuadrados chicos.
                if (r - l) < ancho * 0.30 and (t - b) < 130 and b > 780:
                    cuadrado = abs((r - l) - (t - b)) < 12
                    es_qr = cuadrado and l > ancho * 0.7 and i > 3
                    cajas.append((i, "qr" if es_qr else "barras", (l, b, r, t)))
                    pagina.remove_obj(obj)
                elif (r - l) < ancho * 0.30 and 600 < b < 780 and i < 4:
                    # La marca dominante dibujada a mano es un dato del dueño: se quita y la
                    # casilla queda libre, así en la demo se puede dibujar una dominante.
                    pagina.remove_obj(obj)
                    dominantes += 1
        # En las hojas de Anexo el código de barras viene como imagen + trazos + la cifra
        # (también dibujada): se quita TODO lo que quede dentro de cada caja, con margen
        # abajo para la cifra legible. Y se comprueba que no quede nada.
        for (p, tipo, (l, b, r, t)) in cajas:
            if p != i:
                continue
            caja_ampliada = (l - 3, b - 16, r + 3, t + 3)
            for _ in range(3):
                restos = [o for o in pagina.get_objects()
                          if _dentro(o.get_bounds(), caja_ampliada)]
                if not restos:
                    break
                for o in restos:
                    pagina.remove_obj(o)
            if any(_dentro(o.get_bounds(), caja_ampliada) for o in pagina.get_objects()):
                raise RuntimeError(f"No se pudo limpiar el código de la página {i + 1}.")
        pagina.gen_content()

    intermedio = io.BytesIO()
    doc.save(intermedio)
    doc.close()

    # Dibujar el código de barras y el QR nuevos sobre las páginas ya limpias.
    lector = pypdf.PdfReader(io.BytesIO(intermedio.getvalue()))
    escritor = pypdf.PdfWriter()
    for i, pagina in enumerate(lector.pages):
        propias = [(t, c) for (p, t, c) in cajas if p == i]
        if propias:
            buf = io.BytesIO()
            lienzo = rl_canvas.Canvas(buf, pagesize=(float(pagina.mediabox.width), float(pagina.mediabox.height)))
            for tipo, caja in propias:
                if tipo == "qr":
                    _qr(lienzo, caja, "DEMO-" + NUMERO_FALSO)
                else:
                    _codigo_de_barras(lienzo, caja, "GE29" + NUMERO_FALSO)
            lienzo.save()
            buf.seek(0)
            pagina.merge_page(pypdf.PdfReader(buf).pages[0])
        escritor.add_page(pagina)   # sólo se copia lo que las páginas usan: no quedan objetos huérfanos
    escritor.add_metadata({"/Producer": "demo", "/Creator": "demo", "/Title": "Guía de Traslado (DEMO anonimizada)"})
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "wb") as fh:
        escritor.write(fh)

    sobrevive = _buscar_restos(destino, originales)
    if sobrevive:
        destino.unlink(missing_ok=True)
        raise RuntimeError(f"Quedaron datos originales en el PDF ({sobrevive}); se descartó el resultado.")
    return {"textos_cambiados": cambiados, "codigos_reemplazados": len(cajas), "buscados": len(originales),
            "dominantes_quitadas": dominantes}


def _buscar_restos(pdf: Path, secretos: list[str]) -> list[str]:
    """Busca cada dato original en el texto de las páginas, en los flujos del PDF
    (descomprimidos), en los metadatos y en los bytes crudos del archivo."""
    secretos = [s for s in secretos if s]
    encontrados: set[str] = set()

    def revisar(blob: bytes | str) -> None:
        texto = blob.decode("latin-1", "ignore") if isinstance(blob, bytes) else blob
        for s in secretos:
            if s in texto:
                encontrados.add(s)

    revisar(Path(pdf).read_bytes())
    lector = pypdf.PdfReader(str(pdf))
    revisar(str(lector.metadata or ""))
    visitados: set[int] = set()

    def recorrer(obj) -> None:
        try:
            obj = obj.get_object() if hasattr(obj, "get_object") else obj
        except Exception:
            return
        if id(obj) in visitados:
            return
        visitados.add(id(obj))
        if isinstance(obj, pypdf.generic.StreamObject):
            try:
                revisar(obj.get_data())
            except Exception:
                pass
        if isinstance(obj, dict):
            for v in obj.values():
                recorrer(v)
        elif isinstance(obj, list):
            for v in obj:
                recorrer(v)

    recorrer(lector.trailer)
    doc = pdfium.PdfDocument(str(pdf))
    for i in range(len(doc)):
        revisar(doc[i].get_textpage().get_text_range())
    return sorted(encontrados)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("origen", type=Path)
    ap.add_argument("destino", type=Path)
    args = ap.parse_args()
    if args.origen.resolve() == args.destino.resolve():
        print("El destino no puede ser el mismo archivo que el origen.", file=sys.stderr)
        return 2
    r = anonimizar(args.origen, args.destino)
    print(f"Listo: {args.destino} · {r['textos_cambiados']} textos reemplazados, "
          f"{r['codigos_reemplazados']} códigos de barras/QR rehechos, {r['dominantes_quitadas']} dominantes quitadas, "
          f"{r['buscados']} datos originales "
          "buscados y ninguno encontrado en el resultado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
