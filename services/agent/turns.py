"""In-flight chat turns, so a client can stop the model and the tool loop."""

from __future__ import annotations

import asyncio
import threading


class TurnCancelled(Exception):
    """The client asked to stop this conversation's current turn."""


class TurnBusy(Exception):
    """This conversation already has an in-flight turn."""


class Turns:
    def __init__(self) -> None:
        self._flags: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[object]] = {}
        self._mu = threading.Condition()

    def _busy(self, conversation_id: str) -> bool:
        return conversation_id in self._flags or conversation_id in self._tasks

    def active(self, conversation_id: str) -> bool:
        with self._mu:
            return self._busy(conversation_id)

    def start(self, conversation_id: str) -> asyncio.Event:
        with self._mu:
            if self._busy(conversation_id):
                raise TurnBusy(conversation_id)
            flag = asyncio.Event()
            self._flags[conversation_id] = flag
            return flag

    async def wait_until_free(self, conversation_id: str) -> None:
        """Block until this conversation has no registered turn."""

        def blocked() -> None:
            with self._mu:
                while self._busy(conversation_id):
                    self._mu.wait()

        await asyncio.to_thread(blocked)

    def bind_task(self, conversation_id: str, task: asyncio.Task[object]) -> None:
        with self._mu:
            self._tasks[conversation_id] = task

    def cancel(self, conversation_id: str) -> bool:
        with self._mu:
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
        with self._mu:
            self._flags.pop(conversation_id, None)
            self._tasks.pop(conversation_id, None)
            self._mu.notify_all()


TURNS = Turns()


async def until_cancelled(awaitable, flag: asyncio.Event | None):
    """Await `awaitable`, or raise TurnCancelled if `flag` is set first.

    The inner call is cancelled *and awaited* so the llama-server HTTP
    request does not keep generating after the client hits stop.
    """
    if flag is None:
        return await awaitable
    if flag.is_set():
        raise TurnCancelled()
    task = asyncio.ensure_future(awaitable)
    waiter = asyncio.create_task(flag.wait())
    try:
        done, _pending = await asyncio.wait(
            {task, waiter}, return_when=asyncio.FIRST_COMPLETED
        )
        if task not in done:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise TurnCancelled()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        return task.result()
    finally:
        for item in (task, waiter):
            if not item.done():
                item.cancel()
        await asyncio.gather(task, waiter, return_exceptions=True)
