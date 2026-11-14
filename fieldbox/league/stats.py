"""Small statistics helpers for the evolution league (stdlib only)."""

from __future__ import annotations

import math
from typing import Dict, List, Sequence, Tuple


def expected(r: float, r_opp: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((r_opp - r) / 400.0))


def performance_rating(results: Sequence[Tuple[float, float]]) -> float:
    """Elo rating R whose expected total score against the given opponents
    equals the actual total. `results` = [(opponent_rating, score 0..1), ...].
    The total is clipped to [0.5, n - 0.5] so a perfect or empty record stays
    finite. Bisection, so the answer is exact and deterministic."""
    n = len(results)
    if n == 0:
        return 1500.0
    total = min(max(sum(s for _, s in results), 0.5), n - 0.5)
    lo, hi = 0.0, 4000.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(expected(mid, ra) for ra, _ in results) < total:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def mean_std(xs: Sequence[float]) -> Tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    m = sum(xs) / len(xs)
    return m, math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))


def trait_stats(population: Sequence[Dict[str, float]]) -> Dict[str, Tuple[float, float]]:
    """Per-trait (mean, std) over a population of genome dicts."""
    out: Dict[str, Tuple[float, float]] = {}
    for t in sorted(population[0]):
        out[t] = mean_std([g[t] for g in population])
    return out


def winrate_matrix(results: Dict[Tuple[int, int], List[float]], n: int) -> List[List[float]]:
    """Row i vs column j mean score; 0.5 on the diagonal and where unplayed."""
    m = [[0.5] * n for _ in range(n)]
    for (i, j), v in results.items():
        if v:
            m[i][j] = sum(v) / len(v)
    return m
