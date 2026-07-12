# Codex DM Sessions — Focused Design

**Date:** 2026-07-12  
**Status:** Proposed, DM-only scope
**Branch:** `conorlaver/codex-eval-support-spec`

## Outcome

Hearthforge can already run a live campaign from Codex after adding a
project-scoped MCP registration. GPT-5.6 Luna successfully discovered the
`dm-engine` server and started a campaign; the first test also showed that
tool calls were available without being blocked.

The remaining work is reliability and usability around live DM sessions. It
does not include Codex-backed evals, a provider abstraction, a scripted
player, a judge, or a model-comparison matrix.

## Current working path

The committed project configuration is:

```toml
[mcp_servers.dm-engine]
command = "uv"
args = ["run", "dm", "mcp"]
```

It lives at `.codex/config.toml`. Codex loads it for this trusted repository.
The MCP server is local; campaign data remains in Hearthforge SQLite files.

Manual launch:

```bash
cd /Users/conor/repos/hearthforge
codex -m gpt-5.6-luna
```

The player then asks Codex to open a campaign. The existing
`.claude/skills/dm-session/SKILL.md` remains the source of truth for DM
behavior and engine-first rules.

## Goals

- Make Codex a reliable live DM client for existing and new campaigns.
- Preserve the engine as the authority for rules, dice, state, and audit.
- Ensure Codex opens the campaign before narrating anything.
- Reduce character-creation errors and make refusals actionable.
- Keep the launch path short and discoverable for a human player.
- Keep Claude Code behavior unchanged.
- Add only lightweight tests and documentation appropriate to a live-session
  integration.

## Non-goals

- No Codex support in `uv run dm-eval`.
- No Claude/Codex provider abstraction.
- No Codex scripted player or blind judge.
- No eval transcript normalization, timing bundles, or score comparison.
- No `codex exec` orchestration or custom persistent-session wrapper.
- No automatic campaign launcher required for the first release.
- No changes to campaign rules or the scenario/eval harness.

## Remaining changes

### 1. Codex-facing project guidance

Add a small project-facing instruction file or section that tells Codex:

- use the `dm-engine` MCP server for every mechanical operation;
- read and follow `.claude/skills/dm-session/SKILL.md` before acting as DM;
- make `open_campaign` or `create_campaign` the first action;
- never write a tool call as prose;
- stop if the engine tools are unavailable;
- treat refusals as steering, not as invitations to invent an outcome;
- never expose `gm_only` data or monster HP;
- persist improvised NPCs, locations, quests, and scene facts;
- use the world clock for every time skip.

Prefer a short Codex-specific pointer over copying the entire skill. Duplication
would drift as the DM skill evolves.

The guidance should include a canonical opening prompt:

```text
Use the dm-engine MCP server and follow .claude/skills/dm-session/SKILL.md.
Your first action must be a real open_campaign or create_campaign tool call;
do not narrate before its result returns.
```

### 2. Character-creation reliability

The first live Luna session completed the campaign flow but made mistakes while
creating characters. Before changing engine behavior, capture the exact
refusal/tool payloads from one or two sessions and classify them:

- invalid class/race/ability input;
- incorrect attack shape;
- incorrectly supplied saving throws;
- invalid skill/tool/language choices;
- malformed spell or companion data;
- narration before the creation command returned;
- failure to recover after an engine refusal.

Then add targeted guidance to the Codex-facing instructions and, only where a
schema is genuinely ambiguous, improve the MCP parameter description or
structured schema. The engine should continue rejecting invalid mechanics;
the model should learn to correct its input rather than bypassing the refusal.

The character-creation checklist should explicitly require:

1. interview the player for concept, ability-score method, skills, equipment,
   companion preferences, and death mode;
2. call `create_campaign` before creating characters in a new campaign;
3. use `create_character` with engine-recognized class/race/ability fields;
4. provide only choices, not derived saving throws or computed statistics;
5. use the documented attack shape and valid skill slugs;
6. inspect the returned sheet/result before narrating the finished character;
7. treat a refusal as a correction loop and retry with the valid shape.

