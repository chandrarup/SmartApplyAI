"""Tenant-safe storage primitives and an optional PostgreSQL backend.

SQLite is the default for the laptop workflow. ``PostgresStore`` is a thin
async SQLAlchemy adapter used when the v2 deployment provides DATABASE_URL;
imports are lazy so the existing local install remains lightweight.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


def tenant_id(user_id: str) -> str:
    value = str(user_id or "").strip()
    if not value or len(value) > 128 or any(c not in "._-@+" and not c.isalnum() for c in value):
        raise ValueError("invalid tenant id")
    return value


class TenantSQLiteStore:
    """A small RLS-equivalent store: every operation requires a tenant id."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = str(path or os.getenv("SMARTAPPLY_INFRA_DB") or
                       Path(tempfile.gettempdir()) / "smartapply-infra.db")
        self._lock = asyncio.Lock()
        with sqlite3.connect(self.path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS tenant_documents "
                "(tenant_id TEXT NOT NULL, doc_key TEXT NOT NULL, payload TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY (tenant_id, doc_key))"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    async def put(self, user_id: str, key: str, payload: Any) -> None:
        tid = tenant_id(user_id)
        if not key or len(key) > 200:
            raise ValueError("invalid document key")
        async with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO tenant_documents(tenant_id, doc_key, payload) VALUES(?,?,?) "
                    "ON CONFLICT(tenant_id, doc_key) DO UPDATE SET payload=excluded.payload, "
                    "updated_at=CURRENT_TIMESTAMP",
                    (tid, key, json.dumps(payload, ensure_ascii=False)),
                )

    async def get(self, user_id: str, key: str) -> Any | None:
        tid = tenant_id(user_id)
        async with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT payload FROM tenant_documents WHERE tenant_id=? AND doc_key=?",
                    (tid, key),
                ).fetchone()
        return json.loads(row[0]) if row else None


class PostgresStore:
    """Async SQLAlchemy store with transaction-local tenant context/RLS support."""

    def __init__(self, url: str | None = None):
        self.url = url or os.getenv("DATABASE_URL", "")
        if not self.url:
            raise ValueError("DATABASE_URL is required for PostgresStore")
        if not self.url.startswith(("postgresql+asyncpg://", "postgres://", "postgresql://")):
            raise ValueError("DATABASE_URL must be PostgreSQL")
        self._engine = None

    async def _engine_for(self):
        if self._engine is None:
            try:
                from sqlalchemy.ext.asyncio import create_async_engine
            except ImportError as exc:
                raise RuntimeError("Install sqlalchemy[asyncio] and asyncpg for PostgreSQL") from exc
            url = self.url.replace("postgres://", "postgresql+asyncpg://", 1)
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            self._engine = create_async_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20)
        return self._engine

    @asynccontextmanager
    async def transaction(self, user_id: str):
        tid = tenant_id(user_id)
        engine = await self._engine_for()
        async with engine.begin() as conn:
            await conn.exec_driver_sql("SELECT set_config('app.user_id', :tid, true)", {"tid": tid})
            yield conn
