"""Owner-adjustable settings: global switches, spending and pacing limits, model providers."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...agent_settings import AgentSettings


class Control(BaseModel):
    paused: bool
    auto_submit: bool


class Limits(BaseModel):
    job_token_cap: int = Field(ge=50_000, le=2_000_000)
    company_limit: int = Field(ge=1, le=25)


def router(service) -> APIRouter:
    db, config = service.db, service.config
    r = APIRouter()

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
