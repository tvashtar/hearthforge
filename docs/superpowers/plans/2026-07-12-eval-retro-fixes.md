# Eval-Retro Fixes (TVA-51..TVA-61) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the 11 findings from the dm-retro of eval run 20260712-105107 — engine death-handling integrity (TVA-51/52/53/54), spell/save/ergonomics (TVA-55/56/57/58), dm-session skill guidance (TVA-59), and eval harness/scenario fixes (TVA-60/61).

**Architecture:** All engine changes go through existing command handlers and rules modules — no new storage schema. Death-state transitions get centralized so `attack`, `death_save`, healing, and `dm_ruling` agree. Eval changes extend `beat_done` matching and rework `caravan_ambush.yaml`.

**Tech Stack:** Python 3.12, uv, pytest, sqlite (existing). No new dependencies.

## Global Constraints

- FC-1..FC-7 in ARCHITECTURE.md are frozen. FC-7: death mode per campaign (`narrative` default / `hardcore`), death saves identical in both modes. These tasks make code honor FC-7, never change it.
- Refusals, not exceptions: illegal actions return `ok=False` + human-readable `refusal`. Validate before consuming (refusals commit; only exceptions roll back).
- Every die through `ctx.roller`. Monster/companion rolls NEVER accept `player_value`.
- `registry.execute` is the only mutation path; handlers are plain functions signature `fn(ctx, ..., **kwargs) -> CommandResult` registered via `@command`.
- Line length 100 (ruff). Tests live in `tests/commands/` (per-module), `tests/rules/`, `tests/integration/`, `tests/evals/`. Fixtures: `ctx` (fresh store, seed 99, death_mode="narrative"), `party` (Kira PC fighter + Brother Aldric level-3 companion cleric).
- Run scoped tests per task: `uv run pytest tests/commands/test_<module>.py -x -q`. Full suite + `uv run ruff check src tests evals` before the final review.
- Commit per task with conventional commits. PR body must spell out each full ticket ID: TVA-51, TVA-52, TVA-53, TVA-54, TVA-55, TVA-56, TVA-57, TVA-58, TVA-59, TVA-60, TVA-61.
- Never touch `campaigns/` (live data).

---

### Task 1: TVA-51 — damage-while-dying honors death_mode + sets character status

**Files:**
- Modify: `src/dm_engine/commands/attacks.py:265-273` (dying branch of `apply_damage_to_target`)
- Modify: `src/dm_engine/commands/combat.py:536-565` (`end_combat` defeat/XP separation)
- Test: `tests/commands/test_attacks.py`, `tests/commands/test_combat.py`

**Interfaces:**
- Consumes: `ctx.store.campaign_meta()["death_mode"]`, `ctx.store.update_character(cid, status=...)` (both already used by the massive-damage branch at attacks.py:274-284).
- Produces: dying-path kill now yields `frag["target"]["status"]` of `"defeated"` (narrative) or `"dead"` (hardcore) — Task 2 and Task 4 rely on `characters.status` being set on every death path.

- [ ] **Step 1: Write failing tests**

