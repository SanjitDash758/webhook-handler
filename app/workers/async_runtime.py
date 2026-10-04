# app/workers/async_runtime.py
"""
Shared persistent event loop for every Celery task in this worker process.

process_webhook.py and sweep.py previously each kept their own private
event loop. Under --pool=solo both run in the same OS thread but NOT
the same asyncio loop — and the shared SQLAlchemy engine's connection
pool hands out asyncpg connections that are permanently bound to
whichever loop created them. A connection opened on one module's loop,
then reused on the other module's loop, fails with "attached to a
different loop". One shared loop for the whole process removes the
mismatch entirely: every connection the pool hands out is always used
on the same loop it was opened on, no matter which task checks it out.
"""

import asyncio
from threading import Lock
from typing import Optional

_loop: Optional[asyncio.AbstractEventLoop] = None
_loop_lock = Lock()


def get_worker_loop() -> asyncio.AbstractEventLoop:
    global _loop
    if _loop is None or _loop.is_closed():
        with _loop_lock:
            if _loop is None or _loop.is_closed():
                _loop = asyncio.new_event_loop()
                asyncio.set_event_loop(_loop)
    return _loop


def run_async(coro):
    return get_worker_loop().run_until_complete(coro)