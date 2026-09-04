# Contributing to Squid

Thanks for looking. Squid is early, and the most useful thing you can
send is a bug report from a machine that isn't mine — see
[Reporting a bug](#reporting-a-bug).

If you want to write code, the contribution I most want is **a detector
for an agent Squid doesn't watch yet**. That's a self-contained change:
one class, one test file, one line in `build_detectors()`.

---

## Setup

```bash
git clone https://github.com/sirshecomesthisway/squid-pet.git
cd squid-pet
./install.sh          # uv venv + deps + LaunchAgent + starts her
```

macOS 12+ and Python 3.11+. The installer is idempotent — re-run it to
update in place.

To work on the code without the LaunchAgent in the way:

```bash
squid stop                       # or: launchctl bootout gui/$UID/com.pink.squid-pet
.venv/bin/python -m squid_pet    # run in the foreground, Ctrl-C to stop
.venv/bin/python -m squid_pet --watcher-only   # no window, detection only
```

## Tests

```bash
.venv/bin/pytest
```

Every detector is tested in isolation with its I/O dependency-injected,
so the suite never touches psutil, the filesystem, or `ioreg` for real.
Keep it that way — a test that reads your actual `~/.claude/` will pass
on your machine and fail on everyone else's.

`squid doctor` runs a separate 6-check end-to-end self-test against the
*installed* copy. That one does touch the real system.

---

## Adding a detector for a new agent

A detector answers three questions about one tool, and reports what it
saw. That's the whole contract (`src/squid_pet/detectors.py`):

```python
@runtime_checkable
class Detector(Protocol):
    name: str
    enabled: bool

    def is_busy(self, now: float) -> bool: ...
    def is_celebrating(self, now: float) -> bool: ...
    def is_grooving(self, now: float) -> bool: ...
    def diagnostic(self) -> dict: ...
```

`diagnostic()` is not optional and not decoration — it's what `squid why`
prints. If a detector can't explain why it fired, nobody can debug it on
a machine you don't own.

Steps:

1. Write the class in `detectors.py`, next to `ClaudeCodeDetector`.
2. Add it to `build_detectors()` with an `enabled=s.get("your_agent", True)`
   flag, so users can switch it off in `~/.squid-pet/settings.json`
   without a restart.
3. Add `tests/test_detectors_<name>.py`. Inject every source of I/O —
   look at `test_detectors_codex.py` for the pattern.
4. Add a row to the README's **State** table describing what triggers it.

### Two things worth knowing before you start

**Prefer an official hook over process scraping.** Squid's approval wave
doesn't guess from CPU or silence — it reads a flag written by Claude
Code's own `Notification` hook (`scripts/claude_pet_hook.py`). Where the
agent you're adding offers a hook, event, or status file, use it. Guessing
from process state is the fallback, not the default.

**Metadata only.** Detectors may read file *mtimes*, process names, and
CPU percentages. They must not read file contents, and they must not make
network calls. This isn't a style preference — it's the promise in
[`docs/PRIVACY.md`](docs/PRIVACY.md), and a PR that breaks it can't be
merged even if the feature is good.

---

## Reporting a bug

Open an issue with the **Bug report** template. It asks for `squid doctor`
output, your macOS version, and your chip — please fill those in. Squid is
a state machine reacting to timing on your machine, and without them a
report usually isn't reproducible.

The failures I most expect and least can test:

- several concurrent Claude Code / Codex sessions
- Intel Macs
- macOS 12 and 13
- Codex-only workflows

## Pull requests

- Branch off `main`, keep the change focused on one thing.
- `.venv/bin/pytest` passes before you open it.
- Say what you observed, not just what you changed — for a detector,
  paste the `squid why` output showing it firing.

Bug fixes and new detectors are welcome without asking first. For
anything that changes the state cascade or adds a new state, open an
issue to talk about it first — the priority order in
`StateMachine.compute()` is load-bearing and easy to break subtly.

## License

MIT. By contributing you agree your work ships under it.