In `tests/commands/test_attacks.py` (follow the file's existing combat-setup helpers for starting a combat where a monster attacks Kira; use `player` fixture pattern already present):

```python
def test_damage_while_dying_kill_maps_to_defeated_in_narrative(ctx, party):
    # Arrange: Kira at 0 HP, dying (fresh death saves), in combat vs a bandit.
    # Then a crit for >= max_hp while dying -> died=True.
    # Assert: result digest says "defeated" not "dead";
    #   store.get_character("Kira")["status"] == "defeated";
    #   the combatant tracker entry for Kira has defeated == True.

def test_damage_while_dying_kill_maps_to_dead_in_hardcore(ctx_hardcore, party_hardcore):
    # Same arrangement on a hardcore-campaign ctx -> status "dead".
```

Add a `ctx_hardcore` fixture (copy of `ctx` in `tests/conftest.py` with `death_mode="hardcore"`) and `party_hardcore` mirroring `party`, if not already present.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/commands/test_attacks.py -k dying -x -q` → FAIL (digest currently "dead" in narrative; status stays "active").

- [ ] **Step 3: Implement** — replace attacks.py:265-273 with:

```python
    if hp_before == 0:
        # Already dying: another failed save (two on a crit).
        outcome = apply_damage_while_dying(
            DeathSaveState(**res["death_saves"]), amount, max_hp, critical=critical
        )
        ctx.store.update_resources(cid, death_saves=outcome.state.model_dump())
        died = outcome.state.dead
        frag["target"]["hp"] = 0
        if died:
            death_mode = ctx.store.campaign_meta()["death_mode"]
            status = "dead" if death_mode == "hardcore" else "defeated"
            ctx.store.update_character(cid, status=status)
            frag["target"]["status"] = status
        else:
            frag["target"]["status"] = "dying"
```

`_drop_tail` (attacks.py:431) already renders `defeated` correctly ("— Kira is defeated!"). The `died and combatant is not None` block at attacks.py:311 already marks the tracker.

- [ ] **Step 4: end_combat separation** — in `combat.py` `end_combat`, split the defeated list and gate PC XP:

```python
    defeated_monsters = [
        c["key"] for c in combatants if c["defeated"] and c["kind"] == "monster"
    ]
    downed_party = [
        c["key"] for c in combatants if c["defeated"] and c["kind"] != "monster"
    ]
```

Keep `data["defeated"]` = defeated_monsters (the XP-relevant list), add `data["downed_party"]`, and append `f" — {', '.join(downed_party)} defeated" if downed_party else ""` to the digest. Confirm `award_party_xp` recipients already filter on `status == "active"` (they should now exclude the defeated PC since Step 3 writes status); if it does not filter, add that filter with a test.

- [ ] **Step 5: Run tests** — `uv run pytest tests/commands/test_attacks.py tests/commands/test_combat.py -x -q` → PASS. Fix any existing test that asserted the old "dead" digest in narrative mode (that assertion was encoding the bug).

- [ ] **Step 6: Commit** — `git commit -m "fix: dying-path death honors death_mode (TVA-51)"`

---

### Task 2: TVA-52 — revival clears the combatant defeated flag and restores status

**Files:**
- Modify: `src/dm_engine/commands/combatants.py` (add shared `set_combatant_defeated`)
- Modify: `src/dm_engine/commands/checks.py:322-333` (`_mark_combatant_defeated` → use shared helper)
- Modify: `src/dm_engine/commands/spells.py:161-190` (`_apply_healing`)
- Test: `tests/commands/test_spells.py`

**Interfaces:**
- Consumes: Task 1's guarantee that `characters.status` is set on every death path.
- Produces: `set_combatant_defeated(ctx, character_name, defeated: bool) -> None` in `combatants.py` — Task 4's `revive`/`set_defeated` ops call it.

- [ ] **Step 1: Write failing test** in `tests/commands/test_spells.py`:

```python
def test_healing_revived_pc_rejoins_combat(ctx, party):
    # Arrange: combat active; Kira killed via the dying path (Task 1: status
    # "defeated", tracker defeated=True). Aldric casts cure-wounds on Kira.
    # Assert: hp > 0; "unconscious" not in conditions; death_saves reset;
    #   store.get_character("Kira")["status"] == "active";
    #   Kira's combatant tracker entry defeated == False.

def test_healing_hardcore_dead_pc_is_refused(ctx_hardcore, party_hardcore):
    # Arrange: Kira status "dead" (hardcore kill). cast_spell cure-wounds
    # targeting Kira -> ok=False, refusal mentions she is dead.
```

- [ ] **Step 2: Verify failure** — `uv run pytest tests/commands/test_spells.py -k revive -x -q` → FAIL.

- [ ] **Step 3: Implement.** In `combatants.py` add:

```python
def set_combatant_defeated(ctx, character: str, defeated: bool) -> None:
    """Set the active-combat tracker's defeated flag for a character
    combatant (no-op out of combat or for unknown keys)."""
    combat = ctx.store.combat()
    if not combat["active"]:
        return
    combatants = combat["combatants"]
    changed = False
    for combatant in combatants:
        if combatant.get("key") == character:
            combatant["defeated"] = defeated
            changed = True
    if changed:
        ctx.store.update_combat(combatants=combatants)
```

Replace the body of `checks.py:_mark_combatant_defeated` with a call to `set_combatant_defeated(ctx, character, True)` (keep the name/wrapper so call sites don't churn). In `spells.py:_apply_healing`, extend the `hp_before == 0` branch:

```python
    if hp_before == 0:
        conditions = [c for c in res["conditions"] if c != "unconscious"]
        new_hp = min(max_hp, amount)
        ctx.store.update_resources(
            cid, hp=new_hp, conditions=conditions,
            death_saves=DeathSaveState().model_dump(),
        )
        if char_row["status"] == "defeated":
            ctx.store.update_character(cid, status="active")
        set_combatant_defeated(ctx, char_row["name"], False)
```

In `cast_spell`'s step-4 validation (spells.py:362-368), after resolving the heal target, refuse dead targets before any state is spent:

```python
        target_row = ctx.store.get_character_by_id(_heal_target_cid(ctx, targets[0]))
        if target_row["status"] == "dead":
            return refuse("cast_spell", f"{target_row['name']} is dead")
```

Apply the same guard in `use_item`'s healing path if it validates targets separately.

- [ ] **Step 4: Run tests** — `uv run pytest tests/commands/test_spells.py tests/commands/test_checks.py -x -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "fix: revival rejoins combat, hardcore-dead unhealable (TVA-52)"`

---

### Task 3: TVA-53 — dm_ruling adjust_hp routes through real damage/heal transitions

**Files:**
- Modify: `src/dm_engine/commands/rulings.py:252-274` (`_apply_op` adjust_hp character branch)
- Test: `tests/commands/test_rulings.py`

**Interfaces:**
- Consumes: `apply_damage_to_target(ctx, key, amount, damage_type, *, critical)` (attacks.py), `_apply_healing(ctx, key, amount)` (spells.py), both post-Task-1/2 semantics.
- Produces: adjust_hp op echo may now include `"status"` (e.g. "unconscious"/"defeated") and `"concentration_broken": True`.

- [ ] **Step 1: Write failing tests** in `tests/commands/test_rulings.py`:

```python
def test_adjust_hp_to_zero_starts_dying(ctx, party):
    # dm_ruling adjust_hp delta=-<current hp> on conscious Kira ->
    # resources: hp 0, "unconscious" in conditions, death_saves fresh
    # (successes 0, failures 0, not stable, not dead).

def test_adjust_hp_from_zero_revives(ctx, party):
    # Arrange Kira at 0/unconscious/dying. dm_ruling adjust_hp delta=+3 ->
    # hp 3, unconscious removed, death_saves reset.

def test_adjust_hp_breaks_concentration_on_knockout(ctx, party):
    # Aldric concentrating (cast bless via dm_ruling apply_effect w/
    # concentration, or set resources.concentration directly through a cast);
    # adjust_hp drops him to 0 -> concentration cleared.
```

- [ ] **Step 2: Verify failure** — `uv run pytest tests/commands/test_rulings.py -k adjust_hp -x -q` → FAIL.

- [ ] **Step 3: Implement** — in `_apply_op`, replace the character branch of `adjust_hp` (rulings.py:271-274) with:

```python
        # Route through the real transition helpers so crossing 0 HP behaves
        # exactly like damage/healing (unconscious, dying state, death saves,
        # concentration, combatant flag). Local imports dodge module cycles.
        from dm_engine.commands.attacks import apply_damage_to_target
        from dm_engine.commands.spells import _apply_healing

        if delta < 0:
            frag = apply_damage_to_target(
                ctx, char["name"], -delta, "untyped", critical=False
            )
            echo = {"op": "adjust_hp", "target": target, "delta": delta,
                    "hp": frag["target"]["hp"]}
            if "status" in frag["target"]:
                echo["status"] = frag["target"]["status"]
            if frag.get("concentration_broken"):
                echo["concentration_broken"] = True
            return echo
        healed = _apply_healing(ctx, char["name"], delta)
        return {"op": "adjust_hp", "target": target, "delta": delta,
                "hp": healed["hp"]}
```

Keep the monster branch (rulings.py:255-270) unchanged. Note `delta == 0` validation already exists upstream; if not, treat as heal of 0 (no-op).

- [ ] **Step 4: Run** — `uv run pytest tests/commands/test_rulings.py -x -q` → PASS. Watch for import cycles (run the whole commands suite: `uv run pytest tests/commands -x -q`).

- [ ] **Step 5: Commit** — `git commit -m "fix: adjust_hp runs real 0-HP transitions (TVA-53)"`

---

### Task 4: TVA-54 — stabilize command + stabilize/revive/set_defeated ruling ops

**Files:**
- Modify: `src/dm_engine/commands/checks.py` (new `stabilize` command next to `death_save`)
- Modify: `src/dm_engine/commands/rulings.py` (`_OP_FIELDS`, `_validate_op`, `_apply_op`)
- Test: `tests/commands/test_checks.py`, `tests/commands/test_rulings.py`

**Interfaces:**
- Consumes: `set_combatant_defeated` (Task 2), `DeathSaveState` (rules/death.py), `skill_check` handler (checks.py — call the plain function, not registry.execute, so rolls land in the stabilize event row).
- Produces: `stabilize` registry command; `dm_ruling` ops `stabilize(target)`, `revive(target, hp)`, `set_defeated(target)`.

- [ ] **Step 1: Write failing tests**

`tests/commands/test_checks.py`:

```python
def test_stabilize_with_medicine_check_success(ctx, party):
    # Kira dying at 0. registry.execute("stabilize", ctx, character="Kira",
    #   medicine_by="Brother Aldric", player_value=None) with a roller seed
    #   that passes DC 10 (or pass player_value via Kira? NO — medicine_by is
    #   Aldric, companion: player_value must be refused for him).
    # Assert ok, death_saves.stable True, hp still 0, unconscious retained.

def test_stabilize_refuses_when_not_dying(ctx, party):
    # Kira at full hp -> ok=False, refusal names the requirement.

def test_stabilize_without_checker_is_dm_fiat(ctx, party):
    # No medicine_by -> stabilizes unconditionally (DM fiat / Spare the Dying).
```

`tests/commands/test_rulings.py`:

```python
def test_ruling_revive_op_full_transition(ctx, party):
    # Kira defeated (Task 1 path) in combat. dm_ruling effects=
    #   [{"op": "revive", "target": "Kira", "hp": 1}]
    # -> hp 1, unconscious cleared, death_saves fresh, status "active",
    #    combatant defeated flag False.

def test_ruling_set_defeated_op(ctx, party):
    # Conscious Kira -> [{"op": "set_defeated", "target": "Kira"}] ->
    # hp 0, death_saves.dead True, status "defeated" (narrative ctx),
    # combatant flag True when combat active.

def test_ruling_stabilize_op(ctx, party):
    # Dying Kira -> [{"op": "stabilize", "target": "Kira"}] ->
    # death_saves.stable True, hp 0.

def test_ruling_stabilize_op_refuses_conscious_target(ctx, party):
    # Kira at full hp -> dm_ruling with stabilize op -> ok=False (validated
    # before apply; batch refusal per dm_ruling's validate-then-apply).
```

- [ ] **Step 2: Verify failure** — both files, `-k "stabilize or revive or set_defeated"` → FAIL (unknown command / invalid op).

- [ ] **Step 3: Implement `stabilize`** in checks.py after `death_save`:

```python
@command("stabilize")
def stabilize(
    ctx: CommandContext,
    character: str,
    medicine_by: str | None = None,
    player_value: int | None = None,
    gm_only: bool = False,
    **kwargs,
) -> CommandResult:
    """Stabilize a dying character: optional Medicine check (DC 10) by
    `medicine_by`; without a checker it is DM fiat (Spare the Dying etc.)."""
    char = ctx.store.get_character(character)
    if char is None:
        return refuse("stabilize", f"no character named {character!r}")
    resources = ctx.store.get_resources(char["id"])
    ds = resources["death_saves"]
    if resources["hp"] > 0 or ds["stable"] or ds["dead"] or char["status"] != "active":
        return refuse(
            "stabilize", f"{character} is not dying (0 hp, not yet stable or dead)"
        )
    check_data = None
    if medicine_by is not None:
        result = skill_check(
            ctx, character=medicine_by, skill="medicine", dc=10,
            player_value=player_value, gm_only=gm_only,
        )
        if not result.ok:
            return refuse("stabilize", result.refusal)
        check_data = result.data
        if not check_data["success"]:
            digest = (
                f"{medicine_by} fails to stabilize {character} "
                f"(Medicine {check_data['total']} vs DC 10)"
            )
            return CommandResult(
                ok=True, command="stabilize", digest=digest,
                data={"stabilized": False, "check": check_data}, gm_only=gm_only,
            )
    ctx.store.update_resources(
        char["id"], death_saves=DeathSaveState(stable=True).model_dump()
    )
    by = f" by {medicine_by}" if medicine_by else ""
    digest = f"{character} is stabilized{by} — 0 HP, unconscious, no longer dying"
    return CommandResult(
        ok=True, command="stabilize", digest=digest,
        data={"stabilized": True, "check": check_data}, gm_only=gm_only,
    )
```

Match `skill_check`'s actual signature/data keys (read it first — e.g. the total may be `data["total"]` or nested; adjust). `skill_check` is a plain function in the same module, so direct call keeps everything in one event/transaction.

- [ ] **Step 4: Implement the three ops** in rulings.py. Extend `_OP_FIELDS`:

```python
    "stabilize": "target",
    "revive": "target, hp",
    "set_defeated": "target",
```

In `_validate_op` add cases: `stabilize` — target resolves to a character whose resources show hp 0, not stable, not dead; `revive` — target resolves to a character with status in ("defeated", "active") whose hp is 0 or status "defeated", `hp` an int >= 1 (refuse reviving a hardcore-"dead" character: "X is dead — only hardcore campaigns kill, and dead is permanent"); `set_defeated` — target resolves to a character. In `_apply_op` add:

```python
    if kind == "stabilize":
        _, char, _ = _resolve_target(ctx, op["target"])
        ctx.store.update_resources(
            char["id"], death_saves=DeathSaveState(stable=True).model_dump()
        )
        return {"op": "stabilize", "target": op["target"]}

    if kind == "revive":
        _, char, _ = _resolve_target(ctx, op["target"])
        res = ctx.store.get_resources(char["id"])
        hp = min(char["max_hp"], op["hp"])
        conditions = [c for c in res["conditions"] if c != "unconscious"]
        ctx.store.update_resources(
            char["id"], hp=hp, conditions=conditions,
            death_saves=DeathSaveState().model_dump(),
        )
        ctx.store.update_character(char["id"], status="active")
        set_combatant_defeated(ctx, char["name"], False)
        return {"op": "revive", "target": op["target"], "hp": hp}

    if kind == "set_defeated":
        _, char, _ = _resolve_target(ctx, op["target"])
        death_mode = ctx.store.campaign_meta()["death_mode"]
        status = "dead" if death_mode == "hardcore" else "defeated"
        ctx.store.update_resources(
            char["id"], hp=0, death_saves=DeathSaveState(dead=True).model_dump()
        )
        ctx.store.update_character(char["id"], status=status)
        set_combatant_defeated(ctx, char["name"], True)
        return {"op": "set_defeated", "target": op["target"], "status": status}
```

Import `DeathSaveState` and `set_combatant_defeated` at rulings.py top (check for cycles; use local imports if needed). Note `_OP_FIELDS` drives the MCP schema description automatically (TVA-25/36) — no schema edits needed.

- [ ] **Step 5: Run** — `uv run pytest tests/commands/test_checks.py tests/commands/test_rulings.py -x -q` → PASS. Also `uv run pytest tests/test_mcp_schema.py -x -q` (new command surfaces in tool introspection).

- [ ] **Step 6: Commit** — `git commit -m "feat: stabilize command + death-state ruling ops (TVA-54)"`

---

### Task 5: TVA-55 — cast_spell derives spend from casting time

**Files:**
- Modify: `src/dm_engine/commands/spells.py:237-250` (signature) and the spend checks at 317-353
- Test: `tests/commands/test_spells.py`

**Interfaces:**
- Consumes: `SpellRecord.casting_time` (str, e.g. "1 action", "1 bonus action", "1 reaction…") — models/srd.py:56.
- Produces: `cast_spell(spend=None)` default; explicit `spend` still overrides.

- [ ] **Step 1: Write failing test**

```python
def test_healing_word_defaults_to_bonus_action(ctx, party):
    # Combat active, Aldric's turn. cast healing-word (no spend arg) then
    # sacred-flame (no spend arg) in the same turn: both succeed —
    # healing-word consumed the bonus action, sacred-flame the action.

def test_explicit_spend_still_overrides(ctx, party):
    # cast healing-word with spend="action" -> consumes the action
    # (subsequent action-spend cast refused).
```

- [ ] **Step 2: Verify failure** — second cast currently refused "no action remaining". 

- [ ] **Step 3: Implement.** Change the signature default to `spend: str | None = None`, and after the record lookup (spells.py:255-257) add:

```python
    if spend is None:
        ct = record.casting_time.lower()
        if "bonus action" in ct:
            spend = "bonus_action"
        elif "reaction" in ct:
            spend = "reaction"
        else:
            spend = "action"
```

Everything downstream (`_SPENDS` validation, budget spending) is unchanged. Update the `cast_spell` docstring's spend description ("default: derived from the spell's casting time"). Do NOT implement the 2014 bonus-action-spell-limits-your-other-spell rule — out of scope (noted on TVA-55), YAGNI for now.

- [ ] **Step 4: Run** — `uv run pytest tests/commands/test_spells.py -x -q` and `uv run pytest tests/test_mcp_schema.py -x -q` (spend is now nullable in the introspected schema — fix schema-test expectations if they assert the old default).

- [ ] **Step 5: Commit** — `git commit -m "fix: derive cast_spell spend from casting time (TVA-55)"`

---

### Task 6: TVA-56 — saving_throw accepts monster combatants

**Files:**
- Modify: `src/dm_engine/commands/checks.py:248-319` (`saving_throw`)
- Test: `tests/commands/test_checks.py`

**Interfaces:**
- Consumes: `find_combatant`/`ambiguous_combatant_refusal` (commands/combatants.py), `MonsterRecord.ability_scores` + `model_extra["proficiencies"]` (SRD entries like `{"value": 4, "proficiency": {"index": "saving-throw-dex"}}`), `effects_for(conditions, exhaustion)`.
- Produces: `saving_throw(character=<combatant key or display name>)` works for monsters in active combat.

- [ ] **Step 1: Write failing tests**

```python
def test_monster_saving_throw_in_combat(ctx, party):
    # start_combat with a bandit; saving_throw(character="bandit-1",
    #   ability="wis", dc=12) -> ok=True, data has natural/total/success,
    #   modifier == the bandit's WIS modifier (SRD bandit WIS 10 -> +0).

def test_monster_save_uses_srd_save_proficiency(ctx, party):
    # A monster with a saving-throw proficiency in SRD data (e.g. ghoul has
    # none; use one that does, e.g. "lich" is not seeded at low CR — pick
    # from data/srd: "giant-toad" has none either; use e.g. "gladiator"
    # (STR/DEX/CON saves) if present in the seeded DB, else skip via a
    # direct-record unit test on the modifier helper.

def test_monster_save_refuses_player_value(ctx, party):
    # player_value=15 on a monster save -> ok=False (dice audit rule).
```

If no seeded monster carries a save proficiency, test the helper directly in `tests/rules/` style instead — do not fabricate SRD data.

- [ ] **Step 2: Verify failure** — currently "no character named 'bandit-1'".

- [ ] **Step 3: Implement.** In `saving_throw`, replace the `char is None` refusal with a monster branch:

```python
    char = ctx.store.get_character(character)
    if char is None:
        combat = ctx.store.combat()
        if combat["active"]:
            combatant, ambiguous = find_combatant(combat["combatants"], character)
            if ambiguous:
                return refuse(
                    "saving_throw", ambiguous_combatant_refusal(character, ambiguous)
                )
            if combatant is not None and combatant["kind"] == "monster":
                return _monster_saving_throw(
                    ctx, combatant, ability, dc,
                    advantage=advantage, disadvantage=disadvantage,
                    player_value=player_value, gm_only=gm_only,
                )
        return refuse("saving_throw", f"no character named {character!r}")
```

And add:

```python
def _monster_save_modifier(record, ability: str) -> int:
    """SRD save proficiency total if listed, else the bare ability mod."""
    for prof in (record.model_extra or {}).get("proficiencies", []):
        if prof.get("proficiency", {}).get("index") == f"saving-throw-{ability}":
            return int(prof["value"])
    return ability_modifier(record.ability_scores[_FULL_ABILITY[ability]])


def _monster_saving_throw(
    ctx, combatant, ability, dc, *, advantage, disadvantage, player_value, gm_only
):
    ability = _normalize_ability(ability)
    if ability not in _ABILITIES:
        return refuse(
            "saving_throw", f"unknown ability {ability!r} (valid abilities: {_VALID_ABILITIES})"
        )
    if dc < 1:
        return refuse("saving_throw", f"dc must be >= 1 (got {dc})")
    if player_value is not None:
        return refuse(
            "saving_throw", "player_value is only for the PC's own dice — "
            "monster saves always roll in the engine"
        )
    record = ctx.rules.get_monster(combatant["monster_slug"])
    modifier = _monster_save_modifier(record, ability)
    effects = effects_for(combatant.get("conditions", []), 0)
    if effects.auto_fail_str_dex_saves and ability in ("str", "dex"):
        ...same auto-fail data/digest shape as the character path, with the
        combatant key as the name...
    mode = combine_advantage(
        advantage,
        disadvantage or effects.saves_have_disadvantage
        or (ability == "dex" and effects.dex_saves_have_disadvantage),
    )
    check = resolve_check(ctx.roller, modifier, dc, mode, player_value=None, gm_only=gm_only)
    ...same data/digest shape as the character path...
```

Check the actual name of `ability_scores` keys ("str" vs "strength") — `MonsterRecord.ability_scores` returns short keys ("str": …), so `_FULL_ABILITY` is unnecessary; use `record.ability_scores[ability]`. Extract the shared tail (data dict + digest) from the character path into a small helper if it avoids duplication; don't over-refactor.

- [ ] **Step 4: Run** — `uv run pytest tests/commands/test_checks.py -x -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: monster saving throws (TVA-56)"`

---

### Task 7: TVA-58 — travel time required in schema; attack_name defaults when unambiguous

**Files:**
- Modify: `src/dm_engine/commands/world.py:30-49` (`travel`)
- Modify: `src/dm_engine/commands/attacks.py` (the `attack_name (or attack_names) is required` refusal site — find with `rg -n "attack_names\) is required" src/`)
- Test: `tests/commands/test_world.py` (create if absent — check `ls tests/commands/`), `tests/commands/test_attacks.py`, `tests/test_mcp_schema.py`

**Interfaces:** none consumed/produced beyond the two handlers.

- [ ] **Step 1: Write failing tests**

```python
def test_travel_requires_hours_in_schema():
    # Introspect the MCP tool schema for travel (reuse the pattern in
    # tests/test_mcp_schema.py): "hours" is in required[].

def test_attack_name_defaults_when_single_attack(ctx, party):
    # Aldric has exactly one attack (mace). In combat on his turn,
    # attack(attacker="Brother Aldric", target="bandit-1") with NO
    # attack_name -> ok=True, data["attack_name"] == "mace".

def test_attack_name_still_required_when_multiple(ctx, party):
    # Give a character two attacks at creation; omitting attack_name ->
    # ok=False and the refusal lists the available attack names.
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement travel** — signature `def travel(ctx, destination_slug: str, hours: int, days: int = 0, **kwargs)`. Keep the positive-total refusal (hours=0, days=2 stays legal). MCP introspection now marks `hours` required.

- [ ] **Step 4: Implement attack_name default** — at the refusal site, when the attacker's resolved attack list has exactly one entry, use it instead of refusing; when several, extend the refusal to enumerate them: `f"attack_name is required — {attacker} has: {', '.join(names)}"`.

- [ ] **Step 5: Run** — the three test files above → PASS. `uv run pytest tests/integration -x -q` (travel appears in integration flows — update any call that omitted hours; those were exercising the bug).

- [ ] **Step 6: Commit** — `git commit -m "chore: travel/attack_name refusal ergonomics (TVA-58)"`

---

### Task 8: TVA-57 (scoped) — surface riders on damaging attacks + non-attack actions

Scope note: full Tier-1 auto-resolution of rider damage dice stays open on TVA-57 (needs its own spec). This task ships the surfacing half: riders on damaging attacks appear in attack results, and named non-attack actions get an actionable refusal instead of "has no attack named".

**Files:**
- Modify: `src/dm_engine/commands/attacks.py` — `_monster_attack_spec` (line ~192), `_resolve_swing` (line ~373-377), and the unknown-attack-name refusal site
- Test: `tests/commands/test_attacks.py`

**Interfaces:**
- Consumes: `_hit_rider(desc)` (attacks.py:97) — already extracts post-"Hit:" text, SRD condition mentions, escape DC.
- Produces: swing `data["on_hit"]` may now coexist with `data["damage"]`; refusal text for non-attack actions includes the action description.

- [ ] **Step 1: Write failing tests**

```python
def test_damaging_attack_with_rider_surfaces_on_hit(ctx, party):
    # start_combat with giant-toad (SRD bite: damage + poison + grapple text).
    # A hitting bite -> data["damage"] is populated AND data["on_hit"] is the
    # structured rider (conditions/escape_dc/text present).

def test_plain_damage_attack_has_no_on_hit(ctx, party):
    # bandit scimitar hit -> data["on_hit"] is None/absent (no false riders).

def test_non_attack_action_refusal_names_the_action(ctx, party):
    # attack(attacker="giant-toad-1", attack_name="Swallow", ...) ->
    # ok=False; refusal contains "Swallow", "not a weapon attack", the first
    # sentence of the action's desc, and points at roll_dice + dm_ruling.
```

Check the seeded SRD giant-toad action shape first (`uv run dm lookup monster giant-toad` or the vendored JSON) — pick assertion details from real data, don't guess.

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement spec change** — in `_monster_attack_spec`, replace `"on_hit": None if dmg else _hit_rider(desc)` with:

```python
        "on_hit": _rider_if_present(desc, has_damage=dmg is not None),
```

```python
def _rider_if_present(desc: str, *, has_damage: bool) -> dict | None:
    """Structured rider payload. Damage-less attacks always surface their
    rider (TVA-22). Damaging attacks surface one only when the Hit text
    carries mechanics beyond the damage roll — a DC or a condition name —
    so plain weapon attacks stay rider-free (TVA-57)."""
    rider = _hit_rider(desc)
    if not has_damage:
        return rider
    if rider.get("escape_dc") or rider.get("conditions") or "DC" in rider.get("text", ""):
        return rider
    return None
```

(Match `_hit_rider`'s real key names — read it fully first.) In `_resolve_swing`, after the damage block (line ~413), add:

```python
    if spec["on_hit"]:
        data["on_hit"] = spec["on_hit"]
```

and extend `_swing_digest`'s hit line with `" (rider: needs ruling)"` when a damaging swing carries `on_hit`.

- [ ] **Step 4: Implement non-attack refusal** — at the unknown-attack-name site, before refusing, look up the monster record's `actions` (model_extra) for a case-insensitive name match without an `attack_bonus`; if found:

```python
        return refuse(
            "attack",
            f"{name!r} is not a weapon attack — it is a stat-block action: "
            f"\"{first_sentence}\" Resolve it via roll_dice + dm_ruling "
            f"(or apply_condition for its conditions).",
        )
```

- [ ] **Step 5: Run** — `uv run pytest tests/commands/test_attacks.py -x -q` → PASS.

- [ ] **Step 6: Commit** — `git commit -m "feat: surface attack riders and stat-block actions (TVA-57)"`

---

### Task 9: TVA-59 — dm-session skill: next_turn after improvised turns; coin convention

**Files:**
- Modify: `.claude/skills/dm-session/SKILL.md`
- Test: none (prose). Verify by reading the diff in context.

- [ ] **Step 1: Add the initiative rule.** In the combat-loop section (near line 194, "THEN `next_turn` for the next actor"), add:

```markdown
- A `dm_ruling` or `roll_dice` never ends anyone's turn. When you improvise
  a combatant's action that way (an unmodeled feature, a rider, a lair
  action), call `next_turn` explicitly afterward — the initiative pointer
  only moves when you move it.
