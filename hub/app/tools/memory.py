"""Long-term memory: projects and preferences, editable in the dashboard (Settings > Memory)."""
from .. import db, events
from . import Tool

CATEGORIES = ["preference", "project", "person", "fact", "general"]


def list_memories() -> list[dict]:
    return db.q("SELECT id, category, text, updated FROM memories ORDER BY category, id")


def add(text: str, category: str = "general") -> int:
    mid = db.run("INSERT INTO memories(category, text, created, updated) VALUES(?,?,?,?)",
                 (category, text.strip(), db.now(), db.now()))
    events.publish("memory", {"action": "add", "id": mid})
    return mid


def update(mid: int, text: str, category: str | None = None) -> bool:
    row = db.q1("SELECT id, category FROM memories WHERE id=?", (mid,))
    if not row:
        return False
    db.run("UPDATE memories SET text=?, category=?, updated=? WHERE id=?",
           (text.strip(), category or row["category"], db.now(), mid))
    events.publish("memory", {"action": "update", "id": mid})
    return True


def delete(mid: int) -> bool:
    ok = db.q1("SELECT id FROM memories WHERE id=?", (mid,)) is not None
    db.run("DELETE FROM memories WHERE id=?", (mid,))
    events.publish("memory", {"action": "delete", "id": mid})
    return ok


def as_prompt() -> str:
    rows = list_memories()
    if not rows:
        return "(nothing saved yet)"
    return "\n".join(f"- [{r['id']}] ({r['category']}) {r['text']}" for r in rows)


async def _remember(args: dict) -> str:
    mid = add(args["text"], args.get("category", "general"))
    return f"Saved as memory {mid}."


async def _update(args: dict) -> str:
    return "Updated." if update(args["id"], args["text"]) else f"No memory with id {args['id']}."


async def _forget(args: dict) -> str:
    return "Forgotten." if delete(args["id"]) else f"No memory with id {args['id']}."


TOOLS = [
    Tool("remember",
         "Save a lasting fact about the user: a preference, a project, a person, or a useful fact. "
         "Use it when the user tells you something worth knowing next time, or asks you to remember. "
         "Keep each memory to one short sentence. Check the saved memories first and update instead of duplicating.",
         {"type": "object", "properties": {
             "text": {"type": "string", "description": "One short sentence."},
             "category": {"type": "string", "enum": CATEGORIES}},
          "required": ["text"], "additionalProperties": False},
         _remember),
    Tool("update_memory", "Rewrite a saved memory by its id when a fact has changed.",
         {"type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
          "required": ["id", "text"], "additionalProperties": False},
         _update),
    Tool("forget", "Delete a saved memory by its id, when the user asks or it is no longer true.",
         {"type": "object", "properties": {"id": {"type": "integer"}},
          "required": ["id"], "additionalProperties": False},
         _forget),
]
