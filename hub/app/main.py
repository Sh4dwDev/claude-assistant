"""The hub's web server: dashboard, phone app, chat, setup wizard and API."""
import json
import logging
import time
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from . import agents, auth, chat, commands, config, confirm, costs, db, events, jobs, pc
from . import pc_commands  # noqa: F401  (registers the instant PC commands)
from .notify import notify, vapid_keys
from .setup_steps import SECRET_KEYS, STEPS
from .tools import memory

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).resolve().parents[1] / "static"
STARTED = time.time()

db.init()
vapid_keys()

app = FastAPI(title="Assistant hub", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=config.get_secret("SESSION_SECRET"),
                   session_cookie="assistant", max_age=60 * 60 * 24 * 90, same_site="strict")
authed = [Depends(auth.require)]


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if request.url.path in ("/", "/sw.js", "/manifest.webmanifest") or request.url.path.endswith((".js", ".css")):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# --- pages -------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/sw.js")
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/"})


@app.get("/manifest.webmanifest")
def manifest():
    data = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    data["name"] = data["short_name"] = db.get_setting("assistant_name")
    return JSONResponse(data, media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


# --- login and first-run setup ------------------------------------------------
class PasswordIn(BaseModel):
    password: str


class SetupIn(PasswordIn):
    user_name: str = ""
    home: str = ""


@app.get("/api/state")
def state(request: Request):
    return {"password_set": auth.password_set(), "logged_in": auth.logged_in(request),
            "setup_done": bool(db.get_setting("setup_done")),
            "name": db.get_setting("assistant_name")}


@app.post("/api/setup/password")
def setup_password(body: SetupIn, request: Request):
    if auth.password_set() and not auth.logged_in(request):
        raise HTTPException(403, "A password is already set. Log in to change it.")
    if len(body.password) < 10:
        raise HTTPException(400, "Use at least 10 characters.")
    config.set_secret("PASSWORD_HASH", auth.hash_password(body.password))
    if body.user_name.strip():
        db.set_setting("user_name", body.user_name.strip()[:60])
    if body.home.strip():
        db.set_setting("home", body.home.strip()[:80])
    request.session["auth"] = True
    return {"ok": True}


@app.post("/api/login")
def login(body: PasswordIn, request: Request):
    auth.throttle(request)
    if not auth.check_password(body.password):
        auth.record_failure(request)
        raise HTTPException(401, "Wrong password.")
    request.session["auth"] = True
    return {"ok": True}


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/setup/steps", dependencies=authed)
def setup_steps():
    return [{**s, "set": config.has_secret(s["key"])} for s in STEPS]


class SecretIn(BaseModel):
    key: str
    value: str


@app.post("/api/secrets", dependencies=authed)
async def save_secret(body: SecretIn):
    if body.key not in SECRET_KEYS:
        raise HTTPException(400, "Unknown setting.")
    value = body.value.strip()
    if body.key == "ANTHROPIC_API_KEY" and value:
        try:
            c = anthropic.AsyncAnthropic(api_key=value, max_retries=1)
            await c.models.retrieve(db.get_setting("chat_model"))
        except anthropic.AuthenticationError:
            raise HTTPException(400, "That key was rejected. Copy it again from the Console.")
        except anthropic.APIConnectionError:
            raise HTTPException(400, "Couldn't reach Anthropic to check the key. Is the server online?")
        except anthropic.APIStatusError as e:
            if e.status_code != 404:
                raise HTTPException(400, f"The key check failed ({e.status_code}).")
    if body.key == "DISCORD_WEBHOOK_URL" and value and not value.startswith(
            ("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        raise HTTPException(400, "That doesn't look like a Discord webhook URL.")
    config.set_secret(body.key, value)
    return {"ok": True}


@app.post("/api/setup/done", dependencies=authed)
def setup_done():
    db.set_setting("setup_done", True)
    notify("Assistant is ready", "Setup finished. Say hi in the chat.", push=False)
    return {"ok": True}


# --- settings -------------------------------------------------------------------
EDITABLE = {"assistant_name": str, "user_name": str, "home": str, "timezone": str, "chat_model": str, "chat_effort": str,
            "server_cost_eur": float, "eur_usd": float, "cap_claude_api_usd": float, "cap_phone_usd": float,
            "cap_tts_usd": float, "speak_replies": bool, "pc_idle_sleep_min": int}


@app.get("/api/settings", dependencies=authed)
def get_settings():
    s = db.all_settings()
    return {"settings": {k: s[k] for k in EDITABLE},
            "secrets": {k: config.has_secret(k) for k in sorted(SECRET_KEYS)},
            "models": list(costs.CLAUDE_PRICES)}


@app.post("/api/settings", dependencies=authed)
async def save_settings(body: dict):
    for k, v in body.items():
        if k not in EDITABLE:
            raise HTTPException(400, f"Unknown setting {k}")
        try:
            db.set_setting(k, EDITABLE[k](v))
        except (TypeError, ValueError):
            raise HTTPException(400, f"Bad value for {k}")
    events.publish("costs", {})
    if "pc_idle_sleep_min" in body:
        await agents.push_config()
    return {"ok": True}


# --- chat --------------------------------------------------------------------------
class ChatIn(BaseModel):
    text: str
    conversation_id: int | None = None


@app.post("/api/chat", dependencies=authed)
async def chat_endpoint(body: ChatIn):
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "Empty message.")

    reply = None
    hit = commands.match(text)
    if hit:
        rule, m = hit
        t0 = time.perf_counter()
        try:
            reply = await rule.handler(m)
        except commands.Unclear:
            reply = None  # not sure what was meant: Claude handles it
        ms = int((time.perf_counter() - t0) * 1000)

    if reply is not None:
        async def instant():
            conv_id = body.conversation_id or chat.latest_conversation()
            yield chat.sse("meta", {"conversation_id": conv_id, "instant": True})
            chat.save_exchange(conv_id, text, reply)
            yield chat.sse("delta", {"text": reply})
            yield chat.sse("done", {"cost_usd": 0, "conversation_id": conv_id, "instant": True, "ms": ms})
        gen = instant()
    else:
        gen = chat.run_chat(body.conversation_id, text)

    async def safe():
        try:
            async for chunk in gen:
                yield chunk
        except Exception:  # noqa: BLE001 - never leave the phone with a silent, empty reply
            logging.getLogger("chat").exception("chat failed")
            yield chat.sse("error", {"message": "Something went wrong on the hub. Details are in the hub log."})
    return StreamingResponse(safe(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/conversations", dependencies=authed)
def conversations():
    return db.q("SELECT id, title, updated FROM conversations ORDER BY updated DESC LIMIT 50")


@app.post("/api/conversations", dependencies=authed)
def new_conversation():
    return {"id": chat.new_conversation()}


@app.get("/api/conversations/{conv_id}", dependencies=authed)
def conversation(conv_id: int):
    if not db.q1("SELECT id FROM conversations WHERE id=?", (conv_id,)):
        raise HTTPException(404)
    return {"id": conv_id, "messages": chat.history_for_ui(conv_id)}


@app.get("/api/conversations/latest/id", dependencies=authed)
def latest_conversation():
    return {"id": chat.latest_conversation()}


# --- memory ---------------------------------------------------------------------------
class MemoryIn(BaseModel):
    text: str
    category: str = "general"


@app.get("/api/memories", dependencies=authed)
def memories():
    return memory.list_memories()


@app.post("/api/memories", dependencies=authed)
def add_memory(body: MemoryIn):
    return {"id": memory.add(body.text, body.category)}


@app.put("/api/memories/{mid}", dependencies=authed)
def edit_memory(mid: int, body: MemoryIn):
    if not memory.update(mid, body.text, body.category):
        raise HTTPException(404)
    return {"ok": True}


@app.delete("/api/memories/{mid}", dependencies=authed)
def delete_memory(mid: int):
    memory.delete(mid)
    return {"ok": True}


# --- notifications and push ---------------------------------------------------------------
@app.get("/api/events", dependencies=authed)
async def event_stream():
    return StreamingResponse(events.stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/notifications", dependencies=authed)
def notifications():
    return db.q("SELECT * FROM notifications ORDER BY id DESC LIMIT 30")


@app.post("/api/notifications/read", dependencies=authed)
def notifications_read():
    db.run("UPDATE notifications SET read=1 WHERE read=0")
    return {"ok": True}


@app.post("/api/notifications/test", dependencies=authed)
def notifications_test():
    notify("Test notification", "If you see this on your phone, push works.")
    return {"ok": True}


@app.get("/api/push/key", dependencies=authed)
def push_key():
    return {"key": vapid_keys()[1]}


@app.post("/api/push/subscribe", dependencies=authed)
def push_subscribe(sub: dict):
    endpoint = sub.get("endpoint")
    if not endpoint or not str(endpoint).startswith("https://"):
        raise HTTPException(400, "Bad subscription.")
    db.run("INSERT INTO push_subscriptions(endpoint, data, created) VALUES(?,?,?) "
           "ON CONFLICT(endpoint) DO UPDATE SET data=excluded.data", (endpoint, json.dumps(sub), time.time()))
    return {"ok": True}


@app.post("/api/push/unsubscribe", dependencies=authed)
def push_unsubscribe(sub: dict):
    db.run("DELETE FROM push_subscriptions WHERE endpoint=?", (sub.get("endpoint"),))
    return {"ok": True}


# --- dashboard panels ---------------------------------------------------------------------
@app.get("/api/costs", dependencies=authed)
def costs_summary():
    return costs.summary()


@app.get("/api/status", dependencies=authed)
def status():
    return {
        "hub": {"uptime_s": int(time.time() - STARTED), "push_devices":
                db.q1("SELECT COUNT(*) AS n FROM push_subscriptions")["n"]},
        **agents.summary(),
        "jobs": jobs.recent(8),
        "confirmations": confirm.listing(),
        "mail": {"connected": False, "note": "Gmail arrives in milestone 4"},
    }


# --- PC agents ------------------------------------------------------------------------------
@app.post("/api/agent/pair-code", dependencies=authed)
def agent_pair_code():
    return agents.new_pair_code()


class PairIn(BaseModel):
    code: str
    name: str
    os: str
    role: str = "pc"
    mac: str = ""
    broadcast: str = ""


@app.post("/api/agent/pair")
def agent_pair(body: PairIn, request: Request):
    auth.throttle(request)
    if body.role not in ("pc", "relay"):
        raise HTTPException(400, "role must be pc or relay")
    token = agents.pair(body.code, body.name.strip()[:60] or "PC", body.os[:40], body.role, body.mac[:20],
                        body.broadcast[:40])
    if not token:
        auth.record_failure(request)
        raise HTTPException(401, "That pairing code is wrong or expired. Make a new one in the dashboard.")
    return {"token": token}


@app.websocket("/api/agent/ws")
async def agent_ws(ws: WebSocket):
    token = (ws.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    agent = agents.agent_for_token(token)
    if not agent:
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        await agents.serve(ws, agent)
    except WebSocketDisconnect:
        pass


@app.post("/api/pc/wake", dependencies=authed)
async def pc_wake():
    return {"message": await agents.wake()}


@app.post("/api/pc/{agent_id}/forget", dependencies=authed)
def pc_forget(agent_id: int):
    agents.forget(agent_id)
    return {"ok": True}


@app.get("/api/screens/{sid}", dependencies=authed)
def screen(sid: str):
    p = pc.screen_path(sid)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, media_type="image/jpeg")


# --- jobs ----------------------------------------------------------------------------------------------
@app.get("/api/jobs", dependencies=authed)
def jobs_list():
    return jobs.recent(20)


@app.get("/api/jobs/{job_id}", dependencies=authed)
def job_detail(job_id: int):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    return {**j, "logs": jobs.logs(job_id)}


class TextIn(BaseModel):
    text: str


@app.post("/api/jobs/{job_id}/cancel", dependencies=authed)
async def job_cancel(job_id: int):
    return {"message": await jobs.cancel(job_id)}


@app.post("/api/jobs/{job_id}/followup", dependencies=authed)
async def job_followup(job_id: int, body: TextIn):
    try:
        return {"message": await jobs.followup(job_id, body.text)}
    except (agents.AgentOffline, agents.AgentError) as e:
        raise HTTPException(409, str(e))


# --- confirmations -------------------------------------------------------------------------------------
class ConfirmIn(BaseModel):
    approve: bool
    conversation_id: int | None = None


@app.get("/api/confirm", dependencies=authed)
def confirm_list():
    return confirm.listing()


@app.post("/api/confirm/{pid}", dependencies=authed)
async def confirm_resolve(pid: str, body: ConfirmIn):
    result = await confirm.resolve(pid, body.approve)
    if body.conversation_id:
        chat.save_note(body.conversation_id, result)
    return {"message": result}
