import typer

import dm_engine

app = typer.Typer(help="AI dungeon master engine.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """AI dungeon master engine."""


@app.command()
def version() -> None:
    """Print the engine version."""
    typer.echo(dm_engine.__version__)
