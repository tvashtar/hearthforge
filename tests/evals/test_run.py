from evals.cells import Cell
from evals.run import plan_runs


def test_plan_runs_orders_ascending_and_seeds_per_rep():
    runs = plan_runs([Cell("opus", "medium"), Cell("haiku", "medium")], reps=2, base_seed=100)
    assert [(r.cell.model, r.seed) for r in runs] == [
        ("haiku", 100), ("haiku", 101), ("opus", 100), ("opus", 101),
    ]
    assert runs[0].bundle_name == "haiku-medium-r0"