```

- [ ] **Step 2: Add the coin convention.** New short subsection near the inventory/items guidance (or after the rulings section if none exists):

```markdown
### Coin

The engine has no currency field — coin is a counted inventory item named
exactly `gold pieces`. Establish a starting purse once, via `dm_ruling`
(note the rationale) + `add_item`; spend with `remove_item`. If a
`remove_item` for payment is refused, the payment did NOT happen — narrate
the shortfall or barter instead; never narrate a refused payment as paid.
```

- [ ] **Step 3: Sanity-check placement** — `rg -n "next_turn|gold pieces" .claude/skills/dm-session/SKILL.md` shows both additions in sensible sections; the file's existing voice/format is matched.

- [ ] **Step 4: Commit** — `git commit -m "docs: dm-session initiative + coin guidance (TVA-59)"`

---

### Task 10: TVA-60 — done_when matchers (inputs, result paths, refusal_contains)

**Files:**
- Modify: `evals/metrics.py:22-64` (`beat_done`, `classify_beat_failure`)
- Test: `tests/evals/test_metrics.py` (check `ls tests/evals/` for the real filename; extend it)

**Interfaces:**
- Produces: done_when dicts accept optional `inputs: {key: value}`, `result: {json.path: value}`, `refusal_contains: str`. Task 11's YAML uses all three.

- [ ] **Step 1: Write failing tests** (against a scratch sqlite event_log with hand-inserted rows, following the existing test file's pattern):

```python
def test_beat_done_inputs_matcher():
    # rows: cast_spell ok=1 inputs {"spell_slug": "cure-wounds"}, then
    # cast_spell ok=1 inputs {"spell_slug": "bless"}.
    # done_when {command: cast_spell, ok: True, inputs: {spell_slug: bless}}
    # -> True; with inputs {spell_slug: "hold-person"} -> False.

