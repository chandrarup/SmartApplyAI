"""
SmartApplyAI — Centralized Logger
===================================
Usage:
    from logger import get_logger
    log = get_logger(__name__)

    log.info("Endpoint hit", extra={"endpoint": "/analyze"})
    log.error("LLM call failed", exc_info=True)

Toggle logging:
    SMART_APPLY_LOGS=0          → fully disabled (NullHandler only)
    SMART_APPLY_LOGS=1          → enabled (default)
    SMART_APPLY_LOG_LEVEL=DEBUG|INFO|WARNING|ERROR  (default: INFO)

Log files:
    backend/logs/smartapply_YYYY-MM-DD.log
    Rotates at midnight, keeps last 30 days.
"""

import logging
import os
import json
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# ─── Paths & env config ───────────────────────────────────────────
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")

_ENV_ENABLED = os.getenv("SMART_APPLY_LOGS", "1").strip().lower() not in ("0", "false", "no", "off")
_ENV_LEVEL_STR = os.getenv("SMART_APPLY_LOG_LEVEL", "INFO").upper()
_ENV_LEVEL = getattr(logging, _ENV_LEVEL_STR, logging.INFO)

# ─── Runtime toggle (flip without restart via /lh/logs/config) ────
_state = {
    "enabled": _ENV_ENABLED,
    "level": _ENV_LEVEL,
}

def is_logging_enabled() -> bool:
    return _state["enabled"]

def set_logging_enabled(enabled: bool) -> None:
    _state["enabled"] = enabled
    _root.setLevel(_state["level"] if enabled else logging.CRITICAL + 1)

def set_log_level(level_str: str) -> bool:
    lvl = getattr(logging, level_str.upper(), None)
    if lvl is None:
        return False
    _state["level"] = lvl
    if _state["enabled"]:
        _root.setLevel(lvl)
    return True

def get_config() -> dict:
    return {
        "enabled": _state["enabled"],
        "level": logging.getLevelName(_state["level"]),
        "logs_dir": LOGS_DIR,
    }

# ─── Log format ───────────────────────────────────────────────────
_FMT = "[%(asctime)s] [%(levelname)-8s] [%(name)s:%(lineno)d]  %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"
_FORMATTER = logging.Formatter(_FMT, _DATE_FMT)

# ─── Root logger under 'smartapply' namespace ─────────────────────
_root = logging.getLogger("smartapply")
_root.propagate = False  # don't double-log to uvicorn root


def _init_root() -> None:
    if _root.handlers:
        return  # already initialized (e.g. hot-reload)

    _root.setLevel(_state["level"] if _state["enabled"] else logging.CRITICAL + 1)

    # Console handler — INFO+ always (even when file logging is off)
    _ch = logging.StreamHandler()
    _ch.setLevel(logging.INFO)
    _ch.setFormatter(_FORMATTER)
    _root.addHandler(_ch)

    if not _state["enabled"]:
        return

    # Daily rotating file — logs/smartapply_YYYY-MM-DD.log
    os.makedirs(LOGS_DIR, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(LOGS_DIR, f"smartapply_{today}.log")

    _fh = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        backupCount=30,
        encoding="utf-8",
    )
    _fh.suffix = "%Y-%m-%d"
    _fh.namer = lambda n: n  # keep the default suffix style
    _fh.setLevel(_state["level"])
    _fh.setFormatter(_FORMATTER)
    _root.addHandler(_fh)


_init_root()


# ─── Logger factory ───────────────────────────────────────────────
def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under 'smartapply.<name>'.
    Use __name__ for automatic module labeling:
        log = get_logger(__name__)
    """
    # Strip the package prefix if called as get_logger(__name__) inside this package
    short = name.split(".")[-1] if "." in name else name
    return logging.getLogger(f"smartapply.{short}")


# ─── Structured log helper ────────────────────────────────────────
def log_event(logger: logging.Logger, level: str, event: str, **fields) -> None:
    """
    Emit a structured log line:
        log_event(log, "INFO", "llm_call", provider="ollama", latency_ms=1230)
    Produces:
        [2026-05-15 14:32:01] [INFO    ] [main:245]  llm_call | provider=ollama latency_ms=1230
    """
    parts = " | ".join(f"{k}={v}" for k, v in fields.items())
    msg = f"{event}  {parts}" if parts else event
    getattr(logger, level.lower(), logger.info)(msg)
