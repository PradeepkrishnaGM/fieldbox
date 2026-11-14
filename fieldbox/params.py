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
    # Supply sources (PLAN.md §3.10). Land supply is traced from the HQ and
    # from FOBs with a movement-point budget each; roads stretch it. Sea
    # supply is traced over water from each own port. Aircraft rearm within a
    # hex radius of an own airfield or FOB (a radius, not a trace).
    hq_supply_budget: float = 12.0      # land trace budget from the HQ
    fob_supply_budget: float = 6.0      # land trace budget from each FOB
    port_sea_budget: float = 20.0       # sea trace budget from each port
    air_rearm_radius: int = 2           # aircraft within this many hexes of an airfield/FOB are in supply
    fob_air_capacity: int = 2           # aircraft a FOB can rearm per turn (stub until air capacity matters)
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
                     can_capture=False, entrenches=True, builds=True),
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
# Temporary build behaviour (M4) for the scripted agents until the M7 commander.
TEMP_BUILD_LIST = ("INFANTRY", "TANK", "ARTILLERY", "SAM")  # round-robin at land-producing structures
TEMP_AIR_BUILD_LIST = ("HELICOPTER", "JET")                 # round-robin at airfields (a second PP sink)
TEMP_MAX_FOBS = 2           # engineers stop building FOBs once this many exist (or are being built)
TEMP_FOB_SPACING = 4        # a new FOB must be at least this far from the HQ and other FOBs
TEMP_QUEUE_DEPTH = 2        # never queue more than this many units per structure
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
class EconomyParams:
    hq_income: int = 10                 # PP per turn from the HQ (x strength)
    industry_income: int = 15           # PP per turn per industry (x strength x power factor)
    power_output: int = 20              # power one power plant supplies (x strength)
    max_queue: int = 3                  # production queue depth per producing structure
    fob_cost: int = 40                  # PP, paid when the engineer is ordered to build a FOB
    fob_build_turns: int = 3            # turns an engineer needs to finish a FOB
    structure_repair_rate: float = 2.0  # strength regained per turn in supply and not attacked
    engineer_repair_rate: float = 10.0  # extra strength per turn from an engineer's Repair order
    capture_strength_cap: float = 50.0  # a captured structure keeps at most this strength
    cp_base: int = 4                    # command points with no data center
    cp_datacenter: int = 8              # extra command points from a full-strength, fully powered data center
    # One-time VP for destroying an enemy structure, by type.
    structure_vp: Tuple[Tuple[str, int], ...] = (
        ("HQ", 0), ("INDUSTRY", 6), ("POWER", 5), ("DATACENTER", 8),
        ("RADAR", 2), ("AIRFIELD", 5), ("PORT", 4), ("FOB", 2))


ECONOMY = EconomyParams()


@dataclass(frozen=True)
class FogParams:
    structure_spot: int = 2             # every structure sees this many hexes around it
    radar_range_air_sea: int = 10       # radar range against air and sea units (x strength x power; halved with no data center)
    radar_range_ground: int = 4         # radar range against land units and structures (same scaling)
    ghost_ttl: int = 5                  # turns a lost contact stays on the map as a ghost


FOG = FogParams()

# Structure table (PLAN.md §3.3): defence, capturable, power demand, what it
# builds. `produces` is the domain of units it can build, or None.
STRUCTURE_STATS: Dict[str, dict] = {
    "HQ": dict(defense=2.0, capturable=False, power_demand=0, produces="LAND", label="HQ"),
    "INDUSTRY": dict(defense=0.8, capturable=True, power_demand=8, produces="LAND", label="IND"),
    "POWER": dict(defense=0.7, capturable=True, power_demand=0, produces=None, label="PWR"),
    "DATACENTER": dict(defense=0.9, capturable=False, power_demand=10, produces=None, label="DC"),
    "RADAR": dict(defense=0.5, capturable=True, power_demand=2, produces=None, label="RDR"),
    "AIRFIELD": dict(defense=0.8, capturable=True, power_demand=4, produces="AIR", label="AIR"),
    "PORT": dict(defense=0.8, capturable=True, power_demand=4, produces="SEA", label="PRT"),
    "FOB": dict(defense=0.6, capturable=True, power_demand=2, produces=None, label="FOB"),
}


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


# ---------------------------------------------------------------------------
# Doctrine genome and commander (M7)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GenomeParams:
    mut_prob: float = 0.3               # per-gene mutation probability
    mut_sigma: float = 0.08             # gaussian step of a mutation
    cx_prob: float = 0.5                # probability that crossover happens at all (else child = copy of parent A)
    # Decoder ranges (trait 0 -> first value, trait 1 -> second).
    required_ratio_mult: Tuple[float, float] = (1.6, 0.6)   # aggression: force-ratio multiplier
    concentration: Tuple[float, float] = (0.4, 0.95)        # share of the strike force on the main effort
    reserve: Tuple[float, float] = (0.0, 0.3)               # share of land units kept back
    retreat: Tuple[float, float] = (15.0, 50.0)             # withdraw below this strength


GENOME = GenomeParams()

