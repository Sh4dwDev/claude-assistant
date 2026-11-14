"""Instant PC commands (English + a little Norwegian). Matched by pattern, no AI round trip.
Anything a rule isn't sure about raises Unclear, and Claude handles it with the PC tools."""
import re

from . import agents, confirm, jobs, pc
from .commands import Unclear, rule

THE_PC = r"(?:\s+(?:the|my|min|maskinen|pc-?en))?(?:\s+(?:pc|computer|maskin|datamaskin))?"


def _offline_guard(fn):
    async def wrapped(m):
        try:
            return await fn(m)
        except agents.AgentOffline:
            p = agents.primary()
            if not p:
                return "No PC is paired yet. Use Pair a PC in the PC panel."
            return f"{p['name']} is offline. Say “wake my PC” to turn it on."
        except agents.AgentError as e:
            return f"The PC couldn't do that: {e}"
    wrapped.__name__ = fn.__name__
    return wrapped


def r(regex: str, desc: str):
    def deco(fn):
        return rule(regex, desc)(_offline_guard(fn))
    return deco


# --- confirmations ---------------------------------------------------------------------
@r(r"(?:yes|yeah|yep|yup|sure|do it|go ahead|confirm|ok do it|ja|jepp|gjør det)", "confirm the pending question")
async def _yes(m):
    p = confirm.latest()
    if not p:
        raise Unclear
    return await confirm.resolve(p.id, True)


@r(r"(?:no|nope|don'?t|cancel|stop|never mind|nevermind|nei|avbryt)", "decline the pending question")
async def _no(m):
    p = confirm.latest()
    if not p:
        raise Unclear
    return await confirm.resolve(p.id, False)


# --- web ------------------------------------------------------------------------------------
SITE_NAMES = "|".join(sorted((re.escape(s) for s in pc.SEARCH), key=len, reverse=True))


@r(rf"(?:search|look up|find|søk(?: etter)?)\s+(?:on\s+|in\s+|på\s+)?(?P<site>{SITE_NAMES})\s+(?:for\s+|etter\s+)?(?P<q>.+)",
   "search YouTube for lo-fi")
async def _search_site(m):
    return await pc.search(m["site"], m["q"])


@r(rf"(?:search(?: for)?|look up|google|søk(?: etter)?)\s+(?P<q>.+?)\s+(?:on|in|at|på)\s+(?P<site>{SITE_NAMES})",
   "search for lo-fi on YouTube")
async def _search_for_on(m):
    return await pc.search(m["site"], m["q"])


@r(r"play\s+(?P<q>.+?)\s+on\s+youtube", "play X on YouTube")
async def _play_yt(m):
    return await pc.search("youtube", m["q"])


@r(r"(?:search(?: for)?|google|søk(?: etter)?)\s+(?P<q>.+)", "search for X (Google)")
async def _search(m):
    if re.search(r"\b(my|mine|email|emails|mail|inbox|files?|folder|memory|memories|chat)\b", m["q"], re.I):
        raise Unclear
    return await pc.search("google", m["q"])


@r(r"(?:go to|visit|gå til)\s+(?!sleep\b|bed\b)(?P<site>\S+(?:\s\S+)?)", "go to github.com")
async def _goto(m):
    url = pc.as_url(m["site"])
    if not url:
        raise Unclear
    return await pc.open_url(url)


# --- media and volume -----------------------------------------------------------------------------
@r(r"(?:play|pause|resume|play music|pause music|pause the music|spill|pause musikken)", "play / pause")
async def _play_pause(m):
    return await pc.media("play_pause")


@r(r"(?:next|skip|next (?:song|track)|skip (?:this )?(?:song|track)|neste(?: sang)?)", "next track")
async def _next(m):
    return await pc.media("next")


@r(r"(?:previous|prev|last|previous (?:song|track)|go back a (?:song|track)|forrige(?: sang)?)", "previous track")
async def _prev(m):
    return await pc.media("previous")


@r(r"(?:set\s+)?(?:the\s+)?(?:volume|volum)\s+(?:to\s+|til\s+)?(?P<n>\d{1,3})\s*(?:%|percent|prosent)?",
   "volume 30")
async def _vol_set(m):
    return await pc.volume(level=max(0, min(100, int(m["n"]))))


