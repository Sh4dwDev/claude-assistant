from ..notify import notify
from . import Tool


async def _notify(args: dict) -> str:
    notify(args["title"], args.get("body", ""))
    return "Notification sent to the dashboard, phone and Discord (whichever are set up)."


TOOLS = [
    Tool("send_notification",
         "Send the user a free push notification (phone, dashboard, Discord). Use only when the user "
         "asks to be reminded or notified, not to repeat what you are already saying in chat.",
         {"type": "object", "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
          "required": ["title"], "additionalProperties": False},
         _notify),
]
