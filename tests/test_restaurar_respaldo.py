"""Restaurar un respaldo (scripts/restaurar_respaldo.py) en una base nueva: ida y vuelta con datos reales de prueba."""

import sys
from pathlib import Path

import pytest

pg = pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from restaurar_respaldo import leer_respaldo, restaurar  # noqa: E402


def _esquema_sin_pgcrypto() -> str:
    ruta = Path(__file__).resolve().parent.parent / "marcas" / "servidor" / "schema.sql"
    return ruta.read_text(encoding="utf-8").replace("create extension if not exists pgcrypto;", "")


def test_el_respaldo_se_restaura_completo_en_otra_base(cliente, tmp_path):
    import psycopg2
    from psycopg2.extras import RealDictCursor

    from marcas.servidor import db
    from marcas.servidor.consultas import dict_a_json, exportar_todo_para_respaldo

    # --- datos en la base de origen ---
    png = b"\x89PNG\r\n\x1a\n" + bytes(range(200))
    origen = db.obtener_conexion()
    try:
        with origen.cursor() as cur:
            cur.execute("INSERT INTO propietarios (nombre, documento) VALUES ('Prop Uno', '123') RETURNING id")
            prop_id = cur.fetchone()["id"]
            cur.execute("INSERT INTO operaciones (numero_guia, vendedor_nombre, tipo_operacion) VALUES ('R-1', 'V', 'compra') RETURNING id")
            op_id = cur.fetchone()["id"]
            for i in range(3):
                cur.execute(
                    "INSERT INTO marcas (tipo, descripcion, operacion_id, propietario_id, archivo_png) VALUES "
                    "('complementaria', %s, %s, %s, %s)", (f"marca {i}", op_id, prop_id, psycopg2.Binary(png) if i == 0 else None))
            cur.execute("SELECT id FROM usuarios LIMIT 1")
            uid = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO cambios_pendientes (tabla, fila_id, cambios, valores_anteriores, propuesto_por) "
                "VALUES ('operaciones', %s, %s, %s, %s)",
                (op_id, psycopg2.extras.Json({"numero_guia": "R-2"}), psycopg2.extras.Json({"numero_guia": "R-1"}), uid))
        origen.commit()
        exportado = exportar_todo_para_respaldo(origen)
    finally:
        db.devolver_conexion(origen)

    # --- el respaldo, con la misma forma que deja scripts/respaldo_a_github.py ---
    carpeta = tmp_path / "respaldo"
    (carpeta / "marcas").mkdir(parents=True)
    marcas = exportado.pop("marcas")
    for tabla, filas in exportado.items():
        (carpeta / f"{tabla}.json").write_text(dict_a_json(filas), encoding="utf-8")
    for n in range(0, len(marcas), 2):           # lotes de 2 para ejercitar varios archivos
        (carpeta / "marcas" / f"lote_{n // 2 + 1:04d}.json").write_text(dict_a_json(marcas[n:n + 2]), encoding="utf-8")
    datos = leer_respaldo(carpeta)
    assert len(datos["marcas"]) == len(marcas) >= 3

    # --- base destino nueva y vacía ---
    servidor = pg.get_server(tmp_path / "destino")
    try:
        destino = psycopg2.connect(servidor.get_uri(), cursor_factory=RealDictCursor)
        with destino.cursor() as cur:
            cur.execute(_esquema_sin_pgcrypto())
        destino.commit()

        seco = restaurar(destino, datos, escribir=False)
        assert seco["en_seco"]
        with destino.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM marcas")
            assert cur.fetchone()["n"] == 0                      # en seco no escribe

        r = restaurar(destino, datos)
        assert r["marcas"] == len(marcas) and r["usuarios"] == 1 and r["operaciones"] >= 1

        with destino.cursor() as cur:
            cur.execute("SELECT password_hash, email FROM usuarios")
            assert cur.fetchone()["email"] == "admin@prueba.test"        # los usuarios entran con la misma contraseña
            cur.execute("SELECT archivo_png FROM marcas WHERE archivo_png IS NOT NULL")
            assert bytes(cur.fetchone()["archivo_png"]) == png            # la imagen vuelve byte a byte
            cur.execute("SELECT cambios FROM cambios_pendientes")
            assert cur.fetchone()["cambios"] == {"numero_guia": "R-2"}
            # las secuencias siguen desde el máximo: una marca y una operación nuevas no chocan
            cur.execute("INSERT INTO marcas (tipo) VALUES ('dominante') RETURNING codigo")
            nuevo = cur.fetchone()["codigo"]
            cur.execute("SELECT max(substring(codigo from '[0-9]+$')::int) AS m FROM marcas WHERE codigo <> %s", (nuevo,))
            assert int(nuevo.split("-")[1]) == cur.fetchone()["m"] + 1
            cur.execute("INSERT INTO operaciones (numero_guia) VALUES ('R-9') RETURNING id")
            assert cur.fetchone()["id"] > op_id
        destino.commit()

        # no se restaura sobre una base que ya tiene datos
        with pytest.raises(RuntimeError, match="ya tiene"):
            restaurar(destino, datos)
        destino.close()
    finally:
        servidor.cleanup()


