#!/usr/bin/env python3
"""demo_states.py -- drive Squid through her states on a timer, for demo recording.

Walks the pet through a scripted beat sequence so you can record the pitch:

    "When your agent is working, she's working.
     When it's thinking, she's thinking.
     When it needs you, she asks for your attention -- even if you've
     stepped away.
     And when the job is done, she celebrates with you."

HOW IT DRIVES HER
-----------------
Two mechanisms, deliberately:

1. ~/.squid-pet/force_state -- the watcher's built-in test/demo override
   (see StateMachine.compute(), watcher.py). Read every tick, stomps the
   computed state last, so it beats every real detector. This is what
   makes the visuals deterministic even while a real Claude Code session
   is running in the background during the shoot.

2. ~/.squid-pet/claude_awaiting_input/<session> -- a real "session is
   waiting on you" flag, the exact file Claude Code's Notification hook
   writes. force_state alone can NOT produce the macOS banner (the
   override is applied after the approval block), so the approval beat
   drops a synthetic flag to get a genuine notification + sound. That is
   the "even if you've stepped away" half of the line.

Both are removed on exit -- including Ctrl-C, kill, and crash -- because a
stale force_state file silently pins Squid forever.

NOTHING HERE CAN DAMAGE CODE
----------------------------
The only files this script writes are its own two signal files under
~/.squid-pet, plus a disposable prop project in ~/.squid-pet/demo-sandbox
(guarded by an assert that refuses to build outside ~/.squid-pet). The real
Claude session is launched with its cwd pinned to that sandbox and a system
prompt fencing it there, and the command it asks permission for --
`./deploy.sh --production` -- is a prop made of nine echo lines. Saying yes
to it on camera prints a fake deploy log and exits 0. No repo, no config
and no source file is read, moved, or modified by any of this.

USAGE
-----
    python3 scripts/demo_states.py --shot-list     # read this first
    python3 scripts/demo_states.py                 # THE TAKE (real prompt)
    python3 scripts/demo_states.py --hold 12       # slower, for voiceover
    python3 scripts/demo_states.py --all-states    # + grooving/sleeping/... B-roll
    python3 scripts/demo_states.py --fake-prompt   # visuals only, no question
    python3 scripts/demo_states.py --lead-in 5     # 5s to start recording
    python3 scripts/demo_states.py --states working,thinking,celebrating
    python3 scripts/demo_states.py --clear         # panic button: undo everything
"""

from __future__ import annotations

import argparse
import atexit
import os
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

SQUID_HOME = Path.home() / ".squid-pet"
FORCE_STATE_FILE = SQUID_HOME / "force_state"
AWAITING_DIR = SQUID_HOME / "claude_awaiting_input"
STATE_FILE = SQUID_HOME / "state.json"
PID_FILE = SQUID_HOME / "pid"

# Synthetic session id for the fake awaiting-input flag. Prefixed so it is
# obviously ours and never collides with a real uuid from the hook.
DEMO_SESSION_ID = f"demo-{os.getpid()}"

# Session id of the real Claude session THIS run launched, if any. Recorded
# so cleanup can clear its awaiting-input flag.
#
# Why that needs cleaning up: answering "No" to a permission prompt fires no
# hook at all. claude_pet_hook.py clears the flag on UserPromptSubmit (you
# typed something) or SessionEnd (the session exited cleanly) -- a denial is
# neither. Confirmed in ~/.squid-pet/claude_hook.log: a denied-then-closed
# session leaves "Notification ... WRITE permission_prompt" as its last
# entry, and the flag file outlives the process. The watcher's 120s snooze
# eventually quiets the wave, but a stale flag mid-shoot is exactly the
# thing that ruins a take. We opened that session, so we close it out.
_LAUNCHED_SESSION_ID: str | None = None

# Watcher polls once a second; anything under ~2s is not reliably visible.
MIN_BEAT_SEC = 2.0

# --- --real-prompt mode -----------------------------------------------------
# A throwaway project for the real Claude session to work in. Deliberately
# NOT a directory with a .claude/settings.json allowlist: any Bash call in
# here hits the permission gate, which is the whole point.
SANDBOX_DIR = SQUID_HOME / "demo-sandbox"

