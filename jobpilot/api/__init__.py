"""The HTTP interface: `create_app` builds the FastAPI application from one router per resource (`routes/`)."""

from .app import create_app

__all__ = ["create_app"]
