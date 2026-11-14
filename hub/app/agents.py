"""PC agents: pairing, the live WebSocket to each PC, remote calls, Wake-on-LAN.

The agent connects OUT to the hub (over Tailscale), so nothing listens on the PC.
Protocol (JSON text frames):
  agent -> hub  {"type":"hello", name, os, version, mac, broadcast, claude_code, apps}
                {"type":"status", cpu, ram, active, idle_s, uptime_s}
                {"type":"result", id, ok, data | error}
                {"type":"job_event", job_id, kind, text?, summary?, session_id?, cost_usd?}
  hub -> agent  {"type":"call", id, action, args}
                {"type":"config", idle_sleep_min}
"""
import asyncio
import hashlib
import json
import logging
import secrets
import socket
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

from . import db, events

log = logging.getLogger("agents")

PAIR_TTL = 600
_pair_codes: dict[str, float] = {}


class AgentOffline(Exception):
    pass


class AgentError(Exception):
    pass


@dataclass
class Conn:
    agent_id: int
    ws: WebSocket
    info: dict = field(default_factory=dict)
    status: dict = field(default_factory=dict)
    pending: dict[str, asyncio.Future] = field(default_factory=dict)


_conns: dict[int, Conn] = {}


# --- pairing -----------------------------------------------------------------
def new_pair_code() -> dict:
    now = time.time()
    for c, exp in list(_pair_codes.items()):
        if exp < now:
            del _pair_codes[c]
    code = f"{secrets.randbelow(10**6):06d}"
    _pair_codes[code] = now + PAIR_TTL
    return {"code": code, "expires_in": PAIR_TTL}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pair(code: str, name: str, os_name: str, role: str = "pc", mac: str = "", broadcast: str = "") -> str | None:
    exp = _pair_codes.pop(code.strip(), None)
    if not exp or exp < time.time():
        return None
    token = secrets.token_urlsafe(32)
    existing = db.q1("SELECT id FROM agents WHERE name=?", (name,))
    if existing:  # re-pairing the same PC replaces its old token
        db.run("UPDATE agents SET os=?, role=?, mac=?, broadcast=?, token_hash=? WHERE id=?",
               (os_name, role, mac, broadcast, _hash(token), existing["id"]))
    else:
        db.run("INSERT INTO agents(name, os, role, mac, broadcast, token_hash, created) VALUES(?,?,?,?,?,?,?)",
               (name, os_name, role, mac, broadcast, _hash(token), time.time()))
    events.publish("pc", {})
    return token


def agent_for_token(token: str) -> dict | None:
    return db.q1("SELECT * FROM agents WHERE token_hash=?", (_hash(token),)) if token else None


def forget(agent_id: int) -> None:
    db.run("DELETE FROM agents WHERE id=?", (agent_id,))
    c = _conns.pop(agent_id, None)
    if c:
        asyncio.create_task(c.ws.close())
    events.publish("pc", {})


# --- connection ---------------------------------------------------------------------
def online(agent_id: int | None = None) -> bool:
    return (agent_id in _conns) if agent_id else bool(_conns)


def primary() -> dict | None:
    """The PC that commands go to: the first paired one that isn't a wake relay."""
    return db.q1("SELECT * FROM agents WHERE role='pc' ORDER BY id LIMIT 1")


