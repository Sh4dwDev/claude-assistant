"""Ask-first actions. Risky things (shut down, close a program, ...) become a pending
confirmation that the user approves with a tap or by saying "yes". They expire after
two minutes, so a stale "yes" never runs anything."""
import secrets
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import events

TTL = 120


@dataclass
class Pending:
    id: str
    question: str
    action: Callable[[], Awaitable[str]]
    created: float
    conversation_id: int | None = None


_pending: dict[str, Pending] = {}


def _prune() -> None:
    now = time.time()
    for k, p in list(_pending.items()):
        if now - p.created > TTL:
            del _pending[k]
            events.publish("confirm_done", {"id": k, "result": "expired"})


def ask(question: str, action: Callable[[], Awaitable[str]], conversation_id: int | None = None) -> Pending:
    _prune()
    p = Pending(secrets.token_hex(5), question, action, time.time(), conversation_id)
    _pending[p.id] = p
    events.publish("confirm", {"id": p.id, "question": question, "expires_in": TTL})
    return p


def latest() -> Pending | None:
    _prune()
    return max(_pending.values(), key=lambda p: p.created, default=None)


def listing() -> list[dict]:
    _prune()
    return [{"id": p.id, "question": p.question, "expires_in": int(TTL - (time.time() - p.created))}
            for p in sorted(_pending.values(), key=lambda p: p.created)]


async def resolve(pid: str, approve: bool) -> str:
    _prune()
    p = _pending.pop(pid, None)
    if not p:
        return "That request expired. Ask again if you still want it."
    if not approve:
        events.publish("confirm_done", {"id": pid, "result": "cancelled"})
        return "Okay, I won't."
    try:
        result = await p.action()
    except Exception as e:  # noqa: BLE001
        result = f"That didn't work: {e}"
    events.publish("confirm_done", {"id": pid, "result": result})
    return result
