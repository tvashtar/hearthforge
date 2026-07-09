import json
import sqlite3
from pathlib import Path

import typer

import dm_engine
import dm_engine.commands  # noqa: F401 — importing registers every command
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.envelope import CommandResult
from dm_engine.commands.registry import execute, open_campaign_context
from dm_engine.content.lookup import DEFAULT_DB, RulesDB
from dm_engine.state.sheets import render_character_sheet
from dm_engine.state.store import CampaignStore

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


@app.command()
def sheet(
    character: str,
    campaign: str = typer.Option(..., help="Campaign slug"),
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
) -> None:
    """Print a character's rendered markdown sheet (read-only, no snapshot)."""
    root = campaigns_dir / campaign
    db_path = root / "campaign.sqlite"
    if not db_path.exists():
        typer.echo(f"no campaign at {db_path}", err=True)
        raise typer.Exit(code=1)
    store = CampaignStore(sqlite3.connect(db_path), root)
    try:
        char = store.get_character(character)
        if char is None:
            typer.echo(f"no character named {character!r} in campaign {campaign!r}", err=True)
            raise typer.Exit(code=1)
        typer.echo(render_character_sheet(store, char["id"]))
    finally:
        store.close()


@app.command("new")
def new_campaign(
    slug: str,
    name: str = typer.Option(..., help="Campaign name"),
    death_mode: str = typer.Option("narrative", help="'narrative' or 'hardcore'"),
    seed: int | None = typer.Option(None, help="RNG seed (random if omitted)"),
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
    db: Path = typer.Option(DEFAULT_DB, help="Path to rules.sqlite"),
) -> None:
    """Create a new campaign with a minimal skeleton (DM fills it in later)."""
    skeleton = {"premise": f"{name} (created via CLI; skeleton to be written by the DM)"}
    try:
        ctx = bootstrap_campaign(
            campaigns_dir, db, slug=slug, name=name, death_mode=death_mode,
            skeleton=skeleton, seed=seed,
        )
    except FileExistsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = CommandResult(
            ok=True, command="create_campaign",
            digest=f"Campaign '{name}' created",
            data={"slug": slug, "name": name, "death_mode": death_mode},
        )
        typer.echo(result.model_dump_json(indent=2))
    finally:
        ctx.store.close()


@app.command("resume")
def resume_campaign(
    slug: str,
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
    db: Path = typer.Option(DEFAULT_DB, help="Path to rules.sqlite"),
) -> None:
    """Open a campaign (snapshotting it) and print the session brief."""
    try:
        ctx = open_campaign_context(campaigns_dir, slug, db)
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = execute("get_campaign_brief", ctx)
    finally:
        ctx.store.close()
    typer.echo(result.model_dump_json(indent=2))


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


@app.command()
def audit(
    campaign: str = typer.Option(..., help="Campaign slug"),
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
) -> None:
    """Print every dm_ruling event: id, timestamp, rationale, and digest."""
    root = campaigns_dir / campaign
    db_path = root / "campaign.sqlite"
    if not db_path.exists():
        typer.echo(f"no campaign at {db_path}", err=True)
        raise typer.Exit(code=1)
    store = CampaignStore(sqlite3.connect(db_path), root)
    try:
        for ruling in store.rulings():
            result = json.loads(ruling["result"])
            typer.echo(f"#{ruling['id']} {ruling['created_at']} — {ruling['rationale']}")
            typer.echo(f"  {result.get('digest', '')}")
    finally:
        store.close()


@app.command("mcp")
def mcp(
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
    db: Path = typer.Option(DEFAULT_DB, help="Path to rules.sqlite"),
) -> None:
    """Run the MCP server over stdio (for Claude Code to drive the engine)."""
    import anyio

    from dm_engine.mcp.server import run_stdio

    anyio.run(run_stdio, campaigns_dir, db)


@app.command("cmd")
def cmd(
    name: str,
    campaign: str = typer.Option(..., help="Campaign slug"),
    campaigns_dir: Path = typer.Option(
        REPO_ROOT / "campaigns", help="Directory holding campaign folders"
    ),
    db: Path = typer.Option(DEFAULT_DB, help="Path to rules.sqlite"),
    json_kwargs: str = typer.Option(
        "{}", "--json", help="Command kwargs as a JSON object"
    ),
) -> None:
    """Execute one registry command against a campaign and print its result.

    Exit code 0 even for refusals (a refusal is a normal result); exit 1
    for an unknown campaign, an unreadable rules database, or a malformed
    --json payload.
    """
    try:
        kwargs = json.loads(json_kwargs)
    except json.JSONDecodeError as exc:
        typer.echo(f"invalid --json payload: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    try:
        ctx = open_campaign_context(campaigns_dir, campaign, db)
    except (FileNotFoundError, sqlite3.OperationalError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = execute(name, ctx, **kwargs)
    finally:
        ctx.store.close()
    typer.echo(result.model_dump_json(indent=2))
