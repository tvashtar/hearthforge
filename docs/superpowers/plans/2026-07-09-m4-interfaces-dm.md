# M4 — Interfaces & DM Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The MCP server exposing the command registry 1:1, the `dm-session` DM-brain skill, `.mcp.json` wiring, CLI play commands, README quickstart, and the full Phase-1 Goal Gate e2e suite.

**Architecture:** `dm_engine.mcp.server` wraps the M3 registry over stdio using the low-level `mcp.server.Server` API: tools are generated dynamically from `registered_commands()` (JSON schemas introspected from handler signatures), plus two lifecycle tools (`create_campaign` wrapping `bootstrap_campaign`, `open_campaign` wrapping `open_campaign_context`) that set the server's active `CommandContext`. Every tool call returns the FC-1 envelope serialized verbatim. The `dm-session` skill is the Phase-1 DM persona/procedure document. The Goal Gate e2e tests exercise the engine exactly as the LLM will — through `registry.execute` only, fixed seeds, no LLM calls.

**Tech Stack:** Python ≥3.12, `mcp` (official Python SDK, stdio transport), anyio (pytest plugin for the async smoke test), typer, pytest.

## Global Constraints

- Branch: all M4 work on `feat/m4-interfaces-dm`; never commit to `main`; never push.
- FC-1: MCP and CLI serialize `CommandResult` **verbatim** (`model_dump_json()`); FC-3: MCP tools and `dm cmd` are thin 1:1 adapters over `registry.execute` — no game logic in the adapters.
- MCP campaign lifecycle: the server holds ONE active `CommandContext`; `create_campaign`/`open_campaign` set it; every other tool returns an FC-1-shaped refusal (`ok=False`, `refusal="no campaign open — call create_campaign or open_campaign first"`) when none is open. `create_campaign` is NOT a registry command (M3 decision) — its MCP tool calls `dm_engine.commands.campaign.bootstrap_campaign`.
- After any handler exception the server must rebuild the context via `open_campaign_context` before the next call (M3 handoff: in-memory roller drifts from the rolled-back DB).
- Goal Gate tests live in `tests/integration/`, drive the engine through `registry.execute` ONLY (direct store/sqlite access for assertions and reading; `dm_ruling` with rationale `"test scripting"` is the sanctioned way to force specific state).
- Phase 1 is done when: `scripts/sync_srd.py` → `uv run dm seed` → `uv run pytest` all green from a clean checkout, and `.claude/skills/dm-session/SKILL.md` + `.mcp.json` exist.
- Conventional commits <50 chars; `uv run pytest` + `uv run ruff check .` before every commit.

## File Map

```
src/dm_engine/mcp/__init__.py
src/dm_engine/mcp/server.py         # low-level mcp Server: dynamic tools over the registry
src/dm_engine/cli/app.py            # + dm mcp, dm new, dm resume
src/dm_engine/commands/checks.py    # + monster-combatant skill checks (enemy stealth)
.mcp.json
.claude/skills/dm-session/SKILL.md
README.md                           # quickstart
tests/integration/test_mcp_smoke.py
tests/integration/test_e2e_campaign_lifecycle.py
tests/integration/test_e2e_combat_goblin_ambush.py
tests/integration/test_e2e_death_modes.py
tests/integration/test_e2e_spell_tiers.py
tests/integration/test_e2e_resume_rehydration.py
tests/integration/test_e2e_audit_and_integrity.py
```

---

### Task 1: MCP server + smoke test

**Files:**
- Create: `src/dm_engine/mcp/__init__.py` (empty), `src/dm_engine/mcp/server.py`, `.mcp.json`
- Modify: `pyproject.toml` (add `mcp>=1.0` to dependencies; `anyio` is already transitive — add `trio`? NO: use the anyio pytest plugin with the asyncio backend only), `src/dm_engine/cli/app.py` (add `dm mcp`)
- Test: `tests/integration/test_mcp_smoke.py`

