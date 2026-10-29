"""Model clients share priority admission, metering and hard budgets."""

import json
import re
import asyncio
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import case, select, update

from .db import Budget, Event, Run
from .model_admission import admission, priority
from . import provider_health

_deadline = ContextVar("model_deadline", default=None)
_request_sent = ContextVar("model_request_sent", default=None)


# The service tiers the OpenAI SDK's ChatCompletion accepts (plus none).
OPENAI_SERVICE_TIERS = {None, "auto", "default", "flex", "scale", "priority"}
# Model calls with no run: ranking's, and any other background work. They were recorded nowhere (0 model_call events
# for ClassifiedFit/RankedBatch), so the cost of ranking could not be measured (2026-10-29). One event per call, filed
# under a fixed id so a run's own events stay what they were.
RANKING_PURPOSES = {"ClassifiedFit", "RankedBatch"}


def call_owner(run_id, purpose):
    """The `events.run_id` a model call is recorded under: its run, else "ranking" or "background"."""
    return run_id or ("ranking" if purpose in RANKING_PURPOSES else "background")


# Observation-only calls: their failure must never park the application's other model work. Sixty-five runs on
# 2026-10-28 stopped with "model provider unavailable"; the vision endpoint's 404s (no image-capable host, 25 in 3 hours)
# were counted as provider failures and opened the shared five-minute cooldown.
OPTIONAL_PURPOSES = {"browser_vision"}

# Calls whose answer depends only on their prompt: the same question on the same facts again within a run (Avature's
# country-code picker looped 84 identical AnswerBatch calls, 2026-10-28) returns the earlier answer instead of paying
# for it. Decision loops (Navigation, Control) are excluded: the page state they react to is in the prompt anyway, but
# a repeated identical prompt there is a stuck loop the call limit must still bound.
REUSABLE_PURPOSES = {"AnswerBatch", "Inference", "AnswerQualityAudit", "EvidenceEquivalence", "RoleBrief"}
REUSE_TTL = 900
REUSE_PER_RUN = 48
_reuse: dict = {}


def reuse_key(purpose, run_id, instructions, prompt):
    import hashlib

    return run_id, purpose, hashlib.sha256((str(instructions) + "\0" + str(prompt)).encode()).hexdigest()


def cache_hits(usage):
    """Cached input tokens: DeepSeek's prompt_cache_hit_tokens, or the OpenAI/OpenRouter prompt_tokens_details."""
    return int(usage.get("prompt_cache_hit_tokens") or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)


def token_cost(config, input_tokens, output_tokens, cache_hit_tokens=0):
    # Haiku 5.5 charges five times its base rate above 100k input tokens.
    # Reservations use serialized bytes as a conservative token upper bound.
    tier = (
        5
        if (
            config.llm_provider == "haiku"
            and config.llm_model == "claude-haiku-5-5"
            and input_tokens > 100_000
        )
        else 1
    )
    hits = min(max(int(cache_hit_tokens or 0), 0), input_tokens)
    return (
        tier
        * (
            (input_tokens - hits) * config.llm_input_price
            + hits * config.llm_cache_price
            + output_tokens * config.llm_output_price
        )
        / 1_000_000
    )


def provider_rejection(response, content):
    """Retain categories and numeric retry guidance, never a provider message."""
    category = "http_error"
    if response.status_code == 402:
        # Payment Required: the provider account is out of credit (DeepSeek "Insufficient Balance", 2026-10-25).
        category = "quota_exhausted"
    elif response.status_code in {400, 429}:
        try:
            error = json.loads(content).get("error", {})
            text = str(error).casefold()
        except (ValueError, TypeError, AttributeError):
            text = ""
        if re.search(
            r"insufficient[_ ](?:quota|balance|credit)|(?:balance|credits?).{0,25}(?:exhausted|insufficient)"
            r"|balance.{0,25}too low|余额不足|额度不足",
            text,
        ):
            category = "quota_exhausted"
        elif response.status_code == 429:
            category = "rate_limited"
        else:
            # A bad request for this call is not evidence that the provider is down.
            category = "client_error"
    retry_after = None
    if category == "rate_limited":
        value = response.headers.get("retry-after", "")
        try:
            retry_after = max(1, min(3600, float(value)))
        except ValueError:
            from email.utils import parsedate_to_datetime
            from datetime import UTC, datetime

            try:
                retry_after = max(
                    1, min(3600, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
                )
            except (ValueError, TypeError, OverflowError):
                pass
    return {"rejection_category": category, "retry_after_seconds": retry_after}


class BudgetExceeded(RuntimeError):
    @classmethod
    def caused_by(cls, exc):
        return _caused_by(exc, cls)


def _caused_by(exc, kind):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, kind):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


