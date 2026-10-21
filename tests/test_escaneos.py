"""Guías escaneadas: carga del índice, agrupación por N.º de guía, edición y vínculo con las marcas."""

import io
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from cargar_escaneos import cargar, inferir_numeros  # noqa: E402

from test_archivo import _png_marca  # noqa: E402


def _jpeg() -> bytes:
    from PIL import Image

    salida = io.BytesIO()
    Image.new("RGB", (60, 80), (240, 240, 235)).save(salida, "JPEG")
    return salida.getvalue()


def _pagina(carpeta, archivo, orden, formato, numero=None, propietario=None, texto="", **extra):
    return {"carpeta": carpeta, "archivo": archivo, "orden": orden, "formato": formato, "numero_guia": numero,
            "fuente_numero": "ocr" if numero else None, "propietario": propietario, "texto_ocr": texto,
            "propietario_documento": extra.get("doc"), "cantidad_animales": extra.get("cant"),
            "imagen": f"imagenes/{carpeta}_{archivo}.jpg", "miniatura": f"miniaturas/{carpeta}_{archivo}.jpg",
            "n_marcas": extra.get("n_marcas", 0), "ancho": 60, "alto": 80}


def test_inferir_numeros_completa_anexos_y_huecos_entre_iguales():
    paginas = [
        _pagina("A", "1.jpg", 0, "GE2_PRINCIPAL", "91000001"),
        _pagina("A", "2.jpg", 1, "GE2_ANEXO"),                          # anexo: hereda de la principal
        _pagina("A", "3.jpg", 2, "SENACSA_CERTIFICADO", "91000002"),
        _pagina("A", "4.jpg", 3, "CIEFA_GO2"),                          # entre 91000002 y 91000002 -> hereda
        _pagina("A", "5.jpg", 4, "SENACSA_CERTIFICADO", "91000002"),
        _pagina("A", "6.jpg", 5, "CIEFA_GO2"),                          # vecinas distintas -> queda sin número
        _pagina("A", "7.jpg", 6, "CIEFA_GO2", "91000003"),
    ]
    assert inferir_numeros(paginas) == 2
    assert [p["numero_guia"] for p in paginas] == ["91000001", "91000001", "91000002", "91000002", "91000002", None, "91000003"]
    assert paginas[1]["fuente_numero"] == "inferido:anexo" and paginas[3]["fuente_numero"] == "inferido:vecinas"


def _preparar_indice(carpeta: Path, paginas: list[dict]) -> None:
    (carpeta / "imagenes").mkdir(parents=True)
    (carpeta / "miniaturas").mkdir()
    for p in paginas:
        (carpeta / p["imagen"]).write_bytes(_jpeg())
        (carpeta / p["miniatura"]).write_bytes(_jpeg())
    (carpeta / "indice.jsonl").write_text("\n".join(json.dumps(p) for p in paginas), encoding="utf-8")


