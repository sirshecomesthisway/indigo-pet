"""LLM bubbles polish suite (shipped 2026-06-27).

Three behaviors:
  item 1 -- hot-reload of llm_bubbles config toggle (observer respects
            get_llm_enabled callback per-dispatch, not at __init__ time)
  item 3 -- daily cap persists across restarts, resets on date rollover,
            silently falls back to rule-based bubbles when exceeded
  item 4 -- success logging makes 'is LLM firing?' visible without a
            live network probe
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from squid_pet import llm_client, observer


# ----------------------------------------------------------------------
# Item 1: hot-reload of llm_bubbles toggle via get_llm_enabled callback
# ----------------------------------------------------------------------
class TestObserverLlmHotReload:
    """Observer.get_llm_enabled callback is queried per-dispatch."""

    def _make_observer(self, get_llm_enabled):
        """Build an Observer with a mock LLM client that records calls."""
        mock_llm = MagicMock()
        mock_llm.is_available = lambda: True
        mock_llm.ask = MagicMock(return_value="mocked reply")
        published = []
        obs = observer.Observer(
            get_muted=lambda: False,
            llm_client=mock_llm,
            publish_cb=published.append,
            get_llm_enabled=get_llm_enabled,
        )
        return obs, mock_llm, published

    def _dispatch_and_wait(self, obs, trigger_key="celebrating", context="test"):
        """Trigger _async_enrich and wait for the daemon thread to finish."""
        obs._async_enrich(trigger_key, context)
        # _async_enrich starts a daemon thread; give it a moment to do its work
        for _ in range(20):  # up to ~1s
            time.sleep(0.05)
            # No public 'done' signal -- best-effort wait
        time.sleep(0.05)

    def test_get_llm_enabled_true_dispatches(self):
        obs, mock_llm, published = self._make_observer(lambda: True)
        self._dispatch_and_wait(obs)
        assert mock_llm.ask.called

    def test_get_llm_enabled_false_skips_dispatch(self):
        obs, mock_llm, published = self._make_observer(lambda: False)
        self._dispatch_and_wait(obs)
        assert not mock_llm.ask.called
        assert published == []

    def test_get_llm_enabled_callback_is_queried_each_call(self):
        """Critical: the flag is re-read on every dispatch, not cached."""
        flag = [False]  # mutable so the closure tracks current value
        obs, mock_llm, _ = self._make_observer(lambda: flag[0])

        # First dispatch with flag=False
        self._dispatch_and_wait(obs)
        assert mock_llm.ask.call_count == 0

        # Flip flag, dispatch again
        flag[0] = True
        self._dispatch_and_wait(obs)
        assert mock_llm.ask.call_count == 1

        # Flip back off
        flag[0] = False
        self._dispatch_and_wait(obs)
        assert mock_llm.ask.call_count == 1  # no additional call

    def test_default_get_llm_enabled_is_always_true(self):
        """Back-compat: if caller doesn't pass get_llm_enabled, dispatch
        always (matches pre-polish behavior for older embedders/tests)."""
        mock_llm = MagicMock()
        mock_llm.is_available = lambda: True
        mock_llm.ask = MagicMock(return_value="hi")
        obs = observer.Observer(
            get_muted=lambda: False,
            llm_client=mock_llm,
            publish_cb=lambda _: None,
            # NOTE: no get_llm_enabled passed
        )
        # Should default to always-true gate
        assert obs._get_llm_enabled() is True


# ----------------------------------------------------------------------
# Item 3: daily cap behavior
# ----------------------------------------------------------------------
class TestDailyCap:
    """USAGE_FILE persistence + cap enforcement."""

    @pytest.fixture
    def isolated_usage(self, tmp_path, monkeypatch):
        usage_file = tmp_path / "llm_usage.json"
        monkeypatch.setattr(llm_client, "USAGE_FILE", usage_file)
        return usage_file

    def test_load_usage_empty_file_returns_today_zero(self, isolated_usage):
        date, count = llm_client._load_usage()
        assert date == llm_client._today()
        assert count == 0

    def test_load_usage_today_returns_persisted(self, isolated_usage):
        llm_client._write_usage(llm_client._today(), 42)
        date, count = llm_client._load_usage()
        assert date == llm_client._today()
        assert count == 42

    def test_load_usage_stale_date_resets(self, isolated_usage):
        # Write yesterday's date with a count
        isolated_usage.write_text(json.dumps({"date": "2020-01-01", "calls": 999}))
        date, count = llm_client._load_usage()
        assert date == llm_client._today()
        assert count == 0  # reset because date is stale

    def test_load_usage_corrupt_file_is_silent(self, isolated_usage):
        isolated_usage.write_text("{not json")
        date, count = llm_client._load_usage()
        assert date == llm_client._today()
        assert count == 0

    def test_write_usage_creates_parent_dir(self, tmp_path, monkeypatch):
        # Point USAGE_FILE inside a not-yet-existing dir
        usage_file = tmp_path / "deep" / "nested" / "llm_usage.json"
        monkeypatch.setattr(llm_client, "USAGE_FILE", usage_file)
        llm_client._write_usage(llm_client._today(), 5)
        assert usage_file.exists()
        assert json.loads(usage_file.read_text())["calls"] == 5

    def test_read_daily_cap_defaults_to_500(self, monkeypatch):
        # Force config lookup to fall back to default
        from squid_pet import config as _cfg
        monkeypatch.setattr(_cfg, "get",
                           lambda k, d=None: d if k == "llm_bubbles_daily_cap" else None)
        assert llm_client._read_daily_cap() == 500

    def test_read_daily_cap_honors_config(self, monkeypatch):
        from squid_pet import config as _cfg
        monkeypatch.setattr(_cfg, "get",
                           lambda k, d=None: 100 if k == "llm_bubbles_daily_cap" else d)
        assert llm_client._read_daily_cap() == 100

    def test_ask_blocked_when_over_cap(self, isolated_usage, monkeypatch):
        """Over-cap calls return None without hitting the network."""
        # Set cap=5 and write usage already at the cap
        from squid_pet import config as _cfg
        monkeypatch.setattr(_cfg, "get",
                           lambda k, d=None: 5 if k == "llm_bubbles_daily_cap" else d)
        llm_client._write_usage(llm_client._today(), 5)

        # Patch urlopen so any network attempt would be obvious
        urlopen_mock = MagicMock(
            side_effect=AssertionError("urlopen must not be called when over cap")
        )
        monkeypatch.setattr(llm_client.urllib.request, "urlopen", urlopen_mock)

        c = llm_client.LLMClient(token="dummy")
        result = c.ask(system="s", user="u")
        assert result is None
        urlopen_mock.assert_not_called()


# ----------------------------------------------------------------------
# Item 4: success logging visible in stdout
# ----------------------------------------------------------------------
class TestSuccessLogging:
    """Successful ask() prints a metadata-only line to stdout."""

    def test_success_logs_metadata_only(self, capsys, tmp_path, monkeypatch):
        # Isolate the usage file so the counter increment doesn't touch
        # Pink's real ~/.squid-pet/llm_usage.json
        monkeypatch.setattr(llm_client, "USAGE_FILE", tmp_path / "u.json")

        # Mock urlopen to return a successful Anthropic-shaped response
        fake_body = json.dumps({
            "content": [{"type": "text", "text": "hello!"}],
        }).encode("utf-8")

        class FakeResp:
            def read(self): return fake_body
            def __enter__(self): return self
            def __exit__(self, *args): pass

        monkeypatch.setattr(llm_client.urllib.request, "urlopen",
                          lambda *a, **kw: FakeResp())

        c = llm_client.LLMClient(token="dummy")
        result = c.ask(system="s", user="u")

        out = capsys.readouterr().out
        assert result == "hello!"
        assert "llm_client: ok" in out
        # Must NOT contain the response body (PII concern)
        assert "hello!" not in out
        # Must include char count
        assert "6 chars" in out  # len("hello!") == 6

    def test_failure_does_not_log_ok(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setattr(llm_client, "USAGE_FILE", tmp_path / "u.json")
        from urllib.error import URLError
        monkeypatch.setattr(llm_client.urllib.request, "urlopen",
                          lambda *a, **kw: (_ for _ in ()).throw(URLError("dead")))

        c = llm_client.LLMClient(token="dummy")
        result = c.ask(system="s", user="u")
        out = capsys.readouterr().out
        assert result is None
        assert "llm_client: ok" not in out

    def test_success_increments_daily_counter(self, tmp_path, monkeypatch):
        usage_file = tmp_path / "llm_usage.json"
        monkeypatch.setattr(llm_client, "USAGE_FILE", usage_file)

        fake_body = json.dumps({
            "content": [{"type": "text", "text": "ok"}],
        }).encode("utf-8")

        class FakeResp:
            def read(self): return fake_body
            def __enter__(self): return self
            def __exit__(self, *args): pass

        monkeypatch.setattr(llm_client.urllib.request, "urlopen",
                          lambda *a, **kw: FakeResp())

        c = llm_client.LLMClient(token="dummy")
        c.ask(system="s", user="u")

        # Counter should be 1 after one success
        data = json.loads(usage_file.read_text())
        assert data["calls"] == 1
        assert data["date"] == llm_client._today()

    def test_failure_does_not_increment_counter(self, tmp_path, monkeypatch):
        usage_file = tmp_path / "llm_usage.json"
        monkeypatch.setattr(llm_client, "USAGE_FILE", usage_file)

        from urllib.error import URLError
        monkeypatch.setattr(llm_client.urllib.request, "urlopen",
                          lambda *a, **kw: (_ for _ in ()).throw(URLError("dead")))

        c = llm_client.LLMClient(token="dummy")
        c.ask(system="s", user="u")

        # File should not exist OR show calls=0
        if usage_file.exists():
            assert json.loads(usage_file.read_text())["calls"] == 0
