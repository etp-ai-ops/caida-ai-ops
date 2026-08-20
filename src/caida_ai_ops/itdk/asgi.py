"""ASGI module used by the production container command."""

from .server import create_app

app = create_app()
