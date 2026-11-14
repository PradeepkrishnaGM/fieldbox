"""Command line interface.

    python -m fieldbox play --blue doctrine --red rush
    python -m fieldbox tournament --games 30
    python -m fieldbox bench
    python -m fieldbox export --out state.json
"""

from __future__ import annotations

import argparse
import sys
import time

from .agents import REGISTRY
from .league.arena import benchmark, play_match, tournament
from .sim.engine import Game
from .record.render import render, to_json
from .sim.scenario import PRESETS, Scenario
from .sim.units import Side


def _make(name: str):
    if name not in REGISTRY:
        raise SystemExit(f"unknown agent '{name}'. available: {', '.join(REGISTRY)}")
    return REGISTRY[name]()


def _scenario(args) -> Scenario:
    return Scenario.preset(
        args.preset, map_seed=args.map_seed,
        **({"max_turns": args.turns} if hasattr(args, "turns") else {}),
    )


def cmd_play(args) -> None:
    scenario = _scenario(args)
    game = Game(scenario, seed=args.seed)
    blue, red = _make(args.blue), _make(args.red)
    blue.reset(game, Side.BLUE)
    red.reset(game, Side.RED)
    agents = {Side.BLUE: blue, Side.RED: red}

    while not game.is_over:
        side = game.current_side
        game.step(agents[side].act(game, side))
        if args.watch:
            print("\033[2J\033[H" + render(game))
            time.sleep(args.delay)

    if not args.watch:
        print(render(game))
    winner = game.winner()
    print()
    print(f"Result: {winner.name if winner is not None else 'DRAW'}  "
          f"(BLUE {game.vp[Side.BLUE]:.0f} - {game.vp[Side.RED]:.0f} RED)")
    print(f"Survivors: BLUE {len(game.units_of(Side.BLUE))}, "
          f"RED {len(game.units_of(Side.RED))}   Engagements: {len(game.combat_log)}")


def cmd_tournament(args) -> None:
    factories = {name: (lambda c=cls: c()) for name, cls in REGISTRY.items()}
    tournament(factories, games_per_pair=args.games, preset=args.preset)


def cmd_bench(args) -> None:
    rate = benchmark(steps=args.steps, preset=args.preset)
    print(f"{rate:,.0f} side-turns/sec (single core, pure Python)")


def cmd_export(args) -> None:
    scenario = _scenario(args)
    game = Game(scenario, seed=args.seed)
    blue, red = _make(args.blue), _make(args.red)
    agents = {Side.BLUE: blue, Side.RED: red}
    for _ in range(args.steps):
        if game.is_over:
            break
        side = game.current_side
        game.step(agents[side].act(game, side))
    with open(args.out, "w") as fh:
        fh.write(to_json(game))
    print(f"wrote {args.out}")


def cmd_report(args) -> None:
    from .record.report import run
    text = run(args.preset, args.blue, args.red, args.games, outdir=args.csv)
    print(text)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        print(f"\nwritten to {args.out}")
    if args.csv:
        print(f"CSV written to {args.csv}/ (turns.csv, units.csv, combat.csv)")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fieldbox")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("play", help="run a single match")
    pl.add_argument("--blue", default="doctrine")
    pl.add_argument("--red", default="rush")
    pl.add_argument("--seed", type=int, default=0)
    pl.add_argument("--map-seed", type=int, default=1)
    pl.add_argument("--preset", default="combined_arms", choices=sorted(PRESETS))
    pl.add_argument("--turns", type=int, default=30)
    pl.add_argument("--watch", action="store_true", help="animate turn by turn")
    pl.add_argument("--delay", type=float, default=0.25)
    pl.set_defaults(func=cmd_play)

    tn = sub.add_parser("tournament", help="round robin over all agents")
    tn.add_argument("--games", type=int, default=20)
    tn.add_argument("--preset", default="combined_arms", choices=sorted(PRESETS))
    tn.set_defaults(func=cmd_tournament)

    bn = sub.add_parser("bench", help="measure environment throughput")
    bn.add_argument("--steps", type=int, default=5000)
    bn.add_argument("--preset", default="combined_arms", choices=sorted(PRESETS))
    bn.set_defaults(func=cmd_bench)

    ex = sub.add_parser("export", help="dump state as JSON for the frontend")
    ex.add_argument("--out", default="state.json")
    ex.add_argument("--blue", default="doctrine")
    ex.add_argument("--red", default="doctrine")
    ex.add_argument("--seed", type=int, default=0)
    ex.add_argument("--map-seed", type=int, default=1)
    ex.add_argument("--preset", default="combined_arms", choices=sorted(PRESETS))
    ex.add_argument("--steps", type=int, default=10)
    ex.set_defaults(func=cmd_export)

    rp = sub.add_parser("report", help="batch diagnostics with anomaly detection")
    rp.add_argument("--preset", default="combined_arms", choices=sorted(PRESETS))
    rp.add_argument("--blue", default="doctrine")
    rp.add_argument("--red", default="doctrine")
    rp.add_argument("--games", type=int, default=20)
    rp.add_argument("--out", default=None, help="also write the report to a file")
    rp.add_argument("--csv", default=None, help="directory for the CSV tables")
    rp.set_defaults(func=cmd_report)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
