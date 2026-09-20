"""Influence field tests (Phase 4): ramps, corridor distance, combination."""

import math

import pytest

from geoworld_compiler.influence import (
    BoxRamp, CorridorRamp, DiscRamp, RasterField, combine_max, combine_sum,
    corridor_field, smootherstep,
)


def test_smootherstep_shape():
    assert smootherstep(-0.5) == 0.0
    assert smootherstep(0.0) == 0.0
    assert smootherstep(0.5) == pytest.approx(0.5)
    assert smootherstep(1.0) == 1.0
    assert smootherstep(1.5) == 1.0
    # Flat slope at both ends — the property that hides the blend seam.
    assert smootherstep(0.05) < 0.05
    assert smootherstep(0.95) > 0.95


def test_box_ramp():
    f = BoxRamp(half_extent_m=4000.0, ramp_m=500.0)
    assert f.weight(0.0, 0.0) == 1.0
    assert f.weight(3400.0, 0.0) == 1.0          # inside, before the ramp
    assert f.weight(4000.0, 0.0) == 0.0          # at the boundary
    assert f.weight(5000.0, 0.0) == 0.0          # outside
    mid = f.weight(3700.0, 0.0)                  # inside the ramp
    assert 0.0 < mid < 1.0
    # Ramp is monotone: weight decreases as x approaches the edge.
    assert f.weight(3600.0, 0.0) > f.weight(3700.0, 0.0) > f.weight(3900.0, 0.0)
    # Chebyshev metric: the boundary is a square, so the nearer of the two
    # edges dominates — pushing north lowers weight at the same easting.
    assert f.weight(3700.0, 3800.0) < f.weight(3700.0, 0.0)
    assert f.weight(3600.0, 3600.0) == f.weight(3600.0, 0.0)


def test_disc_ramp():
    f = DiscRamp(0.0, 0.0, full_m=400.0, edge_m=500.0)
    assert f.weight(0.0, 0.0) == 1.0
    assert f.weight(399.0, 0.0) == 1.0
    assert f.weight(500.0, 0.0) == 0.0
    assert 0.0 < f.weight(450.0, 0.0) < 1.0
    # Off-center disc.
    g = DiscRamp(100.0, 0.0, full_m=10.0, edge_m=20.0)
    assert g.weight(100.0, 0.0) == 1.0
    assert g.weight(0.0, 0.0) == 0.0


def test_corridor_ramp():
    # A 1 km north-south corridor through the origin (geo +north).
    f = CorridorRamp([[(0.0, -500.0), (0.0, 500.0)]], full_m=30.0, ramp_m=70.0)
    assert f.weight(0.0, 0.0) == 1.0             # on the centerline
    assert f.weight(25.0, 200.0) == 1.0          # within full width
    assert f.weight(150.0, 0.0) == 0.0           # past full + ramp
    mid = f.weight(65.0, 0.0)                    # in the ramp band
    assert 0.0 < mid < 1.0
    # Along the corridor but beyond its end: distance to the endpoint
    # still grants partial/full influence inside reach.
    assert f.weight(0.0, 520.0) == 1.0           # 20 m past the endpoint
    assert f.weight(0.0, 700.0) == 0.0           # 200 m past it
    # Diagonal segment: nearest point on the line gets full weight.
    g = CorridorRamp([[(0.0, 0.0), (100.0, 100.0)]], full_m=5.0, ramp_m=10.0)
    assert g.weight(50.0, 50.0) == 1.0
    assert g.weight(50.0, 60.0) > 0.0            # ~7 m off the line
    assert g.weight(50.0, 80.0) == 0.0


def test_corridor_requires_two_points():
    with pytest.raises(ValueError):
        CorridorRamp([[(0.0, 0.0)]], full_m=10.0, ramp_m=10.0)


def test_combine_max_picks_strongest():
    a = DiscRamp(-100.0, 0.0, full_m=50.0, edge_m=150.0)
    b = BoxRamp(half_extent_m=200.0, ramp_m=50.0)
    f = combine_max(a, b)
    assert f.weight(-100.0, 0.0) == 1.0          # disc center
    assert f.weight(0.0, 0.0) == 1.0             # box interior
    assert f.weight(500.0, 0.0) == 0.0           # outside both
    # Between them: whichever claims more wins.
    assert f.weight(-60.0, 0.0) >= f.weight(100.0, 0.0)


def test_combine_sum_caps_at_one():
    a = DiscRamp(0.0, 0.0, full_m=100.0, edge_m=200.0)
    f = combine_sum(a, a)
    assert f.weight(0.0, 0.0) == 1.0             # 1 + 1 capped
    assert f.weight(150.0, 0.0) <= 1.0


def test_combined_bounds_cover_all():
    a = DiscRamp(0.0, 0.0, full_m=10.0, edge_m=50.0)
    b = CorridorRamp([[(0.0, 0.0), (0.0, 3000.0)]], full_m=20.0, ramp_m=80.0)
    f = combine_max(a, b)
    e0, n0, e1, n1 = f.bounds()
    assert n1 >= 3100.0 and e1 >= 100.0            # corridor reach dominates
    assert n0 <= -100.0 and e0 <= -100.0
    assert math.isinf(f.weight(0.0, 1e9)) is False  # always returns a float


def test_corridor_field_raster():
    # Rasterized corridor: same semantics as CorridorRamp (weight 1 within
    # full_m, smootherstep to 0 across ramp_m), but O(1) per query.
    bounds = (-500.0, -600.0, 500.0, 600.0)
    f = corridor_field([[(0.0, -500.0), (0.0, 500.0)]], bounds,
                       full_m=30.0, ramp_m=70.0, cell_m=4.0)
    assert f.weight(0.0, 0.0) > 0.95           # on the centerline
    assert f.weight(20.0, 200.0) > 0.9         # inside full width
    assert f.weight(150.0, 0.0) == 0.0         # past full + ramp
    mid = f.weight(65.0, 0.0)
    assert 0.0 < mid < 1.0                     # ramp band
    assert f.bounds() == bounds
    assert f.weight(0.0, 700.0) == 0.0         # outside grid region


def test_raster_field_outside_bounds():
    import numpy as np
    f = RasterField(np.full((4, 4), 255, dtype=np.uint8),
                    (0.0, 0.0, 32.0, 32.0), cell_m=8.0)
    assert f.weight(16.0, 16.0) == pytest.approx(1.0, abs=0.01)
    assert f.weight(-1.0, 16.0) == 0.0
    assert f.weight(16.0, 40.0) == 0.0
