"""Instant commands: matched by simple patterns and run without an AI round trip.

Every message goes through match() first. If a rule matches, its handler answers in
milliseconds. If nothing matches, the message goes to Claude as normal.

Milestone 2 registers the PC-control rules here ("open Godot", "search YouTube for X",
"volume 30", "pause", "lock", ...). Risky ones (shut down, close a program) return a
confirmation question instead of acting.
"""
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

Handler = Callable[[re.Match], Awaitable[str]]


@dataclass
class Rule:
    pattern: re.Pattern
    handler: Handler
    description: str


RULES: list[Rule] = []


def rule(regex: str, description: str):
    def deco(fn: Handler) -> Handler:
        RULES.append(Rule(re.compile(regex, re.IGNORECASE), fn, description))
        return fn
    return deco


def _normalise(text: str) -> str:
    t = text.strip().rstrip(".!?").strip()
    t = re.sub(r"^(hey |ok |okay )?(claude|jarvis)[, ]+", "", t, flags=re.IGNORECASE)
    t = re.sub(r"^(please |can you |could you )", "", t, flags=re.IGNORECASE)
    return t.strip()


def match(text: str) -> tuple[Rule, re.Match] | None:
    t = _normalise(text)
    for r in RULES:
        m = r.pattern.fullmatch(t)
        if m:
            return r, m
    return None
