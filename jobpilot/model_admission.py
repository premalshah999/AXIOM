"""Process-wide, cancellation-safe admission for model HTTP requests.

Browser/answer work precedes classification/report queues. A thread-safe owner
supports DBOS and API event loops without sharing asyncio primitives across them.
"""

import asyncio
import heapq
import itertools
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass


def priority(purpose, run_id=None):
    if purpose in {"AnswerQualityAudit", "FailureReport", "FailureSummary", "KnowledgeNotes", "Explanation"}:
        return 3
    if purpose in {"FitResult", "ClassifiedFit", "RankedBatch", "RankedJob"}:
        return 2
    if run_id or purpose in {
        "AnswerBatch",
        "NavigationDecision",
        "ControlDecision",
        "Letter",
        "browser_agent",
        "browser_vision",
    }:
        return 0
    return 1


@dataclass
class Ticket:
    loop: asyncio.AbstractEventLoop
    future: asyncio.Future
    priority: int
    granted: bool = False
    released: bool = False


class ModelAdmission:
    def __init__(self, limit=6, background_limit=2):
        self.limit, self.background_limit = limit, background_limit
        self.active = self.background_active = 0
        self.lock = threading.Lock()
        self.waiters = []
        self.sequence = itertools.count()

    def _ready(self, ticket):
        if not ticket.future.done():
            ticket.future.set_result(None)

    def _drain(self):
        deferred = []
        while self.waiters and self.active < self.limit:
            order, sequence, ticket = heapq.heappop(self.waiters)
            if ticket.released or ticket.loop.is_closed():
                continue
            if order >= 2 and self.background_active >= self.background_limit:
                deferred.append((order, sequence, ticket))
                continue
            ticket.granted = True
            self.active += 1
            self.background_active += order >= 2
            try:
                ticket.loop.call_soon_threadsafe(self._ready, ticket)
            except RuntimeError:  # Its event loop closed between inspection and grant.
                ticket.released = True
                self.active -= 1
                self.background_active -= order >= 2
        for item in deferred:
            heapq.heappush(self.waiters, item)

    async def acquire(self, order=0):
        loop = asyncio.get_running_loop()
        ticket = Ticket(loop, loop.create_future(), order)
        with self.lock:
            heapq.heappush(self.waiters, (order, next(self.sequence), ticket))
            self._drain()
        try:
            await ticket.future
        except BaseException:
            self.release(ticket)
            raise
        return ticket

    def release(self, ticket):
        with self.lock:
            if ticket.released:
                return
            ticket.released = True
            if ticket.granted:
                self.active -= 1
                self.background_active -= ticket.priority >= 2
            self._drain()

    @asynccontextmanager
    async def slot(self, purpose, run_id=None, *, limit=7, background_limit=2):
        with self.lock:
            self.limit = max(1, min(7, limit))
            self.background_limit = min(self.limit, max(1, background_limit))
        ticket = await self.acquire(priority(purpose, run_id))
        try:
            yield
        finally:
            self.release(ticket)


admission = ModelAdmission()
