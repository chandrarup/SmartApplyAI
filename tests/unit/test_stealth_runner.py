import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "backend"))

from stealth_runner import (  # noqa: E402
    AnswerMemoryResolver,
    ConcurrencyLimitExceeded,
    InboxMessage,
    InvalidRunnerTransition,
    OtpInboxSynchronizer,
    RunnerState,
    RunnerStateMachine,
    StealthContextConfig,
    StrictConcurrencyLimiter,
    classify_otp_message,
    create_stealth_context,
    upload_resume_and_wait,
    wait_for_inbox_otp,
)


class MockContext:
    def __init__(self):
        self.options = None
        self.script = None

    async def add_init_script(self, script):
        self.script = script


class MockBrowser:
    def __init__(self):
        self.context = MockContext()

    async def new_context(self, **options):
        self.context.options = options
        return self.context


class MockLocator:
    def __init__(self):
        self.path = None

    async def set_input_files(self, path):
        self.path = path


class MockPage:
    def __init__(self):
        self.input = MockLocator()
        self.expression = None
        self.timeout = None

    def locator(self, selector):
        assert selector == "input[type=file]"
        return self.input

    async def wait_for_function(self, expression, timeout):
        self.expression = expression
        self.timeout = timeout


def test_context_injects_proxy_locale_timezone_and_stealth_script():
    browser = MockBrowser()
    config = StealthContextConfig(
        proxy={"server": "http://proxy.test:8080"},
        locale="en-GB",
        timezone_id="Europe/London",
    )

    context = asyncio.run(create_stealth_context(browser, config))

    assert context is browser.context
    assert context.options["proxy"] == {"server": "http://proxy.test:8080"}
    assert context.options["locale"] == "en-GB"
    assert context.options["timezone_id"] == "Europe/London"
    assert "navigator" in context.script and "webdriver" in context.script


def test_upload_waits_for_parser_barrier():
    page = MockPage()

    asyncio.run(upload_resume_and_wait(page, "resume.pdf", timeout=1234))

    assert page.input.path == "resume.pdf"
    assert "status === 'ready'" in page.expression
    assert page.timeout == 1234


def test_concurrency_ceiling_is_fail_fast_and_releases():
    async def scenario():
        limiter = StrictConcurrencyLimiter(1)
        async with limiter.slot():
            assert limiter.active == 1
            with pytest.raises(ConcurrencyLimitExceeded):
                async with limiter.slot():
                    pass
        assert limiter.active == 0

    asyncio.run(scenario())


def test_runner_state_machine_rejects_skipped_states():
    runner = RunnerStateMachine()
    runner.transition(RunnerState.CONTEXT_READY)
    with pytest.raises(InvalidRunnerTransition):
        runner.transition(RunnerState.SUBMITTED)
    assert runner.history == [RunnerState.CREATED, RunnerState.CONTEXT_READY]


def test_submission_requires_human_approval():
    runner = RunnerStateMachine(RunnerState.READY_FOR_SUBMIT, [
        RunnerState.CREATED,
        RunnerState.CONTEXT_READY,
        RunnerState.FORM_READY,
        RunnerState.READY_FOR_SUBMIT,
    ])
    with pytest.raises(InvalidRunnerTransition):
        runner.mark_submitted(human_approved=False)
    runner.mark_submitted(human_approved=True)
    assert runner.state == RunnerState.SUBMITTED


def test_answer_memory_uses_learned_then_profile_then_reviewed_evidence():
    resolver = AnswerMemoryResolver(
        learned_answers={"example.com::work authorization": "Yes"},
        common_answers={"why interested": "The mission is a strong fit."},
        evidence_lookup=lambda label: "Evidence-backed response" if label.casefold() == "project" else None,
    )

    assert resolver.resolve("example.com", "Work Authorization").value == "Yes"
    assert resolver.resolve("example.com", "Why interested").source == "profile"
    pending = resolver.resolve("example.com", "Project")
    assert pending.value == "Evidence-backed response"
    assert pending.needs_review is True
    approved = resolver.resolve("example.com", "Project", human_approved=True)
    assert approved.source == "evidence"
    assert approved.needs_review is False
    assert resolver.resolve("example.com", "Unknown").needs_review is True


def test_classify_otp_message_requires_verification_context():
    message = InboxMessage("m1", "no-reply@ats.test", "Verification code", "Use 482913 to continue")
    assert classify_otp_message(message).code == "482913"
    assert classify_otp_message(InboxMessage("m2", "x", "Receipt", "Order 482913")) is None


def test_wait_for_inbox_otp_is_read_only_and_runner_syncs_state():
    class Reader:
        def __init__(self):
            self.calls = 0

        async def fetch(self, *, since=None):
            self.calls += 1
            return [
                InboxMessage("m3", "no-reply@ats.test", "Security code", "Code: 123456")
            ]

    async def scenario():
        reader = Reader()
        runner = RunnerStateMachine(RunnerState.FORM_READY, [RunnerState.CREATED, RunnerState.CONTEXT_READY, RunnerState.FORM_READY])
        match = await runner.wait_for_otp(reader, timeout=1, poll_interval=0)
        assert match.code == "123456"
        assert reader.calls == 1
        assert runner.state == RunnerState.OTP_RECEIVED

    asyncio.run(scenario())


def test_otp_synchronizer_publishes_to_an_injected_bus():
    class Reader:
        async def fetch(self, *, since=None):
            return [InboxMessage("m4", "no-reply@ats.test", "OTP", "OTP 765432")]

    class Publisher:
        def __init__(self):
            self.matches = []

        async def publish(self, match):
            self.matches.append(match)

    async def scenario():
        publisher = Publisher()
        match = await OtpInboxSynchronizer(Reader(), publisher).wait_for_code(
            timeout=1, poll_interval=0
        )
        assert match.code == "765432"
        assert [item.code for item in publisher.matches] == ["765432"]

    asyncio.run(scenario())