**Interfaces:**
- Consumes: `registered_commands()`, `execute`, `open_campaign_context` (registry), `bootstrap_campaign` (campaign module), FC-1 envelope.
- Produces (frozen): `build_server(campaigns_dir: Path, rules_db_path: Path) -> mcp.server.Server` and `run_stdio(campaigns_dir, rules_db_path)` (async main); CLI `dm mcp [--campaigns-dir campaigns] [--db data/build/rules.sqlite]` runs it on stdio.

**Design contract (implementer writes the code to this):**
- Use the low-level `mcp.server.Server` (NOT FastMCP): `@server.list_tools()` returns one `mcp.types.Tool` per registered command PLUS `create_campaign` and `open_campaign`; `@server.call_tool()` dispatches. This keeps 1:1 dynamism and full schema control.
- Tool schemas: introspect each handler with `inspect.signature`, dropping the `ctx` parameter and `**kwargs` catch-alls. Map annotations to JSON schema: `str→string`, `int→integer`, `float→number`, `bool→boolean`, `list/list[...]→array`, `dict/dict[...]→object`, `X | None→` same type, not required. Parameters without defaults are `required`. First line of the handler docstring = tool description (fall back to the command name).
- `create_campaign` tool: params `slug, name, death_mode="narrative", skeleton (object), starting_region (object|None), seed (integer|None)`; calls `bootstrap_campaign`, stores the returned context as the active one, returns the synthetic result: `{"ok": true, "command": "create_campaign", "digest": "Campaign <name> created", ...}` as an FC-1 envelope (build a real `CommandResult` and dump it).
- `open_campaign` tool: params `slug`; calls `open_campaign_context` (snapshot side effect = session start), sets active context, returns an FC-1 envelope whose `data` is `get_campaign_brief`'s data (execute it) — one call rehydrates.
- Every other tool: refuse (FC-1 shape) when no context; otherwise `execute(name, ctx, **arguments)` and return `[TextContent(type="text", text=result.model_dump_json())]`.
- Handler exceptions: catch in `call_tool`, rebuild the context via `open_campaign_context` (same slug), and re-raise as an MCP tool error (the exception is an engine bug — visible, not swallowed; the rebuilt context keeps the session usable).
- `.mcp.json` at repo root:
  ```json
  {
    "mcpServers": {
      "dm-engine": {
        "command": "uv",
        "args": ["run", "dm", "mcp"]
      }
    }
  }
  ```
- `dm mcp` CLI: `anyio.run(run_stdio, campaigns_dir, db)` (asyncio backend).

