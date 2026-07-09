#!/usr/bin/env python3
"""Vendor SRD sources into data/srd/.

Text (rules prose):  copies markdown/ from the sibling dnd-5e-srd fork.
Structured (typed records): copies the 2014 SRD JSON files from a local
clone of 5e-bits/5e-database (sibling ../5e-database by default),
shallow-cloning from GitHub only if no local clone exists.

Usage: uv run python scripts/sync_srd.py [--fork-path ../dnd-5e-srd] [--bits-path ../5e-database]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEXT_DEST = REPO_ROOT / "data" / "srd" / "2014" / "text"
STRUCTURED_DEST = REPO_ROOT / "data" / "srd" / "2014" / "structured"
FIVE_E_BITS_URL = "https://github.com/5e-bits/5e-database"


def sync_text(fork_path: Path) -> int:
    src = fork_path / "markdown"
    if not src.is_dir():
        sys.exit(f"error: {src} not found — pass --fork-path")
    TEXT_DEST.mkdir(parents=True, exist_ok=True)
    count = 0
    for md in sorted(src.glob("*.md")):
        shutil.copy2(md, TEXT_DEST / md.name)
        count += 1
    return count


def _copy_bits_json(repo: Path) -> int:
    candidates = sorted(repo.glob("src/**/5e-SRD-*.json"))
    # The repo splits by edition (2014/, 2024/) and locale (en/, fr-FR/, pt-BR/, ru/).
    # Filenames repeat across locales, so narrow before copying or locales clobber
    # each other. Verified layout as of 2026-07: src/2014/en/5e-SRD-*.json (25 files).
    for segment in ("2014", "en"):
        narrowed = [p for p in candidates if segment in p.parts]
        if narrowed:
            candidates = narrowed
    for f in candidates:
        shutil.copy2(f, STRUCTURED_DEST / f.name)
    return len(candidates)


def sync_structured(bits_path: Path) -> int:
    STRUCTURED_DEST.mkdir(parents=True, exist_ok=True)
    if bits_path.is_dir():
        return _copy_bits_json(bits_path)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["git", "clone", "--depth", "1", FIVE_E_BITS_URL, tmp],
            check=True,
        )
        return _copy_bits_json(Path(tmp))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fork-path", type=Path, default=REPO_ROOT.parent / "dnd-5e-srd")
    parser.add_argument("--bits-path", type=Path, default=REPO_ROOT.parent / "5e-database")
    args = parser.parse_args()
    n_text = sync_text(args.fork_path)
    n_json = sync_structured(args.bits_path)
    print(f"vendored {n_text} markdown files -> {TEXT_DEST}")
    print(f"vendored {n_json} json files -> {STRUCTURED_DEST}")


if __name__ == "__main__":
    main()
