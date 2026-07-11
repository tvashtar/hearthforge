"""Active-effect lifecycle wiring: expiry on rest / clock advancement and
concentration linkage (TVA-20)."""

import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


def _apply(ctx, name, **fields):
    result = registry.execute(
        "dm_ruling", ctx, description=f"apply {name}", rationale="testing",
        effects=[{"op": "apply_effect", "target": "Kira", "name": name, **fields}],
    )
    assert result.ok, result.refusal


def _kira_effect_names(ctx):
    kira = ctx.store.get_character("Kira")
    return [e["name"] for e in ctx.store.active_effects_for(kira["id"])]


def test_short_rest_expires_only_short_rest_effects(ctx):
    _apply(ctx, "heroism", expires_on_rest="short")
    _apply(ctx, "mage armor", expires_on_rest="long")
    result = registry.execute("rest", ctx, kind="short")
    assert result.ok, result.refusal
    assert _kira_effect_names(ctx) == ["mage armor"]
    assert result.data["effects_expired"] == ["heroism"]


def test_long_rest_expires_rest_clock_and_concentration_effects(ctx):
    _apply(ctx, "heroism", expires_on_rest="short")
    _apply(ctx, "mage armor", expires_on_rest="long")
    _apply(ctx, "shield of faith", duration_minutes=60)  # < the 8h a long rest takes
    _apply(ctx, "bless", concentration=True, concentration_by="Brother Aldric")
    result = registry.execute("rest", ctx, kind="long")
    assert result.ok, result.refusal
    assert _kira_effect_names(ctx) == []


def test_travel_expires_clock_effects(ctx):
    registry.execute("create_location", ctx, slug="keep", name="Keep", description="x")
    _apply(ctx, "shield of faith", duration_minutes=60)
    _apply(ctx, "mage armor", duration_minutes=480)
    result = registry.execute("travel", ctx, destination_slug="keep", hours=1)
    assert result.ok, result.refusal
    assert result.data["effects_expired"] == ["shield of faith"]
    assert _kira_effect_names(ctx) == ["mage armor"]


def test_break_concentration_clears_linked_effects(ctx):
    # Aldric concentrates on bless; the ruling records its mechanical rider.
    cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                            spell_slug="bless", targets=["Kira"])
    assert cast.ok and cast.data["needs_ruling"]
    _apply(ctx, "bless", concentration=True, concentration_by="Brother Aldric")
    result = registry.execute("break_concentration", ctx, character="Brother Aldric")
    assert result.ok, result.refusal
    assert _kira_effect_names(ctx) == []


def test_new_concentration_spell_clears_replaced_spells_effects(ctx):
    cast = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                            spell_slug="bless", targets=["Kira"])
    assert cast.ok, cast.refusal
    _apply(ctx, "bless", concentration=True, concentration_by="Brother Aldric")
    replaced = registry.execute("cast_spell", ctx, caster="Brother Aldric",
                                spell_slug="hold-person", targets=["Kira"])
    assert replaced.ok, replaced.refusal
    assert replaced.data["concentration_replaced"] == "bless"
    assert _kira_effect_names(ctx) == []


def test_expired_effect_is_ignored_by_attack_ac_even_before_cleanup(ctx):
    """Consultation must filter by the clock, not trust cleanup hooks."""
    kira = ctx.store.get_character("Kira")
    _apply(ctx, "mage armor", mechanics={"ac_override": 20}, duration_minutes=30)
    # Advance the clock past the expiry without going through a hook.
    ctx.store.update_world_clock(minutes=520)
    ctx.store.conn.commit()
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    goblin = next(c["key"] for c in ctx.store.combat()["combatants"]
                  if c["kind"] == "monster")
    while ctx.store.combat()["combatants"][ctx.store.combat()["turn_index"]]["key"] != goblin:
        registry.execute("next_turn", ctx)
    registry.execute("engage", ctx, combatant=goblin, target="Kira")
    result = registry.execute("attack", ctx, attacker=goblin, target="Kira",
                              attack_name="Scimitar")
    assert result.ok, result.refusal
    # Kira's real AC (16), not the stale override (20).
    assert result.data["attack_roll"]["target_ac"] == kira["ac"]
