"""
config.py -- user-facing settings persisted to ~/.squid-pet/config.json.

Currently tracks:
  - muted: bool -- suppresses all observer speech bubbles when True

Read with `get(key, default)`; write with `set(key, value)`. Writes are
atomic via temp-file + os.replace. Reads ALWAYS hit disk (cheap, ensures
menu toggles in one process pick up changes from another).
"""
from __future__ import annotations

import json
import os
import pathlib
from typing import Any

CONFIG_DIR = pathlib.Path.home() / ".squid-pet"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    "muted": False,
    # llm-bubbles change 2026-06-24: opt-in LLM-enriched speech bubbles.
    # When True, eligible state transitions ALSO fire a background LLM
    # call (via puppy-backend, using the user's own puppy_token from
    # ~/.tpa/puppy.cfg) that may replace the rule-based bubble
    # with a more contextual line. Off by default so the pet works
    # the same out of the box for every associate.
    "llm_bubbles": False,
    "llm_bubbles_model": "claude-sonnet-4-6",
    # post-e2e-polish 2026-06-27 Fix 1: celebrate hold duration in seconds.
    # Was 4s hard-coded in 3 places; bumped to 20s after Pink noted
    # she never sees Squid celebrate (window closed before she glanced).
    # Hot-reloadable via config.get() pattern. Valid range 4-60.
    "celebrate_hold_sec": 20,
    # post-e2e-polish 2026-06-27 Fix 6: how long after the last tool-call
    # write Squid stays in "working" before falling to "thinking". Was
    # 8s hard-coded; bumped to 20s after Pink noted Squid kept flickering
    # to "thinking" during LLM-generation gaps in active sessions. Hot-
    # reloadable. Set to 60+ for very long generation windows.
    "tool_active_window_sec": 20,
    # Fix 7: sticky working window -- bridges LLM-gen gaps
    "working_hold_sec": 25,
    # llm-bubbles polish 2026-06-27, item 3: hard daily cap to prevent
    # runaway puppy-backend costs. Enforced silently in LLMClient.ask()
    # -- over-cap calls return None and rule-based bubbles fill in.
    # Persistent across Squid restarts via ~/.squid-pet/llm_usage.json.
    # Resets at midnight local time. Set to 0 to disable LLM bubbles
    # entirely (functionally equivalent to llm_bubbles=False but lets
    # you flip the menu toggle without re-enabling network calls).
    "llm_bubbles_daily_cap": 500,
}


def _load_raw() -> dict[str, Any]:
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def get(key: str, default: Any = None) -> Any:
    raw = _load_raw()
    if key in raw:
        return raw[key]
    if default is not None:
        return default
    return DEFAULTS.get(key)


def set(key: str, value: Any) -> None:  # noqa: A001 -- shadowing builtin OK in module
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    raw = _load_raw()
    raw[key] = value
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(raw, f, indent=2)
    os.replace(tmp, CONFIG_FILE)


def is_muted() -> bool:
    return bool(get("muted", False))


def toggle_muted() -> bool:
    """Flip the mute flag, persist, return new value."""
    new_val = not is_muted()
    set("muted", new_val)
    return new_val

def llm_bubbles_enabled() -> bool:
    """True iff llm_bubbles=True AND a puppy_token is loadable. Cheap to
    call -- file read happens lazily inside LLMClient on construction."""
    return bool(get("llm_bubbles", False))


def toggle_llm_bubbles() -> bool:
    """Flip the llm_bubbles flag, persist, return new value."""
    new_val = not llm_bubbles_enabled()
    set("llm_bubbles", new_val)
    return new_val


def approval_alert_enabled() -> bool:
    """Approval-needed notification + sticky bubble (default ON)."""
    return bool(get("approval_alert_enabled", True))


def toggle_approval_alert() -> bool:
    """Flip the approval-alert flag. Returns new value."""
    new = not approval_alert_enabled()
    set_("approval_alert_enabled", new)
    return new
