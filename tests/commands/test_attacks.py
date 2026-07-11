import pytest

from dm_engine.commands import registry

pytestmark = pytest.mark.usefixtures("party")


@pytest.fixture()
def combat(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 2, "band": "near"}],
                     pc_initiative=15)
    return ctx.store.combat()


def _force_turn(ctx, key, *, engaged_with=None, band=None):
    combat = ctx.store.combat()
    for c in combat["combatants"]:
        if c["key"] == key:
            if band: c["band"] = band
            if engaged_with is not None: c["engaged_with"] = engaged_with
            c["budget"] = {"speed": 30, "movement_remaining": 30,
                           "action_available": True, "bonus_action_available": True,
                           "reaction_available": True}
    idx = next(i for i, c in enumerate(combat["combatants"]) if c["key"] == key)
    ctx.store.update_combat(combatants=combat["combatants"], turn_index=idx)
    ctx.store.conn.commit()


def _engage_pair(ctx, a, b):
    """Put combatants a and b at engaged range, mutually engaged."""
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == a:
            c["band"] = "engaged"; c["engaged_with"] = [b]
        if c["key"] == b:
            c["band"] = "engaged"; c["engaged_with"] = [a]
    ctx.store.update_combat(combatants=combatants)
    ctx.store.conn.commit()


# --- binding tests (verbatim) -------------------------------------------


def test_melee_refused_from_near(ctx, combat):
    _force_turn(ctx, "Kira", band="near")
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False and "reach" in result.refusal.lower()


def test_player_supplied_attack_hits_and_damages(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "goblin-1":
            c["band"] = "engaged"; c["engaged_with"] = ["Kira"]
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["hit"] is True          # 15 + 5 = 20 vs AC 15
    assert result.data["damage"]["final"] == 9  # 6 + STR 3
    goblin = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "goblin-1")
    assert goblin["defeated"] is True and goblin["hp"] == 0  # 9 dmg vs 7 hp
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"player_supplied": true' in row["rolls"]


def test_attack_consumes_action_and_second_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                     attack_name="longsword", player_attack_value=2,
                     player_damage_value=1)
    second = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword", player_attack_value=15,
                              player_damage_value=6)
    assert second.ok is False and "action" in second.refusal.lower()


def test_monster_attack_drops_pc_to_dying(ctx, combat):
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = 1 WHERE character_id = ?",
                           (kira["id"],))
    ctx.store.conn.commit()
    _force_turn(ctx, "goblin-1", band="engaged", engaged_with=["Kira"])
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "Kira":
            c["band"] = "engaged"; c["engaged_with"] = ["goblin-1"]
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    # engine-rolled monster attack; loop until a hit lands (seeded, deterministic)
    for _ in range(10):
        result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                                  attack_name="Scimitar", spend="none")
        if result.ok and result.data["hit"]:
            break
    res = ctx.store.get_resources(kira["id"])
    assert res["hp"] == 0
    assert "unconscious" in res["conditions"]
    assert res["death_saves"]["failures"] == 0


def test_apply_condition_validates_and_breaks_concentration(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute(
        "UPDATE resources SET concentration = '{\"spell\": \"bless\"}'"
        " WHERE character_id = ?", (aldric["id"],))
    ctx.store.conn.commit()
    bad = registry.execute("apply_condition", ctx, target="Brother Aldric",
                           condition="sleepy")
    assert bad.ok is False
    result = registry.execute("apply_condition", ctx, target="Brother Aldric",
                              condition="stunned")
    assert result.ok
    assert ctx.store.get_resources(aldric["id"])["concentration"] is None
    assert result.data.get("concentration_broken") is True


def test_condition_commands_normalize_input(ctx):
    # TVA-24: case and padding reach the canonical condition name.
    result = registry.execute("apply_condition", ctx, target="Kira", condition=" Prone ")
    assert result.ok, result.refusal
    assert result.data["condition"] == "prone"
    result = registry.execute("remove_condition", ctx, target="Kira", condition="PRONE")
    assert result.ok, result.refusal


def test_unknown_condition_refusal_lists_vocabulary(ctx):
    from dm_engine.rules.conditions import CONDITIONS

    bad = registry.execute("apply_condition", ctx, target="Kira", condition="sleepy")
    assert bad.ok is False
    for condition in CONDITIONS:  # all 15, single-shot recovery
        assert condition in bad.refusal
    bad = registry.execute("remove_condition", ctx, target="Kira", condition="sleepy")
    assert bad.ok is False and "blinded" in bad.refusal


# --- refusal matrix -----------------------------------------------------


def test_no_combat_refused(ctx):
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False and "combat" in result.refusal.lower()


def test_unknown_attacker_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Nobody", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False and "attacker" in result.refusal.lower()


def test_unknown_target_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="ghost-9",
                              attack_name="longsword")
    assert result.ok is False and "target" in result.refusal.lower()