# Default genome values (the "doctrine" baseline). Anything not listed is 0.5.
GENOME_DEFAULTS: Dict[str, float] = {
    "aggression": 0.6,                  # decodes to ratio multiplier 1.0 = the old doctrine
    "concentration": 0.7, "reserve": 0.0,
    "retreat": 0.43,                    # decodes to 30 = the old COMBAT_INEFFECTIVE
    "expansion": 0.5, "econ_invest": 0.5,
    "w_build_infantry": 0.25, "w_build_tank": 0.25, "w_build_ifv": 0.0, "w_build_recon": 0.0,
    "w_build_artillery": 0.25, "w_build_sam": 0.25, "w_build_helicopter": 0.5,
    "w_build_jet": 0.5, "w_build_ship": 0.0, "w_build_engineer": 0.0,
    "defend_hq": 0.3, "defend_industry": 0.2, "defend_power": 0.2, "defend_datacenter": 0.2,
    "target_units": 0.5, "target_industry": 0.5, "target_power": 0.5, "target_datacenter": 0.5,
    "target_hq": 0.5, "target_airfield": 0.5,
    "air_role": 0.2, "fob_forward": 0.5, "adaptivity": 0.2,
}


@dataclass(frozen=True)
class CommanderParams:
    # Target scoring: base value in VP-equivalents.
    horizon: float = 15.0               # capital base = province value x horizon x swing
    structure_base: Tuple[Tuple[str, float], ...] = (
        ("HQ", 200.0), ("INDUSTRY", 40.0), ("POWER", 35.0), ("DATACENTER", 35.0),
        ("AIRFIELD", 25.0), ("PORT", 15.0), ("FOB", 15.0), ("RADAR", 10.0))
    unit_group_coef: float = 0.5        # unit-group base = coef x sum(cost x strength/100)
    dist_div_frac: float = 0.25         # distance divisor D = map width x this
    base_ratio: float = 1.5             # strike/defence ratio that counts as fully feasible (x aggression multiplier)
    assumed_defence: float = 60.0       # cost-points assumed to defend an unseen enemy-held target
    hysteresis: float = 1.25            # a rival must beat the current main effort by this factor
    secondary_min_dist: int = 4         # secondary effort must be this far from the main one
    secondary_min_force: int = 4        # strike units needed before a secondary effort is opened
    # Garrisons and reserve.
    garrison_radius: int = 6            # an enemy this close to an own asset makes it "threatened"
    garrison_max: int = 3               # units at a threatened asset when its defend weight is 1
    garrison_always: float = 0.75       # defend weight at/above which one unit always stands guard
    hold_back_frac: float = 0.5         # garrison + reserve never exceed this share of land units
    # Build plan.
    econ_floor_mult: float = 1.5        # PP held back = econ_invest x this x FOB cost while FOBs are wanted
    # Adaptation (PLAN 4.4).
    adapt_interval: int = 5
    adapt_window: int = 10
    adapt_defend_gain: float = 0.25     # defend-weight boost for a structure type hit in the window (x adaptivity)
    adapt_min_change: float = 0.05      # emit an `adapted` event only when a shift moves by this much
    # Land task labels need these thresholds.
    own_hit_drop: float = 1.0           # strength loss that counts as "hit"


COMMANDER = CommanderParams()

# Enemy unit type -> own unit types that beat it.
COUNTER_TABLE: Dict[str, Tuple[str, ...]] = {
    "HELICOPTER": ("SAM",), "JET": ("SAM",),
    "SAM": ("TANK", "IFV"),
    "TANK": ("ARTILLERY", "INFANTRY"), "IFV": ("ARTILLERY", "INFANTRY"),
    "SHIP": ("JET", "ARTILLERY"),
    "ARTILLERY": ("RECON", "JET"),
    "INFANTRY": ("TANK",),
}


# ---------------------------------------------------------------------------
# Evolution league (M8)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvoParams:
    world: str = "standard"             # world preset the games are played on
    pop_size: int = 16
    generations: int = 40
    elites: int = 2                     # copied unchanged into the next generation
    tourn_size: int = 3                 # tournament selection size
    mut_prob: float = 0.3               # per-gene mutation probability
    mut_sigma: float = 0.08             # gaussian step of a mutation (generation 0 uses 3x)
    cx_prob: float = 0.5                # crossover probability
    maps_per_gen: int = 4               # fresh map seeds per generation
    n_peer: int = 4                     # games each genome starts against peers
    n_hof: int = 2                      # ... against hall-of-fame members (anchors while it is empty)
    n_anchor: int = 2                   # ... against the fixed anchors
    hof_size: int = 10                  # hall-of-fame capacity (oldest dropped)
    anchor_eval_games: int = 10         # extra champion games vs anchors, for the Elo estimate
    xgen_stride: int = 5                # champions of every n-th generation meet in the final matrix
    xgen_games: int = 4                 # games per pair in that matrix
    workers: int = 10                   # processes (leave 2 of 12 threads free)
    vp_weight: float = 0.1              # fitness = result + vp_weight * tanh(vp_margin / vp_scale)
    vp_scale: float = 100.0
    status_every: int = 8               # rewrite status.json after this many finished games


EVO = EvoParams()
EVO_PRESETS: Dict[str, EvoParams] = {
    "evo-standard": EVO,
    "evo-tiny": EvoParams(world="tiny", pop_size=4, generations=2, elites=1, maps_per_gen=1,
                          n_peer=2, n_hof=1, n_anchor=1, hof_size=3, anchor_eval_games=3,
                          xgen_stride=1, xgen_games=1, workers=2, status_every=2),
}
# Fixed opponents. Their Elo (doctrine-default = 1500) is calibrated by
# `python -m fieldbox evolve --bench` and pasted here.
EVO_ANCHORS: Tuple[str, ...] = ("doctrine-default", "rush", "doctrine-legacy")
EVO_ANCHOR_ELO: Dict[str, float] = {"doctrine-default": 1500.0, "rush": 1030.0,
                                    "doctrine-legacy": 1500.0}   # calibrated on standard, 8 maps x 2 colours
