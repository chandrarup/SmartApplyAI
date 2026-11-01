"""ARQ-compatible queue facade with collision-free per-job sandboxes."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable


class JobSandbox:
    def __init__(self, user_id: str, job_id: str, root: str | None = None):
        safe_user = "".join(c for c in str(user_id) if c.isalnum() or c in "._-")[:80] or "user"
        safe_job = "".join(c for c in str(job_id) if c.isalnum() or c in "._-")[:80] or "job"
        parent = Path(root or os.getenv("SMARTAPPLY_WORK_ROOT") or tempfile.gettempdir())
        self.path = parent / "smartapply" / safe_user / f"{safe_job}-{secrets.token_hex(6)}"

    def __enter__(self) -> Path:
        self.path.mkdir(parents=True, exist_ok=False)
        return self.path

    def __exit__(self, *_exc):
        shutil.rmtree(self.path, ignore_errors=True)


def sanitize_artifact_name(name: str) -> str:
    clean = Path(str(name or "")).name
    clean = "".join(c for c in clean if c.isalnum() or c in "._- ")
    return clean[:160] or "artifact"


class LocalJobQueue:
    def __init__(self, workers: int = 2):
        self._queue: asyncio.Queue[tuple[str, Callable[..., Awaitable[Any]], tuple, dict]] = asyncio.Queue()
        self._workers = max(1, int(workers))
        self._tasks: list[asyncio.Task] = []
        self._results: dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._worker()) for _ in range(self._workers)]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()

    async def enqueue(self, fn: Callable[..., Awaitable[Any]], *args, **kwargs) -> str:
        await self.start()
        job_id = secrets.token_urlsafe(12)
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._results[job_id] = future
        await self._queue.put((job_id, fn, args, kwargs))
        return job_id

    async def result(self, job_id: str, timeout: float = 30) -> Any:
        future = self._results.get(job_id)
        if future is None:
            raise KeyError(job_id)
        return await asyncio.wait_for(asyncio.shield(future), timeout)

    async def _worker(self):
        while True:
            job_id, fn, args, kwargs = await self._queue.get()
            future = self._results[job_id]
            try:
                future.set_result(await fn(*args, **kwargs))
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self._queue.task_done()
