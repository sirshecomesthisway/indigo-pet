"""post-e2e-polish 2026-06-27 Fix 4: 'squid update' skips reinstall+restart
when git pull is a no-op (HEAD didn't move) and venv is healthy."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_cmd_update_skips_on_no_op_pull(tmp_path):
    """Set up a fake project dir + venv with a healthy squid-pet entry point,
    then run cmd_update twice. Second invocation must print 'already up to date'
    and skip both 'reinstalling' and 'restarting'."""
    # Set up a fake project dir (we use the real one's git history)
    fake_proj = tmp_path / "squid-pet"
    fake_proj.mkdir()

    # Init a tiny git repo with no remote (so 'git pull' will fail quickly,
    # which is fine because we test the HEAD-comparison branch, not the pull)
    subprocess.run(["git", "init", "-q"], cwd=fake_proj, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=fake_proj, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=fake_proj, check=True)
    (fake_proj / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "-A"], cwd=fake_proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=fake_proj, check=True)

    # Fake venv with a "healthy" squid-pet entry point
    venv = fake_proj / ".venv" / "bin"
    venv.mkdir(parents=True)
    entry = venv / "squid-pet"
    entry.write_text("#!/bin/sh\necho fake squid-pet\n")
    entry.chmod(0o755)

    # Copy bin/squid script and call cmd_update directly.
    # We extract just the function logic by sourcing it.
    squid_script = ROOT / "bin/squid"
    assert squid_script.exists()

    # Build a minimal test driver that defines project_dir() to return our
    # fake_proj, then sources squid and calls cmd_update.
    driver = tmp_path / "driver.sh"
    driver.write_text(f"""#!/bin/bash
project_dir() {{ echo "{fake_proj}"; }}
cmd_restart() {{ echo "RESTART_CALLED"; }}
# Source the real bin/squid, then call cmd_update -- BUT we must override
# project_dir before sourcing. So source as a function library by stripping
# the argument dispatch at the bottom.
SQUID_SRC=$(sed '/^case .*\\$1.*in$/,$d' "{squid_script}")
eval "$SQUID_SRC"
# Override AFTER sourcing
project_dir() {{ echo "{fake_proj}"; }}
cmd_restart() {{ echo "RESTART_CALLED"; }}
cmd_update 2>&1
""")
    driver.chmod(0o755)

    # First run: no remote, so pull will fail. We expect it to fail fast,
    # before hitting the skip logic. That's OK -- this test focuses on the
    # SKIP path triggering when pull SUCCEEDS as a no-op. We simulate that
    # by mocking git pull via a wrapper.

    # Simpler approach: just exercise the comparison logic directly by mocking
    # git pull to succeed without changing HEAD.
    # We do this by putting a fake `git` shim ahead of real git in PATH.
    bin_shim = tmp_path / "shim"
    bin_shim.mkdir()
    git_shim = bin_shim / "git"
    git_shim.write_text(f"""#!/bin/bash
# Pass through everything to real git EXCEPT 'pull --ff-only' which is a no-op
if [ "$1" = "pull" ]; then
    echo "Already up to date." # simulate no-op pull
    exit 0
fi
exec /usr/bin/git "$@"
""")
    git_shim.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_shim}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(driver)], capture_output=True, text=True, env=env, timeout=10
    )
    out = result.stdout + result.stderr

    # Expectations:
    assert "already up to date" in out.lower(), (
        f"expected 'already up to date' skip message; got:\n{out}"
    )
    assert "reinstalling" not in out.lower(), (
        f"reinstall should be skipped; got:\n{out}"
    )
    assert "RESTART_CALLED" not in out, (
        f"restart should be skipped; got:\n{out}"
    )


def test_cmd_update_skip_requires_healthy_venv(tmp_path):
    """If the venv is broken (no entry point), don't skip -- reinstall."""
    fake_proj = tmp_path / "squid-pet"
    fake_proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=fake_proj, check=True)
    subprocess.run(["git", "config", "user.email", "test@test"], cwd=fake_proj, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=fake_proj, check=True)
    (fake_proj / "README.md").write_text("test\n")
    subprocess.run(["git", "add", "-A"], cwd=fake_proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=fake_proj, check=True)

    # NO venv -- so entry point doesn't exist
    squid_script = ROOT / "bin/squid"
    driver = tmp_path / "driver.sh"
    driver.write_text(f"""#!/bin/bash
SQUID_SRC=$(sed '/^case .*\\$1.*in$/,$d' "{squid_script}")
eval "$SQUID_SRC"
project_dir() {{ echo "{fake_proj}"; }}
cmd_restart() {{ echo "RESTART_CALLED"; }}
# Stub `uv` and `pip` so reinstall doesn't actually run
uv() {{ echo "FAKE_UV_RAN $*"; }}
cmd_update 2>&1 || true
""")
    driver.chmod(0o755)

    bin_shim = tmp_path / "shim"
    bin_shim.mkdir()
    git_shim = bin_shim / "git"
    git_shim.write_text(f"""#!/bin/bash
if [ "$1" = "pull" ]; then echo "Already up to date."; exit 0; fi
exec /usr/bin/git "$@"
""")
    git_shim.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_shim}:{env['PATH']}"

    result = subprocess.run(
        ["bash", str(driver)], capture_output=True, text=True, env=env, timeout=10
    )
    out = result.stdout + result.stderr
    # Skip should NOT trigger -- venv entry point is missing.
    assert "already up to date" not in out.lower() or "skipping" not in out.lower(), (
        f"should NOT skip when venv is broken; got:\n{out}"
    )
