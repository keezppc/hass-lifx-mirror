"""Shared test doubles."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


class FakeHass:
    """Runs the background tasks Home Assistant would, so tests can wait for them."""

    def __init__(self) -> None:
        """Start with no tasks."""
        self.tasks: list[asyncio.Task] = []

    def async_create_background_task(
        self, coro: Coroutine[Any, Any, Any], name: str | None = None
    ) -> asyncio.Task:
        """Start a task like Home Assistant's, and remember it."""
        task = asyncio.get_running_loop().create_task(coro, name=name)
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        """Wait for every task started so far."""
        await asyncio.gather(*self.tasks)
