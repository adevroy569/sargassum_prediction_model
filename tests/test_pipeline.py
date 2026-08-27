"""Physical sanity checks. Run with: pytest -q"""
import json

import numpy as np
import pytest

from sargassum import biomass, config, emissions
from sargassum.beaching import BeachingAccumulator, LandMask
from sargassum.coastline import build_segments
from sargassum.drift import Particles, VectorField, step

cfg = config.load()


def test_afai_coverage_scaling():
    """dAFAI of 4.41e-2 above background must mean 100% coverage."""
    a = np.zeros((80, 80))
    a[39:42, 39:42] = cfg.get_path("biomass.afai_full_coverage")
    cov = biomass.afai_to_coverage(a, cfg)
    assert cov[40, 40] == pytest.approx(1.0, abs=0.02)
    assert cov[0, 0] == 0.0


def test_isolated_hot_pixel_is_rejected():
    """A lone pixel above threshold is noise, not a Sargassum mat."""
    a = np.zeros((80, 80))
    a[40, 40] = cfg.get_path("biomass.afai_full_coverage")
    assert biomass.afai_to_coverage(a, cfg).max() == 0.0


def test_biomass_density_bound():
    cov = np.array([[0.0, 0.5, 1.0]])
    dens = biomass.coverage_to_wet_kg_m2(cov, cfg)
    assert dens.max() == pytest.approx(3.34)


def test_release_kernel_is_normalised():
    for dt in (1.0, 3.0):
        k = emissions.release_kernel(int(600 / dt), dt, cfg)
        assert k.sum() * dt == pytest.approx(1.0, rel=1e-3)
    # nothing is released in the first 24 h
    k = emissions.release_kernel(600, 1.0, cfg)
    assert k[:24].sum() == 0.0


def test_emissions_conserve_the_yield():
    """Total gas released must equal stranded mass x yield."""
    stranded = np.zeros((1, 900))
    stranded[0, 0] = 1_000_000.0            # 1000 t stranded at t=0
    res = emissions.emissions(stranded, np.array([5000.0]), 1.0, cfg)
    total_h2s = res.h2s_kg_per_h.sum()      # dt = 1 h
    expected = 1000.0 * cfg.get_path("emissions.h2s_kg_per_tonne_wet")
    assert total_h2s == pytest.approx(expected, rel=0.02)


def test_emission_flux_within_published_range():
    """A heavy but realistic stranding must not imply an absurd areal flux."""
    stranded = np.zeros((1, 900))
    stranded[0, 0] = 5000 * 1000.0          # 5000 t on a 5 km beach
    res = emissions.emissions(stranded, np.array([5000.0]), 1.0, cfg)
    peak = res.h2s_flux_mg_m2_s.max()
    assert peak <= emissions.LIT_H2S_FLUX_RANGE[1], (
        f"peak flux {peak:.3f} mg m-2 s-1 exceeds the published maximum")


def test_beaching_conserves_mass():
    segs = build_segments(5.0)
    acc = BeachingAccumulator(segs, 24)
    # The most east-facing segment on the main island. Picking it by normal
    # rather than by taking the first of the "east" bucket keeps the test
    # meaningful on a real shoreline, where that bucket also holds segments
    # tucked inside bays that no eastward drift can reach.
    target = max((s for s in segs if s.island == "puerto_rico"),
                 key=lambda s: s.normal_lon)
    # place the raft 1 km seaward of it
    p = Particles(lat=np.array([target.lat + target.normal_lat * 0.009]),
                  lon=np.array([target.lon + target.normal_lon * 0.009]),
                  mass_kg=np.array([1000.0]))
    before = p.mass_kg.sum()
    total = 0.0
    for t in range(24):
        # drive it straight back down the seaward normal, onto the beach
        u = np.array([-0.4 * target.normal_lon])
        v = np.array([-0.4 * target.normal_lat])
        total += acc.capture(p, u, v, t, 1.0, cfg)
    assert total + p.mass_kg.sum() == pytest.approx(before, rel=1e-9)
    assert total > 0


