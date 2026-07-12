# Codex Campaign and Evaluation Support — Design

**Date:** 2026-07-12  
**Status:** Proposed design, pre-implementation  
**Branch:** `conorlaver/codex-eval-support-spec`

## Summary

Hearthforge's model-evaluation harness currently assumes Claude at all three
model boundaries:

1. the Dungeon Master is a persistent `ClaudeSDKClient` session;
2. the scripted player is a one-shot Anthropic completion; and
3. the blind judge is an Anthropic structured-output completion.

This design makes those boundaries provider-neutral and adds Codex as a
first-class provider. A user logged into Codex with ChatGPT can then run a
live campaign or the complete evaluation pipeline—including smoke tests,
scripted player turns, and judging—without an Anthropic account or API key.

The initial Codex transport is the installed `codex exec` CLI. That is the
only local interface verified to share the user's Codex/ChatGPT login and to
accept exact model slugs such as `gpt-5.6-luna`. The design does not treat a
Codex model as a Claude alias and does not require an OpenAI API key when the
CLI login is available.

## Goals

- Run the DM campaign loop with either Claude or Codex while preserving the
  same dm-engine MCP authority, scenario, beat predicates, transcripts, and
  metrics.
- Run the scripted player and blind judge through either provider so a
  Codex-only smoke test has no hidden Anthropic dependency.
- Support exact Codex model slugs and reasoning efforts, beginning with
  `gpt-5.6-luna`.
- Preserve apples-to-apples comparison: normalized transcripts and timing
  bundles must have the same provider-independent shape.
- Keep Claude behavior and existing CLI invocations backward compatible.
- Use the user's existing provider login where possible and perform an
  explicit credential preflight before creating scratch campaigns.
- Make live Codex-driven campaign sessions and eval-driven sessions use the
  same adapter rather than growing two implementations.

## Non-goals

- Replacing dm-engine's MCP protocol or allowing a model to resolve mechanics
  outside the engine.
- Making model quality comparable across runs with different player or judge
  configurations without recording that difference.
- Implementing a generic agent framework for arbitrary providers.
- Supporting the OpenAI API in the first increment. The provider interface
  leaves room for a Responses API transport later, but ChatGPT-authenticated
  Codex CLI support is the immediate requirement.
- Reproducing every Codex interactive UI feature in the eval harness.
- Changing the scenario, rubric, mechanical metrics, or campaign rules.
- Running Codex and Claude with identical hidden system prompts; semantic
  parity is required, byte-for-byte provider prompt parity is not possible.

## Current constraints

### Claude coupling

- `evals/runner.py` constructs `ClaudeAgentOptions`, waits on Claude MCP
  status, streams Claude SDK message classes, and maintains one SDK client
  across turns.
- `evals/cells.py` recognizes only `haiku`, `sonnet`, `opus`, and `fable`.
- `evals/player.py` fixes the player to `claude-haiku-4-5`.
- `evals/judge.py` fixes the judge to `claude-opus-4-8` and accepts an
  Anthropic client.
- `evals/llm.py` selects between the Anthropic API and Claude Agent SDK only.
- `--smoke` implicitly means Haiku and still invokes the Claude player and
  judge.

### Codex CLI properties used by this design

The installed CLI exposes the required primitives:

- `codex exec --json` for machine-readable event streaming;
- `codex exec resume <session>` for persistent multi-turn sessions;
- `--model <slug>` for an exact model selection;
- `-c model_reasoning_effort=<value>` for effort configuration;
- `-C <repo>` for working-directory control;
- `--output-schema <file>` for structured one-shot judge output;
- normal Codex MCP configuration and the user's ChatGPT login.

Event schemas and resume semantics must be verified in the first implementation
spike and frozen behind fixtures before the production adapter is written.

## Design principles

1. **Provider is explicit.** A bare family alias keeps its current Claude
   meaning, while new cells use `provider/model` identifiers.
2. **dm-engine remains authoritative.** Every mechanical resolution still
   comes from the same MCP server and campaign SQLite database.
