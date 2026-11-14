# Milestone 2: PC agent, PC control, coding jobs, Wake-on-LAN

Platform order: **Windows first**, then CachyOS with Hyprland.

## The agent

A small Python background program on the PC.

- **Install:** one command. On Windows it's a PowerShell one-liner that installs the agent,
  registers it to start at login (Task Scheduler, no window), and pairs it with the hub using a
  one-time code shown in the dashboard. On CachyOS it's a shell script that adds a
  `systemd --user` service started with the Hyprland session.
- **Connection:** the agent opens one WebSocket *to* the hub over Tailscale. There's no port
  forwarding and nothing listens on the PC. The connection stays open, so commands reach the PC
  in tens of milliseconds.
- **Status** to the dashboard: online/offline, CPU, RAM, active window, and running jobs.

## PC control

Control the PC by voice or text, from the PC or the phone.

### Instant path (no AI round trip)

```
"open Godot"  ->  hub: commands.py pattern match (~1 ms)  ->  WebSocket  ->  agent runs it  ->  "Opened Godot"
```

When you type or talk at the PC itself, the agent runs the same rules locally, so it doesn't wait on the hub.
Target: under 300 ms from Enter to action.

| You say | Windows | CachyOS / Hyprland |
|---|---|---|
| open / start / launch *X* | Fuzzy match against Start Menu shortcuts, App Paths and Steam games, then launch | Fuzzy match against `.desktop` files, then `hyprctl dispatch exec` |
| close / quit *X* | Polite close (the app can still ask to save). Asks first | `hyprctl dispatch closewindow`. Asks first |
| open *site* / go to *url* | Default browser | Default browser (`xdg-open`) |
| search *[site]* for *Y* (YouTube, Google, Wikipedia, GitHub, Reddit, Maps, Steam...) | Opens that site's search URL | Same |
| play / pause / next / previous | Media keys | `playerctl` |
| volume *N* / up / down / mute | Windows Core Audio | `wpctl` |
| lock | `LockWorkStation` | `loginctl lock-session` (hyprlock) |
| sleep | Suspend | `systemctl suspend` |
| shut down / restart | `shutdown /s /t 60` (cancellable). Always asks first | `systemctl poweroff`. Always asks first |
| screenshot / what's on my screen | Captured and shown in chat; Claude can look at it when asked | `grim` |

### AI fallback

If no rule matches but the message sounds like a PC request ("put on some lo-fi and turn it down
a bit"), Claude gets the same actions as tools (`open_app`, `open_url`, `media`, `volume`,
`screenshot`...) and decides. Unclear app names ("open the game engine") also go to Claude, with
the list of installed apps.

### Asking first: the risk policy

| Runs immediately | Asks you first (Yes/No card in chat, or say "yes") |
|---|---|
| open apps and sites, search, media, volume, lock, screenshot | closing a program, especially if its window title shows unsaved work (`*`, "Unsaved") |
| | sleep or shut down while a job or a download is running, or while unsaved windows are open |
| | shut down and restart, always |
| | deleting files, force-killing processes, anything Claude proposes that isn't on the left |

Confirmations expire after 2 minutes. Nothing risky ever runs from a stale "yes".

## Coding jobs (Claude Code)

- "Make me a Godot prototype of X": the hub creates a job and wakes the PC if needed. The agent
  creates `~/Projects/<name>`, runs `git init`, and runs Claude Code headless (your Max plan) with
  the request.
- Progress streams to the Jobs panel and your phone ("started", "tests passing", "done" plus a summary).
- Commands: status, cancel, and follow-up instructions to a running job.
- Asks before pushing to any public repo.

## Wake-on-LAN

- Your PC's network card gets WoL turned on (the installer checks and explains the BIOS setting if needed).
- A VPS can't send a WoL packet into your home network, so a device at home relays it. Until the
  Pi exists, any always-on device on your Tailscale network can do it (a Pi, or an Android phone
  with Tailscale, through a small relay app). The installer sets this up.
- Optional: sleep or shut down the PC after N idle minutes once jobs finish (same risk rules).

## What you'll be asked for

Only one thing: run the install command on the PC and type the pairing code from the dashboard.
