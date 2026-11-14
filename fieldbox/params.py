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
    river_attack_mult: float = 0.8      # land attacks INTO a river hex (wired in at M3)
    mountain_vehicle_attack_mult: float = 0.5  # tanks/IFVs attacking into mountains


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
RIVER_COST = 1.0        # extra land cost to enter a river hex (no road/bridge)
AIR_COST = 1.0          # air ignores terrain entirely
SEA_COST = 1.0          # ships pay this on water

# Movement point cost to ENTER a hex, per domain.
MOVE_COST: Dict[str, Dict[str, float]] = {
    "LAND": {"PLAIN": 1.0, "FOREST": 2.0, "HILL": 2.0, "URBAN": 2.0,
             "WATER": IMPASSABLE, "MOUNTAIN": 3.0},
    "AIR": {"PLAIN": AIR_COST, "FOREST": AIR_COST, "HILL": AIR_COST,
            "URBAN": AIR_COST, "WATER": AIR_COST, "MOUNTAIN": AIR_COST},
    "SEA": {"PLAIN": IMPASSABLE, "FOREST": IMPASSABLE, "HILL": IMPASSABLE,
            "URBAN": IMPASSABLE, "WATER": SEA_COST, "MOUNTAIN": IMPASSABLE},
}

# Multiplier on a land defender's combat power in this terrain.
DEF_MULT: Dict[str, float] = {
    "PLAIN": 1.0,       # open ground: no help
    "FOREST": 1.3,      # cover and concealment
    "HILL": 1.4,        # elevation and observation
    "URBAN": 1.8,       # buildings are fortresses
    "WATER": 1.0,       # nothing to hide behind
    "MOUNTAIN": 1.6,    # rugged high ground
}

# ---------------------------------------------------------------------------
# Units (keys: UnitType names, in UnitType order)
# ---------------------------------------------------------------------------
# move: MP per turn; attack/defense: combat coefficients; reach: max range
# (1 = direct fire); spot: detection radius; cost: PP to build; build_turns:
# turns in the queue (both used from M4); domain/targets: Domain names;
# can_capture: may hold an objective; entrenches: may dig in; fire_coef:
# ranged effectiveness; upkeep: supply burned per turn; combat_cost: supply
# burned per attack. label/glyph/sidc are display data kept with the stats.