def test_beat_done_refusal_contains():
    # rows: attack ok=0 refusal "it is not Kira's turn ...", attack ok=0
    # refusal "Longsword (5 ft) cannot reach a target at far — ...".
    # done_when {command: attack, ok: False, refusal_contains: "cannot reach"}
    # -> True; refusal_contains "no such text" -> False.

def test_beat_done_result_path_matcher():
    # row: cast_spell ok=1 result data.needs_ruling=true.
    # done_when {..., result: {"data.needs_ruling": 1}} -> True.

def test_classify_uses_same_matchers():
    # With only the not-Kira's-turn refusal row, done_when refusal_contains
    # "cannot reach": classify -> reason "refused" surfacing the last
    # attempt (command attempted, criteria unmet) — and never "not_attempted".
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement:**

```python
def _matcher_clauses(done_when: dict) -> tuple[str, list]:
    """Extra SQL predicates for the optional done_when matchers."""
    clauses: list[str] = []
    params: list = []
    for key, val in (done_when.get("inputs") or {}).items():
        clauses.append(f"AND json_extract(inputs, '$.{key}') = ?")
        params.append(val)
    for path, val in (done_when.get("result") or {}).items():
        clauses.append(f"AND json_extract(result, '$.{path}') = ?")
        params.append(val)
    if done_when.get("refusal_contains"):
        clauses.append("AND json_extract(result, '$.refusal') LIKE ?")
        params.append(f"%{done_when['refusal_contains']}%")
    return " ".join(clauses), params


def beat_done(db_path: Path, done_when: dict, *, after_id: int) -> bool:
    ok = 1 if done_when.get("ok", True) else 0
    extra_sql, extra_params = _matcher_clauses(done_when)
    with _connect(db_path) as db:
        row = db.execute(
            "SELECT COUNT(*) FROM event_log WHERE id > ? AND command = ?"
            f" AND json_extract(result, '$.ok') = ? {extra_sql}",
            (after_id, done_when["command"], ok, *extra_params),
        ).fetchone()
    return row[0] > 0
```

