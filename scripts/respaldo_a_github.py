"""Respaldo automático diario: sube todo el contenido de la base a un
repositorio de GitHub aparte (privado), para sobrevivir aunque desaparezca
el proyecto de Railway entero -- que es exactamente lo que le pasó al
proyecto de Supabase que usaba este sistema antes.

Pensado para correr como un servicio de tipo "Cron Job" en Railway, con el
mismo código de la app pero un comando de arranque distinto:

    python scripts/respaldo_a_github.py

Respalda TODO lo necesario para recuperar el sistema (ver marcas/servidor/respaldo.py): usuarios con sus
contraseñas, operaciones, marcas con sus imágenes, páginas escaneadas con su imagen, el PDF de cada documento
emitido, el historial de imágenes de las marcas y los cambios pendientes.

Variables de entorno que necesita (además de las que ya usa la app):
- DATABASE_URL: la base principal (Railway). Se respalda en la carpeta «respaldo/».
- RENDER_DATABASE_URL: (opcional) la base de Render. Se respalda en «respaldo_render/», en el mismo repositorio.
- GITHUB_TOKEN: un token de acceso personal de GitHub (de grano fino,
  limitado sólo al repo de respaldos, con permiso de "Contents"
  lectura+escritura). Nunca se guarda en el repo del código.
- BACKUP_REPO: "owner/repo" del repositorio privado de respaldos.
- BACKUP_BRANCH: rama a usar (opcional, default "main").

Sube siempre a las MISMAS rutas (no una carpeta por fecha) y sólo los archivos que cambiaron desde la última
vez: así el historial de commits de git sirve como "volver a un punto en el tiempo" sin duplicar todo el
contenido (imágenes incluidas) todos los días. Cada archivo queda muy por debajo de los 100 MB que admite GitHub.
Si una base falla, igual se respalda la otra y el script termina con error para que se note.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
from datetime import datetime, timezone

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marcas.servidor.respaldo import volcar

GITHUB_API = "https://api.github.com"


def _config():
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("BACKUP_REPO")
    rama = os.environ.get("BACKUP_BRANCH", "main")
    if not token or not repo:
        sys.exit("Faltan GITHUB_TOKEN y/o BACKUP_REPO como variables de entorno.")
    return token, repo, rama


def _sha_git(contenido: bytes) -> str:
    """El sha que Git le da a un archivo (blob): sirve para saber si ya está subido igual."""
    return hashlib.sha1(b"blob %d\0" % len(contenido) + contenido).hexdigest()


class Repositorio:
    """Subida de archivos al repo de respaldos por la API de contenidos, saltando los que no cambiaron."""

    def __init__(self, token: str, repo: str, rama: str):
        self.repo, self.rama = repo, rama
        self.sesion = requests.Session()
        self.sesion.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        self.shas = self._leer_arbol()
        self.subidos = self.sin_cambios = 0

    def _leer_arbol(self) -> dict[str, str]:
        r = self.sesion.get(f"{GITHUB_API}/repos/{self.repo}/git/trees/{self.rama}", params={"recursive": "1"})
        if r.status_code in (404, 409):      # repo o rama todavía vacíos
            return {}
        r.raise_for_status()
        return {e["path"]: e["sha"] for e in r.json().get("tree", []) if e["type"] == "blob"}

    def subir(self, ruta: str, contenido: bytes, mensaje: str) -> None:
        sha_nuevo = _sha_git(contenido)
        sha_actual = self.shas.get(ruta)
        if sha_actual == sha_nuevo:
            self.sin_cambios += 1
            return
        payload = {"message": mensaje, "content": base64.b64encode(contenido).decode("ascii"), "branch": self.rama}
        if sha_actual:
            payload["sha"] = sha_actual
        r = self.sesion.put(f"{GITHUB_API}/repos/{self.repo}/contents/{ruta}", json=payload, timeout=300)
        if not r.ok:
            raise RuntimeError(f"No se pudo subir {ruta}: {r.status_code} {r.text[:300]}")
        self.shas[ruta] = r.json()["content"]["sha"]
        self.subidos += 1
        print(f"  subido: {ruta} ({len(contenido)} bytes)")


def respaldar(repositorio: Repositorio, url_base: str, carpeta: str, etiqueta: str) -> dict:
    if "sslmode=" not in url_base and ".render.com" in url_base:
        url_base += ("&" if "?" in url_base else "?") + "sslmode=require"
    marca_tiempo = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Respaldo de {etiqueta} iniciado -- {marca_tiempo}")
    conexion = psycopg2.connect(url_base, cursor_factory=RealDictCursor, connect_timeout=30)
    try:
        manifiesto = volcar(
            conexion,
            lambda ruta, contenido: repositorio.subir(ruta, contenido, f"Respaldo automático {marca_tiempo} ({etiqueta}) -- {ruta.split('/', 1)[1]}"),
            carpeta,
        )
    finally:
        conexion.close()
    print(f"Respaldo de {etiqueta} completo: {manifiesto['cantidades']}")
    return manifiesto


def main() -> None:
    token, repo, rama = _config()
    repositorio = Repositorio(token, repo, rama)
    fuentes = []
    if os.environ.get("DATABASE_URL"):
        fuentes.append((os.environ["DATABASE_URL"], os.environ.get("RESPALDO_CARPETA", "respaldo"), "Railway"))
    if os.environ.get("RENDER_DATABASE_URL"):
        fuentes.append((os.environ["RENDER_DATABASE_URL"], "respaldo_render", "Render"))
    if not fuentes:
        sys.exit("Falta DATABASE_URL (y, si querés respaldar también Render, RENDER_DATABASE_URL).")
    fallos = []
    for url, carpeta, etiqueta in fuentes:
        try:
            respaldar(repositorio, url, carpeta, etiqueta)
        except Exception as exc:
            print(f"ERROR respaldando {etiqueta}: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
            fallos.append(etiqueta)
    print(f"Archivos subidos: {repositorio.subidos} · sin cambios: {repositorio.sin_cambios}")
    if fallos:
        sys.exit(f"Falló el respaldo de: {', '.join(fallos)}")


if __name__ == "__main__":
    main()
