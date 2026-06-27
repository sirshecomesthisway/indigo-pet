"""
llm_client.py -- thin internal puppy-backend gateway client for Squid's
optional LLM-enriched speech bubbles.

Design constraints (NON-NEGOTIABLE):
  1. Multi-tenant safe. The puppy_token is read from each user's own
     ~/.tpa/puppy.cfg at runtime. There is NO embedded token,
     no shared key, no fallback to the developer's credentials.
  2. The token is loaded once at startup, kept in memory only, and is
     NEVER written to any log line, state file, exception message, or
     telemetry. Tests assert this.
  3. If the token is missing or unreadable, the client silently disables
     itself. Squid keeps working with rule-based bubbles only.
  4. Failures (network, timeout, bad JSON, rate-limit) return None.
     The caller treats None as "no bubble this turn" -- never crash.
  5. Stdlib only. No new dependencies (urllib.request + json + ssl).

Endpoint references (verified 2026-06-24):
  Anthropic: https://puppy-backend.internal.com/anthropic/v1/messages
  Gemini:    https://puppy-backend.stg.internal.com/gemini
  All accept header X-Api-Key: <puppy_token>
"""
from __future__ import annotations

import configparser
import json
import logging
import os
import ssl
import threading
import datetime
import pathlib
import time
import urllib.error
import urllib.request
import glob
from typing import Optional

log = logging.getLogger(__name__)

# internal puppy-backend, Anthropic Messages API. Prod endpoint is more
# stable than stg; we accept slightly higher latency for reliability.
ANTHROPIC_URL = "https://puppy-backend.internal.com/anthropic/v1/messages"
# Gemini is on stg per TPA's models.json. Cheaper + faster than
# Sonnet for the observer use case (high-frequency, low-stakes blurbs).
GEMINI_URL = "https://puppy-backend.stg.internal.com/gemini/v1beta/models/gemini-2.0-flash-exp:generateContent"

# Path to the TPA config that owns the token. We read the user's
# own copy -- there is no fallback path that would let one associate's
# token be reused for another's session.
PUPPY_CFG_PATH = os.path.join(
    os.path.expanduser("~"), ".tpa", "puppy.cfg"
)

# Hard timeout on any single LLM call. The watcher loop is 1Hz, so we
# can't tolerate long blocks. The LLM call runs in a worker thread, but
# we still cap it to keep threads from piling up.
HTTP_TIMEOUT_SEC = 10.0

# Minimum gap between LLM calls per-process. Protects associates from
# unintentional cost bursts if their state-machine starts thrashing.
MIN_CALL_GAP_SEC = 5.0

# ── Daily cap (cost protection, llm-bubbles polish 2026-06-27) ────────
# Persistent across Squid restarts; resets on date rollover. Set the
# cap via config.json key `llm_bubbles_daily_cap` (default 500). The
# cap is enforced silently -- when exceeded, ask() returns None and
# the observer falls back to rule-based bubbles. Users never see an
# error; they just see fewer LLM-flavored lines until midnight.
USAGE_FILE = pathlib.Path.home() / ".squid-pet" / "llm_usage.json"
DEFAULT_DAILY_CAP = 500

def _today() -> str:
    """ISO date for today (local). Used as the daily-cap reset key."""
    return datetime.date.today().isoformat()

def _load_usage() -> tuple[str, int]:
    """Read (date, count) from USAGE_FILE. On any failure or stale
    date, return (today, 0). Never raises."""
    try:
        data = json.loads(USAGE_FILE.read_text())
        if isinstance(data, dict):
            d = data.get("date")
            n = int(data.get("calls", 0))
            if d == _today():
                return (d, n)
    except (OSError, ValueError, TypeError):
        pass
    return (_today(), 0)

def _write_usage(date: str, count: int) -> None:
    """Persist usage. Best-effort -- failures are silent."""
    try:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        USAGE_FILE.write_text(json.dumps({"date": date, "calls": count}))
    except OSError:
        pass

def _read_daily_cap() -> int:
    """Read cap from config.json (cheap; config caches). Default 500."""
    try:
        from . import config as _cfg
        return int(_cfg.get("llm_bubbles_daily_cap", DEFAULT_DAILY_CAP))
    except Exception:  # noqa: BLE001 -- defensive; never crash on cap read
        return DEFAULT_DAILY_CAP



def _load_puppy_token() -> Optional[str]:
    """Read puppy_token from the user's own ~/.tpa/puppy.cfg.

    Returns None if the file doesn't exist, the section/key is missing,
    or the file can't be parsed. Never raises. Never logs the value.
    """
    if not os.path.isfile(PUPPY_CFG_PATH):
        return None
    try:
        cfg = configparser.ConfigParser()
        cfg.read(PUPPY_CFG_PATH)
        token = cfg.get("puppy", "puppy_token", fallback=None)
        if token and token.strip():
            return token.strip()
    except (configparser.Error, OSError):
        # Swallow silently -- a malformed config from one user must not
        # break the pet for them.
        pass
    return None