### 3. MCP server instructions

Expose concise server-wide MCP `instructions` during initialization. These
should cover the cross-tool rules that must be visible even when Codex does
not load a skill file:

- engine-first mechanics;
- first-call campaign opening gate;
- refusal recovery;
- hidden-result and monster-HP secrecy;
- persistence of improvised facts.

Keep the first 512 characters self-contained, because Codex uses the server's
initialization instructions when deciding how to use its tools.

This is complementary to the project guidance: MCP instructions describe the
server's invariant contract; the skill describes the full DM procedure and
narrative style.

### 4. Launch and troubleshooting documentation

Update README with:

```bash
uv sync
codex mcp list
codex -m gpt-5.6-luna
```

Document the opening prompt, approval behavior, trusted-project requirement,
and the expected recovery steps when `dm-engine` is missing. Explain that
`uv run dm resume <slug>` opens a campaign through the engine CLI but does not
launch a model; Codex is the interactive DM client.

Add a short troubleshooting section:

- `dm-engine` missing: launch Codex from the repo and verify `.codex/config.toml`;
- server startup failure: run `uv run dm mcp` directly and inspect stderr;
- character refusal: read the refusal and retry the documented schema;
- stale scene/time: trust the returned clock and reconcile with
  `advance_clock` before narration;
- permission prompt: approve the intended gameplay MCP tool in interactive
  Codex, or configure only the specific gameplay tools the user accepts.

## Approval policy

The current project config registers the server and may contain per-tool
approval settings. The default policy should remain explicit and conservative:

- read-only lookup and campaign-opening tools can be preapproved;
- mutation tools such as `create_character`, `create_campaign`, inventory,
  combat, and rulings should be approved according to the user's preference;
- never grant Codex broad shell/file authority merely to make the game work.

The configuration must not silently auto-approve every server tool without a
deliberate security decision. Interactive campaign play is the target, so a
user approval prompt is acceptable.

## Testing

No LLM calls are required in CI. Add lightweight checks:

### Static/config checks

- `.codex/config.toml` parses and contains the `dm-engine` stdio server;
- command and args are exactly `uv run dm mcp`;
- README launch instructions reference the project config and `uv`.

### Engine smoke test

Keep the existing MCP smoke test. It verifies server startup and registry
exposure but does not prove Codex can consume the project configuration.

### Manual Codex smoke test

From the trusted repo:

```bash
codex -m gpt-5.6-luna
```

Ask Codex to open `the-fading-of-liraeth`. Verify that:

- `dm-engine` appears in `/mcp` or `codex mcp list`;
- the first model action is a real `open_campaign` call;
- the returned brief is used for the first narration;
- one read-only lookup and one intended gameplay mutation complete through
  MCP;
- no direct SQLite/file manipulation is used for mechanics.

Record failures as a DM-session retro, not as eval scores.

## Acceptance criteria

- A clean trusted checkout discovers `dm-engine` from `.codex/config.toml`.
- GPT-5.6 Luna can open an existing campaign through the MCP tool.
- GPT-5.6 Luna can create a new campaign and character using valid engine
  schemas after the documented interview.
- Invalid character input produces a clear correction loop rather than an
  invented character or silently accepted mechanics.
- Codex does not narrate campaign facts before the opening tool result.
- One normal gameplay exchange persists through the engine audit log.
- The README describes the launch and troubleshooting path.
- All existing engine and Claude tests remain unchanged and passing.

## Delivery order

1. Capture the exact character-creation mistakes from the observed Codex
   session.
2. Add concise Codex-facing guidance and MCP server instructions.
3. Add config/README checks and update the manual smoke procedure.
4. Run one fresh Codex campaign smoke manually.
5. Review the session transcript for any remaining refusal or schema friction.

The project MCP configuration is already implemented. The remaining code
should be limited to guidance, MCP metadata, documentation, and targeted
schema clarity—not a new runtime or evaluation framework.
