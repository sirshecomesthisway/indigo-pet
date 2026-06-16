## 1. Frontend (`frontend/index.html`)

- [x] 1.1 Add CSS `@keyframes heart-rise-fade` (translateY, scale, opacity)
- [x] 1.2 Add `.heart` class with `position: absolute; pointer-events: none;`
       and reference the keyframe with `forwards` fill-mode
- [x] 1.3 Add tunable constants block (HEART_COUNT, HEART_RISE_PX, etc.)
- [x] 1.4 Implement `spawnHearts(centerX, centerY)` JS function
        - Bail early if `document.querySelectorAll('.heart').length >= HEART_MAX_LIVE`
        - Loop HEART_COUNT times, create div with random x-jitter
        - Set `animation-delay` based on index * HEART_STAGGER_MS
        - Append to sprite container; remove on `animationend`
- [x] 1.5 Call `spawnHearts()` in the existing poke setTimeout block,
        next to `api.poke?.()`, computing center from sprite bounding rect

## 2. Validation

- [x] 2.1 Single poke → 3 hearts rise + fade over ~1 second
- [x] 2.2 Rapid 5x poke → hearts stack visibly without performance hit
- [x] 2.3 Spam-poke 20x → cap holds at 12 live hearts, no console errors
- [x] 2.4 Dblclick → zero hearts (poke timeout cancelled by dblclick handler)
- [x] 2.5 Hearts do NOT block clicks: drag still works while hearts visible
- [x] 2.6 Squid drag while hearts mid-animation: hearts ride along correctly

## 3. Documentation

- [x] 3.1 Update `pink-pm/squid-pet.md` memory with the new capability +
        the tunable knob locations
- [x] 3.2 Update `pet-reactions` spec in this change folder if validation
        reveals deviations from the design
