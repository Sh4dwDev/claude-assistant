"""Chat with Claude: streaming, web search, memory tools, cost tracking.

Every reply streams to the dashboard as Server-Sent Events:
  meta {conversation_id} | delta {text} | status {text} | done {cost_usd} | error {message}
"""
import asyncio
import json
import logging
import time
from datetime import datetime
from typing import AsyncIterator
from zoneinfo import ZoneInfo

import anthropic

from . import config, costs, db, events, tools
from .tools import memory

log = logging.getLogger("chat")

MAX_STEPS = 10
_locks: dict[int, asyncio.Lock] = {}
# Optional API features (refusal fallback + automatic compaction of very long chats).
# If the API ever rejects them, we turn them off and carry on with a plain request.
_use_beta = True

SYSTEM_TEMPLATE = """You are {name}, the personal assistant of {user}{home}. You run on {user}'s own \
server and are reached from a dashboard on the PC and an app on the phone, by text or voice.

How to talk: be direct and warm, like a capable friend. Lead with the answer. Keep replies short unless \
{user} asks for depth: a couple of sentences is usually right, because replies may be read aloud. Use \
markdown only when it helps (code, lists, tables). Answer in the language {user} writes in.

What you can do: answer questions, search the web and read pages (cite where facts came from), remember \
lasting facts about {user} with the memory tools, send push notifications, control {user}'s PC (open programs \
and sites, search in the browser, media, volume, lock, sleep, screenshots, wake it when it's off), and run \
coding jobs: Claude Code works on the PC in its own project folder. For anything that means building or \
changing software, start a coding job with a complete brief instead of writing the code in chat. Simple PC \
commands usually run before they reach you; you see the ones that were unclear, so resolve them (for example \
look up the installed apps) and act. Email, phone calls and spending reports come in later milestones; if \
asked, say they aren't connected yet.

Safety: never claim to have done something you did not do. Closing programs, shutting down, restarting and \
cancelling jobs always go through a Yes/No confirmation; the tools handle that, so don't ask twice in text. \
Before anything that sends messages to other people, spends money, deletes files or publishes anything, ask \
{user} first.

Each user message starts with the local date and time in brackets; use it for anything time-related.

Saved memories about {user} (ids in brackets, for update_memory and forget):
{memories}"""


def _system() -> str:
    s = db.all_settings()
    home = f", who lives in {s['home']}" if s.get("home") else ""
    return SYSTEM_TEMPLATE.format(name=s["assistant_name"], user=s["user_name"] or "the user", home=home,
                                  memories=memory.as_prompt())


def _stamp() -> str:
    tz = ZoneInfo(db.get_setting("timezone") or "Europe/Oslo")
    return datetime.now(tz).strftime("%a %d %b %Y %H:%M")


def _client() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=config.get_secret("ANTHROPIC_API_KEY"), max_retries=2)


def sse(kind: str, data: dict) -> str:
    return f"event: {kind}\ndata: {json.dumps(data)}\n\n"


# --- conversations -----------------------------------------------------------
def new_conversation(title: str = "New chat") -> int:
    return db.run("INSERT INTO conversations(title, created, updated) VALUES(?,?,?)", (title, time.time(), time.time()))


def latest_conversation() -> int:
    r = db.q1("SELECT id FROM conversations ORDER BY updated DESC LIMIT 1")
    return r["id"] if r else new_conversation()


def history_for_ui(conv_id: int) -> list[dict]:
    return db.q("SELECT id, role, display_text AS text, created FROM messages "
                "WHERE conversation_id=? AND display_text IS NOT NULL ORDER BY id", (conv_id,))


