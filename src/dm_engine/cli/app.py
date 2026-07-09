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
