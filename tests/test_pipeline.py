"""Physical sanity checks. Run with: pytest -q"""
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
    east = [s for s in segs if s.coast == "east" and s.island == "puerto_rico"][0]
    # place the raft 1 km seaward of a real east-coast segment
    p = Particles(lat=np.array([east.lat + east.normal_lat * 0.009]),
                  lon=np.array([east.lon + east.normal_lon * 0.009]),
                  mass_kg=np.array([1000.0]))
    before = p.mass_kg.sum()
    total = 0.0
    for t in range(24):
        u = np.array([-0.4])   # heading west, onto the east coast
        v = np.array([0.0])
        total += acc.capture(p, u, v, t, 1.0, cfg)
    assert total + p.mass_kg.sum() == pytest.approx(before, rel=1e-9)
    assert total > 0


def test_coast_normals_point_seaward():
    segs = build_segments(5.0)
    north = [s for s in segs if s.island == "puerto_rico" and s.lat > 18.45]
    south = [s for s in segs if s.island == "puerto_rico" and s.lat < 17.97]
    assert all(s.normal_lat > 0 for s in north)
    assert all(s.normal_lat < 0 for s in south)


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
