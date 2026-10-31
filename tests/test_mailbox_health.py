"""A truthful, resilient mailbox (2026-10-31).

The Email page showed "connected" beside an error with no time that cleared only on the next success; 16 finished challenges
carried 'Mailbox request failed' as their reason; the poll loop and the wait loops swallowed their exceptions with nothing
logged or counted; and a connection that outlived a sleep cost 30 s per wake to find out. Synthetic data only."""

import time
from datetime import UTC, datetime, timedelta

import pytest

from jobpilot import gmail_imap
from jobpilot.db import MailChallenge, Mailbox, Setting
from jobpilot.mail_errors import MailPending, MailTransportError
from support_mail import FolderClient, configured, seed


def status(service, mailbox_id):
    return next(m for m in service.mail.summary()["mailboxes"] if m["id"] == mailbox_id)


def test_a_new_mailbox_reports_ok_with_the_fields_the_page_needs(service):
    box, _ = seed(service)
    shown = status(service, box["id"])
    assert shown["status"] == "ok" and shown["retrying"] is False and shown["consecutive_failures"] == 0
    assert {"last_ok_at", "last_error_at", "last_error", "failures_total"} <= set(shown)
    assert "credentials" not in shown


def test_a_transient_error_shows_retrying_with_its_time_until_a_request_succeeds(service):
    box, _ = seed(service)
    with service.db.exclusive() as s:
        s.get(Mailbox, box["id"]).checked_at = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    service.mail.connection_error(box["id"], "connected", "Gmail connection interrupted; retrying")
    shown = status(service, box["id"])
    assert shown["status"] == "retrying" and shown["retrying"] is True
    assert shown["last_error"].startswith("Gmail connection interrupted") and shown["last_error_at"] > shown["last_ok_at"]
    assert shown["consecutive_failures"] == 1 and shown["failures_total"] == 1
    service.mail.connection_error(box["id"], "connected", "Gmail connection interrupted; retrying")
    assert status(service, box["id"])["consecutive_failures"] == 2
    with service.db.exclusive() as s:  # Gmail answers: what transport._request does after a success
        row = s.get(Mailbox, box["id"])
        row.checked_at, row.error = datetime.now(UTC).isoformat(), ""
        service.mail.mailbox_recovered(s, box["id"])
    shown = status(service, box["id"])
    assert shown["status"] == "ok" and shown["retrying"] is False and shown["consecutive_failures"] == 0
    assert shown["failures_total"] == 2  # the totals stay, so a rate can be measured


def test_a_refused_login_is_reconnect_required_not_retrying(service):
    box, _ = seed(service)
    service.mail.connection_error(box["id"], "reconnect_required", "Gmail login failed; check the app password in .env")
    shown = status(service, box["id"])
    assert shown["status"] == "reconnect_required" and shown["retrying"] is False and shown["last_error_at"]


async def test_a_failed_scan_leaves_a_finished_challenge_as_it_was(service):
    """It wrote 'Mailbox request failed' onto rows that had matched, expired or been verified."""
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")

    async def fails_after_the_window_was_closed(*args, **kwargs):
        with service.db.exclusive() as s:
            row = s.get(MailChallenge, c["id"])
            row.state, row.reason = "verified", "Website accepted the verification code"
        raise MailTransportError("Gmail connection interrupted")

    service.mail.request = fails_after_the_window_was_closed
    await service.mail.poll_once()
    with service.db.session() as s:
        row = s.get(MailChallenge, c["id"])
        assert row.state == "verified" and row.reason == "Website accepted the verification code"


