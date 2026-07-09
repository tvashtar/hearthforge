# M1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A uv-managed Python package with both SRD sources vendored, a seeded `rules.sqlite` (typed tables + FTS5 rules-text index), and a working `dm seed` / `dm lookup` CLI.

**Architecture:** `dm_engine` is a src-layout package. `scripts/sync_srd.py` vendors the sibling `../dnd-5e-srd` markdown (rules prose) and 5e-bits/5e-database JSON (structured records) into `data/srd/`. `dm_engine.content.seed` builds `data/build/rules.sqlite` from the vendored files; `dm_engine.content.lookup` is the read API every later milestone uses.

**Tech Stack:** Python ≥3.12, uv, pydantic v2, typer, pytest, ruff, sqlite3 (stdlib, FTS5).

## Global Constraints

- Branch: all M1 work on `feat/m1-foundation`; never commit to `main`; never push.
- Storage layout is frozen (roadmap FC-5): vendored sources are edition-tagged — `data/srd/2014/text/` and `data/srd/2014/structured/` (committed); built DB at `data/build/rules.sqlite` (gitignored) with a `meta` table recording `edition=2014`, `srd_version=5.1`; `campaigns/` gitignored. The 2024 ruleset is a deliberate future migration (5e-bits' 2024 data is incomplete) — tag the data, don't build dual-edition logic.
- 5e-bits record `index` field is the canonical slug everywhere (e.g. `"aboleth"`, `"magic-missile"`).
- The fork's markdown uses setext headings (`===` h1, `---` h2) AND ATX (`###`+) — the section parser must handle both.
- Conventional commits, first line under 50 chars.
- Verify with `uv run pytest` and `uv run ruff check .` before every commit.

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/dm_engine/__init__.py`, `src/dm_engine/cli/__init__.py`, `src/dm_engine/cli/app.py`, `tests/test_cli.py`

**Interfaces:**
- Produces: installed console script `dm` (Typer app at `dm_engine.cli.app:app`); package version string `dm_engine.__version__`.

- [ ] **Step 1: Create branch**

```bash
git checkout -b feat/m1-foundation
```

- [ ] **Step 2: Write pyproject.toml**

```toml
[project]
name = "dm-engine"
version = "0.1.0"
description = "Rules engine and game state for an AI-driven D&D 5e dungeon master"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
]

[project.scripts]
dm = "dm_engine.cli.app:app"

[dependency-groups]
dev = [
    "pytest>=8",
    "ruff>=0.4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/dm_engine"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]
```

- [ ] **Step 3: Write .gitignore**

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.ruff_cache/
data/build/
campaigns/
.DS_Store
```

- [ ] **Step 4: Write the package skeleton**

`src/dm_engine/__init__.py`:
```python
__version__ = "0.1.0"
```

`src/dm_engine/cli/__init__.py`: empty file.

`src/dm_engine/cli/app.py`:
```python
import typer

import dm_engine

app = typer.Typer(help="AI dungeon master engine.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(dm_engine.__version__)
```

- [ ] **Step 5: Write the failing test**

`tests/test_cli.py`:
```python
from typer.testing import CliRunner

from dm_engine.cli.app import app

runner = CliRunner()


def test_version_reports_package_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"
```

- [ ] **Step 6: Sync and run tests**

