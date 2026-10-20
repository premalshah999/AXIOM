"""Close recorded inactive application targets without attaching to worker pages."""

import argparse
import asyncio
import json
import socket
import time
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from websockets.sync.client import connect

from ..db import Run, Setting, now

ACTIVE = {"queued", "running", "submitting"}


def _targets(endpoint, client):
    address = urlsplit(endpoint)
    if address.scheme != "http" or address.hostname not in {"127.0.0.1", "localhost", "host.docker.internal"}:
        raise ValueError("Tab cleanup requires the configured local Chrome endpoint")
    response = client.get(endpoint + "/json/version", headers={"Host": "localhost"})
    response.raise_for_status()
    ws = urlsplit(response.json()["webSocketDebuggerUrl"])
    url = urlunsplit(
        ("ws", f"{socket.gethostbyname(address.hostname)}:{address.port or 80}", ws.path, "", "")
    )
    # /json/list doesn't expose openerId. Browser-level CDP supplies the true
    # popup ancestry without attaching automation or routes to any worker page.
    with connect(url, proxy=None, open_timeout=2, close_timeout=1) as connection:
        connection.send(json.dumps({"id": 1, "method": "Target.getTargets"}))
        deadline = time.monotonic() + 2
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Chrome target inventory exceeded its deadline")
            message = json.loads(connection.recv(timeout=max(0.01, deadline - time.monotonic())))
            if message.get("id") == 1:
                return {t["targetId"]: t for t in message["result"]["targetInfos"] if t.get("type") == "page"}


def close_inactive_tabs(db, endpoint, client):
    if not endpoint:
        return {"closed": 0, "pending": 0}
    endpoint = endpoint.rstrip("/")
    targets = _targets(endpoint, client)
    pending = {}
    # Detach ownership atomically before network work. A subsequently queued
    # owner opens its own page instead of adopting a target being closed.
    with db.exclusive() as session:
        active_jobs = set(session.scalars(select(Run.job_id).where(Run.state.in_(ACTIVE))))
        known_jobs = set(session.scalars(select(Run.job_id)))
        rows = list(session.scalars(select(Setting).where(Setting.key.like("desktop_job:%"))))
        protected = {
            row.value.get("target_id") for row in rows if row.key.removeprefix("desktop_job:") in active_jobs
        }
        while True:
            related = {t.get("openerId") for tid, t in targets.items() if tid in protected}
            related.update(tid for tid, t in targets.items() if t.get("openerId") in protected)
            related.discard(None)
            if related <= protected:
                break
            protected.update(related)
        for row in rows:
            jid = row.key.removeprefix("desktop_job:")
            if jid in active_jobs or jid not in known_jobs:
                continue
            value = row.value
            target = value.get("inactive_target_id") or value.get("target_id")
            owned = set(value.get("inactive_target_ids", [])) | ({target} if target else set())
            owned -= protected
            if not owned:
                continue
            # Keep ledger, upload/checkpoint, armed state and proof intact.
            row.value = {**value, "target_id": None, "inactive_target_ids": sorted(owned)}
            pending.update({tid: row.key for tid in owned})
    # Only descendants of our recorded targets qualify. Browser/mail/user tabs
    # with no application ownership record are never selected by URL or title.
    while True:
        children = {
            tid: pending[target["openerId"]]
            for tid, target in targets.items()
            if target.get("openerId") in pending and tid not in protected and tid not in pending
        }
        if not children:
            break
        pending.update(children)
    # Persist popup identities too, so a failed child close can be retried even
    # after its opener has disappeared.
    with db.exclusive() as session:
        for key in set(pending.values()):
            row = session.get(Setting, key)
            if row:
                row.value = {
                    **row.value,
                    "inactive_target_ids": sorted(tid for tid, owner in pending.items() if owner == key),
                }
    closed = 0
    failures = set()
    for target in set(pending) & targets.keys():
        try:
            response = client.get(endpoint + "/json/close/" + target, headers={"Host": "localhost"})
            response.raise_for_status()
            closed += 1
        except httpx.HTTPError:
            failures.add(target)
    with db.exclusive() as session:
        for key in set(pending.values()):
            row = session.get(Setting, key)
            if not row:
                continue
            attempted = {tid for tid, owner in pending.items() if owner == key}
            value = {**row.value, "tab_closed_at": now()}
            if value.get("inactive_target_id") in attempted:
                value.pop("inactive_target_id", None)
            value["inactive_target_ids"] = sorted(
                (set(value.get("inactive_target_ids", [])) - attempted) | (failures & attempted)
            )
            row.value = value
    return {"closed": closed, "pending": len(failures)}


async def watch(db, endpoint, seconds=15, *, sleep=asyncio.sleep):
    """Run the cleanup inside the app, off the event loop. Replaces the old `browser-cleanup` sidecar container,
    which ran a second copy of this module from a different image.

    It never attaches Playwright to a worker page: it reads the target list over browser-level CDP and closes only
    targets whose job has no active run, exactly as the CLI below does.
    """
    if not endpoint:
        return
    with httpx.Client(timeout=2, trust_env=False) as client:
        while True:
            try:
                result = await asyncio.to_thread(close_inactive_tabs, db, endpoint, client)
                await asyncio.to_thread(
                    db.set_setting, "inactive_tab_cleanup", {**result, "checked_at": now(), "state": "healthy"}
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Chrome not running yet or restarting: report it and keep watching.
                await asyncio.to_thread(
                    db.set_setting,
                    "inactive_tab_cleanup",
                    {"checked_at": now(), "state": "unavailable", "error_type": type(exc).__name__},
                )
            await sleep(max(5, seconds))


def main():
    from ..config import settings
    from ..db import Database

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watch", type=int, default=0, metavar="SECONDS")
    args = parser.parse_args()
    config = settings()
    db = Database(config.database_url)
    with httpx.Client(timeout=2, trust_env=False) as client:
        while True:
            try:
                result = close_inactive_tabs(db, config.browser_cdp_url, client)
                db.set_setting("inactive_tab_cleanup", {**result, "checked_at": now(), "state": "healthy"})
                if result["closed"] or not args.watch:
                    print(result, flush=True)
            except Exception as exc:
                db.set_setting(
                    "inactive_tab_cleanup",
                    {"checked_at": now(), "state": "unavailable", "error_type": type(exc).__name__},
                )
                if not args.watch:
                    raise
            if not args.watch:
                break
            time.sleep(max(5, args.watch))


if __name__ == "__main__":
    main()
