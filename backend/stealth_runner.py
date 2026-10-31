"""Small Playwright primitives for the cloud application runner.

The runner deliberately stops at browser setup and upload synchronization.  It
does not contain OTP handling or an application state machine.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator, Mapping


@dataclass(frozen=True)
class StealthContextConfig:
    """Browser context settings supplied by the deployment/runtime."""

    proxy: Mapping[str, str] | None = None
    locale: str = "en-US"
    timezone_id: str = "America/New_York"
    user_agent: str | None = None
    extra_http_headers: Mapping[str, str] = field(default_factory=dict)
    viewport: Mapping[str, int] | None = None


def _context_options(config: StealthContextConfig) -> dict[str, Any]:
    options: dict[str, Any] = {
        "locale": config.locale,
        "timezone_id": config.timezone_id,
        "extra_http_headers": dict(config.extra_http_headers),
    }
    if config.proxy:
        options["proxy"] = dict(config.proxy)
    if config.user_agent:
        options["user_agent"] = config.user_agent
    if config.viewport:
        options["viewport"] = dict(config.viewport)
    return options


async def create_stealth_context(browser: Any, config: StealthContextConfig | None = None) -> Any:
    """Create a context with injectable fingerprint settings.

    This is intentionally a small, deterministic init script rather than a
    third-party stealth plugin.  Runtime-specific proxy and fingerprint values
    remain injectable and are never hard-coded into a driver.
    """

    config = config or StealthContextConfig()
    context = await browser.new_context(**_context_options(config))
    await context.add_init_script(
        """
        Object.defineProperty(navigator, "webdriver", { get: () => undefined });
        Object.defineProperty(navigator, "languages", {
          get: () => [%s]
        });
        window.chrome = window.chrome || { runtime: {} };
        """
        % json.dumps(config.locale)
    )
    return context


class ConcurrencyLimitExceeded(RuntimeError):
    """Raised when starting another run would exceed the configured ceiling."""


class StrictConcurrencyLimiter:
    """Fail-fast async concurrency ceiling for browser runs."""

    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        self.limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active(self) -> int:
        return self._active

    async def acquire(self) -> None:
        async with self._lock:
            if self._active >= self.limit:
                raise ConcurrencyLimitExceeded(
                    f"concurrency ceiling of {self.limit} reached"
                )
            self._active += 1

    async def release(self) -> None:
        async with self._lock:
            if self._active <= 0:
                raise RuntimeError("concurrency limiter released without acquire")
            self._active -= 1

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            await self.release()


async def wait_for_resume_parser(
    page: Any,
    *,
    ready_expression: str = "window.__smartApplyResumeParser?.status === 'ready'",
    error_expression: str = "window.__smartApplyResumeParser?.status === 'error'",
    timeout: float = 30_000,
) -> None:
    """Wait until an ATS parser has consumed the selected resume.

    The page owns the parser-specific signal.  Waiting on that signal avoids a
    race where the next form step is evaluated before the ATS has extracted the
    PDF's contents.
    """

    await page.wait_for_function(
        f"!({error_expression}) && ({ready_expression})",
        timeout=timeout,
    )


async def upload_resume_and_wait(
    page: Any,
    resume_path: str | Path,
    *,
    input_selector: str = "input[type=file]",
    timeout: float = 30_000,
    ready_expression: str = "window.__smartApplyResumeParser?.status === 'ready'",
    error_expression: str = "window.__smartApplyResumeParser?.status === 'error'",
) -> None:
    """Select a resume and wait for the ATS parser synchronization barrier."""

    await page.locator(input_selector).set_input_files(str(resume_path))
    await wait_for_resume_parser(
        page,
        ready_expression=ready_expression,
        error_expression=error_expression,
        timeout=timeout,
    )