class AnswerModelUnavailable(RuntimeError):
    """A provider failure cannot be repaired by trying another form control."""

    @classmethod
    def raise_for(cls, answer):
        if answer.blocker_type == "site_transient" and answer.reason.startswith(
            "Answer model could not complete:"
        ):
            if answer.reason.endswith(AnswerModelInvalidResponse.__name__):
                raise AnswerModelInvalidResponse()
            raise cls()

    @classmethod
    def caused_by(cls, exc):
        # Provider SDKs wrap transport exceptions; inspect types, never private messages.
        return _caused_by(exc, cls)


class AnswerModelInvalidResponse(AnswerModelUnavailable):
    """The provider answered HTTP 200 but its reply is not a readable completion (Sail Research's service_tier,
    2026-10-28). The provider is up, so this is not an outage on its first sighting; repeated, it opens the cooldown
    (provider_health.note_invalid_response) with its own reason instead of parked runs retrying every 1-2 minutes."""


class AnswerModelTimeout(AnswerModelUnavailable):
    """An isolated slow request may be retried without replaying browser actions."""


class AnswerModelConnectionError(AnswerModelUnavailable):
    """A connection failed before a model response; bounded decision regeneration is safe."""


@asynccontextmanager
async def model_deadline(timeout):
    """Keep the whole-call deadline; transport expires first while actually sending."""
    token = _deadline.set(time.monotonic() + timeout)
    sent = {"sent": False}
    sent_token = _request_sent.set(sent)
    try:
        async with asyncio.timeout(timeout):
            yield
    except TimeoutError:
        if not sent["sent"]:
            # Admission, validation or a local lock used the deadline before any request left.
            # That is local congestion, not a provider outage: do not park the run as one.
            raise
        raise AnswerModelTimeout() from None
    except Exception as exc:
        if BudgetExceeded.caused_by(exc):
            raise BudgetExceeded("Model budget reached") from None
        if AnswerModelTimeout.caused_by(exc):
            raise AnswerModelTimeout() from None
        if AnswerModelConnectionError.caused_by(exc):
            raise AnswerModelConnectionError() from None
        if AnswerModelInvalidResponse.caused_by(exc):
            raise AnswerModelInvalidResponse() from None
        if AnswerModelUnavailable.caused_by(exc):
            raise AnswerModelUnavailable() from None
        raise
    finally:
        _request_sent.reset(sent_token)
        _deadline.reset(token)


def effective_thinking(db, config, thinking=None):
    """Apply the selected provider's runtime preference even to older callers."""
    if config.llm_provider in {"haiku", "openai", "deepseek", "openrouter"}:
        # OpenAI's effort is its own parameter (openai_reasoning_effort), not a thinking switch. DeepSeek never reasons
        # (owner 2026-10-27): its non-thinking mode answers as well here and keeps the cached prefix cheap.
        return "disabled"
    override = db.get_setting("model_reasoning", {}) or {}
    provider_preference = override.get(config.llm_provider + "_thinking")
    if provider_preference in {"enabled", "disabled"}:
        return provider_preference
    if config.llm_provider == "deepseek":
        if override.get("thinking") in {"enabled", "disabled"}:
            return override["thinking"]
    return thinking if thinking is not None else config.llm_thinking


