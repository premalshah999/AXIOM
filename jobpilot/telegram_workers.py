"""Optional Telegram companions for the eight existing application owners.

Bots observe and control canonical jobs; they never start a second worker pipeline.
Message IDs, offsets and conversation contexts belong to a bot, not to all configured bots.
"""

import asyncio
import re

from sqlalchemy import select

from .db import Review, Run, now

COMMANDS = [
    {"command": name, "description": description}
    for name, description in [
        ("start", "Open this worker's application"),
        ("status", "This worker's current job and state"),
        ("job", "Application details and screenshot"),
        ("pause", "Pause this application"),
        ("resume", "Resume this application"),
        ("retry", "Retry this application using its saved draft"),
        ("delete", "Remove this application; preserve history"),
        ("query", "Discuss this application"),
        ("help", "Commands for this application worker"),
    ]
]
MENU = {
    "inline_keyboard": [
        [
            {"text": "Application details", "callback_data": "worker:job"},
            {"text": "Worker help", "callback_data": "worker:help"},
        ]
    ]
}


def run_slot(run):
    packet = run.get("packet", {}) if isinstance(run, dict) else getattr(run, "packet", {})
    slot = (packet or {}).get("worker_slot") or (run.get("worker_slot") if isinstance(run, dict) else None)
    return slot if type(slot) is int and 1 <= slot <= 32 else None


def configured_slots(config):
    """Reject duplicate tokens so each bot has exactly one getUpdates consumer."""
    if not config.telegram_enabled or not config.telegram_user_id:
        return {}
    seen = {config.telegram_bot_token} if config.telegram_bot_token else set()
    slots = {}
    for slot in range(1, min(32, config.workers) + 1):
        token = getattr(config, f"telegram_worker_{slot}_bot_token", "")
        if token and token not in seen:
            slots[slot] = token
            seen.add(token)
    return slots


def any_configured(config):
    from .telegram import configured

    return configured(config) or bool(configured_slots(config))


class ScopedDB:
    def __init__(self, db, slot):
        self.base, self.slot = db, slot

    def key(self, key):
        if key == "telegram" or key.startswith("telegram_"):
            # Pending notifications belong to the single canonical delivery queue.
            if not key.startswith("telegram_run_pending:"):
                return f"telegram_worker:{self.slot}:" + key
        return key

    def get_setting(self, key, default=None):
        # Main owns the activation epoch. Every bot observes the same fresh backlog.
        if key == "telegram_progress":
            return self.base.get_setting(key, default)
        return self.base.get_setting(self.key(key), default)

    def set_setting(self, key, value):
        return self.base.set_setting(self.key(key), value)

    def __getattr__(self, name):
        return getattr(self.base, name)


class WorkerService:
    def __init__(self, service, slot, token):
        self.parent, self.telegram_worker_slot = service, slot
        self.db = ScopedDB(service.db, slot)
        self.config = service.config.model_copy(
            update={"telegram_bot_token": token, "telegram_worker_slot": slot}
        )

    def __getattr__(self, name):
        return getattr(self.parent, name)


def worker_service(service, slot):
    token = configured_slots(service.config).get(slot)
    if not token:
        return None
    if not hasattr(service, "telegram_worker_services"):
        service.telegram_worker_services = {}
    existing = service.telegram_worker_services.get(slot)
    if existing is None or existing.config.telegram_bot_token != token:
        existing = WorkerService(service, slot, token)
        service.telegram_worker_services[slot] = existing
    return existing


def delivery_service(service, run):
    if getattr(service, "telegram_worker_slot", None):
        return service
    return worker_service(service, run_slot(run)) or service


def accepts_run(service, run):
    """One bot delivers a run's questions; the main bot covers unconfigured slots."""
    slot = getattr(service, "telegram_worker_slot", None)
    if slot:
        return run_slot(run) == slot
    # Without worker bots every run is the main bot's: no need to load its packet to learn that (the Telegram question
    # scan defers packets and this undid it, once per unsent question, 2026-10-30).
    slots = configured_slots(service.config)
    return not slots or run_slot(run) not in slots


def owns_job(service, job_id):
    with service.db.session() as session:
        latest = session.scalar(
            select(Run).where(Run.job_id == job_id).order_by(Run.created_at.desc()).limit(1)
        )
        return bool(latest and run_slot(latest) == service.telegram_worker_slot)


def assignment(service):
    return service.parent.db.get_setting(f"telegram_worker_slot:{service.telegram_worker_slot}", {})


