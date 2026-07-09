"""Goal-gate e2e: the audit trail and RNG-integrity guarantees.

A short scripted session exercises the auditable surface — a player-supplied
check, an engine-rolled companion check, a logged ``dm_ruling`` and a rejected
one, a checkpoint — then we assert:

* every command appended exactly one event row (bootstrap included);
* a ``dm_ruling`` with no rationale is refused and logged with ``is_ruling=0``;
* the store and the ``dm audit`` CLI both surface exactly the one real ruling;
* ``open_campaign_context`` writes a session-start snapshot (bootstrap does not);
* every engine roll replays bit-for-bit from a fresh seeded roller.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from dm_engine.cli.app import app
from dm_engine.commands import registry
from dm_engine.commands.campaign import bootstrap_campaign
from dm_engine.commands.registry import open_campaign_context
from dm_engine.rules.dice import SeededDiceRoller

SEED = 20240607
RATIONALE = "trap sprung: house-ruled 5 fire damage to Kira"


class Driver:
    def __init__(self, ctx):
        self.ctx = ctx
        self.calls = 0

    def __call__(self, command, /, **kwargs):
        self.calls += 1
        return registry.execute(command, self.ctx, **kwargs)


def test_audit_and_integrity_gate(tmp_path, rules_path):
    campaigns_dir = tmp_path / "campaigns"
    slug = "audit-gate"

    ctx = bootstrap_campaign(
        campaigns_dir, rules_path, slug=slug, name="Audit Run",
        death_mode="narrative", skeleton={"premise": "the haunted crypt"},
        seed=SEED,
    )
    run = Driver(ctx)
    snapshots = ctx.store.root / "snapshots"
    try:
        # bootstrap did NOT snapshot (creation is not a session open).
        assert list(snapshots.glob("*.sqlite")) == []
        assert ctx.store.event_count() == 1  # only the create_campaign event

        assert run("create_character", name="Kira", role="pc",
                   class_slug="fighter", race_slug="human",
                   abilities={"str": 16, "dex": 14, "con": 14, "int": 10,
                              "wis": 12, "cha": 8},
                   ac=16,
                   proficiencies={"skills": ["athletics"]},
                   attacks=[{"weapon": "longsword", "name": "longsword"}]).ok
        assert run("create_character", name="Brother Aldric", role="companion",
                   class_slug="cleric", race_slug="hill-dwarf",
                   abilities={"str": 14, "dex": 8, "con": 15, "int": 10,
                              "wis": 15, "cha": 12},
                   ac=18,
                   proficiencies={"skills": ["medicine"]},
                   attacks=[{"weapon": "mace", "name": "mace"}],
                   spells_known=["cure-wounds", "bless"]).ok

        # player-supplied PC check (no RNG draw) ...
        pc_check = run("skill_check", character="Kira", skill="athletics",
                       dc=10, player_value=12)
        assert pc_check.ok
        # ... and two engine-rolled companion checks (real RNG draws).
        assert run("skill_check", character="Brother Aldric", skill="medicine",
                   dc=10).ok
        assert run("saving_throw", character="Brother Aldric", ability="wis",
                   dc=12).ok

        # a real ruling with a rationale: logged as a ruling.
        ruling = run("dm_ruling", description="A fire trap scorches Kira.",
                     rationale=RATIONALE,
                     effects=[{"op": "adjust_hp", "target": "Kira", "delta": -5}])
        assert ruling.ok, ruling.refusal

        # a ruling with no rationale: refused, and logged as a NON-ruling row.
        refused = run("dm_ruling", description="An unexplained miracle.",
                      rationale="   ")
        assert refused.ok is False
        last = ctx.store.conn.execute(
            "SELECT command, is_ruling FROM event_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert last["command"] == "dm_ruling"
        assert last["is_ruling"] == 0  # refusal is not an audited ruling

        assert run("checkpoint", content="Recap: the crypt's trap nearly did us in.").ok

        # every execute call appended exactly one event; bootstrap added one.
        assert ctx.store.event_count() == 1 + run.calls

        # exactly one audited ruling, carrying its rationale.
        rulings = ctx.store.rulings()
        assert len(rulings) == 1
        assert rulings[0]["rationale"] == RATIONALE

        # --- seed replay: every engine roll reproduces from a fresh roller ---
        seed = ctx.store.campaign_meta()["rng_seed"]
        assert seed == SEED
        rows = ctx.store.conn.execute(
            "SELECT rolls FROM event_log ORDER BY id"
        ).fetchall()
        engine_rolls = [
            r for row in rows for r in json.loads(row["rolls"])
            if not r["player_supplied"]
        ]
        assert len(engine_rolls) >= 2  # the two companion checks at minimum
        replay = SeededDiceRoller(seed)
        for recorded in engine_rolls:
            fresh = replay.roll(recorded["notation"])
            assert fresh.rolls == recorded["rolls"]
            assert fresh.total == recorded["total"]
    finally:
        ctx.store.close()

    # --- open_campaign_context writes a session-start snapshot ---------------
    ctx2 = open_campaign_context(campaigns_dir, slug, rules_path)
    try:
        assert list(snapshots.glob("*.sqlite"))  # non-empty after the open
    finally:
        ctx2.store.close()

    # --- the `dm audit` CLI prints the ruling and its rationale --------------
    result = CliRunner().invoke(
        app, ["audit", "--campaign", slug, "--campaigns-dir", str(campaigns_dir)]
    )
    assert result.exit_code == 0, result.output
    assert RATIONALE in result.output
    assert "A fire trap scorches Kira." in result.output  # the ruling's digest