`classify_beat_failure` keeps its two reasons but must stay consistent: "not_attempted" = zero rows for the command; "refused" = rows exist but none satisfied `beat_done` (which now includes the matchers) — its current logic already implies that since it's only called when beat_done is False; just update its docstring to say criteria, not `ok`, and surface the last attempt as before. Matcher keys come from checked-in YAML (trusted), so f-string JSON paths are fine — say so in a comment.

- [ ] **Step 4: Run** — `uv run pytest tests/evals -x -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(evals): done_when inputs/result/refusal matchers (TVA-60)"`

---

### Task 11: TVA-61 — caravan_ambush rework: seeded purse, mid-combat illegal-action, hardened scripts

**Files:**
- Modify: `evals/scenario.py:47-84` (`build_campaign` — party items)
- Modify: `evals/scenarios/caravan_ambush.yaml`
- Test: `tests/evals/` (build test), plus YAML review

**Interfaces:**
- Consumes: Task 10's matchers; `add_item` command.
- Produces: scenario YAML party members may carry `items: [{item, quantity}]`.

- [ ] **Step 1: Write failing test**

```python
def test_build_campaign_seeds_party_items(tmp_path, rules_path):
    # Minimal Scenario whose party member has
    #   items=[{"item": "gold pieces", "quantity": 60}].
    # build_campaign -> the campaign store shows Kira holding 60x gold pieces,
    # and an add_item row exists in event_log (audited seeding).
```

