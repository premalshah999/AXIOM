"""The dashboard: static assets and the single-page-app fallback. Register last."""

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parents[2] / "static"


def mount_assets(app):
    if (STATIC / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")


def router() -> APIRouter:
    r = APIRouter()

    @r.get("/{path:path}")
    def frontend(path: str):
        if path.startswith(("api/", "demo/")):
            raise HTTPException(404)
        index = STATIC / "index.html"
        if index.exists():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>ApplyPilot Studio</h1><p>Build the dashboard: cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</p>",
            status_code=503,
        )

    return r
