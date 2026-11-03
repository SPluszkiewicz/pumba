"""In-flight chat turns, so a client can stop the model and the tool loop."""

from __future__ import annotations

import asyncio


class TurnCancelled(Exception):
    """The client asked to stop this conversation's current turn."""


class Turns:
    def __init__(self) -> None:
        self._flags: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[object]] = {}

    def start(self, conversation_id: str) -> asyncio.Event:
        flag = asyncio.Event()
        self._flags[conversation_id] = flag
        return flag

    def bind_task(self, conversation_id: str, task: asyncio.Task[object]) -> None:
        self._tasks[conversation_id] = task

    def cancel(self, conversation_id: str) -> bool:
        flag = self._flags.get(conversation_id)
        task = self._tasks.get(conversation_id)
        if flag is None and task is None:
            return False
        if flag is not None:
            flag.set()
        if task is not None and not task.done():
            task.cancel()
        return True

    def finish(self, conversation_id: str) -> None:
        self._flags.pop(conversation_id, None)
        self._tasks.pop(conversation_id, None)


TURNS = Turns()


async def until_cancelled(awaitable, flag: asyncio.Event | None):
    """Await `awaitable`, or raise TurnCancelled if `flag` is set first."""
    if flag is None:
        return await awaitable
    if flag.is_set():
        raise TurnCancelled()
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.create_task(flag.wait())
    try:
        done, _pending = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if task not in done:
            task.cancel()
            raise TurnCancelled()
        waiter.cancel()
        return task.result()
    finally:
        if not task.done():
            task.cancel()
        if not waiter.done():
            waiter.cancel()
