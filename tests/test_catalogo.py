"""Pruebas del catálogo, los PDF y el selector web."""

import csv

import numpy as np
import pytest

from marcas import db
from marcas.pdf import (
    ItemPlanilla, generar_hoja_control, generar_planilla, generar_plantilla_captura
)
from marcas.web.app import crear_app

CAMPOS = [
    "codigo", "hoja", "fila", "columna", "png", "svg", "ancho_px", "alto_px",
    "aspecto", "tinta_pct", "componentes", "sha1", "revisar", "observaciones",
]


def _png_falso(ruta):
    import cv2
    img = np.zeros((256, 256, 4), np.uint8)
    cv2.circle(img, (128, 128), 90, (0, 0, 0, 255), 10)
    cv2.imwrite(str(ruta), img)
    return ruta


@pytest.fixture
def catalogo(tmp_path):
    """Base con tres marcas importadas desde un manifiesto."""
    png_dir = tmp_path / "png"
    png_dir.mkdir()
    manifiesto = tmp_path / "manifiesto.csv"
    with manifiesto.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS)
        w.writeheader()
        for i in range(1, 4):
            codigo = f"H01-F01C{i:02d}"
            w.writerow({
                "codigo": codigo, "hoja": "h01", "fila": 1, "columna": i,
                "png": str(_png_falso(png_dir / f"{codigo}.png")), "svg": "",
                "ancho_px": 200, "alto_px": 210, "aspecto": 0.95,
                "tinta_pct": 9.5, "componentes": 1, "sha1": f"sha{i}",
                "revisar": i == 3, "observaciones": "trazo débil" if i == 3 else "",
            })
        # Casilla vacía: no debe entrar al catálogo.
        w.writerow({c: "" for c in CAMPOS} | {
            "codigo": "H01-F01C04", "hoja": "h01", "fila": 1, "columna": 4,
            "ancho_px": 0, "alto_px": 0, "aspecto": 0, "tinta_pct": 0,
            "componentes": 0, "revisar": True,
        })
    ruta_db = tmp_path / "marcas.db"
    stats = db.importar_manifiesto(manifiesto, ruta_db)
    return ruta_db, manifiesto, stats


def test_importar_manifiesto(catalogo):
    _, _, stats = catalogo
    assert stats == {"nuevas": 3, "actualizadas": 0, "omitidas": 1}


def test_reimportar_no_duplica(catalogo):
    ruta_db, manifiesto, _ = catalogo
    stats = db.importar_manifiesto(manifiesto, ruta_db)
    assert stats["nuevas"] == 0 and stats["actualizadas"] == 3
    assert db.estadisticas(ruta_db)["marcas"] == 3


def test_marca_con_observaciones_queda_para_revisar(catalogo):
    ruta_db, _, _ = catalogo
    assert db.estadisticas(ruta_db)["por_estado"] == {"activa": 2, "revisar": 1}
    assert [m["codigo"] for m in db.listar_marcas(estado="revisar", ruta_db=ruta_db)] \
        == ["H01-F01C03"]


def test_reimportar_conserva_los_datos_cargados_a_mano(catalogo):
    ruta_db, manifiesto, _ = catalogo
    pid = db.alta_propietario("Estancia La Blanca", ruta_db, localidad="Durazno")
    db.actualizar_marca("H01-F01C01", ruta_db, descripcion="Círculo con barra")
    with db.conectar(ruta_db) as con:
        con.execute("UPDATE marcas SET propietario_id=? WHERE codigo=?",
                    (pid, "H01-F01C01"))

    db.importar_manifiesto(manifiesto, ruta_db)

    marca = db.obtener_marcas(["H01-F01C01"], ruta_db)[0]
    assert marca["descripcion"] == "Círculo con barra"
    assert marca["propietario"] == "Estancia La Blanca"


def test_obtener_marcas_respeta_el_orden_pedido(catalogo):
    ruta_db, _, _ = catalogo
    pedidos = ["H01-F01C03", "H01-F01C01", "INEXISTENTE"]
    assert [m["codigo"] for m in db.obtener_marcas(pedidos, ruta_db)] == pedidos[:2]


def test_busqueda(catalogo):
    ruta_db, _, _ = catalogo
    db.actualizar_marca("H01-F01C02", ruta_db, descripcion="ancla")
    assert len(db.listar_marcas(busqueda="ancla", ruta_db=ruta_db)) == 1
    assert len(db.listar_marcas(busqueda="F01C", ruta_db=ruta_db)) == 3


def test_guardar_planilla(catalogo):
    ruta_db, _, _ = catalogo
    pid = db.guardar_planilla("Rodeo 2026", ["H01-F01C02", "H01-F01C01"],
                              ruta_db=ruta_db)
    with db.conectar(ruta_db) as con:
        items = con.execute(
            "SELECT posicion FROM planilla_items WHERE planilla_id=? ORDER BY posicion",
            (pid,),
        ).fetchall()
    assert [i["posicion"] for i in items] == [1, 2]


def _paginas(pdf):
    import pypdfium2 as pdfium
    return len(pdfium.PdfDocument(str(pdf)))


def test_planilla_pdf_pagina_por_casillas(catalogo, tmp_path):
    ruta_db, _, _ = catalogo
    items = [ItemPlanilla.desde_fila(f) for f in db.listar_marcas(ruta_db=ruta_db)]
    salida = generar_planilla(items * 4, tmp_path / "p.pdf", columnas=2, filas=3)
    assert salida.read_bytes().startswith(b"%PDF")
    assert _paginas(salida) == 2      # 12 marcas en casilleros de 6


