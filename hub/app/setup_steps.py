"""The first-run wizard's steps. Each step: one key, one link, one plain sentence.
Later milestones add their steps here (Twilio, Gmail, ElevenLabs...)."""

STEPS = [
    {
        "key": "ANTHROPIC_API_KEY",
        "title": "Claude API key",
        "why": "This is how the assistant thinks and answers; you pay per use, usually a few dollars "
               "a month, and the spending cap in Settings stops it at your limit.",
        "how": "Sign in, click Create Key, name it 'assistant', and paste it here. "
               "Add a few dollars of credit under Billing if you haven't before.",
        "link": "https://console.anthropic.com/settings/keys",
        "link_label": "Open the Claude Console",
        "placeholder": "sk-ant-...",
        "required": True,
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
