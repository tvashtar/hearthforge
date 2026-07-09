import json

from typer.testing import CliRunner

from dm_engine.cli.app import app
from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign

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


def _bootstrap_campaign_with_ruling(campaigns_dir, rules_path, slug):
    ctx = bootstrap_campaign(
        campaigns_dir, rules_path, slug=slug, name="Test Campaign",
        skeleton={"premise": "test"},
    )
    registry.execute(
        "create_character", ctx, name="Kira", role="pc",
        class_slug="fighter", race_slug="human",
        abilities={"str": 16, "dex": 14, "con": 14, "int": 10, "wis": 12, "cha": 8},
        ac=16, proficiencies={"skills": ["athletics"], "saves": ["str", "con"]},
        attacks=[{"name": "longsword", "ranged": False, "range_ft": 5,
                  "long_range_ft": None, "damage": "1d8", "damage_type": "slashing",
                  "ability": "str", "proficient": True}],
    )
    registry.execute(
        "dm_ruling", ctx, description="Falling rocks", rationale="trap sprung, RAW silent",
    )
    ctx.store.close()


def test_audit_cli_prints_rulings(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    _bootstrap_campaign_with_ruling(campaigns_dir, rules_path, "audit-test")

    result = runner.invoke(app, ["audit", "--campaign", "audit-test",
                                  "--campaigns-dir", str(campaigns_dir)])
    assert result.exit_code == 0
    assert "trap sprung, RAW silent" in result.output
    assert "Falling rocks" in result.output


def test_audit_cli_unknown_campaign_exits_1(tmp_path):
    result = runner.invoke(app, ["audit", "--campaign", "nope",
                                  "--campaigns-dir", str(tmp_path / "campaigns")])
    assert result.exit_code == 1


def test_cmd_cli_executes_skill_check_end_to_end(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    _bootstrap_campaign_with_ruling(campaigns_dir, rules_path, "cmd-test")

    result = runner.invoke(app, [
        "cmd", "skill_check",
        "--campaign", "cmd-test",
        "--campaigns-dir", str(campaigns_dir),
        "--db", str(rules_path),
        "--json", json.dumps({"character": "Kira", "skill": "athletics", "dc": 10}),
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "skill_check"
    assert "total" in payload["data"]


def test_cmd_cli_refusal_still_exits_0(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    _bootstrap_campaign_with_ruling(campaigns_dir, rules_path, "cmd-refuse-test")

    result = runner.invoke(app, [
        "cmd", "skill_check",
        "--campaign", "cmd-refuse-test",
        "--campaigns-dir", str(campaigns_dir),
        "--db", str(rules_path),
        "--json", json.dumps({"character": "Nobody", "skill": "athletics", "dc": 10}),
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is False


def test_cmd_cli_unknown_campaign_exits_1(tmp_path, rules_path):
    result = runner.invoke(app, [
        "cmd", "skill_check",
        "--campaign", "nope",
        "--campaigns-dir", str(tmp_path / "campaigns"),
        "--db", str(rules_path),
        "--json", "{}",
    ])
    assert result.exit_code == 1
