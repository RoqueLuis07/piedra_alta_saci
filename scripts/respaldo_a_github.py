"""Respaldo automático diario: sube todo el contenido de la base a un
repositorio de GitHub aparte (privado), para sobrevivir aunque desaparezca
el proyecto de Railway entero -- que es exactamente lo que le pasó al
proyecto de Supabase que usaba este sistema antes.

Pensado para correr como un servicio de tipo "Cron Job" en Railway, con el
mismo código de la app pero un comando de arranque distinto:

    python scripts/respaldo_a_github.py

Variables de entorno que necesita (además de las que ya usa la app):
- GITHUB_TOKEN: un token de acceso personal de GitHub (de grano fino,
  limitado sólo al repo de respaldos, con permiso de "Contents"
  lectura+escritura). Nunca se guarda en el repo del código.
- BACKUP_REPO: "owner/repo" del repositorio privado de respaldos.
- BACKUP_BRANCH: rama a usar (opcional, default "main").

Sube siempre a las MISMAS rutas (no una carpeta por fecha), así cada
corrida pisa el archivo anterior y el propio historial de commits de git
sirve como "volver a un punto en el tiempo" -- sin duplicar todo el
contenido (imágenes incluidas) todos los días. Las marcas se parten en
lotes para no acercarse al límite de tamaño de archivo de GitHub (100 MB)
a medida que crezca el catálogo.
"""
from __future__ import annotations

import base64
import os
import sys
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marcas.servidor.consultas import dict_a_json, exportar_todo_para_respaldo
from marcas.servidor.db import devolver_conexion, obtener_conexion

TAMANO_LOTE_MARCAS = 200
GITHUB_API = "https://api.github.com"


def _config():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("BACKUP_REPO")
    rama = os.environ.get("BACKUP_BRANCH", "main")
    if not token or not repo:
        sys.exit("Faltan GITHUB_TOKEN y/o BACKUP_REPO como variables de entorno.")
    return token, repo, rama


def _sha_actual(sesion: requests.Session, repo: str, ruta: str, rama: str) -> str | None:
    """El sha del archivo si ya existe en el repo -- la API de GitHub lo
    exige para ACTUALIZAR un archivo (si no se manda, asume que es nuevo
    y falla si ya existía)."""
    r = sesion.get(f"{GITHUB_API}/repos/{repo}/contents/{ruta}", params={"ref": rama})
    if r.status_code == 200:
        return r.json()["sha"]
    if r.status_code == 404:
        return None
    r.raise_for_status()


def _subir_archivo(sesion: requests.Session, repo: str, ruta: str, contenido: str, rama: str, mensaje: str) -> None:
    sha = _sha_actual(sesion, repo, ruta, rama)
    payload = {
        "message": mensaje,
        "content": base64.b64encode(contenido.encode("utf-8")).decode("ascii"),
        "branch": rama,
    }
    if sha:
        payload["sha"] = sha
    r = sesion.put(f"{GITHUB_API}/repos/{repo}/contents/{ruta}", json=payload)
    if not r.ok:
        raise RuntimeError(f"No se pudo subir {ruta}: {r.status_code} {r.text[:300]}")
    print(f"  subido: {ruta} ({len(contenido)} bytes)")


def main() -> None:
    token, repo, rama = _config()
    sesion = requests.Session()
    sesion.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })

    conexion = obtener_conexion()
    try:
        datos = exportar_todo_para_respaldo(conexion)
    finally:
        devolver_conexion(conexion)

    ahora = datetime.now(timezone.utc)
    marca_tiempo = ahora.strftime("%Y-%m-%d %H:%M UTC")
    print(f"Respaldo iniciado -- {marca_tiempo}")

    marcas = datos.pop("marcas")
    for tabla, filas in datos.items():
        _subir_archivo(sesion, repo, f"respaldo/{tabla}.json", dict_a_json(filas), rama, f"Respaldo automático {marca_tiempo} -- {tabla}")

    lotes = [marcas[i : i + TAMANO_LOTE_MARCAS] for i in range(0, len(marcas), TAMANO_LOTE_MARCAS)] or [[]]
    for indice, lote in enumerate(lotes, start=1):
        ruta = f"respaldo/marcas/lote_{indice:04d}.json"
        _subir_archivo(sesion, repo, ruta, dict_a_json(lote), rama, f"Respaldo automático {marca_tiempo} -- marcas lote {indice}")

    manifiesto = dict_a_json({
        "generado_en": ahora,
        "cantidades": {**{t: len(f) for t, f in datos.items()}, "marcas": len(marcas)},
        "lotes_de_marcas": len(lotes),
    })
    _subir_archivo(sesion, repo, "respaldo/manifiesto.json", manifiesto, rama, f"Respaldo automático {marca_tiempo} -- manifiesto")

    print(f"Respaldo completo: {len(marcas)} marcas en {len(lotes)} lote(s).")


if __name__ == "__main__":
    main()
