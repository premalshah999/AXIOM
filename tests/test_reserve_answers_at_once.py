"""Reserve waited 4 to 17 seconds on a real board: the click read every list again before answering, screening every Ready job one by
one, though a reservation changes only that company's rows. It answers now, the rows say so at once, and the lists read again behind it.
Synthetic data only: every employer here is an example."""

import threading
import time

import pytest

from jobpilot import board_view, queue_order, reserved, startable
from jobpilot.admission import QueueBlocked
from jobpilot.db import Job
from tests.test_board_view import listing, rows, setup

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def fresh_rules():
    reserved._compiled.cache_clear()
    yield
    reserved._compiled.cache_clear()


@pytest.fixture
def screens(monkeypatch):
    """Every screening of the Ready jobs waits until the test lets it go; how many began is counted."""
    real, gate = startable.read, threading.Event()
    began = []

    def slow(service):
        began.append(time.monotonic())
        assert gate.wait(30)
        return real(service)

    class Screens:
        def hold(self):
            gate.clear()
            monkeypatch.setattr(startable, "read", slow)

        def free(self):
            gate.set()

        def wait_until_read(self, service, n=1):
            deadline = time.monotonic() + 30
            while len(began) < n and time.monotonic() < deadline:
                time.sleep(0.01)
            gate.set()
            while getattr(service, "lists_reread", None) is not None and time.monotonic() < deadline:
                time.sleep(0.01)
            assert getattr(service, "lists_reread", None) is None

    yield Screens()
    gate.set()


def line_ids(service):
    return [item["id"] for item in queue_order.plans(service).peek()["next"]]


def board_of(service, *jobs):
    setup(service)
    for jid, company in jobs:
        listing(service, jid, company=company)
    rows(service, fit="all")
    queue_order.plans(service).now(service)


def by_id(service, **filters):
    return {r["id"]: r for r in board_view.page(service, fit="all", **filters)["rows"]}


def test_reserve_answers_while_the_screening_is_still_held_back(client, service, screens):
    board_of(service, ("acme", "Acme Example"), ("other", "Other Example"))
    screens.hold()
    started = time.monotonic()
    answer = client.post("/api/reserved/employers", json={"text": "Acme Example"})
    assert answer.status_code == 200 and answer.json()["reserved"] == "Acme Example"
    assert time.monotonic() - started < 10  # the screening below has not been let go: this cannot have waited for it
    # The very next look already says it, with nothing read again yet.
    now = by_id(service)
    assert now["acme"]["state"] == "approval" and now["acme"]["reserved"] == "Acme Example" and now["acme"]["can_add"] is False
    assert now["other"]["state"] == "queue" and now["other"]["reserved"] == ""  # another employer's job keeps its place in line
    value = startable.reading(service).peek()
    assert "acme" not in value["ids"] and value["jobs"]["acme"]["code"] == "reserved" and value["startable"] == 1
    assert "acme" not in line_ids(service) and "other" in line_ids(service)
    # and the list behind it, read in full, agrees.
    screens.wait_until_read(service)
    after = by_id(service)
    assert after["acme"]["state"] == "approval" and after["other"]["state"] == "queue"
    assert startable.reading(service).peek()["jobs"]["acme"]["code"] == "reserved"


def test_nothing_can_start_for_a_reserved_job_while_the_lists_catch_up(client, service, screens):
    board_of(service, ("acme", "Acme Example"))
    screens.hold()
    client.post("/api/reserved/employers", json={"text": "Acme Example"})
    with service.db.session() as s, pytest.raises(QueueBlocked):
        reserved.require_approval(s, s.get(Job, "acme"))
    screens.wait_until_read(service)


def test_release_answers_at_once_and_never_says_a_job_can_start_unscreened(client, service, screens):
    board_of(service, ("acme", "Acme Example"))
    client.post("/api/reserved/employers", json={"text": "Acme Example"})
    deadline = time.monotonic() + 30
    while getattr(service, "lists_reread", None) is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    screens.hold()
    answer = client.delete("/api/reserved/employers", params={"name": "Acme Example"})
    assert answer.status_code == 200
    row = by_id(service)["acme"]
    assert row["state"] == "ready" and row["reserved"] == "" and row["can_add"] is True and row["approval"] == ""
    # The reading no longer holds it, and has not said it can start: that is for the screening.
    value = startable.reading(service).peek()
    assert "acme" not in value["jobs"] and "acme" not in value["ids"]
    screens.wait_until_read(service)
    assert "acme" in startable.reading(service).peek()["ids"]


def test_a_read_under_way_when_the_list_changes_does_not_undo_the_change(client, service, screens):
    board_of(service, ("acme", "Acme Example"))
    held_board = startable.reading(service)
    screens.hold()
    # A periodic read has started before the click, so what it finds predates the reservation.
    held_board.at = 0.0
    held_board.get(service)
    deadline = time.monotonic() + 30
    while held_board.busy.acquire(blocking=False):
        held_board.busy.release()
        assert time.monotonic() < deadline
        time.sleep(0.01)
    client.post("/api/reserved/employers", json={"text": "Acme Example"})
    assert held_board.peek()["jobs"]["acme"]["code"] == "reserved"
    screens.wait_until_read(service, 1)
    assert held_board.peek()["jobs"]["acme"]["code"] == "reserved"
    assert by_id(service)["acme"]["state"] == "approval"


def test_many_clicks_read_again_on_one_thread(client, service, screens):
    board_of(service, ("acme", "Acme Example"), ("beta", "Beta Example"), ("gamma", "Gamma Example"))
    screens.hold()
    for name in ("Acme Example", "Beta Example", "Gamma Example"):
        assert client.post("/api/reserved/employers", json={"text": name}).status_code == 200
    assert sum(t.name == "lists-reread" for t in threading.enumerate()) == 1
    screens.wait_until_read(service)
    value = startable.reading(service).peek()
    assert {value["jobs"][j]["code"] for j in ("acme", "beta", "gamma")} == {"reserved"} and value["startable"] == 0
