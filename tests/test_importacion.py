"""Pruebas de la carga de datos del registro (planilla CSV/Excel -> catálogo).

Es la mitad de la carga que no viene del escaneo: código oficial, propietario,
establecimiento. Se prueba por separado del pipeline de digitalización porque
tiene su propio problema a resolver: la planilla real no tiene un formato
fijo, y no tiene por qué tenerlo.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pytest

from marcas import db
from marcas.importacion import (
    ENCABEZADOS_PLANTILLA,
    ResultadoImportacion,
    importar_datos,
    leer_planilla,
    mapear_columnas,
    normalizar,
    plantilla_csv,
)


# --- normalizar / mapear_columnas --------------------------------------

def test_normalizar_quita_tildes_y_puntuacion():
    assert normalizar("Nº de Marca") == "no de marca"
    assert normalizar("CI / RUC") == "ci ruc"
    assert normalizar("  Código   de  Establecimiento ") == "codigo de establecimiento"
    assert normalizar("Descripción") == "descripcion"


def test_mapear_columnas_reconoce_encabezados_reales():
    mapa, ignoradas = mapear_columnas([
        "Nº de Marca", "Nombre y Apellido / Razón Social", "CI/RUC",
        "Establecimiento", "Cod. Establecimiento", "Peso vivo",
    ])
    assert mapa == {
        0: "codigo", 1: "propietario", 2: "documento",
        3: "establecimiento", 4: "establecimiento_codigo",
    }
    assert ignoradas == ["Peso vivo"]


def test_mapear_columnas_no_asigna_el_mismo_campo_dos_veces():
    """Dos encabezados que caen en el mismo alias: sólo el primero cuenta."""
    mapa, ignoradas = mapear_columnas(["Marca", "Código", "Nombre"])
    assert list(mapa.values()).count("codigo") == 1
    assert ignoradas == ["Código"]


def test_mapear_columnas_ignora_vacios_sin_reportarlos():
    mapa, ignoradas = mapear_columnas(["Codigo", "", "  ", "Nombre"])
    assert ignoradas == []
    assert mapa == {0: "codigo", 3: "propietario"}


# --- leer_planilla -------------------------------------------------------

def test_leer_csv_coma(tmp_path):
    ruta = tmp_path / "r.csv"
    ruta.write_text("codigo,propietario\nM-01,Juan Perez\n", encoding="utf-8")
    encabezados, filas = leer_planilla(ruta)
    assert encabezados == ["codigo", "propietario"]
    assert filas == [["M-01", "Juan Perez"]]


def test_leer_csv_punto_y_coma_en_latin1(tmp_path):
    """Como exporta Excel en español: ';' de separador y acentos en latin-1."""
    ruta = tmp_path / "r.csv"
    ruta.write_bytes(
        "Código;Descripción\nM-01;Ñandú\n".encode("latin-1")
    )
    encabezados, filas = leer_planilla(ruta)
    assert encabezados == ["Código", "Descripción"]
    assert filas == [["M-01", "Ñandú"]]


def test_leer_csv_vacio(tmp_path):
    ruta = tmp_path / "vacio.csv"
    ruta.write_text("", encoding="utf-8")
    assert leer_planilla(ruta) == ([], [])


def test_leer_xlsx(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    ruta = tmp_path / "r.xlsx"
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(["Codigo de Marca", "Propietario", "RUC"])
    hoja.append(["M-04", "ÑU GUAZÚ S.A.", 80083402])   # RUC numérico
    libro.save(ruta)

    encabezados, filas = leer_planilla(ruta)
    assert encabezados == ["Codigo de Marca", "Propietario", "RUC"]
    assert filas[0][0] == "M-04"
    assert filas[0][2] == 80083402                     # openpyxl no lo textifica


def test_leer_planilla_xls_viejo_no_soportado(tmp_path):
    ruta = tmp_path / "viejo.xls"
    ruta.write_bytes(b"no importa el contenido")
    with pytest.raises(ValueError, match="xlsx o .csv"):
        leer_planilla(ruta)


def test_leer_planilla_inexistente(tmp_path):
    with pytest.raises(FileNotFoundError):
        leer_planilla(tmp_path / "no_existe.csv")


# --- importar_datos: alta y actualización de marcas ----------------------

def _csv(tmp_path, nombre, encabezados, filas):
    ruta = tmp_path / nombre
    with open(ruta, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(encabezados)
        w.writerows(filas)
    return ruta


def test_importar_datos_crea_marcas_y_propietario(tmp_path):
    ruta = _csv(tmp_path, "r.csv",
                ["Nº de Marca", "Nombre y Apellido / Razón Social", "CI/RUC",
                 "Establecimiento", "Descripción"],
                [["M-0001", "PIEDRA ALTA S.A.", "80020081", "LA PATRICIA", "Círculo"],
                 ["M-0002", "PIEDRA ALTA S.A.", "80020081", "LA PATRICIA", "Ancla"]])
    ruta_db = tmp_path / "t.db"

    r = importar_datos(ruta, ruta_db)
    assert isinstance(r, ResultadoImportacion)
    assert r.resumen() == {
        "filas_leidas": 2, "marcas_nuevas": 2, "marcas_actualizadas": 0,
        "propietarios_tocados": 1, "omitidas": 0, "columnas_ignoradas": [],
    }

    filas = db.listar_marcas(ruta_db=ruta_db)
    assert {f["codigo"] for f in filas} == {"M-0001", "M-0002"}
    assert all(f["propietario"] == "PIEDRA ALTA S.A." for f in filas)
    assert db.estadisticas(ruta_db)["sin_imagen"] == 2


def test_importar_datos_no_duplica_el_propietario_por_documento(tmp_path):
    """Dos filas, mismo RUC, nombre escrito distinto: un solo propietario."""
    ruta = _csv(tmp_path, "r.csv", ["codigo", "propietario", "documento"], [
        ["M-01", "Piedra Alta S.A. Inmobiliaria", "80020081"],
        ["M-02", "PIEDRA ALTA SA", "80020081"],
    ])
    ruta_db = tmp_path / "t.db"
    r = importar_datos(ruta, ruta_db)
    assert len(r.propietarios) == 1
    assert db.estadisticas(ruta_db)["propietarios"] == 1


def test_reimportar_actualiza_sin_pisar_con_vacios(tmp_path):
    """Una segunda carga con una columna vacía no debe borrar el dato ya cargado."""
    ruta_db = tmp_path / "t.db"
    r1 = _csv(tmp_path, "r1.csv", ["codigo", "descripcion"], [["M-01", "Círculo"]])
    importar_datos(r1, ruta_db)

    r2 = _csv(tmp_path, "r2.csv", ["codigo", "descripcion"], [["M-01", ""]])
    resultado = importar_datos(r2, ruta_db)
    assert resultado.marcas_actualizadas == 1
    assert resultado.marcas_nuevas == 0

    marca = db.obtener_marcas(["M-01"], ruta_db)[0]
    assert marca["descripcion"] == "Círculo", "no se debe borrar con un valor vacío"


def test_importar_datos_completa_propietario_existente(tmp_path):
    """Una carga posterior con más datos del mismo propietario los agrega."""
    ruta_db = tmp_path / "t.db"
    r1 = _csv(tmp_path, "r1.csv", ["codigo", "propietario", "documento"],
              [["M-01", "PIEDRA ALTA S.A.", "80020081"]])
    importar_datos(r1, ruta_db)

    r2 = _csv(tmp_path, "r2.csv",
              ["codigo", "propietario", "documento", "localidad"],
              [["M-02", "PIEDRA ALTA S.A.", "80020081", "Concepción"]])
    importar_datos(r2, ruta_db)

    with db.conectar(ruta_db) as con:
        fila = con.execute(
            "SELECT * FROM propietarios WHERE documento = '80020081'"
        ).fetchone()
    assert fila["localidad"] == "Concepción"
    assert db.estadisticas(ruta_db)["propietarios"] == 1, "sigue siendo un solo propietario"


def test_importar_datos_sin_columna_codigo_falla_claro(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["propietario", "descripcion"],
                [["Juan Perez", "Círculo"]])
    with pytest.raises(ValueError, match="código de marca"):
        importar_datos(ruta, tmp_path / "t.db")


def test_importar_datos_omite_filas_sin_codigo(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "descripcion"],
                [["M-01", "Círculo"], ["", "sin código, se descarta"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.marcas_nuevas == 1
    assert len(r.omitidas) == 1
    assert "fila 3" in r.omitidas[0]


def test_importar_datos_avisa_de_estado_desconocido_pero_no_falla(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "estado"],
                [["M-01", "en tramite"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.marcas_nuevas == 1
    assert any("desconocido" in o for o in r.omitidas)
    marca = db.obtener_marcas(["M-01"], tmp_path / "t.db")[0]
    assert marca["estado"] == "activa", "queda en el valor por defecto del esquema"


def test_importar_datos_reporta_columnas_no_reconocidas(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "peso vivo"], [["M-01", "480"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.columnas_ignoradas == ["peso vivo"]


def test_importar_datos_no_pisa_imagen_ya_cargada(tmp_path):
    """La carga de datos no debe tocar archivo_png de una marca ya digitalizada."""
    ruta_db = tmp_path / "t.db"
    db.inicializar(ruta_db)
    with db.conectar(ruta_db) as con:
        con.execute(
            "INSERT INTO marcas (codigo, archivo_png) VALUES ('M-01', 'x.png')"
        )
    ruta = _csv(tmp_path, "r.csv", ["codigo", "descripcion"], [["M-01", "Círculo"]])
    importar_datos(ruta, ruta_db)
    marca = db.obtener_marcas(["M-01"], ruta_db)[0]
    assert marca["archivo_png"] == "x.png"
    assert marca["descripcion"] == "Círculo"


# --- plantilla_csv ---------------------------------------------------------

def test_plantilla_csv_se_puede_reimportar(tmp_path):
    ruta = plantilla_csv(tmp_path / "plantilla.csv")
    encabezados, filas = leer_planilla(ruta)
    assert encabezados == ENCABEZADOS_PLANTILLA
    assert len(filas) == 2

    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.marcas_nuevas == 2
    assert not r.columnas_ignoradas


# --- tipo, numero_guia y archivo_imagen (salida de un lote de extracción) --

def test_importar_datos_reconoce_tipo_y_numero_guia(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "tipo", "numero_guia"],
                [["M-01", "dominante", "90947260"],
                 ["M-02", "complementaria", "90947260"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert not r.columnas_ignoradas
    filas = {f["codigo"]: f for f in db.listar_marcas(ruta_db=tmp_path / "t.db")}
    assert filas["M-01"]["tipo"] == "dominante"
    assert filas["M-02"]["tipo"] == "complementaria"
    assert filas["M-01"]["numero_guia"] == filas["M-02"]["numero_guia"] == "90947260"


def test_importar_datos_avisa_tipo_desconocido_sin_fallar(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "tipo"], [["M-01", "secundaria"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.marcas_nuevas == 1
    assert any("tipo" in o and "secundaria" in o for o in r.omitidas)
    assert db.obtener_marcas(["M-01"], tmp_path / "t.db")[0]["tipo"] is None


def test_importar_datos_resuelve_imagen_relativa_a_la_planilla(tmp_path):
    """El caso real: un CSV con una carpeta de PNG al lado (lo que entrega un
    lote de extracción), con la ruta escrita relativa al CSV, no al cwd."""
    import cv2
    imagenes = tmp_path / "recortes"
    imagenes.mkdir()
    png = imagenes / "90947260-dominante.png"
    cv2.imwrite(str(png), np.zeros((50, 50, 4), np.uint8))

    ruta = _csv(tmp_path, "registro.csv", ["codigo", "archivo_imagen"],
                [["M-01", "recortes/90947260-dominante.png"]])
    importar_datos(ruta, tmp_path / "t.db")
    marca = db.obtener_marcas(["M-01"], tmp_path / "t.db")[0]
    assert marca["archivo_png"]
    assert Path(marca["archivo_png"]).is_absolute() or (
        Path(marca["archivo_png"]).exists()
    )
    # La ruta guardada, resuelta desde donde corresponda, apunta al PNG real.
    from marcas import config
    guardada = Path(marca["archivo_png"])
    if not guardada.is_absolute():
        guardada = config.RAIZ / guardada
    assert guardada.samefile(png)


def test_importar_datos_avisa_imagen_faltante_sin_fallar_la_fila(tmp_path):
    ruta = _csv(tmp_path, "r.csv", ["codigo", "archivo_imagen"],
                [["M-01", "no_existe/marca.png"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert r.marcas_nuevas == 1
    assert any("no se encontró la imagen" in o for o in r.omitidas)
    assert db.obtener_marcas(["M-01"], tmp_path / "t.db")[0]["archivo_png"] is None


def test_importar_datos_acepta_encabezados_del_brief_de_cowork(tmp_path):
    """Mismos nombres de columna que se le pidieron a Cowork en el prompt de
    extracción: codigo, propietario/vendedor, tipo, numero_guia, archivo_imagen."""
    import cv2
    imagenes = tmp_path / "imgs"
    imagenes.mkdir()
    cv2.imwrite(str(imagenes / "d.png"), np.zeros((40, 40, 4), np.uint8))
    cv2.imwrite(str(imagenes / "c1.png"), np.zeros((40, 40, 4), np.uint8))

    ruta = _csv(tmp_path, "marcas.csv",
                ["codigo", "vendedor_nombre", "vendedor_documento", "tipo",
                 "numero_guia", "archivo_imagen"],
                [["90947260-D", "MIRCO XANDER KLASSEN TOEWS", "5355841",
                  "dominante", "90947260", "imgs/d.png"],
                 ["90947260-C1", "MIRCO XANDER KLASSEN TOEWS", "5355841",
                  "complementaria", "90947260", "imgs/c1.png"]])
    r = importar_datos(ruta, tmp_path / "t.db")
    assert not r.columnas_ignoradas
    assert r.marcas_nuevas == 2
    assert len(r.propietarios) == 1, "las dos marcas deben quedar bajo el mismo vendedor"
    filas = db.listar_marcas(ruta_db=tmp_path / "t.db")
    assert all(f["propietario"] == "MIRCO XANDER KLASSEN TOEWS" for f in filas)
    assert all(f["numero_guia"] == "90947260" for f in filas)
    assert all(f["archivo_png"] for f in filas)
