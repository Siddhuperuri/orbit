"""ASGI entrypoint.

Referenced by uvicorn as ``orbit.composition.asgi:app`` and by the container
image's default command. Kept in ``composition`` rather than at the package root
so that the module which wires everything together is still inside the layer
that is permitted to do so.
"""

from __future__ import annotations

from fastapi import FastAPI

from orbit.composition.app import create_app

app: FastAPI = create_app()
