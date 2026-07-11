import pytest

from evals.cells import ABILITY_ORDER, Cell, parse_cells


def test_default_matrix_is_all_families_medium_ascending():
    cells = parse_cells(None)
    assert [c.model for c in cells] == ["haiku", "sonnet", "opus", "fable"]
    assert all(c.effort == "medium" for c in cells)


def test_explicit_cells_are_resorted_ascending():
    cells = parse_cells("fable:high,haiku,opus:low")
    assert [(c.model, c.effort) for c in cells] == [
        ("haiku", "medium"), ("opus", "low"), ("fable", "high"),
    ]


def test_slug_is_filesystem_safe():
    assert Cell("opus", "medium").slug == "opus-medium"


def test_unknown_model_rejected():
    with pytest.raises(ValueError, match="unknown model"):
        parse_cells("gpt5:high")


def test_unknown_effort_rejected():
    with pytest.raises(ValueError, match="unknown effort"):
        parse_cells("opus:ultra")


def test_ability_order_covers_default_matrix():
    assert ABILITY_ORDER == ["haiku", "sonnet", "opus", "fable"]
