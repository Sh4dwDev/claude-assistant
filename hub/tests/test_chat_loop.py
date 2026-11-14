"""Offline test of the chat loop: a fake Claude asks to save a memory, then answers.
Run from the hub folder:  python -m tests.test_chat_loop"""
import asyncio
import json
import os
import tempfile
from types import SimpleNamespace as NS

os.environ["DATA_DIR"] = tempfile.mkdtemp()

from app import chat, config, costs, db  # noqa: E402

db.init()
config.set_secret("ANTHROPIC_API_KEY", "test")


def block(**kw):
    return NS(**kw, to_dict=lambda mode="json", **_: {k: v for k, v in kw.items()})


def usage():
    return NS(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
              cache_creation_input_tokens=0, server_tool_use=NS(web_search_requests=1))


TURNS = [
    NS(model="claude-opus-5-5", stop_reason="tool_use", usage=usage(), content=[
        block(type="text", text="Noting that."),
        block(type="tool_use", id="tu_1", name="remember", input={"text": "Sam makes games in Godot.", "category": "preference"}),
    ]),
    NS(model="claude-opus-5-5", stop_reason="end_turn", usage=usage(), content=[
        block(type="text", text="Saved. You make games in **Godot**."),
    ]),
]
seen_requests = []


async def fake_stream(client, kwargs):
    seen_requests.append(kwargs)
    turn = TURNS[len(seen_requests) - 1]
    for b in turn.content:
        if b.type == "text":
            yield "text", b.text
        else:
            yield "status", chat._tool_label(b.name)
    yield "final", turn


chat._stream_once = fake_stream


async def main():
    out = [x async for x in chat.run_chat(None, "Remember that I make games in Godot")]
    kinds = [x.split("\n")[0] for x in out]
    print(*kinds, sep="\n")
    assert kinds[0] == "event: meta" and kinds[-1] == "event: done", kinds
    assert any("Saving to memory" in x for x in out)

    mems = db.q("SELECT * FROM memories")
    assert mems and "Godot" in mems[0]["text"], mems

    # second request must carry: user, assistant(tool_use), user(tool_result)
    msgs = seen_requests[1]["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"], msgs
    assert msgs[2]["content"][0]["type"] == "tool_result"
    # memory appears in the system prompt of the second request
    assert "Godot" in seen_requests[1]["system"]

    ui = chat.history_for_ui(json.loads(out[0].split("data: ")[1])["conversation_id"])
    assert [m["role"] for m in ui] == ["user", "assistant", "assistant"], ui

    spent = costs.spent("claude_api")
    expected = 2 * ((1000 * 4 + 200 * 20) / 1e6 + 0.01)
    assert abs(spent - expected) < 1e-9, (spent, expected)
    print(f"OK - memory saved, history valid, cost ${spent:.4f}")


asyncio.run(main())
