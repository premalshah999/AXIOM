"""Owner-adjustable settings: global switches, spending and pacing limits, model providers."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agent_settings import AgentSettings


class Control(BaseModel):
    paused: bool
    auto_submit: bool


class Limits(BaseModel):
    job_token_cap: int = Field(ge=50_000, le=50_000_000)
    company_limit: int = Field(ge=1, le=25)


class Platforms(BaseModel):
    """The platforms the autopilot applies on; every other platform is excluded (the owner's own Apply still works)."""

    include: list[str] = Field(min_length=1, max_length=60)
    reason: str = Field(default="", max_length=300)


class ConnectionsUpdate(BaseModel):
    """Values to save in Setup > Connections; a blank value clears that saved value."""

    values: dict[str, str] = Field(max_length=20)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

    @r.get("/api/connections")
    def connections_status():
        from ... import connections

        return connections.status(config)

    @r.put("/api/connections")
    def save_connections(data: ConnectionsUpdate):
        from ... import connections
        from ...config import settings

        try:
            connections.save(config, data.values)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        cached = settings()
        if cached is not config:
            connections.apply(cached)  # Code that reads settings() directly sees the same keys at once.
        return connections.status(config)

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
        so the container restarts (compose restart: unless-stopped). Running drafts resume after the restart."""
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
        asyncio.get_running_loop().call_later(1.0, os._exit, 0)
        return {"restarting": True}

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
    async def put_agents(data: AgentSettings):
        from ...agent_settings import save

        try:
            return save(db, config, data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @r.put("/api/control")
    async def control(data: Control):
        db.set_setting("control", data.model_dump())
        if not data.paused and service.dispatch:
            from ...workers import resume

            await resume(service)
        return data

    return r
