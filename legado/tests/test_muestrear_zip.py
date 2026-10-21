"""Pruebas del muestreador de ZIP grandes (formularios escaneados en lote).

El caso real: un ZIP de varios GB con cientos de documentos, del que sólo
hace falta compartir un puñado representativo. `zipfile` permite listar y
extraer miembros de a uno, así que nunca se descomprime el archivo entero.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from muestrear_zip import (  # noqa: E402
    _elegir_muestra, _es_relevante, extraer_muestra, extraer_por_nombre, listar,
)


def _zip_con_carpetas(ruta: Path, por_carpeta: int = 8) -> Path:
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for carpeta in ("2024", "2025"):
            for i in range(por_carpeta):
                z.writestr(f"{carpeta}/GE{carpeta}{i:03d}.pdf", b"x" * (100 + i))
        z.writestr("__MACOSX/._GE2024000.pdf", b"basura")
        z.writestr(".DS_Store", b"basura")
    return ruta


def _zip_plano(ruta: Path, cantidad: int = 10) -> Path:
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(cantidad):
            z.writestr(f"GE{i:03d}.pdf", b"x" * (100 + i))
    return ruta


# --- filtrado de basura de sistema ----------------------------------------

def test_es_relevante_descarta_metadata_de_sistema():
    assert not _es_relevante("__MACOSX/._archivo.pdf")
    assert not _es_relevante(".DS_Store")
    assert not _es_relevante("carpeta/")            # entrada de directorio
    assert _es_relevante("2024/GE100.pdf")


def test_listar_ignora_metadata_de_sistema(tmp_path, capsys):
    ruta = _zip_con_carpetas(tmp_path / "f.zip")
    listar(ruta, mostrar=100)
    salida = capsys.readouterr().out
    assert "Archivos            : 16" in salida
    assert "__MACOSX" not in salida
    assert "DS_Store" not in salida


def test_listar_no_extrae_nada(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip")
    antes = set(tmp_path.iterdir())
    listar(ruta, mostrar=5)
    assert set(tmp_path.iterdir()) == antes


# --- muestreo al azar, reparto y reproducibilidad -------------------------

def test_muestra_reparte_entre_carpetas(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip", por_carpeta=8)
    escritos = extraer_muestra(ruta, cantidad=6, semilla=1, salida=tmp_path / "m")
    assert len(escritos) == 6
    de_2024 = sum(1 for p in escritos if p.name.startswith("2024__"))
    de_2025 = sum(1 for p in escritos if p.name.startswith("2025__"))
    assert de_2024 == 3 and de_2025 == 3


def test_muestra_es_reproducible_con_la_misma_semilla(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip")
    a = extraer_muestra(ruta, 6, semilla=42, salida=tmp_path / "a")
    b = extraer_muestra(ruta, 6, semilla=42, salida=tmp_path / "b")
    assert sorted(p.name for p in a) == sorted(p.name for p in b)


def test_semillas_distintas_pueden_dar_muestras_distintas(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip", por_carpeta=20)
    a = extraer_muestra(ruta, 6, semilla=1, salida=tmp_path / "a")
    b = extraer_muestra(ruta, 6, semilla=2, salida=tmp_path / "b")
    assert sorted(p.name for p in a) != sorted(p.name for p in b)


def test_muestra_funciona_con_zip_sin_subcarpetas(tmp_path):
    ruta = _zip_plano(tmp_path / "f.zip", cantidad=10)
    escritos = extraer_muestra(ruta, cantidad=4, semilla=0, salida=tmp_path / "m")
    assert len(escritos) == 4
    assert len({p.name for p in escritos}) == 4          # sin repetidos


def test_pedir_mas_de_las_que_hay_no_falla(tmp_path):
    ruta = _zip_plano(tmp_path / "f.zip", cantidad=5)
    escritos = extraer_muestra(ruta, cantidad=999, semilla=0, salida=tmp_path / "m")
    assert len(escritos) == 5


def test_zip_vacio_de_documentos_avisa_con_claridad(tmp_path):
    ruta = tmp_path / "f.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("__MACOSX/._x", b"basura")
    with pytest.raises(ValueError, match="no tiene archivos"):
        extraer_muestra(ruta, 5, 0, tmp_path / "m")


def test_extraidos_conservan_el_contenido_original(tmp_path):
    ruta = tmp_path / "f.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("a/uno.pdf", b"contenido-uno")
    (escrito,) = extraer_muestra(ruta, 1, 0, tmp_path / "m")
    assert escrito.read_bytes() == b"contenido-uno"


def test_nombres_repetidos_en_carpetas_distintas_no_se_pisan(tmp_path):
    """Dos archivos que se llaman igual en carpetas distintas del ZIP deben
    quedar como dos archivos separados al aplanar a una sola carpeta destino."""
    ruta = tmp_path / "f.zip"
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("2024/formulario.pdf", b"del 2024")
        z.writestr("2025/formulario.pdf", b"del 2025")
    escritos = extraer_muestra(ruta, 2, 0, tmp_path / "m")
    assert len(escritos) == 2
    assert len({p.name for p in escritos}) == 2
    contenidos = {p.read_bytes() for p in escritos}
    assert contenidos == {b"del 2024", b"del 2025"}


# --- extracción por nombre/patrón ------------------------------------------

def test_extraer_por_nombre_exacto(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip")
    escritos = extraer_por_nombre(ruta, ["GE2024000.pdf"], tmp_path / "m")
    assert len(escritos) == 1
    assert escritos[0].name == "2024__GE2024000.pdf"


def test_extraer_por_patron_con_comodin(tmp_path):
    ruta = _zip_con_carpetas(tmp_path / "f.zip", por_carpeta=3)
    escritos = extraer_por_nombre(ruta, ["GE2024*"], tmp_path / "m")
    assert len(escritos) == 3
    assert all(p.name.startswith("2024__") for p in escritos)


def test_extraer_por_nombre_sin_coincidencias_falla_claro(tmp_path):
    ruta = _zip_plano(tmp_path / "f.zip")
    with pytest.raises(ValueError, match="Ningún archivo coincide"):
        extraer_por_nombre(ruta, ["no-existe*"], tmp_path / "m")


# --- CLI: errores de entrada salen limpios, sin traceback ------------------

def test_cli_zip_inexistente_sale_limpio(tmp_path, capsys):
    from muestrear_zip import main
    with pytest.raises(SystemExit):
        sys.argv = ["muestrear_zip.py", str(tmp_path / "no_existe.zip"), "--listar"]
        main()


def test_cli_archivo_invalido_sale_limpio(tmp_path):
    from muestrear_zip import main
    falso = tmp_path / "falso.zip"
    falso.write_text("no soy un zip")
    with pytest.raises(SystemExit):
        sys.argv = ["muestrear_zip.py", str(falso), "--listar"]
        main()


def test_cli_patron_sin_coincidencias_sale_limpio_sin_traceback(tmp_path, capsys):
    """Regresión: antes esto dejaba pasar el ValueError como traceback crudo,
    a diferencia de los demás errores de la herramienta."""
    from muestrear_zip import main
    ruta = _zip_plano(tmp_path / "f.zip")
    with pytest.raises(SystemExit) as exc:
        sys.argv = ["muestrear_zip.py", str(ruta), "--nombres", "no-existe*",
                    "--salida", str(tmp_path / "m")]
        main()
    assert "Ningún archivo coincide" in str(exc.value)