3. **Normalize at the edge.** Provider-specific events are translated once;
   the runner, metrics, report, and beat logic consume neutral events.
4. **No silent fallback.** A requested Codex cell never falls back to Claude,
   and a failed Codex login never produces a synthetic model run.
5. **Credentials are preflighted.** Failure occurs before campaign creation
   and is reported as an authentication/configuration error, not a handshake
   failure.
6. **Comparison provenance is complete.** DM, player, and judge provider,
   requested model, resolved model, effort, CLI version, and transport are
   recorded in every run.

## Provider-neutral model references

Introduce a value object rather than passing model strings throughout the
harness:

```python
@dataclass(frozen=True)
class ModelRef:
    provider: Literal["anthropic", "codex"]
    model: str
    effort: str = "medium"

    @property
    def id(self) -> str:
        return f"{self.provider}/{self.model}"
```

Accepted cell syntax:

```text
haiku:medium                         # legacy alias; anthropic/haiku
anthropic/opus:high
codex/gpt-5.6-luna:medium
codex/gpt-5.6-sol:high
```

Exact Codex slugs are intentionally not hard-coded into an allowlist. The
installed Codex client is the source of truth and returns a clear error when a
slug is unavailable. Provider names and effort values remain validated.

Ordering uses a configured presentation rank for known aliases, followed by
input order for exact model slugs. It must not claim that cross-provider model
ability has an objective total order.

## Core interfaces

### Persistent DM session

```python
class DMSession(Protocol):
    async def start(self) -> SessionMetadata: ...
    async def turn(self, player_message: str) -> TurnResult: ...
    async def close(self) -> None: ...

@dataclass
class TurnResult:
    narration: list[str]
    events: list[NormalizedEvent]
    timing: TurnTiming

@dataclass
class SessionMetadata:
    provider: str
    transport: str
    requested_model: str
    resolved_model: str | None
    effort: str
    client_version: str | None
    session_id: str | None
```

`ClaudeDMSession` wraps today's SDK behavior. `CodexDMSession` owns the Codex
subprocess/session lifecycle. The existing beat loop calls only this protocol.

### One-shot completion

```python
class CompletionProvider(Protocol):
    def complete(
        self,
        model: ModelRef,
        system: str,
        user: str,
        *,
        max_tokens: int,
        output_schema: dict | None = None,
    ) -> CompletionResult: ...
```

The scripted player and judge use this interface. Claude retains API/SDK
fallback behavior. Codex uses an ephemeral `codex exec` process and, for the
judge, a temporary JSON Schema passed with `--output-schema`.

### Normalized events

```python
NormalizedEvent = (
    AssistantText
    | ToolCall
    | ToolResult
    | Usage
    | ProviderDiagnostic
)
```

Required neutral fields:

- assistant text;
- tool call id, MCP tool name, and structured input;
- tool result id, structured/text content, and error flag;
- input, cached-input, and output token counts when supplied;
- provider error category and safe message;
- session and resolved-model metadata.

The transcript file remains compatible with current metric readers:
`player_message`, `dm_text`, `tool_call`, and `tool_result` entries do not
change. New provider metadata is additive.

## Codex DM transport

### Process lifecycle

1. Preflight `codex login status`, `codex --version`, and model availability
   with a no-inference command if the CLI exposes one. Do not decode or log
   tokens.
2. Start the first turn with `codex exec --json`, the exact model and effort,
   repo cwd, deterministic instructions, and dm-engine MCP enabled.
3. Read JSONL incrementally, capturing the Codex session/thread id and
   normalized events.
4. Send later turns with `codex exec resume <session-id> --json` and the same
   model/effort constraints.
5. Apply the existing per-turn and whole-run timeouts to subprocesses. On
   timeout, terminate the process group and preserve all events received.
6. Close the logical session and allow the existing campaign bundle cleanup
   to run.

### Deterministic configuration

Eval sessions must not inherit personal prompt guidance, unrelated MCP
servers, or repository instructions that differ from the evaluated
`dm-session` skill. The adapter builds an isolated temporary `CODEX_HOME`
containing only:

