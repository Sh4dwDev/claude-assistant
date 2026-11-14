# Claude: your personal assistant

A private assistant that runs on your own small server. You reach it from your PC and your
phone, by text or voice. It's on 24/7, even when your PC is off.

**Status: milestone 2 of 5.** What works today:

- Chat in a clean dashboard on the PC, and as an app on your phone (no app store needed)
- Answers questions, searches the web and reads pages
- Remembers your projects and preferences; you can view and edit everything it remembers
- **Controls your PC** from the PC or your phone: open programs and sites, search, media, volume,
  lock, sleep, shut down, screenshots. Simple commands run instantly without the AI; risky ones ask first
- **Coding jobs:** "make me a Godot prototype of X" runs Claude Code on your PC and reports progress
- **Wakes your PC** when it's off (with a small helper device at home)
- Free push notifications to your phone, plus Discord if you want
- Cost panel with monthly spending caps that the hub enforces

Coming next: calls and texts (milestone 3), Gmail (4), and offline mode plus the "Hey Claude"
wake word (5).

---

## What it costs

| Item | Monthly | Notes |
|---|---|---|
| Server (e.g. Hetzner) | about €4–5 | Fixed price. Reusing a server you already have costs nothing extra if it has 2 GB+ RAM. |
| Claude | included | Chat and coding jobs use your own Claude plan (Pro or Max). Nothing extra to pay. |
| Claude API key (optional backup) | $0 unless used | Only answers when you hit your plan's usage limit. Pay per use, capped at **$20** by default. |
| Tailscale | free | Private network so only your devices can reach the hub. |
| Phone notifications, Discord | free | |
| Calls and SMS (milestone 3) | max $5 | Hard cap enforced by the hub. |

How it uses your plan: the hub runs the official Claude Code program, signed in with a token
you create once on your PC. Anthropic's terms allow that when it's your own subscription for
your own use. Don't share the assistant with other people on your plan; for that, use an API key
(Settings > Chat uses).

---

## Install (about 15 minutes, once)

You need two things first:

1. **A server.** A Hetzner Cloud CX22 (Ubuntu 24.04) or any Debian/Ubuntu machine with 2 GB RAM.
   A Raspberry Pi works later too.
2. **A free Tailscale account**: https://login.tailscale.com/start. Install the Tailscale app on
   your phone and PC and sign in on both.

Then log in to the server (`ssh root@your-server-ip`) and paste this one command:

```bash
curl -fsSL https://raw.githubusercontent.com/Sh4dwDev/claude-assistant/main/install-hub.sh | sudo bash
```

The script downloads the code to `/opt/claude-assistant` and installs Docker and Tailscale. It
then shows a Tailscale login link (open it and sign in), starts the hub, and prints your private
address, something like `https://assistant.your-tailnet.ts.net`.

> If it says HTTPS needs enabling: open https://login.tailscale.com/admin/dns, turn on
> MagicDNS and click **Enable HTTPS**, then paste the command again.

### First run

Open the address on your PC. A short setup asks for:

1. **Your name, where you live, and a password** (the second lock after Tailscale).
2. **Your Claude subscription token.** On your PC, open a terminal and run `claude setup-token`
   (if Claude Code isn't installed yet, first run `irm https://claude.ai/install.ps1 | iex` in
   PowerShell). Approve in the browser, then paste the token it prints. It lasts a year.
3. **An API key as backup** (optional; skip it if you like).
4. **Discord** (optional).
5. **Notifications** for this device.

### On your phone

1. Make sure Tailscale is on.
2. Open the same address in Chrome (Android) or Safari (iPhone).
3. **Add to Home Screen** (Android: menu > Install app. iPhone: Share > Add to Home Screen).
4. Open it from the home screen, then Settings > Notifications > Enable.

### On your PC

Click **Pair a PC** in the dashboard and paste the one command it shows (PowerShell on Windows,
a terminal on CachyOS). Everything about PC control, coding jobs and waking the PC is in
[docs/pc.md](docs/pc.md).

---

## Everyday use

- **Talk:** tap the microphone (or press Ctrl+Space on the PC). Speech recognition works in
  Chrome and Edge; full natural voice arrives in milestone 3.
- **Memory:** say "remember that...", or edit memories directly in Settings > Memory.
- **PC:** "open Godot", "search YouTube for lo-fi", "volume 30", "lock my PC", "screenshot".
  Shutting down or closing programs shows a Yes/No bar first. Full list in [docs/pc.md](docs/pc.md).
- **Coding:** "make me a Godot prototype of a space miner". Watch it in the Jobs panel.
- **New chat:** top right. Older chats are kept on the server.
- **Read replies aloud:** Settings > General.

## Updating

Paste the same install command again. It pulls the latest code and restarts the hub; your data stays.

## Moving to a Raspberry Pi or another machine

Everything personal is in `/opt/claude-assistant/data/`: the database, memories and secrets.

1. On the new machine, paste the install command once.
2. Run `cd /opt/claude-assistant && docker compose down`, copy your old `data/` folder over the
   new one, and paste the install command again.

## Backups

Copy `/opt/claude-assistant/data/` somewhere safe now and then. It holds your secrets, so keep
the copy private.

## Your data stays private

The code is public; your data is not. Your name, memories, chats and keys live only in
`data/` on your server. Secrets are in `data/.env`, which the setup page writes and git never
commits.

## If something's wrong

| Problem | Fix |
|---|---|
| The page doesn't load | Tailscale must be on (phone and PC). On the server: `cd /opt/claude-assistant && docker compose ps` should show the hub as running. |
| "Couldn't sign in with your subscription token" | Run `claude setup-token` on your PC again, then Settings > Keys > Replace. Tokens last a year. |
| "You've hit your Claude plan's usage limit" | It resets by itself (every few hours, plus a weekly limit). Add an API key backup in Settings to keep chatting meanwhile. |
| "API key was rejected" | Settings > Keys > Replace. Check your API credit at console.anthropic.com. |
| No phone notifications | iPhone: open the app from the Home Screen icon, not Safari. Then Settings > Notifications > Enable, and Send a test. |
| See what the hub is doing | `cd /opt/claude-assistant && docker compose logs -f hub` |

---

## For later tinkering

```
install-hub.sh        one-command server install
docker-compose.yml    the hub container (listens only on 127.0.0.1; Tailscale serves it)
hub/app/              Python (FastAPI) backend
  chat.py             picks the engine; API path: streaming, web search, tool loop, costs
  cc_chat.py          subscription path: runs Claude Code headless (claude -p) per chat
  mcp_http.py         the hub's tools served over MCP to that Claude Code
  commands.py         instant-command engine (patterns that skip the AI)
  pc_commands.py      the instant PC commands
  pc.py, confirm.py   PC actions and the ask-first confirmations
  agents.py, jobs.py  PC connections, pairing, Wake-on-LAN, coding jobs
  tools/              tools Claude can use; add a module to add a tool
  notify.py           notification channels (push, Discord; SMS in milestone 3)
  costs.py            prices, monthly totals, enforced caps
hub/static/           dashboard and phone app (plain HTML/CSS/JS, no build step)
hub/tests/            offline tests (from hub/): python -m tests.test_chat_loop, python -m tests.test_subscription
agent/                the PC agent (agent.py, platform_win.py, platform_linux.py, jobs.py)
  install-*.{ps1,sh}  one-command installers the dashboard hands out
  tests/              offline test: python tests/test_jobs.py (from agent/)
                      try the agent safely with:  python agent.py run --dry-run
tools/make_icons.py   regenerates the app icons
```
