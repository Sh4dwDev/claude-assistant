"""A tiny MCP server (streamable HTTP, JSON responses) that exposes the hub's tools
(memory, PC control, coding jobs, notifications) to Claude Code running on the hub.

Only reachable from inside the hub: it requires a random token that changes on every
start and is handed to Claude Code in its --mcp-config.
"""
import json
import logging
import os
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from . import tools

log = logging.getLogger("mcp")
TOKEN = secrets.token_urlsafe(32)
SERVER_NAME = "assistant"
router = APIRouter()


def mcp_config() -> dict:
    base_url = os.environ.get("HUB_INTERNAL_URL", "http://127.0.0.1:8000").rstrip("/")
    return {"mcpServers": {SERVER_NAME: {"type": "http", "url": f"{base_url}/api/mcp",
                                         "headers": {"Authorization": f"Bearer {TOKEN}"}}}}


def _content(out) -> list[dict]:
    if isinstance(out, str):
        return [{"type": "text", "text": out}]
    blocks = []
    for b in out:
        if b.get("type") == "image":
            src = b["source"]
            blocks.append({"type": "image", "data": src["data"], "mimeType": src["media_type"]})
        else:
            blocks.append({"type": "text", "text": b.get("text", "")})
    return blocks


async def _handle(msg: dict) -> dict | None:
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notification (e.g. notifications/initialized): no reply
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    if method == "initialize":
        return ok({"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": "1.0"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": [{"name": t.name, "description": t.description, "inputSchema": t.schema}
                             for t in tools.registry().values()]})
    if method == "tools/call":
        tool = tools.registry().get(params.get("name"))
        args = params.get("arguments") or {}
        if not tool:
            return ok({"content": [{"type": "text", "text": f"Unknown tool {params.get('name')}"}], "isError": True})
        err = tools.validate(tool.schema, args)
        if err:
            return ok({"content": [{"type": "text", "text": f"Invalid input: {err}"}], "isError": True})
        try:
            return ok({"content": _content(await tool.handler(args))})
        except Exception as e:  # noqa: BLE001
            log.exception("tool %s failed", tool.name)
            return ok({"content": [{"type": "text", "text": f"Tool error: {e}"}], "isError": True})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


@router.post("/api/mcp")
async def mcp_post(request: Request):
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    body = json.loads(await request.body() or b"null")
    if isinstance(body, list):
        replies = [r for r in [await _handle(m) for m in body] if r]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await _handle(body or {})
    return JSONResponse(reply) if reply else Response(status_code=202)


@router.get("/api/mcp")
async def mcp_get():
    return Response(status_code=405)  # no server-initiated stream


@router.delete("/api/mcp")
async def mcp_delete():
    return Response(status_code=200)
