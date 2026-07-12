"""Goal-gate e2e for TVA-41: the engine, not the DM model, drives the
checkpoint cadence.

Eval run 20260711-205738 showed weaker models never call `checkpoint` on
their own (Sonnet: zero checkpoints across ~74 events; Haiku: none) — crash
insurance can't depend on model discipline. `registry.execute` now fires an
auto-checkpoint once ~20 events have accumulated since the last one (FC-7:
"auto mini-recap checkpoint every ~20 events"), reusing the existing
`checkpoint` command/storage path so it is audited exactly like every other
mutation.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign

SEED = 999


def _roll(ctx, n=1):
    return registry.execute(
        "roll_dice", ctx, count=n, sides=6, reason="auto-checkpoint test filler",
    )


def test_auto_checkpoint_fires_after_n_events(tmp_path, rules_path):
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="auto-ckpt", name="Auto Checkpoint",
        death_mode="narrative", skeleton={"premise": "test"}, seed=SEED,
    )
    try:
        # bootstrap already appended 1 event (create_campaign); fire 20 more
        # mutating commands and confirm exactly one auto-checkpoint appears,
        # with no double-firing and no recursion.
        results = [_roll(ctx) for _ in range(20)]
        assert all(r.ok for r in results)

        recaps = ctx.store.recaps()
        auto_recaps = [r for r in recaps if r["content"].startswith("[auto]")]
        assert len(auto_recaps) == 1, f"expected exactly one auto-checkpoint, got {recaps}"

        # The auto-checkpoint landed in the event log exactly like any other
        # command — one row, command='checkpoint', gm_only, ok=True.
        checkpoint_events = [
            e for e in ctx.store.events_tail(100) if e["command"] == "checkpoint"
        ]
        assert len(checkpoint_events) == 1
        assert checkpoint_events[0]["ok"] is True

        # Mechanical content: scene/clock/party/combat, not narrative prose.
        content = auto_recaps[0]["content"]
        assert "Day" in content
        assert "Kira" not in content  # no party created in this campaign yet

        # No recursion: crossing the threshold fired exactly one checkpoint
        # (asserted above); the counter is reset and nowhere near firing again.
        assert ctx.store.events_since_last_checkpoint() < 20
    finally:
        ctx.store.close()


def test_auto_checkpoint_resets_after_manual_checkpoint(tmp_path, rules_path):
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="auto-ckpt-manual", name="Manual Reset",
        death_mode="narrative", skeleton={"premise": "test"}, seed=SEED,
    )
    try:
        # 10 events, well under the threshold — manual checkpoint anyway.
        for _ in range(10):
            _roll(ctx)
        manual = registry.execute("checkpoint", ctx, content="Party makes camp for the night.")
        assert manual.ok

        # 15 more events: not enough to cross the threshold from the manual
        # checkpoint's reset point (would have been 25 from campaign start).
        for _ in range(15):
            _roll(ctx)

        recaps = ctx.store.recaps()
        auto_recaps = [r for r in recaps if r["content"].startswith("[auto]")]
        assert auto_recaps == [], (
            "manual checkpoint must reset the auto-checkpoint counter"
        )
        manual_recaps = [r for r in recaps if not r["content"].startswith("[auto]")]
        assert len(manual_recaps) == 1
    finally:
        ctx.store.close()


def test_auto_checkpoint_does_not_recurse_on_checkpoint_command(tmp_path, rules_path):
    """A manual checkpoint call must never itself trigger an auto-checkpoint,
    even if (pathologically) it were called after 20+ events without a prior
    reset — the checkpoint command itself is excluded from triggering."""
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="auto-ckpt-recursion", name="Recursion Guard",
        death_mode="narrative", skeleton={"premise": "test"}, seed=SEED,
    )
    try:
        for _ in range(25):
            _roll(ctx)
        # crossing the threshold already fired one auto-checkpoint; a manual
        # checkpoint call right after must not chain into a second one.
        manual = registry.execute("checkpoint", ctx, content="DM checkpoint at a lull.")
        assert manual.ok

        checkpoint_events = [
            e for e in ctx.store.events_tail(200) if e["command"] == "checkpoint"
        ]
        assert len(checkpoint_events) == 2  # one auto, one manual — no cascade
    finally:
        ctx.store.close()
