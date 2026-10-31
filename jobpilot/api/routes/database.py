"""Settings › Database: which database AXIOM uses (this computer, Supabase or a server), a test before anything changes, and the move.

Connection strings are accepted here and never returned: the screen gets where they point (host, port, user, database) and
whether one is saved. Nothing in this module logs a body.
"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...storage import check, choice, switch


class Candidate(BaseModel):
    mode: str = Field(pattern="^(local|supabase|server)$")
    # Blank: the string already saved (or in .env) for this choice.
    url: str = Field(default="", max_length=2000)


class Switch(Candidate):
    with_data: bool = True  # Bring this installation's data along. False: use the other database as it is.
    replace: bool = False  # The other database already holds AXIOM data and may be overwritten.


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    def candidate_url(data: Candidate) -> str:
        if data.mode == "local":
            return choice.local_urls(config)[0]
        try:
            return choice.normalise(data.url or choice.url_for(config, data.mode), data.mode)
        except choice.DatabaseChoiceError as exc:
            raise HTTPException(422, str(exc)) from None

    def ping() -> dict | None:
        """What a round trip to the database in use costs right now, through the pool the app uses."""
        samples = []
        for _ in range(5):
            began = time.perf_counter()
            with db.engine.connect() as connection:
                connection.exec_driver_sql("select 1")
            samples.append((time.perf_counter() - began) * 1000)
        samples.sort()
        return {"median_ms": round(samples[len(samples) // 2], 1), "max_ms": round(samples[-1], 1)}

    def snapshot() -> dict:
        view = choice.view(config)
        view["running"] = switch.running(config)
        view["move"] = switch.read_progress(config)
        if view["mode"] != "local":
            round_trip = ping()
            view["ping"] = round_trip
            view["workers"] = check.rate_workers(round_trip["median_ms"])
        view["audit"] = db.audit.snapshot() if db.audit else None
        view["configured_workers"] = config.workers
        return view

    @r.get("/api/database")
    async def get_database():
        return await asyncio.to_thread(snapshot)

    @r.post("/api/database/test")
    async def test_database(data: Candidate):
        """Check a database without changing anything. Returns each check with a plain sentence."""
        raw = candidate_url(data)
        return await asyncio.to_thread(check.run, data.mode, raw)

    @r.post("/api/database/switch", status_code=202)
    async def start_switch(data: Switch):
        """Move to another database. A separate process does it, so that it outlives a restart of this one; the screen polls
        GET /api/database/switch. Nothing changes until the new database has passed its test and the copy is verified."""
        if data.mode == config.database_mode:
            raise HTTPException(409, "AXIOM already uses that database.")
        if switch.running(config):
            raise HTTPException(409, "A database move is already running.")
        if data.mode != "local":
            candidate_url(data)  # Refuse a string that cannot work before anything starts.
            if data.url.strip():
                choice.keep_url(config, data.mode, data.url)
        args = [sys.executable, "-m", "jobpilot.cli", "database", "switch", data.mode, "--copy" if data.with_data else "--no-copy"]
        if data.replace:
            args.append("--replace")
        folder = Path(config.data_dir) / "migration"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        log = os.open(folder / switch.LOG, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=os.environ)
        finally:
            os.close(log)
        return {"started": True}

    @r.get("/api/database/switch")
    def get_switch():
        return {"running": switch.running(config), **switch.read_progress(config)}

    @r.delete("/api/database/{mode}")
    def forget_database(mode: str):
        """Forget a saved connection string (its password with it). Never the one in use."""
        if mode not in choice.REMOTE:
            raise HTTPException(404)
        if mode == config.database_mode:
            raise HTTPException(409, "That database is in use. Switch to another one first.")
        choice.forget(config, mode)
        return choice.view(config)

    return r
