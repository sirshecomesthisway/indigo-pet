"""Tests for find_codex_processes()'s cmdline-basename matching.

Mirrors test_find_claude_code_processes.py. Codex's npm distribution
ships a JS shim (bin/codex.js under node) that spawns the native Rust
binary (`codex` or `codex-tui`) as a child process -- matching must go
through the native binary's own argv[0], not the node wrapper's.
"""
from __future__ import annotations

import psutil

from squid_pet import watcher


class _FakeProc:
    def __init__(self, pid, cmdline):
        self.pid = pid
        self._cmdline = cmdline

    def cmdline(self):
        return self._cmdline


def test_matches_bare_codex_cmdline(monkeypatch):
    procs = [
        _FakeProc(1, ["codex"]),
        _FakeProc(2, ["zsh"]),
        _FakeProc(3, []),
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [1]


def test_matches_codex_tui_binary(monkeypatch):
    procs = [_FakeProc(4, ["codex-tui"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [4]


def test_does_not_match_node_shim_parent(monkeypatch):
    """The npm shim process itself (`node .../bin/codex.js`) must NOT
    match -- only the native binary it spawns should."""
    procs = [_FakeProc(5, ["node", "/usr/local/lib/node_modules/@openai/codex/bin/codex.js"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert matches == []


def test_matches_full_path_codex_cmdline(monkeypatch):
    procs = [_FakeProc(7, ["/usr/local/bin/codex", "--flag"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [7]


def test_does_not_cross_match_claude(monkeypatch):
    """find_codex_processes and find_claude_code_processes share the
    _find_processes_by_argv0_basename helper -- make sure the name sets
    don't bleed into each other."""
    procs = [_FakeProc(8, ["claude"]), _FakeProc(9, ["codex"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    assert [p.pid for p in watcher.find_codex_processes()] == [9]
    assert [p.pid for p in watcher.find_claude_code_processes()] == [8]


def test_process_iter_errors_are_skipped(monkeypatch):
    class _Dead(_FakeProc):
        def cmdline(self):
            raise psutil.NoSuchProcess(pid=1)

    procs = [_Dead(1, []), _FakeProc(2, ["codex"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [2]


def test_excludes_app_server_headless_mode(monkeypatch):
    """Regression test: found live on 2026-08-15 -- a third-party tool
    (openclaw) runs a vendored Codex binary as `codex app-server
    --listen stdio://`, a JSON-RPC backend for programmatic control, not
    an interactive session a human is watching. Must not count as
    codex_running."""
    procs = [_FakeProc(
        10,
        ["/Users/x/.openclaw/npm/.../vendor/x86_64-apple-darwin/bin/codex",
         "app-server", "--listen", "stdio://"],
    )]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert matches == []


def test_excludes_headless_exec_and_mcp_subcommands(monkeypatch):
    procs = [
        _FakeProc(11, ["codex", "exec", "do the thing"]),
        _FakeProc(12, ["codex", "exec-server"]),
        _FakeProc(13, ["codex", "mcp"]),
        _FakeProc(14, ["codex", "mcp-server"]),
        _FakeProc(15, ["codex"]),  # bare interactive -- should still match
    ]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [15]


def test_interactive_prompt_arg_still_matches(monkeypatch):
    """`codex <prompt text>` (no subcommand, just an initial message) is
    a normal interactive invocation and must still match."""
    procs = [_FakeProc(16, ["codex", "fix the failing test"])]
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(procs))
    matches = watcher.find_codex_processes()
    assert [p.pid for p in matches] == [16]