def _resolve_ca_bundle() -> Optional[str]:
    """Find the right CA bundle for puppy-backend's self-signed chain.

    internal's puppy-backend sits behind a ZScaler MITM proxy that
    presents a self-signed cert. Python's default certifi bundle does
    not trust it. We try, in order:
      1. _SSL_CERT_FILE env var (TPA sets this in subprocesses)
      2. SSL_CERT_FILE env var (standard openssl convention)
      3. The internal CA bundle that ships with TPA itself,
         discovered via glob of the user's tpa-venv.
    If none are found, returns None and the default certifi bundle is
    used -- which will fail on company-issued laptops behind ZScaler.
    The LLMClient handles that gracefully (call returns None, rule-
    based bubbles still work).
    """
    for env_var in ("_SSL_CERT_FILE", "SSL_CERT_FILE"):
        path = os.environ.get(env_var)
        if path and os.path.isfile(path):
            return path
    pattern = os.path.expanduser(
        "~/.tpa-venv/lib/python*/site-packages/"
        "tpa/plugins/company_specific/certs/internal-bundle.pem"
    )
    matches = glob.glob(pattern)
    if matches:
        return matches[0]
    return None


# Resolved once at import time -- the bundle path doesn't change at runtime.
CA_BUNDLE_PATH: Optional[str] = _resolve_ca_bundle()


class LLMClient:
    """One-shot LLM client wired to puppy-backend.

    Instances are cheap; create once at startup and reuse across calls.
    Thread-safe: each .ask() call is independent and the rate-limiter
    uses a single lock.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
    ):
        """token=None means "auto-load from ~/.tpa/puppy.cfg".
        If still None after that, .is_available() returns False and
        every .ask() returns None.
        """
        # Token resolution: explicit arg > user's own cfg > None
        self._token: Optional[str] = token or _load_puppy_token()
        self._model = model
        self._lock = threading.Lock()
        self._last_call_at: float = 0.0

    def is_available(self) -> bool:
        """True iff a token was successfully loaded. Does NOT verify
        the token works -- a probe call would defeat the point."""
        return self._token is not None

    def ask(
        self,
        system: str,
        user: str,
        max_tokens: int = 60,
        timeout: float = HTTP_TIMEOUT_SEC,
    ) -> Optional[str]:
        """Single-turn completion. Returns the model's text reply,
        empty string (if model chose silence), or None on any failure.

        SECURITY: this method never logs the token, never includes the
        token in any returned string, and never lets the token escape
        into exception messages -- exceptions are caught by TYPE only.
        """
        if not self._token:
            return None

        # Rate-limit per-process to protect associate cost. Drop calls
        # that arrive inside the gap rather than queueing them.
        now = time.monotonic()
        with self._lock:
            if now - self._last_call_at < MIN_CALL_GAP_SEC:
                return None
            self._last_call_at = now

        # Daily-cap check (llm-bubbles polish 2026-06-27). Silent enforcement:
        # over-cap calls return None so the rule-based fallback kicks in.
        # USAGE_FILE read is cheap (one syscall) and lets the cap persist
        # across Squid restarts in case launchd restarts us mid-day.
        cap = _read_daily_cap()
        usage_date, usage_count = _load_usage()
        if usage_count >= cap:
            # Log once per day-over-cap to make the gating visible.
            if usage_count == cap:
                print(f"[squid-pet] llm_client: daily cap reached ({cap}); "
                      f"falling back to rule-based bubbles until midnight",
                      flush=True)
                _write_usage(usage_date, usage_count + 1)  # tip past so we don't re-log
            return None

        call_started = time.time()

        body = json.dumps({
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }).encode("utf-8")

        req = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            method="POST",
            headers={
                "X-Api-Key": self._token,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
        )

        try:
            # Use internal CA bundle when available (handles ZScaler MITM).
            # Falls back to system default if unset.
            ctx = ssl.create_default_context(cafile=CA_BUNDLE_PATH)                 if CA_BUNDLE_PATH else ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError,
                OSError, ValueError) as e:
            # Log ONLY the exception class. The message could echo the
            # auth header back from a misbehaving proxy.
            log.warning("llm_client: ask failed (%s)", type(e).__name__)
            return None
        except Exception as e:  # noqa: BLE001 -- defensive catch-all
            log.warning("llm_client: unexpected (%s)", type(e).__name__)
            return None

        # Anthropic Messages API response shape:
        # {"content": [{"type": "text", "text": "..."}], ...}
        elapsed = time.time() - call_started
        try:
            blocks = payload.get("content") or []
            for blk in blocks:
                if blk.get("type") == "text":
                    text = (blk.get("text") or "").strip()
                    # Success log (llm-bubbles polish 2026-06-27, item 4).
                    # Goes to stdout so it lands in /tmp/squid-pet.out.log
                    # next to other lifecycle events. NEVER logs the
                    # response body (could contain PII echoed by the
                    # model) -- only metadata: elapsed time + char count.
                    print(f"[squid-pet] llm_client: ok ({elapsed:.1f}s, "
                          f"{len(text)} chars)", flush=True)
                    # Increment daily counter on success only -- failed
                    # calls don't count toward the cap.
                    _write_usage(usage_date, usage_count + 1)
                    return text
        except (AttributeError, TypeError):
            return None
        return None
