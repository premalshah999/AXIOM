"""TypeSafe Jev: typed decisions (pick one option) with calibrated confidence.

Jev answers bounded questions only; DeepSeek keeps free text and every decision Jev is
unsure about. Calls share the daily model budget and are recorded as model_call events
(provider "typesafe"). No state or answer text is ever logged.
"""

import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .db import Budget, Run

NOT_STATED = "__not_stated__"


def settings(db, config):
    """Owner switch: setting `jev` = {"enabled": bool, "min_confidence": float}; on when a key exists."""
    value = db.get_setting("jev", {}) or {}
    enabled = bool(config.typesafe_api_key) and value.get("enabled", True) is not False
    floor = value.get("min_confidence", config.jev_min_confidence)
    try:
        floor = min(0.99, max(0.5, float(floor)))
    except (TypeError, ValueError):
        floor = config.jev_min_confidence
    return enabled, floor


def _cost(config, tokens):
    return tokens * config.jev_input_price / 1_000_000


def _record(db, config, run_id, purpose, day, reserve, actual, data):
    with db.exclusive() as s:
        budget = s.get(Budget, day)
        if budget:
            budget.spent = max(0, budget.spent + actual - reserve)
        if run_id and (run := s.get(Run, run_id)):
            run.cost = max(0, run.cost + actual - reserve)
    if run_id:
        db.event(
            run_id,
            "model_call",
            "Decision model request",
            {
                "purpose": purpose,
                "provider": "typesafe",
                "model": config.jev_model,
                "thinking": "disabled",
                "completion_tokens": 0,
                "finish_reason": None,
                "reasoning_tokens": None,
                **data,
            },
        )


async def decide(db, config, run_id, purpose, state, questions, *, timeout=10.0):
    """POST /v1/systemone. Returns {question_id: answer} or None on any failure (caller falls back)."""
    if not questions:
        return {}
    payload = json.dumps({"model": config.jev_model, "state": state, "questions": questions}, default=str)
    reserve = _cost(config, len(payload))  # serialized bytes >= tokens: conservative reservation
    day = datetime.now(ZoneInfo(config.timezone)).date().isoformat()
    with db.exclusive() as s:
        budget = s.get(Budget, day)
        if not budget:
            budget = Budget(day=day, spent=0, submissions=0)
            s.add(budget)
        if budget.spent + reserve > config.daily_budget_usd:
            return None
        budget.spent += reserve
        if run_id and (run := s.get(Run, run_id)):
            run.cost += reserve
    started = time.monotonic()
    status, body = None, {}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(3):
                response = await client.post(
                    config.typesafe_base_url.rstrip("/") + "/v1/systemone",
                    content=payload,
                    headers={
                        "Authorization": "Bearer " + config.typesafe_api_key,
                        "Content-Type": "application/json",
                    },
                )
                status = response.status_code
                if status in {429, 529} and attempt < 2:
                    await asyncio.sleep(
                        min(4.0, float(response.headers.get("retry-after") or 0.5 * 2**attempt))
                    )
                    continue
                break
        if status == 200:
            body = response.json()
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
        _record(
            db,
            config,
            run_id,
            purpose,
            day,
            reserve,
            reserve,
            {
                "outcome": "transport_error",
                "error_type": type(exc).__name__,
                "status": status,
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "billing": "reservation_retained",
            },
        )
        return None
    usage = body.get("usage") or {}
    tokens = usage.get("input_tokens")
    actual = _cost(config, tokens) if isinstance(tokens, int) else reserve
    _record(
        db,
        config,
        run_id,
        purpose,
        day,
        reserve,
        actual if status == 200 else 0,
        {
            "outcome": "completed" if status == 200 else "http_error",
            "status": status,
            "prompt_tokens": tokens,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "question_count": len(questions),
        },
    )
    if status != 200:
        return None
    answers = body.get("answers")
    return answers if isinstance(answers, dict) else None


def choice_question(instructions, options):
    """One-of-N with an explicit 'not stated' escape, so missing information is reported, not guessed."""
    criteria = {str(o): None for o in options}
    criteria[NOT_STATED] = "The information provided does not answer this question"
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def accepted(answer, floor):
    """The chosen option when Jev is confident and the choice is a real option, else None."""
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        return None
    choice, confidence = answer.get("choice"), answer.get("confidence")
    if choice in (None, NOT_STATED) or not isinstance(confidence, (int, float)) or confidence < floor:
        return None
    probabilities = answer.get("probabilities") or {}
    if probabilities.get(NOT_STATED, 0) >= 0.15:
        return None  # A material chance the facts do not answer it: let the reasoning model decide.
    return choice
