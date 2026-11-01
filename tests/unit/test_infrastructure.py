import asyncio
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from infra_auth import issue_token, verify_token, new_extension_challenge, verify_extension_proof
from infra_storage import TenantSQLiteStore
from infra_workers import JobSandbox
from llm_resilience import CircuitBreaker, call_with_resilience


def test_jwt_and_extension_identity_are_bound():
    secret = "s" * 32
    token = issue_token("tenant-a", secret=secret, ttl_seconds=60, extra={"role": "user"})
    assert verify_token(token, secret=secret)["sub"] == "tenant-a"
    challenge = new_extension_challenge()
    proof = hmac.new(secret.encode(), f"ext:{challenge.nonce}".encode(), hashlib.sha256).hexdigest()
    assert verify_extension_proof(challenge, proof, "ext", secret)
    assert not verify_extension_proof(challenge, proof, "other", secret)


def test_tenant_store_isolates_profiles(tmp_path):
    async def run():
        store = TenantSQLiteStore(tmp_path / "store.db")
        await store.put("a", "profile", {"name": "A"})
        await store.put("b", "profile", {"name": "B"})
        assert await store.get("a", "profile") == {"name": "A"}
        assert await store.get("b", "profile") == {"name": "B"}
    asyncio.run(run())


def test_job_sandboxes_are_unique_and_cleaned(tmp_path):
    with JobSandbox("u", "compile", str(tmp_path)) as first:
        with JobSandbox("u", "compile", str(tmp_path)) as second:
            assert first != second
            assert first.exists() and second.exists()
        assert not second.exists()
    assert not first.exists()


def test_resilience_returns_deterministic_degraded_value():
    async def run():
        breaker = CircuitBreaker(threshold=1, cooldown=60)
        async def fail():
            raise RuntimeError("down")
        assert await call_with_resilience(fail, retries=0, breaker=breaker, degraded={"score": 0}) == {"score": 0}
        assert await call_with_resilience(fail, retries=0, breaker=breaker, degraded={"score": 1}) == {"score": 1}
    asyncio.run(run())
