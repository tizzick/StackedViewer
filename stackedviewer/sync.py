"""Timing maths for offset tiles. Pure functions, no Qt or mpv imports."""

from __future__ import annotations


def tile_position(index: int, offset_s: float, duration_s: float, master_s: float = 0.0) -> float:
    """Where tile `index` should be when the first tile is at `master_s`.

    Each tile runs `offset_s` seconds ahead of the tile before it, wrapping
    around the end of the video.
    """
    if duration_s <= 0:
        return 0.0
    return (master_s + index * offset_s) % duration_s


def drift(actual_s: float, expected_s: float, duration_s: float) -> float:
    """Signed distance from expected to actual, taking the short way round
    the loop point. Positive means the tile is ahead."""
    if duration_s <= 0:
        return 0.0
    half = duration_s / 2.0
    return (actual_s - expected_s + half) % duration_s - half


def format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