- [ ] **Step 2: Verify failure** — `create_character` swallows `items` via `**kwargs` today; the inventory stays empty.

- [ ] **Step 3: Implement in `build_campaign`:**

```python
        for member in scenario.party:
            member = dict(member)
            items = member.pop("items", [])
            result = registry.execute("create_character", ctx, **member)
            if not result.ok:
                raise RuntimeError(
                    f"create_character failed for {member.get('name')!r}: "
                    f"{result.refusal}"
                )
            for item_spec in items:
                r = registry.execute(
                    "add_item", ctx, character=member["name"], **item_spec
                )
                if not r.ok:
                    raise RuntimeError(
                        f"add_item failed for {member['name']!r}: {r.refusal}"
                    )
```

- [ ] **Step 4: Rework the YAML.** Apply exactly:

1. Under Kira's party entry add:

```yaml
    items:
      - {item: gold pieces, quantity: 60}
```

2. `buy-supplies`: `max_player_messages: 5`; `done_when: {command: add_item, ok: true, inputs: {character: Kira}}`.
3. Reorder beats: `question-innkeeper`, `buy-supplies`, `travel-to-ambush`, `investigate-wreckage`, `fight-bandits`, `illegal-action`, `tier1-heal`, `tier2-spell`, `wrap-up` (illegal-action moves to mid-combat, right after the fight starts).
4. `fight-bandits` goal seeds the archer:

