"""Command line interface.

    python -m fieldbox play --blue doctrine --red rush --preset tiny --watch
    python -m fieldbox tournament --games 10 --preset tiny
    python -m fieldbox bench
    python -m fieldbox report --games 20
    python -m fieldbox worldgen --seed 3
    python -m fieldbox gui --screen mapview
"""

from __future__ import annotations

import argparse
import sys
import time

from .agents import REGISTRY
from .league.arena import benchmark, run_game, tournament
from .params import PRESETS
from .record.render import render, to_json
from .sim.units import Side


def _make(name: str):
    if name not in REGISTRY:
        raise SystemExit(f"unknown agent '{name}'. available: {', '.join(REGISTRY)}")
    return REGISTRY[name]()


def _world(args):
    from .world import generate_world
    return generate_world(args.map_seed, args.preset)


def cmd_play(args) -> None:
    from .record.replay import LiveMatch
    world = _world(args)
    match = LiveMatch(_make(args.blue), _make(args.red), world, seed=args.seed,
                      max_turns=args.turns, blue_name=args.blue, red_name=args.red)
    while not match.done:
        match.step()
        if args.watch:
            print("\033[2J\033[H" + render(match.game))
            time.sleep(args.delay)
    game = match.game
    if not args.watch:
        print(render(game))
    winner = game.winner()
    print()
    print(f"Result: {winner.name if winner is not None else 'DRAW'} by {game.result[1]}  "
          f"(BLUE {game.vp[Side.BLUE]:.0f} - {game.vp[Side.RED]:.0f} RED VP, {game.turn} turns)")
    print(f"Survivors: BLUE {len(game.units_of(Side.BLUE))}, "
          f"RED {len(game.units_of(Side.RED))}   Engagements: {len(game.combat_log)}")
    if args.save:
        print(f"replay written to {match.replay.save(args.save)}")


def cmd_tournament(args) -> None:
    factories = {name: (lambda c=cls: c()) for name, cls in REGISTRY.items()}
    tournament(factories, games_per_pair=args.games, preset=args.preset)


def cmd_bench(args) -> None:
    rate = benchmark(steps=args.steps, preset=args.preset)
    print(f"{rate:,.0f} side-turns/sec (single core, pure Python, preset {args.preset})")


def cmd_export(args) -> None:
    game = run_game(_make(args.blue), _make(args.red), _world(args), seed=args.seed,
                    max_turns=args.turns)
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


def cmd_worldgen(args) -> None:
    from .world import generate_world
    w = generate_world(args.seed, args.preset)
    s = w.summary()
    print(f"world seed={s['seed']} preset={s['preset']} size={s['size']}")
    print(f"  land: {s['land_pct']:.1f}%")
    print("  terrain: " + "  ".join(f"{k.lower()} {v}" for k, v in s["terrain"].items()))
    print(f"  provinces: {s['provinces']}   regions: {s['regions']}   cities: {s['cities']}")
    print(f"  river hexes: {s['river_hexes']}   road hexes: {s['road_hexes']}")
    print(f"  retries: {s['retries']}")


def cmd_gui(args) -> None:
    from .ui.app import run_gui
    run_gui(args.screen, seed=args.seed, preset=args.preset, blue=args.blue, red=args.red,
            game_seed=args.game_seed, turns=args.turns, replay=args.replay,
            root=args.root, run=args.run)


