"""Small Playwright primitives for the cloud application runner.

The runner deliberately stops at browser setup and upload synchronization.  It
does not contain OTP handling or an application state machine.
"""

from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Mapping, Protocol


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


class RunnerState(str, Enum):
    """Explicit lifecycle states for one application run."""

    CREATED = "created"
    CONTEXT_READY = "context_ready"
    FORM_READY = "form_ready"
    WAITING_FOR_OTP = "waiting_for_otp"
    OTP_RECEIVED = "otp_received"
    READY_FOR_SUBMIT = "ready_for_submit"
    SUBMITTED = "submitted"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvalidRunnerTransition(RuntimeError):
    """Raised when a run attempts to skip or repeat a lifecycle transition."""


_RUNNER_TRANSITIONS: dict[RunnerState, frozenset[RunnerState]] = {
    RunnerState.CREATED: frozenset({RunnerState.CONTEXT_READY, RunnerState.FAILED, RunnerState.CANCELLED}),
    RunnerState.CONTEXT_READY: frozenset({RunnerState.FORM_READY, RunnerState.FAILED, RunnerState.CANCELLED}),
    RunnerState.FORM_READY: frozenset({
        RunnerState.WAITING_FOR_OTP,
        RunnerState.READY_FOR_SUBMIT,
        RunnerState.FAILED,
        RunnerState.CANCELLED,
    }),
    RunnerState.WAITING_FOR_OTP: frozenset({
        RunnerState.OTP_RECEIVED,
        RunnerState.FAILED,
        RunnerState.CANCELLED,
    }),
    RunnerState.OTP_RECEIVED: frozenset({RunnerState.READY_FOR_SUBMIT, RunnerState.FAILED, RunnerState.CANCELLED}),
    RunnerState.READY_FOR_SUBMIT: frozenset({RunnerState.SUBMITTED, RunnerState.FAILED, RunnerState.CANCELLED}),
    RunnerState.SUBMITTED: frozenset(),
    RunnerState.FAILED: frozenset(),
    RunnerState.CANCELLED: frozenset(),
}


@dataclass
class RunnerStateMachine:
    """Small auditable state machine shared by browser-runner integrations."""

    state: RunnerState = RunnerState.CREATED
    history: list[RunnerState] = field(default_factory=lambda: [RunnerState.CREATED])

    def transition(self, target: RunnerState) -> RunnerState:
        if target not in _RUNNER_TRANSITIONS[self.state]:
            raise InvalidRunnerTransition(f"{self.state.value} -> {target.value} is not allowed")
        self.state = target
        self.history.append(target)
        return target

    def fail(self) -> RunnerState:
        return self.transition(RunnerState.FAILED)

    def cancel(self) -> RunnerState:
        return self.transition(RunnerState.CANCELLED)

    def mark_submitted(self, *, human_approved: bool) -> RunnerState:
        if not human_approved:
            raise InvalidRunnerTransition("submission requires explicit human approval")
        return self.transition(RunnerState.SUBMITTED)

    async def wait_for_otp(
        self,
        reader: "InboxReader",
        *,
        timeout: float = 60.0,
        poll_interval: float = 1.0,
        since: datetime | None = None,
    ) -> "OtpMatch":
        self.transition(RunnerState.WAITING_FOR_OTP)
        try:
            match = await wait_for_inbox_otp(
                reader,
                timeout=timeout,
                poll_interval=poll_interval,
                since=since,
            )
        except Exception:
            self.fail()
            raise
        self.transition(RunnerState.OTP_RECEIVED)
        return match


@dataclass(frozen=True)
class AnswerResolution:
    """Answer lookup result; unresolved answers must remain a human decision."""

    value: str | None
    source: str
    needs_review: bool = False


def _answer_key(value: str) -> str:
    return " ".join((value or "").casefold().split())