def test_coast_normals_point_seaward():
    """Every receptor's normal must lead off the island.

    This used to assert that segments north of 18.45 face north and segments
    south of 17.97 face south, which is true of a smooth traced outline and
    false of the real coastline: San Juan Bay, Bahia de Guanica and Ensenada
    Honda all contain shoreline whose genuine seaward direction is the
    opposite of the coast they sit on. Walking seaward off the land is the
    property that actually has to hold, and it holds everywhere.
    """
    from sargassum.coastline import ISLANDS, _point_in_ring

    segs = build_segments(5.0)
    for s in segs:
        ring = ISLANDS[s.island]
        for km in (0.5, 1.0, 2.0):   # well past the ~150 m offset of a receptor
            d = km / 111.0
            assert not _point_in_ring(s.lon + s.normal_lon * d,
                                      s.lat + s.normal_lat * d, ring), (
                f"{s.seg_id} ({s.name}) normal re-enters {s.island} at {km} km")

    # And the open coast should still broadly face the way it looks on a map.
    north = [s for s in segs if s.island == "puerto_rico" and s.lat > 18.48]
    south = [s for s in segs if s.island == "puerto_rico" and s.lat < 17.94]
    assert np.mean([s.normal_lat > 0 for s in north]) > 0.9
    assert np.mean([s.normal_lat < 0 for s in south]) > 0.9


def test_receptor_cache_notices_a_changed_shoreline(tmp_path):
    """The cached receptor list must be keyed on the shoreline, not on mere
    existence: it silently outlived the outline it was derived from once."""
    from sargassum.coastline import segments_are_current, write_segments

    p = tmp_path / "coast_segments.geojson"
    write_segments(p, 5.0)
    assert segments_are_current(p)

    stale = json.loads(p.read_text())
    stale["properties"]["islands_sha"] = "0" * 16
    p.write_text(json.dumps(stale))
    assert not segments_are_current(p)


def test_receptors_sit_on_the_shoreline():
    """Receptors must be on the water's edge, not a kilometre inland.

    The hand-traced outline they used to come from was accurate to 5-15 km
    between vertices, which put the drawn segments visibly off the coast.
    """
    from sargassum.coastline import ISLANDS

    segs = build_segments(5.0)
    assert len(segs) > 100, f"only {len(segs)} receptors"
    for s in segs:
        km = _km_to_ring(s.lon, s.lat, ISLANDS[s.island])
        assert km < 0.3, (
            f"{s.seg_id} ({s.name}) is {km:.2f} km from its shoreline")


def _km_to_ring(px, py, ring):
    """Distance from a point to the nearest edge of a closed ring, in km.

    Measured to the edges rather than to the vertices: a receptor lands part
    way along an edge, so vertex distance would report up to half an edge
    length even for a receptor sitting exactly on the line.
    """
    best = float("inf")
    mx = np.cos(np.radians(py))
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        ax, ay = (x2 - x1) * mx, y2 - y1
        bx, by = (px - x1) * mx, py - y1
        den = ax * ax + ay * ay
        t = 0.0 if den == 0 else max(0.0, min(1.0, (bx * ax + by * ay) / den))
        best = min(best, np.hypot(bx - t * ax, by - t * ay))
    return float(best) * 111.195


def test_land_blocks_drift():
    land = LandMask()
    assert land.inside(np.array([18.2]), np.array([-66.5]))[0]      # inland
    assert not land.inside(np.array([18.2]), np.array([-64.0]))[0]  # open sea


def test_windage_direction():
    """Easterly wind must push rafts west."""
    lat = np.linspace(17, 19, 5)
    lon = np.linspace(-68, -64, 5)
    zero = VectorField(lat, lon, np.zeros((5, 5)), np.zeros((5, 5)))
    wind = VectorField(lat, lon, np.full((5, 5), -10.0), np.zeros((5, 5)))
    p = Particles(lat=np.array([18.0]), lon=np.array([-66.0]),
                  mass_kg=np.array([100.0]))
    lon0 = p.lon[0]
    rng = np.random.default_rng(0)
    step(p, zero, wind, 6.0, cfg, rng)
    assert p.lon[0] < lon0
