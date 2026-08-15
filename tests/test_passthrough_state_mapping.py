"""Regression test for PassthroughController.set_state()'s
approval_needed -> attention_needed sprite remap.

Found during a sprite audit (2026-08-15): frontend/index.html's
spriteUrl() maps the backend state "approval_needed" to
sprites/attention_needed*.png (there is no approval_needed.png), but
PassthroughController.set_state() didn't mirror that remap -- it used
the raw backend state as the mask-lookup key, which is never a key in
self._masks (built from PNG filename stems), so _current_state silently
kept whichever mask was active before the flag-wave started. Click
hit-testing then ran against the wrong sprite's alpha channel for the
entire duration of the "your turn" animation.
"""
from __future__ import annotations

from squid_pet.passthrough import PassthroughController


def _make_controller(masks):
    """Build a PassthroughController without touching the real sprite
    files on disk or spinning up its background thread."""
    ctrl = PassthroughController.__new__(PassthroughController)
    ctrl._get_ns_window = lambda: None
    ctrl._masks = masks
    ctrl._current_state = "idle"
    ctrl._current_edge = ""
    ctrl._paused = False
    ctrl._hidden = False
    import threading
    ctrl._stop = threading.Event()
    ctrl._lock = threading.Lock()
    ctrl._last_ignore = None
    return ctrl


def test_approval_needed_maps_to_attention_needed_mask():
    ctrl = _make_controller({
        "idle": object(), "working": object(), "attention_needed": object(),
    })
    ctrl.set_state("working")
    assert ctrl._current_state == "working"

    ctrl.set_state("approval_needed")
    assert ctrl._current_state == "attention_needed"


def test_normal_states_pass_through_unchanged():
    ctrl = _make_controller({"idle": object(), "thinking": object()})
    ctrl.set_state("thinking")
    assert ctrl._current_state == "thinking"


def test_unknown_state_does_not_update_current_state():
    """Guard preserved: a state with no matching mask leaves the last
    good mask in place rather than crashing or clearing it."""
    ctrl = _make_controller({"idle": object()})
    ctrl.set_state("idle")
    ctrl.set_state("some_future_state_with_no_sprite")
    assert ctrl._current_state == "idle"


def test_real_sprite_masks_include_attention_needed_not_approval_needed():
    """Sanity check against the actual bundled sprite set -- guards
    against this test suite silently drifting from reality if sprites
    are ever renamed."""
    from squid_pet.passthrough import load_alpha_masks
    masks = load_alpha_masks()
    assert "attention_needed" in masks
    assert "approval_needed" not in masks
