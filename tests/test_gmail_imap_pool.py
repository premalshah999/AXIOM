"""Gmail IMAP commands took ~1.9 s each (2026-10-18), and one connection re-SELECTing Spam, Trash and All Mail made each
search 7+ commands: a verification code waited 46 s (median) per mailbox scan. A pool keeps one connection per folder,
never searches Trash, searches Spam on every fifth listing, and fetches a just-searched message directly."""

import pytest

from jobpilot import gmail_imap
from jobpilot.gmail_imap import _Pool, _request
from support_mail import FolderClient


class FolderConnection(FolderClient):
    """One logged-in connection; the pool selects its folder once."""

    def noop(self):
        self.calls.append(("noop", self.folder))
        return "OK", [b""]


@pytest.fixture
def pool(monkeypatch):
    connections = []

    def connect(address, password):
        conn = FolderConnection()
        connections.append(conn)
        return conn

    monkeypatch.setattr(gmail_imap, "_connect", connect)
    gmail_imap._where.clear()
    return _Pool("alex@example.test", "app-password"), connections


def test_each_folder_keeps_its_own_connection_and_trash_is_never_searched(pool):
    pool, connections = pool
    first = _request(pool, "alex@example.test", "/messages", {"q": "after:1"})
    assert {m["id"] for m in first["messages"]} == {"gm:900", "gm:901", "gm:902"}  # Spam on the first listing
    second = _request(pool, "alex@example.test", "/messages", {"q": "after:1"})
    assert {m["id"] for m in second["messages"]} == {"gm:900", "gm:901"}  # All Mail only
    selects = [c for conn in connections for c in conn.calls if c[0] == "select"]
    assert sorted(f for _, f in selects) == ['"[Gmail]/All Mail"', '"[Gmail]/Spam"']  # each selected once
    assert not any("Trash" in str(c) for conn in connections for c in conn.calls)


def test_a_just_searched_message_is_fetched_without_another_search(pool):
    pool, connections = pool
    _request(pool, "alex@example.test", "/messages", {"q": "after:1"})
    before = sum(len(conn.calls) for conn in connections)
    message = _request(pool, "alex@example.test", "/messages/gm:901", {"format": "full"})
    assert message["id"] == "gm:901"
    added = [c for conn in connections for c in conn.calls][before:]
    assert [c[0] for c in added if c[0] != "noop"] == ["fetch"]


def test_a_repeated_search_skips_identity_fetches(pool):
    pool, connections = pool
    gmail_imap._identity.clear()
    _request(pool, "alex@example.test", "/messages", {"q": "after:1"})
    before = sum(len(conn.calls) for conn in connections)
    again = _request(pool, "alex@example.test", "/messages", {"q": "after:1"})
    assert {m["id"] for m in again["messages"]} == {"gm:900", "gm:901"}
    added = [c for conn in connections for c in conn.calls][before:]
    assert [c[0] for c in added if c[0] != "noop"] == ["search"]
