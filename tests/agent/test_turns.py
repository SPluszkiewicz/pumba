"""Stopping an in-flight model call."""

from __future__ import annotations

import asyncio

import pytest
from turns import TurnCancelled, until_cancelled


@pytest.mark.asyncio
async def test_until_cancelled_aborts_the_inner_call() -> None:
    flag = asyncio.Event()

    async def slow() -> str:
        await asyncio.sleep(30)
        return "done"

    async def trip() -> None:
        await asyncio.sleep(0.01)
        flag.set()

    stopper = asyncio.create_task(trip())
    with pytest.raises(TurnCancelled):
        await until_cancelled(slow(), flag)
    await stopper
