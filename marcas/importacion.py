"""Carga del catálogo desde la planilla del registro (CSV o Excel).

Las marcas tienen dos orígenes que llegan por separado:

* la **imagen**, que sale de digitalizar el papel (`marcas procesar`);
* los **datos**: código oficial, propietario, establecimiento. Eso está en una
  planilla, no en el escaneo.

Este módulo carga los datos. Una marca puede existir en el catálogo con sus
datos y todavía sin imagen —es el estado normal mientras se digitaliza— y al
revés: una imagen digitalizada esperando que le asignen su código real. Las
dos mitades se unen por el **código de la marca**.

La planilla no tiene que tener un formato exacto: las columnas se reconocen
por el nombre del encabezado, tolerando tildes, mayúsculas y variantes
("CI/RUC", "ci ruc", "Cédula"). Las columnas que no se reconocen se informan y
se ignoran, en lugar de hacer fallar la carga entera.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from marcas import config, db

# Campo del catálogo -> nombres de columna aceptados (ya normalizados).
ALIAS: dict[str, tuple[str, ...]] = {
    "codigo": (
        # "Nº de Marca" se normaliza a "no de marca": el símbolo de ordinal
        # se descompone en una "o".
        "codigo", "codigo de marca", "codigo marca", "cod marca", "cod de marca",
        "marca", "nro de marca", "nro marca", "no de marca", "no marca",
        "n de marca", "numero de marca", "numero", "nro", "id",
    ),
    "descripcion": ("descripcion", "detalle", "diseno", "diseno de marca", "figura"),
    "estado": ("estado", "situacion"),
    "observaciones": ("observaciones", "observacion", "nota", "notas"),
    "propietario": (
        "propietario", "nombre", "nombre y apellido", "razon social",
        "nombre y apellido razon social", "vendedor", "vendedor nombre",
        "titular", "dueno", "productor",
    ),
    "documento": (
        "documento", "ci", "ruc", "ci ruc", "cedula", "cedula de identidad",
        "vendedor documento", "vendedor ci ruc",
    ),
    "establecimiento": (
        "establecimiento", "estancia", "campo", "finca", "vendedor establecimiento",
    ),
    "establecimiento_codigo": (
        "codigo de establecimiento", "cod establecimiento", "codigo establecimiento",
        "cod est", "codigo de est", "vendedor establecimiento codigo",
    ),
    "localidad": ("localidad", "distrito", "ciudad"),
    "departamento": ("departamento", "depto"),
    "telefono": ("telefono", "tel", "celular", "contacto"),
    "tipo": ("tipo", "tipo de marca"),
    "numero_guia": (
        "numero guia", "numero de guia", "n de guia", "no de guia",
        "nro de guia", "nro guia", "guia", "numero de orden", "n de orden",
        "no de orden", "nro de orden", "n de guia orden",
    ),
    "archivo_imagen": (
        "archivo imagen", "archivo", "imagen", "archivo png", "png",
        "archivo de imagen", "ruta imagen", "ruta de imagen",
    ),
}

CAMPOS_PROPIETARIO = (
    "documento", "establecimiento", "establecimiento_codigo",
    "localidad", "departamento", "telefono",
)
ESTADOS = ("activa", "revisar", "baja")
TIPOS_MARCA = ("dominante", "complementaria")


@dataclass
class ResultadoImportacion:
    marcas_nuevas: int = 0
    marcas_actualizadas: int = 0
    propietarios: set[int] = field(default_factory=set)
    filas_leidas: int = 0
    omitidas: list[str] = field(default_factory=list)
    columnas_reconocidas: dict[str, str] = field(default_factory=dict)
    columnas_ignoradas: list[str] = field(default_factory=list)

    def resumen(self) -> dict:
        return {
            "filas_leidas": self.filas_leidas,
            "marcas_nuevas": self.marcas_nuevas,
            "marcas_actualizadas": self.marcas_actualizadas,
            "propietarios_tocados": len(self.propietarios),
            "omitidas": len(self.omitidas),
            "columnas_ignoradas": self.columnas_ignoradas,
        }


def normalizar(texto: str) -> str:
    """'CI / RUC' -> 'ci ruc'. Sin tildes, sin puntuación, en minúsculas."""
    sin_tildes = unicodedata.normalize("NFKD", str(texto))
    sin_tildes = sin_tildes.encode("ascii", "ignore").decode()
    limpio = re.sub(r"[^a-zA-Z0-9]+", " ", sin_tildes)
    return re.sub(r"\s+", " ", limpio).strip().lower()


def mapear_columnas(encabezados: list[str]) -> tuple[dict[int, str], list[str]]:
    """Asocia cada columna de la planilla con un campo del catálogo."""
    por_alias = {alias: campo for campo, lista in ALIAS.items() for alias in lista}
    mapa: dict[int, str] = {}
    ignoradas: list[str] = []
    for i, bruto in enumerate(encabezados):
        clave = normalizar(bruto or "")
        campo = por_alias.get(clave)
        if campo and campo not in mapa.values():
            mapa[i] = campo
        elif bruto and str(bruto).strip():
            ignoradas.append(str(bruto).strip())
    return mapa, ignoradas


def _leer_csv(ruta: Path) -> tuple[list[str], list[list[str]]]:
    # Excel en español exporta con punto y coma; el sniffer decide solo.
    for codificacion in ("utf-8-sig", "latin-1"):
        try:
            texto = ruta.read_text(encoding=codificacion)
            break
        except UnicodeDecodeError:
            continue
    else:                                              # pragma: no cover
        raise ValueError(f"No se pudo leer {ruta.name}: codificación desconocida")
    muestra = texto[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=",;\t|")
    except csv.Error:
        dialecto = csv.excel
    filas = list(csv.reader(texto.splitlines(), dialecto))
    filas = [f for f in filas if any(str(c).strip() for c in f)]
    if not filas:
        return [], []
    return filas[0], filas[1:]


def _leer_xlsx(ruta: Path) -> tuple[list[str], list[list]]:
    try:
        import openpyxl
    except ImportError:                                # pragma: no cover
        raise ValueError(
            "Para leer Excel hace falta openpyxl:  pip install openpyxl\n"
            "También sirve guardar la planilla como CSV."
        )
    libro = openpyxl.load_workbook(ruta, read_only=True, data_only=True)
    hoja = libro.active
    filas = [
        ["" if c is None else c for c in fila]
        for fila in hoja.iter_rows(values_only=True)
        if any(c is not None and str(c).strip() for c in fila)
    ]
    libro.close()
    if not filas:
        return [], []
    return [str(c) for c in filas[0]], filas[1:]


def leer_planilla(ruta: Path | str) -> tuple[list[str], list[list]]:
    """Devuelve (encabezados, filas) de un CSV o un XLSX."""
    ruta = Path(ruta)
    if not ruta.exists():
        raise FileNotFoundError(f"No existe la planilla: {ruta}")
    if ruta.suffix.lower() in {".xlsx", ".xlsm"}:
        return _leer_xlsx(ruta)
    if ruta.suffix.lower() == ".xls":
        raise ValueError(
            "El formato .xls (Excel viejo) no se lee. Guardar como .xlsx o .csv."
        )
    return _leer_csv(ruta)


def _texto(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)               # 80020081.0 -> 80020081
    return str(valor).strip()


def _resolver_imagen(valor: str, carpeta_base: Path) -> tuple[str | None, str | None]:
    """Ubica el archivo de imagen de una fila y lo deja listo para guardar.

    ``valor`` suele venir como ruta relativa a la propia planilla (el caso
    típico: un CSV con una carpeta de PNG al lado, como entrega un lote de
    extracción). Devuelve ``(ruta_para_guardar, aviso)``: la ruta queda
    relativa a la raíz del proyecto cuando es posible, igual que hace el
    resto del pipeline; si el archivo no aparece, se devuelve ``None`` y un
    aviso en vez de fallar la fila entera.
    """
    ruta = Path(valor)
    candidatos = [ruta] if ruta.is_absolute() else [carpeta_base / ruta, ruta]
    for candidata in candidatos:
        if candidata.exists():
            candidata = candidata.resolve()
            if candidata.is_relative_to(config.RAIZ):
                return str(candidata.relative_to(config.RAIZ)), None
            return str(candidata), None
    return None, f"no se encontró la imagen '{valor}'"


def importar_datos(
    ruta: Path | str, ruta_db: Path | None = None
) -> ResultadoImportacion:
    """Carga (o actualiza) marcas y propietarios desde la planilla del registro."""
    encabezados, filas = leer_planilla(ruta)
    resultado = ResultadoImportacion()
    if not encabezados:
        resultado.omitidas.append("la planilla está vacía")
        return resultado

    mapa, ignoradas = mapear_columnas(encabezados)
    resultado.columnas_ignoradas = ignoradas
    resultado.columnas_reconocidas = {
        campo: encabezados[i] for i, campo in mapa.items()
    }
    if "codigo" not in mapa.values():
        raise ValueError(
            "La planilla no tiene una columna de código de marca. "
            f"Se esperaba alguna de: {', '.join(ALIAS['codigo'][:5])}. "
            f"Encabezados encontrados: {', '.join(map(str, encabezados))}"
        )

    db.inicializar(ruta_db)
    carpeta_base = Path(ruta).resolve().parent
    for numero, fila in enumerate(filas, start=2):     # 1 es el encabezado
        resultado.filas_leidas += 1
        datos = {campo: _texto(fila[i]) if i < len(fila) else ""
                 for i, campo in mapa.items()}
        codigo = datos.get("codigo", "")
        if not codigo:
            resultado.omitidas.append(f"fila {numero}: sin código de marca")
            continue

        propietario_id = None
        if datos.get("propietario"):
            propietario_id = db.alta_propietario(
                datos["propietario"], ruta_db,
                **{k: datos.get(k) for k in CAMPOS_PROPIETARIO},
            )
            resultado.propietarios.add(propietario_id)

        estado = normalizar(datos.get("estado", "")) or None
        if estado and estado not in ESTADOS:
            resultado.omitidas.append(
                f"fila {numero}: estado '{datos['estado']}' desconocido, se deja como está"
            )
            estado = None

        tipo = normalizar(datos.get("tipo", "")) or None
        if tipo and tipo not in TIPOS_MARCA:
            resultado.omitidas.append(
                f"fila {numero}: tipo '{datos['tipo']}' desconocido "
                f"(se esperaba dominante o complementaria)"
            )
            tipo = None

        archivo_png = None
        if datos.get("archivo_imagen"):
            archivo_png, aviso = _resolver_imagen(datos["archivo_imagen"], carpeta_base)
            if aviso:
                resultado.omitidas.append(f"fila {numero}: {aviso}")

        with db.conectar(ruta_db) as con:
            existe = con.execute(
                "SELECT id FROM marcas WHERE codigo = ?", (codigo,)
            ).fetchone()
            campos = {
                "descripcion": datos.get("descripcion") or None,
                "observaciones": datos.get("observaciones") or None,
                "propietario_id": propietario_id,
                "numero_guia": datos.get("numero_guia") or None,
                "archivo_png": archivo_png,
            }
            if estado:
                campos["estado"] = estado
            if tipo:
                campos["tipo"] = tipo
            # Sólo se escriben los campos con valor: una planilla incompleta no
            # tiene por qué borrar lo que ya estaba cargado.
            campos = {k: v for k, v in campos.items() if v is not None}
            if existe:
                if campos:
                    asignaciones = ", ".join(f"{k} = ?" for k in campos)
                    con.execute(
                        f"UPDATE marcas SET {asignaciones}, "
                        "actualizado_en = datetime('now') WHERE codigo = ?",
                        [*campos.values(), codigo],
                    )
                resultado.marcas_actualizadas += 1
            else:
                columnas = ["codigo", *campos]
                marcadores = ",".join("?" * len(columnas))
                con.execute(
                    f"INSERT INTO marcas ({','.join(columnas)}) VALUES ({marcadores})",
                    [codigo, *campos.values()],
                )
                resultado.marcas_nuevas += 1
    return resultado


ENCABEZADOS_PLANTILLA = [
    "codigo", "descripcion", "propietario", "ci_ruc", "establecimiento",
    "codigo_de_establecimiento", "localidad", "departamento", "telefono",
    "estado", "observaciones", "tipo", "numero_guia", "archivo_imagen",
]
EJEMPLOS = [
    ["M-0001", "Círculo con barra", "PIEDRA ALTA S.A. INMOBILIARIA", "80020081",
     "LA PATRICIA", "1706020006", "Concepción", "Concepción", "", "activa", "",
     "dominante", "90947260", ""],
    ["M-0002", "Ancla", "PIEDRA ALTA S.A. INMOBILIARIA", "80020081",
     "LA PATRICIA", "1706020006", "", "", "", "activa", "marca heredada",
     "complementaria", "90947260", ""],
]


def plantilla_csv(destino: Path | str) -> Path:
    """Escribe un CSV vacío con las columnas que entiende el importador."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        escritor = csv.writer(fh)
        escritor.writerow(ENCABEZADOS_PLANTILLA)
        escritor.writerows(EJEMPLOS)
    return destino
