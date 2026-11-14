"""Cost tracking and spending caps for everything the assistant pays for."""
import json
import time

from . import db

# USD per million tokens: (input, output, cache_read). Cache writes bill at 1.25x input.
CLAUDE_PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
WEB_SEARCH_USD = 10.0 / 1000  # per search

SERVICES = {
    "server": "Server",
    "claude_api": "Claude API",
    "phone": "Calls and SMS",
    "tts": "Voice (TTS)",
}
CAP_KEYS = {"claude_api": "cap_claude_api_usd", "phone": "cap_phone_usd", "tts": "cap_tts_usd"}


def month_key(ts: float | None = None) -> str:
    return time.strftime("%Y-%m", time.localtime(ts or time.time()))


def record(service: str, cost_usd: float, detail: str = "", meta: dict | None = None) -> None:
    db.run("INSERT INTO usage_log(ts, month, service, detail, cost_usd, meta) VALUES(?,?,?,?,?,?)",
           (time.time(), month_key(), service, detail, cost_usd, json.dumps(meta or {})))
    _check_cap(service)


def claude_cost(model: str, usage) -> float:
    price = CLAUDE_PRICES.get(model) or next(
        (p for m, p in CLAUDE_PRICES.items() if model.startswith(m)), CLAUDE_PRICES["claude-opus-5-5"])
    pin, pout, pcache = price
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cread = getattr(usage, "cache_read_input_tokens", 0) or 0
    cwrite = getattr(usage, "cache_creation_input_tokens", 0) or 0
    searches = 0
    stu = getattr(usage, "server_tool_use", None)
    if stu is not None:
        searches = getattr(stu, "web_search_requests", 0) or 0
    return (inp * pin + out * pout + cread * pcache + cwrite * pin * 1.25) / 1e6 + searches * WEB_SEARCH_USD


def spent(service: str, month: str | None = None) -> float:
    r = db.q1("SELECT COALESCE(SUM(cost_usd),0) AS s FROM usage_log WHERE month=? AND service=?",
              (month or month_key(), service))
    return float(r["s"]) if r else 0.0


def cap(service: str) -> float | None:
    key = CAP_KEYS.get(service)
    return float(db.get_setting(key)) if key else None


def over_cap(service: str) -> bool:
    c = cap(service)
    return c is not None and spent(service) >= c


def _check_cap(service: str) -> None:
    c = cap(service)
    if not c:
        return
    s = spent(service)
    for level, frac in (("100", 1.0), ("80", 0.8)):
        if s >= c * frac:
            flag = f"capwarn_{service}_{month_key()}_{level}"
            if not db.get_setting(flag):
                db.set_setting(flag, True)
                from .notify import notify
                name = SERVICES.get(service, service)
                if level == "100":
                    notify(f"{name} cap reached", f"${s:.2f} of ${c:.2f} this month. Paused until next month "
                           "or until you raise the cap in Settings.", level="warn")
                else:
                    notify(f"{name} at 80% of cap", f"${s:.2f} of ${c:.2f} this month.", level="warn")
            break


def summary() -> dict:
    month = month_key()
    eur_usd = float(db.get_setting("eur_usd"))
    server_usd = float(db.get_setting("server_cost_eur")) * eur_usd
    rows = []
    total = server_usd
    rows.append({"service": "server", "name": SERVICES["server"], "spent": server_usd, "cap": None, "fixed": True})
    sub = db.q1("SELECT COUNT(*) AS n FROM usage_log WHERE month=? AND service='claude_sub'", (month,))["n"]
    if sub:
        rows.append({"service": "claude_sub", "name": "Claude plan", "spent": 0.0, "cap": None, "fixed": False,
                     "note": f"{sub} replies, included"})
    for svc in ("claude_api", "phone", "tts"):
        s = spent(svc, month)
        total += s
        rows.append({"service": svc, "name": SERVICES[svc], "spent": s, "cap": cap(svc), "fixed": False})
    # simple daily series for the current month (Claude API)
    daily = db.q("SELECT strftime('%d', ts, 'unixepoch', 'localtime') AS day, SUM(cost_usd) AS s "
                 "FROM usage_log WHERE month=? GROUP BY day ORDER BY day", (month,))
    return {"month": month, "total": total, "rows": rows, "daily": daily}
