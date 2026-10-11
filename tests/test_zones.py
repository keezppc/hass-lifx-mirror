"""Tests for per-zone helpers."""

from __future__ import annotations

import pytest

pytest.importorskip("lifx")

from lifx import HSBK

from custom_components.lifx_mirror.util import gradient_zones, segment_zones

# A short ring: rows 0, 0, 6, 12
POSITIONS = (0, 1, 24, 48)


def test_gradient_runs_from_first_to_last_row() -> None:
    """The ends take the end stops and the middle blends them."""
    red = HSBK(hue=0, saturation=1, brightness=0.2, kelvin=3500)
    blue = HSBK(hue=240, saturation=1, brightness=0.6, kelvin=3500)

    zones = gradient_zones(POSITIONS, [red, blue])

    assert zones[0] == red
    assert zones[1] == red
    assert zones[3] == blue
    assert zones[2].brightness == pytest.approx(0.4)


def test_gradient_blends_hue_the_short_way_round() -> None:
    """350 degrees to 10 degrees passes through 0, not through 180."""
    a = HSBK(hue=350, saturation=1, brightness=1, kelvin=3500)
    b = HSBK(hue=10, saturation=1, brightness=1, kelvin=3500)

    middle = gradient_zones(POSITIONS, [a, b])[2]

    assert middle.hue == pytest.approx(0, abs=1e-6) or middle.hue == pytest.approx(360)


def test_three_stops_hit_the_middle_stop_halfway() -> None:
    """Stops are spaced evenly."""
    stops = [
        HSBK(hue=h, saturation=1, brightness=1, kelvin=3500) for h in (0, 120, 240)
    ]

    zones = gradient_zones(POSITIONS, stops)

    assert zones[2].hue == pytest.approx(120)


def test_a_single_stop_is_one_colour() -> None:
    """One stop paints every zone the same."""
    stop = HSBK(hue=10, saturation=1, brightness=1, kelvin=3500)

    assert gradient_zones(POSITIONS, [stop]) == [stop] * 4


def test_segments_cover_their_share_of_the_length() -> None:
    """A third of the length is a third of the rows, and the rest stays dark."""
    positions = tuple(range(0, 52, 4))  # one zone per row, 13 rows
    red = HSBK(hue=0, saturation=1, brightness=0.2, kelvin=3500)

    zones = segment_zones(positions, [(0, 100 / 3, red)])

    assert [z.brightness for z in zones] == [0.2] * 4 + [0.0] * 9


def test_a_segment_ending_at_100_reaches_the_last_row() -> None:
    """The far end is included."""
    positions = tuple(range(0, 52, 4))
    blue = HSBK(hue=240, saturation=1, brightness=0.5, kelvin=3500)

    zones = segment_zones(positions, [(50, 100, blue)])

    assert zones[-1] == blue
    assert zones[0].brightness == 0
