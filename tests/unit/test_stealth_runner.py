import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "backend"))

from stealth_runner import (  # noqa: E402
    ConcurrencyLimitExceeded,
    StealthContextConfig,
    StrictConcurrencyLimiter,
    create_stealth_context,
    upload_resume_and_wait,
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
