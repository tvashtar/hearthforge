"""Materialized markdown character sheets. Task 5 implements the renderer;
until then the registry's post-command hook is a no-op."""

from __future__ import annotations

from pathlib import Path

from dm_engine.state.store import CampaignStore


def write_party_sheets(store: CampaignStore) -> list[Path]:
    return []