@r(r"(?:volume up|turn it up|louder|turn up the volume|turn the volume up|høyere|skru opp(?: lyden)?)", "volume up")
async def _vol_up(m):
    return await pc.volume(delta=10)


@r(r"(?:volume down|turn it down|quieter|lower the volume|turn the volume down|lavere|skru ned(?: lyden)?)", "volume down")
async def _vol_down(m):
    return await pc.volume(delta=-10)


@r(r"(?:mute|mute (?:it|the sound|the pc|sound)|demp(?: lyden)?)", "mute")
async def _mute(m):
    return await pc.volume(mute=True)


@r(r"(?:unmute|unmute (?:it|the sound|the pc|sound))", "unmute")
async def _unmute(m):
    return await pc.volume(mute=False)


# --- power and screen ----------------------------------------------------------------------------------
@r(rf"(?:lock|lås){THE_PC}(?:\s+(?:screen|skjermen))?", "lock the PC")
async def _lock(m):
    return await pc.lock()


@r(rf"(?:go to sleep|sleep|put{THE_PC}\s+to sleep|suspend|hvilemodus|sett{THE_PC}\s+i hvilemodus)", "sleep")
async def _sleep(m):
    return await pc.sleep_pc()


@r(rf"(?:shut ?down|turn off|power off|skru av|slå av){THE_PC}", "shut down (always asks)")
async def _shutdown(m):
    return await pc.power("shutdown")


@r(rf"(?:restart|reboot|start på nytt){THE_PC}", "restart (always asks)")
async def _restart(m):
    return await pc.power("restart")


@r(r"(?:cancel|abort|stop)\s+(?:the\s+)?(?:shutdown|restart|reboot)", "cancel shutdown")
async def _cancel_shutdown(m):
    return await pc.cancel_shutdown()


@r(r"(?:wake(?: up)?|turn on|start|boot|vekk|skru på|start opp)(?:\s+(?:the|my|min))?\s+(?:pc|computer|maskin|maskinen|datamaskin|pc-en)", "wake the PC")
async def _wake(m):
    return await agents.wake()


@r(r"(?:take a\s+)?(?:screenshot|screen shot|skjermbilde|ta et skjermbilde)", "screenshot")
async def _screenshot(m):
    md, _ = await pc.screenshot()
    return md


# --- jobs ------------------------------------------------------------------------------------------------
@r(r"(?:job|jobs|job status|status of (?:the|my) jobs?|how(?:'s| is) the job(?: going)?)", "job status")
async def _job_status(m):
    rows = jobs.recent(3)
    if not rows:
        return "No jobs yet."
    return "\n".join("- " + jobs.describe(j) for j in rows)


@r(r"cancel (?:the |my )?(?:job|coding job)(?:\s+(?P<id>\d+))?", "cancel the job")
async def _job_cancel(m):
    if m["id"]:
        jid = int(m["id"])
    else:
        active = [j for j in jobs.recent(10) if j["status"] in jobs.ACTIVE]
        if len(active) != 1:
            raise Unclear
        jid = active[0]["id"]
    job = jobs.get(jid)

    async def act():
        return await jobs.cancel(jid)
    q = f"Cancel job {jid}: {job['title'] if job else ''}?"
    confirm.ask(q, act)
    return q


# --- apps ------------------------------------------------------------------------------------
@r(r"(?:open|start|launch|run|åpne|start opp)\s+(?:up\s+)?(?:the\s+)?(?P<name>.+?)(?:\s+(?:app|program|programmet))?",
   "open Godot")
async def _open(m):
    name = m["name"].strip()
    if name.lower() in ("it", "that", "the browser", "browser", "nettleseren"):
        if "browser" in name.lower() or "nettleser" in name.lower():
            return await pc.open_url("about:blank")
        raise Unclear
    res = await pc.open_app(name)
    if res.get("opened"):
        return f"Opened {res['opened']}."
    url = pc.as_url(name)  # not an installed app: maybe a website ("open youtube")
    if url:
        return await pc.open_url(url)
    raise Unclear  # ambiguous or unknown: let Claude look at the app list


@r(r"(?:close|quit|exit|kill|lukk|avslutt)\s+(?:the\s+)?(?P<name>.+?)(?:\s+(?:app|program|window))?",
   "close Godot (asks first)")
async def _close(m):
    name = m["name"].strip()
    if name.lower() in ("it", "that", "this", "the window", "window"):
        raise Unclear
    return await pc.ask_close(name)
