"""Every tunable number in Fieldbox lives here.

Rules (PLAN.md §0.4):
- each default has a comment;
- defaults listed in PLAN.md are not changed without the user's approval;
- this module imports nothing from the engine. Tables are keyed by enum
  *names* (``"LAND"``, ``"PLAIN"``, ``"TANK"``) and each engine module
  converts them to its own enums. That keeps the import graph acyclic.

M1 holds the original engine's values, unchanged (the golden-hash test proves it). Later
milestones add WorldParams, EconomyParams, FogParams and the rest of §6.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Combat
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CombatParams:
    base_loss: float = 12.0             # percentage points lost at even odds
    min_loss: float = 1.5               # floor on loss per engagement
    max_loss: float = 45.0              # cap on loss per engagement
    flank_bonus: float = 0.15           # per extra friendly unit adjacent to the target
    entrench_bonus: float = 0.18        # defensive multiplier gained per entrenchment level
    max_entrench: int = 3               # entrenchment levels a unit can reach
    no_return_fire_mult: float = 0.60   # defender that cannot engage the attacker's domain
    indirect_coef: float = 0.55         # area fire: attrition, not decision
    direct_ad_coef: float = 1.00        # aimed missile engagement (SAM)


COMBAT = CombatParams()

# ---------------------------------------------------------------------------
# Supply
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SupplyParams:
    supply_max: float = 100.0           # full stock
    # Movement-point budget for tracing supply out from a depot, per domain.
    # Calibrated so a side's supply reaches about two thirds of the map; at
    # 13 the road net covered 87% of the board and supply meant nothing.
    supply_range: Tuple[Tuple[str, float], ...] = (
        ("LAND", 7.0), ("AIR", 6.0), ("SEA", 12.0))
    resupply_rate: float = 34.0         # in supply: full stock in about three turns
    drain_rate: float = 16.0            # out of supply: dry in about six turns
    starvation_loss: float = 4.0        # strength lost per turn at zero stock
    repair_rate: float = 3.0            # strength regained per turn in supply, out of contact
    repair_cap: float = 100.0           # repair never goes above this strength
    supply_effective: float = 50.0      # stock above which there is no combat penalty
    supply_floor: float = 0.45          # combat power multiplier at zero stock


SUPPLY = SupplyParams()

# ---------------------------------------------------------------------------
# Terrain and movement (keys: Domain and Terrain names)
# ---------------------------------------------------------------------------

IMPASSABLE = 999.0      # movement-cost sentinel: cannot enter
ROAD_COST = 0.5         # land cost to enter a road hex, any terrain
AIR_COST = 1.0          # air ignores terrain entirely
SEA_COST = 1.0          # ships pay this on water

# Movement point cost to ENTER a hex, per domain.
MOVE_COST: Dict[str, Dict[str, float]] = {
    "LAND": {"PLAIN": 1.0, "FOREST": 2.0, "HILL": 2.0, "URBAN": 2.0,
             "WATER": IMPASSABLE},
    "AIR": {"PLAIN": AIR_COST, "FOREST": AIR_COST, "HILL": AIR_COST,
            "URBAN": AIR_COST, "WATER": AIR_COST},
    "SEA": {"PLAIN": IMPASSABLE, "FOREST": IMPASSABLE, "HILL": IMPASSABLE,
            "URBAN": IMPASSABLE, "WATER": SEA_COST},
}

# Multiplier on a land defender's combat power in this terrain.
DEF_MULT: Dict[str, float] = {
    "PLAIN": 1.0,       # open ground: no help
    "FOREST": 1.3,      # cover and concealment
    "HILL": 1.4,        # elevation and observation
    "URBAN": 1.8,       # buildings are fortresses
    "WATER": 1.0,       # nothing to hide behind
}

# ---------------------------------------------------------------------------
# Units (keys: UnitType names, in UnitType order)
# ---------------------------------------------------------------------------
# move: MP per turn; attack/defense: combat coefficients; reach: max range
# (1 = direct fire); spot: detection radius; domain/targets: Domain names;
# can_capture: may hold an objective; entrenches: may dig in; fire_coef:
# ranged effectiveness; upkeep: supply burned per turn; combat_cost: supply
# burned per attack. label/glyph/sidc are display data kept with the stats.

UNIT_STATS: Dict[str, dict] = {
    # --- Land: manoeuvre and holding ground ---
    "INFANTRY": dict(move=4, attack=1.00, defense=1.30, reach=1, spot=2,
                     label="INF", glyph="I", sidc_function="UCI---",
                     domain="LAND", targets=("LAND",),
                     can_capture=True, entrenches=True),
    "TANK": dict(move=7, attack=1.70, defense=1.00, reach=1, spot=2,
                 label="TNK", glyph="A", sidc_function="UCA---",
                 domain="LAND", targets=("LAND",),
                 can_capture=True, entrenches=True),
    "IFV": dict(move=8, attack=1.20, defense=0.95, reach=1, spot=3,
                label="IFV", glyph="M", sidc_function="UCIZ--",
                domain="LAND", targets=("LAND",),
                can_capture=True, entrenches=True),
    "RECON": dict(move=9, attack=0.55, defense=0.60, reach=1, spot=4,
                  label="RCN", glyph="R", sidc_function="UCR---",
                  domain="LAND", targets=("LAND",),
                  can_capture=True, entrenches=False),
    # --- Land: fires and air defence ---
    # Guns eat ammunition: a battery out of supply is just a slow truck.
    "ARTILLERY": dict(move=3, attack=1.45, defense=0.45, reach=3, spot=1,
                      label="ART", glyph="T", sidc_function="UCF---",
                      domain="LAND", targets=("LAND", "SEA"),
                      can_capture=False, entrenches=True,
                      upkeep=3.0, combat_cost=20.0),
    # Air defence counters air power and nothing else; fragile and slow.
    "SAM": dict(move=2, attack=2.60, defense=0.35, reach=4, spot=3,
                label="SAM", glyph="S", sidc_function="UCDS--",
                domain="LAND", targets=("AIR",),
                can_capture=False, entrenches=True,
                fire_coef=COMBAT.direct_ad_coef, upkeep=3.0, combat_cost=16.0),
    # --- Air: fast, cannot hold ground, cannot dig in ---
    "HELICOPTER": dict(move=10, attack=1.40, defense=0.70, reach=1, spot=3,
                       label="HEL", glyph="H", sidc_function="MFH---",
                       domain="AIR", targets=("LAND", "SEA"),
                       can_capture=False, entrenches=False,
                       upkeep=16.0, combat_cost=14.0),
    "JET": dict(move=16, attack=1.90, defense=0.60, reach=1, spot=4,
                label="JET", glyph="J", sidc_function="MFF---",
                domain="AIR", targets=("LAND", "SEA"),
                can_capture=False, entrenches=False,
                upkeep=22.0, combat_cost=16.0),
    # --- Sea ---
    "SHIP": dict(move=5, attack=1.55, defense=1.10, reach=4, spot=4,
                 label="SHP", glyph="N", sidc_function="CLFF--",
                 domain="SEA", targets=("LAND", "SEA"),
                 can_capture=False, entrenches=False,
                 upkeep=4.0, combat_cost=14.0),
}
UNIT_DEFAULT_UPKEEP = 2.0         # supply per turn when a unit sets no upkeep
UNIT_DEFAULT_COMBAT_COST = 10.0   # supply per attack when a unit sets none

# Multiplier on the ATTACKER's power, by attacker type and target terrain.
# Close terrain punishes vehicles; infantry is comfortable everywhere.
ATTACK_TERRAIN_MOD: Dict[str, Dict[str, float]] = {
    "INFANTRY": {"FOREST": 1.0, "URBAN": 1.0},
    "TANK": {"FOREST": 0.65, "URBAN": 0.55, "HILL": 0.85},
    "IFV": {"FOREST": 0.75, "URBAN": 0.65, "HILL": 0.90},
    "RECON": {"FOREST": 0.8, "URBAN": 0.7},
    "ARTILLERY": {},
    "SAM": {},
    "HELICOPTER": {"FOREST": 0.85, "URBAN": 0.75},
    "JET": {"FOREST": 0.9, "URBAN": 0.8},
    "SHIP": {},
}

# ---------------------------------------------------------------------------
# Doctrine agent (doctrinal tactics)
# ---------------------------------------------------------------------------

# Local force ratio required to launch a deliberate attack, by defender terrain.
RATIO_REQUIRED: Dict[str, float] = {
    "URBAN": 3.0,
    "FOREST": 2.0,
    "HILL": 2.0,
    "PLAIN": 1.3,
    "WATER": 1.3,
}
COMBAT_INEFFECTIVE = 30.0   # strength below which a unit withdraws
ARTY_STANDOFF = 1           # margin the guns keep inside their own reach
MIN_STANDOFF = 2            # never set up within this many hexes of an enemy
SAFETY_WEIGHT = 3.0         # weight on distance-from-enemy vs. targets ranged
RECON_SEARCH_SPAN = 5       # cap on the recon candidate disc (move is 9)
RECON_MIN_STANDOFF = 3      # closer than this is contact, not observation
AIR_SEARCH_SPAN = 6         # cap on the air candidate disc (a jet moves 16)
RESUPPLY_THRESHOLD = 35.0   # stock below which a cut-off unit disengages
EXPOSURE_PENALTY = 0.30     # extra force ratio demanded per uncovered flank
SAM_MIN_STANDOFF = 3        # air defence cannot fight ground: keep clear
AIR_COVER_BONUS = 8.0       # value of sitting under a friendly SAM umbrella

# Scripted agents
COUNTERATTACK_THRESHOLD = 25.0  # rush agent only counterattacks an enemy this broken

# ---------------------------------------------------------------------------
# Diagnostic report thresholds (flags worth a look, not pass/fail)
# ---------------------------------------------------------------------------

REPORT_TOTAL_LOSS = 0.95       # a type wiped out this often is worth a look
REPORT_LOSS_ASYMMETRY = 0.30   # gap between the sides' loss rates for a type
REPORT_NEVER_FIRES = 0.02      # contribution per unit-turn below this is idle
REPORT_STUCK = 0.60            # fraction of its turns spent on its start hex
REPORT_CUTOFF_HEAVY = 0.50     # fraction of unit-turns spent out of supply
REPORT_COLOUR_SWING = 0.50     # win-rate gap between playing BLUE and RED