- a reference/copy of the authenticated Codex credential material using the
  least-privileged supported mechanism;
- a minimal `config.toml` with the dm-engine MCP server;
- the selected model and effort;
- no plugins, unrelated MCP servers, or global instructions.

If Codex cannot safely reuse ChatGPT authentication from an isolated home,
the fallback is the normal home plus explicit config overrides. That fallback
must be marked in metadata because it weakens isolation.

The evaluated skill text is injected directly into the initial instruction,
as it is today for Claude. The adapter must not rely on Codex discovering the
Claude skill directory.

### Tool restrictions

Claude currently disables built-in tools while leaving dm-engine MCP tools
available. Codex parity requires the implementation spike to establish a
supported allowlist or policy that prevents shell/file/network tools from
being used during evals.

This is a release gate. If the installed Codex surface cannot expose only the
dm-engine MCP tools, the adapter may run live campaigns but must not be
advertised as producing comparable eval scores. A model that can inspect or
mutate campaign SQLite directly would invalidate the benchmark.

### MCP readiness

The Claude adapter retains its explicit `get_mcp_status()` readiness barrier.
For Codex, the preferred solution is an app-server/exec event confirming the
dm-engine server and tool inventory before inference. If `codex exec` provides
no readiness event, use a deterministic preflight that starts the configured
MCP server and validates its tool list before the first model turn. Do not
return to timing-based sleeps.

## Live campaign support

Add a thin command using the same `DMSession` adapter:

```text
uv run dm play <campaign-slug> \
  --provider codex \
  --model gpt-5.6-luna \
  --effort medium
```

Responsibilities:

- open a persistent provider session;
- instruct the DM to call `open_campaign` for the requested slug;
- verify the open event in SQLite before accepting player input;
- relay terminal input and narration until EOF/quit;
- preserve dm-engine audit events and normal session checkpoints;
- show provider errors as terminal diagnostics rather than in-fiction text.

This command does not replace direct Codex interactive use. It provides a
reproducible, provider-neutral launcher and proves the same adapter used by
evals works for an actual campaign.

## Eval CLI

Proposed commands:

```text
# Existing behavior remains valid
uv run dm-eval --smoke
uv run dm-eval --cells opus:high,sonnet:low

# Codex DM with explicitly selected supporting models
uv run dm-eval \
  --cells codex/gpt-5.6-luna:medium \
  --player codex/gpt-5.6-luna:low \
  --judge codex/gpt-5.6-sol:high

# Fully Codex-backed two-beat wiring check
uv run dm-eval --smoke \
  --smoke-cell codex/gpt-5.6-luna:medium \
  --player codex/gpt-5.6-luna:low \
  --judge codex/gpt-5.6-sol:high

# Skip expensive narrative judging when testing wiring only
uv run dm-eval --smoke \
  --smoke-cell codex/gpt-5.6-luna:medium \
  --player codex/gpt-5.6-luna:low \
  --no-judge
```

Defaults remain today's Claude matrix/player/judge until a separate decision
changes them. `--smoke` remains backward compatible; `--smoke-cell` overrides
its Haiku default.

`--no-judge` produces mechanical metrics and a clearly marked unjudged report.
It is useful for authentication, MCP, and transcript wiring tests and avoids
requiring a second expensive model merely to prove the runner works.

## Authentication and preflight

Each selected provider implements:

```python
class ProviderPreflight(Protocol):
    def check(self, roles: set[Literal["dm", "player", "judge"]]) -> PreflightResult: ...
```

Codex preflight reports:

- login mode (`ChatGPT` or API key), without token/account secrets;
- CLI version;
- requested model and effort;
- MCP configuration/readiness;
- whether eval tool isolation is enforceable.

Anthropic preflight reports API credentials or Claude login availability and
detects account-limit responses as `quota_exceeded`, not `handshake_failed`.

All preflights run before `build_campaign()`. This prevents scratch campaigns
and misleading reports when credentials or quota are already known to be
unusable.

