"""dm-eval CLI: run the model evaluation matrix."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dm-eval", description="Timed, graded DM model evals")
    p.add_argument("--cells", help="model[:effort],... (default: full family matrix at medium)")
    p.add_argument("--reps", type=int, default=1, help="runs per cell, fresh seed per rep")
    p.add_argument("--parallel", type=int, default=3, help="max concurrent cells")
    p.add_argument("--serial", action="store_true", help="run cells one at a time")
    p.add_argument("--smoke", action="store_true", help="one haiku cell, first 2 beats only")
    p.add_argument("--judge-only", metavar="RUN_DIR", help="re-grade existing bundles")
    return p


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(f"not implemented yet: {args}")


if __name__ == "__main__":
    main()
