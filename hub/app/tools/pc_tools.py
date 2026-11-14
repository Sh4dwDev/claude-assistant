"""PC control and coding-job tools for Claude. Used when an instant command didn't match
or was unclear. Risky actions only ever create a confirmation for the user."""
from .. import agents, confirm, jobs, pc
from . import Tool

ASKED = "Asked the user: “{q}” A Yes/No prompt is showing in the dashboard. Tell them briefly and wait; don't call this again."


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


async def _guard(coro):
    try:
        return await coro
    except agents.AgentOffline:
        p = agents.primary()
        return "No PC is paired." if not p else f"{p['name']} is offline. pc_wake can turn it on."
    except agents.AgentError as e:
        return f"The PC reported an error: {e}"


async def _status(a):
    s = agents.summary()["agents"]
    if not s:
        return "No PC is paired yet."
    lines = []
    for x in s:
        st = x["status"]
        line = f"{x['name']} ({x['os']}, {x['role']}): {'online' if x['online'] else 'offline'}"
        if x["online"] and st:
            line += f", CPU {st.get('cpu', '?')}%, RAM {st.get('ram', '?')}%, active window: {st.get('active') or 'none'}"
        lines.append(line)
    lines.append(f"Coding jobs running: {jobs.running_count()}")
    return "\n".join(lines)


async def _open_app(a):
    async def go():
        res = await pc.open_app(a["name"])
        if res.get("opened"):
            return f"Opened {res['opened']}."
        if res.get("matches"):
            return "Several apps match: " + ", ".join(res["matches"][:10]) + ". Ask which one, or pick the obvious one."
        return "No installed app matches that name. Try pc_list_apps with a shorter query."
    return await _guard(go())


async def _list_apps(a):
    async def go():
        res = await agents.call("list_apps", {"query": a.get("query", "")})
        apps = res.get("apps", [])
        return ("Installed apps: " + ", ".join(apps[:80])) if apps else "No apps match."
    return await _guard(go())


async def _open_url(a):
    url = a["url"] if a["url"].startswith(("http://", "https://")) else "https://" + a["url"]
    return await _guard(pc.open_url(url))


async def _search(a):
    async def go():
        msg = await pc.search(a.get("site") or "google", a["query"])
        return msg or f"Unknown site. Known: {', '.join(sorted(pc.SEARCH))}. Use pc_open_url with a search URL instead."
    return await _guard(go())


async def _media(a):
    return await _guard(pc.media(a["action"]))


async def _volume(a):
    return await _guard(pc.volume(level=a.get("level"), delta=a.get("change"), mute=a.get("mute")))