## Errors and result semantics

Replace free-form `RunResult.error` construction with categorized failures:

- `auth_missing`
- `quota_exceeded`
- `model_unavailable`
- `mcp_start_failed`
- `mcp_not_ready`
- `tool_isolation_unavailable`
- `provider_protocol_error`
- `turn_timeout`
- `run_timeout`
- `handshake_failed`
- `judge_failed`

The safe provider message is retained for diagnosis. Reports distinguish
infrastructure failure from model behavior; authentication and quota failures
must never count as DM handshake failures.

## Bundle and report changes

Add `provider.json` to each bundle:

```json
{
  "dm": {
    "provider": "codex",
    "transport": "codex-cli",
    "requested_model": "gpt-5.6-luna",
    "resolved_model": "gpt-5.6-luna",
    "effort": "medium",
    "client_version": "..."
  },
  "player": {"provider": "codex", "requested_model": "gpt-5.6-luna"},
  "judge": {"provider": "codex", "requested_model": "gpt-5.6-sol"}
}
```

Report changes:

- separate provider and model columns;
- identify player and judge configurations in the header;
- flag mixed-provider runs;
- flag missing token metrics rather than displaying zero;
- group infrastructure failures separately from incomplete gameplay;
- retain blind anonymization for both provider names and exact model ids.

## Proposed module layout

```text
evals/
  providers/
    __init__.py
    base.py              # protocols, ModelRef, neutral events/errors
    anthropic.py         # extracted current SDK/API behavior
    codex.py             # subprocess JSONL and resume adapter
    codex_events.py      # strict event parser/normalizer
    preflight.py
  runner.py              # provider-neutral campaign beat loop
  player.py              # provider-neutral one-shot player
  judge.py               # provider-neutral structured judge
  cells.py               # provider/model parsing
  run.py                 # CLI selection and orchestration
src/dm_engine/cli/
  play.py                # live campaign adapter launcher
tests/evals/providers/
  fixtures/              # captured, scrubbed provider event streams
```

## Testing strategy

### Unit tests—no model calls

- Parse legacy and provider-qualified cell specs.
- Preserve user ordering across incomparable providers.
- Parse scrubbed Codex JSONL fixtures for text, tool calls, tool results,
  usage, session id, model metadata, refusal, and process error.
- Verify transcript normalization is identical for equivalent Claude and
  Codex fixture conversations.
- Verify exact model/effort CLI arguments and safe subprocess construction.
- Verify resume uses the captured session id.
- Verify timeouts terminate the process group and preserve partial output.
- Verify player prompts are provider-independent.
- Verify judge schema validation and one retry for both providers.
- Verify anonymization removes provider aliases and exact model ids.
- Verify auth/quota errors are not classified as handshake failures.
- Verify no credential/token value is written to transcript or provider
  metadata.

### Integration tests—local subprocesses only

- Fake `codex` executable emitting fixture JSONL across initial and resumed
  turns.
- Fake dm-engine MCP readiness and startup failures.
- Full two-beat runner with fake Codex DM/player/judge and real SQLite beat
  predicates.
- Existing Claude tests remain unchanged until extracted behind the adapter;
  then equivalent contract tests run against both adapters.

### Manual smoke tests

1. `--no-judge` Codex-only two-beat smoke.
2. Codex DM + Codex player + Codex judge smoke.
3. One live `dm play` open/turn/checkpoint session.
4. Mixed Claude/Codex matrix proving shared reports and anonymization.

Manual tests are never required in CI.

## Delivery phases

### Phase 0 — protocol and isolation spike

- Capture scrubbed `codex exec --json` output for one text turn, one MCP tool
  call, one resume turn, one model error, and one quota/auth error.
- Verify exact session-id and resolved-model fields.
- Verify MCP readiness signal.
- Prove built-in tool restriction to dm-engine only.
- Decide whether CLI transport is valid for comparable evals. If tool
  restriction fails, evaluate Codex app-server as the transport before
  continuing.

