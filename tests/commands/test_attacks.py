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


def _set_dying(ctx, name="Kira", failures=1):
    """Put `name` at 0 hp, unconscious, mid-death-saves (one failure already
    recorded) — the state `apply_damage_to_target`'s hp_before == 0 branch
    expects on entry."""
    char = ctx.store.get_character(name)
    ctx.store.update_resources(
        char["id"], hp=0, conditions=["unconscious"],
        death_saves={"successes": 0, "failures": failures, "stable": False, "dead": False},
    )
    ctx.store.conn.commit()
    return char


def _start_goblin_engaged_with(ctx, target_key="Kira"):
    """Start a one-goblin combat with the goblin engaged in melee with
    `target_key`, and give the goblin the current turn."""
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == target_key:
            c["band"] = "engaged"; c["engaged_with"] = ["goblin-1"]
        if c["key"] == "goblin-1":
            c["band"] = "engaged"; c["engaged_with"] = [target_key]
    ctx.store.update_combat(combatants=combatants); ctx.store.conn.commit()
    _force_turn(ctx, "goblin-1", band="engaged", engaged_with=[target_key])


def test_damage_while_dying_kill_maps_to_defeated_in_narrative(ctx, party):
    """TVA-51: a killing blow landed while already dying (hp_before == 0)
    must honor death_mode, not hardcode 'dead'. Kira is unconscious and
    dying (engaged melee vs. an unconscious target auto-crits per SRD, so
    one hit adds two death-save failures — enough to push her 1 existing
    failure to 3 and kill her)."""
    _set_dying(ctx, "Kira", failures=1)
    _start_goblin_engaged_with(ctx, "Kira")
    result = None
    for _ in range(20):
        result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                                  attack_name="Scimitar", spend="none")
        if result.ok and result.data["hit"]:
            break
    assert result is not None and result.ok and result.data["hit"], "no hit landed (seeded)"
    assert result.data["critical"] is True  # unconscious + engaged => auto-crit
    assert result.data["target"]["status"] == "defeated"
    assert "Kira is defeated" in result.digest
    assert ctx.store.get_character("Kira")["status"] == "defeated"
    kira_c = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "Kira")
    assert kira_c["defeated"] is True


def test_damage_while_dying_kill_maps_to_dead_in_hardcore(ctx_hardcore, party_hardcore):
    """Same script on a hardcore campaign: the kill maps to 'dead', not
    'defeated'."""
    ctx = ctx_hardcore
    _set_dying(ctx, "Kira", failures=1)
    _start_goblin_engaged_with(ctx, "Kira")
    result = None
    for _ in range(20):
        result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                                  attack_name="Scimitar", spend="none")
        if result.ok and result.data["hit"]:
            break
    assert result is not None and result.ok and result.data["hit"], "no hit landed (seeded)"
    assert result.data["critical"] is True
    assert result.data["target"]["status"] == "dead"
    assert "Kira is dead" in result.digest
    assert ctx.store.get_character("Kira")["status"] == "dead"
    kira_c = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "Kira")
    assert kira_c["defeated"] is True


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


def test_unknown_target_refusal_lists_live_combatants(ctx, combat):
    # TVA-38: an unknown identifier lists the live roster (key, and display
    # name when it differs) so a retry doesn't have to guess again.
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="Bandit 3",
                              attack_name="longsword")
    assert result.ok is False
    assert "'Bandit 3'" in result.refusal
    assert "Kira" in result.refusal
    assert 'goblin-1 "Goblin"' in result.refusal
    assert 'goblin-2 "Goblin"' in result.refusal


def test_target_resolves_by_display_name_case_insensitively(ctx, combat):
    # TVA-38: goblin-1's display name is "Goblin"; unlabeled, so it collides
    # with goblin-2's — use a label to make the name unique first.
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "goblin-1":
            c["name"] = "Fen Scout"
    ctx.store.update_combat(combatants=combatants)
    ctx.store.conn.commit()
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="kira", target="fen scout",
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    assert result.data["target"]["key"] == "goblin-1"


def test_target_ambiguous_display_name_refused(ctx, combat):
    # goblin-1 and goblin-2 both display as "Goblin" (no label) — an
    # identifier that hits both must refuse and list the candidates, never
    # silently pick one.
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1", "goblin-2"])
    result = registry.execute("attack", ctx, attacker="Kira", target="Goblin",
                              attack_name="longsword")
    assert result.ok is False
    assert "goblin-1" in result.refusal and "goblin-2" in result.refusal
    assert "multiple" in result.refusal.lower()


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


