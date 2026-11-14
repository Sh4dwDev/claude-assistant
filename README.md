# Claude: your personal assistant

A private assistant that runs on your own small server. You reach it from your PC and your
phone, by text or voice. It's on 24/7, even when your PC is off.

**Status: milestone 1 of 5.** What works today:

- Chat in a clean dashboard on the PC, and as an app on your phone (no app store needed)
- Answers questions, searches the web and reads pages
- Remembers your projects and preferences; you can view and edit everything it remembers
- Free push notifications to your phone, plus Discord if you want
- Cost panel with monthly spending caps that the hub enforces

Coming next: the PC agent with PC control and coding jobs (milestone 2), calls and texts (3),
Gmail (4), and offline mode plus the "Hey Claude" wake word (5). Details for milestone 2 are in
[docs/milestone-2-plan.md](docs/milestone-2-plan.md).

---

## What it costs

| Item | Monthly | Notes |
|---|---|---|
| Server (e.g. Hetzner) | about €4–5 | Fixed price. Reusing a server you already have costs nothing extra if it has 2 GB+ RAM. |
| Claude API | about $5–10 | Roughly 20 messages a day on the default model. The default cap is **$20**; the assistant stops at the cap and tells you. |
| Tailscale | free | Private network so only your devices can reach the hub. |
| Phone notifications, Discord | free | |
| Calls and SMS (milestone 3) | max $5 | Hard cap enforced by the hub. |

Your Claude Max subscription is separate: it powers Claude Code on your PC (milestone 2) and
doesn't cover the API key used for chat. Change the chat model in Settings: Opus is the smartest,
Sonnet costs half, Haiku is much cheaper.

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
2. **Your Claude API key**, with a link and one sentence on where to get it.
3. **Discord** (optional).
4. **Notifications** for this device.

### On your phone

1. Make sure Tailscale is on.
2. Open the same address in Chrome (Android) or Safari (iPhone).
3. **Add to Home Screen** (Android: menu > Install app. iPhone: Share > Add to Home Screen).
4. Open it from the home screen, then Settings > Notifications > Enable.

---

## Everyday use

- **Talk:** tap the microphone (or press Ctrl+Space on the PC). Speech recognition works in
  Chrome and Edge; full natural voice arrives in milestone 3.
- **Memory:** say "remember that...", or edit memories directly in Settings > Memory.
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
| "API key was rejected" | Settings > Keys > Replace. Check your API credit at console.anthropic.com. |
| No phone notifications | iPhone: open the app from the Home Screen icon, not Safari. Then Settings > Notifications > Enable, and Send a test. |
| See what the hub is doing | `cd /opt/claude-assistant && docker compose logs -f hub` |

---

## For later tinkering

```
install-hub.sh        one-command server install
docker-compose.yml    the hub container (listens only on 127.0.0.1; Tailscale serves it)
hub/app/              Python (FastAPI) backend
  chat.py             Claude API: streaming, web search, tool loop, cost tracking
  commands.py         instant commands that skip the AI (PC control plugs in here)
  tools/              tools Claude can use; add a module to add a tool
  notify.py           notification channels (push, Discord; SMS in milestone 3)
  costs.py            prices, monthly totals, enforced caps
hub/static/           dashboard and phone app (plain HTML/CSS/JS, no build step)
hub/tests/            offline tests: python -m tests.test_chat_loop (from hub/)
tools/make_icons.py   regenerates the app icons
```