def effective_timeout(db, config, thinking, timeout, *, purpose="unspecified"):
    """Provider-specific allowances apply to both HTTP and whole-call deadlines."""
    override = db.get_setting("model_reasoning", {}) or {}
    if config.llm_provider == "mimo" and purpose != "Explanation":
        floor = override.get("mimo_min_timeout_seconds")
        if isinstance(floor, (int, float)) and not isinstance(floor, bool) and 1 <= floor <= 60:
            timeout = max(timeout, floor)
    elif config.llm_provider == "deepseek" and thinking == "enabled":
        floor = override.get("min_timeout_seconds")
        if floor:
            timeout = max(timeout, int(floor))
    return timeout


class MeteredTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, db, config, run_id=None, inner=None, thinking=None, purpose="unspecified", timeout=25, tier=1
    ):
        self.db, self.config, self.run_id, self.tier = db, config, run_id, tier
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.thinking = effective_thinking(db, config, thinking)
        if tier >= 2 and config.llm_provider == "mimo":
            # A failed application's stronger runner reasons even where the global
            # default is reasoning off (that override applies to tier 1).
            self.thinking = "enabled"
        self.purpose = purpose
        self.timeout = timeout

    async def handle_async_request(self, request):
        if not any(path in request.url.path for path in ("/chat/completions", "/messages")):
            return await self.inner.handle_async_request(request)
        async with admission.slot(
            self.purpose,
            self.run_id,
            limit=self.config.model_request_concurrency,
            background_limit=self.config.model_background_concurrency,
        ):
            if priority(self.purpose, self.run_id) == 3 or self.purpose in OPTIONAL_PURPOSES:
                # Optional advice has a shorter deadline and cannot establish
                # application-provider health or consume its recovery request.
                if provider_health.status(self.db, self.config)["state"] != "ready":
                    raise AnswerModelUnavailable()
                ticket = None
            else:
                ticket = provider_health.acquire(self.db, self.config, self.timeout)
                if ticket is None:
                    raise AnswerModelUnavailable()
            try:
                return await self._completion(request, ticket)
            finally:
                if ticket is not None:
                    provider_health.release(self.db, self.config, ticket)

    async def _completion(self, request, ticket):
        body = await request.aread()
        args = json.loads(body or b"{}")
        anthropic = self.config.llm_provider == "haiku"
        # JSON mode is documented by MiMo. Validate schemas in Python, not via unsupported strict mode.
        if args.get("response_format", {}).get("type") == "json_schema":
            args["response_format"] = {"type": "json_object"}
        deepseek = request.url.host == "api.deepseek.com"
        use_openai = self.config.llm_provider == "openai"
        openrouter = request.url.host == "openrouter.ai"
        # Applicant directive (2026-10-08): DeepSeek reasons at maximum effort. The deployment
        # .env stays untouched; the `model_reasoning` setting overrides it at runtime.
        override = self.db.get_setting("model_reasoning", {}) or {}
        from .tiers import effort_for

        effort = effort_for(
            self.tier, override.get("deepseek_effort") or self.config.deepseek_reasoning_effort
        )
        if deepseek and self.thinking == "enabled":
            args["reasoning_effort"] = effort
        elif use_openai:
            # The cheapest setting almost always; a stronger retry tier gets "low", never more.
            args["reasoning_effort"] = "low" if self.tier >= 2 else (self.config.openai_reasoning_effort or "none")
        else:
            args.pop("reasoning_effort", None)
        args.pop("frequency_penalty", None)
        # Thinking is on by default for MiMo v2.6 and shares the completion budget with the JSON.
        if openrouter:
            # OpenRouter's own switches: reasoning off, and only the configured host (Relace) serves the request.
            args["reasoning"] = {"enabled": False}
            if self.config.openrouter_provider:
                from .config import OPENROUTER_HOSTS

                order = [self.config.openrouter_provider]
                if self.config.openrouter_fallback_hosts:
                    # The other offered hosts of the same model, cheapest output first, only when the chosen one fails.
                    order += sorted((h for h in OPENROUTER_HOSTS if h not in order), key=lambda h: OPENROUTER_HOSTS[h][1])
                args["provider"] = {"order": order, "allow_fallbacks": bool(self.config.openrouter_allow_fallbacks)}
        elif self.thinking in {"enabled", "disabled"} and not use_openai:
            args["thinking"] = {"type": self.thinking}
        if self.thinking == "enabled" or anthropic or use_openai:
            # MiMo's deep-thinking endpoint rejects sampling overrides.
            args.pop("temperature", None)
            args.pop("top_p", None)
        max_tokens = args.pop("max_completion_tokens", args.pop("max_tokens", 2400))
        if deepseek and self.thinking == "enabled" and override.get("min_max_tokens"):
            # Reasoning shares the completion budget; a deeper effort must not truncate the JSON.
            max_tokens = max(int(max_tokens), int(override["min_max_tokens"]))
        if deepseek and self.thinking == "enabled" and self.tier >= 2:
            from .tiers import token_floor

            max_tokens = max(int(max_tokens), token_floor(self.tier))
        args["max_tokens" if deepseek or anthropic or openrouter else "max_completion_tokens"] = max_tokens
        body = json.dumps(args).encode()
        headers = dict(request.headers)
        headers.pop("content-length", None)
        request = httpx.Request(
            request.method, request.url, headers=headers, content=body, extensions=request.extensions
        )
        # Conservative upper reservation: serialized bytes >= usual token count, including screenshots.
        reserve = token_cost(self.config, len(body), max_tokens)
        day = datetime.now(ZoneInfo(self.config.timezone)).date().isoformat()
        with self.db.exclusive() as s:
            budget = s.get(Budget, day)
            if not budget:
                budget = Budget(day=day, spent=0, submissions=0)
                s.add(budget)
            # Columns only: s.get(Run) decoded the whole packet on the shared loop before every model call.
            run = (
                s.execute(
                    select(Run.model_calls, Run.packet["worker_pool"].as_string().label("pool")).where(
                        Run.id == self.run_id
                    )
                ).first()
                if self.run_id
                else None
            )
            if budget.spent + reserve > self.config.daily_budget_usd:
                raise BudgetExceeded("Daily model budget reached")
            from .tiers import call_limit

            if run and run.model_calls >= call_limit(self.config, self.tier, run.pool):
                raise BudgetExceeded("Application model-call limit reached")
            budget.spent += reserve
            if run:
                s.execute(
                    update(Run)
                    .where(Run.id == self.run_id)
                    .values(cost=Run.cost + reserve, model_calls=Run.model_calls + 1)
                )
        # On transport failure retain the reservation conservatively: billing outcome is unknown.
        started = time.monotonic()
        try:
            remaining = min(self.timeout, (_deadline.get() or (started + self.timeout)) - started)
            async with asyncio.timeout(max(0.001, remaining - 0.01)):
                if (sent := _request_sent.get()) is not None:
                    sent["sent"] = True
                response = await self.inner.handle_async_request(request)
                content = await response.aread()
        except BaseException as exc:
            # asyncio's hard deadline cancels the request. Record that time too;
            # completed responses alone conceal the most expensive stalls.
            self.db.event(
                call_owner(self.run_id, self.purpose),
                "model_call",
                "Model request did not complete",
                {
                    "purpose": self.purpose,
                    "model": args.get("model"),
                    "thinking": self.thinking,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                    "outcome": "cancelled"
                    if isinstance(exc, asyncio.CancelledError)
                    else "timeout"
                    if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                    else "transport_error",
                    "error_type": type(exc).__name__,
                    "status": None,
                    "billing": "reservation_retained",
                },
            )
            if isinstance(exc, (TimeoutError, httpx.TransportError)):
                if ticket is not None:
                    provider_health.finish(
                        self.db,
                        self.config,
                        ticket,
                        failure="timeout"
                        if isinstance(exc, (TimeoutError, httpx.TimeoutException))
                        else "transport_error",
                    )
                if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
                    raise AnswerModelTimeout() from None
                if isinstance(exc, httpx.ConnectError):
                    raise AnswerModelConnectionError() from None
                raise AnswerModelUnavailable() from None
            raise
        completion = {}
        try:
            completion = json.loads(content)
            usage = completion.get("usage", {})
            finish_reason = (
                completion.get("stop_reason")
                if anthropic
                else (completion.get("choices") or [{}])[0].get("finish_reason")
            )
            actual = token_cost(
                self.config,
                usage["input_tokens"] if anthropic else usage["prompt_tokens"],
                usage["output_tokens"] if anthropic else usage["completion_tokens"],
                0 if anthropic else cache_hits(usage),
            )
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            # A reply that is not a completion (choices a string, usage a list) is the SDK's and pydantic-ai's to refuse,
            # as an unreadable reply; an AttributeError here surfaced as "Connection error".
            usage = {}
            finish_reason = None
            actual = reserve
        with self.db.exclusive() as s:
            budget = s.get(Budget, day)
            budget.spent = max(0, budget.spent + actual - reserve)
            # An UPDATE, not s.get(Run): loading the row decoded its whole packet on the shared loop per model call.
            delta = actual - reserve
            if not self.run_id or s.execute(
                update(Run)
                .where(Run.id == self.run_id)
                .values(cost=case((Run.cost + delta < 0, 0), else_=Run.cost + delta))
            ).rowcount:
                s.add(
                    Event(
                        run_id=call_owner(self.run_id, self.purpose),
                        kind="model_call",
                        message="Model request completed",
                        data={
                            "purpose": self.purpose,
                            "outcome": "completed" if response.is_success else "http_error",
                            "model": args.get("model"),
                            "thinking": self.thinking,
                            "elapsed_seconds": round(time.monotonic() - started, 2),
                            "status": response.status_code,
                            "provider": self.config.llm_provider,
                            "prompt_tokens": usage.get("input_tokens" if anthropic else "prompt_tokens"),
                            # DeepSeek bills cached input at a fraction; record it to see real spend.
                            "cache_hit_tokens": cache_hits(usage) if usage else None,
                            # OpenRouter reports what it billed for this request.
                            **({"billed_usd": usage["cost"]} if openrouter and "cost" in usage else {}),
                            **({"host": completion.get("provider")} if openrouter and completion.get("provider") else {}),
                            "completion_tokens": usage.get(
                                "output_tokens" if anthropic else "completion_tokens"
                            ),
                            "finish_reason": finish_reason,
                            "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get(
                                "reasoning_tokens"
                            ),
                            **(provider_rejection(response, content) if response.status_code >= 400 else {}),
                        },
                    )
                )
        if response.status_code >= 400:
            if ticket is not None:
                rejection = provider_rejection(response, content)
                provider_health.finish(
                    self.db,
                    self.config,
                    ticket,
                    failure=rejection["rejection_category"],
                    retry_after=rejection["retry_after_seconds"],
                )
            raise AnswerModelUnavailable() from None
        if ticket is not None:
            provider_health.finish(self.db, self.config, ticket)
        if openrouter and response.is_success and completion.get("service_tier") not in OPENAI_SERVICE_TIERS:
            # A host's own tier name (Sail Research, 2026-10-28) is outside the OpenAI response model's five, so every
            # reply failed validation and the run parked as "model provider unavailable". The tier is billing detail.
            completion.pop("service_tier", None)
            headers = [
                (k, v) for k, v in response.headers.items() if k.lower() not in {"content-length", "content-encoding"}
            ]
            response = httpx.Response(
                response.status_code,
                headers=headers,
                content=json.dumps(completion).encode(),
                request=request,
                extensions=response.extensions,
            )
        return response

    async def aclose(self):
        await self.inner.aclose()