def test_character_attack_name_matches_case_insensitively(ctx, combat):
    # TVA-38: "Longsword" must resolve the same as the stored "longsword".
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="Longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal


def test_monster_attack_name_matches_case_insensitively(ctx, combat):
    _engage_pair(ctx, "Kira", "goblin-1")
    _force_turn(ctx, "goblin-1", band="engaged", engaged_with=["Kira"])
    result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                              attack_name="scimitar", spend="action")
    assert result.ok, result.refusal


def test_attack_name_defaults_when_single_attack(ctx, combat):
    # TVA-58: Brother Aldric has exactly one attack (mace) — omitting
    # attack_name is unambiguous, so it should resolve instead of refusing.
    _engage_pair(ctx, "Brother Aldric", "goblin-1")
    _force_turn(ctx, "Brother Aldric", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Brother Aldric", target="goblin-1")
    assert result.ok, result.refusal
    assert result.data["attack_name"] == "mace"


def test_attack_name_still_required_when_multiple(ctx):
    # TVA-58: a combatant with more than one attack still needs attack_name
    # (or attack_names) — but the refusal now enumerates the options.
    registry.execute(
        "create_character", ctx, name="Dual", role="companion",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"]},
        attacks=[{"weapon": "longsword", "name": "longsword"},
                 {"weapon": "shortsword", "name": "shortsword"}],
    )
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    _engage_pair(ctx, "Dual", "goblin-1")
    _force_turn(ctx, "Dual", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Dual", target="goblin-1")
    assert result.ok is False
    assert "longsword" in result.refusal and "shortsword" in result.refusal


def test_turn_order_refusal_names_active_combatant(ctx, combat):
    # TVA-39: name whoever IS up and how to proceed instead of a bare
    # "it is not X's turn".
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="goblin-1", target="Kira",
                              attack_name="Scimitar", spend="action")
    assert result.ok is False
    assert "it is not goblin-1's turn" in result.refusal
    assert "it is Kira's turn" in result.refusal
    assert "act with Kira" in result.refusal
    assert "next_turn" in result.refusal


def test_reach_refusal_at_near_band_suggests_engage(ctx, combat):
    # TVA-39: a melee weapon short of a near-band target should point at
    # engage (which can legally close it) rather than a bare distance fact.
    _force_turn(ctx, "Kira", band="near")
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False
    assert "engage" in result.refusal.lower()


def test_reach_refusal_at_far_band_suggests_move_not_engage(ctx, combat):
    # A melee weapon against a far/distant target needs several turns of
    # movement — suggesting engage (a single jump costing the full distance)
    # would routinely be wrong, so the hint must say move instead.
    _force_turn(ctx, "Kira", band="far")
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword")
    assert result.ok is False
    assert "engage" not in result.refusal.lower()
    assert "move" in result.refusal.lower()


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


# --- no-damage attacks (TVA-22) ------------------------------------------


def _rug_turn(ctx):
    """Start combat vs a rug-of-smothering and give it Kira toe-to-toe."""
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "rug-of-smothering", "count": 1, "band": "near"}],
                     pc_initiative=15)
    _engage_pair(ctx, "rug-of-smothering-1", "Kira")
    _force_turn(ctx, "rug-of-smothering-1", band="engaged", engaged_with=["Kira"])


def test_no_damage_attack_resolves_and_surfaces_rider(ctx):
    """Smother has no damage dice: it must still roll +5 vs AC through the
    audited roller and, on a hit, surface the grapple rider (TVA-22)."""
    _rug_turn(ctx)
    hit = None
    for _ in range(12):
        result = registry.execute("attack", ctx, attacker="rug-of-smothering-1",
                                  target="Kira", attack_name="Smother", spend="none")
        assert result.ok, result.refusal
        assert result.data["damage"] is None  # never any direct damage
        assert result.data["attack_roll"]["total"] == result.data["attack_roll"]["natural"] + 5
        if result.data["hit"]:
            hit = result
            break
    assert hit is not None, "no hit landed in 12 seeded attempts"
    rider = hit.data["on_hit"]
    assert "grappled" in rider["text"]
    assert rider["escape_dc"] == 13
    assert {"grappled", "restrained", "blinded"} <= set(rider["conditions"])
    # no HP was touched — the rider is the DM's to apply
    kira = ctx.store.get_character("Kira")
    assert ctx.store.get_resources(kira["id"])["hp"] == 12
    # the d20 went through ctx.roller and into the event log
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"1d20"' in row["rolls"]


