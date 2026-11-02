"""Owner-adjustable settings: global switches, spending and pacing limits, model providers."""

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agent_settings import AgentSettings
from ...db import Setting


class Control(BaseModel):
    """Only what the caller means to change: a screen that sends both from a stale read must not lift a stop."""

    paused: bool | None = None
    auto_submit: bool | None = None


class Limits(BaseModel):
    job_token_cap: int = Field(ge=50_000, le=50_000_000)
    company_limit: int = Field(ge=1, le=25)


class Platforms(BaseModel):
    """The platforms the autopilot applies on; every other platform is excluded (the owner's own Apply still works)."""

    include: list[str] = Field(min_length=1, max_length=60)
    reason: str = Field(default="", max_length=300)


class Erase(BaseModel):
    """The typed confirmation for Settings › Delete everything (erase.PHRASE, exactly)."""

    confirm: str = Field(default="", max_length=100)


class LegalAccept(BaseModel):
    version: str = Field(max_length=60)
    adult: bool = False


class ConnectionsUpdate(BaseModel):
    """Values to save in Setup > Connections; a blank value clears that saved value."""

    values: dict[str, str] = Field(max_length=20)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    def connections_view():
        """What the page shows, with the model that really answers: the two roles under Advanced are what the engine reads."""
        from ... import connections
        from ...agent_settings import current

        status = connections.status(config)
        try:
            used = current(db, config).models.answers
        except ValueError:  # A row this build cannot read must not take the keys page with it.
            return status
        return {**status, "provider": used, "model_ready": bool(getattr(config, used + "_api_key", ""))}

    def save_values(values):
        from ... import connections
        from ...config import settings

        connections.save(config, values)
        cached = settings()
        if cached is not config:
            connections.apply(cached)  # Code that reads settings() directly sees the same keys at once.

    @r.get("/api/connections")
    def connections_status():
        return connections_view()

    @r.put("/api/connections")
    def save_connections(data: ConnectionsUpdate):
        try:
            save_values(data.values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if "llm_provider" in data.values:
            from ...agent_settings import follow_provider

            follow_provider(db, config.llm_provider)
        if "daily_application_limit" in data.values:
            from ...autopilot import apply_daily_limit

            apply_daily_limit(db, config.daily_application_limit)
        return connections_view()

    @r.post("/api/connections/{name}/test")
    async def test_connection(name: str):
        from ... import connections

        if name not in connections.FIELDS:
            raise HTTPException(404)
        return await connections.test(config, name)

    @r.get("/api/control")
    def get_control():
        value = db.get_setting("control", {})
        return {
            "paused": bool(value.get("paused", False)),
            "auto_submit": bool(value.get("auto_submit", False)),
        }

    @r.get("/api/limits")
    def get_limits():
        """Owner-adjustable spending and pacing limits (Setup › System)."""
        from ...application_pacing import COMPANY_LIMIT, LIMIT_KEY
        from ...tiers import JOB_TOKEN_CAP

        tiers = db.get_setting("runner_tiers", {}) or {}
        pacing = db.get_setting(LIMIT_KEY, {}) or {}
        return {
            "job_token_cap": int(tiers.get("job_token_cap") or JOB_TOKEN_CAP),
            "company_limit": int(pacing.get("limit") or COMPANY_LIMIT),
        }

    @r.post("/api/system/restart")
    async def restart():
        """Apply startup-only settings (worker counts): stop admitting new starts, let a final submit finish, then exit
        so whatever owns this process starts it again (compose restart: unless-stopped, or the native supervisor).
        Running drafts resume after the restart."""
        import asyncio
        import os
        import time

        from sqlalchemy import func, select

        from ...db import Run, now

        hold = "execution_v2_deployment_hold"
        db.set_setting(hold, {"at": now(), "stage": "draining", "reason": "restart from Setup", "updated_at": now()})
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            with db.session() as s:
                if not s.scalar(select(func.count()).select_from(Run).where(Run.state == "submitting")):
                    break
            await asyncio.sleep(2)
        db.set_setting(hold, {"at": now(), "stage": "complete", "reason": "restart from Setup", "updated_at": now()})
        db.flush_events()  # os._exit skips the exit hook that would write a remote database's batched events.
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"restarting": True}

    @r.get("/api/legal")
    def legal_status():
        """Whether the Terms and Privacy Policy of this version were accepted, and their text."""
        from ... import legal

        return {**legal.status(db), "terms": legal.text("terms"), "privacy": legal.text("privacy")}

    @r.post("/api/legal/accept")
    def legal_accept(data: LegalAccept):
        from ... import legal

        try:
            return legal.accept(db, data.version, data.adult)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @r.get("/api/system/erase")
    def erase_status():
        from ... import erase

        return {"erasing": erase.running(config.data_dir), "phrase": erase.PHRASE, "cannot_remove": erase.CANNOT}

    @r.post("/api/system/erase", status_code=202)
    async def erase_everything(data: Erase):
        """Delete everything this installation holds and return it to its first run. A separate process does the deleting after this
        one has exited (erase.py), so no worker outlives its data. Only the exact typed phrase starts it."""
        import asyncio
        import os

        from ... import erase

        if data.confirm.strip() != erase.PHRASE:
            raise HTTPException(422, f"Type {erase.PHRASE} to confirm.")
        try:
            await asyncio.to_thread(erase.begin, config, db)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        # Work stops with the process: whatever was running is not left to finish against data that is about to go.
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"erasing": True, "cannot_remove": erase.CANNOT}

    @r.put("/api/limits")
    def put_limits(data: Limits):
        from ...application_pacing import LIMIT_KEY

        tiers = dict(db.get_setting("runner_tiers", {}) or {})
        tiers["job_token_cap"] = data.job_token_cap
        db.set_setting("runner_tiers", tiers)
        pacing = dict(db.get_setting(LIMIT_KEY, {}) or {})
        pacing.update(limit=data.company_limit, reason="Set by the owner in Setup › System")
        db.set_setting(LIMIT_KEY, pacing)
        return get_limits()

    def platform_view():
        from sqlalchemy import func, select

        from ...db import Job

        focus = db.get_setting("ats_focus", {}) or {}
        exclude = set(focus.get("exclude") or [])
        with db.session() as s:
            counts = {
                (ats, status): n
                for ats, status, n in s.execute(
                    select(Job.ats, Job.status, func.count()).where(Job.demo.is_(False)).group_by(Job.ats, Job.status)
                )
            }
        names = sorted({ats for ats, _ in counts if ats} | exclude)
        return {
            "platforms": [
                {
                    "ats": ats,
                    "included": ats not in exclude,
                    "ready": counts.get((ats, "ready"), 0),
                    "confirmed": counts.get((ats, "confirmed"), 0),
                }
                for ats in names
            ],
            "reason": focus.get("reason", ""),
            "updated_at": focus.get("updated_at", ""),
        }

    @r.get("/api/platforms")
    def get_platforms():
        """Which application platforms the autopilot uses (Setup › System)."""
        return platform_view()

    @r.put("/api/platforms")
    def put_platforms(data: Platforms):
        from ...db import now

        include = {name.strip().lower() for name in data.include if name.strip()}
        known = {p["ats"] for p in platform_view()["platforms"]}
        unknown = sorted(include - known)
        if unknown:
            raise HTTPException(422, "Unknown platform: " + ", ".join(unknown))
        focus = dict(db.get_setting("ats_focus", {}) or {})
        before = set(focus.get("exclude") or [])
        exclude = sorted(known - include)
        change = {"at": now()[:10], "included": ", ".join(sorted(include & before)) or None,
                  "excluded": ", ".join(sorted(set(exclude) - before)) or None,
                  "why": data.reason or "Set by the owner"}
        focus.update(exclude=exclude, reason=data.reason or "Set by the owner", updated_at=now(),
                     changes=[*(focus.get("changes") or [])[-30:], {k: v for k, v in change.items() if v}])
        db.set_setting("ats_focus", focus)
        return platform_view()

    @r.get("/api/agents")
    def get_agents():
        from ...agent_settings import summary

        return summary(db, config)

    @r.put("/api/agents")
    def put_agents(data: AgentSettings):
        from ...agent_settings import save

        try:
            saved = save(db, config, data)
            # Ranking, Telegram and the checks before a run read the main model's key: with another provider chosen here and none
            # saved for the old one, the board never ranked a job.
            if data.models.answers != config.llm_provider:
                save_values({"llm_provider": data.models.answers})
            return saved
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @r.put("/api/control")
    async def control(data: Control):
        from ... import stop

        if data.auto_submit is not None:

            def write():
                with db.exclusive() as s:
                    row = s.get(Setting, "control")
                    if row:
                        row.value = {**(row.value or {}), "auto_submit": data.auto_submit}
                    else:
                        s.add(Setting(key="control", value={"paused": False, "auto_submit": data.auto_submit}))

            await asyncio.to_thread(write)
        if data.paused is True:
            await asyncio.to_thread(stop.press, db)
        elif data.paused is False:
            await stop.resume(service)
        value = await asyncio.to_thread(db.get_setting, "control", {})
        return {"paused": bool(value.get("paused")), "auto_submit": bool(value.get("auto_submit"))}

    @r.get("/api/stop")
    def stop_status():
        """What the Stop control shows, on every screen: the sentence, what is finishing and its countdown."""
        from ... import stop

        return stop.status(db)

    @r.post("/api/stop")
    def stop_everything():
        """Stop: nothing new starts from any path, running applications close, and it holds until `POST /api/resume`."""
        from ... import stop

        stop.press(db)
        return stop.status(db)

    @r.post("/api/resume")
    async def resume_everything():
        from ... import stop

        return await stop.resume(service)

    return r
