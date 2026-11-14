"""Windows: apps, windows, media, volume, power and screenshots via the Win32 API."""
import ctypes
import json
import os
import re
import subprocess
import time
import webbrowser
from ctypes import wintypes
from pathlib import Path

OS_NAME = "Windows"
NATIVE_POWER_DELAY = True
NO_WINDOW = 0x08000000

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetForegroundWindow.restype = wintypes.HWND
kernel32.GetTickCount.restype = wintypes.DWORD

UNSAVED = re.compile(r"(^\*|\s\*\s|\(\*\)|\*$|\u25cf|\u2022\s|unsaved|modified|endret|ikke lagret)", re.I)


# --- apps -----------------------------------------------------------------------------
def _start_menu_dirs() -> list[Path]:
    env = os.environ
    return [Path(env.get("PROGRAMDATA", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(env.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
            Path(env.get("USERPROFILE", "")) / "Desktop",
            Path(env.get("PUBLIC", r"C:\Users\Public")) / "Desktop"]


def scan_apps() -> dict[str, tuple[str, str]]:
    """name -> ("file", path) or ("appid", AUMID)."""
    apps: dict[str, tuple[str, str]] = {}
    # Store / UWP apps and everything else Windows lists in Start
    try:
        raw = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                              "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                             capture_output=True, timeout=30, creationflags=NO_WINDOW).stdout or b""
        out = raw.decode("utf-8", "replace").lstrip("﻿")
        rows = json.loads(out) if out.strip() else []
        for r in rows if isinstance(rows, list) else [rows]:
            if r.get("Name") and r.get("AppID"):
                apps[r["Name"]] = ("appid", r["AppID"])
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    # Shortcuts (these win over Start entries with the same name; Steam games are .url files)
    for d in _start_menu_dirs():
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix.lower() in (".lnk", ".url", ".appref-ms"):
                apps[p.stem] = ("file", str(p))
    return apps


def launch(target) -> None:
    kind, value = target
    if kind == "file":
        os.startfile(value)  # noqa: S606 - a shortcut from the user's own Start menu
    else:
        subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{value}"], creationflags=NO_WINDOW)


def open_url(url: str) -> None:
    webbrowser.open(url)


# --- keys, media, volume ------------------------------------------------------------------------
VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
      "vol_mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF}


def _tap(vk: int) -> None:
    user32.keybd_event(vk, 0, 1, 0)       # KEYEVENTF_EXTENDEDKEY
    user32.keybd_event(vk, 0, 1 | 2, 0)   # + KEYEVENTF_KEYUP


def media(cmd: str) -> None:
    if cmd not in ("play_pause", "next", "previous", "stop"):
        raise ValueError("unknown media command")
    _tap(VK[cmd])


def _endpoint():
    import comtypes
    comtypes.CoInitialize()
    from pycaw.pycaw import AudioUtilities
    dev = AudioUtilities.GetSpeakers()
    ep = getattr(dev, "EndpointVolume", None)
    if ep is None:  # older pycaw
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import IAudioEndpointVolume
        ep = cast(dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None), POINTER(IAudioEndpointVolume))
    return ep


def volume(level=None, delta=None, mute=None) -> int | None:
    try:
        ep = _endpoint()
    except Exception:  # noqa: BLE001 - no pycaw: fall back to volume keys (2% per press)
        if mute is not None:
            _tap(VK["vol_mute"])
        elif delta:
            for _ in range(abs(delta) // 2):
                _tap(VK["vol_up" if delta > 0 else "vol_down"])
        return None
    if mute is not None:
        ep.SetMute(1 if mute else 0, None)
    if level is not None or delta:
        cur = round(ep.GetMasterVolumeLevelScalar() * 100)
        new = max(0, min(100, level if level is not None else cur + delta))
        ep.SetMasterVolumeLevelScalar(new / 100, None)
        if new > 0:
            ep.SetMute(0, None)
    return round(ep.GetMasterVolumeLevelScalar() * 100)


# --- power ------------------------------------------------------------------------------------------
def lock() -> None:
    user32.LockWorkStation()


def sleep() -> None:
    ctypes.WinDLL("powrprof").SetSuspendState(False, True, False)


def power(kind: str) -> None:
    flag = {"shutdown": "/s", "restart": "/r"}[kind]
    subprocess.run(["shutdown", flag, "/t", "60", "/c", "Requested from your assistant. Say 'cancel shutdown' to stop."],
                   creationflags=NO_WINDOW, check=False)


def cancel_power() -> None:
    subprocess.run(["shutdown", "/a"], creationflags=NO_WINDOW, check=False)


# --- windows ------------------------------------------------------------------------------------------
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def all_windows() -> list[dict]:
    import psutil
    out = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER: top-level only
            title = _title(hwnd)
            if title and title not in ("Program Manager", "Windows Input Experience"):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                try:
                    proc = psutil.Process(pid.value).name()
                except psutil.Error:
                    proc = ""
                out.append({"hwnd": int(hwnd), "title": title, "pid": pid.value, "process": proc,
                            "app": re.sub(r"\.exe$", "", proc, flags=re.I), "unsaved": bool(UNSAVED.search(title))})
        return True
    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def _n(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def find_windows(name: str) -> list[dict]:
    q = _n(name)
    if not q:
        return []
    wins = all_windows()
    by_proc = [w for w in wins if q in _n(w["app"])]
    if by_proc:
        return by_proc
    # e.g. "close the godot project" -> title ends with "- Godot Engine"
    return [w for w in wins if q in _n(w["title"].split(" - ")[-1])]


def close_windows(wins: list[dict]) -> bool:
    import psutil
    for w in wins:
        user32.PostMessageW(w["hwnd"], 0x0010, 0, 0)  # WM_CLOSE: polite, the app may ask to save
    time.sleep(2.5)
    pids = {w["pid"] for w in wins}
    return not any(psutil.pid_exists(p) and any(x["pid"] == p for x in all_windows()) for p in pids)


def active_window() -> str:
    hwnd = user32.GetForegroundWindow()
    return _title(hwnd)[:120] if hwnd else ""


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def idle_seconds() -> int | None:
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    return int(((kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF) / 1000)


# --- screen -------------------------------------------------------------------------------------------------
def screenshot():
    import mss
    from PIL import Image
    with mss.mss() as sct:
        shot = sct.grab(sct.monitors[0])  # all monitors
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
