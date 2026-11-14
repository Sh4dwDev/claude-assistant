"""PC agent: keeps one connection open to the hub and runs what it asks.

  python agent.py pair --hub https://assistant.your-tailnet.ts.net --code 123456 [--role relay] [--name NAME]
  python agent.py run [--dry-run]

--dry-run logs every action instead of doing it (for testing).
"""
import argparse
import asyncio
import base64
import collections
import difflib
import io
import json
import logging
import os
import platform
import re
import socket
import sys
import time
import urllib.request
from pathlib import Path

VERSION = "0.2.0"
IS_WIN = sys.platform == "win32"
HOME_DIR = Path(os.environ["CLAUDE_AGENT_HOME"]) if os.environ.get("CLAUDE_AGENT_HOME") else \
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "ClaudeAgent" if IS_WIN \
    else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "claude-agent"
CONFIG = HOME_DIR / "config.json"
LOCK_PORT = 47811

log = logging.getLogger("agent")

if IS_WIN:
    import platform_win as plat
else:
    import platform_linux as plat

from jobs import JobRunner  # noqa: E402


# --- config -------------------------------------------------------------------------
def load_config() -> dict:
    if not CONFIG.exists():
        sys.exit("Not paired yet. Run the install command from the dashboard (PC panel > Pair a PC).")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def save_config(cfg: dict) -> None:
    HOME_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if not IS_WIN:
        CONFIG.chmod(0o600)


def network_identity() -> tuple[str, str]:
    """MAC and broadcast address of the interface that reaches the internet (for Wake-on-LAN)."""
    import ipaddress

    import psutil
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))  # no packet is sent; this only picks the outgoing interface
        local_ip = s.getsockname()[0]
        s.close()
    except OSError:
        return "", ""
    for name, addrs in psutil.net_if_addrs().items():
        ipv4 = next((a for a in addrs if a.family == socket.AF_INET and a.address == local_ip), None)
        if not ipv4:
            continue
        mac = next((a.address for a in addrs if a.family == psutil.AF_LINK), "")
        bcast = ""
        if ipv4.netmask:
            net = ipaddress.IPv4Network(f"{local_ip}/{ipv4.netmask}", strict=False)
            bcast = str(net.broadcast_address)
        return mac.replace("-", ":").lower(), bcast
    return "", ""


def pair(hub: str, code: str, role: str, name: str) -> None:
    hub = hub.rstrip("/")
    mac, bcast = network_identity()
    body = json.dumps({"code": code, "name": name, "os": plat.OS_NAME, "role": role,
                       "mac": mac, "broadcast": bcast}).encode()
    req = urllib.request.Request(hub + "/api/agent/pair", data=body, headers={"Content-Type": "application/json"})
    try:
        token = json.loads(urllib.request.urlopen(req, timeout=20).read())["token"]
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}").get("detail", e.reason)
        sys.exit(f"Pairing failed: {detail}")
    except urllib.error.URLError as e:
        sys.exit(f"Can't reach the hub at {hub}: {e.reason}. Is Tailscale on?")
    default_projects = Path.home() / "Projects"
    save_config({"hub": hub, "token": token, "name": name, "role": role, "projects_dir": str(default_projects)})
    print(f"Paired as '{name}'. Settings saved to {CONFIG}")


# --- app matching ------------------------------------------------------------------------------
SKIP = re.compile(r"\b(uninstall|readme|read me|help|documentation|manual|license|website|release notes|"
                  r"what'?s new|support|changelog)\b", re.I)


def norm(s: str) -> str:
    s = re.sub(r"\(.*?\)|\bv?\d+(\.\d+)+\S*", " ", s.lower())
    return re.sub(r"[^a-z0-9æøå]+", " ", s).strip()


class AppIndex:
    def __init__(self):
        self.apps: dict[str, object] = {}  # display name -> launch target
        self.updated = 0.0

    async def refresh(self, force=False):
        if force or time.time() - self.updated > 1800:
            try:
                found = await asyncio.to_thread(plat.scan_apps)
            except Exception:  # noqa: BLE001 - a broken app list must never take the agent offline
                log.exception("app scan failed")
                self.updated = time.time()
                return
            self.apps = {n: t for n, t in found.items() if not SKIP.search(n)}
            self.updated = time.time()
            log.info("indexed %d apps", len(self.apps))

    def match(self, query: str) -> tuple[str | None, list[str]]:
        """Returns (name, []) for a confident match, else (None, candidates)."""
        q = norm(query)
        if not q:
            return None, []
        names = {n: norm(n) for n in self.apps}
        exact = [n for n, k in names.items() if k == q]
        if exact:
            return min(exact, key=len), []
        words = q.split()
        contains = [n for n, k in names.items() if all(re.search(rf"\b{re.escape(w)}", k) for w in words)]
        if len(contains) == 1:
            return contains[0], []
        if contains:
            starts = sorted((n for n in contains if names[n].startswith(q)), key=len)
            # "godot" -> "Godot Engine" beats "Godot Engine (Mono)"; clear winner only
            if starts and (len(starts) == 1 or len(names[starts[0]]) + 4 <= len(names[starts[1]])):
                return starts[0], []
            return None, sorted(contains, key=len)[:10]
        close = difflib.get_close_matches(q, list(names.values()), n=3, cutoff=0.82)
        hits = [n for n, k in names.items() if k in close]
        if len(set(names[h] for h in hits)) == 1 and hits:
            return min(hits, key=len), []
        return None, hits[:10]