UNIT_STATS: Dict[str, dict] = {
    # --- Land: manoeuvre and holding ground ---
    "INFANTRY": dict(move=4, attack=1.00, defense=1.30, reach=1, spot=2,
                  cost=20, build_turns=2,
                     label="INF", glyph="I", sidc_function="UCI---",
                     domain="LAND", targets=("LAND",),
                     can_capture=True, entrenches=True),
    "TANK": dict(move=7, attack=1.70, defense=1.00, reach=1, spot=2,
              cost=45, build_turns=3,
                 label="TNK", glyph="A", sidc_function="UCA---",
                 domain="LAND", targets=("LAND",),
                 can_capture=True, entrenches=True),
    "IFV": dict(move=8, attack=1.20, defense=0.95, reach=1, spot=3,
             cost=35, build_turns=3,
                label="IFV", glyph="M", sidc_function="UCIZ--",
                domain="LAND", targets=("LAND",),
                can_capture=True, entrenches=True),
    "RECON": dict(move=9, attack=0.55, defense=0.60, reach=1, spot=4,
               cost=20, build_turns=2,
                  label="RCN", glyph="R", sidc_function="UCR---",
                  domain="LAND", targets=("LAND",),
                  can_capture=True, entrenches=False),
    # --- Land: fires and air defence ---
    # Guns eat ammunition: a battery out of supply is just a slow truck.
    "ARTILLERY": dict(move=3, attack=1.45, defense=0.45, reach=3, spot=1,
                   cost=40, build_turns=3,
                      label="ART", glyph="T", sidc_function="UCF---",
                      domain="LAND", targets=("LAND", "SEA"),
                      can_capture=False, entrenches=True,
                      upkeep=3.0, combat_cost=20.0),
    # Air defence counters air power and nothing else; fragile and slow.
    "SAM": dict(move=2, attack=2.60, defense=0.35, reach=4, spot=3,
             cost=45, build_turns=3,
                label="SAM", glyph="S", sidc_function="UCDS--",
                domain="LAND", targets=("AIR",),
                can_capture=False, entrenches=True,
                fire_coef=COMBAT.direct_ad_coef, upkeep=3.0, combat_cost=16.0),
    # --- Air: fast, cannot hold ground, cannot dig in ---
    "HELICOPTER": dict(move=10, attack=1.40, defense=0.70, reach=1, spot=3,
                    cost=50, build_turns=3,
                       label="HEL", glyph="H", sidc_function="MFH---",
                       domain="AIR", targets=("LAND", "SEA"),
                       can_capture=False, entrenches=False,
                       upkeep=16.0, combat_cost=14.0),
    "JET": dict(move=16, attack=1.90, defense=0.60, reach=1, spot=4,
             cost=70, build_turns=4,
                label="JET", glyph="J", sidc_function="MFF---",
                domain="AIR", targets=("LAND", "SEA"),
                can_capture=False, entrenches=False,
                upkeep=22.0, combat_cost=16.0),
    # --- Sea ---
    "SHIP": dict(move=5, attack=1.55, defense=1.10, reach=4, spot=4,
              cost=80, build_turns=5,
                 label="SHP", glyph="N", sidc_function="CLFF--",
                 domain="SEA", targets=("LAND", "SEA"),
                 can_capture=False, entrenches=False,
                 upkeep=4.0, combat_cost=14.0),
    # --- Added in M3: builds FOBs and repairs structures (from M4) ---
    "ENGINEER": dict(move=4, attack=0.40, defense=0.70, reach=1, spot=2,
                     cost=25, build_turns=2,
                     label="ENG", glyph="E", sidc_function="UCE---",
                     domain="LAND", targets=("LAND",),
                     can_capture=False, entrenches=True),
}
UNIT_DEFAULT_UPKEEP = 2.0         # supply per turn when a unit sets no upkeep
UNIT_DEFAULT_COMBAT_COST = 10.0   # supply per attack when a unit sets none

