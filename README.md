# Sargassum forecast — Puerto Rico

A self-updating Sargassum system for Puerto Rico, Vieques and Culebra that goes
past "here is where the seaweed is right now": it advects the observed offshore
biomass forward with real currents and winds, estimates **how much wet mass
lands on each stretch of coast**, and estimates **how much hydrogen sulfide and
ammonia that mass releases as it rots**.

Everything runs as a scheduled GitHub Action. Nothing needs a server, an API
key, or a paid data subscription.

```
 satellite biomass  ─┐
 HF-radar currents  ─┼─►  Lagrangian drift  ─►  shoreline capture  ─►  gas release
 WRF forecast wind  ─┘                                │
                                                      ▼
 CariCOOS trap data ────────────────────────►  learned calibration
```

---

## What it produces

Written to `site/` on every run, ready for GitHub Pages:

| File | Contents |
|---|---|
| `site/data/latest.json` | Run metadata, island totals, worst-affected segments, model info |
| `site/data/forecast_segments.geojson` | Per coast segment: stranded kg/m, tonnes/day, H₂S and NH₃ kg/day, risk tier, indicative ppm |
| `site/data/biomass_field.json` | Sparse offshore biomass field for the web map |
| `site/data/drift_tracks.json` | Sample raft trajectories, for animating the drift |
| `site/data/stations.json` | Latest in-situ trap measurements |
| `site/maps/*.png` | Static maps: offshore biomass, beaching forecast, H₂S forecast |

The website itself is three files in `site/`: `index.html`, `styles.css` and
`app.js`. It has no build step and no dependency beyond MapLibre GL, loaded from
a CDN. It reads the JSON above at page load, so a new pipeline run updates the
site without touching any markup.

The archive that feeds the model keeps growing in `data/archive/`.

---

## Data sources

All are public and machine-readable. No credentials.