def test_defeated_target_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "goblin-1":
            c["defeated"] = True
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False and "defeated" in result.refusal.lower()


def test_invalid_spend_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword", spend="bonus")
    assert result.ok is False and "spend" in result.refusal.lower()


def test_action_off_turn_refused(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    # goblin-1 tries an action-spend attack when it is not its turn
    _engage_pair(ctx, "Kira", "goblin-1")
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                              attack_name="Scimitar", spend="action")
    assert result.ok is False and "turn" in result.refusal.lower()


def test_unknown_attack_name_lists_available(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="greataxe")
    assert result.ok is False and "longsword" in result.refusal


def test_player_value_on_companion_refused(ctx, combat):
    _force_turn(ctx, "Brother Aldric", band="engaged", engaged_with=["goblin-1"])
    _engage_pair(ctx, "Brother Aldric", "goblin-1")
    _force_turn(ctx, "Brother Aldric", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Brother Aldric",
                              target="goblin-1", attack_name="mace",
                              player_attack_value=15)
    assert result.ok is False
    assert "pc" in result.refusal.lower() or "player" in result.refusal.lower()


def test_player_attack_value_out_of_range(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword", player_attack_value=25)
    assert result.ok is False and "20" in result.refusal


# --- reaction / opportunity attack --------------------------------------


def test_attack_with_invalid_stored_spec_refuses_not_crashes(ctx, combat):
    """A stored spec that predates validation (or survived migration
    untouched) must refuse cleanly on use, never KeyError mid-combat."""
    kira = ctx.store.get_character("Kira")
    ctx.store.update_character(
        kira["id"],
        attacks=[{"name": "haunted-blade", "attack_bonus": 6, "damage": "1d6+4"}],
    )
    ctx.store.conn.commit()
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="haunted-blade")
    assert not result.ok
    assert "haunted-blade" in result.refusal and "Kira" in result.refusal
    assert "invalid" in result.refusal


def test_off_turn_reaction_used_once(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    _engage_pair(ctx, "Kira", "goblin-1")
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    first = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                             attack_name="Scimitar", spend="reaction")
    assert first.ok, first.refusal
    second = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                              attack_name="Scimitar", spend="reaction")
    assert second.ok is False and "reaction" in second.refusal.lower()


# --- crit path via player_value=20 --------------------------------------


def test_crit_via_player_attack_value_20(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    _engage_pair(ctx, "Kira", "goblin-1")
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword",
                              player_attack_value=20, player_damage_value=1)
    assert result.ok, result.refusal
    assert result.data["hit"] is True
    assert result.data["critical"] is True


# --- mitigation vs a resistant monster (from the real DB) ---------------


def test_mitigation_against_resistant_monster(ctx):
    # shadow resists slashing (expressed as a compound phrase in the record)
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "shadow", "count": 1, "band": "near"}],
                     pc_initiative=15)
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["shadow-1"])
    _engage_pair(ctx, "Kira", "shadow-1")
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["shadow-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="shadow-1",
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["hit"] is True
    assert result.data["damage"]["raw"] == 9   # 6 + STR 3
    assert result.data["damage"]["final"] == 4  # halved by resistance
    assert "resistance" in result.data["damage"]["applied"]


def _make_longsword_magical(ctx):
    """Mark Kira's longsword as a magic weapon via the spec's properties."""
    kira = ctx.store.get_character("Kira")
    attacks = kira["attacks"]
    for spec in attacks:
        if spec["name"] == "longsword":
            spec["properties"] = [*spec.get("properties", []), "magical"]
    ctx.store.update_character(kira["id"], attacks=attacks)
    ctx.store.conn.commit()


def _duel(ctx, slug):
    """Start combat against one monster and put Kira toe-to-toe with it."""
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": slug, "count": 1, "band": "near"}],
                     pc_initiative=15)
    key = f"{slug}-1"
    _force_turn(ctx, "Kira", band="engaged", engaged_with=[key])
    _engage_pair(ctx, "Kira", key)
    _force_turn(ctx, "Kira", band="engaged", engaged_with=[key])
    return key


