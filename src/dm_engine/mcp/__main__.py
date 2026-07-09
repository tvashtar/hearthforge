"""Entry point: `python -m dm_engine.mcp` runs the MCP server on stdio."""

from __future__ import annotations

import argparse
from pathlib import Path

import anyio

from dm_engine.content.lookup import DEFAULT_DB
from dm_engine.mcp.server import run_stdio


def main() -> None:
    parser = argparse.ArgumentParser(prog="dm_engine.mcp", description=__doc__)
    parser.add_argument("--campaigns-dir", type=Path, default=Path("campaigns"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    anyio.run(run_stdio, args.campaigns_dir, args.db)


if __name__ == "__main__":
    main()
