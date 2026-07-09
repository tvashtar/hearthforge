"""Goal Gate e2e: death saving throws resolve identically in both campaign
death modes; only the final disposition differs. Narrative maps a third failed
save to ``defeated`` (the campaign carries on); hardcore maps it to ``dead``
(and the dead PC no longer blocks a replacement PC — ``party()`` counts only
active + defeated). Both modes are driven through the same lethal script.
"""

from __future__ import annotations

from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign

SEED = 7

KIRA_KWARGS = dict(
    name="Kira", role="pc", class_slug="fighter", race_slug="human",
    abilities={"str": 15, "dex": 13, "con": 14, "int": 10, "wis": 12, "cha": 8},
    ac=16, proficiencies={"skills": ["athletics"]},
    attacks=[{"weapon": "longsword", "name": "longsword"}],
)


def _run_lethal_script(tmp_path, rules_path, death_mode):
    """Bootstrap a campaign in ``death_mode`` and run the identical lethal
    script: a fighter PC dropped to 0 HP + unconscious, then three failed death
    saves. Returns the open ctx and the third death_save result."""
    ctx = bootstrap_campaign(
        tmp_path / "campaigns", rules_path, slug="doom", name="Doom",
        death_mode=death_mode, skeleton={"premise": "a lethal trap"}, seed=SEED,
    )
    assert registry.execute("create_character", ctx, **KIRA_KWARGS).ok

    drop = registry.execute(
        "dm_ruling", ctx, description="A scythe trap opens Kira's throat.",
        rationale="test scripting",
        effects=[{"op": "adjust_hp", "target": "Kira", "delta": -1000},
                 {"op": "set_condition", "target": "Kira", "condition": "unconscious"}],
    )
    assert drop.ok, drop.refusal
    res = ctx.store.get_resources(ctx.store.get_character("Kira")["id"])
    assert res["hp"] == 0 and "unconscious" in res["conditions"]

    results = [
        registry.execute("death_save", ctx, character="Kira", player_value=5)
        for _ in range(3)
    ]
    for r in results:
        assert r.ok, r.refusal
    assert [r.data["failures"] for r in results] == [1, 2, 3]  # one failure each
    return ctx, results[-1]


def _assert_three_failures(ctx, last):
    """Death-save mechanics are mode-independent: three recorded failures."""
    assert last.data["failures"] == 3
    assert last.data["dead"] is True  # rules-level dying resolved
    saves = ctx.store.get_resources(ctx.store.get_character("Kira")["id"])["death_saves"]
    assert saves["failures"] == 3


def test_narrative_death_is_defeated_and_campaign_continues(tmp_path, rules_path):
    ctx, last = _run_lethal_script(tmp_path, rules_path, "narrative")
    try:
        kira = ctx.store.get_character("Kira")
        assert kira["status"] == "defeated"  # NOT dead in narrative mode
        _assert_three_failures(ctx, last)

        # The campaign still accepts commands after a narrative "death".
        cp = registry.execute("checkpoint", ctx, content="Kira falls; the party regroups.")
        assert cp.ok, cp.refusal
    finally:
        ctx.store.close()


def test_hardcore_death_is_dead_and_replacement_pc_allowed(tmp_path, rules_path):
    ctx, last = _run_lethal_script(tmp_path, rules_path, "hardcore")
    try:
        kira = ctx.store.get_character("Kira")
        assert kira["status"] == "dead"  # hardcore: the PC is gone for good
        _assert_three_failures(ctx, last)

        # The dead PC must NOT block a replacement: party() counts only
        # active + defeated, so the one-PC rule sees no living PC. A refusal
        # here would be a real engine bug in characters.py.
        replacement = registry.execute(
            "create_character", ctx, **{**KIRA_KWARGS, "name": "Bran"}
        )
        assert replacement.ok, replacement.refusal
        assert ctx.store.get_character("Bran")["status"] == "active"
    finally:
        ctx.store.close()
