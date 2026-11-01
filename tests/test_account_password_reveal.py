"""The account manager says whose password each employer record holds, and shows it only when asked (wave 13, passwords)."""

from jobpilot.accounts import AccountStore

EMAIL = "applicant@example.com"
MAIN = "Main-Secret-2026!x"
OWN = "Random-Own-77!qzw"


def keep(service, url, password, **extra):
    store = AccountStore(service, EMAIL, url)
    store.save(url, {"email": EMAIL, "password": password}, "authenticated", **extra)
    return store.key(url)


def test_the_list_names_whose_password_it_is_and_carries_none(client, service):
    object.__setattr__(service.config, "application_password", MAIN)
    main = keep(service, "https://a.example.test/apply", MAIN)
    own = keep(service, "https://b.example.test/apply", OWN)
    yours = keep(service, "https://c.example.test/apply", "Typed-By-Me-9!k", owner_set_at="2026-11-01T00:00:00")
    answer = client.get("/api/accounts")
    assert MAIN not in answer.text and OWN not in answer.text and "Typed-By-Me" not in answer.text
    kinds = {row["id"]: row["password_kind"] for row in answer.json()["accounts"]}
    assert kinds == {main: "main", own: "own", yours: "yours"}


def test_a_password_is_shown_only_for_the_one_record_asked_for(client, service):
    object.__setattr__(service.config, "application_password", MAIN)
    own = keep(service, "https://b.example.test/apply", OWN)
    shown = client.post(f"/api/accounts/{own}/reveal", json={})
    assert shown.status_code == 200 and shown.json() == {"email": EMAIL, "password": OWN}
    assert client.post("/api/accounts/employer_account:missing/reveal", json={}).status_code == 404
    assert client.post("/api/accounts/not_an_account/reveal", json={}).status_code == 404


def test_the_main_password_is_never_shown_with_its_tail(service):
    from jobpilot.connections import save, status

    config = save(service.config, {"application_password": MAIN})
    field = next(f for f in status(config)["fields"] if f["name"] == "application_password")
    assert field["set"] and MAIN[-4:] not in field["value"] and field["value"] == "••••"
