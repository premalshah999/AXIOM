"""HTTP contract of token sign-in and sign-out: a cookie opens the API, a wrong token does not, attempts are throttled."""

from fastapi.testclient import TestClient


def anonymous(app, config):
    return TestClient(app, base_url=config.base_url)


def test_a_wrong_token_is_refused(app, config):
    answer = anonymous(app, config).post("/api/login", json={"token": "not-the-token"})
    assert answer.status_code == 401
    assert answer.json() == {"detail": "Incorrect access token"}


def test_the_right_token_sets_a_session_cookie_that_opens_the_api(app, config):
    web = anonymous(app, config)
    assert web.get("/api/control").status_code == 401
    answer = web.post("/api/login", json={"token": config.app_token})
    assert answer.status_code == 200 and answer.json() == {"ok": True}
    cookie = answer.headers["set-cookie"].lower()
    assert "jp_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert web.get("/api/control").status_code == 200


def test_logout_clears_the_cookie(app, config):
    web = anonymous(app, config)
    web.post("/api/login", json={"token": config.app_token})
    answer = web.post("/api/logout", json={})
    assert answer.status_code == 200 and answer.json() == {"ok": True}
    assert web.get("/api/control").status_code == 401


def test_the_eleventh_attempt_in_a_minute_is_throttled(app, config):
    web = anonymous(app, config)
    codes = [web.post("/api/login", json={"token": "wrong"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_a_token_longer_than_the_limit_is_a_schema_error(app, config):
    assert anonymous(app, config).post("/api/login", json={"token": "x" * 300}).status_code == 422
