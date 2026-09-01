#!/usr/bin/env python3
"""demo_live.py -- set up a real Claude Code task that walks Squid through
working -> thinking -> approval_needed -> celebrating, for the demo video.

Nothing here forces a state. You paste one prompt into a normal Claude Code
session and Squid reacts to genuine activity, the same way she does on any
ordinary day:

  thinking          Claude reasons before touching a tool (streaming, no
                    shell child, no file write) -> watcher's branch-4 cascade
  working           Claude writes RELEASE.md and runs build.py (file write +
                    live shell child)
  approval_needed   Claude asks permission to run ./deploy.sh --production.
                    A REAL Claude Code permission prompt on your screen; the
                    Notification hook fires, Squid waves, banner + sound.
  celebrating       Claude runs squid_task_complete.py. This one is NOT
                    optional: watcher.py's CELEBRATING branch fires on the
                    explicit claude_task_complete marker (or a git commit),
                    never on "Claude stopped" alone -- a bare Stop only gets
                    you GROOVING. That's why the prompt ends with it.

The demo project is disposable and nothing in it can damage anything:
deploy.sh is echo lines, build.py writes one file inside its own folder.

The allowlist is the trick that makes the take clean: everything EXCEPT
./deploy.sh is pre-approved in the project's own settings.local.json, so
you get exactly one permission prompt, on the beat you want it.

    python3 scripts/demo_live.py          # set up + copy the prompt
    python3 scripts/demo_live.py --show   # just print the prompt again

Then, and this flag is REQUIRED:

    cd ~/squid-demo && claude --permission-mode default

autoMode is enabled globally on this machine (~/.claude/settings.json), and
it auto-approves tool calls -- inconsistently, since it escalates to asking
only when it judges an action sensitive. Confirmed live: one take got two
permission prompts, the next got none at all and ran ./deploy.sh unasked.
--permission-mode default pins the session to normal gating so the approval
beat happens every time.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

DEFAULT_DIR = Path.home() / "squid-demo"
TASK_COMPLETE = Path.home() / "Projects" / "squid-pet" / "scripts" / "squid_task_complete.py"

README = """# checkout-service

A small payments service. Demo project for the squid-pet video --
everything in here is disposable.

## Release process
1. Write the release notes
2. Build the bundle
3. Deploy to production
"""

# Real work, and slow enough on purpose: Squid needs a few seconds of live
# shell child to settle into WORKING and be legible on camera.
BUILD_PY = '''#!/usr/bin/env python3
"""Builds the release bundle. Writes one file, inside this folder."""
import time
from pathlib import Path

STEPS = [
    "resolving dependencies",
    "compiling modules",
    "bundling assets",
    "optimizing output",
    "writing manifest",
]

out = Path(__file__).parent / "build"
out.mkdir(exist_ok=True)

for step in STEPS:
    print(f"  {step} ...", flush=True)
    time.sleep(1.6)

(out / "bundle.js").write_text("// checkout-service release bundle\\n")
print("\\nbuild complete -> build/bundle.js")
'''

# The prop. Every line is an echo -- approving this on camera does nothing.
DEPLOY_SH = """#!/usr/bin/env bash
# Demo prop. Does nothing, deliberately and verifiably.
echo "==> deploying checkout-service to production"
sleep 1
echo "==> uploading bundle ........ ok"
sleep 1
echo "==> running migrations ...... ok"
sleep 1
echo "==> health check ............ ok"
echo "==> deploy complete"
exit 0
"""

PROMPT = """Ship the v2.1 release of this service. Work through these steps in order, and do NOT run any command that isn't one of these five -- no exploring, no ls, no git, no reading files first:

1. First, think it through before you touch anything: in 3-4 sentences, \
explain what a release checklist for this service needs and in what order. \
Don't use any tools yet -- just reason it out.

2. Write those steps into RELEASE.md as a short checklist. Use the Write tool for this, not a shell heredoc.

3. Build the release bundle: run `python3 build.py`.

4. Now ship it: run exactly `./deploy.sh --production`.

5. Once the deploy succeeds, mark the whole task done by running:
   python3 {task_complete}
