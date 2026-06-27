"""Tests for GitDetector using a real tmp_path filesystem.

We create fake .git/HEAD + .git/index files and tweak their mtimes
via os.utime() to simulate commits / staging / pushes.
"""
from __future__ import annotations
import os
import time
from pathlib import Path
import pytest

from squid_pet.detectors import GitDetector


def _make_repo(root: Path, name: str) -> Path:
    """Create root/name/.git/{HEAD,index,refs/heads/main}."""
    repo = root / name
    git = repo / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "index").write_bytes(b"")
    (git / "refs" / "heads" / "main").write_text("abc123\n")
    return git


def _touch(p: Path, ts: float) -> None:
    p.touch(exist_ok=True)
    os.utime(str(p), (ts, ts))


def test_head_mtime_within_5s_fires_celebrating(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 2.0)         # 2s ago -> celebrating
    _touch(git / "index", now - 100.0)
    _touch(git / "refs" / "heads", now - 100.0)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_celebrating(now=now) is True
    assert d.is_busy(now=now) is False


def test_index_only_fires_busy_not_celebrating(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 100.0)
    _touch(git / "index", now - 1.0)
    _touch(git / "refs" / "heads", now - 100.0)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_busy(now=now) is True
    assert d.is_celebrating(now=now) is False


def test_refs_heads_fires_celebrating_post_push(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 100.0)
    _touch(git / "index", now - 100.0)
    _touch(git / "refs" / "heads", now - 1.0)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_celebrating(now=now) is True


def test_no_activity_is_quiet(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 999.0)
    _touch(git / "index", now - 999.0)
    _touch(git / "refs" / "heads", now - 999.0)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_busy(now=now) is False
    assert d.is_celebrating(now=now) is False


def test_celebrate_hold_lasts_20_seconds(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 1.0)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_celebrating(now=now) is True
    # Even after the underlying signal drops out (HEAD now stale), the
    # sticky hold should keep firing for CELEBRATE_HOLD_SEC.
    _touch(git / "HEAD", now - 100.0)
    assert d.is_celebrating(now=now + 1.0) is True
    assert d.is_celebrating(now=now + 25.0) is False  # past 20s hold (post-e2e-polish Fix 1)


def test_disabled_detector_returns_false(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    now = time.time()
    _touch(git / "HEAD", now - 1.0)
    d = GitDetector(project_dirs=[str(tmp_path)], enabled=False)
    assert d.is_busy(now=now) is False
    assert d.is_celebrating(now=now) is False


def test_no_repos_under_project_dirs_is_quiet(tmp_path):
    (tmp_path / "not-a-repo" / "src").mkdir(parents=True)
    d = GitDetector(project_dirs=[str(tmp_path)])
    assert d.is_busy(now=time.time()) is False
    assert d.is_celebrating(now=time.time()) is False


def test_max_repos_cap_respected(tmp_path):
    # Create 60 repos -> only first 50 should be discovered.
    for i in range(60):
        _make_repo(tmp_path, f"repo_{i:02d}")
    d = GitDetector(project_dirs=[str(tmp_path)])
    d.is_busy(now=time.time())
    assert len(d._discovered) == GitDetector.MAX_REPOS == 50


def test_discovery_cache_reused_within_60s(tmp_path):
    git = _make_repo(tmp_path, "myrepo")
    calls = {"n": 0}
    real_walk = os.walk
    def counting_walk(*a, **kw):
        calls["n"] += 1
        return real_walk(*a, **kw)
    d = GitDetector(project_dirs=[str(tmp_path)], walk_fn=counting_walk)
    now = time.time()
    d.is_busy(now=now); d.is_busy(now=now + 1.0); d.is_busy(now=now + 30.0)
    walks_within_cache = calls["n"]
    d.is_busy(now=now + 70.0)  # past cache TTL
    assert calls["n"] > walks_within_cache, "cache should expire after 60s"


def test_does_not_descend_into_node_modules(tmp_path):
    # Repo lives inside node_modules -> should be skipped.
    (tmp_path / "node_modules" / "fake-pkg" / ".git").mkdir(parents=True)
    _make_repo(tmp_path, "real-repo")
    d = GitDetector(project_dirs=[str(tmp_path)])
    d.is_busy(now=time.time())
    names = [g.parent.name for g in d._discovered]
    assert "real-repo" in names
    assert "fake-pkg" not in names


def test_diagnostic_keys_present(tmp_path):
    _make_repo(tmp_path, "myrepo")
    d = GitDetector(project_dirs=[str(tmp_path)])
    d.is_busy(now=time.time())
    diag = d.diagnostic()
    for k in ("name", "enabled", "repos_watched", "celebrate_until"):
        assert k in diag
    assert diag["name"] == "git"
    assert diag["repos_watched"] == 1
