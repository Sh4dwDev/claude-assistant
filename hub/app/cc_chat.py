"""Chat through your Claude subscription: the hub runs Claude Code headless (`claude -p`),
signed in with a long-lived token from `claude setup-token`.

Claude Code gets web search/fetch plus the hub's own tools over MCP (memory, PC control,
coding jobs, notifications), and nothing else: no shell, no file editing on the hub.
Each chat keeps its own Claude Code session, resumed with --resume.
"""
import asyncio
import json
import logging
import os
import re
import shutil
from pathlib import Path

from . import config, costs, db, events
from .config import DATA_DIR
from .mcp_http import SERVER_NAME, mcp_config

log = logging.getLogger("cc_chat")

WORK_DIR = DATA_DIR / "claude-work"
TIMEOUT_S = 600
LIMIT_TEXT = re.compile(r"(hit your (session|weekly|opus|sonnet) limit|usage limit|rate[ _-]?limit|429)", re.I)
# Flags newer than some Claude Code builds; dropped automatically if the CLI doesn't know them.
_optional_flags_ok = True


class SubscriptionLimit(Exception):
    pass


class EngineError(Exception):
    pass


CLAUDE_CMD: list[str] | None = None  # tests swap in a fake Claude Code here


def claude_bin() -> str | None:
    if CLAUDE_CMD:
        return CLAUDE_CMD[0]
    found = shutil.which("claude")
    if found:
        return found
    p = Path.home() / ".local/bin/claude"
    return str(p) if p.exists() else None


def available() -> bool:
    return bool(config.get_secret("CLAUDE_CODE_OAUTH_TOKEN")) and bool(claude_bin())


def _env() -> dict:
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}  # these would bill the API instead of the plan
    env["CLAUDE_CODE_OAUTH_TOKEN"] = config.get_secret("CLAUDE_CODE_OAUTH_TOKEN")
    env.setdefault("CLAUDE_CONFIG_DIR", str(DATA_DIR / "claude"))
    env["CLAUDE_CODE_PROJECT_DIR_NAME"] = "assistant"
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def _args(prompt: str, system: str, session_id: str | None, model: str, optional: bool) -> list[str]:
    args = [*(CLAUDE_CMD or [claude_bin()]), "-p", prompt,
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--permission-mode", "dontAsk",
            "--tools", "WebSearch,WebFetch",
            "--allowedTools", f"mcp__{SERVER_NAME}__*,WebSearch,WebFetch",
            "--system-prompt", system,
            "--model", model]
    if optional:
        args += ["--system-prompt-snapshot", "off"]  # memories change; rebuild the prompt every turn
    if session_id:
        args += ["--resume", session_id]
    # --mcp-config takes several values, so it goes last where it can't swallow anything
    args += ["--strict-mcp-config", "--mcp-config", json.dumps(mcp_config())]
    return args


def _label(name: str) -> str:
    from .chat import _tool_label
    return _tool_label(name.removeprefix(f"mcp__{SERVER_NAME}__").replace("WebSearch", "web_search")
                       .replace("WebFetch", "web_fetch"))


async def stream(prompt: str, system: str, session_id: str | None, model: str):
    """Yields ("text", str) / ("status", str) / ("final", dict(text, session_id, cost_equiv))."""
    global _optional_flags_ok
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    for attempt in (0, 1):
        optional = _optional_flags_ok and attempt == 0
        proc = await asyncio.create_subprocess_exec(
            *_args(prompt, system, session_id, model, optional), cwd=WORK_DIR, env=_env(),
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=16 * 1024 * 1024)
        err_task = asyncio.create_task(proc.stderr.read())
        result, rate_limited, sid, any_output = None, False, session_id, False
        try:
            async with asyncio.timeout(TIMEOUT_S):
                async for raw in proc.stdout:
                    try:
                        ev = json.loads(raw)
                    except ValueError:
                        continue
                    t = ev.get("type")
                    if t == "system" and ev.get("subtype") == "init":
                        sid = ev.get("session_id") or sid
                    elif t == "system" and ev.get("subtype") == "api_retry":
                        if ev.get("error") == "rate_limit" or ev.get("error_status") == 429:
                            rate_limited = True
                    elif t == "stream_event":
                        e = ev.get("event") or {}
                        if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
                            any_output = True
                            yield "text", e["delta"]["text"]
                        elif e.get("type") == "content_block_start":
                            b = e.get("content_block") or {}
                            if b.get("type") in ("tool_use", "server_tool_use", "mcp_tool_use"):
                                yield "status", _label(b.get("name", ""))
                    elif t == "result":
                        result = ev
                        sid = ev.get("session_id") or sid
        except TimeoutError:
            proc.kill()
            raise EngineError("Claude took too long to answer.")
        await proc.wait()
        stderr = (await err_task).decode("utf-8", "replace")

        if proc.returncode != 0 and not any_output and optional and "unknown option" in stderr.lower():
            log.warning("this Claude Code build lacks an optional flag; retrying without it")
            _optional_flags_ok = False
            continue

        failed = proc.returncode != 0 or not result or result.get("is_error") or result.get("subtype") != "success"
        if failed:
            text = (result or {}).get("result") or stderr.strip()[-400:] or f"exit code {proc.returncode}"
            if rate_limited or LIMIT_TEXT.search(text):
                raise SubscriptionLimit(text)
            if "auth" in text.lower() or "login" in text.lower() or "401" in text:
                raise EngineError("Claude Code couldn't sign in with your subscription token. "
                                  "Make a new one with `claude setup-token` and paste it in Settings.")
            raise EngineError(f"Claude Code stopped: {text}")
        yield "final", {"text": result.get("result") or "", "session_id": sid,
                        "cost_equiv": float(result.get("total_cost_usd") or 0)}
        return


async def check_token(token: str) -> str | None:
    """Returns None if the token works, else a reason. Skipped (None) when Claude Code isn't installed here."""
    if not claude_bin():
        return None
    env = _env()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = token
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        claude_bin(), "-p", "Reply with just: OK", "--output-format", "json", "--tools", "",
        "--no-session-persistence", cwd=WORK_DIR, env=env, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), 120)
    except asyncio.TimeoutError:
        proc.kill()
        return "Claude Code didn't answer in time."
    if proc.returncode == 0:
        return None
    if b"unknown option" in err.lower():  # older build without --no-session-persistence: don't block setup
        return None
    msg = (out or err).decode("utf-8", "replace").strip()
    try:
        msg = json.loads(msg).get("result") or msg
    except ValueError:
        pass
    return f"That token didn't work: {msg[-300:]}"


def record_use(model: str, cost_equiv: float) -> None:
    # Covered by your plan: recorded at $0 so the costs panel stays honest, with the API-equivalent kept for interest.
    costs.record("claude_sub", 0.0, detail=model, meta={"api_equivalent_usd": round(cost_equiv, 5)})
    events.publish("costs", {})


def session_for(conv_id: int) -> str | None:
    r = db.q1("SELECT cc_session_id FROM conversations WHERE id=?", (conv_id,))
    return r["cc_session_id"] if r else None


def save_session(conv_id: int, sid: str | None) -> None:
    if sid:
        db.run("UPDATE conversations SET cc_session_id=? WHERE id=?", (sid, conv_id))
