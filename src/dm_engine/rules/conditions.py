"""The 15 SRD conditions plus exhaustion levels: aggregated mechanical flags.

`effects_for` folds a creature's active conditions into one flag set.
Interactions that depend on range (prone, paralyzed auto-crit) live in
`attack_interaction`, which takes an `engaged` flag — in the band system
(FC-4), engaged means within 5 ft.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from dm_engine.rules.checks import AdvantageMode, combine_advantage

CONDITIONS = frozenset({
    "blinded", "charmed", "deafened", "exhaustion", "frightened", "grappled",
    "incapacitated", "invisible", "paralyzed", "petrified", "poisoned",
    "prone", "restrained", "stunned", "unconscious",
})

_INCAPACITATING = frozenset({"incapacitated", "paralyzed", "petrified", "stunned", "unconscious"})
_NO_MOVE = frozenset({"grappled", "paralyzed", "petrified", "restrained", "stunned", "unconscious"})
_NO_SPEECH = frozenset({"paralyzed", "petrified", "unconscious"})
_ATTACKED_WITH_ADVANTAGE = frozenset(
    {"blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"}
)
_ATTACKS_WITH_DISADVANTAGE = frozenset({"blinded", "frightened", "poisoned", "prone", "restrained"})
_AUTO_FAIL_STR_DEX = frozenset({"paralyzed", "petrified", "stunned", "unconscious"})
_MELEE_AUTO_CRIT = frozenset({"paralyzed", "unconscious"})


class ConditionEffects(BaseModel):
    can_take_actions: bool = True
    can_take_reactions: bool = True
    can_move: bool = True
    can_speak: bool = True
    speed_multiplier: float = 1.0
    attacks_have_advantage: bool = False  # invisible attacker
    attacks_have_disadvantage: bool = False
    attacked_with_advantage: bool = False  # attackers roll with advantage
    attacked_with_disadvantage: bool = False  # invisible target
    checks_have_disadvantage: bool = False
    saves_have_disadvantage: bool = False  # exhaustion >= 3
    dex_saves_have_disadvantage: bool = False  # restrained
    auto_fail_str_dex_saves: bool = False
    melee_hits_are_critical: bool = False  # paralyzed/unconscious, hit from within 5 ft
    prone: bool = False
    resist_all_damage: bool = False  # petrified
    auto_fail_sight_checks: bool = False  # blinded
    auto_fail_hearing_checks: bool = False  # deafened
    cannot_attack_charmer: bool = False  # charmed
    cannot_approach_fear_source: bool = False  # frightened
    hp_max_halved: bool = False  # exhaustion >= 4
    dead: bool = False  # exhaustion 6


def effects_for(conditions: Iterable[str], exhaustion_level: int = 0) -> ConditionEffects:
    names = set(conditions)
    unknown = names - CONDITIONS
    if unknown:
        raise ValueError(f"unknown conditions: {sorted(unknown)}")
    if not 0 <= exhaustion_level <= 6:
        raise ValueError(f"exhaustion level out of range: {exhaustion_level}")
    if "exhaustion" in names and exhaustion_level == 0:
        exhaustion_level = 1

    e = ConditionEffects()
    if names & _INCAPACITATING:
        e.can_take_actions = False
        e.can_take_reactions = False
    if names & _NO_MOVE:
        e.can_move = False
    if names & _NO_SPEECH:
        e.can_speak = False
    if names & _ATTACKED_WITH_ADVANTAGE:
        e.attacked_with_advantage = True
    if names & _ATTACKS_WITH_DISADVANTAGE:
        e.attacks_have_disadvantage = True
    if names & _AUTO_FAIL_STR_DEX:
        e.auto_fail_str_dex_saves = True
    if names & _MELEE_AUTO_CRIT:
        e.melee_hits_are_critical = True
    if names & {"prone", "unconscious"}:
        e.prone = True
    if "invisible" in names:
        e.attacks_have_advantage = True
        e.attacked_with_disadvantage = True
    if names & {"poisoned", "frightened"}:
        e.checks_have_disadvantage = True
    if "restrained" in names:
        e.dex_saves_have_disadvantage = True
    if "petrified" in names:
        e.resist_all_damage = True
    if "blinded" in names:
        e.auto_fail_sight_checks = True
    if "deafened" in names:
        e.auto_fail_hearing_checks = True
    if "charmed" in names:
        e.cannot_attack_charmer = True
    if "frightened" in names:
        e.cannot_approach_fear_source = True

    if exhaustion_level >= 1:
        e.checks_have_disadvantage = True
    if exhaustion_level >= 2:
        e.speed_multiplier = 0.5
    if exhaustion_level >= 3:
        e.attacks_have_disadvantage = True
        e.saves_have_disadvantage = True
    if exhaustion_level >= 4:
        e.hp_max_halved = True
    if exhaustion_level >= 5:
        e.can_move = False
    if exhaustion_level >= 6:
        e.dead = True
    return e


class AttackInteraction(BaseModel):
    mode: AdvantageMode
    auto_crit_on_hit: bool


def attack_interaction(
    attacker: ConditionEffects, target: ConditionEffects, *, engaged: bool
) -> AttackInteraction:
    """Advantage state of one attack, from both creatures' conditions.

    Prone targets grant advantage to engaged attackers and impose
    disadvantage on everyone else. Paralyzed/unconscious targets turn
    engaged hits into critical hits.
    """
    advantage = (
        attacker.attacks_have_advantage
        or target.attacked_with_advantage
        or (target.prone and engaged)
    )
    disadvantage = (
        attacker.attacks_have_disadvantage
        or target.attacked_with_disadvantage
        or (target.prone and not engaged)
    )
    return AttackInteraction(
        mode=combine_advantage(advantage, disadvantage),
        auto_crit_on_hit=target.melee_hits_are_critical and engaged,
    )
