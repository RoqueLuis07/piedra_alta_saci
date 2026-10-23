"""Archivo de documentos emitidos: contra un Postgres real (embebido).

Se salta solo si no está instalado ``pixeltable-pgserver`` (requirements-dev).
Cubre el flujo completo: login -> crear guía -> agregar marca con imagen ->
generar el PDF -> queda guardado y se puede ver/descargar desde /archivo.
"""

import io
import json
import re
import sys
from pathlib import Path

import pytest

pg = pytest.importorskip("pixeltable_pgserver")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from generar_guia_demo import generar as generar_guia  # noqa: E402


def _png_marca() -> bytes:
    from PIL import Image, ImageDraw

    imagen = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    ImageDraw.Draw(imagen).line([(40, 200), (128, 40), (216, 200)], fill=(0, 0, 0, 255), width=14)
    buffer = io.BytesIO()
    imagen.save(buffer, format="PNG")
    return buffer.getvalue()


def _crear_guia(cliente, numero: str) -> int:
    r = cliente.post(
        "/guias/nueva",
        data={"numero_guia": numero, "fecha": "20/10/2026", "vendedor_nombre": "Vendedor Prueba",
              "comprador_nombre": "Comprador Prueba", "cantidad_animales": "12"},
    )
    assert r.status_code == 302
    return int(re.search(r"/guias/(\d+)", r.headers["Location"]).group(1))


def _agregar_marca(cliente, operacion_id: int, con_imagen: bool) -> int:
    datos = {"tipo": "complementaria", "descripcion": "marca de prueba"}
    if con_imagen:
        datos["imagen"] = (io.BytesIO(_png_marca()), "marca.png")
    r = cliente.post(f"/guias/{operacion_id}/marcas", data=datos, content_type="multipart/form-data")
    assert r.status_code == 302
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT max(id) AS id FROM marcas WHERE operacion_id = %s", (operacion_id,))
            return cur.fetchone()["id"]
    finally:
        db.devolver_conexion(conexion)


def _pedido(pdf_bytes, dibujos=0, catalogo=(), dominante=False, extra=None):
    """Arma el formulario multipart de /emitir/*."""
    datos = {"pdf_guia": (io.BytesIO(pdf_bytes), "guia.pdf")}
    orden = [{"t": "cat", "codigo": c} for c in catalogo]
    for i in range(dibujos):
        datos[f"dibujo_{i}"] = (io.BytesIO(_png_marca()), f"dibujo_{i}.png")
        orden.append({"t": "dib", "archivo": f"dibujo_{i}", "guardar": i == 0})
    datos["orden"] = json.dumps(orden)
    if dominante:
        datos["dominante_png"] = (io.BytesIO(_png_marca()), "dominante.png")
        datos["dominante"] = json.dumps({"t": "dib", "archivo": "dominante_png", "guardar": False})
    datos.update(extra or {})
    return datos


def _conteo(sql, *params):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]
    finally:
        db.devolver_conexion(conexion)


def test_emitir_dibujando_crea_operacion_marcas_y_archivo(cliente, tmp_path):
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0001", incluir_rubro2=True).read_bytes()
    r = cliente.post(
        "/emitir/generar",
        data=_pedido(pdf, dibujos=2, dominante=True, extra={
            "tipo_operacion": "compra", "numero_guia": "ARCH-0001", "vendedor_nombre": "Vendedor Prueba",
            "comprador_nombre": "Comprador Prueba", "cantidad_animales": "12"}),
        content_type="multipart/form-data",
    )
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert r.mimetype == "application/pdf"
    emitido = r.get_data()
    assert emitido.startswith(b"%PDF")

    # operación y marcas creadas; sólo la primera dibujada se guardó en el catálogo
    assert _conteo("SELECT count(*) AS n FROM operaciones WHERE numero_guia = %s", "ARCH-0001") == 1
    assert _conteo("SELECT count(*) AS n FROM marcas m JOIN operaciones o ON o.id = m.operacion_id "
                   "WHERE o.numero_guia = %s", "ARCH-0001") == 3
    assert _conteo("SELECT count(*) AS n FROM marcas m JOIN operaciones o ON o.id = m.operacion_id "
                   "WHERE o.numero_guia = %s AND m.activo", "ARCH-0001") == 1

    lista = cliente.get("/archivo?q=ARCH-0001").get_data(as_text=True)
    assert "Vendedor Prueba" in lista and "Comprador Prueba" in lista
    documento_id = int(re.search(r"/archivo/(\d+)/pdf", lista).group(1))
    detalle = cliente.get(f"/archivo/{documento_id}").get_data(as_text=True)
    assert "Vendedor Prueba" in detalle and "Dibujada al emitir el PDF" in detalle
    assert cliente.get(f"/archivo/{documento_id}/pdf").get_data() == emitido
    assert "/pdf" not in cliente.get("/archivo?desde=2999-01-01").get_data(as_text=True)