def test_no_damage_attack_listed_as_available(ctx):
    _rug_turn(ctx)
    result = registry.execute("attack", ctx, attacker="rug-of-smothering-1",
                              target="Kira", attack_name="Squash", spend="none")
    assert result.ok is False
    assert "available: Smother" in result.refusal


def test_no_damage_attack_miss_reports_miss(ctx):
    _rug_turn(ctx)
    for _ in range(12):
        result = registry.execute("attack", ctx, attacker="rug-of-smothering-1",
                                  target="Kira", attack_name="Smother", spend="none")
        assert result.ok, result.refusal
        if not result.data["hit"]:
            assert result.data.get("on_hit") is None
            assert result.data["damage"] is None
            return
    pytest.skip("seeded rolls never missed")  # pragma: no cover


# --- multiattack (TVA-17) -------------------------------------------------


def _bear_turn(ctx, *, target_ac=1, target_hp=100):
    """Brown bear engaged with Kira, bear's turn; Kira's AC/HP forced so
    swings land deterministically without dropping her."""
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "brown-bear", "count": 1, "band": "near"}],
                     pc_initiative=15)
    kira = ctx.store.get_character("Kira")
    ctx.store.conn.execute("UPDATE resources SET hp = ? WHERE character_id = ?",
                           (target_hp, kira["id"]))
    # Character targets resolve AC from the store (base + active effects),
    # not the combatant entry, so force it there.
    ctx.store.conn.execute("UPDATE characters SET ac = ? WHERE id = ?",
                           (target_ac, kira["id"]))
    ctx.store.conn.commit()
    _engage_pair(ctx, "brown-bear-1", "Kira")
    _force_turn(ctx, "brown-bear-1", band="engaged", engaged_with=["Kira"])


def test_multiattack_two_swings_one_action(ctx):
    _bear_turn(ctx)
    result = registry.execute("attack", ctx, attacker="brown-bear-1", target="Kira",
                              attack_names=["Bite", "Claws"])
    assert result.ok, result.refusal
    swings = result.data["swings"]
    assert [s["attack_name"] for s in swings] == ["Bite", "Claws"]
    expected_types = {"Bite": "piercing", "Claws": "slashing"}
    for s in swings:
        assert s["attack_roll"]["target_ac"] == 1
        if s["attack_roll"]["natural"] > 1:  # anything but a nat 1 hits AC 1
            assert s["hit"] is True
            assert s["damage"]["type"] == expected_types[s["attack_name"]]
            assert s["damage"]["final"] >= 1
    assert result.data["hits"] == sum(1 for s in swings if s["hit"])
    assert result.data["total_damage"] == sum(
        s["damage"]["final"] for s in swings if s["damage"])
    # the whole volley cost ONE action
    bear = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "brown-bear-1")
    assert bear["budget"]["action_available"] is False
    second = registry.execute("attack", ctx, attacker="brown-bear-1", target="Kira",
                              attack_name="Bite")
    assert second.ok is False and "action" in second.refusal.lower()


def test_multiattack_unknown_name_refused_before_action_spent(ctx):
    """Validate-before-consume: a bad swing name must refuse with the
    action still available."""
    _bear_turn(ctx)
    result = registry.execute("attack", ctx, attacker="brown-bear-1", target="Kira",
                              attack_names=["Bite", "Tail Slap"])
    assert result.ok is False and "Tail Slap" in result.refusal
    bear = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "brown-bear-1")
    assert bear["budget"]["action_available"] is True