def _api_messages(conv_id: int) -> list[dict]:
    rows = db.q("SELECT role, content_json FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))
    return [{"role": r["role"], "content": json.loads(r["content_json"])} for r in rows]


def _save(conv_id: int, role: str, content: list, display: str | None) -> None:
    db.run("INSERT INTO messages(conversation_id, role, content_json, display_text, created) VALUES(?,?,?,?,?)",
           (conv_id, role, json.dumps(content), display, time.time()))
    db.run("UPDATE conversations SET updated=? WHERE id=?", (time.time(), conv_id))


def save_note(conv_id: int, text: str) -> None:
    """An assistant line produced outside the API (e.g. the result of a confirmed action)."""
    if db.q1("SELECT id FROM conversations WHERE id=?", (conv_id,)):
        _save(conv_id, "assistant", [{"type": "text", "text": text}], text)


def save_exchange(conv_id: int, user_text: str, reply: str) -> None:
    """For replies produced without the API (instant commands): keep them in the chat history."""
    _save(conv_id, "user", [{"type": "text", "text": f"[{_stamp()}] {user_text}"}], user_text)
    _save(conv_id, "assistant", [{"type": "text", "text": reply}], reply)


# --- the main loop -----------------------------------------------------------
def _tool_label(name: str, inp: dict | None = None) -> str:
    return {"web_search": "Searching the web", "web_fetch": "Reading a page",
            "remember": "Saving to memory", "update_memory": "Updating memory",
            "forget": "Forgetting", "send_notification": "Sending a notification",
            "pc_screenshot": "Looking at the screen", "pc_list_apps": "Checking installed apps",
            "pc_open_app": "Opening on the PC", "pc_open_url": "Opening in the browser", "pc_search": "Searching on the PC",
            "pc_wake": "Waking the PC", "start_coding_job": "Starting a coding job",
            "job_status": "Checking the job"}.get(name, f"Using {name.replace('_', ' ')}")


async def _stream_once(client, kwargs) -> AsyncIterator[tuple[str, object]]:
    """Yields ('text', str) / ('status', str) / ('final', Message)."""
    global _use_beta
    for attempt in (0, 1):
        beta = _use_beta and attempt == 0
        try:
            if beta:
                cm = client.beta.messages.stream(
                    **kwargs,
                    betas=["server-side-fallback-2026-07-01", "compact-2026-01-12"],
                    fallbacks="default",
                    context_management={"edits": [{"type": "compact_20260112"}]},
                )
            else:
                cm = client.messages.stream(**kwargs)
            async with cm as stream:
                async for ev in stream:
                    if ev.type == "text":
                        yield "text", ev.text
                    elif ev.type == "content_block_start":
                        b = ev.content_block
                        if b.type in ("server_tool_use", "tool_use"):
                            yield "status", _tool_label(b.name)
                yield "final", await stream.get_final_message()
            return
        except anthropic.BadRequestError as e:
            if beta:
                log.warning("optional API features rejected, retrying without them: %s", e.message)
                _use_beta = False
                continue
            raise


async def _run_tools(blocks) -> list[dict]:
    reg = tools.registry()

    async def one(b):
        tool = reg.get(b.name)
        if not tool:
            return {"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": f"Unknown tool {b.name}"}
        err = tools.validate(tool.schema, b.input)
        if err:
            return {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                    "content": json.dumps({"INVALID_INPUT": err})}
        try:
            out = await tool.handler(b.input)
            return {"type": "tool_result", "tool_use_id": b.id, "content": out}
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", b.name)
            return {"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": f"Tool error: {e}"}

    return list(await asyncio.gather(*(one(b) for b in blocks)))

def engine() -> str | None:
    """Which brain answers: your Claude subscription (via Claude Code) or the API key."""
    from . import cc_chat
    pref = db.get_setting("chat_engine") or "auto"
    sub_ok, api_ok = cc_chat.available(), config.has_secret("ANTHROPIC_API_KEY")
    if pref == "api" and api_ok:
        return "api"
    if sub_ok:
        return "subscription"
    return "api" if api_ok else None


async def run_chat(conv_id: int | None, user_text: str) -> AsyncIterator[str]:
    from . import cc_chat
    conv_id = conv_id or latest_conversation()
    if not db.q1("SELECT id FROM conversations WHERE id=?", (conv_id,)):
        conv_id = new_conversation()
    yield sse("meta", {"conversation_id": conv_id})

    which = engine()
    if not which:
        yield sse("error", {"message": "Connect your Claude subscription in Settings > Keys first."})
        return

    lock = _locks.setdefault(conv_id, asyncio.Lock())
    async with lock:
        conv = db.q1("SELECT title FROM conversations WHERE id=?", (conv_id,))
        if conv and conv["title"] in (None, "New chat"):
            db.run("UPDATE conversations SET title=? WHERE id=?", (user_text.strip()[:60], conv_id))
        prompt = f"[{_stamp()}] {user_text}"
        _save(conv_id, "user", [{"type": "text", "text": prompt}], user_text)

        if which == "subscription":
            reply, model = "", db.get_setting("chat_model")
            try:
                async for kind, val in cc_chat.stream(prompt, _system(), cc_chat.session_for(conv_id), model):
                    if kind == "text":
                        reply += val
                        yield sse("delta", {"text": val})
                    elif kind == "status":
                        yield sse("status", {"text": val})
                    else:
                        reply = reply or val["text"]
                        if not reply:  # nothing streamed (older CLI): send the final text at once
                            yield sse("delta", {"text": val["text"]})
                        cc_chat.save_session(conv_id, val["session_id"])
                        cc_chat.record_use(model, val["cost_equiv"])
                _save(conv_id, "assistant", [{"type": "text", "text": reply or "(no reply)"}], reply or None)
                yield sse("done", {"cost_usd": 0, "conversation_id": conv_id, "engine": "subscription"})
                return
            except cc_chat.SubscriptionLimit as e:
                if not config.has_secret("ANTHROPIC_API_KEY"):
                    yield sse("error", {"message": f"You've hit your Claude plan's usage limit ({e}). It resets on "
                                                   "its own; add an API key in Settings to keep chatting meanwhile."})
                    return
                yield sse("status", {"text": "Plan limit reached, answering with the API key"})
            except cc_chat.EngineError as e:
                yield sse("error", {"message": str(e)})
                return

        async for chunk in _run_api(conv_id):
            yield chunk


async def _run_api(conv_id: int) -> AsyncIterator[str]:
    if costs.over_cap("claude_api"):
        yield sse("error", {"message": "This month's Claude API spending cap is reached. "
                                       "Raise it in Settings, or wait until next month."})
        return
    settings = db.all_settings()
    model = settings["chat_model"]
    tool_specs = [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5,
         "user_location": {"type": "approximate", "country": "NO", "timezone": settings["timezone"]}},
        {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5},
    ] + [t.spec() for t in tools.registry().values()]

    client = _client()
    total_cost = 0.0
    wrote_text = False
    try:
        for _step in range(MAX_STEPS):
            kwargs = dict(
                model=model,
                max_tokens=16000,
                system=_system(),
                messages=_api_messages(conv_id),
                tools=tool_specs,
                output_config={"effort": settings["chat_effort"]},
                cache_control={"type": "ephemeral"},
            )
            final = None
            first_text_this_step = True
            async for kind, val in _stream_once(client, kwargs):
                if kind == "text":
                    if first_text_this_step and wrote_text:
                        yield sse("delta", {"text": "\n\n"})
                    first_text_this_step = False
                    wrote_text = True
                    yield sse("delta", {"text": val})
                elif kind == "status":
                    yield sse("status", {"text": val})
                else:
                    final = val

            cost = costs.claude_cost(final.model, final.usage)
            total_cost += cost
            costs.record("claude_api", cost, detail=final.model,
                         meta={"in": final.usage.input_tokens, "out": final.usage.output_tokens})

            content = [b.to_dict(mode="json") for b in final.content]
            text = "".join(b.text for b in final.content if b.type == "text").strip()
            _save(conv_id, "assistant", content, text or None)

            stop = final.stop_reason
            tool_uses = [b for b in final.content if b.type == "tool_use"]
            if tool_uses and stop != "tool_use":
                # cut off mid tool call: never run it, but keep the history valid
                _save(conv_id, "user", [{"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                         "content": "Not run: the reply was cut off."} for b in tool_uses], None)
            if stop == "pause_turn":
                continue
            if stop == "refusal":
                yield sse("delta", {"text": "\n\n(I can't help with that one.)"})
                break
            if stop == "tool_use" and tool_uses:
                results = await _run_tools(tool_uses)
                _save(conv_id, "user", results, None)
                continue
            if stop == "max_tokens":
                yield sse("delta", {"text": "\n\n(Reply cut off - ask me to continue.)"})
            break
    except anthropic.AuthenticationError:
        yield sse("error", {"message": "The Claude API key was rejected. Update it in Settings."})
        return
    except anthropic.RateLimitError:
        yield sse("error", {"message": "Claude is rate-limited right now. Try again in a minute."})
        return
    except anthropic.APIConnectionError:
        yield sse("error", {"message": "Can't reach the Claude API (internet down?). Your message is saved."})
        return
    except anthropic.APIStatusError as e:
        yield sse("error", {"message": f"Claude API error {e.status_code}: {e.message}"})
        return

    events.publish("costs", {})
    yield sse("done", {"cost_usd": round(total_cost, 5), "conversation_id": conv_id})
