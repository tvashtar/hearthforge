# roll_dice: arbitrary audited dice — design

**Date:** 2026-07-10
**Status:** approved (brainstormed with user; player-values reuse question resolved)

## Problem

`dm_ruling` resolves effects the engine doesn't model, but when a ruling
needs dice (sleep's 5d8 HP pool while `cast_spell` was broken; improvised
trap damage; roll-4d6-drop-lowest at campaign creation), there is no engine
command to roll them. In session 1 the DM fell back to out-of-engine RNG
(`python secrets` in a shell). That broke two FC-2/FC-6 guarantees:

1. **Replay determinism** — the roll didn't come from the campaign's seeded
   RNG, so replaying the seed cannot reproduce it.
2. **Audit trail** — the dice exist only as prose inside the ruling's
   rationale; the event log's `rolls` column is empty and `dm audit` cannot
   show them.

The same hole exists for *player-rolled* arbitrary dice: reported values
land in ruling prose instead of being logged as `player_supplied` Rolls.

## Decision

One new registry command, `roll_dice`, in `commands/rulings.py` (companion
to the `dm_ruling` escape hatch). Auto-exposed as an MCP tool by the
existing signature introspection; no MCP/CLI changes.

### Signature

```python
@command("roll_dice")
def roll_dice(
    ctx: CommandContext,
    count: int,
    sides: int,
    reason: str,
    gm_only: bool = False,
    player_values: list[int] | None = None,
) -> CommandResult
```

`count`/`sides` (not notation strings) per user decision: trivially valid
schema, impossible to mis-parse; modifiers belong in the ruling, not the
roll.

### Validation (refusals, never exceptions)

- `count` outside 1–100 → refuse
- `sides` outside 2–1000 → refuse
- `reason` empty/blank → refuse (same rule as `dm_ruling.rationale`:
  an arbitrary roll with no stated purpose is an audit hole)
- `player_values` given but `len != count` → refuse
- any player value outside `1..sides` → refuse

### Resolution

- **Engine path** (no `player_values`): one
  `ctx.roller.roll(f"{count}d{sides}", gm_only=gm_only)` call — the
  campaign's seeded RNG, so the Roll (individual dice included) lands in
  the event log automatically and replay determinism holds.
- **Player path** (`player_values` given): FC-2's `DiceRoller` protocol is
  frozen and takes a single `player_value` per roll, so the command
  decomposes into `count` calls of `1d{sides}`, one supplied value each.
  Each is logged as a `player_supplied=True` Roll. Same envelope shape out.

### Result envelope

```
data = {count, sides, rolls: [...], total, reason,
        player_supplied: bool}
digest = 'DM rolls 5d8 → [1, 8, 7, 6, 5] = 27 (sleep HP pool)'
         ('Player rolls …' on the player path)
gm_only = as passed (hidden rolls stay behind the screen)
```

### What roll_dice does NOT do

- No modifiers, no drop-lowest, no rerolls — composition happens in the
  ruling that consumes the numbers.
- No character binding, and therefore no enforcement of *whose* dice may be
  player-supplied. FC-2's etiquette (companion/monster dice are never
  player-supplied) is unenforceable without a character; the judgment stays
  where `gm_only` judgment already lives: the dm-session skill.

### dm-session skill edit

One line added to the dice-etiquette section: arbitrary ruling dice for the
PC may pass `player_values`; all other arbitrary dice are engine-rolled.

## Testing

In `tests/commands/test_rulings.py` (same fixtures as existing command
tests):

- engine path: rolls list has `count` entries, each in `1..sides`, total
  is their sum; result echoes reason; envelope `gm_only` mirrors the flag
- determinism: same seed → same rolls across two fresh contexts
- player path: values echoed verbatim, total correct,
  `player_supplied: true` in data, per-die Rolls flagged in the event row
- refusals: empty reason, count 0/101, sides 1, length mismatch,
  out-of-range player value — all `ok=False`, nothing logged as a roll
- event log: the command's row carries the Roll(s) in its `rolls` column