# --- the agent -------------------------------------------------------------------------------------
class Agent:
    def __init__(self, cfg: dict, dry_run: bool):
        self.cfg = cfg
        self.dry = dry_run
        self.apps = AppIndex()
        self.ws = None
        self.outbox: collections.deque = collections.deque(maxlen=500)  # job events kept while offline
        self.idle_sleep_min = 0
        self.slept_for_idle = False
        self.power_task: asyncio.Task | None = None
        self.jobs = JobRunner(self.emit, Path(cfg.get("projects_dir") or Path.home() / "Projects"), dry_run)

    # outgoing
    def emit(self, msg: dict) -> None:
        self.outbox.append(msg)
        asyncio.get_running_loop().create_task(self.flush())

    async def flush(self):
        while self.ws and self.outbox:
            msg = self.outbox[0]
            try:
                await self.ws.send(json.dumps(msg))
                self.outbox.popleft()
            except Exception:  # noqa: BLE001
                return

    async def send(self, msg: dict):
        if self.ws:
            await self.ws.send(json.dumps(msg))

    # actions -------------------------------------------------------------
    async def act(self, action: str, a: dict):
        if self.dry and action not in ("ping", "list_apps", "screenshot", "unsaved_windows", "find_windows",
                                       "job_start", "job_followup", "job_cancel"):
            log.info("[dry-run] %s %s", action, a)
            if action == "open_app":
                await self.apps.refresh()
                name, cands = self.apps.match(a["name"])
                return {"opened": name} if name else {"matches": cands}
            return {"dry_run": True, "level": a.get("level")}

        if action == "ping":
            return {"pong": True, "version": VERSION}
        if action == "list_apps":
            await self.apps.refresh()
            q = norm(a.get("query", ""))
            return {"apps": sorted(n for n in self.apps.apps if q in norm(n))[:200]}
        if action == "open_app":
            await self.apps.refresh()
            name, cands = self.apps.match(a["name"])
            if not name:
                await self.apps.refresh(force=True)  # maybe just installed
                name, cands = self.apps.match(a["name"])
            if name:
                await asyncio.to_thread(plat.launch, self.apps.apps[name])
                return {"opened": name}
            return {"matches": cands}
        if action == "open_url":
            url = a["url"]
            if not re.match(r"^(https?://|about:blank$)", url):
                raise ValueError("Only web addresses can be opened.")
            await asyncio.to_thread(plat.open_url, url)
            return {}
        if action == "media":
            await asyncio.to_thread(plat.media, a["cmd"])
            return {}
        if action == "volume":
            return {"level": await asyncio.to_thread(plat.volume, a.get("level"), a.get("delta"), a.get("mute"))}
        if action == "lock":
            await asyncio.to_thread(plat.lock)
            return {}
        if action == "sleep":
            asyncio.get_running_loop().call_later(1.5, lambda: asyncio.ensure_future(asyncio.to_thread(plat.sleep)))
            return {}
        if action == "power":
            return await self.power(a["kind"])
        if action == "find_windows":
            wins = await asyncio.to_thread(plat.find_windows, a["name"])
            return {"windows": wins, "label": wins[0]["app"] if wins else a["name"]}
        if action == "close_app":
            wins = await asyncio.to_thread(plat.find_windows, a["name"])
            if not wins:
                return {"closed": False}
            closed = await asyncio.to_thread(plat.close_windows, wins)
            return {"closed": closed, "label": wins[0]["app"]}
        if action == "unsaved_windows":
            wins = await asyncio.to_thread(plat.all_windows)
            return {"titles": [w["title"] for w in wins if w.get("unsaved")]}
        if action == "screenshot":
            return {"jpeg_b64": await asyncio.to_thread(screenshot_b64)}
        if action == "wol":
            send_magic_packet(a["mac"], a.get("broadcast") or "255.255.255.255")
            return {}
        if action == "job_start":
            await self.jobs.start(a["job_id"], a["project"], a["prompt"])
            return {}
        if action == "job_followup":
            await self.jobs.start(a["job_id"], a["project"], a["prompt"], session_id=a.get("session_id"))
            return {}
        if action == "job_cancel":
            await self.jobs.cancel(a["job_id"])
            return {}
        raise ValueError(f"Unknown action {action}")

    async def power(self, kind: str):
        if kind == "cancel":
            if self.power_task:
                self.power_task.cancel()
                self.power_task = None
            await asyncio.to_thread(plat.cancel_power)
            return {}
        if plat.NATIVE_POWER_DELAY:  # Windows: shutdown /t 60 is cancellable on its own
            await asyncio.to_thread(plat.power, kind)
        else:
            async def later():
                await asyncio.sleep(60)
                await asyncio.to_thread(plat.power, kind)
            self.power_task = asyncio.create_task(later())
        return {"in_seconds": 60}

    async def handle_call(self, msg: dict):
        try:
            data = await self.act(msg["action"], msg.get("args") or {})
            await self.send({"type": "result", "id": msg["id"], "ok": True, "data": data})
        except Exception as e:  # noqa: BLE001
            log.exception("action %s failed", msg.get("action"))
            await self.send({"type": "result", "id": msg["id"], "ok": False, "error": str(e) or type(e).__name__})

    # loops ---------------------------------------------------------------------------
    async def status_loop(self):
        import psutil
        psutil.cpu_percent(None)
        while True:
            await asyncio.sleep(5)
            idle = await asyncio.to_thread(plat.idle_seconds)
            st = {"type": "status", "cpu": round(psutil.cpu_percent(None)), "ram": round(psutil.virtual_memory().percent),
                  "active": await asyncio.to_thread(plat.active_window), "idle_s": idle,
                  "uptime_s": int(time.time() - psutil.boot_time()), "jobs": self.jobs.running_ids()}
            try:
                await self.send(st)
            except Exception:  # noqa: BLE001
                pass
            await self.maybe_idle_sleep(idle)

    async def maybe_idle_sleep(self, idle):
        if not self.idle_sleep_min or idle is None or self.cfg.get("role") == "relay":
            return
        if idle < 60:
            self.slept_for_idle = False
            return
        if (not self.slept_for_idle and idle >= self.idle_sleep_min * 60 and not self.jobs.running_ids()
                and not self.dry):
            self.slept_for_idle = True
            log.info("idle for %ss with no jobs: sleeping", idle)
            await asyncio.to_thread(plat.sleep)

    async def connect_loop(self):
        from websockets.asyncio.client import connect
        url = re.sub(r"^http", "ws", self.cfg["hub"].rstrip("/")) + "/api/agent/ws"
        delay = 1
        while True:
            try:
                async with connect(url, additional_headers={"Authorization": f"Bearer {self.cfg['token']}"},
                                   ping_interval=20, ping_timeout=20, max_size=8 * 1024 * 1024) as ws:
                    self.ws = ws
                    delay = 1
                    mac, bcast = network_identity()
                    await self.apps.refresh()
                    await self.send({"type": "hello", "name": self.cfg["name"], "os": plat.OS_NAME, "version": VERSION,
                                     "role": self.cfg.get("role", "pc"), "mac": mac, "broadcast": bcast,
                                     "claude_code": bool(self.jobs.claude_path()), "apps": len(self.apps.apps),
                                     "jobs": self.jobs.running_ids()})
                    await self.flush()
                    log.info("connected to %s", url)
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "call":
                            asyncio.create_task(self.handle_call(msg))
                        elif msg.get("type") == "config":
                            self.idle_sleep_min = int(msg.get("idle_sleep_min") or 0)
            except Exception as e:  # noqa: BLE001
                if "4401" in str(e) or "HTTP 403" in str(e):
                    log.error("The hub rejected this PC's token. Pair it again from the dashboard.")
                    delay = 60
                else:
                    log.warning("connection lost (%s); retrying in %ss", e, delay)
            self.ws = None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def run(self):
        await asyncio.gather(self.connect_loop(), self.status_loop())


