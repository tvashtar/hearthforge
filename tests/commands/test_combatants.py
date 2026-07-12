"""Unit tests for the shared combatant-identifier resolver (TVA-38/TVA-39)."""

from dm_engine.commands.combatants import (
    ambiguous_combatant_refusal,
    describe_combatants,
    find_combatant,
    turn_order_refusal,
    unknown_combatant_refusal,
)

_COMBATANTS = [
    {"key": "Kira", "name": "Kira"},
    {"key": "bandit-1", "name": "Fen Scout"},
    {"key": "bandit-2", "name": "Fen Scout"},
    {"key": "Brother Aldric", "name": "Brother Aldric"},
]


def test_find_combatant_matches_key_exactly():
    combatant, ambiguous = find_combatant(_COMBATANTS, "bandit-1")
    assert ambiguous is None
    assert combatant["key"] == "bandit-1"


def test_find_combatant_matches_display_name_case_insensitively():
    combatant, ambiguous = find_combatant(_COMBATANTS, "kira")
    assert ambiguous is None
    assert combatant["key"] == "Kira"


def test_find_combatant_matches_key_case_insensitively():
    combatant, ambiguous = find_combatant(_COMBATANTS, "BANDIT-1")
    assert ambiguous is None
    assert combatant["key"] == "bandit-1"


def test_find_combatant_strips_whitespace():
    combatant, ambiguous = find_combatant(_COMBATANTS, "  Kira  ")
    assert ambiguous is None
    assert combatant["key"] == "Kira"


def test_find_combatant_no_match():
    combatant, ambiguous = find_combatant(_COMBATANTS, "Bandit 3")
    assert combatant is None and ambiguous is None


def test_find_combatant_ambiguous_name_lists_matches():
    combatant, ambiguous = find_combatant(_COMBATANTS, "fen scout")
    assert combatant is None
    assert {c["key"] for c in ambiguous} == {"bandit-1", "bandit-2"}


def test_describe_combatants_shows_key_only_when_name_matches():
    assert describe_combatants([{"key": "Kira", "name": "Kira"}]) == "Kira"


def test_describe_combatants_shows_key_and_name_when_they_differ():
    assert (
        describe_combatants([{"key": "bandit-1", "name": "Fen Scout"}])
        == 'bandit-1 "Fen Scout"'
    )


def test_unknown_combatant_refusal_lists_roster():
    msg = unknown_combatant_refusal("target", "Bandit 3", _COMBATANTS)
    assert msg == (
        "unknown target 'Bandit 3' (combatants: Kira, bandit-1 \"Fen Scout\", "
        'bandit-2 "Fen Scout", Brother Aldric)'
    )


def test_unknown_combatant_refusal_without_roster():
    assert unknown_combatant_refusal("target", "Bandit 3", []) == "unknown target 'Bandit 3'"


def test_ambiguous_combatant_refusal_lists_matches():
    matches = [c for c in _COMBATANTS if c["name"] == "Fen Scout"]
    msg = ambiguous_combatant_refusal("Fen Scout", matches)
    assert msg == (
        '\'Fen Scout\' matches multiple combatants: bandit-1 "Fen Scout", '
        'bandit-2 "Fen Scout"'
    )


def test_turn_order_refusal_names_active_combatant():
    msg = turn_order_refusal(_COMBATANTS, 1, "Kira")
    assert msg == (
        "it is not Kira's turn — it is bandit-1's turn "
        "(act with bandit-1, or call next_turn)"
    )


def test_turn_order_refusal_handles_out_of_range_index():
    assert turn_order_refusal(_COMBATANTS, 99, "Kira") == "it is not Kira's turn"
