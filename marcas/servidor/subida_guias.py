"""Carga de guías nuevas desde un PDF o desde fotos/escaneos: cada página queda como un escaneo más,
con su imagen, su miniatura y (si el PDF trae texto) el texto para buscar.

No hay OCR acá: el servidor chico no lo soporta. Un PDF oficial de SENACSA trae su texto y el N.º de
orden se lee solo; en una foto el N.º de guía se escribe a mano al subirla (o después, en la página).
"""

from __future__ import annotations

import io
import re
import tempfile
import uuid
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from PIL import Image, ImageOps

LADO_IMAGEN = 1400
LADO_MINIATURA = 320
MAX_PAGINAS = 60
MAX_BYTES_ARCHIVO = 30 * 1024 * 1024
CARPETA = "subidas"
_EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


class ErrorSubida(ValueError):
    """Un archivo que no se puede usar; el mensaje es para mostrarle a la persona."""


def _jpeg(imagen: Image.Image, lado: int, calidad: int) -> bytes:
    imagen = imagen.convert("RGB")
    imagen.thumbnail((lado, lado))
    salida = io.BytesIO()
    imagen.save(salida, "JPEG", quality=calidad, optimize=True)
    return salida.getvalue()


def _formato(texto: str) -> str:
    t = texto.upper()
    if "ANEXO" in t and "RUBRO" not in t:
        return "GE2_ANEXO"
    if "RUBRO 2" in t or "GUÍA DE TRASLADO" in t or "GUIA DE TRASLADO" in t:
        return "GE2_PRINCIPAL"
    if "CERTIFICADO" in t and "SANITARIO" in t:
        return "SENACSA_CERTIFICADO"
    return "DESCONOCIDO"


def _numero_del_pdf(contenido: bytes, nombre: str) -> str | None:
    """El N.º de orden de una guía oficial (del texto del PDF) o, si no se pudo, el del nombre del archivo (GE123…)."""
    try:
        from marcas.pdf.guia import extraer_encabezado

        with tempfile.TemporaryDirectory() as carpeta:
            ruta = Path(carpeta) / "guia.pdf"
            ruta.write_bytes(contenido)
            numero = extraer_encabezado(ruta).get("numero_orden")
        if numero:
            return str(numero).strip()
    except Exception:
        pass
    m = re.search(r"GE\s*(\d{6,})", nombre, re.I) or re.search(r"(\d{7,})", nombre)
    return m.group(1) if m else None


def paginas_de_pdf(contenido: bytes, maximo: int) -> list[dict]:
    import pypdf
    import pypdfium2 as pdfium

    try:
        documento = pdfium.PdfDocument(contenido)
        lector = pypdf.PdfReader(io.BytesIO(contenido))
    except Exception as exc:
        raise ErrorSubida("El PDF está dañado o protegido con contraseña.") from exc
    paginas = []
    for i in range(min(len(documento), maximo)):
        pagina = documento[i]
        ancho, alto = pagina.get_size()
        imagen = pagina.render(scale=LADO_IMAGEN / max(ancho, alto, 1)).to_pil()
        try:
            texto = (lector.pages[i].extract_text() or "").strip()
        except Exception:
            texto = ""
        respaldo = None
        try:
            from marcas.pdf.guia import parsear_respaldo, texto_ordenado_de_pagina

            respaldo = parsear_respaldo(texto_ordenado_de_pagina(pagina)) or None
        except Exception:
            respaldo = None
        paginas.append({"imagen": imagen, "texto": texto, "respaldo": respaldo})
    return paginas


def preparar(nombre: str, contenido: bytes, maximo: int) -> tuple[list[dict], str | None]:
    """(páginas, número de guía detectado) de un archivo subido: PDF o imagen."""
    if len(contenido) > MAX_BYTES_ARCHIVO:
        raise ErrorSubida(f"«{nombre}» pesa más de {MAX_BYTES_ARCHIVO // 1024 // 1024} MB.")
    sufijo = Path(nombre).suffix.lower()
    if contenido[:5] == b"%PDF-" or sufijo == ".pdf":
        paginas = paginas_de_pdf(contenido, maximo)
        if not paginas:
            raise ErrorSubida(f"«{nombre}» no tiene páginas.")
        return paginas, _numero_del_pdf(contenido, nombre)
    if sufijo in _EXTENSIONES_IMAGEN or contenido[:3] == b"\xff\xd8\xff" or contenido[:4] == b"\x89PNG":
        try:
            imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(contenido)))
            imagen.load()
        except Exception as exc:
            raise ErrorSubida(f"«{nombre}» no se pudo leer como imagen.") from exc
        return [{"imagen": imagen, "texto": ""}], None
    raise ErrorSubida(f"«{nombre}» no es un PDF ni una imagen (JPG, PNG).")


def guardar_paginas(conexion, paginas: list[dict], numero_guia: str | None, fuente: str | None,
                    formato: str | None, lote: str, orden_inicial: int, nombre: str = "") -> list[int]:
    """Inserta las páginas como escaneos nuevos. Devuelve sus ids."""
    ids = []
    with conexion.cursor() as cur:
        for i, p in enumerate(paginas):
            imagen = p["imagen"]
            grande = _jpeg(imagen, LADO_IMAGEN, 78)
            chica = _jpeg(imagen, LADO_MINIATURA, 70)
            cur.execute(
                """
                INSERT INTO escaneos (carpeta, archivo, orden, formato, numero_guia, fuente_numero, confianza_numero,
                                      texto_ocr, ancho, alto, etiqueta, respaldo)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
                """,
                (CARPETA, f"{lote}_{orden_inicial + i + 1:03d}.jpg", orden_inicial + i, formato or _formato(p["texto"]),
                 numero_guia, fuente if numero_guia else None, 1.0 if numero_guia else None,
                 p["texto"] or None, imagen.size[0], imagen.size[1],
                 f"Subida · {nombre[:60]} · pág. {i + 1}" if nombre else f"Subida · pág. {i + 1}",
                 Json(p["respaldo"]) if p.get("respaldo") else None),
            )
            nuevo = cur.fetchone()["id"]
            cur.execute("INSERT INTO escaneos_imagenes (escaneo_id, imagen, miniatura) VALUES (%s, %s, %s)",
                        (nuevo, psycopg2.Binary(grande), psycopg2.Binary(chica)))
            ids.append(nuevo)
    return ids


def nuevo_lote() -> str:
    return uuid.uuid4().hex[:10]
