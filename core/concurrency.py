"""Bound admission to local model work and serialize source document operations."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import partial


class BusyError(RuntimeError):
    """Work could not start within the bounded local queue."""


class InferenceGate:
    """FIFO async admission; cancelled callers never release a running thread's lock."""

    def __init__(self, timeout=30.0, max_pending=16):
        self.timeout = timeout
        self.max_pending = max_pending
        self.lock = asyncio.Lock()
        self.pending = 0
        self.closed = False

    async def run(self, function, *args, **kwargs):
        if self.closed or self.pending >= self.max_pending:
            raise BusyError("Local inference is busy. Retry shortly.")
        self.pending += 1
        try:
            try:
                await asyncio.wait_for(self.lock.acquire(), self.timeout)
            except TimeoutError as exc:
                raise BusyError("Local inference is busy. Retry shortly.") from exc
        finally:
            self.pending -= 1
        if self.closed:
            self.lock.release()
            raise BusyError("Local inference is shutting down. Retry shortly.")
        task = asyncio.create_task(asyncio.to_thread(partial(function, *args, **kwargs)))

        def finished(future):
            self.lock.release()
            # Retrieve exceptions even when the requesting coroutine was cancelled.
            if not future.cancelled():
                future.exception()

        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def close(self):
        self.closed = True
        async with self.lock:
            pass


@dataclass
class _DocumentLock:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


_document_locks = {}


@asynccontextmanager
async def document_operation(collection, document_id, timeout=30.0):
    """Serialize preparation/publication/deletion in the supported single API process.

    References include waiters, so cleanup never creates a second lock for a key
    still in use. No connection or transaction is held during inference.
    """
    key = (collection, document_id)
    entry = _document_locks.setdefault(key, _DocumentLock())
    entry.users += 1
    acquired = False
    try:
        try:
            await asyncio.wait_for(entry.lock.acquire(), timeout)
        except TimeoutError as exc:
            raise BusyError("This document is being updated. Retry shortly.") from exc
        acquired = True
        yield
    finally:
        if acquired:
            entry.lock.release()
        entry.users -= 1
        if not entry.users:
            del _document_locks[key]
