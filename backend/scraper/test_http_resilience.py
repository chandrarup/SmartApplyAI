"""Tests for HTTP retry/backoff (sourcing-v3 §2.2).

Fully offline: requests.get/post are monkeypatched, time.sleep and random.uniform
are stubbed so backoff is instant and deterministic. No real network.
"""

from __future__ import annotations

import requests
import pytest

from backend.scraper.providers import base


class FakeResp:
    def __init__(self, status, json_data=None, headers=None):
        self.status_code = status
        self._json = json_data if json_data is not None else {"ok": True}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Record sleeps without waiting; make jitter deterministic (0)."""
    sleeps: list[float] = []
    monkeypatch.setattr(base.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(base.random, "uniform", lambda a, b: 0.0)
    return sleeps


def _seq_get(monkeypatch, responses):
    """Patch requests.get to yield the given responses/exceptions in order."""
    calls = {"n": 0}

    def fake_get(url, **kw):
        i = calls["n"]
        calls["n"] += 1
        item = responses[min(i, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(base.requests, "get", fake_get)
    return calls


def test_success_first_try(monkeypatch):
    _seq_get(monkeypatch, [FakeResp(200, {"jobs": [1, 2]})])
    payload, latency = base.timed_get_json("http://x")
    assert payload == {"jobs": [1, 2]}
    assert isinstance(latency, float) and latency >= 0.0


def test_retries_then_succeeds_on_connection_error(monkeypatch, _no_sleep):
    calls = _seq_get(monkeypatch, [
        requests.exceptions.ConnectionError("boom"),
        requests.exceptions.ConnectionError("boom"),
        FakeResp(200, {"ok": 1}),
    ])
    payload = base.http_get_json("http://x", retries=3)
    assert payload == {"ok": 1}
    assert calls["n"] == 3           # 2 failures + 1 success
    assert len(_no_sleep) == 2       # slept before each retry


def test_5xx_is_retried(monkeypatch, _no_sleep):
    calls = _seq_get(monkeypatch, [FakeResp(503), FakeResp(500), FakeResp(200, {"ok": 1})])
    assert base.http_get_json("http://x", retries=3) == {"ok": 1}
    assert calls["n"] == 3


def test_429_honors_retry_after(monkeypatch, _no_sleep):
    _seq_get(monkeypatch, [FakeResp(429, headers={"Retry-After": "7"}), FakeResp(200)])
    base.http_get_json("http://x", retries=3)
    assert _no_sleep == [7.0]        # Retry-After overrode exponential backoff


def test_retry_after_capped(monkeypatch, _no_sleep):
    _seq_get(monkeypatch, [FakeResp(429, headers={"Retry-After": "9999"}), FakeResp(200)])
    base.http_get_json("http://x", retries=3)
    assert _no_sleep == [base.RETRY_AFTER_CAP_SECONDS]


def test_404_fails_immediately_no_retry(monkeypatch, _no_sleep):
    calls = _seq_get(monkeypatch, [FakeResp(404), FakeResp(200)])
    with pytest.raises(requests.exceptions.HTTPError):
        base.http_get_json("http://x", retries=3)
    assert calls["n"] == 1           # non-retryable 4xx → single attempt
    assert _no_sleep == []


def test_exhausts_retries_then_raises(monkeypatch, _no_sleep):
    calls = _seq_get(monkeypatch, [FakeResp(500)])   # always 500
    with pytest.raises(requests.exceptions.HTTPError):
        base.http_get_json("http://x", retries=2)
    assert calls["n"] == 3           # initial + 2 retries
    assert len(_no_sleep) == 2


def test_exponential_backoff_bounded(monkeypatch, _no_sleep):
    _seq_get(monkeypatch, [FakeResp(500), FakeResp(500), FakeResp(500), FakeResp(200)])
    base.http_get_json("http://x", retries=5)
    # jitter stubbed to 0 → 1*2^0, 1*2^1, 1*2^2 = 1, 2, 4; all <= cap
    assert _no_sleep == [1.0, 2.0, 4.0]
    assert all(s <= base.BACKOFF_CAP_SECONDS for s in _no_sleep)


def test_post_retries_and_returns_latency(monkeypatch, _no_sleep):
    calls = {"n": 0}

    def fake_post(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.Timeout("slow")
        return FakeResp(200, {"posted": True})

    monkeypatch.setattr(base.requests, "post", fake_post)
    payload, latency = base.timed_post_json("http://x", {"q": "ml"}, retries=2)
    assert payload == {"posted": True}
    assert isinstance(latency, float) and latency >= 0.0
    assert calls["n"] == 2
