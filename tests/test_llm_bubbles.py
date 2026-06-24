"""
test_llm_bubbles.py -- coverage for the LLM enrichment layer.

Two concerns:
  1. Functional: LLM enrichment publishes when it should, stays silent
     when it shouldn't, never breaks the rule-based fallback.
  2. Security (per Pink's mandate 2026-06-24): the puppy_token must
     NEVER leak to logs, exceptions, or returned strings. Multi-tenant
     safety means each associate's token comes from THEIR config.
"""
from __future__ import annotations

import io
import logging
import os
import tempfile
import threading
import time
import unittest.mock as mock
from pathlib import Path

import pytest

from squid_pet import llm_client as llm_mod
from squid_pet.observer import (
    LLM_ENRICH_TRIGGERS,
    MAX_BUBBLE_CHARS,
    Observer,
)


# ============================================================
# LLMClient -- token loading + multi-tenant safety
# ============================================================

def test_loads_token_from_users_own_puppy_cfg(tmp_path, monkeypatch):
    """Token comes from THE USER'S OWN ~/.tpa/puppy.cfg.
    There is no embedded fallback, no shared key."""
    fake_home = tmp_path
    cfg_dir = fake_home / ".tpa"
    cfg_dir.mkdir()
    (cfg_dir / "puppy.cfg").write_text(
        "[puppy]\npuppy_token = test-token-abc123\n"
    )
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH",
                        str(cfg_dir / "puppy.cfg"))
    c = llm_mod.LLMClient()
    assert c.is_available()


def test_no_puppy_cfg_means_unavailable(tmp_path, monkeypatch):
    """If the user has no TPA installed, LLMClient quietly
    disables itself -- no crash, no error, no rule-based fallback break."""
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH",
                        str(tmp_path / "does-not-exist.cfg"))
    c = llm_mod.LLMClient()
    assert not c.is_available()
    assert c.ask("system", "user") is None


def test_malformed_puppy_cfg_means_unavailable(tmp_path, monkeypatch):
    """A corrupt config in one user's home must not break the pet."""
    cfg = tmp_path / "puppy.cfg"
    cfg.write_text("this is not [ valid INI {")
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH", str(cfg))
    c = llm_mod.LLMClient()
    assert not c.is_available()


def test_token_never_in_log_output_on_failure(tmp_path, monkeypatch, caplog):
    """SECURITY: when the HTTP call fails, the token must not appear
    in any log line. We catch exceptions by TYPE, not by message,
    precisely to prevent proxy-error-echo leakage."""
    cfg = tmp_path / "puppy.cfg"
    secret = "SECRET-TOKEN-DO-NOT-LEAK-xyz789"
    cfg.write_text(f"[puppy]\npuppy_token = {secret}\n")
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH", str(cfg))

    c = llm_mod.LLMClient()
    # Force a connection failure by pointing at an unreachable URL
    monkeypatch.setattr(llm_mod, "ANTHROPIC_URL",
                        "https://127.0.0.1:1/anthropic/v1/messages")
    with caplog.at_level(logging.WARNING):
        result = c.ask("sys", "user", timeout=1.0)
    assert result is None
    # The token must NEVER appear in any log line
    for record in caplog.records:
        assert secret not in record.getMessage(), \
            f"TOKEN LEAKED in log: {record.getMessage()!r}"


def test_token_not_returned_in_any_reply(tmp_path, monkeypatch):
    """SECURITY: even on the happy path, the token never appears in
    returned strings. (Sanity check -- we only return model content.)"""
    cfg = tmp_path / "puppy.cfg"
    secret = "ANOTHER-SECRET-TOKEN-zzz"
    cfg.write_text(f"[puppy]\npuppy_token = {secret}\n")
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH", str(cfg))

    c = llm_mod.LLMClient()
    fake_resp = io.BytesIO(
        b'{"content":[{"type":"text","text":"hmm, pytest"}]}'
    )
    fake_resp.__enter__ = lambda *_: fake_resp
    fake_resp.__exit__ = lambda *_: None

    with mock.patch.object(llm_mod.urllib.request, "urlopen",
                           return_value=fake_resp):
        result = c.ask("sys", "user")
    assert result == "hmm, pytest"
    assert secret not in (result or "")


def test_rate_limiter_drops_calls_inside_gap(tmp_path, monkeypatch):
    """Cost protection: rapid-fire calls past the gap return None
    without hitting the network."""
    cfg = tmp_path / "puppy.cfg"
    cfg.write_text("[puppy]\npuppy_token = whatever\n")
    monkeypatch.setattr(llm_mod, "PUPPY_CFG_PATH", str(cfg))
    monkeypatch.setattr(llm_mod, "MIN_CALL_GAP_SEC", 60.0)  # ensure gap

    c = llm_mod.LLMClient()
    fake_resp = io.BytesIO(
        b'{"content":[{"type":"text","text":"first"}]}'
    )
    fake_resp.__enter__ = lambda *_: fake_resp
    fake_resp.__exit__ = lambda *_: None
    with mock.patch.object(llm_mod.urllib.request, "urlopen",
                           return_value=fake_resp) as m:
        first = c.ask("sys", "user")
        second = c.ask("sys", "user")
    assert first == "first"
    assert second is None
    assert m.call_count == 1  # second call never hit network


