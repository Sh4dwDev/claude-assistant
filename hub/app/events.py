"""In-process event bus. Every open dashboard holds one Server-Sent Events stream.
publish() is safe to call from any thread."""
import asyncio
import json

_subscribers: set[asyncio.Queue] = set()
_loop: asyncio.AbstractEventLoop | None = None


def _deliver(msg: str) -> None:
    for q in list(_subscribers):
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            pass


def publish(kind: str, data: dict) -> None:
    msg = f"event: {kind}\ndata: {json.dumps(data)}\n\n"
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    if running is not None and running is _loop:
        _deliver(msg)
    elif _loop is not None:
        _loop.call_soon_threadsafe(_deliver, msg)


async def stream():
    global _loop
    _loop = asyncio.get_running_loop()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    _subscribers.add(q)
    try:
        yield "event: hello\ndata: {}\n\n"
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=20)
                yield msg
            except asyncio.TimeoutError:
                yield ": ping\n\n"  # keeps proxies and phones from closing the stream
    finally:
        _subscribers.discard(q)
