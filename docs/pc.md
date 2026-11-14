# Your PC: control, coding jobs, wake

The PC agent is a small background program on your PC. It keeps one connection open to the hub
over Tailscale, so nothing on your PC is reachable from outside and no ports are opened.
Windows is supported first; CachyOS with Hyprland works too.

## Pairing a PC

1. In the dashboard, click **Pair a PC** (PC panel, or Settings > Paired devices).
2. Copy the command for your system and paste it on the PC:
   - **Windows:** into PowerShell. No admin rights needed.
   - **CachyOS / Hyprland:** into a terminal, as your normal user.
3. The PC shows as online within seconds. From now on it starts by itself at login.

The command installs Python if it's missing, installs the agent, pairs it with a one-time code that
expires after 10 minutes, and offers to install Git and Claude Code for coding jobs. Run the same
command again to update the agent.

## What you can say

These run instantly, matched by pattern with no AI involved (a few milliseconds, plus the hop to your PC):

| Say or type | What happens |
|---|---|
| open Godot · start Steam · åpne Discord | Finds the installed app and opens it |
| open youtube · go to github.com | Opens the site |
| search YouTube for lo-fi · search for cheap flights · play X on YouTube | Searches that site in your browser |
| pause · play · next song · previous | Media keys |
| volume 30 · turn it up · quieter · mute | Volume |
| lock my PC · go to sleep | Lock, sleep |
| shut down · restart · cancel shutdown | Always asks first; 60-second countdown |
| close Discord | Asks first; the app can still ask you to save |
| screenshot | Shows your screen in the chat |
| wake my PC | Wake-on-LAN (see below) |
| job status · cancel the job | Coding jobs |
| yes / no | Answers the question that's waiting |

Anything else, or anything unclear ("open the game engine", "put on something calm and turn it
down a bit", "what's on my screen?"), goes to Claude. Claude has the same PC actions as tools and
can look at the list of installed apps or take a screenshot to work it out.

## Ask-first rules

| Runs immediately | Asks you first (Yes/No bar above the chat box, or say "yes") |
|---|---|
| Opening apps and sites, searches, media, volume, lock, screenshots | Closing a program. The question tells you if a window looks unsaved (`*`, "Unsaved", `●` in its title) |
| Sleep, when nothing is running | Sleep while a coding job is running or work looks unsaved |
| | Shut down and restart, always. The question lists running jobs and unsaved windows |
| | Cancelling a coding job |

Questions expire after 2 minutes, so a late "yes" never runs anything.

## Coding jobs

Ask for something to be built ("make me a Godot prototype of a space miner"). Claude writes a
brief and starts a job:

- The agent creates `Projects/<name>` in your home folder with its own git repository, then runs
  Claude Code headless with your Claude Max login.
- Progress streams to the **Jobs** panel. Your phone gets "tests passing" and "done" with a summary.
- Click a job to see its log, cancel it, or send follow-up instructions. A follow-up continues the
  same Claude Code session.
- If the PC is off, the hub wakes it and the job starts when it comes online.

Built-in limits: Claude Code may edit files and run commands in the project folder. It can't push,
delete files, publish, use sudo or shut down; those are blocked by Claude Code's own permission
rules. It commits locally as it goes.

## Wake-on-LAN

Your hub runs on a server outside your home, and that server can't reach your home network
directly. So a device at home passes the wake signal on. Any always-on device works, such as a
Raspberry Pi. The pairing dialog has a "relay" command for it.

One-time setup on the PC (a network cable is needed; wake over Wi-Fi rarely works):

**Windows**
1. In the BIOS/UEFI, turn on *Wake on LAN* (sometimes called *Power On By PCI-E*). If there's an
   *ErP* setting, turn it off.
2. Device Manager > Network adapters > your Ethernet adapter > Properties:
   - *Power Management*: tick **Allow this device to wake the computer** and **Only allow a magic packet**.
   - *Advanced*: set **Wake on Magic Packet** to Enabled.
3. Control Panel > Power Options > *Choose what the power buttons do* > untick **Turn on fast startup**.

**CachyOS**
1. The same BIOS setting.
2. `nmcli connection modify "<your wired connection>" 802-3-ethernet.wake-on-lan magic`

Optional: Settings > *PC auto-sleep after idle* puts a Windows PC to sleep after N idle minutes,
but only when no coding job is running.

## Files on the PC

| | Windows | CachyOS |
|---|---|---|
| Agent and settings | `%LOCALAPPDATA%\ClaudeAgent` | `~/.local/share/claude-agent`, `~/.config/claude-agent` |
| Log | `%LOCALAPPDATA%\ClaudeAgent\agent.log` | `~/.config/claude-agent/agent.log` |
| Starts at login via | Startup folder shortcut "Claude Agent" | `exec-once` line in `hyprland.conf` |
| Projects | `%USERPROFILE%\Projects` | `~/Projects` |

To remove a PC: Settings > Paired devices > Remove. Then delete the folder and the startup entry above.