def worker_status(service):
    slots = configured_slots(service.config)
    seen = {service.config.telegram_bot_token} if service.config.telegram_bot_token else set()
    rows = []
    for slot in range(1, min(32, service.config.workers) + 1):
        token = getattr(service.config, f"telegram_worker_{slot}_bot_token", "")
        current = service.db.get_setting(f"telegram_worker_slot:{slot}", {})
        health = service.db.get_setting(f"telegram_worker:{slot}:telegram_health", {})
        duplicate = bool(token and token in seen)
        if token:
            seen.add(token)
        rows.append(
            {
                "slot": slot,
                "name": f"ApplyPilot Worker {slot}",
                "configured": slot in slots,
                "state": health.get("state", "configured" if slot in slots else "not_configured"),
                "reason": (
                    "duplicate_bot_token"
                    if duplicate
                    else ""
                    if slot in slots
                    else "disabled"
                    if not service.config.telegram_enabled
                    else "missing_owner"
                    if token and not service.config.telegram_user_id
                    else "missing_token"
                ),
                "run_id": current.get("run_id"),
                "job_id": current.get("job_id"),
                "job_state": current.get("state"),
            }
        )
    return rows


async def send(service, text):
    from .telegram import api

    await api(
        service.config, "sendMessage", {"chat_id": service.config.telegram_user_id, "text": text[:4000]}
    )
    return text


async def handle_update(service, update):
    """Authorize first, then resolve explicit bot-local replies before current assignment."""
    from . import telegram
    from .job_control import choices, manage

    callback = update.get("callback_query")
    message = update.get("message", {})
    incoming = callback or message
    chat = (callback.get("message", {}) if callback else message).get("chat", {})
    owner = service.config.telegram_user_id
    if str(incoming.get("from", {}).get("id")) != owner or (
        chat and (str(chat.get("id")) != owner or chat.get("type", "private") != "private")
    ):
        return None
    raw = callback.get("data", "") if callback else message.get("text", "")
    if not raw:
        return None
    reply = await _route(service, update, telegram, choices, manage)
    telegram.record_conversation(service, raw, reply or "", update)
    return reply


async def _route(service, update, telegram, choices, manage):
    callback = update.get("callback_query")
    message = update.get("message", {})
    text = message.get("text", "").strip()
    if callback:
        parts = callback.get("data", "").split(":")
        await telegram.api(service.config, "answerCallbackQuery", {"callback_query_id": callback["id"]})
        if len(parts) == 2 and parts[0] == "worker" and parts[1] in {"job", "help"}:
            return await _route(service, {"message": {"text": "/" + parts[1]}}, telegram, choices, manage)
        if len(parts) != 3 or parts[0] not in {"j", "a"}:
            return await send(
                service,
                "Use this worker's /status, /job, /pause or /resume. Batch controls are in the main bot.",
            )
        if parts[0] == "a":
            with service.db.session() as session:
                review = session.get(Review, parts[1])
                run = session.get(Run, review.run_id) if review else None
                try:
                    value = review.options[int(parts[2])] if review and int(parts[2]) >= 0 else None
                except (ValueError, IndexError):
                    value = None
            if not run or run_slot(run) != service.telegram_worker_slot or value is None:
                return await send(service, "That question does not belong to this worker or has expired.")
            # Canonical Telegram validation rejects superseded questions before learning.
            return await telegram._handle_update(service, update)
        action, job_id = parts[1:]
        if action not in {"inspect", "pause", "resume", "delete"} or not owns_job(service, job_id):
            return await send(service, "That application does not belong to this worker.")
        return await send(service, await telegram.manage_reference(service, job_id, action))

    if telegram.conversation_text(text) == "[Message containing a credential omitted]":
        return await send(
            service,
            "Keep passwords, verification codes and API keys in secure settings. I haven’t saved or used that credential.",
        )
    cmd, _, rest = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    if cmd in {"/help"}:
        return await send(
            service,
            f"ApplyPilot Worker {service.telegram_worker_slot}\n/status · /job · /pause · /resume · /retry · /delete · /query\nReply to a question or screenshot to control exactly that application. Use the main bot for batches and global settings.",
        )
    # An unknown replied message must never fall through to a different current job.
    job_id = telegram.message_job(service, message)
    if message.get("reply_to_message") and not job_id:
        return await send(
            service,
            "That reply has no application context in this bot. Open /job or reply to its question here.",
        )
    current = assignment(service)
    job_id = job_id or current.get("job_id")
    if not job_id or not owns_job(service, job_id):
        return await send(
            service,
            f"Worker {service.telegram_worker_slot} has no assigned application yet. The main bot manages discovery and batches.",
        )
    if rest and cmd in {"/job", "/pause", "/resume", "/retry", "/delete"}:
        matches = choices(service, rest)
        if len(matches) != 1 or not owns_job(service, matches[0]["job_id"]):
            return await send(
                service,
                "That job is ambiguous or belongs to another worker. Use /job for this worker's current application.",
            )
        job_id = matches[0]["job_id"]
    if cmd in {"/start", "/status", "/job", "/applications"} or text.lower() in {"status", "show", "job"}:
        return await send(service, await telegram.manage_reference(service, job_id, "inspect"))
    natural = re.fullmatch(r"(?:please\s+)?(pause|resume|retry|delete|remove)(?:\s+(.+))?", text, re.I)
    if natural:
        action = natural[1].lower()
        if natural[2]:
            matches = choices(service, natural[2])
            if len(matches) != 1 or matches[0]["job_id"] != job_id:
                return await send(
                    service,
                    "Use this worker's /pause or /resume for its application. Global controls are in the main bot.",
                )
        cmd = "/" + ("delete" if action == "remove" else action)
    if cmd in {"/pause", "/resume", "/retry", "/delete"}:
        action = "resume" if cmd in {"/resume", "/retry"} else cmd[1:]
        try:
            result = (
                await telegram.resume_instruction(service, job_id)
                if action == "resume"
                else await manage(service, job_id, action)
            )
            return await send(service, result["message"])
        except (ValueError, RuntimeError) as exc:
            return await send(service, str(exc)[:500])
    if text.lower() in {"done", "finished", "completed", "signed in", "captcha done"}:
        # Completion of a manual browser step resumes its original mode through
        # the same fit and duplicate guards as the explicit Resume control.
        try:
            result = await telegram.resume_instruction(service, job_id)
            return await send(service, result["message"])
        except (ValueError, RuntimeError) as exc:
            return await send(service, str(exc)[:500])
    if cmd == "/query":
        return await send(service, await telegram.chat(service, rest, job_id))
    if cmd.startswith("/"):
        return await send(
            service,
            "This bot controls its own application. Use the main bot for batches, new links and global settings.",
        )
    service.db.set_setting("telegram_context", {"job_id": job_id, "at": now()})
    instruction = await telegram.worker_instruction(service, message)
    if instruction is not None:
        return await send(service, instruction)
    # Unreplied answers are correlated only to this bot's current job. Explicit older
    # question replies preserve their original run when the slot has moved on.
    active = service.db.get_setting("telegram_question", {})
    if not message.get("reply_to_message") and active.get("review_id"):
        with service.db.session() as session:
            review = session.get(Review, active["review_id"])
            run = session.get(Run, review.run_id) if review else None
        if run and run.job_id == job_id:
            message = {**message, "reply_to_message": {"message_id": active.get("message_id")}}
    answer = await telegram.answer_from_text(service, message) if message.get("reply_to_message") else None
    if answer is not None:
        return await send(service, answer)
    return await send(service, await telegram.chat(service, text, job_id))