def test_planilla_admite_casillas_vacias(catalogo, tmp_path):
    ruta_db, _, _ = catalogo
    items = [ItemPlanilla.desde_fila(f) for f in db.listar_marcas(ruta_db=ruta_db)]
    salida = generar_planilla(items, tmp_path / "p.pdf", columnas=2, filas=2,
                              casillas_vacias=5)
    assert _paginas(salida) == 2


def test_hoja_control_y_plantilla_de_captura(catalogo, tmp_path):
    ruta_db, _, _ = catalogo
    items = [ItemPlanilla.desde_fila(f) for f in db.listar_marcas(ruta_db=ruta_db)]
    assert generar_hoja_control(items, tmp_path / "c.pdf").exists()
    plantilla = generar_plantilla_captura(tmp_path / "t.pdf", hojas=3)
    assert _paginas(plantilla) == 3


def test_planilla_tolera_imagen_faltante(catalogo, tmp_path):
    """Si el PNG se borró, el PDF sale igual con la casilla señalada."""
    ruta_db, _, _ = catalogo
    fila = db.listar_marcas(ruta_db=ruta_db)[0]
    item = ItemPlanilla.desde_fila(fila)
    item.imagen = tmp_path / "no_existe.png"
    assert generar_planilla([item], tmp_path / "p.pdf").exists()


@pytest.fixture
def cliente(catalogo):
    ruta_db, _, _ = catalogo
    app = crear_app(ruta_db)
    app.config.update(TESTING=True)
    return app.test_client()


def test_web_lista_las_marcas(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert b"H01-F01C01" in r.data


def test_web_filtra_por_estado(cliente):
    r = cliente.get("/?estado=revisar")
    assert b"H01-F01C03" in r.data and b"H01-F01C01" not in r.data


def test_web_sirve_la_imagen(cliente):
    assert cliente.get("/imagen/H01-F01C01.png").status_code == 200
    assert cliente.get("/imagen/NO-EXISTE.png").status_code == 404


def test_web_genera_el_pdf(cliente):
    r = cliente.post("/planilla", data={"marca": ["H01-F01C01", "H01-F01C02"],
                                        "titulo": "Rodeo"})
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert r.data.startswith(b"%PDF")


def test_web_sin_seleccion_vuelve_al_inicio(cliente):
    assert cliente.post("/planilla", data={}).status_code == 302


def test_web_edita_una_marca(cliente, catalogo):
    ruta_db, _, _ = catalogo
    cliente.post("/marca/H01-F01C02", data={"descripcion": "V corta", "estado": "baja"})
    marca = db.obtener_marcas(["H01-F01C02"], ruta_db)[0]
    assert marca["descripcion"] == "V corta" and marca["estado"] == "baja"


def test_web_estampa_la_guia_subida(cliente, catalogo, tmp_path):
    """La guía oficial se sube en cada trámite; el sistema no guarda plantillas."""
    import io
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "scripts"))
    from generar_guia_demo import generar as generar_guia

    guia = generar_guia(tmp_path / "guia.pdf")
    r = cliente.post(
        "/guia",
        data={
            "marca": ["H01-F01C01", "H01-F01C02"],
            "guia_pdf": (io.BytesIO(guia.read_bytes()), "GE291181750.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "application/pdf"
    assert "GE291181750_con_marcas.pdf" in r.headers["Content-Disposition"]
    assert r.data.startswith(b"%PDF")


def test_web_guia_sin_archivo_vuelve_al_inicio(cliente):
    assert cliente.post("/guia", data={"marca": ["H01-F01C01"]}).status_code == 302


def test_actualizar_marca_permite_renombrar_el_codigo(catalogo):
    """Pasar 'codigo' en **campos renombra la fila sin chocar con el parámetro posicional.

    Regresión: el primer parámetro de actualizar_marca identificaba la fila y
    se llamaba 'codigo', igual que la clave usada para renombrar. Python no
    permite que un mismo nombre llegue a la vez posicional y por **kwargs.
    """
    ruta_db, _, _ = catalogo
    n = db.actualizar_marca("H01-F01C01", ruta_db, codigo="H01-NUEVO",
                            descripcion="renombrada")
    assert n == 1
    assert db.obtener_marcas(["H01-F01C01"], ruta_db) == []
    marca = db.obtener_marcas(["H01-NUEVO"], ruta_db)[0]
    assert marca["descripcion"] == "renombrada"


def test_web_edita_una_marca_cambiando_el_codigo(cliente, catalogo):
    """El formulario de edición de la ficha permite pasar del código automático
    de la digitalización al código oficial del registro."""
    ruta_db, _, _ = catalogo
    r = cliente.post("/marca/H01-F01C01", data={
        "codigo": "M-0001", "descripcion": "Círculo con barra", "estado": "activa",
    })
    assert r.status_code == 302
    assert db.obtener_marcas(["H01-F01C01"], ruta_db) == []
    marca = db.obtener_marcas(["M-0001"], ruta_db)[0]
    assert marca["descripcion"] == "Círculo con barra"


def test_web_edita_una_marca_sin_cambiar_el_codigo(cliente, catalogo):
    """El caso normal (sin tocar el código) sigue funcionando igual que antes."""
    ruta_db, _, _ = catalogo
    r = cliente.post("/marca/H01-F01C01", data={
        "codigo": "H01-F01C01", "descripcion": "Otra desc", "estado": "revisar",
    })
    assert r.status_code == 302
    marca = db.obtener_marcas(["H01-F01C01"], ruta_db)[0]
    assert marca["descripcion"] == "Otra desc" and marca["estado"] == "revisar"
