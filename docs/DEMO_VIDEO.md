# Demo video — recording plan

Reusable shot list + scripts for a Squid demo video (a16z Sprint
application and future accelerator/social use). Footage is modular —
cut once, re-edit into different lengths as needed.

## Pre-production setup

**Tools**
- Recording: macOS built-in `Cmd+Shift+5` (fine), or CleanShot X /
  ScreenFlow for easier zoom/captions in post
- Editing: iMovie or CapCut (captions, music, multi-ratio export)
- Voiceover: record separately with a phone voice memo in a quiet room,
  sync in the editor — don't narrate live while screen-recording, sync
  issues aren't worth the hassle

**Clean up the screen before recording**
- Auto-hide the Dock, close unrelated apps/tabs
- Use a demo project folder (no real client/company paths visible)
- Dark terminal theme, larger font — reads better at a distance
- Record at native screen resolution; crop/zoom to Squid in the editor
  rather than framing tight during capture

**Force a specific mood on demand instead of waiting for it to happen
naturally** — squid-pet has a debug override built for exactly this:

```bash
echo "thinking"    > ~/.squid-pet/force_state   # record this clip
echo "working"     > ~/.squid-pet/force_state   # record this clip
echo "celebrating" > ~/.squid-pet/force_state   # record this clip
echo ""            > ~/.squid-pet/force_state   # clear override, resume live detection
```

**Exception — record `approval_needed` for real.** The macOS system
notification banner is OS-rendered chrome; faking it looks wrong, and
triggering it for real is easy (ask Claude Code to run a command that
isn't allow-listed yet, let the permission prompt fire). This is also
the single most "actually useful, not just cute" beat — worth a clean,
real take.

## Features to capture

| Priority | Feature | Why |
|---|---|---|
| Must | idle breathing | Establishes "she's alive" baseline |
| Must | working (yellow aura) vs thinking (cyan aura) | Core differentiator — must read as visibly different |
| Must | approval_needed wave + real OS notification | The one feature that's genuinely useful, not just decorative |
| Must | celebrating (confetti/bounce) | Emotional payoff beat |
| Nice | Claude Code AND Codex both triggering reactions | Backs up "watches Claude Code and Codex" from the one-liner |
| Nice | drag, right-click menu, sprint easter egg | Shows polish / personality |
| Optional | 30-second install | Good for developer/demo-day audiences, skip for general VC |

## Shot list

| # | Time | Shot | How to get it | VO / caption |
|---|---|---|---|---|
| 1 | 0:00–0:03 | Wide desktop shot, Squid idle in the corner | Real idle, no force needed | "Meet Squid." |
| 2 | 0:03–0:08 | Developer typing a prompt to Claude Code in terminal | Real | "She lives on your desktop, watching your AI coding agent." |
| 3 | 0:08–0:14 | Quick-cut or split screen: working (yellow) vs thinking (cyan) | `force_state`, one clip each, intercut with terminal footage | "When it's working, she's working. When it's thinking, she's thinking." |
| 4 | 0:14–0:20 | Claude Code hits a permission prompt → Squid waves + macOS notification pops | Real trigger, single take | "When it needs you, she waves — and pings you, even if you've wandered off." |
| 5 | 0:20–0:26 | Task/commit finishes → bounce + confetti | `force_state=celebrating`, overlay finished terminal output | "And when the job's done, she celebrates with you." |
| 6 | 0:26–0:32 | Quick montage: drag her, right-click menu, sprint easter egg | Real, fast cuts | "Poke her. Drag her. She's got moods of her own." |
| 7 | 0:32–0:38 | Codex CLI window, Squid reacting the same way | Real | (caption) "Works with Codex too." |
| 8 | 0:38–0:45 | End card: idle Squid + logo + one-liner | Static | "Squid — the spirit animal for the AI-native coder. So your agent finally feels alive." |

Runtime ≈ 45s.

## Scripts

**Version A — 45s narrated (application / formal use)**

> Meet Squid.
> She lives on your desktop, watching your AI coding agent — Claude Code, Codex, whatever you run.
> When it's working, she's working. When it's thinking, she's thinking.
> When it needs you, she waves — and pings you, even if you've wandered off.
> And when the job's done, she celebrates with you.
> Poke her. Drag her. She's got moods of her own.
> Squid — the spirit animal for the AI-native coder. So your agent finally feels alive.

**Version B — 15s no-VO (social / X / LinkedIn)**

Sequence: working→thinking quick-cut (2s) → approval wave + notification (4s) → celebrating (3s) → logo card (3s), captions instead of voiceover:

1. "She feels what your agent feels."
2. "Needs you? She waves."
3. "Done? She celebrates."
4. "Squid 🦑"

## Export notes

- Version A → 16:9, 1080p (application attachment, demo day)
- Version B → also cut a 9:16 vertical crop from the same footage for social
- Light sound effects on the approval-wave and celebrating beats (notification chime, confetti pop) add realism
- Keep the raw, uncut footage — re-cut to different lengths for other accelerator applications (YC, a16z speedrun proper) without re-shooting