async def _screenshot(a):
    async def go():
        md, b64 = await pc.screenshot()
        return [
            {"type": "text", "text": f"Screenshot of the PC screen. It's saved; to show it to the user include this exact markdown in your reply: {md}"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
        ]
    return await _guard(go())


async def _lock(a):
    return await _guard(pc.lock())


async def _sleep(a):
    async def go():
        before = confirm.latest()
        msg = await pc.sleep_pc()
        after = confirm.latest()
        return ASKED.format(q=msg) if after and after is not before else msg
    return await _guard(go())


async def _power(a):
    return await _guard(_asked(pc.power(a["kind"])))


async def _close(a):
    async def go():
        q = await pc.ask_close(a["name"])
        return ASKED.format(q=q) if q.endswith("?") or "?" in q else q
    return await _guard(go())


async def _asked(coro):
    q = await coro
    return ASKED.format(q=q)


async def _wake(a):
    return await agents.wake()


# --- coding jobs -----------------------------------------------------------------------
async def _start_job(a):
    try:
        job = await jobs.create(a["request"], title=a.get("title", ""), project=a.get("project_name", ""))
    except agents.AgentOffline as e:
        return str(e)
    state = {"running": "started", "waking": "queued while the PC wakes up"}.get(job["status"], job["status"])
    return (f"Job {job['id']} {state}: Claude Code is working in Projects/{job['project']} on the PC. "
            "Progress shows in the Jobs panel and as notifications.")


async def _job_status(a):
    if a.get("job_id"):
        j = jobs.get(a["job_id"])
        if not j:
            return f"No job {a['job_id']}."
        recent_log = "\n".join(x["text"] for x in jobs.logs(j["id"], 15))
        return jobs.describe(j) + (f"\nRecent log:\n{recent_log}" if recent_log else "")
    rows = jobs.recent(5)
    return "\n".join(jobs.describe(j) for j in rows) if rows else "No jobs yet."


async def _followup(a):
    try:
        return await jobs.followup(a["job_id"], a["instructions"])
    except (agents.AgentOffline, agents.AgentError) as e:
        return f"Couldn't send it: {e}"


async def _cancel_job(a):
    j = jobs.get(a["job_id"])
    if not j:
        return f"No job {a['job_id']}."

    async def act():
        return await jobs.cancel(a["job_id"])
    q = f"Cancel job {j['id']}: {j['title']}?"
    confirm.ask(q, act)
    return ASKED.format(q=q)


TOOLS = [
    Tool("pc_status", "Show whether the user's PC is on, its load, the active window, and running coding jobs.",
         _obj({}), _status),
    Tool("pc_open_app", "Open an installed program on the user's PC by name (fuzzy match), e.g. 'Godot', 'Discord'.",
         _obj({"name": {"type": "string"}}, ["name"]), _open_app),
    Tool("pc_list_apps", "List programs installed on the PC, optionally filtered by a word. Use to resolve unclear app names.",
         _obj({"query": {"type": "string"}}), _list_apps),
    Tool("pc_open_url", "Open a website in the PC's default browser.",
         _obj({"url": {"type": "string"}}, ["url"]), _open_url),
    Tool("pc_search", "Search a site in the PC's browser (google, youtube, wikipedia, github, reddit, maps, steam, spotify, ...).",
         _obj({"query": {"type": "string"}, "site": {"type": "string"}}, ["query"]), _search),
    Tool("pc_media", "Control media playback on the PC.",
         _obj({"action": {"type": "string", "enum": ["play_pause", "next", "previous", "stop"]}}, ["action"]), _media),
    Tool("pc_volume", "Set the PC volume: an absolute level 0-100, a relative change like -10, or mute true/false.",
         _obj({"level": {"type": "integer"}, "change": {"type": "integer"}, "mute": {"type": "boolean"}}), _volume),
    Tool("pc_screenshot", "Take a screenshot of the PC screen so you can see it (and show it to the user).",
         _obj({}), _screenshot),
    Tool("pc_lock", "Lock the PC.", _obj({}), _lock),
    Tool("pc_sleep", "Put the PC to sleep. Asks the user first if jobs are running or work is unsaved.", _obj({}), _sleep),
    Tool("pc_power", "Shut down or restart the PC. Always asks the user to confirm first.",
         _obj({"kind": {"type": "string", "enum": ["shutdown", "restart"]}}, ["kind"]), _power, risky=True),
    Tool("pc_close_app", "Close a program on the PC. Always asks the user to confirm first.",
         _obj({"name": {"type": "string"}}, ["name"]), _close, risky=True),
    Tool("pc_wake", "Turn on (Wake-on-LAN) the user's PC when it is off or asleep.", _obj({}), _wake),
    Tool("start_coding_job",
         "Start a coding job: Claude Code works on the user's PC in a new project folder with its own git repo. "
         "Use for building or changing software (e.g. 'make me a Godot prototype of X'). Write the request as a "
         "complete, specific brief. Wakes the PC if needed.",
         _obj({"request": {"type": "string", "description": "Full brief for Claude Code."},
               "title": {"type": "string", "description": "Short title, under 60 characters."},
               "project_name": {"type": "string", "description": "Folder name, e.g. 'space-miner'."}},
              ["request", "title"]), _start_job),
    Tool("job_status", "Status and recent log of a coding job (or the latest jobs if no id).",
         _obj({"job_id": {"type": "integer"}}), _job_status),
    Tool("job_followup", "Send follow-up instructions to an existing coding job (continues the same Claude Code session).",
         _obj({"job_id": {"type": "integer"}, "instructions": {"type": "string"}}, ["job_id", "instructions"]), _followup),
    Tool("cancel_job", "Cancel a running coding job. Asks the user to confirm first.",
         _obj({"job_id": {"type": "integer"}}, ["job_id"]), _cancel_job, risky=True),
]
