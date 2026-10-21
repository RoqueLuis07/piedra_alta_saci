"""Memoria de las guías que se están preparando en «Emitir PDF».

La lectura de una guía (Rubro 2, Anexo, encabezado) y el dibujado de sus páginas son lo que más tarda;
se hacen una vez por PDF y se guardan unos minutos, para que ver la guía con cada marca nueva sea
instantáneo. Es memoria del proceso: si el servicio se reinicia, la pantalla vuelve a subir el PDF sola.
"""

from __future__ import annotations

import hashlib
import io
import threading
from collections import OrderedDict

import pypdfium2 as pdfium

_MAX_GUIAS = 6
_ESCALA = 1.4
_CALIDAD = 74
_candado = threading.Lock()
_guias: "OrderedDict[str, dict]" = OrderedDict()


def clave_de(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()[:24]


def guardar(clave: str, pdf_bytes: bytes, analisis: dict) -> None:
    with _candado:
        _guias[clave] = {"pdf": pdf_bytes, "analisis": analisis, "paginas": {}}
        _guias.move_to_end(clave)
        while len(_guias) > _MAX_GUIAS:
            _guias.popitem(last=False)


def obtener_analisis(clave: str) -> dict | None:
    with _candado:
        g = _guias.get(clave)
        if g:
            _guias.move_to_end(clave)
        return g["analisis"] if g else None


def pagina_jpeg(clave: str, indice: int) -> bytes | None:
    """La página como JPEG (la dibuja la primera vez que se pide)."""
    with _candado:
        g = _guias.get(clave)
        if not g:
            return None
        if indice in g["paginas"]:
            return g["paginas"][indice]
        pdf_bytes = g["pdf"]
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        if not 0 <= indice < len(doc):
            return None
        salida = io.BytesIO()
        doc[indice].render(scale=_ESCALA).to_pil().convert("RGB").save(salida, "JPEG", quality=_CALIDAD)
    finally:
        doc.close()
    contenido = salida.getvalue()
    with _candado:
        if clave in _guias:
            _guias[clave]["paginas"][indice] = contenido
    return contenido