| What | Source | Access |
|---|---|---|
| Offshore Sargassum (AFAI) | USF / NOAA AOML `..._AFAI_7D`, `_3D`, `_1D` | [ERDDAP griddap](https://cwcgom.aoml.noaa.gov/erddap/griddap/noaa_aoml_atlantic_oceanwatch_AFAI_7D.html), 0.015°, 2016→now |
| Surface currents | CariCOOS HF radar, 2 km (6 km fallback) | [ERDDAP griddap](https://dm3.caricoos.org/erddap/griddap/noaa_ndbc_6484_c6c4_c96e.html), hourly |
| 10 m wind forecast | CariCOOS WRF-NMM 2 km | [ERDDAP griddap](https://dm3.caricoos.org/erddap/griddap/wrf_nmm_2km_best_agg.html), hourly, forecast |
| Nearshore waves | CariCOOS SWAN 120 m, Puerto Rico | [ERDDAP griddap](https://dm3.caricoos.org/erddap/griddap/SWAN_HighRes_PR.html) |
| **Ground truth** | 14 Sargassum biomass traps, La Parguera, Lajas PR | [ERDDAP tabledap](https://dm3.caricoos.org/erddap/tabledap/Sargassum_Biomass_E1.html), weekly kg m⁻¹, 2020→now |

**A note on AFAI freshness.** The three composites do not update in step. As of
August 2026 the 7-day stream is roughly a month behind while the 1-day and
3-day streams are current to yesterday, so every run asks all three which is
freshest and falls back automatically (`sources.afai.prefer_7d_within_days`).
Keep `backfill_product` matched to whatever live runs actually use — training on
one composite and predicting from another quietly biases the model.

Those 14 trap stations are what make this more than a simulation — they are a
six-year weekly record of *actual* shoreline influx in kg per metre, and they
are what the model is trained and bias-corrected against.

---

## The model

### 1. Satellite biomass

AFAI is converted to Sargassum the way the USF/NOAA product does it:

```
ΔAFAI  = AFAI − local background (25th-percentile moving window)
coverage = (ΔAFAI − threshold) / 4.41e-2      clipped to [0, 1]
wet mass = coverage × 3.34 kg m⁻²
```

`4.41e-2` is the ΔAFAI of a fully covered pixel and `3.34 kg m⁻²` the mean wet
biomass at 100% coverage (Wang & Hu). The detection threshold is not a fixed
number: Sargassum only ever *raises* AFAI above the water background, so the
negative half of the ΔAFAI distribution is pure noise, and the threshold is set
at 3× a median-absolute noise estimate from that half. The scene calibrates its
own sensitivity.

### 2. Drift

Rafts move with

```
u_raft = u_current + 1.2% × u_wind10 + random walk (K_h = 30 m² s⁻¹)
```

integrated with a 2nd-order (Heun) step, 1 h, out to 5 days. HF radar is an
*observation*, not a forecast, so the current field relaxes from the latest
observed field toward its 72-hour mean with a 24 h e-folding time as lead time
grows. Wind genuinely is a forecast, so the windage term stays predictive
throughout. Land is a barrier: a raft that would move onto an island is held
where it was, and whether it strands is decided by the beaching rule, not by
the coarse coastline.

### 3. Beaching

The coast is resampled into 103 segments of ~5 km, each with a seaward normal.
Per time step, a raft inside a segment's capture zone loses

```
fraction = efficiency × min(1, onshore_speed × Δt / capture_distance)
```

of its mass to that segment. Mass-conserving by construction. The result is
**kg per metre of shoreline**, the same unit the CariCOOS traps report, so
prediction and observation are directly comparable.

### 4. Gas release — why a yield model, not a flux model

Published areal H₂S fluxes for stranded Sargassum span five orders of
magnitude, from 5×10⁻⁶ mg m⁻² s⁻¹ for dry Florida wrack to 0.45–3.58
mg m⁻² s⁻¹ inverse-modelled for Martinique. Applying the top of that range to a
continuous wrack band gives ~12 tonnes of H₂S per day from a single 5 km beach,
which no sulfur source can supply. So this model conserves mass first and
checks the implied flux afterwards.

The budget is set by anaerobic carbon mineralisation, not by the algae's own
sulfur — sulfate-reducing bacteria oxidise organic carbon using sulfate from
trapped seawater:

```
2 CH₂O + SO₄²⁻ → H₂S + 2 HCO₃⁻
```

Per tonne wet: ~100 kg dry, ~30 kg organic C (~2500 mol). Mineralise a fifth
anaerobically and let 5–20% of the sulfide escape rather than being re-oxidised
or fixed as iron sulfide, and the yield lands near **0.1–3 kg H₂S per tonne
wet**; the default is 0.8. Ammonia follows the nitrogen budget (~1.2% N of dry
weight, 10–30% volatilised) at **0.25 kg NH₃ per tonne wet**.

Release timing: nothing for ~48 h, ramp to a peak near 72 h, exponential decay
with a 5-day e-folding time. The kernel is normalised, so total gas released
equals `stranded mass × yield` exactly — there is a unit test for it.

Areal flux and an indicative wrack-line concentration (a box model,
`C = F·X/(U·H)`, breathing height rather than boundary-layer height) are then
*derived* from the conserved rate, and the run flags itself if the derived flux
leaves the published range. In practice a heavy stranding lands around
0.2 mg m⁻² s⁻¹ — the same order as the low end of the Martinique estimate,
which is a useful independent check that the two approaches agree.

Concentrations are reported against the ATSDR detection threshold (0.5 ppb),
the WHO 24 h guideline (0.107 ppm), the ATSDR acute MRL (0.07 ppm) and the OSHA
ceiling (20 ppm).

### 5. Learned calibration

The physical chain has one badly constrained knob (how efficiently rafts strand)
and the satellite has none of the nearshore detail. So the trap record is used
twice:

* **`BeachingModel`** — a gradient-boosted regressor predicting log influx at a
  station from nested upstream biomass catchments (100 / 250 / 600 km, upstream
  half only beyond 100 km) at 0–3 week lags, plus trend terms, seasonality and
  site. Trained on the backfilled 2020→now record.
* **`physical_scale`** — one multiplicative bias correction between the physical
  forecast and the same observations, applied to every segment that has no
  instrument.

Segments carry a `source` field of `learned` or `physical`, so the output never
hides which path produced a number.

---

## Running it

```bash
pip install -r requirements.txt

python scripts/update.py              # one live forecast cycle
python scripts/update.py --offline    # synthetic data, no network (CI smoke test)

python scripts/backfill.py --start 2020-10-01   # build the training set (slow)
python scripts/backfill.py --limit 20           # quick trial
python scripts/train_model.py                   # fit / refit the model

pytest -q                             # physical sanity checks
```

`scripts/backfill.py` is resumable — every downloaded scene is cached, so an
interrupted run picks up where it left off.

### Automation

| Workflow | Schedule | Does |
|---|---|---|
| `.github/workflows/update.yml` | every 3 h | forecast, render, commit `site/` and the archive |
| `.github/workflows/backfill.yml` | manual | download AFAI history, build training table, retrain |
| `.github/workflows/retrain.yml` | Mondays | extend the training table, refit |
| `.github/workflows/test.yml` | on push | offline pipeline run + unit tests |

Three hours is the sweet spot: HF radar and WRF update hourly, but the AFAI
composite is daily, so hourly commits would mostly be noise.

The workflow files ship in `github-workflows/` and need to be moved to
`.github/workflows/` once — see the note in that folder. Then set Settings →
Actions → General → Workflow permissions to **Read and write**, so the
scheduled runs can commit their output.

To publish: Settings → Pages → deploy from branch, folder `/site`.

---

## The website

One map with three switchable layers: observed offshore biomass, predicted
stranding, and predicted H₂S. Below it, three charts: island-wide stranding per
day, H₂S and NH₃ release per day, and the ten worst-affected places. Clicking a
coastal point opens its daily breakdown, its estimated concentration at the
wrack line, and whether the number came from the learned model or the physical
one.

Colour is deliberate. The biomass layer uses **cividis** and the stranding layer
uses **inferno**, both perceptually uniform and colourblind-safe, both clipped
away from their darkest end so low values stay visible against the dark
basemap. Risk tiers always carry a written label, so colour never carries
meaning on its own.

The page sets no cookies, loads no analytics and stores nothing. The only third
party requests are the CARTO basemap tiles and the MapLibre script.

Develop locally with a real HTTP server, since browsers block `fetch` on
`file://` pages:

```bash
python -m http.server 8000     # from the repo root
# then open http://localhost:8000/site/
```

Before the first pipeline run there is no `site/data/`, so the page falls back
to the sample outputs in `examples/data/` when it is served from the repo root.

## Configuration

Every physical coefficient lives in `config/config.yaml` — nothing is buried in
code. The ones worth tuning first:

| Key | Default | Meaning |
|---|---|---|
| `drift.windage` | 0.012 | fraction of wind speed added to the current |
| `beaching.onshore_efficiency` | 0.35 | how readily rafts strand |
| `beaching.capture_km` | 2.0 | width of the nearshore capture zone |
| `emissions.h2s_kg_per_tonne_wet` | 0.8 | H₂S yield (plausible 0.1–3) |
| `emissions.nh3_kg_per_tonne_wet` | 0.25 | NH₃ yield (plausible 0.15–0.45) |
| `emissions.deposit_width_m` | 8.0 | cross-shore width of the wrack band |
| `run.horizon_hours` | 120 | forecast length |

---

## Honest limitations

* **The absolute mass scale is the weakest number.** Spatial pattern (which
  coasts get hit, and when) rests on observed currents and forecast winds and is
  the trustworthy part. Absolute kg/m depends on the stranding efficiency, which
  is why `physical_scale` exists and why it needs the trap record to converge.
* **Currents are persistence beyond now.** HF radar has no forecast mode. Skill
  degrades with lead time; days 4–5 are indicative.
* **HF-radar coverage has gaps** — it thins with distance offshore and during
  outages. Gaps are treated as zero current, which biases drift toward
  wind-only motion there.
* **The coastline is coarse** (~5–15 km vertices, hand-traced). Good enough to
  place receptors and compute a seaward normal; not navigational. Swap in a real
  shapefile-derived polygon and everything downstream still works.
* **The learned model is only trained at La Parguera**, on the south-west coast.
  It is applied only to the segments that host traps. Everywhere else is
  physics plus one global bias scale.
* **Gas numbers are order-of-magnitude.** The yield model is defensible and
  mass-conserving, but the escape fraction of sulfide is uncertain to at least
  a factor of a few. Treat tiers and relative ranking as more reliable than
  absolute tonnes.
* **No growth, sinking, or fragmentation.** Rafts are conserved until they
  strand. Over a 5-day horizon that is reasonable; over weeks it is not.

## Repository layout

```
config/config.yaml          all tunable coefficients
sargassum/
  erddap.py                 ERDDAP client: retries, caching
  sources.py                one function per data product
  biomass.py                AFAI → coverage → wet biomass
  coastline.py              island outlines → named segments with normals
  drift.py                  vector fields, particles, Heun integration
  beaching.py               shoreline capture, land barrier
  emissions.py              H₂S / NH₃ yield model
  features.py               catchment indices, lags, seasonality
  model.py                  learned calibration layer
  pipeline.py               end-to-end run
  render.py                 maps + the website data contract
  palette.py                shared colour tokens
scripts/
  update.py                 the scheduled job
  backfill.py               historical training set
  train_model.py            fit / refit
tests/                      physical sanity checks + synthetic fields
data/archive/               accumulating record (committed)
site/                       published output (GitHub Pages)
```