def test_magical_attack_bypasses_nonmagical_resistance(ctx):
    # shadow resists 'bludgeoning, piercing, and slashing from nonmagical
    # weapons' — a magic longsword deals full damage (2014 RAW).
    _make_longsword_magical(ctx)
    key = _duel(ctx, "shadow")
    result = registry.execute("attack", ctx, attacker="Kira", target=key,
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["hit"] is True
    assert result.data["damage"]["raw"] == 9
    assert result.data["damage"]["final"] == 9  # NOT halved
    assert result.data["damage"]["applied"] == []


def test_plain_resistance_applies_to_magical_attack(ctx):
    # swarm-of-rats has a plain (no caveat) slashing resistance: it halves
    # magical and nonmagical hits alike.
    _make_longsword_magical(ctx)
    key = _duel(ctx, "swarm-of-rats")
    result = registry.execute("attack", ctx, attacker="Kira", target=key,
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["hit"] is True
    assert result.data["damage"]["final"] == 4  # 9 halved, rounded down
    assert "resistance" in result.data["damage"]["applied"]


def test_werewolf_immunity_blocks_nonmagical_but_not_magical(ctx):
    # werewolf-hybrid is immune to 'bludgeoning, piercing, and slashing from
    # nonmagical weapons that aren't silvered'.
    key = _duel(ctx, "werewolf-hybrid")
    plain = registry.execute("attack", ctx, attacker="Kira", target=key,
                             attack_name="longsword",
                             player_attack_value=15, player_damage_value=6)
    assert plain.ok, plain.refusal
    assert plain.data["hit"] is True
    assert plain.data["damage"]["final"] == 0
    assert plain.data["damage"]["applied"] == ["immunity"]

    _make_longsword_magical(ctx)
    _force_turn(ctx, "Kira", band="engaged", engaged_with=[key])
    magical = registry.execute("attack", ctx, attacker="Kira", target=key,
                               attack_name="longsword",
                               player_attack_value=15, player_damage_value=6)
    assert magical.ok, magical.refusal
    assert magical.data["hit"] is True
    assert magical.data["damage"]["final"] == 9
    assert magical.data["damage"]["applied"] == []


# --- condition commands -------------------------------------------------


def test_remove_condition_inverse(ctx):
    kira_id = ctx.store.get_character("Kira")["id"]
    registry.execute("apply_condition", ctx, target="Kira", condition="prone")
    assert "prone" in ctx.store.get_resources(kira_id)["conditions"]
    removed = registry.execute("remove_condition", ctx, target="Kira", condition="prone")
    assert removed.ok
    assert "prone" not in ctx.store.get_resources(kira_id)["conditions"]
    again = registry.execute("remove_condition", ctx, target="Kira", condition="prone")
    assert again.ok is False


def test_apply_condition_idempotent_refusal(ctx):
    registry.execute("apply_condition", ctx, target="Kira", condition="poisoned")
    dup = registry.execute("apply_condition", ctx, target="Kira", condition="poisoned")
    assert dup.ok is False and "already" in dup.refusal.lower()


def test_apply_condition_to_monster(ctx, combat):
    result = registry.execute("apply_condition", ctx, target="goblin-1",
                              condition="prone")
    assert result.ok
    goblin = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "goblin-1")
    assert "prone" in goblin["conditions"]


def test_apply_exhaustion_delta(ctx):
    kira_id = ctx.store.get_character("Kira")["id"]
    result = registry.execute("apply_condition", ctx, target="Kira",
                              condition="exhaustion", exhaustion_delta=2)
    assert result.ok
    assert ctx.store.get_resources(kira_id)["exhaustion"] == 2


def test_break_concentration_command(ctx):
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute(
        "UPDATE resources SET concentration = '{\"spell\": \"bless\"}'"
        " WHERE character_id = ?", (aldric["id"],))
    ctx.store.conn.commit()
    result = registry.execute("break_concentration", ctx, character="Brother Aldric")
    assert result.ok
    assert ctx.store.get_resources(aldric["id"])["concentration"] is None
    again = registry.execute("break_concentration", ctx, character="Brother Aldric")
    assert again.ok is False
