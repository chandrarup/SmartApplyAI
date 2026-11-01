"""Async retries, circuit breaking, and deterministic degraded responses."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class CircuitBreaker:
    failures: int = 0
    opened_until: float = 0.0
    threshold: int = 3
    cooldown: float = 30.0

    def allow(self) -> bool:
        return time.monotonic() >= self.opened_until

    def success(self) -> None:
        self.failures = 0
        self.opened_until = 0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold:
            self.opened_until = time.monotonic() + self.cooldown


async def call_with_resilience(fn: Callable[[], Awaitable[Any]], *,
                               timeout: float = 25.0, retries: int = 2,
                               breaker: CircuitBreaker | None = None,
                               degraded: Any = None) -> Any:
    breaker = breaker or CircuitBreaker()
    if not breaker.allow():
        return degraded
    for attempt in range(max(0, retries) + 1):
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout)
            breaker.success()
            return result
        except Exception:
            breaker.failure()
            if attempt >= retries:
                return degraded
            await asyncio.sleep(min(0.25 * (2 ** attempt), 1.0))
    return degraded