**Binding smoke test** (verbatim; satisfies the roadmap's `test_mcp_smoke`):

```python
# tests/integration/test_mcp_smoke.py
"""MCP smoke test: server starts on stdio, exposes the registry 1:1, and
round-trips commands with verbatim FC-1 envelopes."""

import json
import sys

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dm_engine.commands.registry import registered_commands


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_smoke(tmp_path, rules_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "dm_engine.mcp", "--campaigns-dir", str(tmp_path / "campaigns"),
              "--db", str(rules_path)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = {t.name for t in (await session.list_tools()).tools}
            expected = set(registered_commands()) | {"create_campaign", "open_campaign"}
            assert tools == expected  # 1:1, nothing missing, nothing extra

            # refusal before a campaign is open — still an FC-1 envelope
            early = await session.call_tool("lookup_rule", {"query": "grappling"})
            envelope = json.loads(early.content[0].text)
            assert envelope["ok"] is False and "no campaign open" in envelope["refusal"]

            result = await session.call_tool("create_campaign", {
                "slug": "smoke", "name": "Smoke Test", "death_mode": "narrative",
                "skeleton": {"premise": "smoke"}, "seed": 7,
            })
            envelope = json.loads(result.content[0].text)
            assert envelope["ok"] is True

            result = await session.call_tool("create_character", {
                "name": "Kira", "role": "pc", "class_slug": "fighter",
                "race_slug": "human",
                "abilities": {"str": 16, "dex": 14, "con": 14, "int": 10,
                              "wis": 12, "cha": 8},
                "ac": 16,
                "proficiencies": {"skills": ["athletics"], "saves": ["str", "con"]},
                "attacks": [],
            })
            assert json.loads(result.content[0].text)["ok"] is True

            check = await session.call_tool("skill_check", {
                "character": "Kira", "skill": "athletics", "dc": 10,
                "player_value": 15,
            })
            envelope = json.loads(check.content[0].text)
            # verbatim FC-1 envelope
            assert set(envelope) == {"ok", "command", "refusal", "digest", "data",
                                     "gm_only", "event_ids"}
            assert envelope["ok"] is True and envelope["command"] == "skill_check"
            assert envelope["data"]["total"] == 20  # 15 + STR 3 + prof 2

            rule = await session.call_tool("lookup_rule", {"query": "grappling"})
            envelope = json.loads(rule.content[0].text)
            assert envelope["ok"] is True and envelope["data"]["hits"]
```

Note: the module must be runnable as `python -m dm_engine.mcp` — add a `__main__.py` (or `__main__` guard in `__init__.py`) that parses `--campaigns-dir`/`--db` with argparse and calls `run_stdio`. The smoke test uses `sys.executable -m` so it runs inside uv's venv without PATH games.

- [ ] Step 1: create branch `feat/m4-interfaces-dm`; add `mcp>=1.0` dependency, `uv sync`
- [ ] Step 2: binding smoke test (RED — module not found)
- [ ] Step 3: implement server + `__main__` + `dm mcp` CLI + `.mcp.json` (GREEN)
- [ ] Step 4: full suite + ruff; commit `feat: add MCP server over the registry`

---

### Task 2: Play-surface polish — `dm new`, `dm resume`, monster skill checks

**Files:**
- Modify: `src/dm_engine/cli/app.py`, `src/dm_engine/commands/checks.py`
- Test: extend `tests/test_cli.py`, `tests/commands/test_checks.py`

**Interfaces / contract:**
- `dm new <slug> --name TEXT [--death-mode narrative|hardcore] [--seed INT] [--campaigns-dir campaigns] [--db data/build/rules.sqlite]` — calls `bootstrap_campaign` with a minimal skeleton `{"premise": "<name> (created via CLI; skeleton to be written by the DM)"}`; prints the FC-1 envelope-style confirmation JSON. Exit 1 if the slug exists.
- `dm resume <slug> [--campaigns-dir] [--db]` — `open_campaign_context` (snapshot side effect) then `execute("get_campaign_brief", ctx)` and print `result.model_dump_json(indent=2)`.
- `skill_check` extension (checks.py): when `character` names no character BUT matches a combatant key in an active combat whose `kind == "monster"`, resolve the check for the monster: ability from `SKILL_ABILITIES`, modifier = `ability_modifier(record ability)` + proficiency parsed from the monster record's `proficiencies` list (entries like `{"proficiency": {"index": "skill-stealth"}, "value": 6}` — when present, use the record's `value` as the TOTAL modifier instead of computing). `player_value` refused for monsters. Engine-rolled; `gm_only` kwarg works as for characters (this enables the goal-gate hidden enemy stealth check: `skill_check(character="goblin-2", skill="stealth", dc=..., gm_only=True)`). Refusal when the name matches nothing: keep the existing text.
- Binding tests:

```python
# append to tests/commands/test_checks.py
def test_monster_stealth_check_gm_only(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="goblin-1",
                              skill="stealth", dc=12, gm_only=True)
    assert result.ok, result.refusal
    assert result.gm_only is True
    assert result.data["modifier"] == 6  # goblin Stealth +6 from the SRD record
    row = ctx.store.conn.execute(
        "SELECT rolls FROM event_log ORDER BY id DESC LIMIT 1").fetchone()
    assert '"gm_only": true' in row["rolls"]


def test_monster_check_refuses_player_value(ctx):
    registry.execute("start_combat", ctx,
                     monsters=[{"slug": "goblin", "count": 1, "band": "near"}],
                     pc_initiative=15)
    result = registry.execute("skill_check", ctx, character="goblin-1",
                              skill="stealth", dc=12, player_value=15)
    assert result.ok is False
```

(These need the `party` fixture — mark the module-level usefixtures already present in the file.)

