"""PC actions shared by instant commands and Claude's tools. Every risky action goes
through confirm.ask() instead of running straight away."""
import base64
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote_plus

from . import agents, confirm, jobs
from .config import DATA_DIR

SCREENS = DATA_DIR / "screens"
SCREENS.mkdir(exist_ok=True)

SEARCH = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "github": "https://github.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "google maps": "https://www.google.com/maps/search/{q}",
    "steam": "https://store.steampowered.com/search/?term={q}",
    "spotify": "https://open.spotify.com/search/{q}",
    "twitch": "https://www.twitch.tv/search?term={q}",
    "stack overflow": "https://stackoverflow.com/search?q={q}",
    "stackoverflow": "https://stackoverflow.com/search?q={q}",
    "itch": "https://itch.io/search?q={q}",
    "itch.io": "https://itch.io/search?q={q}",
    "godot docs": "https://docs.godotengine.org/en/stable/search.html?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "images": "https://www.google.com/search?tbm=isch&q={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
}

SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "github": "https://github.com", "reddit": "https://www.reddit.com", "netflix": "https://www.netflix.com",
    "twitch": "https://www.twitch.tv", "chatgpt": "https://chatgpt.com", "claude": "https://claude.ai",
    "google maps": "https://www.google.com/maps", "maps": "https://www.google.com/maps",
    "calendar": "https://calendar.google.com", "google drive": "https://drive.google.com",
    "drive": "https://drive.google.com", "wikipedia": "https://www.wikipedia.org", "itch": "https://itch.io",
    "steam store": "https://store.steampowered.com", "x": "https://x.com", "twitter": "https://x.com",
    "nrk": "https://www.nrk.no", "yr": "https://www.yr.no", "finn": "https://www.finn.no", "vg": "https://www.vg.no",
}

DOMAIN = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$", re.I)


def as_url(text: str) -> str | None:
    t = text.strip().lower()
    if t in SITES:
        return SITES[t]
    if DOMAIN.match(text.strip()):
        return text.strip() if text.strip().startswith("http") else "https://" + text.strip()
    return None


# --- simple actions ------------------------------------------------------------------
async def open_app(name: str) -> dict:
    """Returns {"opened": label} or {"matches": [...]} (ambiguous) or {"matches": []} (not found)."""
    return await agents.call("open_app", {"name": name})


async def open_url(url: str) -> str:
    await agents.call("open_url", {"url": url})
    return f"Opened {re.sub(r'^https?://(www\\.)?', '', url).split('?')[0].rstrip('/')}."


async def search(site: str, query: str) -> str:
    site = (site or "google").strip().lower()
    tpl = SEARCH.get(site) or SEARCH.get(site.rstrip("s"))
    if not tpl:
        return ""
    await agents.call("open_url", {"url": tpl.format(q=quote_plus(query))})
    return f"Searching {site.title() if site != 'youtube' else 'YouTube'} for “{query}”."


async def media(cmd: str) -> str:
    await agents.call("media", {"cmd": cmd})
    return {"play_pause": "Play/pause.", "next": "Next track.", "previous": "Previous track.",
            "stop": "Stopped."}.get(cmd, "Done.")


async def volume(level: int | None = None, delta: int | None = None, mute: bool | None = None) -> str:
    data = await agents.call("volume", {"level": level, "delta": delta, "mute": mute})
    if mute is True:
        return "Muted."
    if mute is False:
        return "Unmuted."
    lvl = data.get("level")
    if lvl is None:
        lvl = level
    if lvl is None:
        return "Volume up." if (delta or 0) > 0 else "Volume down."
    return f"Volume {lvl}%."


async def lock() -> str:
    await agents.call("lock")
    return "Locked."


async def screenshot() -> tuple[str, str]:
    """Returns (markdown for the chat, base64 jpeg for Claude)."""
    data = await agents.call("screenshot", timeout=30)
    sid = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}.jpg"
    (SCREENS / sid).write_bytes(base64.b64decode(data["jpeg_b64"]))
    _prune_screens()
    return f"![Screenshot](/api/screens/{sid})", data["jpeg_b64"]


def _prune_screens(keep: int = 50) -> None:
    files = sorted(SCREENS.glob("*.jpg"))
    for f in files[:-keep]:
        f.unlink(missing_ok=True)


def screen_path(sid: str) -> Path | None:
    if not re.fullmatch(r"[\w-]+\.jpg", sid):
        return None
    p = SCREENS / sid
    return p if p.exists() else None


# --- risky actions: always go through a confirmation ------------------------------------
async def _risk_notes() -> list[str]:
    notes = []
    n = jobs.running_count()
    if n:
        notes.append(f"{n} coding job{'s are' if n > 1 else ' is'} still running")
    try:
        unsaved = (await agents.call("unsaved_windows", timeout=8)).get("titles", [])
        if unsaved:
            notes.append("unsaved work in: " + ", ".join(unsaved[:4]))
    except (agents.AgentOffline, agents.AgentError):
        pass
    return notes


async def ask_close(name: str) -> str:
    info = await agents.call("find_windows", {"name": name})
    wins = info.get("windows", [])
    if not wins:
        return f"I can't find an open window for “{name}”."
    label = info.get("label") or name
    unsaved = [w["title"] for w in wins if w.get("unsaved")]
    q = f"Close {label}?"
    if unsaved:
        q += f" It looks like there's unsaved work ({'; '.join(unsaved[:3])}). It will ask you to save if it can."

    async def act():
        r = await agents.call("close_app", {"name": name})
        return f"Closed {r.get('label', label)}." if r.get("closed") else f"{label} didn't close. It may be waiting for you to save."
    confirm.ask(q, act)
    return q


async def sleep_pc() -> str:
    notes = await _risk_notes()

    async def act():
        await agents.call("sleep")
        return "Putting the PC to sleep."
    if notes:
        q = f"Put the PC to sleep? Note: {'; '.join(notes)}."
        confirm.ask(q, act)
        return q
    return await act()


async def power(kind: str) -> str:
    """kind: shutdown | restart. Always asks."""
    notes = await _risk_notes()
    verb = "Shut down" if kind == "shutdown" else "Restart"
    q = f"{verb} the PC?" + (f" Note: {'; '.join(notes)}." if notes else "")

    async def act():
        await agents.call("power", {"kind": kind})
        return f"{'Shutting down' if kind == 'shutdown' else 'Restarting'} in 60 seconds. Say “cancel shutdown” to stop it."
    confirm.ask(q, act)
    return q


async def cancel_shutdown() -> str:
    await agents.call("power", {"kind": "cancel"})
    return "Shutdown cancelled."