def test_emitir_con_marca_del_catalogo_en_operacion_existente(cliente, tmp_path):
    operacion_id = _crear_guia(cliente, "ARCH-0003")
    marca_id = _agregar_marca(cliente, operacion_id, con_imagen=True)
    codigo = _conteo("SELECT 1 AS n") and None
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT codigo FROM marcas WHERE id = %s", (marca_id,))
        codigo = cur.fetchone()["codigo"]
    db.devolver_conexion(conexion)

    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0003", incluir_rubro2=True).read_bytes()
    antes = _conteo("SELECT count(*) AS n FROM marcas")
    r = cliente.post(
        "/emitir/generar",
        data=_pedido(pdf, catalogo=[codigo], extra={"operacion_id": str(operacion_id)}),
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert _conteo("SELECT count(*) AS n FROM marcas") == antes  # no se crean marcas nuevas
    assert _conteo("SELECT count(*) AS n FROM documentos_emitidos WHERE operacion_id = %s", operacion_id) == 1


def test_previsualizar_no_guarda_nada(cliente, tmp_path):
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0004", incluir_rubro2=True).read_bytes()
    antes = (_conteo("SELECT count(*) AS n FROM marcas"), _conteo("SELECT count(*) AS n FROM operaciones"))
    r = cliente.post("/emitir/previsualizar", data=_pedido(pdf, dibujos=1), content_type="multipart/form-data")
    assert r.status_code == 200
    cuerpo = r.get_json()
    assert cuerpo["paginas"] and cuerpo["cupo"]["complementarias_libres_rubro2"] > 0
    assert (_conteo("SELECT count(*) AS n FROM marcas"), _conteo("SELECT count(*) AS n FROM operaciones")) == antes


def test_marca_sin_imagen_no_genera_ni_archiva(cliente, tmp_path):
    operacion_id = _crear_guia(cliente, "ARCH-0002")
    marca_id = _agregar_marca(cliente, operacion_id, con_imagen=False)
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("SELECT codigo FROM marcas WHERE id = %s", (marca_id,))
        codigo = cur.fetchone()["codigo"]
    db.devolver_conexion(conexion)
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0002", incluir_rubro2=True).read_bytes()
    r = cliente.post(
        "/emitir/generar",
        data=_pedido(pdf, catalogo=[codigo], extra={"operacion_id": str(operacion_id)}),
        content_type="multipart/form-data",
    )
    assert r.status_code == 400
    assert "imagen" in r.get_json()["error"]
    assert _conteo("SELECT count(*) AS n FROM documentos_emitidos WHERE operacion_id = %s", operacion_id) == 0


def test_emitir_sin_marcas_o_sin_pdf_se_rechaza(cliente, tmp_path):
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0005", incluir_rubro2=True).read_bytes()
    r = cliente.post("/emitir/generar", data=_pedido(pdf, extra={"tipo_operacion": "compra", "numero_guia": "X"}),
                     content_type="multipart/form-data")
    assert r.status_code == 400 and "marca" in r.get_json()["error"]
    r = cliente.post("/emitir/generar", data={"orden": "[]"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "PDF" in r.get_json()["error"]
    assert _conteo("SELECT count(*) AS n FROM operaciones WHERE numero_guia = %s", "ARCH-0005") == 0


def test_pantalla_emitir_y_rutas_eliminadas(cliente):
    assert cliente.get("/").status_code == 302
    pagina = cliente.get("/emitir").get_data(as_text=True)
    assert "Emitir PDF" in pagina and "lienzo" in pagina
    for ruta in ("/estadisticas", "/aprobaciones"):
        assert cliente.get(ruta).status_code == 404


def _marca_con_estado(estado):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute(
                "INSERT INTO marcas (tipo, estado, motivo_calidad, descripcion) "
                "VALUES ('complementaria', %s, 'trazo_muy_chico', 'para revisar') RETURNING id, codigo",
                (estado,),
            )
            fila = cur.fetchone()
        conexion.commit()
        return fila["id"], fila["codigo"]
    finally:
        db.devolver_conexion(conexion)


def _situacion(marca_id):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        with conexion.cursor() as cur:
            cur.execute("SELECT estado, activo FROM marcas WHERE id = %s", (marca_id,))
            fila = cur.fetchone()
            return fila["estado"], fila["activo"]
    finally:
        db.devolver_conexion(conexion)


def test_revision_lista_y_acciones_rapidas(cliente):
    marca_id, codigo = _marca_con_estado("revisar")
    pagina = cliente.get("/revision").get_data(as_text=True)
    assert codigo in pagina and "trazo muy chico" in pagina

    url = f"/marcas/{marca_id}/revision"
    r = cliente.post(url, json={"accion": "aprobar"})
    assert r.status_code == 200 and r.get_json()["estado"] == "activa"
    assert _situacion(marca_id) == ("activa", True)
    assert codigo not in cliente.get("/revision").get_data(as_text=True)  # ya no está pendiente
    assert codigo in cliente.get("/revision?ver=todas").get_data(as_text=True)

    r = cliente.post(url, json={"accion": "baja"})
    assert r.get_json()["anterior"] == {"estado": "activa", "activo": True}
    assert _situacion(marca_id) == ("baja", True)

    r = cliente.post(url, json={"accion": "ocultar"})
    assert _situacion(marca_id) == ("baja", False)

    # deshacer vuelve exactamente al estado anterior
    r = cliente.post(url, json={"accion": "deshacer", "estado": "revisar", "activo": True})
    assert r.status_code == 200 and _situacion(marca_id) == ("revisar", True)


def test_revision_rechaza_pedidos_invalidos(cliente):
    marca_id, _ = _marca_con_estado("revisar")
    url = f"/marcas/{marca_id}/revision"
    assert cliente.post(url, json={"accion": "borrar"}).status_code == 400
    assert cliente.post(url, json={"accion": "deshacer", "estado": "inventado", "activo": True}).status_code == 400
    assert cliente.post("/marcas/99999999/revision", json={"accion": "aprobar"}).status_code == 404
    assert _situacion(marca_id) == ("revisar", True)


def test_marca_del_catalogo_como_dominante_queda_archivada_con_su_papel(cliente, tmp_path):
    operacion_id = _crear_guia(cliente, "ARCH-0006")
    marca_id = _agregar_marca(cliente, operacion_id, con_imagen=True)
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("UPDATE marcas SET tipo = 'dominante' WHERE id = %s RETURNING codigo", (marca_id,))
        codigo = cur.fetchone()["codigo"]
    conexion.commit()
    db.devolver_conexion(conexion)

    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0006", incluir_rubro2=True, dominante_ocupada=False).read_bytes()
    datos = _pedido(pdf, catalogo=[codigo], extra={"operacion_id": str(operacion_id)})
    datos["dominante"] = json.dumps({"t": "cat", "codigo": codigo})
    r = cliente.post("/emitir/generar", data=datos, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    roles = _conteo(
        "SELECT count(*) AS n FROM documentos_emitidos d, jsonb_array_elements(d.marcas) m "
        "WHERE d.operacion_id = %s AND m->>'tipo' = 'dominante'", operacion_id)
    assert roles == 1  # sólo la que se estampó como dominante; la otra va de complementaria


def test_revision_filtra_por_motivo_y_decide_en_lote(cliente):
    from marcas.servidor import db

    ids = []
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        for motivo in ("no_es_marca:anulacion (95 % recta)", "no_es_marca:anulacion (88 % recta)", "no_es_marca:texto_o_sello (5 letras)"):
            cur.execute(
                "INSERT INTO marcas (tipo, estado, motivo_calidad, sospechosa_calidad) "
                "VALUES ('complementaria', 'revisar', %s, true) RETURNING id, codigo", (motivo,))
            ids.append(cur.fetchone())
    conexion.commit()
    db.devolver_conexion(conexion)

    pagina = cliente.get("/revision?motivo=no_es_marca:anulacion").get_data(as_text=True)
    assert ids[0]["codigo"] in pagina and ids[1]["codigo"] in pagina and ids[2]["codigo"] not in pagina
    assert "X y rayas de anulación" in cliente.get("/revision").get_data(as_text=True)  # opciones de motivo

    lote_ids = [ids[0]["id"], ids[1]["id"]]
    r = cliente.post("/revision/lote", json={"accion": "baja", "ids": lote_ids})
    assert r.status_code == 200 and r.get_json()["cantidad"] == 2
    assert _situacion(lote_ids[0]) == ("baja", True) and _situacion(lote_ids[1]) == ("baja", True)
    assert _situacion(ids[2]["id"]) == ("revisar", True)  # las demás no se tocan

    anteriores = r.get_json()["anteriores"]
    assert {a["estado"] for a in anteriores} == {"revisar"}
    r = cliente.post("/revision/lote", json={"accion": "restaurar", "items": anteriores})
    assert r.status_code == 200
    assert _situacion(lote_ids[0]) == ("revisar", True) and _situacion(lote_ids[1]) == ("revisar", True)

    # pedidos inválidos
    assert cliente.post("/revision/lote", json={"accion": "baja", "ids": []}).status_code == 400
    assert cliente.post("/revision/lote", json={"accion": "baja", "ids": ["1; DROP TABLE marcas"]}).status_code == 400
    assert cliente.post("/revision/lote", json={"accion": "restaurar", "items": [{"id": 1, "estado": "x", "activo": True}]}).status_code == 400
    assert cliente.post("/revision/lote", json={"accion": "ocultar", "ids": [1]}).status_code == 400


def test_emitir_no_duplica_la_operacion_ni_deja_estampar_una_marca_de_baja(cliente, tmp_path):
    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0007", incluir_rubro2=True).read_bytes()
    extra = {"tipo_operacion": "compra", "numero_guia": "ARCH-0007", "vendedor_nombre": "V", "comprador_nombre": "C"}
    for _ in range(2):  # emitir dos veces la misma guía
        r = cliente.post("/emitir/generar", data=_pedido(pdf, dibujos=1, extra=extra), content_type="multipart/form-data")
        assert r.status_code == 200
    assert _conteo("SELECT count(*) AS n FROM operaciones WHERE numero_guia = %s", "ARCH-0007") == 1
    assert _conteo("SELECT count(*) AS n FROM documentos_emitidos WHERE numero_guia = %s", "ARCH-0007") == 2

    _, codigo = _marca_con_estado("baja")
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute("UPDATE marcas SET archivo_png = %s WHERE codigo = %s", (_png_marca(), codigo))
    conexion.commit()
    db.devolver_conexion(conexion)
    r = cliente.post("/emitir/generar", data=_pedido(pdf, catalogo=[codigo], extra=extra), content_type="multipart/form-data")
    assert r.status_code == 400 and "baja" in r.get_json()["error"]
    assert cliente.get(f"/marcas/buscar.json?q={codigo}").get_json() == []  # el buscador tampoco la ofrece


def test_respaldo_incluye_los_datos_del_archivo_sin_los_pdf(cliente):
    from marcas.servidor import db
    from marcas.servidor.consultas import exportar_todo_para_respaldo

    conexion = db.obtener_conexion()
    try:
        datos = exportar_todo_para_respaldo(conexion)
    finally:
        db.devolver_conexion(conexion)
    assert datos["documentos_emitidos"]
    primero = datos["documentos_emitidos"][0]
    assert "contenido" not in primero and primero["bytes_pdf"] > 1000


def test_marca_oculta_solo_se_reutiliza_en_su_propia_operacion(cliente, tmp_path):
    from marcas.servidor import db

    pdf = generar_guia(tmp_path / "entrada.pdf", guia="ARCH-0008", incluir_rubro2=True).read_bytes()
    extra = {"tipo_operacion": "compra", "numero_guia": "ARCH-0008", "vendedor_nombre": "V", "comprador_nombre": "C"}
    r = cliente.post("/emitir/generar", data=_pedido(pdf, dibujos=2, extra=extra), content_type="multipart/form-data")
    assert r.status_code == 200
    # de las 2 dibujadas, sólo la primera se guardó en el catálogo; la otra quedó oculta
    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute(
            "SELECT m.codigo, m.operacion_id FROM marcas m JOIN operaciones o ON o.id = m.operacion_id "
            "WHERE o.numero_guia = 'ARCH-0008' AND NOT m.activo")
        oculta = cur.fetchone()
    db.devolver_conexion(conexion)

    # en otra operación: rechazada
    r = cliente.post("/emitir/generar", data=_pedido(pdf, catalogo=[oculta["codigo"]], extra={**extra, "numero_guia": "ARCH-0009"}),
                     content_type="multipart/form-data")
    assert r.status_code == 400 and "oculta" in r.get_json()["error"]
    # en su propia operación: permitida, y la pantalla la precarga
    r = cliente.post("/emitir/generar", data=_pedido(pdf, catalogo=[oculta["codigo"]], extra={"operacion_id": str(oculta["operacion_id"])}),
                     content_type="multipart/form-data")
    assert r.status_code == 200
    assert oculta["codigo"] in cliente.get(f"/emitir?operacion={oculta['operacion_id']}").get_data(as_text=True)


def test_cartel_de_entorno_de_demo(cliente, monkeypatch):
    assert "ENTORNO DE DEMOSTRACIÓN" not in cliente.get("/emitir").get_data(as_text=True)
    monkeypatch.setenv("ENTORNO_DEMO", "1")
    assert "ENTORNO DE DEMOSTRACIÓN" in cliente.get("/emitir").get_data(as_text=True)


def test_usuarios_de_demo_se_crean_una_sola_vez(cliente):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from crear_usuarios_demo import crear
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    try:
        primera = crear(conexion)
        assert [r[1] for r in primera] == ["administrador", "operador", "consulta"]
        assert all(r[2] and len(r[2]) >= 10 for r in primera)           # contraseña aleatoria
        assert all(r[2] is None for r in crear(conexion))                # la segunda vez no cambia nada
    finally:
        db.devolver_conexion(conexion)


def test_revision_muestra_las_bajas_automaticas_para_recuperar_las_buenas(cliente):
    from marcas.servidor import db

    conexion = db.obtener_conexion()
    with conexion.cursor() as cur:
        cur.execute(
            "INSERT INTO marcas (tipo, estado, motivo_calidad, sospechosa_calidad) "
            "VALUES ('complementaria', 'baja', 'no_es_marca:anulacion (91 % recta)', true) RETURNING id, codigo"
        )
        fila = cur.fetchone()
    conexion.commit()
    db.devolver_conexion(conexion)

    pagina = cliente.get("/revision?ver=bajas").get_data(as_text=True)
    assert fila["codigo"] in pagina and "POR EL DETECTOR" in pagina
    assert fila["codigo"] not in cliente.get("/revision").get_data(as_text=True)        # no está en «por revisar»
    r = cliente.post(f"/marcas/{fila['id']}/revision", json={"accion": "aprobar"})      # recuperada: era buena
    assert r.status_code == 200 and _situacion(fila["id"]) == ("activa", True)
    assert fila["codigo"] not in cliente.get("/revision?ver=bajas").get_data(as_text=True)
