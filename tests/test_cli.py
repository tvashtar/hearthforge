from typer.testing import CliRunner

from dm_engine.cli.app import app

runner = CliRunner()


def test_version_reports_package_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"


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