def cmd_evolve(args) -> None:
    from .league import evolve as E
    ov = {k: v for k, v in (("pop_size", args.pop), ("generations", args.generations)) if v}
    if args.bench:
        E.bench(args.preset, workers=args.workers, overrides=ov, calibrate=not args.quick)
        return
    if not args.run:
        raise SystemExit("--run NAME is required")
    d = E.evolve(args.preset, args.run, args.seed, root=args.root, resume=args.resume,
                 workers=args.workers, overrides=ov)
    print(f"done: {d}")


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="fieldbox")
    sub = p.add_subparsers(dest="cmd", required=True)
    presets = sorted(PRESETS)

    pl = sub.add_parser("play", help="run a single match")
    pl.add_argument("--blue", default="doctrine")
    pl.add_argument("--red", default="rush")
    pl.add_argument("--seed", type=int, default=0)
    pl.add_argument("--map-seed", type=int, default=1)
    pl.add_argument("--preset", default="tiny", choices=presets)
    pl.add_argument("--turns", type=int, default=None, help="override the preset's turn limit")
    pl.add_argument("--watch", action="store_true", help="animate turn by turn")
    pl.add_argument("--delay", type=float, default=0.25)
    pl.add_argument("--save", default=None, metavar="FILE.fbr",
                    help="write a replay (gzip JSON) to open with `gui --screen match --replay`")
    pl.set_defaults(func=cmd_play)

    tn = sub.add_parser("tournament", help="round robin over all agents")
    tn.add_argument("--games", type=int, default=20)
    tn.add_argument("--preset", default="tiny", choices=presets)
    tn.set_defaults(func=cmd_tournament)

    bn = sub.add_parser("bench", help="measure environment throughput")
    bn.add_argument("--steps", type=int, default=5000)
    bn.add_argument("--preset", default="tiny", choices=presets)
    bn.set_defaults(func=cmd_bench)

    ex = sub.add_parser("export", help="dump the final state as JSON")
    ex.add_argument("--out", default="state.json")
    ex.add_argument("--blue", default="doctrine")
    ex.add_argument("--red", default="doctrine")
    ex.add_argument("--seed", type=int, default=0)
    ex.add_argument("--map-seed", type=int, default=1)
    ex.add_argument("--preset", default="tiny", choices=presets)
    ex.add_argument("--turns", type=int, default=10)
    ex.set_defaults(func=cmd_export)

    rp = sub.add_parser("report", help="batch diagnostics with anomaly detection")
    rp.add_argument("--preset", default="tiny", choices=presets)
    rp.add_argument("--blue", default="doctrine")
    rp.add_argument("--red", default="doctrine")
    rp.add_argument("--games", type=int, default=20)
    rp.add_argument("--out", default=None, help="also write the report to a file")
    rp.add_argument("--csv", default=None, help="directory for the CSV tables")
    rp.set_defaults(func=cmd_report)

    from .params import EVO_PRESETS
    ev = sub.add_parser("evolve", help="evolve doctrine genomes through league play")
    ev.add_argument("--preset", default="evo-tiny", choices=sorted(EVO_PRESETS))
    ev.add_argument("--run", default=None, help="run name (directory under --root)")
    ev.add_argument("--seed", type=int, default=1, help="run seed")
    ev.add_argument("--resume", action="store_true", help="continue from the last finished generation")
    ev.add_argument("--workers", type=int, default=None, help="override the preset's worker count")
    ev.add_argument("--root", default="runs/evo")
    ev.add_argument("--pop", type=int, default=None, help="override population size")
    ev.add_argument("--generations", type=int, default=None, help="override generation count")
    ev.add_argument("--quick", action="store_true", help="with --bench: skip the anchor calibration")
    ev.add_argument("--bench", action="store_true",
                    help="time games, print the ETA and calibrate anchor Elo (runs no evolution)")
    ev.set_defaults(func=cmd_evolve)

    from .params import PRESETS as WORLD_PRESETS
    wg = sub.add_parser("worldgen", help="generate a world and print its stats")
    wg.add_argument("--seed", type=int, default=1)
    wg.add_argument("--preset", default="standard", choices=sorted(WORLD_PRESETS))
    wg.set_defaults(func=cmd_worldgen)

    gu = sub.add_parser("gui", help="open a desktop window (needs pygame-ce)")
    gu.add_argument("--screen", default="mapview", choices=["mapview", "symbols", "match", "evolab"])
    gu.add_argument("--seed", type=int, default=1, help="map seed")
    gu.add_argument("--preset", default="standard", choices=sorted(WORLD_PRESETS))
    gu.add_argument("--blue", default="doctrine", help="match: BLUE agent")
    gu.add_argument("--red", default="rush", help="match: RED agent")
    gu.add_argument("--game-seed", type=int, default=0, help="match: game seed")
    gu.add_argument("--turns", type=int, default=None, help="match: turn limit override")
    gu.add_argument("--replay", default=None, metavar="FILE.fbr", help="match: open a saved replay")
    gu.add_argument("--root", default="runs/evo", help="evolab: runs directory")
    gu.add_argument("--run", default=None, help="evolab: run to select")
    gu.set_defaults(func=cmd_gui)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
