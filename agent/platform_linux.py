"""Linux with Hyprland (CachyOS): hyprctl, playerctl, wpctl, grim, loginctl, systemctl.
Also works headless on a Raspberry Pi in relay mode (PC actions then just report errors)."""
import configparser
import io
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path

OS_NAME = "Linux (Hyprland)" if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") else "Linux"
NATIVE_POWER_DELAY = False  # the agent waits 60 s itself, so "cancel shutdown" works

UNSAVED = re.compile(r"(^\*|\s\*\s|\(\*\)|\*$|●|\[\+\]|unsaved|modified)", re.I)


def _run(args: list[str], timeout=10) -> str:
    if not shutil.which(args[0]):
        raise RuntimeError(f"'{args[0]}' isn't installed on this PC.")
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:200] or f"{args[0]} failed")
    return r.stdout


def _spawn(args: list[str]) -> None:
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


# --- apps ------------------------------------------------------------------------------
def _app_dirs() -> list[Path]:
    data = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
    home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    dirs = [home / "applications", home / "flatpak/exports/share/applications",
            Path("/var/lib/flatpak/exports/share/applications")] + [Path(d) / "applications" for d in data]
    return [d for d in dirs if d.exists()]


def scan_apps() -> dict[str, tuple[str, str]]:
    apps: dict[str, tuple[str, str]] = {}
    for d in reversed(_app_dirs()):  # user entries last, so they win
        for f in d.glob("*.desktop"):
            cp = configparser.ConfigParser(interpolation=None, strict=False)
            try:
                cp.read(f, encoding="utf-8")
                e = cp["Desktop Entry"]
            except (configparser.Error, KeyError, UnicodeDecodeError):
                continue
            if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
                continue
            if e.get("Type", "Application") != "Application" or not e.get("Name"):
                continue
            apps[e["Name"]] = (f.stem, e.get("Exec", ""))
    return apps


def launch(target) -> None:
    desktop_id, exec_line = target
    if shutil.which("gtk-launch"):
        _spawn(["gtk-launch", desktop_id])
        return
    cmd = [a for a in shlex.split(exec_line) if not re.fullmatch(r"%[a-zA-Z]", a)]
    if shutil.which("hyprctl"):
        _spawn(["hyprctl", "dispatch", "exec", "--", shlex.join(cmd)])
    else:
        _spawn(cmd)


def open_url(url: str) -> None:
    _spawn(["xdg-open", url])


# --- media and volume -----------------------------------------------------------------------
def media(cmd: str) -> None:
    arg = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}[cmd]
    _run(["playerctl", arg])


SINK = "@DEFAULT_AUDIO_SINK@"


def volume(level=None, delta=None, mute=None) -> int | None:
    if mute is not None:
        _run(["wpctl", "set-mute", SINK, "1" if mute else "0"])
    if level is not None:
        _run(["wpctl", "set-volume", SINK, f"{max(0, min(100, level))}%"])
        _run(["wpctl", "set-mute", SINK, "0"])
    elif delta:
        _run(["wpctl", "set-volume", "-l", "1.0", SINK, f"{abs(delta)}%{'+' if delta > 0 else '-'}"])
    m = re.search(r"([\d.]+)", _run(["wpctl", "get-volume", SINK]))
    return round(float(m.group(1)) * 100) if m else None


# --- power -----------------------------------------------------------------------------------------
def lock() -> None:
    try:
        _run(["loginctl", "lock-session"])
    except RuntimeError:
        _spawn(["hyprlock"])


def sleep() -> None:
    _run(["systemctl", "suspend"])


def power(kind: str) -> None:
    _run(["systemctl", {"shutdown": "poweroff", "restart": "reboot"}[kind]])


def cancel_power() -> None:
    pass  # the agent cancels its own 60-second timer


# --- windows ----------------------------------------------------------------------------------------
def all_windows() -> list[dict]:
    if not shutil.which("hyprctl"):
        return []
    try:
        clients = json.loads(_run(["hyprctl", "clients", "-j"]))
    except (RuntimeError, ValueError):
        return []
    return [{"address": c["address"], "title": c.get("title", ""), "pid": c.get("pid"),
             "app": c.get("class") or c.get("initialClass", ""), "unsaved": bool(UNSAVED.search(c.get("title", "")))}
            for c in clients if c.get("mapped", True) and c.get("title")]


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find_windows(name: str) -> list[dict]:
    q = _n(name)
    wins = all_windows()
    by_class = [w for w in wins if q and q in _n(w["app"])]
    return by_class or [w for w in wins if q and q in _n(w["title"].split(" - ")[-1])]


def close_windows(wins: list[dict]) -> bool:
    import time
    for w in wins:
        _run(["hyprctl", "dispatch", "closewindow", f"address:{w['address']}"])
    time.sleep(2.5)
    left = {w["address"] for w in all_windows()}
    return not any(w["address"] in left for w in wins)


def active_window() -> str:
    try:
        return (json.loads(_run(["hyprctl", "activewindow", "-j"])) or {}).get("title", "")[:120]
    except (RuntimeError, ValueError):
        return ""


def idle_seconds() -> int | None:
    return None  # Hyprland has no simple idle query; auto-sleep is left to hypridle


# --- screen --------------------------------------------------------------------------------------------
def screenshot():
    from PIL import Image
    if not shutil.which("grim"):
        raise RuntimeError("Screenshots need 'grim' (sudo pacman -S grim).")
    png = subprocess.run(["grim", "-t", "png", "-"], capture_output=True, timeout=15, check=True).stdout
    return Image.open(io.BytesIO(png))
