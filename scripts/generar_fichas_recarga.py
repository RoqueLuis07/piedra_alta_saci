"""Genera una ficha personalizada por cada guía existente, para redibujar a mano.

A diferencia de generar_ficha_carga.py (una ficha en blanco genérica), esta
versión trae los datos y las marcas YA CARGADAS de cada guía, para que la
persona sólo corrija lo que esté mal y redibuje cada marca sobre su propio
recuadro -- no hace falta retipear todo de nuevo, y cada recuadro queda
identificado con el código que reemplaza para poder importarlo de vuelta
sin ambigüedad.

Requiere dos exports de la base (operaciones y marcas, con las columnas que
usa este script) como JSON, y la carpeta local de PNG ya digitalizados para
mostrar una miniatura de referencia (tenue) en cada recuadro.

Uso:
    python scripts/generar_fichas_recarga.py \
        --operaciones /tmp/operaciones.json \
        --marcas /tmp/marcas.json \
        --png-dir datos/marcas/png \
        --salida /tmp/fichas_recarga
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

VERDE_OSCURO = (0x1F / 255, 0x4D / 255, 0x34 / 255)
GRIS_TEXTO = (0x5B / 255, 0x65 / 255, 0x60 / 255)
GRIS_CLARO = (0.85, 0.85, 0.83)
NEGRO = (0.08, 0.09, 0.10)

ANCHO, ALTO = A4
MARGEN = 40
EMBLEMA = Path(__file__).resolve().parent.parent / "marcas/servidor/static/logo/emblema_oscuro.png"


def _limpiar(texto: str | None) -> str | None:
    """Los campos de formulario de reportlab sólo aceptan WinAnsi -- sacar lo que no entre ahí."""
    if texto is None:
        return None
    reemplazos = {
        "—": "-", "–": "-", "‘": "'", "’": "'",
        "“": '"', "”": '"', "…": "...",
    }
    for viejo, nuevo in reemplazos.items():
        texto = texto.replace(viejo, nuevo)
    return texto.encode("cp1252", errors="ignore").decode("latin-1")


def _encabezado(c: canvas.Canvas, ref_interna: str, titulo: str, pagina: int, total_paginas: int) -> float:
    y = ALTO - MARGEN
    if EMBLEMA.exists():
        c.drawImage(str(EMBLEMA), MARGEN, y - 34, width=34, height=34, mask="auto", preserveAspectRatio=True)
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGEN + 44, y - 12, "PIEDRA ALTA S.A.C.I.")
    c.setFont("Helvetica", 8)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(MARGEN + 44, y - 24, "ESTANCIA LA PATRICIA · PARAGUAY")

    c.setFont("Helvetica", 8)
    c.drawRightString(ANCHO - MARGEN, y - 12, f"REF. INTERNA {ref_interna} · PÁGINA {pagina} DE {total_paginas}")
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 8)
    c.drawRightString(ANCHO - MARGEN, y - 24, titulo)

    y -= 42
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(1)
    c.line(MARGEN, y, ANCHO - MARGEN, y)
    return y - 10


def _pie(c: canvas.Canvas, ref_interna: str) -> None:
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(MARGEN, 22, f"Recarga de guía {ref_interna} · no cambiar esta referencia -- se usa para saber qué actualizar en el sistema.")


def _titulo_seccion(c: canvas.Canvas, x: float, y: float, ancho: float, texto: str) -> float:
    c.setFillColorRGB(*NEGRO)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawString(x, y, texto)
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.75)
    c.line(x, y - 4, x + ancho, y - 4)
    return y - 22


def _campo(c: canvas.Canvas, x: float, y: float, ancho: float, etiqueta: str, nombre_campo: str, valor: str | None) -> None:
    c.setFont("Helvetica", 7)
    c.setFillColorRGB(*GRIS_TEXTO)
    c.drawString(x, y, etiqueta.upper())
    alto_linea = 18
    y_linea = y - alto_linea + 2
    c.acroForm.textfield(
        name=nombre_campo, x=x, y=y_linea, width=ancho, height=alto_linea,
        value=_limpiar(valor) or "", borderWidth=0, fillColor=None, forceBorder=False,
        fontName="Helvetica", fontSize=10,
    )
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.6)
    c.line(x, y_linea, x + ancho, y_linea)


def _fila_campos(c: canvas.Canvas, x: float, y: float, ancho_total: float, campos: list[tuple[str, str, float, str | None]]) -> float:
    cursor = x
    gap = 14
    disponible = ancho_total - gap * (len(campos) - 1)
    for etiqueta, nombre, fraccion, valor in campos:
        ancho = disponible * fraccion
        _campo(c, cursor, y, ancho, etiqueta, nombre, valor)
        cursor += ancho + gap
    return y - 34


def _recuadro_marca(
    c: canvas.Canvas, x: float, y_top: float, lado: float,
    codigo: str, ruta_referencia: Path | None, campo_descripcion: str, descripcion_actual: str | None,
) -> float:
    """Recuadro para redibujar, con el código a reemplazar y una miniatura tenue de referencia."""
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(1)
    c.rect(x, y_top - lado, lado, lado, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 8)
    c.setFillColorRGB(*NEGRO)
    c.drawString(x, y_top + 3, codigo)

    if ruta_referencia and ruta_referencia.exists():
        lado_ref = min(30, lado * 0.28)
        try:
            c.saveState()
            c.setFillAlpha(0.35)
            c.drawImage(
                str(ruta_referencia), x + lado - lado_ref - 4, y_top - lado_ref - 4,
                width=lado_ref, height=lado_ref, mask="auto", preserveAspectRatio=True, anchor="c",
            )
            c.restoreState()
            c.setFont("Helvetica", 5.5)
            c.setFillColorRGB(*GRIS_TEXTO)
            c.drawRightString(x + lado - 4, y_top - lado_ref - 10, "referencia actual")
        except Exception:
            pass

    y_desc = y_top - lado - 16
    c.acroForm.textfield(
        name=campo_descripcion, x=x, y=y_desc, width=lado, height=14,
        value=_limpiar(descripcion_actual) or "", borderWidth=0, fillColor=None, forceBorder=False,
        fontName="Helvetica", fontSize=8,
    )
    c.setStrokeColorRGB(*NEGRO)
    c.setLineWidth(0.5)
    c.line(x, y_desc, x + lado, y_desc)
    return y_desc


def generar_ficha(operacion: dict, marcas: list[dict], png_dir: Path, salida: Path) -> None:
    dominantes = [m for m in marcas if m.get("tipo") == "dominante"]
    complementarias = [m for m in marcas if m.get("tipo") != "dominante"]
    ref = f"OP-{operacion['id']:04d}"

    total_paginas = 1 + -(-len(complementarias) // 9) if complementarias else 1

    c = canvas.Canvas(str(salida), pagesize=A4)
    c.setTitle(f"Recarga guía {ref}")

    # ---- Página 1: datos + dominante -------------------------------------
    y = _encabezado(c, ref, "DATOS Y MARCA DOMINANTE", 1, total_paginas)
    ancho_util = ANCHO - 2 * MARGEN

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Datos de la guía")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("N.º de guía", "numero_guia", 0.34, operacion.get("numero_guia")),
        ("Fecha", "fecha", 0.33, operacion.get("fecha")),
        ("Tipo de formulario", "tipo_formulario", 0.33, operacion.get("tipo_formulario")),
    ])
    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Vendedor")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Nombre y apellido / razón social", "vendedor_nombre", 0.55, operacion.get("vendedor_nombre")),
        ("CI / RUC", "vendedor_documento", 0.45, operacion.get("vendedor_documento")),
    ])
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Establecimiento de origen", "vendedor_establecimiento", 0.65, operacion.get("vendedor_establecimiento")),
        ("Código de establecimiento", "vendedor_establecimiento_codigo", 0.35, operacion.get("vendedor_establecimiento_codigo")),
    ])
    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Comprador")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Nombre y apellido / razón social", "comprador_nombre", 0.65, operacion.get("comprador_nombre")),
        ("CI / RUC", "comprador_documento", 0.35, operacion.get("comprador_documento")),
    ])
    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Animales")
    cant = operacion.get("cantidad_animales")
    y = _fila_campos(c, MARGEN, y, ancho_util, [
        ("Cantidad", "cantidad_animales", 0.3, str(cant) if cant is not None else None),
        ("Categoría", "categoria_animales", 0.7, operacion.get("categoria_animales")),
    ])

    y = _titulo_seccion(c, MARGEN, y, ancho_util, "Marca dominante")
    if dominantes:
        m = dominantes[0]
        ruta = png_dir / m["origen_archivo"] if m.get("origen_archivo") else None
        _recuadro_marca(c, MARGEN, y, 190, m["codigo"], ruta, f"desc_{m['id']}", m.get("descripcion"))
        if len(dominantes) > 1:
            c.setFont("Helvetica-Oblique", 7)
            c.setFillColorRGB(*GRIS_TEXTO)
            c.drawString(MARGEN + 210, y - 14, f"+ {len(dominantes) - 1} dominante(s) más -- tratar como complementaria en el dibujo.")
    else:
        c.setFont("Helvetica-Oblique", 8)
        c.setFillColorRGB(*GRIS_TEXTO)
        c.drawString(MARGEN, y - 14, "Esta guía no tiene marca dominante cargada.")

    _pie(c, ref)
    c.showPage()

    # ---- Páginas siguientes: complementarias -----------------------------
    restantes = list(complementarias)
    pagina = 2
    while restantes:
        tanda, restantes = restantes[:9], restantes[9:]
        y = _encabezado(c, ref, "MARCAS COMPLEMENTARIAS", pagina, total_paginas)
        ancho_util = ANCHO - 2 * MARGEN
        columnas = 3
        gap_x, gap_y = 16, 34
        ancho_caja = (ancho_util - gap_x * (columnas - 1)) / columnas
        alto_caja = 178

        for i, m in enumerate(tanda):
            fila, col = divmod(i, columnas)
            x = MARGEN + col * (ancho_caja + gap_x)
            y_top = y - fila * (alto_caja + gap_y + 26)
            ruta = png_dir / m["origen_archivo"] if m.get("origen_archivo") else None
            _recuadro_marca(c, x, y_top, ancho_caja, m["codigo"], ruta, f"desc_{m['id']}", m.get("descripcion"))

        _pie(c, ref)
        c.showPage()
        pagina += 1

    c.save()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operaciones", required=True, type=Path)
    parser.add_argument("--marcas", required=True, type=Path)
    parser.add_argument("--png-dir", required=True, type=Path)
    parser.add_argument("--salida", required=True, type=Path)
    args = parser.parse_args()

    operaciones = json.loads(args.operaciones.read_text(encoding="utf-8"))
    marcas = json.loads(args.marcas.read_text(encoding="utf-8"))
    por_operacion: dict[int, list[dict]] = {}
    for m in marcas:
        por_operacion.setdefault(m["operacion_id"], []).append(m)

    args.salida.mkdir(parents=True, exist_ok=True)
    manifiesto = []
    generadas = 0
    for op in operaciones:
        marcas_op = por_operacion.get(op["id"], [])
        if not marcas_op:
            continue
        numero = (op.get("numero_guia") or "SIN-NUMERO").replace("/", "-")
        nombre_archivo = f"OP-{op['id']:04d}_{numero}.pdf"
        ruta_salida = args.salida / nombre_archivo
        generar_ficha(op, marcas_op, args.png_dir, ruta_salida)
        manifiesto.append({
            "archivo": nombre_archivo,
            "operacion_id": op["id"],
            "numero_guia": op.get("numero_guia"),
            "marcas": [{"id": m["id"], "codigo": m["codigo"], "tipo": m["tipo"]} for m in marcas_op],
        })
        generadas += 1

    (args.salida / "manifiesto.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Generadas {generadas} fichas en {args.salida}")


if __name__ == "__main__":
    main()