# Multiplier on the ATTACKER's power, by attacker type and target terrain.
# Close terrain punishes vehicles; infantry is comfortable everywhere.
ATTACK_TERRAIN_MOD: Dict[str, Dict[str, float]] = {
    "INFANTRY": {"FOREST": 1.0, "URBAN": 1.0},
    "TANK": {"FOREST": 0.65, "URBAN": 0.55, "HILL": 0.85,
             "MOUNTAIN": COMBAT.mountain_vehicle_attack_mult},
    "IFV": {"FOREST": 0.75, "URBAN": 0.65, "HILL": 0.90,
            "MOUNTAIN": COMBAT.mountain_vehicle_attack_mult},
    "RECON": {"FOREST": 0.8, "URBAN": 0.7},
    "ARTILLERY": {},
    "SAM": {},
    "HELICOPTER": {"FOREST": 0.85, "URBAN": 0.75},
    "JET": {"FOREST": 0.9, "URBAN": 0.8},
    "SHIP": {},
    "ENGINEER": {"FOREST": 1.0, "URBAN": 1.0},
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
    "MOUNTAIN": 2.5,    # NEW in M2: between hill (2.0) and urban (3.0), matching its 1.6 defence
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
EXPANSION_SAFE_RADIUS = 4    # NEW (M3): fast capturing units expand to unheld capitals while no enemy land unit is this close
THREAT_RADIUS = 2           # NEW (M3): infantry keeps holding a taken capital while an enemy that can capture is this close

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

# ---------------------------------------------------------------------------
# World generation, start positions, match length (PLAN.md §3.1, §6.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorldParams:
    width: int = 48            # map width in columns; must be even
    height: int = 32           # map height in rows; must be even
    noise_scale: float = 9.0   # base wavelength of the elevation noise, in hexes
    edge_falloff: float = 0.35  # elevation lost at the north/south edges (stops one big landmass)
    water_fraction: float = 0.30  # target share of WATER hexes
    min_island: int = 4        # land specks smaller than this become water
    min_lake: int = 3          # lakes smaller than this become land
    min_open_sea: int = 12     # NEW: a sea must have at least this many hexes to host ports and ships
    central_sea_length: float = 1.00  # NEW: central sea ellipse length, fraction of map width
    central_sea_width: float = 0.20   # NEW: central sea ellipse height, fraction of map height
    central_sea_depth: float = 0.40   # NEW: elevation removed at the sea's centre (fades to 0 at the rim)
    central_sea_core: float = 0.50    # NEW: inner fraction of the ellipse forced to WATER
    mountain_fraction: float = 0.06  # share of land that is MOUNTAIN
    hill_fraction: float = 0.12      # share of land that is HILL
    forest_threshold: float = 0.58   # moisture above this turns PLAIN into FOREST
    rivers_per_half: int = 3   # river sources per half (each is mirrored)
    min_river_len: int = 4     # rivers shorter than this are dropped
    cities_per_half: int = 5   # city centres per half
    city_spacing: int = 5      # minimum hex distance between city centres
    provinces_per_half: int = 8  # RISK provinces per half
    region_size: int = 3       # provinces per region (continent bonus group)
    min_home_provinces: int = 3  # provinces each side owns at the start
    province_base_value: int = 1  # value of any province
    city_value: int = 2        # extra value of a province containing a city
    region_bonus: int = 4      # per-turn bonus for holding a whole region

    def __post_init__(self):
        if self.width % 2 or self.height % 2:
            raise ValueError("world width and height must both be even")


@dataclass(frozen=True)
class StartParams:
    start_pp: int = 100        # production points each side starts with
    # Starting structures per side, besides the HQ.
    structures: Tuple[Tuple[str, int], ...] = (
        ("INDUSTRY", 2), ("POWER", 2), ("DATACENTER", 1),
        ("RADAR", 2), ("AIRFIELD", 1), ("PORT", 1))
    # Starting forces per side, in deployment order.
    forces: Tuple[Tuple[str, int], ...] = (
        ("INFANTRY", 4), ("TANK", 2), ("IFV", 2), ("RECON", 1),
        ("ARTILLERY", 2), ("SAM", 1), ("ENGINEER", 1),
        ("HELICOPTER", 1), ("JET", 1), ("SHIP", 1))


@dataclass(frozen=True)
class MatchParams:
    max_turns: int = 80        # a match ends at this turn if nobody has won


@dataclass(frozen=True)
class Params:
    world: WorldParams = WorldParams()
    start: StartParams = StartParams()
    match: MatchParams = MatchParams()


def _scaled_forces(fn) -> Tuple[Tuple[str, int], ...]:
    return tuple((name, fn(n)) for name, n in StartParams().forces)


PRESETS: Dict[str, Params] = {
    # tiny: for tests. Half the forces (at least 1 of each).
    "tiny": Params(
        world=WorldParams(
            width=24, height=16, noise_scale=6.0, min_island=3, min_lake=3,
            rivers_per_half=1, min_river_len=3, cities_per_half=2,
            city_spacing=4, provinces_per_half=3, region_size=3,
            min_home_provinces=2),   # 3 would leave no NEUTRAL province
        start=StartParams(forces=_scaled_forces(lambda n: max(1, n // 2))),
        match=MatchParams(max_turns=30)),
    # standard: the defaults above.
    "standard": Params(),
    # large: showcase. 1.5x forces, rounded up.
    "large": Params(
        world=WorldParams(
            width=64, height=40, rivers_per_half=4, cities_per_half=7,
            provinces_per_half=12),
        start=StartParams(forces=_scaled_forces(lambda n: (3 * n + 1) // 2)),
        match=MatchParams(max_turns=120)),
}


@dataclass(frozen=True)
class UIParams:
    window: Tuple[int, int] = (1600, 900)  # default window size in pixels
    min_window: Tuple[int, int] = (1280, 720)  # smallest allowed window
    fps: int = 60              # frame cap
    hex_size: int = 20         # hex circumradius in pixels at zoom 1.0
    zoom_min: float = 0.35     # camera zoom limits
    zoom_max: float = 3.0
    zoom_step: float = 0.05    # zoom is snapped to this, so surfaces can be cached per step
    turn_anim_ms: int = 600    # unit move animation per turn (used from M5)
    panel_width: int = 360     # right-hand side panel
