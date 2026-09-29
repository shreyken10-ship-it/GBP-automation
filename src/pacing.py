"""Posting schedule computation (PRD §6.3 / FR-11).

Base interval = window / n posts (~24-32 min for 15-20 posts over 8h),
with ±30-40% randomized jitter per slot so the cadence never looks fixed.
Pure functions — unit-testable without Playwright or wall-clock sleeps.
"""
import random

from . import config


def jittered_interval(base_seconds: float, rng: random.Random | None = None) -> float:
    """One interval: base ± (30-40%) of base, sign chosen at random."""
    rng = rng or random
    magnitude = rng.uniform(config.JITTER_MIN_PCT, config.JITTER_MAX_PCT)
    sign = rng.choice((-1, 1))
    return base_seconds * (1 + sign * magnitude)


def compute_schedule(
    n: int,
    window_hours: float | None = None,
    rng: random.Random | None = None,
) -> list[float]:
    """Return n offsets (seconds from start) at which to publish each post.

    First post goes out after a short randomized warm-up rather than at t=0,
    subsequent posts follow jittered base intervals. The jitter math keeps the
    total within roughly the operating window without ever producing a
    negative gap.
    """
    if n <= 0:
        return []
    rng = rng or random
    window_seconds = (window_hours or config.POSTING_WINDOW_HOURS) * 3600
    base = window_seconds / n

    offsets = []
    t = rng.uniform(60, 300)  # warm-up: 1-5 minutes before first post
    for _ in range(n):
        offsets.append(t)
        t += max(60.0, jittered_interval(base, rng))
    return offsets


def action_pause(rng: random.Random | None = None) -> float:
    """Small human-ish pause between in-page actions (FR-12), in seconds."""
    rng = rng or random
    return rng.uniform(0.8, 3.5)


def typing_delay_ms(rng: random.Random | None = None) -> int:
    """Per-keystroke delay for Playwright type(), in milliseconds."""
    rng = rng or random
    return rng.randint(20, 90)


def post_gap_seconds(rng: random.Random | None = None) -> float:
    """Variable gap enforced between consecutive live GBP posts within a
    manual/batch run — 8-10 minutes, randomized so it never looks fixed."""
    rng = rng or random
    return rng.uniform(480, 600)