async def test_a_failed_scan_of_an_open_window_says_so_and_is_counted(service):
    from jobpilot.db import Event

    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")

    async def fails(*args, **kwargs):
        raise MailTransportError("Gmail connection interrupted")

    service.mail.request = fails
    await service.mail.poll_once()
    await service.mail.poll_once()
    with service.db.session() as s:
        assert s.get(MailChallenge, c["id"]).reason == "Mailbox request failed; check the connection"
        events = [e.data for e in s.query(Event).filter(Event.run_id == run["id"], Event.kind == "mail_poll_failed")]
    assert [e["failures_so_far"] for e in events] == [1, 2] and events[0]["error"] == "MailTransportError"
    assert service.mail.failure_counts["poll_scan"]["count"] == 2
    assert service.db.get_setting("mail_failures")["poll_scan"]["last_error"] == "MailTransportError"


async def test_a_wait_loop_that_cannot_poll_counts_it_instead_of_swallowing_it(service):
    _, run, rule = await configured(service)
    c = service.mail.begin(run["id"], rule, "alex@example.test", "code")

    async def broken():
        raise RuntimeError("provider said something with a secret in it")

    service.mail.poll_once = broken
    with pytest.raises(MailPending):
        await service.inbox.wait(c, max_wait_seconds=0.3)
    counted = service.mail.failure_counts["wait_poll"]
    assert counted["count"] >= 1 and counted["last_error"] == "RuntimeError"
    assert "secret" not in str(service.db.get_setting("mail_failures"))  # the class only, never the text


# ----- the IMAP pool after the machine slept -------------------------------------------------------------------------


class Socket:
    def __init__(self):
        self.timeouts = []

    def settimeout(self, seconds):
        self.timeouts.append(seconds)


class Connection(FolderClient):
    def __init__(self):
        super().__init__()
        self.sock, self.closed = Socket(), False

    def socket(self):
        return self.sock

    def noop(self):
        self.calls.append(("noop", self.folder))
        return "OK", [b""]

    def logout(self):
        self.closed = True


@pytest.fixture
def connections(monkeypatch):
    made = []

    def connect(address, password):
        made.append(Connection())
        return made[-1]

    monkeypatch.setattr(gmail_imap, "_connect", connect)
    gmail_imap._clients.clear()
    yield made
    gmail_imap._clients.clear()


def test_sleep_is_the_gap_between_the_wall_and_monotonic_clocks():
    pool = gmail_imap._Pool("a@example.test", "pw")
    pool.seen = (1000.0, 50.0)
    assert not pool.slept(wall=1030.0, mono=80.0)  # thirty awake seconds
    assert not pool.slept(wall=1033.0, mono=80.0)  # a few seconds of drift
    assert pool.slept(wall=1000.0 + 3600, mono=80.0)  # an hour of wall time, thirty seconds awake


def test_a_pool_last_used_before_a_sleep_is_dropped_without_a_command(connections):
    gmail_imap.request("a@example.test", "pw" * 8, "/profile")
    (old,) = connections
    key = next(iter(gmail_imap._clients))
    gmail_imap._clients[key].seen = (time.time() - 3600, time.monotonic())  # the Mac slept for an hour
    gmail_imap.request("a@example.test", "pw" * 8, "/profile")
    assert old.closed and len(connections) == 2  # the dead session was closed and a new login made, no 30 s wait
    assert ("noop", old.folder) not in old.calls[1:]


def test_an_awake_pool_keeps_its_connection(connections):
    gmail_imap.request("a@example.test", "pw" * 8, "/profile")
    gmail_imap.request("a@example.test", "pw" * 8, "/profile")
    assert len(connections) == 1 and not connections[0].closed


def test_closing_and_probing_a_connection_use_short_timeouts():
    conn = Connection()
    gmail_imap._close(conn)
    assert conn.closed and conn.sock.timeouts[0] == gmail_imap.CLOSE_SECONDS  # LOGOUT on a dead session waits 2 s, not 30
    pool = gmail_imap._Pool("a@example.test", "pw")
    conn = Connection()
    conn._jp_refreshed = 0
    pool.conns['"INBOX"'] = conn
    pool.folder('"INBOX"')
    assert conn.sock.timeouts == [gmail_imap.PROBE_SECONDS, 30]  # NOOP under the probe timeout, then back to normal