class AnswerMemoryResolver:
    """Resolve form answers through safe, deterministic memory tiers.

    The resolver intentionally does not fuzzy-match questions.  An exact learned
    answer or exact profile key is safe to reuse; evidence suggestions are always
    marked for review unless the caller explicitly approves them.
    """

    def __init__(
        self,
        *,
        learned_answers: Mapping[str, Any] | None = None,
        common_answers: Mapping[str, Any] | None = None,
        autofill: Mapping[str, Any] | None = None,
        evidence_lookup: Callable[[str], str | None] | None = None,
    ) -> None:
        self.learned_answers = dict(learned_answers or {})
        self.common_answers = dict(common_answers or {})
        self.autofill = dict(autofill or {})
        self.evidence_lookup = evidence_lookup
        self._cache: dict[tuple[str, str], AnswerResolution] = {}

    def resolve(
        self,
        host: str,
        label: str,
        *,
        human_approved: bool = False,
    ) -> AnswerResolution:
        cache_key = (_answer_key(host), _answer_key(label))
        if cache_key in self._cache and not human_approved:
            return self._cache[cache_key]

        learned_key = f"{cache_key[0]}::{cache_key[1]}"
        value = self.learned_answers.get(learned_key)
        if isinstance(value, str) and value.strip():
            result = AnswerResolution(value.strip(), "learned", False)
        else:
            value = self.common_answers.get(cache_key[1])
            if not isinstance(value, str) or not value.strip():
                value = self.autofill.get(cache_key[1])
            if isinstance(value, str) and value.strip():
                result = AnswerResolution(value.strip(), "profile", False)
            elif self.evidence_lookup is not None:
                evidence = self.evidence_lookup(label)
                if isinstance(evidence, str) and evidence.strip():
                    result = AnswerResolution(
                        evidence.strip(),
                        "evidence" if human_approved else "evidence_pending_review",
                        not human_approved,
                    )
                else:
                    result = AnswerResolution(None, "human", True)
            else:
                result = AnswerResolution(None, "human", True)

        if not result.needs_review:
            self._cache[cache_key] = result
        return result


@dataclass(frozen=True)
class InboxMessage:
    """Read-only message shape used by Gmail/Outlook adapters."""

    message_id: str
    sender: str
    subject: str
    body: str
    received_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class OtpMatch:
    code: str
    message_id: str
    sender: str


class OtpPublisher(Protocol):
    async def publish(self, match: OtpMatch) -> None:
        """Publish an OTP event (for example to Redis PubSub)."""


class InboxReader(Protocol):
    async def fetch(self, *, since: datetime | None = None) -> Iterable[InboxMessage]:
        """Return messages without mutating or marking the inbox read."""


_OTP_PATTERNS = (
    re.compile(r"\b(?:code|otp|verification|passcode)[^\d]{0,24}(\d{4,8})\b", re.I),
    re.compile(r"\b(\d{4,8})\b"),
)


def classify_otp_message(message: InboxMessage) -> OtpMatch | None:
    """Extract an OTP only from a verification-looking message."""

    haystack = f"{message.subject}\n{message.body}"
    if not re.search(r"\b(?:otp|one[- ]time|verification|verify|passcode|security code)\b", haystack, re.I):
        return None
    for pattern in _OTP_PATTERNS:
        match = pattern.search(haystack)
        if match:
            return OtpMatch(match.group(1), message.message_id, message.sender)
    return None


async def wait_for_inbox_otp(
    reader: InboxReader,
    *,
    timeout: float = 60.0,
    poll_interval: float = 1.0,
    since: datetime | None = None,
    publisher: OtpPublisher | None = None,
) -> OtpMatch:
    """Poll a read-only inbox adapter until a fresh OTP arrives or timeout."""

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        for message in await reader.fetch(since=since):
            match = classify_otp_message(message)
            if match:
                if publisher is not None:
                    await publisher.publish(match)
                return match
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for an inbox verification code")
        await asyncio.sleep(min(poll_interval, remaining))


class OtpInboxSynchronizer:
    """Bridge a read-only inbox listener to runner state and event delivery."""

    def __init__(self, reader: InboxReader, publisher: OtpPublisher | None = None) -> None:
        self.reader = reader
        self.publisher = publisher

    async def wait_for_code(
        self,
        *,
        timeout: float = 60.0,
        poll_interval: float = 1.0,
        since: datetime | None = None,
    ) -> OtpMatch:
        return await wait_for_inbox_otp(
            self.reader,
            timeout=timeout,
            poll_interval=poll_interval,
            since=since,
            publisher=self.publisher,
        )


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
