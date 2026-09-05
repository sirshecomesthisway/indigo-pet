# Note on these records

`openspec/changes/` holds this project's decision history -- proposals,
designs, and task lists, both active and archived under `archive/`. They
are records of what was decided and why, written at the time.

**Pink-2026-09-05: these documents were de-identified.** Squid started
life inside a company, watching a third-party CLI coding agent that is
not part of this project and was never actually installed or run on this
machine. Names belonging to that agent, to the company, and to its
internal infrastructure were replaced. Nothing else was changed: the
decisions, the reasoning, the dates, and the outcomes are as originally
written.

## What the placeholders mean

| Placeholder | Refers to |
|---|---|
| `TPA`, `tpa`, `TPADetector`, `tpa_running`, `~/.tpa/` | The third-party CLI coding agent this project originally watched. All detection for it was removed 2026-08-22/27; the last dependency on it went 2026-09-04. Nothing in `src/` reads it today. |
| `internal-ghe.example.com` | A self-hosted GitHub Enterprise instance. No longer a remote of this repo. |
| `pypi.internal.example.com` | A self-hosted PyPI mirror, used as the package index in the installer work. |
| `internal GHE`, `internal artifactory`, `corporate VPN`, `corporate MDM self-service` | The corresponding internal services. |

The two idle-timer identifiers were also renamed to match the code as it
stands today rather than as it was written. The live names are
`agent_idle_seconds` in `watcher.PetState` and in
`frontend/index.html`, and `agent_idle` in prose.

## Reading these documents

Treat any `TPA` reference as historical. If you are looking for how
Squid detects agents **now**, read `openspec/specs/state-detection/spec.md`
and `src/squid_pet/detectors.py` -- the live detectors are `claude_code`,
`codex`, `git`, `ide`, and `terminal`.
