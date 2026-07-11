"""dm-eval CLI: run the model evaluation matrix."""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
from dataclasses import dataclass
from pathlib import Path

import anthropic

from dm_engine.content.seed import ensure_rules_db
from evals.cells import Cell, parse_cells
from evals.judge import JUDGE_MODEL, anonymize, judge_transcript
from evals.metrics import compute_metrics
from evals.report import render_report
from evals.runner import run_cell
from evals.scenario import load_scenario

REPO_ROOT = Path(__file__).parents[1]
SCENARIO_PATH = REPO_ROOT / "evals" / "scenarios" / "caravan_ambush.yaml"
RUNS_DIR = REPO_ROOT / "evals" / "runs"
BASE_SEED = 20260711


@dataclass(frozen=True)
class PlannedRun:
    cell: Cell
    seed: int
    rep: int

    @property
    def bundle_name(self) -> str:
        return f"{self.cell.slug}-r{self.rep}"


def plan_runs(cells: list[Cell], *, reps: int, base_seed: int) -> list[PlannedRun]:
    """Ascending-ability launch order is load-bearing: weakest models first."""
    ordered = parse_cells(",".join(f"{c.model}:{c.effort}" for c in cells))
    return [PlannedRun(c, base_seed + r, r) for c in ordered for r in range(reps)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dm-eval", description="Timed, graded DM model evals")
    p.add_argument("--cells", help="model[:effort],... (default: full family matrix at medium)")
    p.add_argument("--reps", type=int, default=1, help="runs per cell, fresh seed per rep")
    p.add_argument("--parallel", type=int, default=3, help="max concurrent cells")
    p.add_argument("--serial", action="store_true", help="run cells one at a time")
    p.add_argument("--smoke", action="store_true", help="one haiku cell, first 2 beats only")
    p.add_argument("--judge-only", metavar="RUN_DIR", help="re-grade existing bundles")
    return p


async def _run_all(runs, run_dir: Path, parallel: int, beats_limit: int | None):
    scenario = load_scenario(SCENARIO_PATH)
    rules = ensure_rules_db()
    sem = asyncio.Semaphore(parallel)

    async def one(pr: PlannedRun):
        async with sem:
            return pr, await run_cell(
                pr.cell, scenario,
                repo_root=REPO_ROOT, campaigns_dir=REPO_ROOT / "campaigns",
                rules_db_path=rules, bundle_dir=run_dir / pr.bundle_name,
                seed=pr.seed, beats_limit=beats_limit,
            )

    # created in ascending order; the semaphore admits them in creation order
    return await asyncio.gather(*(one(pr) for pr in runs))


def grade_run_dir(run_dir: Path) -> list[dict]:
    scenario_yaml = SCENARIO_PATH.read_text()
    skill_text = (REPO_ROOT / ".claude/skills/dm-session/SKILL.md").read_text()
    client = anthropic.Anthropic()
    results = []
    for bundle in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        timing = json.loads((bundle / "timing.json").read_text())
        metrics = compute_metrics(bundle / "campaign.sqlite", bundle / "transcript.jsonl")
        transcript = anonymize(
            (bundle / "transcript.jsonl").read_text(), [timing.get("resolved_model") or ""]
        )
        judge = judge_transcript(client, transcript, scenario_yaml, skill_text)
        turns = timing.get("turns", [])
        walls = sorted(t["wall_s"] for t in turns) or [0.0]
        out_tokens = sum(
            (t.get("usage") or {}).get("output_tokens", 0) for t in turns
        )
        results.append({
            "cell": timing["cell"], "resolved_model": timing.get("resolved_model"),
            "wall_clock_s": timing["wall_clock_s"],
            "median_turn_s": walls[len(walls) // 2],
            "output_tokens": out_tokens,
            "beats_completed": timing["beats_completed"],
            "beats_failed": timing["beats_failed"], "error": timing.get("error"),
            "metrics": metrics, "judge": judge,
        })
    return results


def main() -> None:
    args = build_parser().parse_args()
    if args.judge_only:
        run_dir = Path(args.judge_only)
    else:
        stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%d-%H%M%S")
        run_dir = RUNS_DIR / stamp
        run_dir.mkdir(parents=True)
        cells = parse_cells("haiku:medium" if args.smoke else args.cells)
        runs = plan_runs(cells, reps=args.reps, base_seed=BASE_SEED)
        parallel = 1 if args.serial else args.parallel
        beats_limit = 2 if args.smoke else None
        outcomes = asyncio.run(_run_all(runs, run_dir, parallel, beats_limit))
        for pr, res in outcomes:
            status = res.error or f"{len(res.beats_completed)} beats"
            print(f"{pr.bundle_name}: {status} in {res.wall_clock_s}s")
    results = grade_run_dir(run_dir)
    report = render_report(results, judge_model=JUDGE_MODEL)
    (run_dir / "report.md").write_text(report)
    print(f"\nreport: {run_dir / 'report.md'}")


if __name__ == "__main__":
    main()
