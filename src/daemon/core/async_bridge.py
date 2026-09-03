"""Dedicated background event loop for async-to-sync bridging.

Usage from any thread (with or without a running event loop):

    from core.async_bridge import run_async
    result = run_async(some_coroutine())

Why: asyncio.run() creates and tears down a new loop on every call and
cannot be used from within an already-running loop.
run_coroutine_threadsafe() submits the coroutine to a persistent loop
running in a dedicated daemon thread, which is safe from any calling
context and avoids repeated loop creation overhead.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, TypeVar

_T = TypeVar("_T")

_loop: asyncio.AbstractEventLoop | None = None
_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        with _lock:
            if _loop is None or _loop.is_closed():
                loop = asyncio.new_event_loop()
                t = threading.Thread(
                    target=loop.run_forever,
                    daemon=True,
                    name="voice-assistant-async-bridge",
                )
                t.start()
                _loop = loop
    return _loop


def run_async(coro: Any, timeout: float = 30.0) -> Any:
    """Submit *coro* to the shared background loop and block until it completes."""
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)