def http_client(db, config, run_id=None, *, thinking=None, timeout=25, purpose="unspecified"):
    from .tiers import apply_model

    thinking = effective_thinking(db, config, thinking)
    timeout = effective_timeout(db, config, thinking, timeout, purpose=purpose)
    tier, thinking, timeout = apply_model(db, config, run_id, thinking, timeout)
    return httpx.AsyncClient(
        transport=MeteredTransport(
            db, config, run_id, thinking=thinking, purpose=purpose, timeout=timeout, tier=tier
        ),
        timeout=timeout,
    )


async def structured(*args, **kwargs):
    """Retry one isolated timeout/connection failure, sharing two extra requests per run.

    Every attempt goes through admission and budget reservation again. A retry
    only regenerates a model decision; it never repeats a browser action.
    """
    db = args[1] if len(args) > 1 else kwargs["db"]
    run_id = args[5] if len(args) > 5 else kwargs.get("run_id")
    output_type = args[2] if len(args) > 2 else kwargs.get("output_type")
    purpose = getattr(output_type, "__name__", "")
    key = None
    if run_id and purpose in REUSABLE_PURPOSES:
        key = reuse_key(purpose, run_id, args[3] if len(args) > 3 else kwargs.get("instructions"),
                        args[4] if len(args) > 4 else kwargs.get("prompt"))
        hit = _reuse.get(key)
        if hit and time.monotonic() - hit[0] < REUSE_TTL:
            db.event(run_id, "model_reuse", "Reused this run's earlier answer to an identical request", {"purpose": purpose})
            return hit[1].model_copy(deep=True)
    result = await _structured_with_retry(db, run_id, args, kwargs)
    if key is not None and hasattr(result, "model_copy"):
        now = time.monotonic()
        for old in [k for k, (at, _) in _reuse.items() if now - at > REUSE_TTL]:
            del _reuse[old]
        mine = sorted((v[0], k) for k, v in _reuse.items() if k[0] == run_id)
        for _, old in mine[: max(0, len(mine) - REUSE_PER_RUN + 1)]:
            del _reuse[old]
        _reuse[key] = (time.monotonic(), result.model_copy(deep=True))
    return result