- CLI tests: `dm new` then `dm resume` round-trip in a tmp dir (assert brief JSON contains the campaign name); `dm new` on an existing slug exits 1.
- [ ] TDD, full suite + ruff, commit `feat: add play CLI and monster checks`

---

### Task 3: dm-session skill + README

**Files:**
- Create: `.claude/skills/dm-session/SKILL.md`
- Modify: `README.md`

No code. The SKILL.md content is specified verbatim below — transcribe exactly. README: replace the stub with a quickstart (see outline after the skill).

**`.claude/skills/dm-session/SKILL.md` (verbatim):**

````markdown
---
name: dm-session
description: Run a D&D 5e (2014 rules) session as the Dungeon Master, using the dm-engine MCP tools for every mechanical resolution. Use when the player wants to start, continue, or manage a campaign.
---

# Dungeon Master Session

You are the Dungeon Master for a solo D&D 5e campaign. The `dm-engine` MCP
server is the complete mechanical game: rules, dice, and state. You are the
narrative brain on top of it. You NEVER compute or record mechanical facts
yourself — you issue commands, the engine validates/rolls/persists/returns,
and you narrate the results.

## Iron rules

1. **The DB is the truth.** On session start call `open_campaign` (or
   `create_campaign` for a new one) and trust its brief over anything you
   remember from conversation. Never trust conversation memory over the DB.
2. **Every mechanical claim comes from a command result.** Hit or miss,
   damage, save, slots, HP, XP, conditions — if you didn't just read it in a
   result, you may not narrate it. No exceptions, no mental math.
3. **Refusals steer you.** `ok=false` means the action is illegal — narrate
   around the reason (`refusal`) or pick a legal action. Never work around a
   refusal by inventing outcomes.
4. **Improvised facts must be persisted or they didn't happen.** A new NPC,
   rumor, location, or quest development goes through `create_npc`,
   `create_location`, `update_quest`, or `set_scene` in the same breath as
   the narration that invents it.
5. **Never reveal `gm_only` material.** Hidden rolls (enemy stealth, monster
   stat blocks from `lookup_monster`, checkpoint recaps) are behind the
   screen — narrate their consequences, not their numbers.

## Dice etiquette

- The player rolls ALL of their PC's dice at the table: d20s (checks, saves,
  attacks, death saves), damage, and hit dice. Prompt for the raw result and
  pass it through the command's `player_value` / `player_attack_value` /
  `player_damage_value` / `pc_initiative` input. Report the raw die total,
  before modifiers — the engine adds those.
- If the player says "/roll" (or asks you to roll), simply omit the player
  value — the engine rolls. Any single roll is delegable.
- Companions and monsters are always engine-rolled: never pass player values
  for them.
- Where the rules imply a DM screen (enemy stealth vs the party, contested
  checks the party shouldn't see), set `gm_only=true` on the command.

## Session procedure

- **Start:** `open_campaign` → read the brief (skeleton, scene, party,
  quests, last recap) → give the player a "previously on…" recap → resume
  the scene. If mid-combat (brief says combat_active), call
  `get_scene_state` and pick up exactly where the initiative order stands.
- **During play:** narrate → when mechanics arise, command → narrate the
  digest. Keep tool payloads out of the narration; the digest line is your
  hook.
- **Checkpoints:** every ~20 events (count your command calls), silently
  call `checkpoint` with a 2-3 sentence mini-recap of the current scene,
  stakes, and party state. This is crash insurance — do not mention it.
- **End:** when the player wraps up, call `end_session` with a recap
  covering: what happened, open threads, where the party stands. Confirm to
  the player that the session is saved.

## Campaign creation (new campaign interview)

Interview the player first — do not generate anything until you know:
1. Tone and themes (grim? heroic? intrigue? dungeon-crawl?), and any hard
   limits (content to avoid).
2. Character concept (class/race/background sketch).
3. Companion preferences (how many of the 1–3, what roles).
4. Death mode: `narrative` (default — defeat has consequences but not
   death) or `hardcore` (opt-in — death is final; a new PC joins the world).
5. Ability scores: player's choice of rolled 4d6-drop-lowest (THEY roll and
   report), standard array (15/14/13/12/10/8), or point buy.

