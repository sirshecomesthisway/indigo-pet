# Indigo Pet — Image Generation Prompts (v2)

**Locked design:** bright pink chibi pixel octopus, round bulbous head, large
googly eyes (cream sclera + big black pupil, no shine), 6-8 visible tentacles
fanning out at the bottom with cute little curls on the outer ones, cream/off-
white solid background, minimalist pixel art style.

## Style anchor — PASTE THIS WITH EVERY PROMPT + attach idle.png as reference

> Pixel art, EXACT same character as the reference image. Bright bubblegum pink
> chibi octopus with a round bulbous head, large googly cartoon eyes (cream
> sclera with big solid black pupils, no shine), 6-8 visible tentacles fanning
> out at the bottom with cute curls on the outer ones. Minimalist style, solid
> cream/off-white background. Same color, same proportions, same pixel density
> as reference. Only change [what's described below].

---

## State 1: THINKING 🤔
> [STYLE ANCHOR] — Only change: eyes look up and to the right. A small white
> pixel-art thought-bubble cloud floats in the upper-right corner.

## State 2: WORKING 💻
> [STYLE ANCHOR] — Only change: sitting at a tiny pixel-art laptop. The front
> two tentacles rest on the keyboard like she's typing. Small green code text
> visible on the laptop screen.

## State 3: GROOVING 🎧
> [STYLE ANCHOR] — Only change: tiny cyan pixel-art headphones over the top of
> the head. 2-3 small yellow music notes floating around. Tentacles wave
> outward to the sides.

## State 4: CELEBRATING 🎉
> [STYLE ANCHOR] — Only change: eyes wide and sparkling (tiny star shapes
> inside the pupils), small open happy mouth. Yellow pixel-art star sparkles
> and confetti pixels scattered around the character.

## State 5: SLEEPING 😴
> [STYLE ANCHOR] — Only change: eyes closed (drawn as small curved lines), a
> trail of small purple "Z" letters floating up to the upper-right. Body posed
> slightly lower/relaxed, tentacles limp.

## State 6: CONCERNED ⚠️
> [STYLE ANCHOR] — Only change: a small red pixel-art exclamation mark "!"
> floating directly above the head. Eyes wide open with worried "raised
> eyebrow" pixels above them. Tentacles tucked in slightly.

---

## Tips for consistency

- **ChatGPT / DALL-E 3**: Upload idle.png first, then paste style anchor + one
  state prompt. If output drifts, say "match reference exactly, only change X."
- **Midjourney**: `--cref <idle.png url> --cw 100 --style raw --ar 1:1`
- Generate one state at a time so you can re-roll if it drifts.

## Save outputs to this folder as:

```
idle.png         ← the reference (already here)
thinking.png
working.png
grooving.png
celebrating.png
sleeping.png
concerned.png
```

Once all 7 are present, the pet auto-discovers them on next launch.