def test_multiattack_param_validation(ctx, combat):
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    both = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                            attack_name="longsword", attack_names=["longsword"])
    assert both.ok is False
    # `neither` (no attack_name, no attack_names) is deliberately not tested
    # here: TVA-58 makes it default to Kira's sole attack (longsword) rather
    # than refuse — see test_attack_name_defaults_when_single_attack and
    # test_attack_name_still_required_when_multiple.
    empty = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                             attack_names=[])
    assert empty.ok is False
    reaction = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                                attack_names=["longsword"], spend="reaction")
    assert reaction.ok is False
    player_vals = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                                   attack_names=["longsword", "longsword"],
                                   player_attack_value=15)
    assert player_vals.ok is False
    # none of the refusals spent Kira's action
    kira = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "Kira")
    assert kira["budget"]["action_available"] is True


def test_multiattack_concentration_check_per_hit(ctx, combat):
    """Each hit that damages a concentrating target must carry its own
    concentration trigger (same pattern as magic missile's darts)."""
    aldric = ctx.store.get_character("Brother Aldric")
    ctx.store.conn.execute(
        "UPDATE resources SET concentration = '{\"spell\": \"bless\"}', hp = 100"
        " WHERE character_id = ?", (aldric["id"],))
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "Brother Aldric":
            c["ac"] = 1
    ctx.store.update_combat(combatants=combatants)
    ctx.store.conn.commit()
    _engage_pair(ctx, "goblin-1", "Brother Aldric")
    _force_turn(ctx, "goblin-1", band="engaged", engaged_with=["Brother Aldric"])
    result = registry.execute("attack", ctx, attacker="goblin-1",
                              target="Brother Aldric",
                              attack_names=["Scimitar", "Scimitar"])
    assert result.ok, result.refusal
    for s in result.data["swings"]:
        if s["hit"]:
            assert s["concentration_check"]["dc"] >= 10


def test_multiattack_halts_when_target_drops(ctx, combat):
    """A defeated target ends the volley; unspent swings are not rolled."""
    result = None
    for _ in range(10):
        combatants = ctx.store.combat()["combatants"]
        for c in combatants:
            if c["key"] == "goblin-1":
                c["ac"] = 1
                c["hp"] = 1
                c["defeated"] = False
        ctx.store.update_combat(combatants=combatants)
        ctx.store.conn.commit()
        _engage_pair(ctx, "Kira", "goblin-1")
        _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
        result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                                  attack_names=["longsword", "longsword"])
        assert result.ok, result.refusal
        if result.data["swings"][0]["hit"]:
            break
    assert result.data["swings"][0]["hit"], "first swing never hit AC 1 in 10 tries"
    assert result.data["swings"][0]["target"]["status"] == "defeated"
    assert len(result.data["swings"]) == 1
    assert "halted" in result.data


def test_single_attack_shape_unchanged(ctx, combat):
    """attack_name single-swing results keep their existing top-level shape."""
    _engage_pair(ctx, "Kira", "goblin-1")
    _force_turn(ctx, "Kira", band="engaged", engaged_with=["goblin-1"])
    result = registry.execute("attack", ctx, attacker="Kira", target="goblin-1",
                              attack_name="longsword",
                              player_attack_value=15, player_damage_value=6)
    assert result.ok, result.refusal
    for key in ("attack_roll", "hit", "critical", "damage", "target"):
        assert key in result.data
    assert "swings" not in result.data


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


def test_apply_condition_matches_combatant_name_case_insensitively(ctx, combat):
    # TVA-38: unlabeled goblin-1/goblin-2 both display "Goblin" — relabel
    # one so a name match is unambiguous.
    combatants = ctx.store.combat()["combatants"]
    for c in combatants:
        if c["key"] == "goblin-1":
            c["name"] = "Fen Scout"
    ctx.store.update_combat(combatants=combatants)
    ctx.store.conn.commit()
    result = registry.execute("apply_condition", ctx, target="fen scout",
                              condition="prone")
    assert result.ok, result.refusal
    goblin = next(c for c in ctx.store.combat()["combatants"] if c["key"] == "goblin-1")
    assert "prone" in goblin["conditions"]


def test_apply_condition_unknown_target_lists_combatants(ctx, combat):
    result = registry.execute("apply_condition", ctx, target="Bandit 3",
                              condition="prone")
    assert result.ok is False
    assert "'Bandit 3'" in result.refusal
    assert "Kira" in result.refusal


def test_apply_condition_ambiguous_target_refused(ctx, combat):
    result = registry.execute("apply_condition", ctx, target="Goblin",
                              condition="prone")
    assert result.ok is False
    assert "goblin-1" in result.refusal and "goblin-2" in result.refusal


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
