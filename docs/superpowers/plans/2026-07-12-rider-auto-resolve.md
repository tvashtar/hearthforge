# Auto-Resolve Monster Rider Damage Dice (TVA-63) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Auto-resolve a monster attack's unconditional secondary damage dice (e.g. the giant-toad bite's "plus 5 (1d10) poison damage", dragons' elemental riders) at Tier 1 — rolled through `ctx.roller`, mitigated per type, applied, audited — instead of forcing the DM to hand-roll them via `roll_dice` + `dm_ruling`.

**Architecture:** The SRD structured data already lists secondary damage as extra entries in an action's `damage` array (`[{piercing 1d10+2}, {poison 1d10}]`); the current code only reads `damage[0]`. This plan adds the *unconditional* secondary entries to the monster attack spec and resolves them alongside primary damage in `_resolve_swing`. Save-gated extra damage and condition/grapple/swallow riders keep the already-shipped `on_hit` / `needs_ruling` surfacing (TVA-57 part 1) — they are NOT auto-applied.

**Tech Stack:** Python 3.12, uv, pytest. No new dependencies.

## Global Constraints

- FC-1..FC-7 frozen. This is Tier-1 automation consistent with FC-7's tiered-spell philosophy applied to attacks — resolvable mechanics resolve fully; anything needing judgment stays a ruling.
- Refusals not exceptions; every die through `ctx.roller`; monster dice never accept `player_value`.
- ruff line length 100. Tests in `tests/commands/test_attacks.py` (+ `tests/rules/` if a pure helper warrants it).
- Use real seeded SRD monsters in tests — NO fabricated data. Validated auto-resolve cases: giant-toad Bite (poison 1d10), adult-red-dragon Bite (fire 2d6), ankheg Bite (acid 1d6). Validated stay-as-ruling cases: assassin Shortsword (save-gated poison 7d6), aboleth Tentacle (save-gated acid 1d12).
- Commit per task, conventional commits. This lands on branch `conorlaver/eval-retro-fixes` (PR #27). Commit trailer:
  ```
  Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01BjSPDPJp9uRGjNR6p9BJYY
  ```

## The auto-resolve boundary (validated against all 67 multi-damage SRD attacks)

A secondary `damage` entry (index >= 1) with dice `D` and type `T` **auto-resolves (Tier 1)** iff the action `desc` contains an unconditional introduction of it — regex (case-insensitive): `plus\s+\d+\s*\(D\)\s+T\s+damage` — AND it is not save-gated (no `taking\s+\d+\s*\(D\)` construct and no "saving throw ... D"). Otherwise it stays Tier 2 (existing `on_hit`/`needs_ruling` surfacing; NOT auto-applied). Empirically: this classifies giant-toad/dragon/ankheg/bone-devil/mummy/remorhaz/salamander riders AUTO and assassin/aboleth save-gated riders RULING. Conservative default: if unmatched, do NOT auto-apply (under-automating is safe; dealing unowed damage is not).

Crit rule (5e RAW): secondary damage dice are part of the attack's damage and DOUBLE on a crit — pass the same `critical` flag used for primary damage. Mitigation: each rider is mitigated against the target's resistance/vulnerability/immunity to ITS OWN damage type (a fire-resistant target halves a fire rider but not the piercing primary).

---

### Task 1: Parse unconditional secondary damage into the monster attack spec

**Files:**
- Modify: `src/dm_engine/commands/attacks.py` — add `_bonus_damage_riders(action)`, extend the monster branch of `_resolve_attack_spec` (~lines 206-236) and the character branch (~line 174-184) to carry a `bonus_damage` key.
- Test: `tests/commands/test_attacks.py`

**Interfaces:**
- Produces: attack spec dict gains `"bonus_damage": list[dict]`, each `{"damage_notation": str, "damage_type": str}`. Character specs always get `[]`. Task 2 consumes this.

- [ ] **Step 1: Write the failing test**

```python
def test_bonus_damage_riders_parses_unconditional_secondary(ctx, rules_path):
    # Use RulesDB directly to build the spec for giant-toad Bite and assert
    # the spec's bonus_damage == [{"damage_notation": "1d10", "damage_type": "poison"}].
    # And adult-red-dragon Bite -> [{"1d10"? check data}] fire 2d6.
    # Build via _resolve_attack_spec against a combatant, OR unit-test
    # _bonus_damage_riders(action_dict) directly with the real seeded action.

def test_bonus_damage_riders_excludes_save_gated(ctx, rules_path):
    # assassin Shortsword action -> _bonus_damage_riders == [] (the 7d6 poison
    # is save-gated "taking ... on a failed save", not "plus ... damage").
    # aboleth Tentacle -> [] (DC-gated acid).
```

Read `_hit_rider`/`_resolve_attack_spec` first to match how actions are fetched (`record.model_extra["actions"]`). Prefer unit-testing `_bonus_damage_riders(action)` directly against the real seeded action dict (fetch via `RulesDB(rules_path).get_monster("giant-toad").model_extra["actions"]`) — that isolates the classifier cleanly.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/commands/test_attacks.py -k bonus_damage -x -q` → FAIL (`_bonus_damage_riders` undefined).

- [ ] **Step 3: Implement the parser**

```python
def _bonus_damage_riders(action: dict) -> list[dict]:
    """Unconditional secondary damage entries that auto-resolve at Tier 1.

    SRD lists secondary damage as extra `damage` entries; only those the desc
    introduces as "plus N (dice) <type> damage" (never save-gated) are applied
    automatically. Save-gated / conditional extra damage stays DM-adjudicated
    (surfaced via the on_hit rider)."""
    desc = action.get("desc", "")
    riders: list[dict] = []
    for entry in (action.get("damage") or [])[1:]:
        dice = entry.get("damage_dice")
        dtype = (entry.get("damage_type") or {}).get("index")
        if not dice or not dtype:
            continue
        unconditional = re.search(
            rf"plus\s+\d+\s*\({re.escape(dice)}\)\s+{dtype}\s+damage", desc, re.I
        )
        gated = re.search(rf"taking\s+\d+\s*\({re.escape(dice)}\)", desc, re.I) or \
            re.search(rf"saving throw.{{0,80}}?{re.escape(dice)}", desc, re.I)
        if unconditional and not gated:
            riders.append({"damage_notation": dice, "damage_type": dtype})
    return riders
```

- [ ] **Step 4: Wire it into both spec branches** — in the monster return dict (attacks.py ~226-236) add `"bonus_damage": _bonus_damage_riders(action),`. In the character return dict (~174-184) add `"bonus_damage": [],` (PCs carry no SRD riders in this model).

- [ ] **Step 5: Run** — `uv run pytest tests/commands/test_attacks.py -k bonus_damage -x -q` → PASS.

- [ ] **Step 6: Commit** — `git commit -m "feat: parse unconditional secondary damage riders (TVA-63)"`

---

### Task 2: Apply bonus-damage riders in _resolve_swing

**Files:**
- Modify: `src/dm_engine/commands/attacks.py` — `_resolve_swing` (~lines 373-413), `_swing_digest` (~line 443+)
- Test: `tests/commands/test_attacks.py`

**Interfaces:**
- Consumes: `spec["bonus_damage"]` from Task 1; `roll_damage`, `apply_mitigation`, `apply_damage_to_target`, `_monster_defense_sets` (all already in the file).
- Produces: swing `data["bonus_damage"] = [{"raw", "final", "type", "applied"}]` when riders fired; digest gains a `" +N poison"` style tail.

- [ ] **Step 1: Write the failing test**

```python
def test_giant_toad_bite_auto_applies_poison_rider(ctx, party):
    # start_combat with a giant-toad; force a hit (AC low / retry-until-hit
    # like the existing _land_hit helper). Assert the hit's data has both
    # data["damage"] (piercing) AND data["bonus_damage"] a list with a poison
    # entry (raw>0, final>0, type=="poison"), and that the target's HP dropped
    # by primary+bonus. on_hit (grapple/restrained rider) still present.

def test_plain_attack_has_no_bonus_damage(ctx, party):
    # bandit scimitar hit -> data["bonus_damage"] absent or []. No false rider.

def test_bonus_rider_doubles_on_crit(ctx, party):
    # Force a crit (player-side is PC only; for a monster attacker use a seeded
    # roller offset that crits, or assert via a monster whose crit is
    # reproducible). Assert the poison rider's raw reflects doubled dice.
    # If deterministic crit forcing is impractical, assert the rider passes
    # critical through by checking dice count in a unit test of the roll path
    # instead — do NOT fabricate.

def test_fire_resistant_target_halves_only_the_rider(ctx, party):
    # Optional if a seeded resistant PC/target is available; otherwise skip
    # with a comment. Do not fabricate resistances.
```

Reuse the existing hit-forcing helper in the file (the TVA-57 tests used a `_land_hit`/retry-until-hit pattern — find and reuse it). Assert store HP delta, not just the data dict.

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/commands/test_attacks.py -k "bonus or rider" -x -q` → FAIL (`data["bonus_damage"]` absent).

- [ ] **Step 3: Implement.** In `_resolve_swing`, after the primary damage block applies and sets `data["target"] = fragment["target"]` (attacks.py ~406), and only on a hit with primary damage, add:

```python
    bonus = []
    for rider in spec.get("bonus_damage", []):
        rdmg = roll_damage(
            ctx.roller, rider["damage_notation"], critical=critical, player_value=None
        )
        rtype = rider["damage_type"]
        if tgt["kind"] == "monster":
            record = ctx.rules.get_monster(tgt["monster_slug"])
            r_res, r_vuln, r_imm = _monster_defense_sets(
                record, rtype, is_magical=spec["magical"]
            )
        else:
            tres = ctx.store.get_resources(tgt["character_id"])
            petr = effects_for(tres["conditions"], tres.get("exhaustion", 0)).resist_all_damage
            r_res = {rtype} if petr else set()
            r_vuln = set(); r_imm = set()
        rmit = apply_mitigation(
            rdmg.total, rtype, resistances=r_res, vulnerabilities=r_vuln, immunities=r_imm
        )
        frag = apply_damage_to_target(ctx, tgt["key"], rmit.final, rtype, critical=critical)
        data["target"] = frag["target"]  # keep latest hp/status
        if frag.get("defeated"):
            data["defeated"] = True
        if frag.get("concentration_broken"):
            data["concentration_broken"] = True
        bonus.append({"raw": rdmg.total, "final": rmit.final,
                      "type": rtype, "applied": rmit.applied})
    if bonus:
        data["bonus_damage"] = bonus
```

Place this AFTER the primary `apply_damage_to_target` and its concentration/defeat propagation, so the primary drop is recorded first and the rider stacks onto the already-lowered HP. (A rider can itself drop/kill the target — the `data["target"]`/`defeated`/`concentration_broken` refresh above handles that.)

- [ ] **Step 4: Extend the digest** — in `_swing_digest`, when `swing.get("bonus_damage")`, append `" " + ", ".join(f"+{b['final']} {b['type']}" for b in swing["bonus_damage"])` to the hit line (before the drop tail). Keep it terse.

- [ ] **Step 5: Run** — `uv run pytest tests/commands/test_attacks.py -x -q` → PASS. Then full `uv run pytest -q` (watch the existing giant-toad TVA-57 surfacing tests — they asserted `on_hit` present; they should STILL pass, since the grapple rider stays in `on_hit`; only the poison moves to `bonus_damage`. If a TVA-57 test asserted the poison appears in `on_hit`, that was surfacing a now-auto-resolved rider — update it to assert `bonus_damage` and say so in the report).

- [ ] **Step 6: Commit** — `git commit -m "feat: auto-resolve unconditional attack damage riders (TVA-63)"`

---

### Task 3: Docs + full gate

**Files:** `README.md` / `docs/SCHEMA.md` only if either documents attack result shape or tiering (check: `rg -n "on_hit|bonus_damage|rider|Tier" README.md docs/SCHEMA.md`); the dm-session skill (`.claude/skills/dm-session/SKILL.md`) if it describes how riders are handled during combat.

- [ ] **Step 1:** If the dm-session skill tells the DM to hand-resolve rider damage, update it: unconditional secondary damage now auto-applies (visible as `bonus_damage` in the attack result); the DM only rules on conditions/grapple/save-gated riders surfaced via `on_hit`. If no such guidance exists, note that and skip. Do not invent doc sections.
- [ ] **Step 2:** Full gate — `uv run pytest -q` all green; `uv run ruff check src tests evals` clean.
- [ ] **Step 3: Commit** (only if docs changed) — `git commit -m "docs: rider auto-resolution (TVA-63)"`

---

## Self-Review Notes

- Boundary validated on all 67 multi-damage SRD attacks: "plus ... damage" ⇒ AUTO, save-gated ⇒ RULING. giant-toad poison AUTO (motivating case), assassin/aboleth RULING (correctness-critical exclusions).
- Task 2 depends on Task 1's `bonus_damage` spec key.
- Interaction with TVA-57 (already on this branch): `on_hit` continues to carry condition/grapple/save-gated riders; only unconditional secondary DAMAGE moves to `bonus_damage`. A single attack (giant-toad bite) legitimately has both.
- Crit doubling and per-type mitigation are the two easy-to-miss correctness points — both have test coverage in Task 2.
