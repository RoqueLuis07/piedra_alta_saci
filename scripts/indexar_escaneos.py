"""Indexa las páginas escaneadas: lee con OCR el N.º de guía y los datos de cada
página, prepara las imágenes (reducidas) y agrupa las páginas por guía.

No toca ninguna base de datos: escribe una carpeta de salida con

    indice.jsonl      una línea por página (formato, N.º de guía, propietario, texto leído…)
    imagenes/         JPEG de cada página (1400 px de lado largo)
    miniaturas/       JPEG de 320 px

que luego carga ``scripts/cargar_escaneos.py``. Se puede cortar y volver a correr:
las páginas ya indexadas se saltean.

    python scripts/indexar_escaneos.py --escaneos "<carpeta Escaneos>" --salida salida_escaneos --trabajadores 5
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

LADO_OCR = 1600
LADO_IMAGEN = 1400
LADO_MINIATURA = 320
RUC_PIEDRA_ALTA = "80020081"

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


# ---------------------------------------------------------------- páginas ----

def listar_paginas(escaneos: Path) -> list[dict]:
    """Las 790 páginas, con el formato que ya les puso la extracción de marcas."""
    filas = list(csv.DictReader(open(escaneos / "Catalogo_Final" / "paginas.csv", encoding="utf-8")))
    paginas = []
    for orden, r in enumerate(filas):
        carpeta, archivo = r["carpeta_escaneo"], r["archivo_origen"]
        if carpeta == "L3210":
            ruta, pdf_pagina = escaneos / "Carpeta Epson L3210" / archivo, None
        elif carpeta == "L5290":
            ruta, pdf_pagina = escaneos / "Carpeta Epson L5290" / archivo, None
        else:  # PARTE1 / PARTE2: página de un PDF
            n = int(re.search(r"_(\d+)", archivo).group(1))
            ruta = escaneos / "Carpeta Epson L5290" / ("Parte 1.pdf" if carpeta == "PARTE1" else "Parte 2.pdf")
            pdf_pagina = n - 1
        paginas.append({
            "carpeta": carpeta, "archivo": archivo, "orden": orden, "ruta": str(ruta), "pdf_pagina": pdf_pagina,
            "formato": r["formato_detectado"], "n_marcas": int(r["n_marcas_extraidas"] or 0),
        })
    return paginas


def cargar_imagen(pagina: dict):
    from PIL import Image

    if pagina["pdf_pagina"] is None:
        im = Image.open(pagina["ruta"])
        im.load()
        return im.convert("RGB")
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(pagina["ruta"])
    try:
        return doc[pagina["pdf_pagina"]].render(scale=2.4).to_pil().convert("RGB")
    finally:
        doc.close()


def reducir(im, lado: int):
    w, h = im.size
    esc = lado / max(w, h)
    return im.resize((max(1, int(w * esc)), max(1, int(h * esc)))) if esc < 1 else im.copy()


# ------------------------------------------------------------- extracción ----

def _centro(caja):
    xs = [p[0] for p in caja]
    ys = [p[1] for p in caja]
    return (sum(xs) / 4, sum(ys) / 4)


def extraer_campos(formato: str, lineas: list[dict], alto: int) -> dict:
    """``lineas``: [{texto, caja, conf}] del OCR. Devuelve los datos reconocibles de la página."""
    campos = {"numero_guia": None, "fuente_numero": None, "numero_certificado": None,
              "propietario": None, "propietario_documento": None, "establecimiento": None,
              "cantidad_animales": None, "fecha_texto": None, "conf_numero": 0.0}
    norm = [(re.sub(r"\s+", "", l["texto"]).upper(), l) for l in lineas]
    todo = "\n".join(l["texto"] for l in lineas)

    def ochos(texto):
        return [m for m in re.findall(r"(?<!\d)\d{8}(?!\d)", texto) if m != RUC_PIEDRA_ALTA]

    # N.º de certificado: 14 dígitos bajo el código de barras
    m = re.search(r"(?<!\d)(\d{14})(?!\d)", todo.replace(" ", ""))
    if m and formato == "SENACSA_CERTIFICADO":
        campos["numero_certificado"] = m.group(1)

    numero = None
    if formato == "SENACSA_CERTIFICADO":
        # «…los datos 10894231 del anverso de este documento…»
        for t, l in norm:
            m = re.search(r"(\d{8})DELANVERSO", t)
            if m and m.group(1) != RUC_PIEDRA_ALTA:
                numero, campos["fuente_numero"], campos["conf_numero"] = m.group(1), "certificado:anverso", l["conf"]
                break
        if not numero:
            for t, l in norm:
                if "ANVERSO" in t or "DATOS" in t:
                    c = ochos(l["texto"])
                    if c:
                        numero, campos["fuente_numero"], campos["conf_numero"] = c[0], "certificado:linea", l["conf"]
                        break
        if not numero:
            anverso = [l for t, l in norm if "ANVERSO" in t or "DATOSDEL" in t]
            cand = [(c, l) for _, l in norm for c in ochos(l["texto"]) if c not in (campos["numero_certificado"] or "")]
            if cand:
                if anverso:
                    cand.sort(key=lambda par: min(abs(_centro(par[1]["caja"])[1] - _centro(a["caja"])[1]) for a in anverso))
                c, l = cand[0]
                numero, campos["fuente_numero"], campos["conf_numero"] = c, "certificado:cercano", l["conf"] * 0.8
        # propietario: «PROPIETARIO 4304490» + el nombre en la misma fila
        for t, l in norm:
            m = re.match(r"PROPIETARIO[:\.]?(\d{4,9})", t)
            if m:
                campos["propietario_documento"] = m.group(1)
                cy = _centro(l["caja"])[1]
                cx = _centro(l["caja"])[0]
                mejores = [
                    o for _, o in norm
                    if abs(_centro(o["caja"])[1] - cy) < alto * 0.012 and _centro(o["caja"])[0] > cx
                    and re.fullmatch(r"[A-ZÁÉÍÓÚÑ ]{5,}", o["texto"].strip())
                    and not re.search(r"DEPART|DISTRITO|NOMBRE|CODIGO", o["texto"].upper())
                ]
                if mejores:
                    campos["propietario"] = mejores[0]["texto"].strip()
                    campos["caja_propietario"] = mejores[0]["caja"]
                break
        for t, l in norm:
            m = re.match(r"ESTABLECIMIENTO[:\.]?(\d{6,12})", t)
            if m:
                campos["establecimiento"] = l["texto"].strip()[:80]
                break
        # animales en tránsito: las celdas vienen como «****16» y la última suma todo
        cifras = [int(x) for x in re.findall(r"\*{3,}(\d{1,4})", todo.replace(" ", ""))]
        if cifras:
            campos["cantidad_animales"] = max(cifras)
    elif formato in ("CIEFA_GO2", "GE2_PRINCIPAL", "GE2_ANEXO"):
        # el número impreso junto a «N° DE ORDEN» / «Guía N°»
        etiquetas = [l for t, l in norm if re.search(r"N.?DEORDEN|NDEORDEN|GUIAN|ORDEN", t)]
        candidatos = []
        for t, l in norm:
            for c in ochos(l["texto"]):
                candidatos.append((c, l))
        if candidatos:
            def distancia(par):
                _, l = par
                cx, cy = _centro(l["caja"])
                if not etiquetas:
                    return 9e9
                return min(abs(_centro(e["caja"])[1] - cy) + abs(_centro(e["caja"])[0] - cx) * 0.2 for e in etiquetas)

            numero, l = min(candidatos, key=distancia)
            campos["fuente_numero"], campos["conf_numero"] = f"{formato.lower()}:orden", l["conf"]
        if formato == "GE2_PRINCIPAL":
            nombres = [l["texto"].strip() for _, l in norm if re.search(r"S\.?A\.?|S\.?R\.?L|LTDA|INMOBILIARIA", l["texto"].upper())]
            if nombres:
                campos["propietario"] = nombres[-1][:80]
    campos["numero_guia"] = numero

    fecha = re.search(r"(\d{2}/\d{2}/\d{4})", todo)
    if fecha:
        campos["fecha_texto"] = fecha.group(1)
    return campos


# ---------------------------------------------------------------- trabajo ----

_motor = None


def _iniciar_trabajador(hilos: int):
    """Cada trabajador usa pocos hilos de onnxruntime: con todos los núcleos por
    proceso, varios procesos se pisan y resulta MÁS lento que uno solo."""
    global _motor
    import onnxruntime as ort

    original = ort.InferenceSession.__init__

    def con_hilos(self, ruta, sess_options=None, providers=None, **kw):
        if sess_options is None:
            sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = hilos
        sess_options.inter_op_num_threads = 1
        original(self, ruta, sess_options=sess_options, providers=providers, **kw)

    ort.InferenceSession.__init__ = con_hilos
    from rapidocr_onnxruntime import RapidOCR

    _motor = RapidOCR()


def _nombre_con_espacios(im, tam_ocr, caja, leido: str) -> str:
    """El OCR de toda la página junta las palabras de un nombre («HUGOALBERTOFRANCO»).
    Releyendo sólo esa línea, recortada del original en alta resolución y estirada a lo
    ancho, aparecen los espacios («HUGO ALBERTO FRANCO»)."""
    import numpy as np

    esc = im.size[0] / tam_ocr[0]
    xs, ys = [p[0] for p in caja], [p[1] for p in caja]
    margen = 6
    recorte = im.crop((max(0, int((min(xs) - margen) * esc)), max(0, int((min(ys) - margen) * esc)),
                       min(im.size[0], int((max(xs) + margen) * esc)), min(im.size[1], int((max(ys) + margen) * esc))))
    estirado = recorte.resize((int(recorte.size[0] * 1.6), recorte.size[1]))
    resultado, _ = _motor(np.array(estirado))
    if not resultado:
        return leido
    trozos = sorted(resultado, key=lambda r: min(p[0] for p in r[0]))
    nuevo = " ".join(r[1].strip() for r in trozos).strip()
    # sólo se acepta si dice lo mismo que la lectura original (sin contar espacios)
    solo = lambda t: "".join(ch for ch in t.upper() if ch.isalpha())
    a, b = solo(leido), solo(nuevo)
    parecido = sum(1 for x, y in zip(a, b) if x == y) / max(len(a), len(b), 1)
    return nuevo if nuevo and parecido >= 0.85 else leido


def procesar(pagina: dict, salida: str) -> dict:
    import numpy as np

    salida = Path(salida)
    im = cargar_imagen(pagina)
    base = f"{pagina['carpeta']}_{Path(pagina['archivo']).stem}"
    reducir(im, LADO_IMAGEN).save(salida / "imagenes" / f"{base}.jpg", "JPEG", quality=78, optimize=True)
    reducir(im, LADO_MINIATURA).save(salida / "miniaturas" / f"{base}.jpg", "JPEG", quality=70)
    ocr = reducir(im, LADO_OCR)
    inicio = time.time()
    resultado, _ = _motor(np.array(ocr))
    lineas = [{"texto": r[1], "caja": [[float(x), float(y)] for x, y in r[0]], "conf": float(r[2])}
              for r in (resultado or [])]
    campos = extraer_campos(pagina["formato"], lineas, ocr.size[1])
    caja = campos.pop("caja_propietario", None)
    if caja and campos.get("propietario"):
        campos["propietario"] = _nombre_con_espacios(im, ocr.size, caja, campos["propietario"])
    return {
        "carpeta": pagina["carpeta"], "archivo": pagina["archivo"], "orden": pagina["orden"],
        "formato": pagina["formato"], "n_marcas": pagina["n_marcas"], "imagen": f"imagenes/{base}.jpg",
        "miniatura": f"miniaturas/{base}.jpg", "ancho": im.size[0], "alto": im.size[1],
        "texto_ocr": "\n".join(l["texto"] for l in lineas), "segundos_ocr": round(time.time() - inicio, 1),
        **campos,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--escaneos", required=True, type=Path)
    ap.add_argument("--salida", required=True, type=Path)
    ap.add_argument("--trabajadores", type=int, default=6)
    ap.add_argument("--hilos", type=int, default=2, help="hilos de onnxruntime por trabajador")
    ap.add_argument("--limite", type=int, default=None, help="sólo las primeras N páginas (para probar)")
    args = ap.parse_args()

    (args.salida / "imagenes").mkdir(parents=True, exist_ok=True)
    (args.salida / "miniaturas").mkdir(parents=True, exist_ok=True)
    indice = args.salida / "indice.jsonl"
    hechas = set()
    if indice.exists():
        for linea in indice.read_text(encoding="utf-8").splitlines():
            if linea.strip():
                d = json.loads(linea)
                hechas.add((d["carpeta"], d["archivo"]))
    pendientes = [p for p in listar_paginas(args.escaneos) if (p["carpeta"], p["archivo"]) not in hechas]
    if args.limite:
        pendientes = pendientes[: args.limite]
    print(f"{len(hechas)} ya indexadas · {len(pendientes)} por hacer · {args.trabajadores} trabajadores", flush=True)
    inicio = time.time()
    hechas_ahora = 0
    with open(indice, "a", encoding="utf-8") as salida_idx, ProcessPoolExecutor(
        max_workers=args.trabajadores, initializer=_iniciar_trabajador, initargs=(args.hilos,)
    ) as pool:
        futuros = {pool.submit(procesar, p, str(args.salida)): p for p in pendientes}
        for f in as_completed(futuros):
            p = futuros[f]
            try:
                d = f.result()
            except Exception as exc:                              # una página mala no frena el resto
                d = {"carpeta": p["carpeta"], "archivo": p["archivo"], "orden": p["orden"], "formato": p["formato"],
                     "n_marcas": p["n_marcas"], "error": f"{type(exc).__name__}: {exc}"[:200]}
            salida_idx.write(json.dumps(d, ensure_ascii=False) + "\n")
            salida_idx.flush()
            hechas_ahora += 1
            if hechas_ahora % 10 == 0:
                ritmo = (time.time() - inicio) / hechas_ahora
                print(f"  {hechas_ahora}/{len(pendientes)} · {ritmo:.1f} s/página · faltan ~{(len(pendientes) - hechas_ahora) * ritmo / 60:.0f} min", flush=True)
    print("Listo.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
