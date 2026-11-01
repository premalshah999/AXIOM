"""An install must show. The page names the assets of its build, so a stored copy keeps an old build on screen: the app's
window showed the build before an install, whatever was installed."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from jobpilot.api.routes import frontend


def test_the_page_is_revalidated_every_time(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<html></html>")
    monkeypatch.setattr(frontend, "STATIC", tmp_path)
    app = FastAPI()
    app.include_router(frontend.router())
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_the_window_asks_for_the_page_past_any_stored_copy():
    source = (Path(__file__).parents[1] / "desktop" / "AXIOM.m").read_text()
    assert "NSURLRequestReloadIgnoringLocalCacheData" in source