Run: `uv sync && uv run pytest -v`
Expected: PASS (1 test). If `dm` entry point matters to you here, also check: `uv run dm version` → `0.1.0`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore src tests uv.lock
git commit -m "feat: scaffold dm-engine package with CLI"
```

---

### Task 2: SRD vendoring script

**Files:**
- Create: `scripts/sync_srd.py`, `data/srd/ATTRIBUTION.md`
- Creates at runtime (committed after running): `data/srd/text/*.md`, `data/srd/structured/5e-SRD-*.json`

**Interfaces:**
- Produces: vendored files at the FC-5 paths. Later tasks read `data/srd/structured/5e-SRD-Monsters.json` etc. and `data/srd/text/*.md`.

- [ ] **Step 1: Write scripts/sync_srd.py**

```python
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
```

- [ ] **Step 2: Write data/srd/ATTRIBUTION.md**

```markdown
# SRD Attribution

This directory vendors the Dungeons & Dragons 5th Edition Systems Reference
Document (SRD 5.1), © Wizards of the Coast, licensed under the Creative
Commons Attribution 4.0 International License (CC-BY-4.0):
https://creativecommons.org/licenses/by/4.0/legalcode

Sources (edition-tagged; currently the 2014 rules / SRD 5.1):
- `2014/text/` — markdown conversion from https://github.com/tvashtar/dnd-5e-srd
  (fork of https://github.com/vitusventure/5thSRD)
- `2014/structured/` — JSON records from https://github.com/5e-bits/5e-database
  (`src/2014/en/`)

Both are re-distributions of the same SRD 5.1 content. Re-run
`scripts/sync_srd.py` to refresh from upstream. A future migration to the
2024 rules (SRD 5.2) adds a `2024/` directory beside `2014/`.
```

- [ ] **Step 3: Run the script and inspect**

Run: `uv run python scripts/sync_srd.py`
Expected: `vendored 17 markdown files ...` and 25 JSON files (must include `5e-SRD-Monsters.json`, `5e-SRD-Spells.json`, `5e-SRD-Classes.json`, `5e-SRD-Races.json`, `5e-SRD-Equipment.json`, `5e-SRD-Magic-Items.json`, `5e-SRD-Conditions.json`, `5e-SRD-Features.json`).
Sanity check: `python3 -c "import json; d=json.load(open('data/srd/2014/structured/5e-SRD-Monsters.json')); print(len(d))"` → 300+.

- [ ] **Step 4: Commit the script and vendored data**

```bash
git add scripts/sync_srd.py data/srd
git commit -m "feat: vendor SRD text and structured sources"
```

---

### Task 3: SRD record models

**Files:**
- Create: `src/dm_engine/models/__init__.py`, `src/dm_engine/models/srd.py`
- Test: `tests/test_srd_models.py`

**Interfaces:**
- Produces: `MonsterRecord` and `SpellRecord` (pydantic, `extra="allow"`), each with `.slug` (the 5e-bits `index`). `MonsterRecord.ac -> int`, `MonsterRecord.ability_scores -> dict[str, int]` (keys `str,dex,con,int,wis,cha`). `SpellRecord.school_name -> str`, `SpellRecord.is_concentration -> bool`. Later milestones parse DB `data` JSON back through these models.

- [ ] **Step 1: Write the failing tests**

`tests/test_srd_models.py`:
```python
import json
from pathlib import Path

from dm_engine.models.srd import MonsterRecord, SpellRecord

STRUCTURED = Path(__file__).parent.parent / "data" / "srd" / "2014" / "structured"


def _load(filename: str) -> list[dict]:
    return json.loads((STRUCTURED / filename).read_text())


def test_monster_record_parses_aboleth():
    monsters = {m["index"]: m for m in _load("5e-SRD-Monsters.json")}
    aboleth = MonsterRecord.model_validate(monsters["aboleth"])
    assert aboleth.slug == "aboleth"
    assert aboleth.name == "Aboleth"
    assert aboleth.ac == 17
    assert aboleth.hit_points == 135
    assert aboleth.challenge_rating == 10
    assert aboleth.xp == 5900
    assert aboleth.ability_scores == {
        "str": 21, "dex": 9, "con": 15, "int": 18, "wis": 15, "cha": 18,
    }


def test_every_monster_parses():
    records = [MonsterRecord.model_validate(m) for m in _load("5e-SRD-Monsters.json")]
    assert len(records) > 300
    assert all(r.hit_points > 0 and r.ac > 0 for r in records)


def test_spell_record_parses_magic_missile():
    spells = {s["index"]: s for s in _load("5e-SRD-Spells.json")}
    mm = SpellRecord.model_validate(spells["magic-missile"])
    assert mm.slug == "magic-missile"
    assert mm.level == 1
    assert mm.school_name == "Evocation"
    assert mm.is_concentration is False


def test_every_spell_parses():
    records = [SpellRecord.model_validate(s) for s in _load("5e-SRD-Spells.json")]
    assert len(records) > 300
    assert all(0 <= r.level <= 9 for r in records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_srd_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dm_engine.models'`

- [ ] **Step 3: Write the models**

`src/dm_engine/models/__init__.py`: empty file.

`src/dm_engine/models/srd.py`:
```python
"""Typed views over 5e-bits SRD records.

Records keep all upstream fields (extra="allow"); these models type the
fields the engine queries and leave the rest reachable via model_extra.
The 5e-bits `index` field is the canonical slug everywhere.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MonsterRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(alias="index")
    name: str
    size: str
    type: str
    alignment: str
    armor_class: list[dict]
    hit_points: int
    hit_dice: str
    challenge_rating: float
    xp: int
    strength: int
    dexterity: int
    constitution: int
    intelligence: int
    wisdom: int
    charisma: int

    @property
    def ac(self) -> int:
        return int(self.armor_class[0]["value"])

    @property
    def ability_scores(self) -> dict[str, int]:
        return {
            "str": self.strength,
            "dex": self.dexterity,
            "con": self.constitution,
            "int": self.intelligence,
            "wis": self.wisdom,
            "cha": self.charisma,
        }


class SpellRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    slug: str = Field(alias="index")
    name: str
    level: int
    school: dict
    casting_time: str
    range: str
    duration: str
    concentration: bool
    ritual: bool
    desc: list[str]

    @property
    def school_name(self) -> str:
        return str(self.school["name"])

    @property
    def is_concentration(self) -> bool:
        return self.concentration
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_srd_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dm_engine/models tests/test_srd_models.py
git commit -m "feat: add typed SRD monster and spell models"
```

---

### Task 4: Markdown section parser

**Files:**
- Create: `src/dm_engine/content/__init__.py`, `src/dm_engine/content/markdown_sections.py`
- Test: `tests/test_markdown_sections.py`

**Interfaces:**
- Produces: `Section(source: str, heading_path: str, heading: str, body: str)` dataclass and `parse_sections(text: str, source: str) -> list[Section]`. `heading_path` joins ancestors with `" > "`. Task 5 feeds every vendored markdown file through this into FTS5.

- [ ] **Step 1: Write the failing tests**

`tests/test_markdown_sections.py`:
```python
from dm_engine.content.markdown_sections import parse_sections

SAMPLE = """\
Using Ability Scores
====================

Six abilities provide a quick description.

Ability Checks
--------------

An ability check tests talent and training.

### Contests

Sometimes efforts are directly opposed.

#### Typical DCs

| Task | DC |
|------|----|
| Easy | 10 |
"""


def test_parses_setext_and_atx_headings():
    sections = parse_sections(SAMPLE, source="06 mechanics.md")
    paths = [s.heading_path for s in sections]
    assert paths == [
        "Using Ability Scores",
        "Using Ability Scores > Ability Checks",
        "Using Ability Scores > Ability Checks > Contests",
        "Using Ability Scores > Ability Checks > Contests > Typical DCs",
    ]


def test_bodies_attach_to_their_heading():
    sections = {s.heading: s for s in parse_sections(SAMPLE, source="x.md")}
    assert "Six abilities" in sections["Using Ability Scores"].body
    assert "directly opposed" in sections["Contests"].body
    assert "| Easy | 10 |" in sections["Typical DCs"].body


def test_table_separators_are_not_headings():
    sections = parse_sections(SAMPLE, source="x.md")
    assert all("---" not in s.heading for s in sections)
    assert len(sections) == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_markdown_sections.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dm_engine.content'`

- [ ] **Step 3: Write the parser**

`src/dm_engine/content/__init__.py`: empty file.

`src/dm_engine/content/markdown_sections.py`:
```python
"""Split SRD markdown into heading-addressed sections for the FTS index.

The vendored fork mixes setext headings (`===` h1, `---` h2) with ATX
(`###`+). Table rows also contain runs of dashes, so a setext underline only
counts when the *previous* line is non-empty and is not itself a table row.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_SETEXT_H1 = re.compile(r"^=+\s*$")
_SETEXT_H2 = re.compile(r"^-{3,}\s*$")


@dataclass
class Section:
    source: str
    heading_path: str
    heading: str
    body: str


def _is_table_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def parse_sections(text: str, source: str) -> list[Section]:
    lines = text.splitlines()
    sections: list[Section] = []
    # stack of (level, title); body lines accumulate for the deepest heading
    stack: list[tuple[int, str]] = []
    body: list[str] = []

    def flush() -> None:
        if not stack:
            return
        content = "\n".join(body).strip()
        if content:
            sections.append(
                Section(
                    source=source,
                    heading_path=" > ".join(title for _, title in stack),
                    heading=stack[-1][1],
                    body=content,
                )
            )
        body.clear()

    def open_heading(level: int, title: str) -> None:
        flush()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))

    i = 0
    while i < len(lines):
        line = lines[i]
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        prev_ok = line.strip() and not _is_table_row(line)
        if prev_ok and _SETEXT_H1.match(nxt):
            open_heading(1, line.strip())
            i += 2
            continue
        if prev_ok and _SETEXT_H2.match(nxt) and not _is_table_row(nxt):
            open_heading(2, line.strip())
            i += 2
            continue
        m = _ATX.match(line)
        if m:
            open_heading(len(m.group(1)), m.group(2))
            i += 1
            continue
        body.append(line)
        i += 1

    flush()
    return sections
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_markdown_sections.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Smoke it against the real corpus**

Run: `uv run python -c "
from pathlib import Path
from dm_engine.content.markdown_sections import parse_sections
total = 0
for f in sorted(Path('data/srd/text').glob('*.md')):
    total += len(parse_sections(f.read_text(), f.name))
print('sections:', total)
"`
Expected: prints a four-digit section count (the corpus has thousands of headings); no exceptions.

- [ ] **Step 6: Commit**

```bash
git add src/dm_engine/content tests/test_markdown_sections.py
git commit -m "feat: parse SRD markdown into FTS sections"
```

---

### Task 5: rules.sqlite seeding

**Files:**
- Create: `src/dm_engine/content/seed.py`
- Test: `tests/test_seed.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `MonsterRecord`, `SpellRecord` (Task 3); `parse_sections` (Task 4).
- Produces: `build_rules_db(structured_dir: Path, text_dir: Path, dest: Path) -> dict[str, int]` (table → row count). DB tables: `monsters`, `spells`, `classes`, `races`, `equipment`, `magic_items`, `conditions`, `features` — each with typed query columns plus the full record as JSON in a `data` column — and FTS5 table `srd_text(source, heading_path, heading, body)`. Frozen for M2/M3: slugs are 5e-bits `index` values; full records live in `data`.

- [ ] **Step 1: Write the shared fixture**

`tests/conftest.py`:
```python
from pathlib import Path

import pytest

from dm_engine.content.seed import build_rules_db

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="session")
def rules_db(tmp_path_factory) -> Path:
    dest = tmp_path_factory.mktemp("rules") / "rules.sqlite"
    build_rules_db(
        structured_dir=REPO_ROOT / "data" / "srd" / "2014" / "structured",
        text_dir=REPO_ROOT / "data" / "srd" / "2014" / "text",
        dest=dest,
    )
    return dest
```

- [ ] **Step 2: Write the failing tests**

`tests/test_seed.py`:
```python
import json
import sqlite3


def test_seed_row_counts(rules_db):
    conn = sqlite3.connect(rules_db)
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in [
            "monsters", "spells", "classes", "races",
            "equipment", "magic_items", "conditions", "features",
        ]
    }
    assert counts["monsters"] > 300
    assert counts["spells"] > 300
    assert counts["classes"] == 12
    assert counts["races"] == 9
    assert counts["conditions"] == 15
    assert counts["equipment"] > 200
    assert counts["magic_items"] > 200
    assert counts["features"] > 300


def test_monster_typed_columns(rules_db):
    conn = sqlite3.connect(rules_db)
    row = conn.execute(
        "SELECT name, armor_class, hit_points, challenge_rating, xp"
        " FROM monsters WHERE slug='aboleth'"
    ).fetchone()
    assert row == ("Aboleth", 17, 135, 10.0, 5900)


def test_full_record_survives_in_data_column(rules_db):
    conn = sqlite3.connect(rules_db)
    (data,) = conn.execute("SELECT data FROM monsters WHERE slug='aboleth'").fetchone()
    record = json.loads(data)
    assert record["speed"]["swim"] == "40 ft."
    assert any(a["name"] == "Multiattack" for a in record["actions"])


def test_cr_range_query(rules_db):
    conn = sqlite3.connect(rules_db)
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM monsters WHERE challenge_rating <= 0.25 AND type='humanoid'"
    ).fetchone()
    assert n >= 5  # goblins, kobolds, bandits, cultists, ...


def test_meta_records_edition(rules_db):
    conn = sqlite3.connect(rules_db)
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert meta["edition"] == "2014"
    assert meta["srd_version"] == "5.1"


def test_fts_index_finds_rules_text(rules_db):
    conn = sqlite3.connect(rules_db)
    rows = conn.execute(
        "SELECT heading_path FROM srd_text WHERE srd_text MATCH ? ORDER BY rank LIMIT 5",
        ('"opportunity attack"',),
    ).fetchall()
    assert any("Opportunity Attacks" in p for (p,) in rows)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_seed.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.content.seed`)

- [ ] **Step 4: Write the seeder**

`src/dm_engine/content/seed.py`:
```python
"""Build rules.sqlite from the vendored SRD sources.

Static reference data: rebuilt by `dm seed`, never written during play.
Each table keeps queryable columns plus the full upstream record as JSON
in `data`. Slugs are the 5e-bits `index` values.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from dm_engine.content.markdown_sections import parse_sections
from dm_engine.models.srd import MonsterRecord, SpellRecord

SCHEMA = """
CREATE TABLE monsters (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, size TEXT, type TEXT, alignment TEXT,
    armor_class INTEGER, hit_points INTEGER, hit_dice TEXT,
    challenge_rating REAL, xp INTEGER,
    str INTEGER, dex INTEGER, con INTEGER, "int" INTEGER, wis INTEGER, cha INTEGER,
    data TEXT NOT NULL
);
CREATE TABLE spells (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, level INTEGER NOT NULL, school TEXT,
    concentration INTEGER NOT NULL, ritual INTEGER NOT NULL,
    casting_time TEXT, range TEXT, duration TEXT,
    data TEXT NOT NULL
);
CREATE TABLE classes (slug TEXT PRIMARY KEY, name TEXT NOT NULL, hit_die INTEGER, data TEXT NOT NULL);
CREATE TABLE races (slug TEXT PRIMARY KEY, name TEXT NOT NULL, speed INTEGER, data TEXT NOT NULL);
CREATE TABLE equipment (slug TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, data TEXT NOT NULL);
CREATE TABLE magic_items (slug TEXT PRIMARY KEY, name TEXT NOT NULL, rarity TEXT, data TEXT NOT NULL);
CREATE TABLE conditions (slug TEXT PRIMARY KEY, name TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE features (
    slug TEXT PRIMARY KEY, name TEXT NOT NULL, class_slug TEXT, level INTEGER, data TEXT NOT NULL
);
CREATE VIRTUAL TABLE srd_text USING fts5(source, heading_path, heading, body);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

EDITION_META = [("edition", "2014"), ("srd_version", "5.1")]


def _records(structured_dir: Path, filename: str) -> list[dict]:
    return json.loads((structured_dir / filename).read_text())


def build_rules_db(structured_dir: Path, text_dir: Path, dest: Path) -> dict[str, int]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    conn = sqlite3.connect(dest)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO meta VALUES (?,?)", EDITION_META)

    for raw in _records(structured_dir, "5e-SRD-Monsters.json"):
        m = MonsterRecord.model_validate(raw)
        conn.execute(
            "INSERT INTO monsters VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                m.slug, m.name, m.size, m.type, m.alignment,
                m.ac, m.hit_points, m.hit_dice, m.challenge_rating, m.xp,
                m.strength, m.dexterity, m.constitution,
                m.intelligence, m.wisdom, m.charisma,
                json.dumps(raw),
            ),
        )

    for raw in _records(structured_dir, "5e-SRD-Spells.json"):
        s = SpellRecord.model_validate(raw)
        conn.execute(
            "INSERT INTO spells VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                s.slug, s.name, s.level, s.school_name,
                int(s.concentration), int(s.ritual),
                s.casting_time, s.range, s.duration,
                json.dumps(raw),
            ),
        )

    simple_tables = [
        ("5e-SRD-Classes.json", "classes", lambda r: (r.get("hit_die"),)),
        ("5e-SRD-Races.json", "races", lambda r: (r.get("speed"),)),
        (
            "5e-SRD-Equipment.json",
            "equipment",
            lambda r: (r.get("equipment_category", {}).get("name"),),
        ),
        ("5e-SRD-Magic-Items.json", "magic_items", lambda r: (r.get("rarity", {}).get("name"),)),
        ("5e-SRD-Conditions.json", "conditions", lambda r: ()),
        (
            "5e-SRD-Features.json",
            "features",
            lambda r: (r.get("class", {}).get("index"), r.get("level")),
        ),
    ]
    for filename, table, extra_cols in simple_tables:
        for raw in _records(structured_dir, filename):
            cols = (raw["index"], raw["name"], *extra_cols(raw), json.dumps(raw))
            placeholders = ",".join("?" * len(cols))
            conn.execute(f"INSERT INTO {table} VALUES ({placeholders})", cols)

    for md_file in sorted(text_dir.glob("*.md")):
        for sec in parse_sections(md_file.read_text(), source=md_file.name):
            conn.execute(
                "INSERT INTO srd_text VALUES (?,?,?,?)",
                (sec.source, sec.heading_path, sec.heading, sec.body),
            )

    conn.commit()
    counts = {}
    for table in [
        "monsters", "spells", "classes", "races",
        "equipment", "magic_items", "conditions", "features", "srd_text",
    ]:
        counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()
    return counts
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_seed.py -v`
Expected: PASS (6 tests). If a count assertion fails, inspect the actual vendored JSON before touching the assertion — upstream counts drift slightly; the `>` floors are the contract, the `==` values (12 classes, 15 conditions, 9 races) are RAW facts and must hold.

- [ ] **Step 6: Commit**

```bash
git add src/dm_engine/content/seed.py tests/conftest.py tests/test_seed.py
git commit -m "feat: seed rules.sqlite from vendored SRD"
```

---

### Task 6: Lookup API and CLI wiring

**Files:**
- Create: `src/dm_engine/content/lookup.py`
- Modify: `src/dm_engine/cli/app.py`
- Test: `tests/test_lookup.py`

**Interfaces:**
- Consumes: `rules.sqlite` schema (Task 5), `MonsterRecord`/`SpellRecord` (Task 3).
- Produces (frozen for M2/M3 — this is how the whole engine reads reference data):
  - `RulesDB(path: Path)` — read-only connection wrapper, context-manager.
  - `RulesDB.lookup_rule(query: str, limit: int = 5) -> list[RuleHit]` where `RuleHit(source, heading_path, heading, snippet)`.
  - `RulesDB.get_monster(slug: str) -> MonsterRecord | None`
  - `RulesDB.search_monsters(max_cr: float | None = None, type: str | None = None, limit: int = 20) -> list[MonsterSummary]` where `MonsterSummary(slug, name, challenge_rating, xp)`.
  - `RulesDB.get_spell(slug: str) -> SpellRecord | None`
  - `RulesDB.search_spells(level: int | None = None, limit: int = 20) -> list[SpellSummary]` where `SpellSummary(slug, name, level, school)`.
  - CLI: `dm seed [--dest PATH]`, `dm lookup rule|monster|spell <query> [--db PATH]`. Default DB path `data/build/rules.sqlite` (FC-5), resolved relative to CWD.

- [ ] **Step 1: Write the failing tests**

`tests/test_lookup.py`:
```python
from dm_engine.content.lookup import RulesDB


def test_get_monster_roundtrips_typed_record(rules_db):
    with RulesDB(rules_db) as db:
        aboleth = db.get_monster("aboleth")
    assert aboleth is not None
    assert aboleth.hit_points == 135
    assert aboleth.ability_scores["str"] == 21


def test_get_monster_missing_returns_none(rules_db):
    with RulesDB(rules_db) as db:
        assert db.get_monster("tarrasque-jr") is None


def test_search_monsters_filters_by_cr_and_type(rules_db):
    with RulesDB(rules_db) as db:
        results = db.search_monsters(max_cr=0.25, type="humanoid")
    assert any(r.slug == "goblin" for r in results)
    assert all(r.challenge_rating <= 0.25 for r in results)


def test_get_spell(rules_db):
    with RulesDB(rules_db) as db:
        mm = db.get_spell("magic-missile")
    assert mm is not None and mm.level == 1


def test_search_spells_by_level(rules_db):
    with RulesDB(rules_db) as db:
        cantrips = db.search_spells(level=0)
    assert any(s.slug == "fire-bolt" for s in cantrips)
    assert all(s.level == 0 for s in cantrips)


def test_lookup_rule_returns_grappling_section(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule("grappling")
    assert hits, "expected at least one FTS hit"
    assert any("Grappl" in h.heading_path for h in hits)


def test_lookup_rule_survives_fts_special_chars(rules_db):
    with RulesDB(rules_db) as db:
        hits = db.lookup_rule('opportunity "attack" -weird*')
    assert isinstance(hits, list)  # must not raise on FTS syntax characters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_lookup.py -v`
Expected: FAIL with `ModuleNotFoundError` (no `dm_engine.content.lookup`)

- [ ] **Step 3: Write the lookup API**

`src/dm_engine/content/lookup.py`:
```python
"""Read API over rules.sqlite — the only way the engine reads reference data."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dm_engine.models.srd import MonsterRecord, SpellRecord

DEFAULT_DB = Path("data/build/rules.sqlite")


@dataclass
class RuleHit:
    source: str
    heading_path: str
    heading: str
    snippet: str


@dataclass
class MonsterSummary:
    slug: str
    name: str
    challenge_rating: float
    xp: int


@dataclass
class SpellSummary:
    slug: str
    name: str
    level: int
    school: str


class RulesDB:
    def __init__(self, path: Path = DEFAULT_DB):
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} not found — run `dm seed` first")
        self._conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)

    def __enter__(self) -> "RulesDB":
        return self

    def __exit__(self, *exc) -> None:
        self._conn.close()

    def lookup_rule(self, query: str, limit: int = 5) -> list[RuleHit]:
        # Quote every term: user text must never hit FTS5 query syntax.
        terms = [t for t in query.replace('"', " ").split() if t]
        if not terms:
            return []
        fts_query = " ".join(f'"{t}"' for t in terms)
        rows = self._conn.execute(
            "SELECT source, heading_path, heading,"
            " snippet(srd_text, 3, '[', ']', ' … ', 24)"
            " FROM srd_text WHERE srd_text MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, limit),
        ).fetchall()
        return [RuleHit(*row) for row in rows]

    def get_monster(self, slug: str) -> MonsterRecord | None:
        row = self._conn.execute(
            "SELECT data FROM monsters WHERE slug=?", (slug,)
        ).fetchone()
        return MonsterRecord.model_validate(json.loads(row[0])) if row else None

    def search_monsters(
        self,
        max_cr: float | None = None,
        type: str | None = None,
        limit: int = 20,
    ) -> list[MonsterSummary]:
        clauses, params = [], []
        if max_cr is not None:
            clauses.append("challenge_rating <= ?")
            params.append(max_cr)
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT slug, name, challenge_rating, xp FROM monsters {where}"
            " ORDER BY challenge_rating, name LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [MonsterSummary(*row) for row in rows]

    def get_spell(self, slug: str) -> SpellRecord | None:
        row = self._conn.execute(
            "SELECT data FROM spells WHERE slug=?", (slug,)
        ).fetchone()
        return SpellRecord.model_validate(json.loads(row[0])) if row else None

    def search_spells(self, level: int | None = None, limit: int = 20) -> list[SpellSummary]:
        clauses, params = [], []
        if level is not None:
            clauses.append("level = ?")
            params.append(level)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT slug, name, level, school FROM spells {where}"
            " ORDER BY level, name LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [SpellSummary(*row) for row in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_lookup.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Wire the CLI**

Replace `src/dm_engine/cli/app.py` with:
```python
import json
from pathlib import Path

import typer

import dm_engine
from dm_engine.content.lookup import DEFAULT_DB, RulesDB

app = typer.Typer(help="AI dungeon master engine.", no_args_is_help=True)
lookup_app = typer.Typer(help="Query the seeded SRD rules database.", no_args_is_help=True)
app.add_typer(lookup_app, name="lookup")

REPO_ROOT = Path(__file__).resolve().parents[3]


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(dm_engine.__version__)


@app.command()
def seed(dest: Path = typer.Option(DEFAULT_DB, help="Output path for rules.sqlite")) -> None:
    """Build rules.sqlite from the vendored SRD sources."""
    from dm_engine.content.seed import build_rules_db

    counts = build_rules_db(
        structured_dir=REPO_ROOT / "data" / "srd" / "2014" / "structured",
        text_dir=REPO_ROOT / "data" / "srd" / "2014" / "text",
        dest=dest,
    )
    for table, n in counts.items():
        typer.echo(f"{table:12} {n}")


@lookup_app.command("rule")
def lookup_rule(
    query: str,
    db: Path = typer.Option(DEFAULT_DB),
    limit: int = typer.Option(5),
) -> None:
    """Full-text search the SRD rules prose."""
    with RulesDB(db) as rules:
        for hit in rules.lookup_rule(query, limit=limit):
            typer.echo(f"## {hit.heading_path}  ({hit.source})")
            typer.echo(hit.snippet)
            typer.echo()


@lookup_app.command("monster")
def lookup_monster(slug: str, db: Path = typer.Option(DEFAULT_DB)) -> None:
    """Print a monster's full record by slug (e.g. 'aboleth')."""
    with RulesDB(db) as rules:
        monster = rules.get_monster(slug)
    if monster is None:
        typer.echo(f"no monster with slug {slug!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(monster.model_dump(by_alias=True), indent=2))


@lookup_app.command("spell")
def lookup_spell(slug: str, db: Path = typer.Option(DEFAULT_DB)) -> None:
    """Print a spell's full record by slug (e.g. 'magic-missile')."""
    with RulesDB(db) as rules:
        spell = rules.get_spell(slug)
    if spell is None:
        typer.echo(f"no spell with slug {slug!r}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(spell.model_dump(by_alias=True), indent=2))
```

Append to `tests/test_cli.py`:
```python
def test_seed_and_lookup_cli(tmp_path):
    db = tmp_path / "rules.sqlite"
    result = runner.invoke(app, ["seed", "--dest", str(db)])
    assert result.exit_code == 0
    assert "monsters" in result.output

    result = runner.invoke(app, ["lookup", "monster", "aboleth", "--db", str(db)])
    assert result.exit_code == 0
    assert '"hit_points": 135' in result.output

    result = runner.invoke(app, ["lookup", "rule", "grappling", "--db", str(db)])
    assert result.exit_code == 0
    assert "Grappl" in result.output

    result = runner.invoke(app, ["lookup", "monster", "nonexistent", "--db", str(db)])
    assert result.exit_code == 1
```

- [ ] **Step 6: Run the full suite and lint**

Run: `uv run pytest -v && uv run ruff check .`
Expected: all tests PASS, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add src/dm_engine/content/lookup.py src/dm_engine/cli/app.py tests/test_lookup.py tests/test_cli.py
git commit -m "feat: add rules lookup API and CLI"
```

---

### Task 7: Milestone gate

**Files:** none new — verification only.

- [ ] **Step 1: Clean-checkout style verification**

```bash
uv run python scripts/sync_srd.py   # idempotent re-vendor
uv run dm seed
uv run dm lookup rule "grappling"
uv run dm lookup monster aboleth
uv run dm lookup spell magic-missile
uv run pytest -v
uv run ruff check .
```
Expected: seed prints table counts (monsters/spells > 300, classes 12); each lookup prints correct content; suite green; lint clean. This is the M1 gate from the roadmap.

- [ ] **Step 2: Merge**

Merge `feat/m1-foundation` into `main` (no push). Then the orchestrator writes the M2 plan per the roadmap.
