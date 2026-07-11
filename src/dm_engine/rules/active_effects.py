"""Pure logic for timed mechanical effects (`active_effects` rows, TVA-20).

An effect's `mechanics` is a JSON object of known keys, validated at the
door (dm_ruling's `apply_effect` op) so a typo can never become a silent
no-op. v1 resolves AC only; future mechanics (advantage riders, save
bonuses) are added as new keys here without any schema change:

- ``ac_override`` (int) — candidate replacement AC (mage armor's "AC
  becomes 13 + DEX"); the best override replaces the base only when it is
  higher, so it never strips real armor.
- ``ac_bonus`` (int) — additive AC modifier (shield's +5, cover); every
  bonus stacks on top of the override/base fold.
- ``note`` (str) — free-text annotation with no engine mechanics, for
  effects the engine cannot resolve yet (faerie fire's advantage rider);
  the effect is still tracked and expired on the sheet.

Expiry is against the world clock: an effect with ``expires_day`` /
``expires_minutes`` set is expired the instant the clock reaches that
time. ``expires_on_rest`` and concentration linkage are handled by the
command layer (`commands/effects.py`); this module stays pure.
"""

from __future__ import annotations

_INT_KEYS = ("ac_override", "ac_bonus")
_STR_KEYS = ("note",)
KNOWN_MECHANICS = _INT_KEYS + _STR_KEYS

_MINUTES_PER_DAY = 1440


def validate_mechanics(mechanics: object) -> str | None:
    """Return a refusal reason, or None if `mechanics` is a legal object."""
    if not isinstance(mechanics, dict):
        return "mechanics must be an object"
    for key, value in mechanics.items():
        if key in _INT_KEYS:
            if not isinstance(value, int) or isinstance(value, bool):
                return f"mechanic {key!r} requires an integer value"
        elif key in _STR_KEYS:
            if not isinstance(value, str) or not value.strip():
                return f"mechanic {key!r} requires non-empty text"
        else:
            return f"unknown mechanic {key!r} (known: {', '.join(KNOWN_MECHANICS)})"
    return None


def effective_ac(base_ac: int, mechanics_list: list[dict]) -> int:
    """Fold active-effect mechanics into an effective AC.

    The best ``ac_override`` replaces the base when higher (mage armor on
    an unarmored AC 12 wizard -> 15, but never lowers plate), then every
    ``ac_bonus`` stacks on top (shield, cover).
    """
    overrides = [m["ac_override"] for m in mechanics_list if "ac_override" in m]
    ac = max([base_ac, *overrides])
    ac += sum(m["ac_bonus"] for m in mechanics_list if "ac_bonus" in m)
    return ac


def clock_expired(effect: dict, day: int, minutes: int) -> bool:
    """Whether an effect's world-clock expiry has been reached (inclusive)."""
    if effect.get("expires_day") is None:
        return False
    return (day, minutes) >= (effect["expires_day"], effect["expires_minutes"])


def remaining_minutes(effect: dict, day: int, minutes: int) -> int | None:
    """World-clock minutes until expiry, or None for untimed effects."""
    if effect.get("expires_day") is None:
        return None
    return (
        (effect["expires_day"] - day) * _MINUTES_PER_DAY
        + effect["expires_minutes"] - minutes
    )
