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
