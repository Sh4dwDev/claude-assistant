"""The first-run wizard's steps. Each step: one key, one link, one plain sentence.
Later milestones add their steps here (Twilio, Gmail, ElevenLabs...)."""

STEPS = [
    {
        "key": "CLAUDE_CODE_OAUTH_TOKEN",
        "title": "Your Claude subscription",
        "why": "The assistant thinks with your own Claude plan (Pro or Max), so chatting costs nothing extra.",
        "how": "On your PC, open a terminal and run  claude setup-token  (install Claude Code first if needed: "
               "irm https://claude.ai/install.ps1 | iex). Approve in the browser, then copy the token it prints "
               "(it starts with sk-ant-oat) and paste it here. It lasts a year.",
        "link": "https://code.claude.com/docs/en/authentication",
        "link_label": "How the token works",
        "placeholder": "sk-ant-oat01-...",
        "required": True,
        "milestone": 1,
    },
    {
        "key": "ANTHROPIC_API_KEY",
        "title": "Backup: Claude API key (optional)",
        "why": "Only used if you hit your plan's usage limit, or if you choose the API in Settings. "
               "You pay per use, and the spending cap in Settings stops it at your limit.",
        "how": "Sign in, click Create Key, name it 'assistant', and paste it here. Skip this if you don't want a backup.",
        "link": "https://console.anthropic.com/settings/keys",
        "link_label": "Open the Claude Console",
        "placeholder": "sk-ant-api...",
        "required": False,
        "milestone": 1,
    },
    {
        "key": "DISCORD_WEBHOOK_URL",
        "title": "Discord notifications (optional)",
        "why": "A free second place for alerts, next to phone notifications from the app.",
        "how": "In your own Discord server: Server Settings > Integrations > Webhooks > New Webhook > "
               "Copy Webhook URL, then paste it here. Skip if you don't use Discord.",
        "link": "https://support.discord.com/hc/en-us/articles/228383668",
        "link_label": "Discord's webhook guide",
        "placeholder": "https://discord.com/api/webhooks/...",
        "required": False,
        "milestone": 1,
    },
]

SECRET_KEYS = {s["key"] for s in STEPS}