Then generate and persist via `create_campaign`:
- A plot skeleton: premise, a 3-act arc outline, 3–5 factions with goals
  and secrets, an endgame condition.
- A fleshed-out starting region in `starting_region`: a home-base town, 5–10
  NPCs, 3–5 hooks, and a first dungeon — as locations/npcs records.
Everything beyond the starting region is generated lazily when the party
approaches it, and persisted with world-write commands at that moment.

Create the PC with `create_character` (role `"pc"`), then introduce
companions IN FICTION — they are recruited through play, not spawned.

## Companions

- DM-generated to complement the player's build; created with
  `create_character` (role `"companion"`, standard array) once recruited.
- They act autonomously on their personality and tactical doctrine: on
  their combat turns YOU decide their actions and issue their commands
  (engine-rolled). The player may suggest in-fiction; they usually comply.
- They are mortal. If one dies (hardcore) or falls (narrative), it is real;
  replacements emerge through play. Keep the spotlight on the PC.

## Combat procedure

1. Build the encounter: `lookup_monster` for stat blocks (gm_only), then
   `start_combat` with monsters and their starting bands. The result
   includes the advisory difficulty — report it to yourself; you may
   deliberately deviate from a fair fight, but say why in the narration
   (the deviation is logged).
2. Drive turns with `get_scene_state` (whose turn, budgets) → the actor's
   commands (`move`/`engage`/`attack`/`cast_spell`/…) → `next_turn`.
3. Range bands: engaged/near/far/distant. Leaving `engaged` without
   Disengage provokes — the result lists provokers; resolve each as a
   reaction `attack` (spend="reaction").
4. PC at 0 HP: death saves are the player's dice (`death_save` with
   `player_value`). In `narrative` mode a third failure means *defeated,
   not dead* — invent real consequences (capture, loss, rescue at cost).
   In `hardcore` mode death is final: help the player make a new character
   who joins the persistent world (or promote a companion to PC).
5. Victory or resolution: `end_combat` awards XP automatically. Non-combat
   resolutions of an encounter earn its XP via `award_xp` (the encounter's
   full value — cite the reason).

## Spells

- `cast_spell` resolves damage/heal spells mechanically (Tier 1). For
  everything else (Tier 2) it consumes the slot, sets concentration, and
  returns `needs_ruling` with the spell text — resolve the effect yourself
  via `dm_ruling` (with a written rationale) immediately after.
- Concentration checks after damage come back in the attack result
  (`concentration_check.dc`) — prompt the player's CON save (or roll the
  companion's) with `saving_throw`, and `break_concentration` on failure.

## Rulings

`dm_ruling` is the escape hatch for corner cases the engine doesn't model.
Full power, two obligations: a written `rationale` (mandatory — the command
refuses without it), and restraint (prefer engine commands whenever one
fits). Rulings are prominently marked in the audit trail (`dm audit`).

## The character sheet

The engine materializes `campaigns/<slug>/sheets/<character>.md` after every
command — tell the player to keep their sheet open in an editor; it live-
updates. `dm sheet <name> --campaign <slug>` prints it on demand.
````

**README.md quickstart outline (implementer writes prose to this structure):**
1. What this is (one paragraph: engine-first AI DM, Phase 1 = play in Claude Code).
2. Setup: clone → `uv sync` → `uv run python scripts/sync_srd.py` (only to re-vendor; data is committed) → `uv run dm seed`.
3. Verify: `uv run pytest`.
4. Play: open the repo in Claude Code (`.mcp.json` wires the `dm-engine` MCP server automatically), say "start a new campaign" — the `dm-session` skill takes it from there. Keep `campaigns/<slug>/sheets/<you>.md` open while playing.
5. Debug surface: `dm cmd`, `dm audit`, `dm sheet`, `dm lookup`, `dm new`, `dm resume`.
6. Attribution: SRD 5.1 CC-BY-4.0 (link `data/srd/ATTRIBUTION.md`).