async def serve(ws: WebSocket, agent: dict) -> None:
    conn = Conn(agent["id"], ws)
    old = _conns.get(agent["id"])
    if old:
        try:
            await old.ws.close()
        except Exception:  # noqa: BLE001
            pass
    _conns[agent["id"]] = conn
    db.run("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent["id"]))
    await ws.send_text(json.dumps({"type": "config", "idle_sleep_min": int(db.get_setting("pc_idle_sleep_min") or 0)}))
    log.info("agent %s connected", agent["name"])
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            t = msg.get("type")
            if t == "hello":
                conn.info = msg
                if msg.get("mac"):
                    db.run("UPDATE agents SET mac=?, broadcast=? WHERE id=?",
                           (msg["mac"], msg.get("broadcast", ""), agent["id"]))
                events.publish("pc", {})
                from . import jobs
                asyncio.create_task(jobs.dispatch_queued(agent["id"], msg.get("jobs")))
            elif t == "status":
                conn.status = msg
                db.run("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent["id"]))
                events.publish("pc_status", {"agent_id": agent["id"], **msg})
            elif t == "result":
                fut = conn.pending.pop(msg.get("id"), None)
                if fut and not fut.done():
                    fut.set_result(msg)
            elif t == "job_event":
                from . import jobs
                jobs.on_event(msg)
    finally:
        if _conns.get(agent["id"]) is conn:
            del _conns[agent["id"]]
        for fut in conn.pending.values():
            if not fut.done():
                fut.set_exception(AgentOffline("PC disconnected"))
        log.info("agent %s disconnected", agent["name"])
        events.publish("pc", {})


async def call(action: str, args: dict | None = None, agent_id: int | None = None, timeout: float = 20) -> dict:
    """Run an action on a PC and return its data. Raises AgentOffline / AgentError."""
    if agent_id is None:
        p = primary()
        agent_id = p["id"] if p else None
    conn = _conns.get(agent_id) if agent_id else None
    if not conn:
        raise AgentOffline("The PC is offline.")
    cid = secrets.token_hex(6)
    fut = asyncio.get_running_loop().create_future()
    conn.pending[cid] = fut
    await conn.ws.send_text(json.dumps({"type": "call", "id": cid, "action": action, "args": args or {}}))
    try:
        res = await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        conn.pending.pop(cid, None)
        raise AgentError("The PC didn't answer in time.")
    if not res.get("ok"):
        raise AgentError(res.get("error") or "The PC reported an error.")
    return res.get("data") or {}


async def push_config() -> None:
    msg = json.dumps({"type": "config", "idle_sleep_min": int(db.get_setting("pc_idle_sleep_min") or 0)})
    for c in list(_conns.values()):
        try:
            await c.ws.send_text(msg)
        except Exception:  # noqa: BLE001
            pass


def summary() -> dict:
    rows = db.q("SELECT id, name, os, role, mac, last_seen FROM agents ORDER BY id")
    p = primary()
    out = []
    for r in rows:
        c = _conns.get(r["id"])
        out.append({**r, "online": bool(c), "status": c.status if c else {},
                    "claude_code": bool(c and c.info.get("claude_code")),
                    "primary": bool(p and p["id"] == r["id"])})
    return {"agents": out}


# --- Wake-on-LAN ------------------------------------------------------------------------
def magic_packet(mac: str) -> bytes:
    raw = bytes.fromhex(mac.replace(":", "").replace("-", ""))
    if len(raw) != 6:
        raise ValueError("bad MAC address")
    return b"\xff" * 6 + raw * 16


async def wake(agent_id: int | None = None) -> str:
    """Wake a sleeping/off PC. A VPS can't reach your home network, so another device
    at home that runs the agent (a Pi, a laptop) relays the packet. Returns a sentence."""
    target = db.q1("SELECT * FROM agents WHERE id=?", (agent_id,)) if agent_id else primary()
    if not target:
        return "No PC is paired yet."
    if online(target["id"]):
        return f"{target['name']} is already on."
    if not target["mac"]:
        return f"I don't know {target['name']}'s network address yet. It's learned the first time the agent runs."
    relays = [aid for aid in _conns if aid != target["id"]]
    for rid in relays:
        try:
            await call("wol", {"mac": target["mac"], "broadcast": target["broadcast"] or "255.255.255.255"},
                       agent_id=rid, timeout=8)
            relay_name = db.q1("SELECT name FROM agents WHERE id=?", (rid,))["name"]
            return f"Wake signal sent to {target['name']} through {relay_name}. It usually takes 10-30 seconds."
        except (AgentOffline, AgentError) as e:
            log.warning("relay %s failed: %s", rid, e)
    # Last resort: works only when the hub itself sits on the home network (e.g. a Pi with host networking).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(magic_packet(target["mac"]), (target["broadcast"] or "255.255.255.255", 9))
        s.close()
    except OSError:
        pass
    return (f"Sent a wake signal, but no other device at home is online to relay it. That only works if the "
            f"hub is on your home network. Run the agent on an always-on device at home, like a Raspberry Pi, "
            f"to make wake work from anywhere.")
