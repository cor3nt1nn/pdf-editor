"""Timing helper for performance budgets that must not flake on a loaded machine.

``best_time(fn, budget)`` runs ``fn`` until one run fits the budget (at most ``runs``
times) and returns the best time and every time measured: a test asserts
``best < budget``. A real regression is slower on every run and still fails, while a
run slowed down by another process (a parallel build, an antivirus scan) is retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: Attempts before a budget is declared missed.
RUNS = 7


def best_time(
    fn: Callable[[], object],
    budget: float,
    *,
    runs: int = RUNS,
    setup: Callable[[], object] | None = None,
) -> tuple[float, list[float]]:
    """Best wall time of ``fn()`` over at most ``runs`` runs (stops at the first run
    within ``budget``); ``setup()`` runs untimed before each run (e.g. to drop a cache)."""
    times: list[float] = []
    for _ in range(runs):
        if setup is not None:
            setup()
        start = time.perf_counter()
        fn()
        times.append(time.perf_counter() - start)
        if times[-1] < budget:
            break
    return min(times), times
