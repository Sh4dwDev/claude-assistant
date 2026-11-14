"""Notification channels. Free ones first: dashboard, phone push (PWA), Discord.
Paid ones (SMS / calls) plug in here in milestone 3, behind the spending cap."""
import json
import logging
import threading
import urllib.request

from . import config, db, events

log = logging.getLogger("notify")


# --- Web Push (PWA notifications) -----------------------------------------
def vapid_keys() -> tuple[str, str]:
    """Generated once, stored in data/.env. Returns (private_pem_b64-ish, public_key_b64url)."""
    priv = config.get_secret("VAPID_PRIVATE_KEY")
    pub = config.get_secret("VAPID_PUBLIC_KEY")
    if priv and pub:
        return priv, pub
    import base64
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv_raw = key.private_numbers().private_value.to_bytes(32, "big")
    pub_raw = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
    priv, pub = b64(priv_raw), b64(pub_raw)
    config.set_secret("VAPID_PRIVATE_KEY", priv)
    config.set_secret("VAPID_PUBLIC_KEY", pub)
    return priv, pub


def _send_push(payload: dict) -> int:
    from pywebpush import WebPushException, webpush

    priv, _ = vapid_keys()
    sent = 0
    for row in db.q("SELECT id, endpoint, data FROM push_subscriptions"):
        try:
            webpush(subscription_info=json.loads(row["data"]), data=json.dumps(payload),
                    vapid_private_key=priv, vapid_claims={"sub": "mailto:assistant@localhost"}, ttl=3600)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # phone unsubscribed or reinstalled the app
                db.run("DELETE FROM push_subscriptions WHERE id=?", (row["id"],))
            else:
                log.warning("push failed: %s", e)
        except Exception as e:  # noqa: BLE001
            log.warning("push failed: %s", e)
    return sent


# --- Discord (free) -----------------------------------------------------------
def _send_discord(title: str, body: str) -> None:
    url = config.get_secret("DISCORD_WEBHOOK_URL")
    if not url:
        return
    data = json.dumps({"content": f"**{title}**\n{body}"[:1900]}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json",
                                                         "User-Agent": "assistant-hub"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001
        log.warning("discord failed: %s", e)


def notify(title: str, body: str = "", url: str = "/", level: str = "info", push: bool = True) -> int:
    """Store, show on every open dashboard, and send to phone + Discord. Safe from any thread."""
    nid = db.run("INSERT INTO notifications(title, body, url, level, created) VALUES(?,?,?,?,?)",
                 (title, body, url, level, db.now()))
    item = {"id": nid, "title": title, "body": body, "url": url, "level": level, "created": db.now()}
    events.publish("notification", item)
    if push:
        threading.Thread(target=lambda: (_send_push({"title": title, "body": body, "url": url}),
                                         _send_discord(title, body)), daemon=True).start()
    return nid
