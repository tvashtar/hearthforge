"""TVA-20 acceptance: mage armor on a DEX +2, AC 12 wizard.

- the sheet shows AC 15 plus an active-effect row;
- an attack totaling 14 against her reports a miss with no manual ruling;
- the effect expires after 8 hours of world clock (or via end_effect),
  after which the same attack total hits again.

Everything flows through registry.execute (the only mutation path).
"""

import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")

MAGE_ARMOR = {"op": "apply_effect", "target": "Elowen", "name": "mage armor",
              "mechanics": {"ac_override": 15}, "duration_minutes": 480}


@pytest.fixture()
def elowen(ctx):
    result = registry.execute(
        "create_character", ctx, name="Elowen", role="companion",
        class_slug="wizard", race_slug="human",
        abilities={"str": 8, "dex": 14, "con": 12, "int": 16, "wis": 12, "cha": 10},
        ac=12, proficiencies={"skills": ["arcana", "history"]},
        attacks=[], spells_known=["mage-armor"],
    )
    assert result.ok, result.refusal
    return ctx


def _sheet(ctx) -> str:
    return (ctx.store.root / "sheets" / "elowen.md").read_text()


def _attack_elowen_at_total(ctx, total: int):
    """Kira (longsword +5 to hit) attacks Elowen with a player-supplied
    natural of `total - 5`, off-turn (spend='none') so turn order and
    action economy never matter for this probe."""
    return registry.execute(
        "attack", ctx, attacker="Kira", target="Elowen",
        attack_name="longsword", spend="none",
        player_attack_value=total - 5, player_damage_value=1,
    )


def _enter_combat_and_engage_kira_on_elowen(ctx):
    result = registry.execute(
        "start_combat", ctx,
        monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
        pc_initiative=20,
    )
    assert result.ok, result.refusal
    active = result.data["active"]
    while active != "Kira":
        active = registry.execute("next_turn", ctx).data["active"]
    engaged = registry.execute("engage", ctx, combatant="Kira", target="Elowen")
    assert engaged.ok, engaged.refusal


def test_mage_armor_full_lifecycle(elowen):
    ctx = elowen

    # Tier-2 mage armor cast hands the effect to the DM...
    cast = registry.execute("cast_spell", ctx, caster="Elowen",
                            spell_slug="mage-armor", targets=["Elowen"])
    assert cast.ok and cast.data.get("needs_ruling")
    # ...and the ruling records it mechanically.
    ruling = registry.execute(
        "dm_ruling", ctx, description="Mage armor: Elowen's AC becomes 15 for 8 hours",
        rationale="tier-2 spell; SRD sets base AC to 13 + DEX", effects=[MAGE_ARMOR],
    )
    assert ruling.ok, ruling.refusal

    # Sheet: effective AC plus an effect row with time remaining.
    sheet = _sheet(ctx)
    assert "- AC: 15 (base 12)" in sheet
    assert "## Active Effects" in sheet
    assert "mage armor: AC 15 (8h remaining)" in sheet

    # Attack totaling 14 vs her is a miss — no manual ruling needed.
    _enter_combat_and_engage_kira_on_elowen(ctx)
    miss = _attack_elowen_at_total(ctx, 14)
    assert miss.ok, miss.refusal
    assert miss.data["attack_roll"]["target_ac"] == 15
    assert miss.data["attack_roll"]["total"] == 14
    assert miss.data["hit"] is False
    # 15 still hits: the override raises AC, it doesn't blank attacks.
    hit = _attack_elowen_at_total(ctx, 15)
    assert hit.ok and hit.data["hit"] is True
    registry.execute("end_combat", ctx)

    # 8 hours of world clock expire the effect.
    registry.execute("create_location", ctx, slug="keep", name="Keep", description="x")
    travelled = registry.execute("travel", ctx, destination_slug="keep", hours=8)
    assert travelled.ok and "mage armor" in travelled.data["effects_expired"]
    sheet = _sheet(ctx)
    assert "- AC: 12" in sheet
    assert "mage armor" not in sheet

    # The same total-14 attack now hits her base AC 12.
    _enter_combat_and_engage_kira_on_elowen(ctx)
    result = _attack_elowen_at_total(ctx, 14)
    assert result.ok, result.refusal
    assert result.data["attack_roll"]["target_ac"] == 12
    assert result.data["hit"] is True


def test_end_effect_removes_it_immediately(elowen):
    ctx = elowen
    registry.execute("dm_ruling", ctx, description="Mage armor on Elowen",
                     rationale="tier-2 spell", effects=[MAGE_ARMOR])
    assert "- AC: 15 (base 12)" in _sheet(ctx)
    ended = registry.execute(
        "dm_ruling", ctx, description="Elowen dismisses her mage armor",
        rationale="caster may dismiss at will",
        effects=[{"op": "end_effect", "target": "Elowen", "name": "mage armor"}],
    )
    assert ended.ok, ended.refusal
    sheet = _sheet(ctx)
    assert "- AC: 12" in sheet and "Active Effects" not in sheet