async def _structured_with_retry(db, run_id, args, kwargs):
    from sqlalchemy import func

    for attempt in range(2):
        try:
            return await _structured(*args, **kwargs)
        except Exception as exc:
            transient = AnswerModelTimeout.caused_by(exc) or AnswerModelConnectionError.caused_by(exc)
            if attempt or not transient:
                raise
            if run_id:
                with db.exclusive() as session:
                    used = session.scalar(select(func.count()).select_from(Event).where(
                        Event.run_id == run_id, Event.kind == "model_timeout_retry"
                    ))
                    if used >= 2:
                        raise
                    session.add(Event(
                        run_id=run_id, kind="model_timeout_retry",
                        message="Retrying an isolated model transport failure",
                        data={"attempt": used + 1, "category": "connection" if AnswerModelConnectionError.caused_by(exc) else "timeout"},
                    ))
            await asyncio.sleep(0.25)


def unreadable_reply(exc):
    """Whether pydantic-ai refused the provider's HTTP 200 reply as not a chat completion ("Invalid response from ...
    chat completions endpoint"), as opposed to an output that failed the caller's schema after retries."""
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, UnexpectedModelBehavior) and str(getattr(exc, "message", "") or exc).startswith(
            "Invalid response from"
        ):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


async def _structured(
    config,
    db,
    output_type,
    instructions,
    prompt,
    run_id=None,
    max_tokens=2400,
    *,
    thinking=None,
    timeout=25,
    retries=1,
):
    from .agent_settings import for_role, require

    purpose = output_type.__name__
    navigation = purpose in {
        "NavigationDecision",
        "ControlDecision",
        "OptionDecision",
        "RecoveryDecision",
        "ApplicationDecision",
        "RepairPlan",
    }
    require(db, "navigator" if navigation else "answers")
    config = for_role(db, config, "navigator" if navigation else "answers")
    from .tiers import apply_model, provider_for, tier_of

    if tier_of(db, run_id) >= 2:
        config = provider_for(db, config, tier_of(db, run_id))
    if not config.llm_api_key:
        from .agent_settings import missing_key_message

        raise RuntimeError(missing_key_message(config.llm_provider))
    thinking = effective_thinking(db, config, thinking)
    timeout = effective_timeout(db, config, thinking, timeout, purpose=output_type.__name__)
    _, thinking, timeout = apply_model(db, config, run_id, thinking, timeout)
    from openai import AsyncOpenAI
    from pydantic_ai import Agent, PromptedOutput
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    if priority(output_type.__name__, run_id) != 3:
        # Before the call deadline starts: a blip that clears within 90 s should not park the run.
        await provider_health.wait_out_short_cooldown(db, config)
    async with http_client(
        db, config, run_id, thinking=thinking, timeout=timeout, purpose=output_type.__name__
    ) as client:
        if config.llm_provider == "haiku":
            from anthropic import AsyncAnthropic

            from pydantic import ValidationError

            sdk = AsyncAnthropic(
                api_key=config.llm_api_key, base_url=config.llm_base_url, http_client=client, max_retries=0
            )
            messages = [{"role": "user", "content": prompt}]
            # Same bounded schema repair the other providers get from pydantic-ai:
            # one malformed reply must not end an application.
            for attempt in range(retries + 1):
                async with model_deadline(timeout):
                    result = await sdk.messages.create(
                        model=config.llm_model,
                        max_tokens=max_tokens,
                        system=instructions
                        + "\nReturn only a JSON object matching this schema:\n"
                        + json.dumps(output_type.model_json_schema()),
                        messages=messages,
                        thinking={"type": "disabled"},
                    )
                content = "".join(part.text for part in result.content if part.type == "text").strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                if not content.startswith("{") and "{" in content and "}" in content:
                    content = content[content.index("{") : content.rindex("}") + 1]
                try:
                    return output_type.model_validate_json(content)
                except ValidationError as exc:
                    issues = [
                        {"type": e["type"], "field": list(e["loc"]), "message": e["msg"]}
                        for e in exc.errors(include_input=False, include_context=False)[:8]
                    ]
                    if run_id:
                        db.event(
                            run_id,
                            "model_output",
                            "Model response did not match the action schema",
                            {"schema": purpose, "issues": issues, "attempt": attempt + 1},
                        )
                    if attempt >= retries:
                        raise
                    messages += [
                        {"role": "assistant", "content": content or "{}"},
                        {
                            "role": "user",
                            "content": "That JSON did not match the schema: "
                            + json.dumps(issues)
                            + ". Return only the corrected JSON object.",
                        },
                    ]
        else:
            sdk = AsyncOpenAI(
                api_key=config.llm_api_key, base_url=config.llm_base_url, http_client=client, max_retries=0
            )
            model = OpenAIChatModel(config.llm_model, provider=OpenAIProvider(openai_client=sdk))
        agent = Agent(
            model,
            output_type=PromptedOutput(output_type),
            instructions=instructions,
            retries=retries,
            model_settings={
                **({} if config.llm_provider == "haiku" else {"temperature": 0.1}),
                "max_tokens": max_tokens,
                "timeout": timeout,
            },
        )
        # HTTP read timeouts reset on each received chunk. A provider can spend
        # minutes reasoning while emitting keepalives; enforce the whole call.
        try:
            async with model_deadline(timeout):
                result = await agent.run(prompt)
        except Exception as exc:
            from pydantic import ValidationError

            cause, seen, issues = exc, set(), []
            while cause is not None and id(cause) not in seen:
                seen.add(id(cause))
                if isinstance(cause, ValidationError):
                    issues = [
                        {"type": e["type"], "field": list(e["loc"])}
                        for e in cause.errors(include_input=False, include_context=False)[:8]
                    ]
                    break
                cause = cause.__cause__ or cause.__context__
            if issues and run_id:
                db.event(
                    run_id,
                    "model_output",
                    "Model response did not match the action schema",
                    {"schema": output_type.__name__, "issues": issues},
                )
            if unreadable_reply(exc):
                # HTTP 200, but the completion itself failed validation: neither a model_output slip (the model said
                # nothing wrong) nor an outage on its first sighting. Counted toward the provider's health.
                provider_health.note_invalid_response(db, config)
                raise AnswerModelInvalidResponse() from exc
            raise
        provider_health.note_readable_response(config)
        return result.output
