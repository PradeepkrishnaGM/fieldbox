"""Evaluation harness: matches, round-robin tournaments, Elo.

This is the part that turns "a game with some AI in it" into a benchmark you
can put a number on -- which is the version worth talking about in an
interview.
"""

from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from ..agents import Agent
from ..sim.engine import Game
from ..world import WorldMap, generate_world
from ..sim.units import Side


@dataclass
class MatchResult:
    winner: Optional[Side]
    vp: Dict[Side, float]
    turns: int
    survivors: Dict[Side, int]
    seed: int


def play_match(
    blue: Agent,
    red: Agent,
    world: WorldMap,
    seed: int = 0,
    max_turns: Optional[int] = None,
) -> MatchResult:
    """One WEGO match. Agents see only their Observation."""
    game = run_game(blue, red, world, seed, max_turns)
    return MatchResult(
        winner=game.winner(),
        vp=dict(game.vp),
        turns=game.turn,
        survivors={s: len(game.units_of(s)) for s in (Side.BLUE, Side.RED)},
        seed=seed,
    )


def run_game(blue: Agent, red: Agent, world: WorldMap, seed: int = 0,
             max_turns: Optional[int] = None, on_turn=None) -> Game:
    """Play a match to the end and return the finished Game. `on_turn(game)`
    is called after every turn (the recorder uses it)."""
    game = Game(world, seed=seed, max_turns=max_turns)
    agents = {Side.BLUE: blue, Side.RED: red}
    for side, agent in agents.items():
        agent.reset(side, world)
    while not game.is_over:
        game.step_turn({s: agents[s].act(game.observe(s)) for s in (Side.BLUE, Side.RED)})
        if on_turn is not None:
            on_turn(game)
    return game


@dataclass
class Record:
    wins: int = 0
    losses: int = 0
    draws: int = 0
    vp_for: float = 0.0
    vp_against: float = 0.0
    elo: float = 1000.0

    @property
    def played(self) -> int:
        return self.wins + self.losses + self.draws

    @property
    def score(self) -> float:
        return (self.wins + 0.5 * self.draws) / max(self.played, 1)


def _update_elo(a: Record, b: Record, score_a: float, k: float = 24.0) -> None:
    exp_a = 1.0 / (1.0 + 10 ** ((b.elo - a.elo) / 400.0))
    a.elo += k * (score_a - exp_a)
    b.elo += k * ((1.0 - score_a) - (1.0 - exp_a))


def tournament(
    agent_factories: Dict[str, callable],
    games_per_pair: int = 20,
    map_seeds: Tuple[int, ...] = (1, 2, 3, 4),
    verbose: bool = True,
    preset: str = "tiny",
) -> Dict[str, Record]:
    """Round robin, both colours, across several maps.

    Playing every pair on both sides cancels out any first-move advantage,
    and rotating map seeds stops agents from being tuned to one layout.
    """
    records: Dict[str, Record] = {n: Record() for n in agent_factories}
    worlds = [generate_world(s, preset) for s in map_seeds]

    start = time.perf_counter()
    total_turns = 0
    matches = 0

    for name_a, name_b in itertools.combinations(agent_factories, 2):
        for i in range(games_per_pair):
            world = worlds[i % len(worlds)]
            # Alternate colours between repetitions.
            if i % 2 == 0:
                blue_name, red_name = name_a, name_b
            else:
                blue_name, red_name = name_b, name_a
            blue = agent_factories[blue_name]()
            red = agent_factories[red_name]()
            result = play_match(blue, red, world, seed=i)
            total_turns += result.turns
            matches += 1

            rec_blue, rec_red = records[blue_name], records[red_name]
            rec_blue.vp_for += result.vp[Side.BLUE]
            rec_blue.vp_against += result.vp[Side.RED]
            rec_red.vp_for += result.vp[Side.RED]
            rec_red.vp_against += result.vp[Side.BLUE]

            if result.winner is Side.BLUE:
                rec_blue.wins += 1
                rec_red.losses += 1
                _update_elo(rec_blue, rec_red, 1.0)
            elif result.winner is Side.RED:
                rec_red.wins += 1
                rec_blue.losses += 1
                _update_elo(rec_blue, rec_red, 0.0)
            else:
                rec_blue.draws += 1
                rec_red.draws += 1
                _update_elo(rec_blue, rec_red, 0.5)

    elapsed = time.perf_counter() - start
    if verbose:
        print(format_table(records))
        print(
            f"\n{matches} matches, {total_turns} game-turns in {elapsed:.2f}s "
            f"({matches / elapsed:.1f} matches/s, "
            f"{total_turns * 2 / elapsed:.0f} side-turns/s)"
        )
    return records


def format_table(records: Dict[str, Record]) -> str:
    rows = sorted(records.items(), key=lambda kv: -kv[1].elo)
    head = f"{'agent':<12}{'elo':>7}{'W':>5}{'L':>5}{'D':>5}{'win%':>8}{'VP+':>9}{'VP-':>9}"
    lines = [head, "-" * len(head)]
    for name, rec in rows:
        lines.append(
            f"{name:<12}{rec.elo:>7.0f}{rec.wins:>5}{rec.losses:>5}{rec.draws:>5}"
            f"{rec.score * 100:>7.1f}%{rec.vp_for:>9.0f}{rec.vp_against:>9.0f}"
        )
    return "\n".join(lines)


def benchmark(steps: int = 2000, preset: str = "tiny") -> float:
    """Raw environment throughput in side-turns per second (two per WEGO
    turn), random vs random -- the number that decides whether RL is
    feasible at all."""
    from ..agents import RandomAgent
    world = generate_world(1, preset)
    a, b = RandomAgent(seed=1), RandomAgent(seed=2)
    done = 0
    start = time.perf_counter()
    while done < steps:
        game = Game(world, seed=done)
        a.reset(Side.BLUE, world)
        b.reset(Side.RED, world)
        while not game.is_over and done < steps:
            game.step_turn({Side.BLUE: a.act(game.observe(Side.BLUE)),
                            Side.RED: b.act(game.observe(Side.RED))})
            done += 2
    elapsed = time.perf_counter() - start
    return done / elapsed