def test_guias_escaneadas_de_punta_a_punta(cliente, tmp_path):
    from marcas.servidor import db

    paginas = [
        _pagina("L1", "0001.jpg", 0, "GE2_PRINCIPAL", "10894231", texto="Fiebre Aftosa vacunacion", n_marcas=2),
        _pagina("L1", "0002.jpg", 1, "GE2_ANEXO"),                      # sin número: hereda de la principal
        _pagina("L1", "0003.jpg", 2, "SENACSA_CERTIFICADO", "10894231", "HUGO ALBERTO FRANCO", doc="4304490", cant=21,
                texto="PROPIETARIO 4304490 HUGO ALBERTO FRANCO"),
        _pagina("L1", "0004.jpg", 3, "SENACSA_CERTIFICADO", "10895005", "OTRO PROPIETARIO", doc="111"),
        _pagina("L1", "0005.jpg", 4, "LOGISTICA_SIN_MARCAS"),         # sin número y sin vecinas iguales
    ]
    _preparar_indice(tmp_path / "indice", paginas)

    conexion = db.obtener_conexion()
    try:
        # catálogo de dos marcas que "salieron" de la página 0001
        with conexion.cursor() as cur:
            ids = []
            for i in range(2):
                cur.execute(
                    "INSERT INTO marcas (tipo, estado, origen_carpeta, origen_pagina, posicion, archivo_png) "
                    "VALUES ('complementaria', 'activa', 'L1', '0001.jpg', %s, %s) RETURNING id, codigo",
                    (f"Complementaria · casilla 1·{i + 1}", psycopg2_binary(_png_marca())),
                )
                ids.append(cur.fetchone())
        conexion.commit()
        seco = cargar(conexion, tmp_path / "indice", escribir=False, informar=lambda *_: None)
        assert seco["paginas"] == 5 and seco["nuevas"] == 0
        c = cargar(conexion, tmp_path / "indice", informar=lambda *_: None)
        assert (c["nuevas"], c["imagenes"], c["marcas_vinculadas"]) == (5, 5, 2)
        assert c["inferidas"] == 1                                    # el anexo hereda 10894231
        otra = cargar(conexion, tmp_path / "indice", informar=lambda *_: None)
        assert (otra["nuevas"], otra["actualizadas"], otra["imagenes"]) == (0, 5, 0)   # idempotente
    finally:
        db.devolver_conexion(conexion)

    # --- lista agrupada por guía ---
    lista = cliente.get("/escaneos").get_data(as_text=True)
    assert "10894231" in lista and "10895005" in lista and "HUGO ALBERTO FRANCO" in lista
    assert "/escaneos/miniatura/" in lista
    # búsqueda en el texto leído de la imagen: encuentra la GUÍA entera aunque sólo coincida una página
    assert "10894231" in cliente.get("/escaneos?q=Aftosa").get_data(as_text=True)
    assert "10895005" not in cliente.get("/escaneos?q=Aftosa").get_data(as_text=True).split("<tbody>")[1]
    assert "Certificado sanitario" in cliente.get("/escaneos?formato=SENACSA_CERTIFICADO").get_data(as_text=True)

    # --- ficha de la guía: documentos + marcas + botón para estampar ---
    ficha = cliente.get("/escaneos/guia/10894231").get_data(as_text=True)
    assert "Documentos escaneados (3)" in ficha           # CIEFA + certificado + anexo heredado
    assert "Marcas de esta guía (2)" in ficha and ids[0]["codigo"] in ficha
    assert "/emitir?marcas=" in ficha
    assert cliente.get("/escaneos/guia/no-existe").status_code == 404

    # --- páginas sin número, y asignarlo a mano ---
    assert "L1 · 0005.jpg" in cliente.get("/escaneos?vista=sin_numero").get_data(as_text=True)
    from marcas.servidor import db as _db

    c2 = _db.obtener_conexion()
    with c2.cursor() as cur:
        cur.execute("SELECT id FROM escaneos WHERE archivo = '0005.jpg'")
        sin_numero_id = cur.fetchone()["id"]
    _db.devolver_conexion(c2)
    r = cliente.post(f"/escaneos/pagina/{sin_numero_id}", json={"numero_guia": "10895005"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert "L1 · 0005.jpg" not in cliente.get("/escaneos?vista=sin_numero").get_data(as_text=True).split("tarjetas")[-1]
    assert "Documentos escaneados (2)" in cliente.get("/escaneos/guia/10895005").get_data(as_text=True)
    assert "corregido a mano" in cliente.get(f"/escaneos/pagina/{sin_numero_id}").get_data(as_text=True)

    # lo corregido a mano NO se pisa al volver a cargar el índice
    conexion = _db.obtener_conexion()
    try:
        c3 = cargar(conexion, tmp_path / "indice", informar=lambda *_: None)
        assert c3["protegidas"] == 1
    finally:
        _db.devolver_conexion(conexion)
    assert "Documentos escaneados (2)" in cliente.get("/escaneos/guia/10895005").get_data(as_text=True)

    # --- imágenes ---
    assert cliente.get(f"/escaneos/imagen/{sin_numero_id}.jpg").mimetype == "image/jpeg"
    assert cliente.get(f"/escaneos/miniatura/{sin_numero_id}.jpg").status_code == 200
    assert cliente.get("/escaneos/imagen/999999.jpg").status_code == 404

    # --- la marca sabe de qué página salió; Emitir PDF precarga las de la guía ---
    ficha_marca = cliente.get(f"/marcas/{ids[0]['codigo']}").get_data(as_text=True)
    assert "De dónde salió" in ficha_marca and "L1 · 0001.jpg" in ficha_marca
    emitir = cliente.get(f"/emitir?marcas={ids[0]['codigo']},{ids[1]['codigo']}").get_data(as_text=True)
    assert ids[0]["codigo"] in emitir and ids[1]["codigo"] in emitir

    # datos inválidos
    assert cliente.post(f"/escaneos/pagina/{sin_numero_id}", json={"numero_guia": "9" * 60}).status_code == 400
    assert cliente.post("/escaneos/pagina/999999", json={"numero_guia": "1"}).status_code == 404


def psycopg2_binary(datos: bytes):
    import psycopg2

    return psycopg2.Binary(datos)
