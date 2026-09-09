"""Punto de entrada para el servidor de producción (gunicorn).

``crear_app()`` es una factory (Flask no expone la app como variable de
módulo), así que gunicorn necesita este archivo aparte que la instancia una
sola vez al arrancar.
"""

from marcas.web.app import crear_app

app = crear_app()
