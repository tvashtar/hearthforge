"""Unit tests for the MCP tool-schema introspection helper."""

from dm_engine.commands.registry import registered_commands
from dm_engine.mcp.server import _description, input_schema


def test_skill_check_schema():
    schema = input_schema(registered_commands()["skill_check"])

    assert schema["type"] == "object"
    props = schema["properties"]

    # ctx is dropped; the **kwargs catch-all is dropped.
    assert "ctx" not in props
    assert "kwargs" not in props

    assert props["character"] == {"type": "string"}
    assert props["skill"] == {"type": "string"}
    assert props["dc"] == {"type": "integer"}
    # X | None maps to the same type, and is optional.
    assert props["player_value"] == {"type": "integer"}
    assert props["advantage"] == {"type": "boolean"}

    # params without defaults are required; those with defaults are not.
    assert set(schema["required"]) == {"character", "skill", "dc"}
    assert "player_value" not in schema["required"]
    assert "advantage" not in schema["required"]


def test_type_mapping_covers_collections():
    # create_character exercises dict/list/list[dict] annotations.
    schema = input_schema(registered_commands()["create_character"])
    props = schema["properties"]

    assert props["abilities"] == {"type": "object"}
    assert props["proficiencies"] == {"type": "object"}
    assert props["attacks"] == {"type": "array"}
    assert props["spells_known"] == {"type": "array"}  # list[str] | None
    assert props["speed"] == {"type": "integer"}

    assert set(schema["required"]) == {
        "name", "role", "class_slug", "race_slug",
        "abilities", "ac", "proficiencies", "attacks",
    }


def test_dm_ruling_description_enumerates_effect_ops():
    # TVA-25: the effect-op mini-DSL must be discoverable from the MCP tool
    # surface — every op and its required fields, no source-diving.
    desc = _description(registered_commands()["dm_ruling"], "dm_ruling")
    for op in ("adjust_hp", "set_condition", "clear_condition", "adjust_slot",
               "set_exhaustion", "adjust_xp", "note"):
        assert op in desc
    assert "slot_level" in desc  # per-op required fields, not just op names
    assert "rationale" in desc


def test_open_campaign_is_a_registered_command_with_slug_schema():
    # TVA-26: open_campaign goes through the registry so session starts are
    # first-class audit events; its introspected schema keeps the old shape.
    handler = registered_commands()["open_campaign"]
    schema = input_schema(handler)
    assert schema["properties"]["slug"] == {"type": "string"}
    assert schema["required"] == ["slug"]
