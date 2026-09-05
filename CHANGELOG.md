# Changelog

All notable changes to Squid are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-09-05

First public release. Squid is a tiny macOS desktop companion that shows what
your AI coding agents are doing — thinking, working, finishing, or waiting for
you — without you having to watch the terminal.

### Added

- **Ambient state sprite.** An always-on-top squid that reflects the live state
  of your agent: thinking, working, done, needs-you, and away.
- **Approval-needed wave.** Rides Claude Code's official hook system
  (`Notification` / `UserPromptSubmit` / `SessionEnd`) rather than scraping
  processes, so a blocked session is detected exactly and per-session. Squid
  waves and fires a macOS notification when a session is waiting on you.
- **Double-click to the waiting window.** Double-clicking a waving Squid focuses
  the exact terminal window that's blocked, and names which session it is — the
  feature that pays for itself when several sessions run at once.
- **Codex support**, with git commits and IDE activity feeding the same
  fixed-priority state machine as fallback signals.
- **`thinking` vs `working` detection** from session-transcript mtime weighed
  against live shell children and recent project-file writes.
- **Idle life.** Fifteen seconds of dozing before she drops off to sleep, an
  idle squash-and-stretch flourish, and small-talk bubbles that speak only when
  she's genuinely idle.
- **State-reason bubbles** that explain *why* she changed state instead of just
  emoting.
- **`squid doctor`** — a six-check self-diagnostic — and **`squid why`**, which
  reports which detector fired and why she's in her current state.
- **Clean install / uninstall.** One-command `./install.sh`; `squid uninstall`
  keeps your settings and `squid uninstall --yes --all` removes every trace,
  including the LaunchAgent and the launcher on your `PATH`.
- **Privacy by construction.** Reads filesystem metadata (mtimes), process names
  and CPU percentages only — never file contents, no network calls, no
  telemetry. Every detector is individually toggleable in
  `~/.squid-pet/settings.json`, live, with no restart. Full disclosure in
  [`docs/PRIVACY.md`](docs/PRIVACY.md).
- **Safe startup verification** and a documented startup-integrity path
  (see [`docs/STARTUP_SAFETY.md`](docs/STARTUP_SAFETY.md)).
- **789 tests**, the large majority on the state machine rather than the
  rendering.

### Notes

- macOS 12+ only (the always-on-top window layer is built on pyobjc). Apple
  Silicon and Intel.
- Built on and off since 12 June 2026 across roughly 11 active weeks.

[0.3.0]: https://github.com/sirshecomesthisway/squid-pet/releases/tag/v0.3.0