"""


def build_project(target: Path) -> None:
    """Create the demo project. Everything it writes lives under `target`."""
    target.mkdir(parents=True, exist_ok=True)
    (target / "README.md").write_text(README)
    (target / "build.py").write_text(BUILD_PY)
    deploy = target / "deploy.sh"
    deploy.write_text(DEPLOY_SH)
    deploy.chmod(0o755)

    # Pre-approve everything except the deploy. This is what turns a noisy
    # session (a prompt per tool call) into one clean beat: Claude writes,
    # builds and marks-complete without interrupting you, and stops exactly
    # once -- at ./deploy.sh --production, on camera.
    claude_dir = target / ".claude"
    claude_dir.mkdir(exist_ok=True)
    (claude_dir / "settings.local.json").write_text(json.dumps({
        "permissions": {
            "allow": [
                "Read",
                "Write",
                "Edit",
                # Both the exact form and the prefix form: Claude may
                # append args or run it via a slightly different invocation,
                # and a near-miss here means an extra permission prompt in
                # the middle of the take.
                "Bash(python3 build.py)",
                "Bash(python3 build.py:*)",
                f"Bash(python3 {TASK_COMPLETE})",
                f"Bash(python3 {TASK_COMPLETE}:*)",
                # Harmless read-only fallbacks. The prompt tells Claude
                # not to explore, but if it does anyway these keep the extra
                # call from throwing a permission prompt in the middle of the
                # take -- the ONLY prompt that should appear is the deploy.
                "Bash(ls:*)",
                "Bash(cat:*)",
                "Bash(echo:*)",
                "Bash(head:*)",
                "Bash(wc:*)",
                "Bash(pwd)",
                "Bash(git status:*)",
            ],
            "deny": [],
        }
    }, indent=2) + "\n")

    (target / "PROMPT.txt").write_text(prompt_text())


def prompt_text() -> str:
    return PROMPT.format(task_complete=TASK_COMPLETE)


def copy_to_clipboard(text: str) -> bool:
    try:
        subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=5)
        return True
    except Exception:
        return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--dir", type=Path, default=DEFAULT_DIR,
                   help=f"where to build the demo project (default: {DEFAULT_DIR})")
    p.add_argument("--show", action="store_true",
                   help="print the prompt again without rebuilding anything")
    args = p.parse_args()

    if args.show:
        print(prompt_text())
        copy_to_clipboard(prompt_text())
        return 0

    if not TASK_COMPLETE.exists():
        print(f"!! can't find {TASK_COMPLETE} -- the celebrate beat needs it.",
              file=sys.stderr)
        return 1

    build_project(args.dir)
    copied = copy_to_clipboard(prompt_text())

    print("=" * 66)
    print("  SQUID DEMO -- REAL CLAUDE CODE TAKE")
    print("=" * 66)
    print(f"\n  Demo project built at: {args.dir}")
    print("    README.md  build.py  deploy.sh  .claude/settings.local.json")
    print("\n  HOW TO RECORD")
    print(f"    1. cd {args.dir}")
    print("    2. claude --permission-mode default")
    print("       ^^^^^^^^^^^^^^^^^^^^^^^^^^ REQUIRED. Not optional.")
    print("    3. Start your screen recording")
    print("    4. Paste the prompt"
          + ("  (already on your clipboard)" if copied
             else "  (below -- clipboard copy failed)"))
    print("    5. When Claude asks about ./deploy.sh --production, say YES")
    print("\n  WHAT SQUID DOES, AND WHY")
    print("    step 1  -> THINKING         Claude reasons, no tools yet")
    print("    step 2  -> WORKING          RELEASE.md written")
    print("    step 3  -> WORKING          build.py runs ~8s in a live shell")
    print("    step 4  -> APPROVAL_NEEDED  real permission prompt + banner")
    print("    step 5  -> CELEBRATING      task-complete marker")
    print("\n  WHY --permission-mode default IS REQUIRED")
    print("    This machine has autoMode enabled in ~/.claude/settings.json,")
    print("    which auto-approves tool calls -- so plain `claude` may run the")
    print("    deploy without ever asking, and the approval beat silently")
    print("    never happens. Worse, it is not consistent: auto mode escalates")
    print("    to asking when it judges an action sensitive, and its config")
    print("    flags anything matching 'production' -- so the same prompt asks")
    print("    on one take and not the next. --permission-mode default pins")
    print("    the session to normal gating and makes the beat deterministic.")
    print("\n  NOTES")
    print("    - DO A DRY LAUNCH FIRST: run `claude` in this folder once and")
    print("      clear the 'do you trust the files in this folder?' dialog,")
    print("      then /exit. It only appears on first visit -- you don't want")
    print("      it on camera. Re-launch for the real take.")
    print("    - Only step 4 stops to ask. Everything else is pre-approved")
    print("      in the project's settings.local.json, so the take is clean.")
    print("    - Say YES to the deploy: it's a prop made of echo lines.")
    print("    - Answering NO fires no hook, so she'd keep waving until the")
    print("      120s snooze. Say yes, or you lose the celebrate beat.")
    print("    - Quit other Claude Code sessions first; a second one")
    print("      competes for Squid's state.")
    print("=" * 66)
    if not copied:
        print("\n--- PROMPT ---\n")
        print(prompt_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
