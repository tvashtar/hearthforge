from typer.testing import CliRunner

from dm_engine.cli.app import app

runner = CliRunner()


def test_version_reports_package_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0"