# --- helpers --------------------------------------------------------------------------------------------
def screenshot_b64() -> str:
    from PIL import Image
    img = plat.screenshot()
    img.thumbnail((1568, 1568), Image.LANCZOS)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=72, optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def send_magic_packet(mac: str, broadcast: str) -> None:
    raw = bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", mac))
    if len(raw) != 6:
        raise ValueError("bad MAC address")
    pkt = b"\xff" * 6 + raw * 16
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for addr in {broadcast, "255.255.255.255"}:
        for port in (9, 7):
            s.sendto(pkt, (addr, port))
    s.close()


def single_instance() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        sys.exit("The agent is already running.")
    return s


def main():
    ap = argparse.ArgumentParser(description="Assistant PC agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair")
    p.add_argument("--hub", required=True)
    p.add_argument("--code", required=True)
    p.add_argument("--role", default="pc", choices=["pc", "relay"])
    p.add_argument("--name", default=platform.node() or "PC")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    HOME_DIR.mkdir(parents=True, exist_ok=True)
    handlers = [logging.FileHandler(HOME_DIR / "agent.log", encoding="utf-8")]
    if sys.stderr and sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)

    if args.cmd == "pair":
        pair(args.hub, args.code, args.role, args.name)
        return
    lock = single_instance()  # noqa: F841 (kept open for the life of the process)
    asyncio.run(Agent(load_config(), args.dry_run).run())


if __name__ == "__main__":
    main()