# Where the launched window records claude's exit status. Lets the demo tell
# "claude never started" apart from "claude started but you haven't answered
# yet" -- without putting a pipe on claude's stdout (see launch_claude_ask).
EXIT_STATUS_FILE = SQUID_HOME / "demo-claude-exit"

# The ask Claude puts on screen. It has to satisfy two things at once:
# legible on camera (the permission dialog shows the raw command, so it
# should read as a real, weighty decision) and INCAPABLE of damaging
# anything (this runs during a take, and you may well hit "yes" on camera).
#
# `./deploy.sh --production` is the sweet spot: it looks like the scariest
# thing you could greenlight, and prepare_sandbox() writes that script as
# nine lines of echo. Approving it prints a fake deploy log and exits 0.
# Nothing is deleted, moved, pushed, or overwritten -- in the sandbox or
# anywhere else.
DEFAULT_ASK_PROMPT = (
    "Ship this release: run exactly this command, nothing else, and don't "
    "explain first: ./deploy.sh --production"
)

# Belt-and-braces for the launched session: cwd is already the sandbox, but
# a real model with a real Bash tool deserves an explicit fence.
ASK_SYSTEM_PROMPT = (
    "You are running inside a disposable demo sandbox for a screen "
    "recording. Run only the single command the user names, then stop. "
    "Do not read, create, modify, or delete anything outside this "
    "directory. Do not run git commands. Keep all output short."
)

# ---------------------------------------------------------------------------
# Beats -- one per line of the pitch. `notify` marks the beat that should also
# fire a real macOS notification.
# ---------------------------------------------------------------------------
Beat = tuple  # (state, caption, notify)

PITCH_BEATS: list[Beat] = [
    ("working",         "When your agent is working, she's working.",          False),
    ("thinking",        "When it's thinking, she's thinking.",                 False),
    ("approval_needed", "When it needs you, she asks for your attention "
                        "\u2014 even if you've stepped away.",                 True),
    ("celebrating",     "And when the job is done, she celebrates with you.",  False),
]

# Every state the frontend has a sprite (or explicit mapping) for. Passing
# something else is not fatal -- the frontend falls back to idle.png -- but a
# typo'd state during a take is worth catching up front.
KNOWN_STATES = {
    "idle", "thinking", "working", "grooving", "celebrating",
    "sleeping", "concerned", "approval_needed", "drowsy", "stretch",
}


# ---------------------------------------------------------------------------
# Cleanup -- must be bulletproof, a stale force_state pins her forever
# ---------------------------------------------------------------------------
def cleanup(verbose: bool = True) -> None:
    """Remove every artifact this script creates. Safe to call repeatedly."""
    removed = []
    try:
        if FORCE_STATE_FILE.exists():
            FORCE_STATE_FILE.unlink()
            removed.append(str(FORCE_STATE_FILE))
    except OSError as e:
        print(f"!! could not remove {FORCE_STATE_FILE}: {e}", file=sys.stderr)
        print("!! REMOVE IT BY HAND or Squid stays pinned.", file=sys.stderr)
    # Our synthetic flag, plus the real flag left behind by the session we
    # launched. Only ever these two -- never a flag belonging to a Claude
    # session you started yourself.
    for sid in (DEMO_SESSION_ID, _LAUNCHED_SESSION_ID):
        if not sid:
            continue
        try:
            flag = AWAITING_DIR / sid
            if flag.exists():
                flag.unlink()
                removed.append(str(flag))
        except OSError:
            pass
    if verbose and removed:
        print("\ncleaned up:")
        for path in removed:
            print(f"  - {path}")
    if verbose:
        print("force_state override cleared -- Squid is back on real signals.")


