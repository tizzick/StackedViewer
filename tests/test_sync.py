import pytest

from stackedviewer.sync import drift, format_time, tile_position, tile_starts


def test_first_tile_starts_at_zero():
    assert tile_position(0, 300, 3600) == 0


def test_each_tile_is_one_offset_ahead():
    assert tile_position(1, 300, 3600) == 300
    assert tile_position(3, 300, 3600) == 900


def test_offset_wraps_past_end():
    # 4 tiles x 20 min on a 50 min video: tile 3 is at 60 min -> wraps to 10 min
    assert tile_position(3, 1200, 3000) == 600


def test_master_time_moves_all_tiles():
    assert tile_position(2, 300, 3600, master_s=100) == 700
    assert tile_position(2, 300, 3600, master_s=3500) == pytest.approx(500)


def test_zero_duration_is_safe():
    assert tile_position(5, 300, 0) == 0
    assert drift(1, 2, 0) == 0


def test_drift_sign():
    assert drift(10.2, 10.0, 100) == pytest.approx(0.2)
    assert drift(9.8, 10.0, 100) == pytest.approx(-0.2)


def test_drift_across_loop_point():
    assert drift(0.1, 99.9, 100) == pytest.approx(0.2)
    assert drift(99.9, 0.1, 100) == pytest.approx(-0.2)


def test_format_time():
    assert format_time(0) == "00:00:00"
    assert format_time(3725.9) == "01:02:05"


def test_tile_starts_flags_wrapped_tiles():
    # 2x2 with 20 min offset on a 50 min video
    assert tile_starts(4, 1200, 3000) == [(0, False), (1200, False), (2400, False), (600, True)]


def test_tile_starts_exact_end_counts_as_wrapped():
    assert tile_starts(2, 3000, 3000) == [(0, False), (0, True)]


def test_tile_starts_unknown_duration():
    assert tile_starts(2, 300, 0) == [(0.0, False), (0.0, False)]
