from dm_engine.rules.dice import SeededDiceRoller
from dm_engine.rules.initiative import roll_initiative


def test_orders_by_total_descending():
    entries = roll_initiative(
        SeededDiceRoller(11), [("kira", 3), ("goblin-1", 2), ("goblin-2", 2), ("brother-aldric", 0)]
    )
    totals = [e.total for e in entries]
    assert totals == sorted(totals, reverse=True)
    assert {e.combatant_id for e in entries} == {"kira", "goblin-1", "goblin-2", "brother-aldric"}
    for e in entries:
        assert e.total == e.roll.total + e.dex_modifier


def test_ties_break_by_dex_then_input_order():
    class FixedRoller:
        def roll(self, notation, *, player_value=None, gm_only=False):
            from dm_engine.rules.dice import Roll

            value = player_value if player_value is not None else 10
            return Roll(
                notation=notation, rolls=[value], modifier=0, total=value,
                player_supplied=player_value is not None,
            )

    entries = roll_initiative(FixedRoller(), [("slow", 1), ("late", 2), ("early", 2)])
    assert [e.combatant_id for e in entries] == ["late", "early", "slow"]


def test_player_value_flags_player_roll():
    entries = roll_initiative(
        SeededDiceRoller(1), [("kira", 3), ("goblin-1", 2)], player_values={"kira": 18}
    )
    by_id = {e.combatant_id: e for e in entries}
    assert by_id["kira"].roll.player_supplied is True
    assert by_id["kira"].total == 21
    assert by_id["goblin-1"].roll.player_supplied is False