def clear_all_demo_flags() -> None:
    """--clear: undo anything a previous (possibly crashed) run left behind,
    including demo flags from other PIDs."""
    cleanup(verbose=False)
    stale = []
    try:
        for entry in AWAITING_DIR.iterdir():
            if entry.name.startswith("demo-"):
                entry.unlink()
                stale.append(entry.name)
    except OSError:
        pass
    print(f"cleared: {FORCE_STATE_FILE} (if present)")
    if stale:
        print(f"cleared stale demo flags: {', '.join(stale)}")

    # Any remaining flag is a real Claude session id. We do NOT delete those
    # blindly -- one of them may be a session of yours that is genuinely
    # waiting on you right now, and silently cancelling that wave is worse
    # than a stuck one. Report them with their age and let you decide.
    others = []
    try:
        now = time.time()
        for entry in sorted(AWAITING_DIR.iterdir()):
            if entry.name.startswith("demo-"):
                continue
            others.append((entry.name, now - entry.stat().st_mtime))
    except OSError:
        pass
    if others:
        print("\nstill waving for these real Claude sessions:")
        for name, age in others:
            print(f"  {name}  ({age / 60:.1f} min old)")
        print("  If one is stuck (you answered 'No' to a permission prompt,")
        print("  which fires no hook, then closed the window), clear it with:")
        print(f"    rm {AWAITING_DIR}/<session-id>")
    print("\nSquid is back on real signals.")


def _install_signal_handlers() -> None:
    def _handler(signum, _frame):
        print(f"\n[signal {signum}] stopping demo...")
        cleanup()
        # Re-raise with default disposition so the exit code is honest.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            pass


# ---------------------------------------------------------------------------
# Driving the pet
# ---------------------------------------------------------------------------
def set_state(state: str) -> None:
    """Pin the pet to `state` via the watcher's force_state override."""
    SQUID_HOME.mkdir(parents=True, exist_ok=True)
    tmp = FORCE_STATE_FILE.with_suffix(".tmp")
    tmp.write_text(state + "\n")
    tmp.replace(FORCE_STATE_FILE)  # atomic -- watcher may read mid-write


def release_state() -> None:
    """Drop the override and hand the pet back to her real signals. Used by
    the --real-prompt beat: forcing a state there would mask the very
    behaviour we're trying to film."""
    try:
        FORCE_STATE_FILE.unlink()
    except OSError:
        pass


def raise_attention_flag() -> bool:
    """Drop a synthetic awaiting-input flag so the watcher fires a REAL macOS
    notification (banner + sound), the way Claude Code's Notification hook
    does. Returns True if the flag was written."""
    try:
        AWAITING_DIR.mkdir(parents=True, exist_ok=True)
        (AWAITING_DIR / DEMO_SESSION_ID).write_text("")
        return True
    except OSError as e:
        print(f"  (couldn't write attention flag: {e})", file=sys.stderr)
        return False


def lower_attention_flag() -> None:
    try:
        (AWAITING_DIR / DEMO_SESSION_ID).unlink()
    except OSError:
        pass


# The fake deploy. Every line is an echo -- this is the whole reason the
# demo can point a real model with a real Bash tool at a real permission
# prompt and still be unable to break anything.
_FAKE_DEPLOY_SH = """#!/usr/bin/env bash
# squid-pet demo prop. Does nothing. Deliberately, verifiably nothing.
echo "==> deploying to production"
echo "==> building bundle ......... ok"
echo "==> uploading assets ........ ok"
echo "==> running migrations ...... ok"
echo "==> health check ............ ok"
echo "==> done (this is a demo prop; nothing was deployed)"
exit 0
"""


def prepare_sandbox() -> None:
    """Build a plausible little project for the real Claude session to act on,
    so the command in the permission prompt refers to something that exists.

    Everything lives under ~/.squid-pet/demo-sandbox -- not a git repo, not
    on your PATH, containing nothing but props. The assert below is the one
    line that guarantees this function can never write into real code, no
    matter how SANDBOX_DIR is edited later.
    """
    assert SANDBOX_DIR.resolve().is_relative_to(SQUID_HOME.resolve()), (
        f"refusing to build a sandbox outside {SQUID_HOME}")

    build = SANDBOX_DIR / "build"
    build.mkdir(parents=True, exist_ok=True)
    (SANDBOX_DIR / "README.md").write_text(
        "# demo-sandbox\n\nScratch project for the squid-pet demo video.\n"
        "Everything in here is a disposable prop.\n")
    (SANDBOX_DIR / "main.py").write_text('print("hello from the demo")\n')
    deploy = SANDBOX_DIR / "deploy.sh"
    deploy.write_text(_FAKE_DEPLOY_SH)
    deploy.chmod(0o755)
    for name in ("bundle.js", "bundle.js.map", "styles.css"):
        (build / name).write_text("// demo build artifact\n")