```yaml
  - id: fight-bandits
    goal: Bandits attack — two rush you with blades while a third with a
      crossbow hangs back far across the marsh, out of anyone's reach.
      Fight them. Engage the nearest bandit in melee with your longsword.
    notes: If asked for initiative report 12. If asked for an attack roll
      report 18, damage 7. On later attacks say "roll for me".
    max_player_messages: 8
    done_when: {command: attack, ok: true}
```

5. `illegal-action` hardened:

```yaml
  - id: illegal-action
    goal: Insist on swinging your longsword at the far-off crossbowman from
      exactly where you stand. Do not move, do not close the distance, do
      not throw the sword. If the DM narrates you closing in or suggests
      anything else, repeat that you stay put and swing, and ask for the
      attack roll to be resolved as declared.
    notes: This is deliberately impossible — you want the table ruling, not
      a workaround. Refuse every alternative the DM offers.
    max_player_messages: 5
    done_when: {command: attack, ok: false, refusal_contains: "cannot reach"}
```

6. `tier2-spell`: `max_player_messages: 6`; add note "If Aldric defers or heals instead, insist again on his next turn: the party needs Bless specifically."; `done_when: {command: cast_spell, ok: true, inputs: {spell_slug: bless}}`.
   (Decision: no separate follow-up dm_ruling beat — the ruling lands inside this beat's window before the next marker, so a dedicated beat would start after it and read as failed. The existing `orphaned_tier2` metric already covers a missing ruling.)
7. Leave `question-innkeeper`, `travel-to-ambush`, `investigate-wreckage`, `tier1-heal`, `wrap-up` unchanged.

- [ ] **Step 5: Validate** — `uv run pytest tests/evals -x -q` → PASS, and load-check the YAML: `uv run python -c "from pathlib import Path; from evals.scenario import load_scenario; s=load_scenario(Path('evals/scenarios/caravan_ambush.yaml')); print([b.id for b in s.beats])"` prints the new order.

- [ ] **Step 6: Commit** — `git commit -m "feat(evals): scenario rework — purse, mid-combat illegal beat (TVA-61)"`

---

### Task 12: Docs sweep + full verification

**Files:**
- Modify: `README.md` (new `stabilize` command, travel's required hours, rider surfacing — only where the README documents the command surface), `docs/SCHEMA.md` only if it enumerates dm_ruling ops (check: `rg -n "adjust_hp" docs/SCHEMA.md`).

- [ ] **Step 1:** Update README/SCHEMA command listings for: `stabilize`, new dm_ruling ops, travel signature, cast_spell spend default, monster saving throws. Match existing doc style; touch nothing else.
- [ ] **Step 2:** Full gate: `uv run pytest -q` → all green; `uv run ruff check src tests evals` → clean.
- [ ] **Step 3: Commit** — `git commit -m "docs: README/SCHEMA for eval-retro fixes"`

---

## Self-Review Notes

- Spec coverage: TVA-51→T1, 52→T2, 53→T3, 54→T4, 55→T5, 56→T6, 57(scoped)→T8, 58→T7, 59→T9, 60→T10, 61→T11, docs→T12. TVA-57's auto-resolve half deliberately deferred — update the ticket after merge.
- Task 3 depends on Tasks 1-2 (transition semantics); Task 4 on Task 2 (`set_combatant_defeated`); Task 11 on Task 10 (matchers). Execute in order.
- Type consistency: `set_combatant_defeated(ctx, character: str, defeated: bool)` used in T2/T4; `DeathSaveState(stable=True)`/`(dead=True)` per rules/death.py:22-23; done_when matcher keys `inputs`/`result`/`refusal_contains` shared T10/T11.