- [ ] Transcribe SKILL.md exactly; write README; commit `docs: add dm-session skill and README`

---

### Task 4: Goal Gate e2e — lifecycle, death modes, spell tiers

**Files:**
- Create: `tests/integration/test_e2e_campaign_lifecycle.py`, `tests/integration/test_e2e_death_modes.py`, `tests/integration/test_e2e_spell_tiers.py`

All three drive the engine through `registry.execute` only (bootstrap via `bootstrap_campaign`, reopen via `open_campaign_context` with the root `rules_path` fixture). Assertions below are the roadmap's Goal Gate requirements — every bullet MUST be asserted.

**test_e2e_campaign_lifecycle** (single test function, narrative mode, seed 42):
- create campaign → create PC (point-buy-legal scores, fighter) → recruit companion (standard array 15/14/13/12/10/8, cleric) → assert both sheets exist at `campaigns/<slug>/sheets/`.
- `create_location` + `travel` → world clock advanced.
- `award_xp` 600 with reason ("quest: the missing miller") → PC levels 1→2: level, max_hp increased by 8 (d10+2), sheet shows the new XP and HP max.
- Spend a cleric slot (`cast_spell` cure-wounds on the fighter after a `dm_ruling` scripting some damage) → `rest` long → slots restored (remaining == max), hit dice restored per RAW, sheet reflects restored slots.
- `end_session` with a recap → reopen via `open_campaign_context` → `get_campaign_brief` returns: skeleton premise, scene/clock, party state (levels/hp), open quests, and the recap text.
- Sheet-reflects-mutation assertions at three points minimum (after award_xp, after damage, after rest).

**test_e2e_death_modes** (two tests sharing a helper that runs the same lethal script):
- Script: create campaign (mode parametrized, fixed seed) → PC fighter → `dm_ruling` (rationale "test scripting") sets hp to 0 + unconscious → three failed `death_save`s (player_values 5, 5, 5).
- `narrative`: PC status == "defeated", NOT "dead"; campaign still accepts commands (e.g. `checkpoint` succeeds).
- `hardcore`: status == "dead"; campaign persists and accepts a replacement PC (`create_character` role "pc" succeeds — the dead PC no longer blocks the one-PC rule; if it does, that is a REAL bug to fix in characters.py: the one-PC check must only count characters with status active or defeated... check the actual behavior — `party()` includes defeated but not dead, so a dead PC should not block. Assert it.)
- Both: death-save mechanics identical — assert the death_saves state shows 3 failures in both modes.

**test_e2e_spell_tiers** (seed fixed; party incl. level-3 cleric like the unit fixtures):
- Tier 1 heal: `dm_ruling` scripts the fighter to 3 hp → `cast_spell` cure-wounds → healed via effect record (hp increased by rolled amount), 1st-level slot consumed.
- Tier 1 AoE: `start_combat` 3 goblins at near → `cast_spell` burning-hands (band="near") → exactly 3 targets (15-ft cone cap), each entry shows save DC 12 and half damage (floor) on successful save; goblin hp reduced in combat state.
- Tier 2: `cast_spell` hold-person → slot consumed, concentration set (spell + duration recorded), result `needs_ruling` is True with spell text → follow-up `dm_ruling` (rationale given) applies `set_condition` paralyzed on a goblin → BOTH events audit correctly (the cast event is not a ruling; the dm_ruling event has is_ruling=1 and the rationale).
- Refusal: drain the remaining 2nd-level slots (cast hold-person again after topping targets via dm_ruling as needed, or set slots via dm_ruling adjust_slot) → casting with no slots left returns ok=False with the ordinal refusal text.

