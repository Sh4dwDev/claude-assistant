"""Coding jobs: run Claude Code on the PC, wake it if needed, stream progress back."""
import asyncio
import logging
import re
import time

from . import agents, db, events
from .notify import notify

log = logging.getLogger("jobs")

ACTIVE = ("queued", "waking", "running")


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:40].rstrip("-") or "project")


def get(job_id: int) -> dict | None:
    return db.q1("SELECT * FROM jobs WHERE id=?", (job_id,))


def recent(limit: int = 10) -> list[dict]:
    return db.q("SELECT id, title, project, status, summary, last_log, cost_usd, created, updated "
                "FROM jobs ORDER BY id DESC LIMIT ?", (limit,))


def logs(job_id: int, limit: int = 300) -> list[dict]:
    rows = db.q("SELECT ts, text FROM job_logs WHERE job_id=? ORDER BY id DESC LIMIT ?", (job_id, limit))
    return list(reversed(rows))


def _update(job_id: int, **fields) -> None:
    fields["updated"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    db.run(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
    events.publish("job", {"id": job_id, **{k: v for k, v in fields.items() if k != "summary"}})


def _log(job_id: int, text: str) -> None:
    text = text.strip()[:500]
    if not text:
        return
    db.run("INSERT INTO job_logs(job_id, ts, text) VALUES(?,?,?)", (job_id, time.time(), text))
    db.run("UPDATE jobs SET last_log=?, updated=? WHERE id=?", (text[:200], time.time(), job_id))
    events.publish("job_log", {"id": job_id, "text": text})


async def create(request: str, title: str = "", project: str = "") -> dict:
    title = (title or request).strip().split("\n")[0][:80]
    project = slug(project or title)
    pc = agents.primary()
    if not pc:
        raise agents.AgentOffline("No PC is paired yet. Pair one from the PC panel first.")
    jid = db.run("INSERT INTO jobs(agent_id, title, project, prompt, status, created, updated) VALUES(?,?,?,?,?,?,?)",
                 (pc["id"], title, project, request, "queued", time.time(), time.time()))
    events.publish("job", {"id": jid, "status": "queued"})
    if agents.online(pc["id"]):
        await _start(jid)
    else:
        _update(jid, status="waking")
        msg = await agents.wake(pc["id"])
        _log(jid, msg)
    return get(jid)


async def _start(job_id: int) -> None:
    job = get(job_id)
    try:
        await agents.call("job_start", {"job_id": job_id, "project": job["project"], "prompt": job["prompt"]},
                          agent_id=job["agent_id"], timeout=30)
        _update(job_id, status="running")
        notify(f"Job started: {job['title']}", f"Working in Projects/{job['project']}", url="/#jobs", push=False)
    except (agents.AgentOffline, agents.AgentError) as e:
        _update(job_id, status="failed")
        _log(job_id, f"Couldn't start: {e}")
        notify(f"Job couldn't start: {job['title']}", str(e), level="warn")


async def dispatch_queued(agent_id: int, running_on_pc: list[int] | None = None) -> None:
    """Called when a PC connects: mark jobs lost to a PC restart, then start jobs that waited for it."""
    alive = set(running_on_pc or [])
    for j in db.q("SELECT id, title FROM jobs WHERE agent_id=? AND status='running'", (agent_id,)):
        if j["id"] not in alive:
            _update(j["id"], status="failed", summary="The PC restarted or the agent stopped while this was running. "
                                                      "Send a follow-up to continue where it left off.")
            _log(j["id"], "Interrupted: the PC restarted.")
    await asyncio.sleep(1)
    for j in db.q("SELECT id FROM jobs WHERE agent_id=? AND status IN ('queued','waking') ORDER BY id", (agent_id,)):
        await _start(j["id"])


async def followup(job_id: int, text: str) -> str:
    job = get(job_id)
    if not job:
        return f"There's no job {job_id}."
    if job["status"] in ("queued", "waking"):
        return "That job hasn't started yet."
    await agents.call("job_followup", {"job_id": job_id, "project": job["project"], "prompt": text,
                                       "session_id": job["session_id"]}, agent_id=job["agent_id"], timeout=30)
    _update(job_id, status="running")
    _log(job_id, f"Follow-up: {text}")
    return f"Sent to job {job_id}."


async def cancel(job_id: int) -> str:
    job = get(job_id)
    if not job:
        return f"There's no job {job_id}."
    if job["status"] not in ACTIVE:
        return f"Job {job_id} isn't running."
    if job["status"] == "running":
        try:
            await agents.call("job_cancel", {"job_id": job_id}, agent_id=job["agent_id"])
        except (agents.AgentOffline, agents.AgentError):
            pass
    _update(job_id, status="cancelled")
    _log(job_id, "Cancelled.")
    return f"Cancelled job {job_id}: {job['title']}."


def on_event(msg: dict) -> None:
    job_id = msg.get("job_id")
    job = get(job_id) if job_id else None
    if not job:
        return
    kind = msg.get("kind")
    if job["status"] == "cancelled" and kind in ("done", "failed", "milestone"):
        return
    if msg.get("session_id"):
        db.run("UPDATE jobs SET session_id=? WHERE id=?", (msg["session_id"], job_id))
    if kind == "log":
        _log(job_id, msg.get("text", ""))
    elif kind == "milestone":  # e.g. "tests passing"
        _log(job_id, msg.get("text", ""))
        notify(f"{job['title']}: {msg.get('text')}", "", url="/#jobs", push=True)
    elif kind == "done":
        summary = (msg.get("summary") or "Finished.").strip()
        _update(job_id, status="done", summary=summary[:4000], cost_usd=float(msg.get("cost_usd") or 0))
        notify(f"Done: {job['title']}", summary[:300], url="/#jobs")
    elif kind == "failed":
        _update(job_id, status="failed", summary=(msg.get("summary") or "")[:4000])
        notify(f"Job failed: {job['title']}", (msg.get("summary") or "See the job log.")[:300], url="/#jobs", level="warn")


def running_count(agent_id: int | None = None) -> int:
    if agent_id:
        r = db.q1("SELECT COUNT(*) AS n FROM jobs WHERE agent_id=? AND status='running'", (agent_id,))
    else:
        r = db.q1("SELECT COUNT(*) AS n FROM jobs WHERE status='running'")
    return r["n"]


def describe(job: dict) -> str:
    s = f"Job {job['id']} '{job['title']}' (Projects/{job['project']}): {job['status']}"
    if job.get("last_log"):
        s += f". Latest: {job['last_log']}"
    if job["status"] in ("done", "failed") and job.get("summary"):
        s += f"\nSummary: {job['summary'][:1500]}"
    return s