def test_el_respaldo_completo_incluye_documentos_escaneos_y_versiones(cliente, tmp_path):
    """Ida y vuelta de lo que pesa: páginas escaneadas con su imagen, PDF emitidos, historial de imágenes y el
    documento original de cada operación, con el mismo volcado por tandas que usa el script de respaldo."""
    import io
    import json

    import psycopg2
    from PIL import Image
    from psycopg2.extras import Json, RealDictCursor

    from marcas.servidor import db
    from marcas.servidor.respaldo import volcar

    foto = io.BytesIO()
    Image.new("RGB", (400, 600), (210, 200, 190)).save(foto, "JPEG")
    foto = foto.getvalue()
    pdf = b"%PDF-1.4\n" + bytes(range(256)) * 20 + b"\n%%EOF"

    origen = db.obtener_conexion()
    try:
        with origen.cursor() as cur:
            cur.execute("SELECT id FROM usuarios LIMIT 1")
            uid = cur.fetchone()["id"]
            cur.execute("INSERT INTO operaciones (numero_guia, tipo_operacion, vendedor_nombre) VALUES ('RC-1', 'venta', 'V') RETURNING id")
            op = cur.fetchone()["id"]
            for i in range(3):    # 3 páginas con tandas de 2 para ejercitar varios archivos
                cur.execute(
                    "INSERT INTO escaneos (carpeta, archivo, numero_guia, formato, respaldo, texto_ocr) VALUES "
                    "('subidas', %s, 'RC-1', 'GE2_PRINCIPAL', %s, 'texto') RETURNING id",
                    (f"rc{i}.jpg", Json([{"cod": "G", "numero": "90000001", "fecha": None}]) if i == 0 else None))
                esc = cur.fetchone()["id"]
                cur.execute("INSERT INTO escaneos_imagenes (escaneo_id, imagen, miniatura) VALUES (%s, %s, %s)",
                            (esc, psycopg2.Binary(foto), psycopg2.Binary(b"mini")))
            cur.execute("INSERT INTO marcas (tipo, escaneo_id, operacion_id, archivo_png) VALUES ('complementaria', %s, %s, %s) RETURNING id",
                        (esc, op, psycopg2.Binary(b"\x89PNG-uno")))
            marca = cur.fetchone()["id"]
            cur.execute("INSERT INTO marcas_versiones_imagen (marca_id, archivo_png, motivo, creado_por) VALUES (%s, %s, 'reemplazo', %s)",
                        (marca, psycopg2.Binary(b"\x89PNG-viejo"), uid))
            cur.execute("INSERT INTO respaldos_operacion (operacion_id, contenido, nombre) VALUES (%s, %s, 'orig.pdf')", (op, psycopg2.Binary(pdf)))
            cur.execute(
                "INSERT INTO documentos_emitidos (operacion_id, tipo, numero_guia, emitido_por, datos, marcas, nombre, contenido) "
                "VALUES (%s, 'venta', 'RC-1', %s, %s, %s, 'emitido.pdf', %s) RETURNING id",
                (op, uid, Json({"k": 1}), Json([{"codigo": "M-1"}]), psycopg2.Binary(pdf)))
            doc = cur.fetchone()["id"]
        origen.commit()

        archivos: dict[str, bytes] = {}
        from marcas.servidor import respaldo

        tanda_original = respaldo.TANDAS["escaneos"]
        respaldo.TANDAS["escaneos"] = 2
        try:
            manifiesto = volcar(origen, lambda ruta, contenido: archivos.__setitem__(ruta, contenido), "respaldo_prueba")
        finally:
            respaldo.TANDAS["escaneos"] = tanda_original
    finally:
        db.devolver_conexion(origen)

    assert manifiesto["cantidades"]["escaneos"] >= 3 and manifiesto["lotes"]["escaneos"] >= 2
    assert manifiesto["cantidades"]["documentos_pdf"] >= 1 and f"respaldo_prueba/documentos/{doc:06d}.pdf" in archivos
    assert archivos[f"respaldo_prueba/documentos/{doc:06d}.pdf"] == pdf
    assert json.loads(archivos["respaldo_prueba/manifiesto.json"])["cantidades"]["marcas_versiones_imagen"] >= 1

    carpeta = tmp_path / "volcado"
    for ruta, contenido in archivos.items():
        destino_arch = tmp_path / "volcado" / ruta.split("/", 1)[1]
        destino_arch.parent.mkdir(parents=True, exist_ok=True)
        destino_arch.write_bytes(contenido)
    datos = leer_respaldo(carpeta)
    assert len(datos["escaneos"]) == manifiesto["cantidades"]["escaneos"]

    servidor = pg.get_server(tmp_path / "destino2")
    try:
        destino = psycopg2.connect(servidor.get_uri(), cursor_factory=RealDictCursor)
        with destino.cursor() as cur:
            cur.execute(_esquema_sin_pgcrypto())
        destino.commit()
        r = restaurar(destino, datos)
        assert r["escaneos"] == manifiesto["cantidades"]["escaneos"] and r["documentos_emitidos"] >= 1
        with destino.cursor() as cur:
            cur.execute("SELECT imagen, length(miniatura) AS m FROM escaneos_imagenes ORDER BY escaneo_id LIMIT 1")
            f = cur.fetchone()
            assert bytes(f["imagen"]) == foto and f["m"] > 10                     # imagen igual; miniatura regenerada
            cur.execute("SELECT respaldo FROM escaneos WHERE respaldo IS NOT NULL")
            assert cur.fetchone()["respaldo"][0]["numero"] == "90000001"
            cur.execute("SELECT escaneo_id FROM marcas WHERE archivo_png = %s", (psycopg2.Binary(b"\x89PNG-uno"),))
            assert cur.fetchone()["escaneo_id"] is not None                        # la marca sigue apuntando a su página
            cur.execute("SELECT contenido FROM documentos_emitidos WHERE id = %s", (doc,))
            assert bytes(cur.fetchone()["contenido"]) == pdf                       # el PDF emitido vuelve byte a byte
            cur.execute("SELECT archivo_png FROM marcas_versiones_imagen")
            assert bytes(cur.fetchone()["archivo_png"]) == b"\x89PNG-viejo"
            cur.execute("SELECT contenido FROM respaldos_operacion")
            assert bytes(cur.fetchone()["contenido"]) == pdf
            cur.execute("INSERT INTO escaneos (carpeta, archivo) VALUES ('x', 'nuevo.jpg') RETURNING id")
            assert cur.fetchone()["id"] > max(e["id"] for e in datos["escaneos"])  # las secuencias siguen desde el máximo
        destino.commit()
        destino.close()
    finally:
        servidor.cleanup()
