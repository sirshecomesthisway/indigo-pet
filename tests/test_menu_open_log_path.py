"""Regression test: 'Open Squid log' menu item must open the file
launchd actually writes to.

Found in a 2026-08-17 code review: _menu_open_log() opened
/tmp/squid-pet.log, but nothing in the codebase ever wrote to that
path -- debug_log() (and every other log line) just calls print(),
which launchd redirects to /tmp/squid-pet.out.log per the plist's
StandardOutPath (see launchagent/com.pink.squid-pet.plist.template).
doctor.py's STDOUT_LOG and bin/squid's OUT_LOG already agreed on
squid-pet.out.log; only the menu item's hardcoded path was wrong.
"""
from __future__ import annotations

import threading
from unittest.mock import patch

from squid_pet.window import PetApi


def _make_api():
    api = PetApi.__new__(PetApi)
    api._lock = threading.Lock()
    api._hint_text = ""
    api._hint_seq = 0
    return api


def test_menu_open_log_opens_out_log_path():
    api = _make_api()
    with patch("subprocess.Popen") as mock_popen:
        api._menu_open_log()
    mock_popen.assert_called_once_with(
        ["open", "-a", "Console", "/tmp/squid-pet.out.log"]
    )


def test_menu_open_log_does_not_open_wrong_path():
    """Explicit negative check for the exact bug found: the old path
    (missing '.out') must never be what gets opened."""
    api = _make_api()
    with patch("subprocess.Popen") as mock_popen:
        api._menu_open_log()
    opened_path = mock_popen.call_args[0][0][3]
    assert opened_path != "/tmp/squid-pet.log"
    assert opened_path == "/tmp/squid-pet.out.log"


def test_menu_open_log_path_matches_doctor_and_bin_squid():
    """Cross-check against the other two places this path is
    hardcoded, so all three can't silently drift apart again."""
    import re
    from pathlib import Path
    from squid_pet import doctor

    api = _make_api()
    with patch("subprocess.Popen") as mock_popen:
        api._menu_open_log()
    opened_path = mock_popen.call_args[0][0][3]

    assert opened_path == str(doctor.STDOUT_LOG)

    bin_squid = Path(__file__).parent.parent / "bin" / "squid"
    bin_squid_src = bin_squid.read_text()
    m = re.search(r'OUT_LOG="([^"]+)"', bin_squid_src)
    assert m is not None, "bin/squid's OUT_LOG assignment not found"
    assert opened_path == m.group(1)