**Exit gate:** frozen event fixtures plus demonstrated tool isolation.

### Phase 1 — provider model and Claude extraction

- Add neutral types/protocols and cell parsing.
- Move existing Claude behavior behind adapters without behavior changes.
- Keep every current test and CLI invocation green.

### Phase 2 — Codex one-shot player/judge

- Add ephemeral completion and structured-output support.
- Add provider selection and preflight.
- Enable Codex-only player and judge tests with fake subprocesses.

### Phase 3 — persistent Codex DM

- Implement JSONL process/resume lifecycle and normalized transcripts.
- Add Codex cell selection, metadata, categorized errors, and `--no-judge`.
- Run the two-beat Codex smoke.

### Phase 4 — live campaign launcher

- Add `dm play` using the same persistent adapter.
- Verify open handshake, terminal relay, checkpointing, and clean shutdown.

### Phase 5 — comparison hardening

- Mixed-provider report formatting and anonymization.
- Repeated runs, timeout cleanup, quota handling, documentation, and full
  manual smoke matrix.

## Acceptance criteria

- `uv run dm-eval --smoke --smoke-cell codex/gpt-5.6-luna:medium
  --player codex/gpt-5.6-luna:low --no-judge` completes two beats without
  invoking Anthropic.
- A fully Codex-backed smoke with a Codex judge produces mechanical metrics
  and validated narrative scores.
- A Codex DM can call every required dm-engine MCP tool, and successful tool
  results appear in `transcript.jsonl` with the current neutral shape.
- Multi-turn Codex sessions retain conversation state through the campaign.
- A missing login, unavailable model, or quota failure is detected before
  scratch campaign creation and accurately categorized.
- Eval-mode Codex sessions cannot use shell, filesystem, web, or unrelated MCP
  tools.
- Existing Claude commands and all LLM-free tests remain backward compatible.
- `uv run dm play <slug> --provider codex --model gpt-5.6-luna` opens an
  existing campaign, completes at least one player/DM exchange, and records
  the normal dm-engine audit events.
- Every bundle records complete DM/player/judge provenance without secrets.

## Risks and decisions requiring validation

### Codex event protocol stability

CLI JSON events may change between Codex releases. Strict parsing should
accept known additive fields, reject missing required fields with a protocol
error, and record the CLI version. Fixture tests pin the versions observed.

### Tool isolation

This is the largest benchmark-validity risk. Live play can tolerate the normal
Codex sandbox, but scored evals cannot permit direct file/SQLite access. Tool
isolation is therefore a Phase 0 release gate, not a follow-up improvement.

### ChatGPT authentication reuse

Copying or symlinking credential files into temporary homes may be unsupported
or unsafe. Prefer a documented credential/config override. Never copy tokens
into run bundles, logs, prompts, or subprocess arguments.

### Effort parity

Provider effort names do not promise equal compute. Reports compare named
configurations and record exact effort; they must not imply that Claude
`medium` and Codex `medium` are equivalent budgets.

### Player and judge choice

Changing the player or judge changes the experiment. Results are comparable
only when those configurations match. Reports make both prominent and should
warn when aggregating bundles with different supporting models.

### CLI latency

Spawning/resuming a CLI process each turn adds transport overhead. Record both
end-to-end wall time and provider-reported inference time when available.
Model comparisons should use end-to-end time for user experience and annotate
transport differences.

## Documentation changes at implementation time

- Update README model-eval examples and credential guidance.
- Document Codex ChatGPT login and exact-model selection.
- Document `dm play` and provider-specific limitations.
- Add troubleshooting for login, quota, model availability, MCP readiness,
  and tool-isolation failures.
- State clearly which smoke modes make paid/model calls.

## Recommended implementation sequence

Begin with Phase 0 only. Do not refactor the current harness until Codex JSON
events, resume behavior, MCP readiness, and eval-safe tool isolation have been
demonstrated. Those four findings determine whether `codex exec` is the right
transport or whether the adapter should target Codex app-server instead.