# ============================================================
# Observer -- backward compat + LLM enrichment behavior
# ============================================================

def test_observer_with_no_llm_behaves_identically():
    """Backward compat: Observer(get_muted) with no LLM = pre-llm behavior."""
    obs = Observer(get_muted=lambda: False)
    result = obs.on_state_change("idle", "celebrating")
    assert result is not None
    assert len(result) <= MAX_BUBBLE_CHARS


def test_async_enrich_only_fires_for_whitelisted_triggers():
    """Triggers like 'poke', 'drowsy', 'waking' should NOT incur LLM cost."""
    publish_calls = []
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "should not be called"

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    # poke is NOT in LLM_ENRICH_TRIGGERS
    assert "poke" not in LLM_ENRICH_TRIGGERS
    obs.on_interaction("poke")
    # Give worker thread a beat (it should never spawn)
    time.sleep(0.1)
    fake_llm.ask.assert_not_called()
    assert publish_calls == []


def test_async_enrich_fires_and_publishes_for_celebrating():
    """Happy path: celebrating fires LLM, valid reply gets published."""
    publish_calls = []
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "shipped"

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    rule_bubble = obs.on_state_change("working", "celebrating")
    # Rule-based bubble returned synchronously
    assert rule_bubble is not None
    # Wait for background worker
    deadline = time.monotonic() + 2.0
    while not publish_calls and time.monotonic() < deadline:
        time.sleep(0.05)
    assert publish_calls == ["shipped"]


def test_async_enrich_drops_oversized_reply():
    """A hallucinated long reply must not break the 32-char bubble UI."""
    publish_calls = []
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "this is way way way way too long for a bubble line yes"

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    obs.on_state_change("working", "concerned", concern_reason="boom")
    time.sleep(0.5)  # generous wait
    assert publish_calls == []  # dropped, never published


def test_async_enrich_drops_empty_reply():
    """Model choosing silence = empty string = no publish."""
    publish_calls = []
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "   "  # whitespace only

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    obs.on_state_change("idle", "working")
    time.sleep(0.3)
    assert publish_calls == []


def test_async_enrich_respects_mute_before_call():
    """If muted at trigger time, LLM is never called at all (cost saving)."""
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "anything"

    obs = Observer(
        get_muted=lambda: True,  # always muted
        llm_client=fake_llm,
        publish_cb=lambda t: None,
    )
    rule_bubble = obs.on_state_change("idle", "celebrating")
    assert rule_bubble is None  # mute kills rule-based too
    time.sleep(0.2)
    fake_llm.ask.assert_not_called()


def test_async_enrich_respects_mute_after_call():
    """If muted while LLM is in-flight, the reply is dropped (not published)."""
    publish_calls = []
    muted = {"v": False}
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True

    def slow_ask(*a, **kw):
        time.sleep(0.15)
        muted["v"] = True  # user mutes during the call
        return "dropped"

    fake_llm.ask.side_effect = slow_ask
    obs = Observer(
        get_muted=lambda: muted["v"],
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    obs.on_state_change("idle", "celebrating")
    time.sleep(0.5)
    assert publish_calls == []  # mute-after-call dropped the publish


def test_llm_client_none_means_no_enrichment():
    """Belt-and-suspenders: explicit None llm_client = no thread spawned."""
    publish_calls = []
    obs = Observer(
        get_muted=lambda: False,
        llm_client=None,
        publish_cb=lambda t: publish_calls.append(t),
    )
    rule = obs.on_state_change("idle", "celebrating")
    assert rule is not None
    time.sleep(0.2)
    assert publish_calls == []


def test_unavailable_llm_means_no_enrichment():
    """LLMClient whose is_available()=False is treated as None."""
    publish_calls = []
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = False

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=lambda t: publish_calls.append(t),
    )
    obs.on_state_change("idle", "celebrating")
    time.sleep(0.2)
    fake_llm.ask.assert_not_called()
    assert publish_calls == []


def test_llm_publish_failure_does_not_crash_observer(caplog):
    """If publish_cb itself raises (e.g. window torn down), worker
    swallows it and logs class only -- no thread crash."""
    fake_llm = mock.Mock()
    fake_llm.is_available.return_value = True
    fake_llm.ask.return_value = "boom"

    def broken_publish(_):
        raise RuntimeError("window gone")

    obs = Observer(
        get_muted=lambda: False,
        llm_client=fake_llm,
        publish_cb=broken_publish,
    )
    with caplog.at_level(logging.WARNING):
        obs.on_state_change("idle", "celebrating")
        time.sleep(0.5)
    # Should have logged a warning, but exception class only
    assert any("publish_cb raised" in r.getMessage()
               for r in caplog.records)
