"""In-process Knowledge Service (SQLite-backed profile + semantic search)."""

from . import store


def search(*args, **kwargs):
    from .semantic import search as _search

    return _search(*args, **kwargs)


__all__ = ["store", "search"]
