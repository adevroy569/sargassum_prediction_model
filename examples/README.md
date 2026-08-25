# Example outputs

These files were produced by `python scripts/update.py --offline`, which runs
the whole pipeline against **synthetic** AFAI, current and wind fields. They
exist so the website can be built against the real data contract before the
first live run — they are not a real forecast, and nothing here is committed to
`site/` where GitHub Pages would serve it.

The first live run writes the same file names into `site/data/` and
`site/maps/`.

## `latest.json`

```jsonc
{
  "issued_at": "2026-08-25T20:00:00+00:00",   // run time, UTC
  "afai_scene_time": "...",                    // satellite scene used
  "current_field_time": "...",                 // HF-radar field used
  "horizon_hours": 120,
  "days": ["2026-08-25", ...],                 // stranding forecast days
  "gas_days": ["2026-08-25", ...],             // longer: includes decomposition tail
  "offshore_wet_tonnes": 841122.0,             // biomass in the model domain
  "predicted_stranding_tonnes": 19082.6,       // island-wide over the horizon
  "predicted_h2s_tonnes": 14.2,
  "predicted_nh3_tonnes": 4.4,
  "worst_segments": [ {name, seg_id, kg_per_m, risk_tier}, ... ],
  "calibration_scale": 1.0,                    // physical bias correction in force
  "h2s_kg_per_tonne_wet": 0.8,                 // yields actually used
  "nh3_kg_per_tonne_wet": 0.25,
  "flux_above_literature_fraction": 0.0,       // >0 means the run flagged itself
  "model": { ...hold-out metrics, or {} if untrained... },
  "notes": ["..."],                            // degraded-input warnings
  "maps": { "offshore": "maps/...png", ... }
}
```

## `forecast_segments.geojson`

`FeatureCollection` of ~103 Points, one per ~5 km of coast. Top-level
`properties.days` / `properties.gas_days` give the date axis for the arrays.

Per feature:

| Property | Meaning |
|---|---|
| `seg_id`, `name`, `island`, `coast` | identity (`name` is the nearest landmark) |
| `length_m` | shoreline length the segment represents |
| `kg_per_m_total` | published stranding forecast, kg per metre of shoreline |
| `kg_per_m_physical` | uncalibrated physical-model value |
| `tonnes_total`, `tonnes_by_day` | absolute stranded wet mass |
| `h2s_kg_per_day`, `nh3_kg_per_day` | gas release, aligned to `gas_days` |
| `h2s_peak_kg_day_km` | peak daily H₂S per km — what the risk tier is cut on |
| `risk_tier` | `minimal` / `low` / `moderate` / `high` |
| `exposure` | `{ppm, label, ...reference thresholds}` at the wrack line |
| `source` | `learned` (trained at that site) or `physical` |

## `biomass_field.json`

`{time, total_wet_tonnes, bounds: [latMin, lonMin, latMax, lonMax],
points: [[lat, lon, kg_per_m2], ...]}` — only cells with detected Sargassum,
capped at 9000 of the densest.

## `drift_tracks.json`

`{issued_at, tracks: [{lat: [...], lon: [...], mass_kg}]}` — up to 120 of the
heaviest rafts, sampled every 6 h. For animating the drift.

## `stations.json`

Latest measurement from each CariCOOS trap: `{time, station, latitude,
longitude, biomass_kg_per_m}`.
