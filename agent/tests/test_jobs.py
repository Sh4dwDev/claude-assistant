"""Offline test of the Claude Code job runner with a fake `claude` that replays stream-json.
Run from the agent folder:  python tests/test_jobs.py"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jobs import JobRunner  # noqa: E402

FAKE = r'''
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
assert "-p" in args and "--output-format" in args and "--disallowedTools" in args, args
assert "make a pong game" in prompt and "make a pong game" not in " ".join(args)  # task only on stdin
resume = "--resume" in args
def out(o): print(json.dumps(o), flush=True)
out({"type": "system", "subtype": "init", "session_id": "sess-123"})
out({"type": "assistant", "message": {"content": [{"type": "text", "text": "I'll set up the project.\nMore detail"}]}})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": "/p/main.gd", "content": "x"}}]}})
out({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "godot --headless -s addons/gut/gut_cmdln.gd"}}]}})
out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok", "is_error": False}]}})
out({"type": "result", "subtype": "success", "result": "Built Pong." + (" (resumed)" if resume else ""), "session_id": "sess-123", "total_cost_usd": 0.42, "num_turns": 4})
'''


async def main():
    events = []
    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp) / "fake_claude.py"
        fake.write_text(FAKE, encoding="utf-8")
        runner = JobRunner(events.append, Path(tmp) / "Projects")
        runner.claude_cmd = [sys.executable, str(fake)]

        await runner.start(7, "pong", "Please make a pong game in Godot")
        for _ in range(100):
            if any(e["kind"] in ("done", "failed") for e in events):
                break
            await asyncio.sleep(0.1)
        kinds = [e["kind"] for e in events]
        texts = [e.get("text") for e in events if e["kind"] in ("log", "milestone")]
        print(json.dumps(texts, indent=1))
        assert "milestone" in kinds and any(e.get("text") == "tests passing" for e in events), kinds
        done = next(e for e in events if e["kind"] == "done")
        assert done["summary"] == "Built Pong." and done["session_id"] == "sess-123" and done["cost_usd"] == 0.42
        assert (Path(tmp) / "Projects" / "pong").is_dir()
        assert any(e.get("session_id") == "sess-123" for e in events if e["kind"] == "log")

        events.clear()
        await runner.start(7, "pong", "now make a pong game faster", session_id="sess-123")
        for _ in range(100):
            if any(e["kind"] in ("done", "failed") for e in events):
                break
            await asyncio.sleep(0.1)
        done = next(e for e in events if e["kind"] == "done")
        assert done["summary"].endswith("(resumed)"), done
        await asyncio.sleep(0.3)  # let the process handles close before the temp dir goes
    print("OK - progress, tests-passing milestone, summary, follow-up resume")


asyncio.run(main())