async def poll(service):
    from . import telegram

    offset = service.db.get_setting("telegram", {}).get("offset", 0)
    try:
        await telegram.api(service.config, "setMyCommands", {"commands": COMMANDS})
    except Exception:
        pass
    delivery = None

    async def deliver():
        await telegram.ask_next_question(service)
        await telegram.ask_next_browser(service)

    try:
        while True:
            try:
                if delivery is None or delivery.done():
                    if delivery and not delivery.cancelled() and delivery.exception():
                        service.db.set_setting(
                            "telegram_delivery_error",
                            {"at": now(), "error": type(delivery.exception()).__name__},
                        )
                    delivery = asyncio.create_task(deliver())
                updates = await telegram.api(
                    service.config,
                    "getUpdates",
                    {"offset": offset, "timeout": 25, "allowed_updates": ["message", "callback_query"]},
                )
                service.db.set_setting("telegram_health", {"state": "connected", "checked_at": now()})
                for update in updates:
                    offset = update["update_id"] + 1
                    service.db.set_setting("telegram", {"offset": offset})
                    try:
                        await handle_update(service, update)
                        service.db.set_setting(
                            "telegram_health",
                            {
                                "state": "connected",
                                "checked_at": now(),
                                "last_update_at": now(),
                                "last_update_id": update["update_id"],
                            },
                        )
                    except Exception as exc:
                        service.db.set_setting(
                            "telegram_health",
                            {"state": "command_error", "checked_at": now(), "error": type(exc).__name__},
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                service.db.set_setting(
                    "telegram_health",
                    {"state": "disconnected", "checked_at": now(), "error": type(exc).__name__},
                )
                await asyncio.sleep(5)
    finally:
        if delivery:
            delivery.cancel()
            await asyncio.gather(delivery, return_exceptions=True)


async def poll_all(service):
    """One optional getUpdates poller per unique token, without application workers."""
    tasks = [
        asyncio.create_task(poll(worker_service(service, slot))) for slot in configured_slots(service.config)
    ]
    try:
        if tasks:
            await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