# Measured live 2026-08-31 (Claude Code 2.1.252): ~29s from window open to
# UserPromptSubmit, ~60s to the permission_prompt Notification. The default
# wave timeout has to clear that with room to spare.
def launch_claude_ask(session_id: str, prompt: str, bounds: str | None) -> bool:
    """Open a NEW Terminal window running a real `claude` session whose first
    move needs your permission.

    This is the genuine path, not a simulation: Claude Code fires its
    Notification hook with notification_type=permission_prompt, squid-pet's
    own hook receiver (scripts/claude_pet_hook.py) writes the awaiting-input
    flag, and the watcher reacts to that exactly as it would on any real day.
    Nothing is forced.

    The session gets an explicit --session-id so we can watch for that one
    flag file and ignore any other Claude session you have open.
    """
    claude_bin = _which_claude()
    if not claude_bin:
        print("!! `claude` not found on PATH -- can't launch the real prompt.",
              file=sys.stderr)
        return False

    prepare_sandbox()
    EXIT_STATUS_FILE.unlink(missing_ok=True)
    # NOT `exec claude ...`. Two reasons, both learned the hard way:
    #
    #  - exec replaces the shell, so when claude exits for any reason the
    #    window collapses to Terminal's bare "[Process completed]" and
    #    whatever claude printed on the way out is unreadable.
    #  - stdout must stay attached to the TTY. Piping it anywhere (even to
    #    `tee` for a log) makes Claude Code detect a non-interactive session
    #    and refuse to prompt at all -- it prints "this session can't prompt
    #    for it" and exits, which is exactly the failure this demo can't
    #    afford. So: no pipes, ever, on this command line.
    #
    # The exit status goes to a file instead, which the demo can read back
    # without touching claude's stdout.
    inner = (
        f"cd {shlex.quote(str(SANDBOX_DIR))} && "
        f"{shlex.quote(claude_bin)} --permission-mode default "
        f"--append-system-prompt {shlex.quote(ASK_SYSTEM_PROMPT)} "
        f"--session-id {shlex.quote(session_id)} {shlex.quote(prompt)}; "
        f"echo $? > {shlex.quote(str(EXIT_STATUS_FILE))}; "
        f"echo; echo '[demo] claude exited. Window kept open so you can read "
        f"anything above.'; exec $SHELL -l"
    )
    script_lines = ['tell application "Terminal"', "activate",
                    f'do script "{_applescript_escape(inner)}"']
    if bounds:
        # e.g. "100,100,1200,800" -- left, top, right, bottom in screen points.
        script_lines.append(f"set bounds of front window to {{{bounds}}}")
    script_lines.append("end tell")

    try:
        subprocess.run(["osascript", "-e", "\n".join(script_lines)],
                       timeout=15, capture_output=True, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"!! Terminal launch failed: "
              f"{e.stderr.decode(errors='replace').strip()}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"!! Terminal launch failed: {e}", file=sys.stderr)
        return False


def _which_claude() -> str | None:
    import shutil
    found = shutil.which("claude")
    if found:
        return found
    # Terminal.app launches a login shell, but this script may not have the
    # same PATH -- check the standard install location before giving up.
    fallback = Path.home() / ".local" / "bin" / "claude"
    return str(fallback) if fallback.exists() else None


def _applescript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def flag_present(session_id: str) -> bool:
    return (AWAITING_DIR / session_id).exists()


def wait_until(predicate, timeout: float, label: str, countdown: bool) -> bool:
    """Poll `predicate` until true or timeout. Returns whether it came true."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            if countdown:
                print(f"\r    {label}  ok            ")
            return True
        if countdown:
            print(f"\r    {label}  {end - time.monotonic():4.1f}s ",
                  end="", flush=True)
        time.sleep(0.25)
    if countdown:
        print(f"\r    {label}  timed out     ")
    return False


def watcher_running() -> bool:
    """Best-effort check that the pet is actually up -- a demo against a dead
    watcher looks like the script is broken."""
    try:
        pid = int(PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)  # signal 0 == existence check, doesn't touch the proc
        return True
    except OSError:
        return False


def read_live_state() -> str:
    """What the watcher actually published -- confirms the override landed."""
    try:
        import json
        return json.loads(STATE_FILE.read_text()).get("state", "?")
    except Exception:
        return "?"


def wait_for_state(state: str, timeout: float = 4.0) -> tuple[bool, float]:
    """Poll state.json until the watcher publishes `state`.

    The nominal tick is POLL_INTERVAL_SEC (1.0s), but a real tick costs more
    than that -- the detectors walk the process table -- so a fixed 1.2s
    sleep reads a stale file and cries "watcher is dead" on a healthy pet.
    Poll instead, and report how long the override actually took to land so
    the caller can subtract it from the beat.

    Returns (landed, elapsed_seconds).
    """
    start = time.monotonic()
    deadline = start + timeout
    while time.monotonic() < deadline:
        if read_live_state() == state:
            return True, time.monotonic() - start
        time.sleep(0.15)
    return False, time.monotonic() - start


def hold(seconds: float, label: str, countdown: bool) -> None:
    """Sleep, optionally printing a countdown so you can time your voiceover."""
    if not countdown:
        time.sleep(seconds)
        return
    end = time.monotonic() + seconds
    while True:
        left = end - time.monotonic()
        if left <= 0:
            break
        print(f"\r    {label}  {left:4.1f}s ", end="", flush=True)
        time.sleep(min(0.1, left))
    print(f"\r    {label}  done      ")


def run_real_ask_beat(prompt: str, bounds: str | None, wave_timeout: float,
                      answer_timeout: float, countdown: bool) -> None:
    """The honest version of the attention beat: a real Claude Code session
    asks you a real question, and Squid reacts to the real hook.

    No force_state, no synthetic flag -- the override is dropped first, so
    what the camera sees is the actual production path:

        Claude needs permission
          -> Notification hook (permission_prompt)
            -> claude_pet_hook.py writes claude_awaiting_input/<session>
              -> watcher sees the flag, sets approval_needed, fires the banner
                -> you answer -> UserPromptSubmit hook removes the flag
                  -> Squid stands down
    """
    global _LAUNCHED_SESSION_ID
    release_state()
    session_id = str(uuid.uuid4())
    _LAUNCHED_SESSION_ID = session_id
    print("    launching a real Claude Code session in a new Terminal "
          "window...")
    if not launch_claude_ask(session_id, prompt, bounds):
        print("    falling back to the synthetic attention flag.")
        if raise_attention_flag():
            set_state("approval_needed")
            time.sleep(6)
            lower_attention_flag()
        return

    print("    (Claude takes ~30-60s to boot and reach the permission "
          "gate -- that's your cue to get the shot framed)")

    def _asking_or_dead() -> bool:
        return flag_present(session_id) or EXIT_STATUS_FILE.exists()

    wait_until(_asking_or_dead, wave_timeout, "waiting for Claude to ask",
               countdown)

    if EXIT_STATUS_FILE.exists() and not flag_present(session_id):
        status = EXIT_STATUS_FILE.read_text().strip()
        print(f"    !! claude exited (status {status}) without ever asking.")
        print(f"    !! The Terminal window is still open -- read the error "
              f"in it.")
        return
    if not flag_present(session_id):
        print(f"    (no permission prompt within {wave_timeout:g}s -- Claude "
              f"may still be booting. Check the new window.)")
        return

    print("    >>> Claude is asking. Squid is waving, the banner fired. <<<")
    print("    >>> Answer it in the new Terminal window when you're ready. <<<")
    print("    (heads up: the watcher snoozes a wave after "
          "_CLAUDE_SESSION_SNOOZE_SEC = 120s -- shoot this beat inside two "
          "minutes or she'll quietly stand down mid-take)")
    if wait_until(lambda: not flag_present(session_id), answer_timeout,
                  "waiting for your answer", countdown):
        print("    answered -- flag cleared, Squid stands down.")
    else:
        print(f"    (still waiting after {answer_timeout:g}s -- moving on; "
              f"the prompt is still live in that window.)")


def run_sequence(beats: list[Beat], hold_sec: float, notify: bool,
                 countdown: bool, real_prompt: bool = True,
                 ask_prompt: str = DEFAULT_ASK_PROMPT,
                 bounds: str | None = None, wave_timeout: float = 150.0,
                 answer_timeout: float = 180.0) -> None:
    for i, (state, caption, wants_notify) in enumerate(beats, 1):
        if wants_notify and real_prompt:
            print(f"\n[{i}/{len(beats)}] {state}  [REAL Claude prompt]")
            print(f"    \u201c{caption}\u201d")
            run_real_ask_beat(ask_prompt, bounds, wave_timeout,
                              answer_timeout, countdown)
            continue

        set_state(state)
        flagged = False
        if wants_notify and notify:
            flagged = raise_attention_flag()

        bell = "  [+ macOS notification]" if flagged else ""
        print(f"\n[{i}/{len(beats)}] {state}{bell}")
        print(f"    “{caption}”")

        # Wait for the watcher to actually publish the override before we
        # start the beat's clock -- otherwise short beats get eaten by tick
        # latency and the state is on screen for less time than requested.
        landed, latency = wait_for_state(state)
        if not landed:
            print(f"    (warning: state.json still says "
                  f"{read_live_state()!r} after {latency:.1f}s -- "
                  f"is the watcher running? `squid status`)")

        hold(max(MIN_BEAT_SEC, hold_sec - latency), state, countdown)

        if flagged:
            lower_attention_flag()


def print_shot_list(beats: list[Beat], hold_sec: float,
                    real_prompt: bool) -> None:
    """The shooting script: what's on screen, what you say, how long it runs.

    Prints only -- no files written, no state forced, nothing launched. Safe
    to run mid-take to remind yourself what's coming.
    """
    print("=" * 68)
    print("  SQUID DEMO -- SHOOTING SCRIPT")
    print("=" * 68)
    print("\n  BEFORE YOU ROLL")
    print("    1. `squid status` -- make sure she's running")
    print("    2. Park her somewhere the screen recorder will catch her")
    print("    3. Quit or silence any OTHER Claude Code session: a second")
    print("       one can fight for the state between beats")
    print("    4. Do Not Disturb OFF -- the attention beat needs the banner")

    running = 0.0
    print("\n  BEATS")
    for i, (state, caption, is_ask) in enumerate(beats, 1):
        if is_ask and real_prompt:
            dur = "~60s + your answer"
            action = ("a new Terminal window opens, Claude boots, then asks "
                      "permission to run ./deploy.sh --production. Squid "
                      "waves and the macOS banner fires. Answer it on camera.")
        else:
            dur = f"{hold_sec:g}s"
            action = "held on screen; nothing for you to do"
            if is_ask:
                action = "synthetic flag: she waves, banner fires, no question"
                running += hold_sec
            else:
                running += hold_sec
        print(f"\n    [{i}] {state.upper()}   ({dur})")
        print(f"        say:  \u201c{caption}\u201d")
        print(f"        note: {action}")

    ask_beats = sum(1 for _, _, a in beats if a)
    print(f"\n  RUNTIME  ~{running:.0f}s of held beats"
          + (f", plus the attention beat ({ask_beats} of them) which runs at "
             f"your pace" if real_prompt and ask_beats else ""))
    print("\n  SAFETY")
    print("    - Nothing outside ~/.squid-pet/demo-sandbox is touched.")
    print("    - The command Claude asks to run is a prop: deploy.sh is nine")
    print("      echo lines. Saying yes on camera changes nothing.")
    print("    - force_state is cleared on every exit path, Ctrl-C included.")
    print("    - `--clear` is the panic button if a take goes sideways.")
    print("=" * 68)


# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(
        description="Drive Squid through her states for demo recording.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("USAGE\n-----\n")[-1],
    )
    p.add_argument("--hold", type=float, default=8.0,
                   help="seconds to hold each state (default: 8)")
    p.add_argument("--lead-in", type=float, default=3.0,
                   help="seconds before the first beat, to get your hands off "
                        "the keyboard and start recording (default: 3)")
    p.add_argument("--states", type=str, default="",
                   help="comma-separated custom sequence, e.g. "
                        "working,thinking,approval_needed,celebrating")
    p.add_argument("--shot-list", action="store_true",
                   help="print the shooting script (beats, narration, timings) "
                        "and exit without touching anything")
    p.add_argument("--loop", action="store_true",
                   help="repeat the sequence until Ctrl-C")
    p.add_argument("--no-notify", action="store_true",
                   help="don't fire the real macOS notification on the "
                        "approval beat (visual flag-wave only)")
    p.add_argument("--no-countdown", action="store_true",
                   help="don't print the per-beat countdown timer")
    p.add_argument("--fake-prompt", action="store_true",
                   help="DON'T launch a real Claude session for the attention "
                        "beat -- just drop a synthetic flag. The wave and the "
                        "banner still fire, but there's no question on screen "
                        "to answer. Use this for a quick visuals-only take; "
                        "the default is the real thing.")
    p.add_argument("--ask-prompt", type=str, default=DEFAULT_ASK_PROMPT,
                   help="what to ask that real Claude session to do "
                        "(must be something that needs your permission)")
    p.add_argument("--terminal-bounds", type=str, default="",
                   help="place the new Terminal window, e.g. "
                        "'100,100,1200,800' (left,top,right,bottom)")
    p.add_argument("--wave-timeout", type=float, default=150.0,
                   help="how long to wait for Claude's question to appear. "
                        "Measured live: CLI boot + first turn is ~55s, so "
                        "don't go below ~90 (default: 150)")
    p.add_argument("--answer-timeout", type=float, default=180.0,
                   help="how long to wait for you to answer it before moving "
                        "on (default: 180)")
    p.add_argument("--clear", action="store_true",
                   help="remove force_state + any leftover demo flags and exit")
    args = p.parse_args()

    if args.clear:
        clear_all_demo_flags()
        return 0

    if args.states:
        names = [s.strip() for s in args.states.split(",") if s.strip()]
        unknown = [n for n in names if n not in KNOWN_STATES]
        if unknown:
            print(f"unknown state(s): {', '.join(unknown)}", file=sys.stderr)
            print(f"known: {', '.join(sorted(KNOWN_STATES))}", file=sys.stderr)
            return 2
        beats = [(n, n, n == "approval_needed") for n in names]
    else:
        beats = list(PITCH_BEATS)

    if args.shot_list:
        print_shot_list(beats, args.hold, not args.fake_prompt)
        return 0

    if not watcher_running():
        print("!! squid-pet doesn't look like it's running "
              "(no live pid in ~/.squid-pet/pid).")
        print("   Start it with `squid start`, then re-run this.")
        return 1

    _install_signal_handlers()
    atexit.register(cleanup, False)

    print("=" * 62)
    print("  SQUID DEMO DRIVER")
    print(f"  {len(beats)} beats x {args.hold:g}s"
          + ("  (looping -- Ctrl-C to stop)" if args.loop else ""))
    print("  Ctrl-C at any time; the override always gets cleared.")
    print("=" * 62)

    if args.lead_in > 0:
        hold(args.lead_in, "starting in", not args.no_countdown)

    try:
        while True:
            run_sequence(beats, hold_sec=args.hold,
                         notify=not args.no_notify,
                         countdown=not args.no_countdown,
                         real_prompt=not args.fake_prompt,
                         ask_prompt=args.ask_prompt,
                         bounds=args.terminal_bounds or None,
                         wave_timeout=args.wave_timeout,
                         answer_timeout=args.answer_timeout)
            if not args.loop:
                break
            print("\n--- looping ---")
    finally:
        cleanup()

    print("\nthat's a wrap \U0001f991")
    return 0


if __name__ == "__main__":
    sys.exit(main())
