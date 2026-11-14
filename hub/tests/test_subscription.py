"""Offline test of chat through the Claude subscription: a fake `claude -p` reads the hub's MCP
config, calls the hub's real MCP endpoint (saving a memory), streams text, and later hits a usage limit.
Run from the hub folder:  python -m tests.test_subscription"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

PORT = 8799
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["HUB_INTERNAL_URL"] = f"http://127.0.0.1:{PORT}"

import uvicorn  # noqa: E402

from app import cc_chat, chat, config, db, main  # noqa: E402

FAKE = r'''
import json, os, sys, urllib.request
a = sys.argv[1:]
prompt = a[a.index("-p") + 1]
assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-ant-oat01-test", "token not passed"
assert "ANTHROPIC_API_KEY" not in os.environ, "API key must not leak into Claude Code"
for flag in ("--strict-mcp-config", "--include-partial-messages", "--system-prompt"):
    assert flag in a, flag
assert a[a.index("--permission-mode") + 1] == "dontAsk" and a[a.index("--tools") + 1] == "WebSearch,WebFetch"
cfg = json.loads(a[a.index("--mcp-config") + 1])["mcpServers"]["assistant"]
def rpc(method, params=None, i=1):
    req = urllib.request.Request(cfg["url"], data=json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}).encode(),
                                 headers={**cfg["headers"], "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    return json.loads(urllib.request.urlopen(req).read())
def out(o): print(json.dumps(o), flush=True)
if "limit" in prompt:
    out({"type": "system", "subtype": "api_retry", "attempt": 1, "max_retries": 3, "error": "rate_limit", "error_status": 429})
    out({"type": "result", "subtype": "error_during_execution", "is_error": True, "result": "You've hit your session limit"})
    sys.exit(1)
resume = a[a.index("--resume") + 1] if "--resume" in a else None
out({"type": "system", "subtype": "init", "session_id": resume or "sess-abc"})
assert rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]["serverInfo"]["name"] == "assistant"
names = [t["name"] for t in rpc("tools/list")["result"]["tools"]]
assert "remember" in names and "pc_open_app" in names and "start_coding_job" in names, names
out({"type": "stream_event", "event": {"type": "content_block_start", "content_block": {"type": "tool_use", "name": "mcp__assistant__remember"}}})
r = rpc("tools/call", {"name": "remember", "arguments": {"text": "Sam likes Godot.", "category": "preference"}})
assert not r["result"].get("isError"), r
for chunk in ["Got it", ", saved", "." if not resume else " (resumed)."]:
    out({"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": chunk}}})
out({"type": "result", "subtype": "success", "is_error": False, "result": "Got it, saved.", "session_id": resume or "sess-abc", "total_cost_usd": 0.03})
'''


def serve():
    uvicorn.run(main.app, host="127.0.0.1", port=PORT, log_level="warning")


async def collect(conv, text):
    out = [x async for x in chat.run_chat(conv, text)]
    events = [(c.split("\n")[0][7:], json.loads(c.split("data: ")[1])) for c in out]
    return events, "".join(d["text"] for k, d in events if k == "delta")


async def run():
    fake = Path(os.environ["DATA_DIR"]) / "fake_claude.py"
    fake.write_text(FAKE, encoding="utf-8")
    cc_chat.CLAUDE_CMD = [sys.executable, str(fake)]
    config.set_secret("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-test")
    os.environ["ANTHROPIC_API_KEY"] = "should-be-stripped"
    assert chat.engine() == "subscription"

    conv = chat.new_conversation()
    events, text = await collect(conv, "Remember that I like Godot")
    kinds = [k for k, _ in events]
    print(kinds, repr(text))
    assert text == "Got it, saved." and "status" in kinds and kinds[-1] == "done", events
    assert db.q1("SELECT text FROM memories")["text"] == "Sam likes Godot."
    assert cc_chat.session_for(conv) == "sess-abc"

    events, text = await collect(conv, "And again")
    assert text.endswith("(resumed)."), text

    # plan limit with an API key configured: falls back to the API (the fake key is then rejected)
    events, text = await collect(conv, "hit the limit please")
    assert any(k == "status" and "Plan limit" in d["text"] for k, d in events), events
    # plan limit without an API key: a clear message
    del os.environ["ANTHROPIC_API_KEY"]
    events, text = await collect(conv, "hit the limit again")
    err = [d for k, d in events if k == "error"]
    assert err and "usage limit" in err[0]["message"], events
    ui = chat.history_for_ui(conv)
    assert [m["role"] for m in ui] == ["user", "assistant", "user", "assistant", "user", "user"], ui
    print("OK - subscription chat via Claude Code, MCP tools, resume, usage-limit message")


if __name__ == "__main__":
    threading.Thread(target=serve, daemon=True).start()
    time.sleep(2)
    asyncio.run(run())
