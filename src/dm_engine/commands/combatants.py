"""Shared combatant-identifier resolution (TVA-38/TVA-39).

Every combat command that takes a combatant identifier from the caller
(attacker/target, the actor of `move`/`engage`, a condition target, a
`dm_ruling` effect-op target...) used to resolve it via an exact,
case-sensitive match on the combatant's `key` alone — duplicated
independently in `attacks.py` and `rulings.py`. In play the caller more
often has the display `name` ("Bandit 3", read off the narration) than the
internal `key` ("bandit-3"), and case varies. This module is the single
place that resolves an identifier against a list of live combatants:

- a live combatant's `key` OR its display `name` matches, case-insensitively;
- an identifier that resolves to more than one distinct combatant (e.g. two
  unlabeled monsters sharing a display name) is never silently guessed at —
  it comes back as an ambiguous match for the caller to refuse with, listing
  the candidates;
- "unknown"/turn-order refusals list the live roster or name the active
  combatant, so the caller can retry in one shot instead of a blind guess.
"""

from __future__ import annotations


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


def describe_combatants(combatants: list[dict]) -> str:
    """Render live combatants for a refusal, e.g. 'Kira, bandit-1 "Fen
    Scout", bandit-2 "Fen Scout 2", Brother Aldric'. A combatant whose key
    already is its display name (characters) is shown once."""
    parts = []
    for c in combatants:
        if c["key"] == c["name"]:
            parts.append(c["key"])
        else:
            parts.append(f'{c["key"]} "{c["name"]}"')
    return ", ".join(parts)


def find_combatant(
    combatants: list[dict], identifier: str
) -> tuple[dict | None, list[dict] | None]:
    """Resolve `identifier` against each combatant's `key` or `name`,
    case-insensitively (surrounding whitespace ignored).

    Returns `(combatant, None)` for a unique match, `(None, None)` for no
    match, or `(None, matches)` when the identifier matches more than one
    distinct combatant — `matches` is every distinct combatant it hit, for
    the caller to report via `ambiguous_combatant_refusal`.
    """
    norm = identifier.strip().lower()
    found: dict[str, dict] = {}
    for c in combatants:
        if c["key"].lower() == norm or c["name"].lower() == norm:
            found[c["key"]] = c
    matches = list(found.values())
    if len(matches) == 1:
        return matches[0], None
    if not matches:
        return None, None
    return None, matches


def ambiguous_combatant_refusal(identifier: str, matches: list[dict]) -> str:
    return f"{identifier!r} matches multiple combatants: {describe_combatants(matches)}"


def unknown_combatant_refusal(label: str, identifier: str, combatants: list[dict]) -> str:
    """'unknown {label} 'X'' with the live roster appended when there is one
    to show (combat may be active but empty, or inactive)."""
    if combatants:
        return f"unknown {label} {identifier!r} (combatants: {describe_combatants(combatants)})"
    return f"unknown {label} {identifier!r}"


def turn_order_refusal(combatants: list[dict], turn_index: int, actor_key: str) -> str:
    """'it is not X's turn', naming whoever IS up and the way forward
    (TVA-39) instead of leaving the caller to guess."""
    if 0 <= turn_index < len(combatants):
        active_key = combatants[turn_index]["key"]
        return (
            f"it is not {actor_key}'s turn — it is {active_key}'s turn "
            f"(act with {active_key}, or call next_turn)"
        )
    return f"it is not {actor_key}'s turn"
