"""Punto de entrada para el servidor de producción (gunicorn).

Corre ``marcas.servidor`` (login por Supabase + roles), no ``marcas.web``
(el selector visual local de una sola persona, sin login, pensado para la
máquina de la oficina). ``crear_app()`` es una factory, así que gunicorn
necesita este archivo aparte que la instancia una sola vez al arrancar.
"""

from marcas.servidor.app import crear_app

app = crear_app()
