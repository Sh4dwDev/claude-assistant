"""Tool registry. Each module in this package exposes TOOLS: list[Tool].
To add a tool (Discord, PC control, email...), drop a module here and list it in MODULES."""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

Handler = Callable[[dict], Awaitable[str | list]]  # a string, or content blocks (text + image)


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Handler
    # Risky tools (send email, delete, spend money) return a confirmation request
    # instead of acting; the user approves in the dashboard. Used from milestone 2 on.
    risky: bool = False
    extra: dict = field(default_factory=dict)

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema,
                "eager_input_streaming": True, **self.extra}


def _modules():
    from . import memory, notify_tool, pc_tools
    return [memory, notify_tool, pc_tools]


def registry() -> dict[str, Tool]:
    out: dict[str, Tool] = {}
    for m in _modules():
        for t in m.TOOLS:
            out[t.name] = t
    return out


def validate(schema: dict, args: Any) -> str | None:
    """Minimal schema check (eager input streaming means the server does not validate)."""
    if not isinstance(args, dict):
        return "input must be an object"
    props = schema.get("properties", {})
    for req in schema.get("required", []):
        if req not in args:
            return f"missing field '{req}'"
    for k, v in args.items():
        if k not in props:
            return f"unknown field '{k}'"
        t = props[k].get("type")
        ok = {"string": isinstance(v, str), "integer": isinstance(v, int) and not isinstance(v, bool),
              "boolean": isinstance(v, bool), "number": isinstance(v, (int, float))}.get(t, True)
        if not ok:
            return f"field '{k}' must be {t}"
        if "enum" in props[k] and v not in props[k]["enum"]:
            return f"field '{k}' must be one of {props[k]['enum']}"
    return None