- [ ] Write the three test files (they should pass against main's engine — where they expose real engine bugs, dispatch-fix them in this task with a clear commit); full suite + ruff; commit `test: add lifecycle, death-mode, spell-tier e2e`

---

### Task 5: Goal Gate e2e — goblin ambush, resume, audit

**Files:**
- Create: `tests/integration/test_e2e_combat_goblin_ambush.py`, `tests/integration/test_e2e_resume_rehydration.py`, `tests/integration/test_e2e_audit_and_integrity.py`

**test_e2e_combat_goblin_ambush** (fixed seed; the roadmap's scripted fight — the existing `test_combat_headless.py` covers a similar arc but THESE assertions are the gate; keep both files):
- `start_combat` 2 goblins → initiative ordered (totals descending).
- Band legality: companion's shortbow/guiding-bolt legal from `near` (ok=True); PC dagger/longsword attack from `near` refused (ok=False, "reach").
- Opportunity attack: force a goblin engaged with the PC to `move` out without disengage → result lists the PC as provoker → resolve as reaction `attack` (spend="reaction") → assert the reaction was consumed (second reaction refused this round).
- Player-supplied attack roll: PC `attack` with `player_attack_value` → event-log rolls contain `"player_supplied": true`.
- Hidden enemy stealth: `skill_check(character="goblin-2", skill="stealth", dc=11, gm_only=True)` → result gm_only, roll flagged gm_only in the log (Task 2 feature).
- Damage/HP arithmetic vs the known seed: after the first landed engine-rolled goblin attack, assert the PC's hp equals `max_hp - result.data["damage"]["final"]` (exact value read from the result — arithmetic consistency, not magic constants).
- PC drops to 0 (script with dm_ruling if the seed is uncooperative) → death-save sequence (one fail via player_value 4) → healed mid-sequence (cure-wounds) → back to consciousness (hp > 0, unconscious cleared, death saves reset).
- Kill both goblins (player_value 20 crits are legal scripting) → `end_combat` → engine XP award divided (2 goblins = 100 XP, party of 2 → 50 each).
- Event-log completeness: every `registry.execute` call in the script produced exactly one event row (count them).

**test_e2e_resume_rehydration:**
- Bootstrap, party, `checkpoint` with a mini-recap, `start_combat`, two turns of scripted actions (attack + next_turn), and NO end_session. Record: round, turn_index, active key, PC hp, a goblin's hp, PC's remaining budget flags.
- Close the store (ctx.store.close()) — simulating a killed process.
- `open_campaign_context` (fresh) → `get_campaign_brief` shows combat_active True and the checkpoint recap as latest; `get_scene_state` reconstructs: initiative order (same keys in same order), whose turn, remaining action economy (budget flags equal), HP and conditions for both characters and monsters.

**test_e2e_audit_and_integrity:**
- Run a short scripted session (bootstrap → PC → a skill_check with player_value → an engine-rolled companion check → a dm_ruling with rationale → checkpoint).
- Event rows exist for EVERY command (count == number of execute calls; bootstrap adds one).
- `dm_ruling` without rationale is refused (and logs with is_ruling=0).
- `ctx.store.rulings()` lists exactly the one ruling, with its rationale; the CLI `dm audit --campaign <slug> --campaigns-dir <dir>` prints it (CliRunner).
- A snapshot exists: bootstrap does NOT snapshot (creation), so `open_campaign_context` once and assert `campaigns/<slug>/snapshots/*.sqlite` is non-empty.
- Seed replay: read every event's rolls; filter engine rolls (`player_supplied` false); re-create `SeededDiceRoller(seed)` and replay the same notation sequence in order → identical `rolls` lists and totals.

- [ ] Write the three files; fix real engine bugs they surface (separate commits); full suite + ruff; commit `test: add ambush, resume, audit e2e gates`

---

### Task 6: Goal Gate verification & merge

**Files:** none new — verification only.

- [ ] **Step 1: Clean-checkout simulation**

```bash
rm -rf data/build
uv run python scripts/sync_srd.py
uv run dm seed
uv run pytest -q
uv run ruff check .
ls .claude/skills/dm-session/SKILL.md .mcp.json README.md
```
Expected: sync re-vendors (no diff), seed rebuilds `data/build/rules.sqlite` (with class_levels), the ENTIRE suite green including all seven `tests/integration/test_e2e_*` + `test_mcp_smoke`, ruff clean, skill + wiring files present.

- [ ] **Step 2: Merge** `feat/m4-interfaces-dm` into `main` (no push) after the final whole-branch review. Phase 1 complete.
