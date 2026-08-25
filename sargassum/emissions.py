"""Hydrogen sulfide and ammonia release from stranded Sargassum.

Why a yield model rather than a flux model
------------------------------------------
Published areal fluxes for stranded Sargassum span five orders of magnitude
(from 5e-6 mg m-2 s-1 for dry wrack in Florida to 0.45-3.58 mg m-2 s-1
inverse-modelled for Martinique). Applying the high end to a continuous wrack
band implies more sulfur than any plausible source can supply, so this model
is built the other way round: **conserve mass first, then check the implied
flux against the published range.**

Budget
------
H2S in a beached wrack pile comes mostly from bacterial sulfate reduction,
which oxidises algal organic carbon using sulfate from trapped seawater:

    2 CH2O + SO4(2-) -> H2S + 2 HCO3(-)

so the ceiling is set by mineralisable carbon, not by the algae's own sulfur.
Per tonne of wet Sargassum: ~100 kg dry, ~30 kg organic C (~2500 mol). If a
fifth is mineralised anaerobically and 5-20% of the resulting sulfide escapes
to air rather than being re-oxidised or fixed as iron sulfide, the yield lands
near 0.1-3 kg H2S per tonne wet. Default: 0.8.

NH3 follows the nitrogen budget: ~1.2% N of dry weight (Lapointe et al. 2021)
gives ~1.2 kg N per tonne wet, of which 10-30% typically volatilises.
Default: 0.25 kg NH3 per tonne wet.

Timing
------
Emission is negligible for ~48 h after stranding, peaks around day 3, then
decays with an e-folding time of ~5 days. The kernel is normalised to unit
integral, so the total released over the full decomposition equals
`stranded mass x yield` exactly.

Diagnostics
-----------
Areal flux and an indicative wrack-line concentration are *derived* from the
conserved emission rate and the footprint of the deposit, then compared with
the published Martinique flux range so an out-of-range result is visible
rather than silent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

# molar-volume conversions at 25 C, 1 atm
MG_M3_PER_PPM_H2S = 1.393
MG_M3_PER_PPM_NH3 = 0.696

# reference concentrations, ppm
H2S_ODOR_DETECT_PPM = 0.0005       # ATSDR: detectable at 0.5 ppb
H2S_ODOR_STRONG_PPM = 0.03         # clearly objectionable to most people
H2S_WHO_24H_PPM = 0.107            # WHO guideline 150 ug/m3, 24 h average
H2S_ATSDR_ACUTE_MRL_PPM = 0.07     # ATSDR acute inhalation MRL (1-14 d)
H2S_OSHA_CEILING_PPM = 20.0        # OSHA ceiling (occupational)
H2S_NIOSH_IDLH_PPM = 100.0         # immediately dangerous to life or health
NH3_ODOR_THRESHOLD_PPM = 5.0
NH3_ATSDR_ACUTE_MRL_PPM = 1.7

# published areal flux range for stranded Sargassum, mg m-2 s-1 (Martinique
# inverse model) - used only as a plausibility check on our derived flux
LIT_H2S_FLUX_RANGE = (0.45, 3.58)
LIT_NH3_FLUX_RANGE = (0.25, 2.03)


def release_kernel(n_steps: int, dt_h: float, cfg) -> np.ndarray:
    """Normalised release-rate curve k(tau); sum(k) * dt = 1."""
    lag = float(cfg.get_path("emissions.onset_lag_hours", 48))
    peak = float(cfg.get_path("emissions.peak_hours", 72))
    efold = float(cfg.get_path("emissions.decay_efold_hours", 120))
    tau = np.arange(n_steps) * dt_h
    k = np.zeros_like(tau, dtype=float)
    ramp = (tau >= lag) & (tau < peak)
    k[ramp] = (tau[ramp] - lag) / max(peak - lag, 1e-6)
    tail = tau >= peak
    k[tail] = np.exp(-(tau[tail] - peak) / max(efold, 1e-6))
    total = k.sum() * dt_h
    return k / total if total > 0 else k


def _convolve(stranded: np.ndarray, kern: np.ndarray, dt_h: float) -> np.ndarray:
    """rate[:, t] = sum_tau stranded[:, t-tau] * kern[tau]  (units: mass/h)."""
    n_seg, n_steps = stranded.shape
    out = np.zeros_like(stranded, dtype=float)
    nz = np.flatnonzero(kern > 0)
    for k in nz:
        out[:, k:] += stranded[:, : n_steps - k] * kern[k]
    return out


def active_mass(stranded_kg: np.ndarray, dt_h: float, cfg) -> np.ndarray:
    """Wet mass still present and decomposing, for footprint/density."""
    n_steps = stranded_kg.shape[1]
    kern = release_kernel(n_steps, dt_h, cfg)
    remaining = 1.0 - np.cumsum(kern) * dt_h          # fraction not yet released
    remaining = np.clip(remaining, 0.0, 1.0)
    return _convolve(stranded_kg, remaining, dt_h)


@dataclass
class EmissionResult:
    h2s_kg_per_h: np.ndarray      # [segment, step]
    nh3_kg_per_h: np.ndarray
    density_index: np.ndarray     # [segment, step], 0-8 (Martinique scale)
    footprint_m2: np.ndarray
    h2s_flux_mg_m2_s: np.ndarray
    h2s_ppm: np.ndarray           # indicative wrack-line concentration
    nh3_ppm: np.ndarray
    flux_above_literature: float  # fraction of emitting cells above the range


def emissions(stranded_kg: np.ndarray, length_m: np.ndarray, dt_h: float,
              cfg, onshore_wind_ms: np.ndarray | float = 4.0,
              mixing_height_m: float | None = None) -> EmissionResult:
    width = float(cfg.get_path("emissions.deposit_width_m", 8.0))
    sig8 = float(cfg.get_path("emissions.density_index_8_kg_m2", 40.0))
    sig_thin = float(cfg.get_path("emissions.thin_mat_kg_m2", 5.0))
    y_h2s = float(cfg.get_path("emissions.h2s_kg_per_tonne_wet", 0.8)) / 1000.0
    y_nh3 = float(cfg.get_path("emissions.nh3_kg_per_tonne_wet", 0.25)) / 1000.0
    if mixing_height_m is None:
        mixing_height_m = float(cfg.get_path("emissions.mixing_height_m", 2.5))

    n_steps = stranded_kg.shape[1]
    kern = release_kernel(n_steps, dt_h, cfg)          # 1/h

    h2s_kg_h = _convolve(stranded_kg, kern, dt_h) * y_h2s
    nh3_kg_h = _convolve(stranded_kg, kern, dt_h) * y_nh3

    # --- geometry of the deposit, for flux and concentration diagnostics ---
    A = active_mass(stranded_kg, dt_h, cfg)
    band_area = (width * length_m)[:, None]
    footprint = np.clip(A / max(sig_thin, 1e-6), 0.0, band_area)
    sigma = np.where(footprint > 0, A / np.maximum(footprint, 1e-9), 0.0)
    frac = np.clip((sigma - sig_thin) / max(sig8 - sig_thin, 1e-6), 0.0, 1.0)
    d_index = np.where(footprint > 0, 1.0 + 7.0 * frac, 0.0)

    # kg/h over m2 -> mg m-2 s-1. A footprint smaller than 1 m2 is the
    # numerical tail of the decomposition curve, not a real deposit; reporting
    # a flux there would divide a vanishing emission by a vanishing area.
    real = footprint >= 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        flux_h2s = np.where(real, h2s_kg_h * 1e6 / 3600.0
                            / np.maximum(footprint, 1e-9), 0.0)
        flux_nh3 = np.where(real, nh3_kg_h * 1e6 / 3600.0
                            / np.maximum(footprint, 1e-9), 0.0)

    emitting = flux_h2s > 0
    above = float((flux_h2s[emitting] > LIT_H2S_FLUX_RANGE[1]).mean()) \
        if emitting.any() else 0.0

    u = np.maximum(np.asarray(onshore_wind_ms, dtype=float), 0.5)
    box = width / (u * max(mixing_height_m, 0.5))      # mg m-3 per (mg m-2 s-1)
    if box.ndim == 1:
        box = box[:, None]
    h2s_ppm = flux_h2s * box / MG_M3_PER_PPM_H2S
    nh3_ppm = flux_nh3 * box / MG_M3_PER_PPM_NH3

    return EmissionResult(h2s_kg_h, nh3_kg_h, d_index, footprint,
                          flux_h2s, h2s_ppm, nh3_ppm, above)


def risk_tier(h2s_kg_per_day_per_km: float, cfg) -> str:
    t = cfg.get_path("emissions.risk_tiers", {})
    if h2s_kg_per_day_per_km < float(t.get("low", 0.5)):
        return "minimal"
    if h2s_kg_per_day_per_km < float(t.get("moderate", 3.0)):
        return "low"
    if h2s_kg_per_day_per_km < float(t.get("high", 12.0)):
        return "moderate"
    return "high"


def exposure_context(h2s_ppm: float) -> Dict[str, object]:
    """Plain-language framing for an indicative wrack-line concentration."""
    if h2s_ppm < H2S_ODOR_DETECT_PPM:
        label = "not detectable"
    elif h2s_ppm < H2S_ODOR_STRONG_PPM:
        label = "faint odour"
    elif h2s_ppm < H2S_ATSDR_ACUTE_MRL_PPM:
        label = "clear rotten-egg odour"
    elif h2s_ppm < 1.0:
        label = "strong odour, above health guideline levels"
    elif h2s_ppm < H2S_OSHA_CEILING_PPM:
        label = "irritation likely with prolonged exposure"
    else:
        label = "avoid, occupational ceiling exceeded"
    return {
        "ppm": round(float(h2s_ppm), 5),
        "label": label,
        "odour_detect_ppm": H2S_ODOR_DETECT_PPM,
        "who_24h_ppm": H2S_WHO_24H_PPM,
        "atsdr_acute_mrl_ppm": H2S_ATSDR_ACUTE_MRL_PPM,
        "osha_ceiling_ppm": H2S_OSHA_CEILING_PPM,
    }
