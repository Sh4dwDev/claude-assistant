"""Runs Claude Code headless (your Claude Max login) in a project folder and reports progress.

Safety: Claude Code may edit files and run commands inside the project folder, but pushing,
deleting and shutting down are denied by its own permission rules (deny always wins over allow).
The task text is passed on stdin, never on the command line.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("jobs")
IS_WIN = sys.platform == "win32"

INSTRUCTION = "Do the task described in the piped input."
ALLOWED = "Bash,Read,Edit,Write,Glob,Grep,WebSearch,WebFetch,TodoWrite,NotebookEdit"
DENIED = ",".join(f"Bash({p}:*)" for p in (
    "git push", "git remote", "gh repo", "gh release", "rm", "rmdir", "del", "erase", "rd", "Remove-Item",
    "git clean", "git reset --hard", "git rm", "shutdown", "reboot", "poweroff", "systemctl", "format",
    "sudo", "npm publish", "cargo publish"))
SYSTEM = """You are running unattended on the user's PC, started by their personal assistant. Work only inside \
the current folder, which is a fresh project with its own git repository. Commit your work to git with clear \
messages as you reach working states. Never push, never delete files outside this folder, and never publish \
anything. If you need a decision you can't make, stop and explain what you need in your final message. Keep \
your final message to a short summary: what you built, how to run it, and what's left."""

TEST_CMD = re.compile(r"\b(test|tests|pytest|unittest|gut|gdunit|vitest|jest|cargo test|go test|ctest)\b", re.I)


class JobRunner:
    def __init__(self, emit, projects_dir: Path, dry_run: bool = False):
        self.emit = emit
        self.projects_dir = projects_dir
        self.dry = dry_run
        self.procs: dict[int, asyncio.subprocess.Process] = {}
        self.claude_cmd: list[str] | None = None  # tests swap in a fake Claude Code here

    def claude_path(self) -> str | None:
        if self.claude_cmd:
            return self.claude_cmd[0]
        found = shutil.which("claude")
        if found:
            return found
        for p in (Path.home() / ".local/bin/claude.exe", Path.home() / ".local/bin/claude"):
            if p.exists():
                return str(p)
        return None

    def running_ids(self) -> list[int]:
        return [j for j, p in self.procs.items() if p.returncode is None]

    def _ev(self, job_id: int, kind: str, **kw):
        self.emit({"type": "job_event", "job_id": job_id, "kind": kind, **kw})

    async def start(self, job_id: int, project: str, prompt: str, session_id: str | None = None):
        if job_id in self.running_ids():
            raise RuntimeError("That job is already running. Wait for it or cancel it first.")
        claude = self.claude_path()
        if not claude:
            raise RuntimeError("Claude Code isn't installed on this PC. Run: irm https://claude.ai/install.ps1 | iex"
                               if IS_WIN else "Claude Code isn't installed. Run: curl -fsSL https://claude.ai/install.sh | bash")
        folder = self.projects_dir / re.sub(r"[^a-zA-Z0-9_-]", "-", project)
        folder.mkdir(parents=True, exist_ok=True)
        if not (folder / ".git").exists() and shutil.which("git"):
            subprocess.run(["git", "init", "-q"], cwd=folder, check=False,
                           creationflags=0x08000000 if IS_WIN else 0)
        args = [*(self.claude_cmd or [claude]), "-p", INSTRUCTION, "--output-format", "stream-json", "--verbose",
                "--permission-mode", "acceptEdits", "--allowedTools", ALLOWED, "--disallowedTools", DENIED,
                "--append-system-prompt", SYSTEM]
        if session_id:
            args += ["--resume", session_id]
        self._ev(job_id, "log", text=f"{'Continuing' if session_id else 'Starting'} in {folder}")
        if self.dry:
            self._ev(job_id, "done", summary=f"[dry run] would run Claude Code in {folder}")
            return
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=folder, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
            creationflags=0x08000000 if IS_WIN else 0, env={**os.environ, "CI": "1"})
        proc.stdin.write(prompt.encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()
        self.procs[job_id] = proc
        asyncio.create_task(self._watch(job_id, proc))

    async def _watch(self, job_id: int, proc):
        test_calls: dict[str, str] = {}
        tests_passing = None
        got_result = False
        stderr_task = asyncio.create_task(proc.stderr.read())
        async for raw in proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            t = ev.get("type")
            if t == "system" and ev.get("subtype") == "init":
                self._ev(job_id, "log", text="Claude Code started", session_id=ev.get("session_id"))
            elif t == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "text" and b.get("text", "").strip():
                        self._ev(job_id, "log", text=b["text"].strip().split("\n")[0][:200])
                    elif b.get("type") == "tool_use":
                        line = describe_tool(b.get("name", ""), b.get("input") or {})
                        if line:
                            self._ev(job_id, "log", text=line)
                        cmd = (b.get("input") or {}).get("command", "")
                        if b.get("name") == "Bash" and TEST_CMD.search(cmd):
                            test_calls[b.get("id")] = cmd
            elif t == "user":
                for b in (ev.get("message") or {}).get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in test_calls:
                        ok = not b.get("is_error")
                        if ok and tests_passing is not True:
                            self._ev(job_id, "milestone", text="tests passing")
                        elif not ok:
                            self._ev(job_id, "log", text="tests failing, fixing")
                        tests_passing = ok
            elif t == "result":
                got_result = True
                summary = ev.get("result") or ""
                if ev.get("subtype") == "success" and not ev.get("is_error"):
                    self._ev(job_id, "done", summary=summary, session_id=ev.get("session_id"),
                             cost_usd=ev.get("total_cost_usd") or 0)
                else:
                    reason = {"error_max_turns": "hit the turn limit", "error_max_budget_usd": "hit the budget limit",
                              "error_during_execution": "an error stopped it"}.get(ev.get("subtype"), ev.get("subtype"))
                    self._ev(job_id, "failed", summary=summary or f"Claude Code stopped: {reason}.",
                             session_id=ev.get("session_id"))
        await proc.wait()
        err = (await stderr_task).decode("utf-8", "replace").strip()
        self.procs.pop(job_id, None)
        if not got_result and not getattr(proc, "_cancelled", False):
            self._ev(job_id, "failed", summary=(err[-600:] or f"Claude Code exited with code {proc.returncode}."))

    async def cancel(self, job_id: int):
        proc = self.procs.get(job_id)
        if not proc or proc.returncode is not None:
            return
        proc._cancelled = True
        import psutil
        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.Error:
            pass


def describe_tool(name: str, inp: dict) -> str:
    if name == "Bash":
        return "$ " + (inp.get("command") or "").split("\n")[0][:160]
    if name in ("Edit", "Write", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        return f"{'Writing' if name == 'Write' else 'Editing'} {Path(path).name}"
    if name in ("WebSearch",):
        return f"Searching: {inp.get('query', '')[:100]}"
    if name == "TodoWrite":
        todos = inp.get("todos") or []
        doing = next((t.get("content") for t in todos if t.get("status") == "in_progress"), None)
        return f"Now: {doing[:150]}" if doing else ""
    return ""  # reads and searches are too chatty for the log
