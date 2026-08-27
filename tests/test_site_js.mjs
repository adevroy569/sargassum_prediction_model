/* Headless checks for site/app.js.
 *
 * The page has no build step and no test runner, so its logic was only ever
 * exercised by loading the deployed site and looking at it. That is how a
 * banner reading "this forecast run is running on incomplete inputs" stayed
 * up on every run for days: nothing asserted when it was supposed to appear.
 *
 * Run:  node tests/test_site_js.mjs      (requires: npm i jsdom)
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { JSDOM } from 'jsdom';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SITE = join(ROOT, 'site');

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log('  ok   ' + name);
  } catch (e) {
    failures++;
    console.log('  FAIL ' + name + '\n         ' + e.message);
  }
}
function assert(cond, msg) {
  // An assert with no message reports a blank failure, which costs more time
  // to diagnose than the assert saved to write. Refuse them.
  if (msg === undefined) throw new Error('assert() called without a message');
  if (!cond) throw new Error(msg);
}

/** Boot app.js against a fake DOM with the given latest.json, and hand back
 *  the document so assertions can look at what it rendered. */
async function boot(latest, missing = []) {
  const read = (f) => JSON.parse(readFileSync(join(SITE, 'data', f), 'utf8'));
  const files = {
    'latest.json': latest,
    'forecast_segments.geojson': read('forecast_segments.geojson'),
    'biomass_field.json': read('biomass_field.json'),
    'drift_tracks.json': read('drift_tracks.json'),
  };
  missing.forEach((f) => { delete files[f]; });

  // runScripts: 'dangerously' so app.js executes with the window's own
  // globals. External <script src> tags are not fetched (no resource loader),
  // so index.html's own reference to app.js is inert and the copy injected
  // below is the only one that runs.
  const dom = new JSDOM(readFileSync(join(SITE, 'index.html'), 'utf8'),
                        { url: 'https://example.invalid/',
                          runScripts: 'dangerously' });
  const { window } = dom;

  window.fetch = (url) => {
    const name = String(url).split('/').pop();
    return Object.prototype.hasOwnProperty.call(files, name)
      ? Promise.resolve({ ok: true, status: 200,
                          json: () => Promise.resolve(files[name]) })
      : Promise.resolve({ ok: false, status: 404,
                          json: () => Promise.reject(new Error('404')) });
  };

  // Enough of MapLibre to let buildMap run to completion. The map itself is
  // not under test here; the code paths around it are.
  // app.js keeps its state inside an IIFE, so the map instance is captured
  // here on construction rather than read off a global.
  const layers = [], sources = [], markers = [], maps = [], styles = [];
  window.maplibregl = {
    Map: class {
      constructor(opts) { this._h = {}; maps.push(this); styles.push(opts.style); }
      on(ev, a, b) { (this._h[ev] = this._h[ev] || []).push(b || a); }
      once(ev, cb) { this.on(ev, cb); }
      fire(ev) { (this._h[ev] || []).forEach((f) => f({})); }
      addSource(id, s) { sources.push(id); }
      addLayer(l) { layers.push(l.id); }
      getLayer() { return null; }
      getSource() { return { setData() {} }; }
      addControl() {} setLayoutProperty() {} setPaintProperty() {}
      getCanvas() { return { style: {} }; }
      getZoom() { return 9; }
      fitBounds() {} easeTo() {} resize() {}
    },
    NavigationControl: class {}, ScaleControl: class {},
    Popup: class { setLngLat() { return this; } setHTML() { return this; }
                   addTo() { return this; } remove() { return this; } },
    Marker: class {
      constructor(o) { this.el = o.element; markers.push(this); }
      setLngLat() { return this; } addTo() { return this; }
      getElement() { return this.el; }
    },
    LngLatBounds: class { extend() { return this; } },
  };

  const script = window.document.createElement('script');
  script.textContent = readFileSync(join(SITE, 'app.js'), 'utf8');
  window.document.body.appendChild(script);
  // let the data promises settle
  await new Promise((r) => setTimeout(r, 250));
  if (maps.length) maps[0].fire('load');
  await new Promise((r) => setTimeout(r, 150));

  return { doc: window.document, window, layers, sources, markers,
           style: styles[0] };
}

/* A healthy run, written out in full rather than read from site/data.
 *
 * These tests used to use whatever latest.json happened to be on disk. CI runs
 * `update.py --offline` before them, which replaces it with a synthetic run
 * carrying no notes and no wind coverage - so the suite passed locally and
 * failed in CI, and its meaning changed with every pipeline run. A fixture is
 * the whole point: the alert logic has to be pinned to known inputs.
 *
 * These numbers mirror a real run: the CariCOOS 2 km WRF nest covering 106 h
 * of the 120 h drift horizon, which is the normal, permanent state.
 */
const base = {
  issued_at: '2026-08-27T20:00:00+00:00',
  afai_scene_time: '2026-08-26 12:00:00',
  current_field_time: '2026-08-27 17:00:00',
  horizon_hours: 120,
  days: ['2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30', '2026-08-31'],
  gas_days: ['2026-08-27', '2026-08-28', '2026-08-29', '2026-08-30',
             '2026-08-31', '2026-09-01', '2026-09-02', '2026-09-03',
             '2026-09-04', '2026-09-05', '2026-09-06', '2026-09-07',
             '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11',
             '2026-09-12'],
  offshore_wet_tonnes: 4282465.0,
  predicted_stranding_tonnes: 71763.12,
  predicted_h2s_tonnes: 55.02,
  predicted_nh3_tonnes: 17.19,
  worst_segments: [
    { name: 'Yabucoa', seg_id: 'PU040', kg_per_m: 873.45, risk_tier: 'high' },
    { name: 'Fajardo / Sardinera', seg_id: 'PU033', kg_per_m: 553.67,
      risk_tier: 'high' }
  ],
  calibration_scale: 1.0,
  h2s_kg_per_tonne_wet: 0.8,
  nh3_kg_per_tonne_wet: 0.25,
  flux_above_literature_fraction: 0.0,
  model: {},
  stranding_classes: [
    { name: 'Scattered', max: 5.0, blurb: 'A thin, broken line of weed.' },
    { name: 'Continuous band', max: 25.0, blurb: 'Unbroken weed along the beach.' },
    { name: 'Heavy windrow', max: 100.0, blurb: 'A packed ridge, shin to knee deep.' },
    { name: 'Severe pile-up', max: null, blurb: 'Deep continuous mats.' }
  ],
  h2s_risk_tiers: { low: 0.5, moderate: 3.0, high: 12.0 },
  notes: ['WRF wind runs 106 h of the 120 h horizon; wind is held constant ' +
          'beyond that'],
  status: {
    wind: { ok: true, dataset: 'wrf_nmm_2km_best_agg', n_fields: 110,
            covered_hours: 106.0, horizon_hours: 120, reason: '' }
  },
  maps: { offshore: 'maps/offshore_biomass.png',
          beaching: 'maps/beaching_forecast.png',
          emissions: 'maps/h2s_forecast.png' }
};
const clone = (o) => JSON.parse(JSON.stringify(o));

console.log('site/app.js');

// ---------------------------------------------------------------- alerts
{
  // The live run: WRF covers 106 h of a 120 h horizon. That is the permanent,
  // healthy state of a 2 km nest against a 5 day horizon.
  const { doc } = await boot(clone(base));
  check('healthy run with a short WRF tail shows no alert', () => {
    const box = doc.getElementById('runStatus');
    assert(box.hidden, 'alert was shown: ' + box.textContent.trim().slice(0, 120));
  });
  check('the short tail is still disclosed in the run notes', () => {
    const notes = doc.getElementById('notes').textContent;
    assert(/held constant/i.test(notes), 'notes did not mention it: ' + notes);
  });
}
{
  const l = clone(base);
  l.status.wind = { ok: true, covered_hours: 60, horizon_hours: 120 };
  const { doc } = await boot(l);
  check('a materially short wind record does raise the alert', () => {
    const box = doc.getElementById('runStatus');
    assert(!box.hidden, 'alert stayed hidden');
    assert(/60 h of the 120 h/.test(box.textContent),
           'alert did not quantify the gap: ' + box.textContent);
  });
}
{
  const l = clone(base);
  l.status.wind = { ok: false, reason: 'ErddapError' };
  const { doc } = await boot(l);
  check('missing wind entirely still raises the alert', () => {
    const box = doc.getElementById('runStatus');
    assert(!box.hidden, 'alert stayed hidden');
    assert(/unavailable/i.test(box.textContent), box.textContent);
  });
}
{
  const l = clone(base);
  l.predicted_stranding_tonnes = 0;
  const { doc } = await boot(l);
  check('a zero-stranding run still raises the alert', () => {
    const box = doc.getElementById('runStatus');
    assert(!box.hidden, 'alert stayed hidden');
    assert(/no stranding anywhere/i.test(box.textContent), box.textContent);
  });
}
{
  const l = clone(base);
  delete l.status;                       // archived run, prose notes only
  l.notes = ['WRF wind runs 40 h of the 120 h horizon; wind is held constant beyond that'];
  const { doc } = await boot(l);
  check('older runs without status are read from their notes', () => {
    const box = doc.getElementById('runStatus');
    assert(!box.hidden, 'alert stayed hidden for an 80 h gap');
  });
}

// --------------------------------------------------------------- basemap
{
  const { doc, layers, style } = await boot(clone(base));

  check('the basemap is Esri Firefly imagery, base plus labels', () => {
    const ids = style.layers.map((l) => l.id);
    assert(ids.includes('esri-base') && ids.includes('esri-labels'),
           'style layers: ' + ids.join(', '));
    const baseTiles = style.sources['esri-base'].tiles.join(' ');
    // Firefly is served from fly.maptiles, NOT from services.arcgisonline -
    // the same service path under the arcgisonline host returns 404s.
    assert(/fly\.maptiles\.arcgis\.com/.test(baseTiles), baseTiles);
    assert(/World_Imagery_Firefly/.test(baseTiles), baseTiles);
    const refTiles = style.sources['esri-labels'].tiles.join(' ');
    assert(/services\.arcgisonline\.com/.test(refTiles), refTiles);
    ['esri-base', 'esri-labels'].forEach((k) => {
      // ArcGIS REST tile paths are {z}/{row}/{col}. Getting x and y the wrong
      // way round still returns valid tiles, just of somewhere else entirely.
      assert(/\{z\}\/\{y\}\/\{x\}/.test(style.sources[k].tiles.join(' ')),
             k + ' has x and y transposed');
    });
  });

  check('the imagery is dimmed, labels held higher', () => {
    const byId = Object.fromEntries(style.layers.map((l) => [l.id, l]));
    const b = byId['esri-base'].paint;
    const t = byId['esri-labels'].paint;
    // Firefly carries highlights up to luminance 254 - cloud, sand, city
    // lights. Undimmed, those outshine the data ramps drawn on top.
    assert(b['raster-brightness-max'] <= 0.65,
           'imagery not dimmed: ' + b['raster-brightness-max']);
    assert(b['raster-saturation'] < 0, 'imagery not desaturated');
    assert(t['raster-brightness-max'] > b['raster-brightness-max'],
           'labels dimmed as hard as the imagery');
  });

  check('Esri attribution is carried', () => {
    const a = style.sources['esri-base'].attribution || '';
    assert(/esri/i.test(a), 'no Esri attribution: ' + a);
  });

  check('the retired vector basemap leaves nothing behind', () => {
    ['land-halo', 'land-fill', 'land-outline', 'coast-shadow', 'coast-edge']
      .forEach((id) => assert(!layers.includes(id), 'stale layer ' + id));
    const js = readFileSync(join(SITE, 'app.js'), 'utf8');
    const css = readFileSync(join(SITE, 'styles.css'), 'utf8');
    assert(!/basemap_land|basemap_places|addPlaceLabels|addBasemapLayers/
           .test(js), 'app.js still references the vector basemap');
    assert(!/\.map-place/.test(css), 'styles.css still has the marker rules');
  });

  check('the segment count in the methodology comes from the data', () => {
    const n = readFileSync(join(SITE, 'data', 'forecast_segments.geojson'),
                           'utf8');
    const expected = String(JSON.parse(n).features.length);
    assert(doc.getElementById('segCount').textContent === expected,
           'said ' + doc.getElementById('segCount').textContent +
           ', data has ' + expected);
  });
  check('the map still failed over nothing', () => {
    assert(!/could not start/i.test(doc.getElementById('map').textContent),
           'buildMap threw');
  });
}
{
  // Losing the drift tracks must cost the tracks and nothing else.
  const { doc, layers } = await boot(clone(base), ['drift_tracks.json']);
  check('a missing optional file does not take the forecast down with it', () => {
    assert(layers.includes('segment-line'),
           'forecast layers missing: ' + layers.join(', '));
    assert(!/could not start/i.test(doc.getElementById('map').textContent),
           'the map bailed out');
    assert(/t wet|tonnes/i.test(doc.getElementById('stats').textContent),
           'headline figures did not render');
  });
}

// -------------------------------------------------------------- formulas
{
  const l = clone(base);
  l.coefficients = {
    windage: 0.012, horizontal_diffusivity: 30.0, capture_km: 2.0,
    onshore_efficiency: 0.35, min_onshore_speed: 0.02,
    segment_spacing_km: 5.0, onset_lag_hours: 48, peak_hours: 72,
    decay_efold_hours: 120, deposit_width_m: 8.0, mixing_height_m: 2.5
  };
  const { doc } = await boot(l);
  check('the published formulas carry the run\'s own coefficients', () => {
    const t = doc.getElementById('formulas').textContent;
    // Printing a default while the run used something else is the whole
    // failure mode this block exists to prevent, so check the values through.
    [['0.012', 'windage'], ['0.35', 'stranding efficiency'],
     ['2 km', 'capture distance'], ['0.8 kg', 'H2S yield'],
     ['48', 'onset lag'], ['72', 'peak'], ['120', 'e-folding']]
      .forEach(([v, what]) =>
        assert(t.includes(v), what + ' (' + v + ') missing from: ' + t.slice(0, 200)));
    assert(/conserves\s+mass|conserve/i.test(t), 'mass conservation not stated');
  });
  check('a run without coefficients still prints the formulas', () => {
    const bare = clone(base);
    delete bare.coefficients;
    return boot(bare).then(({ doc: d2 }) => {
      const t = d2.getElementById('formulas').textContent;
      assert(t.length > 200, 'formulas block collapsed without coefficients');
    });
  });
}

// ----------------------------------------------------------- calibration
{
  // The live state: no model fitted, scale 1.0, every segment "physical".
  const { doc } = await boot(clone(base));
  check('an untrained run says so instead of implying a trained one', () => {
    const t = doc.getElementById('calibState').textContent;
    assert(/not yet active/i.test(t), 'calibration state not disclosed: ' + t);
    assert(/upper bound|absolute kilograms are not/i.test(t),
           'no caveat on the absolute numbers: ' + t);
    assert(doc.getElementById('calibState').className.includes('calib-warn'),
           'untrained state not visually flagged');
  });
}
{
  const l = clone(base);
  l.model = { n_train: 812, mae_kg_per_m: 3.4 };
  l.calibration_scale = 0.41;
  const { doc } = await boot(l);
  check('a trained run reports its training size and error', () => {
    const t = doc.getElementById('calibState').textContent;
    assert(/812/.test(t), 'training size missing: ' + t);
    assert(/3\.4/.test(t), 'hold-out error missing: ' + t);
    assert(!/not yet active/i.test(t), 'still claiming untrained: ' + t);
  });
}

// ------------------------------------------------- forecast window edges
{
  const l = clone(base);
  const { doc, window } = await boot(l);

  /* Driven through the real controls rather than an exported hook: app.js
   * keeps its state inside an IIFE, and clicking the tab and dragging the
   * slider is what a reader actually does to reach this state. */
  const setLayer = (name) => {
    const b = doc.querySelector('#layerSwitch button[data-layer="' + name + '"]');
    assert(b, 'no layer tab for ' + name);
    b.dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  };
  const setDay = (i) => {
    const r = doc.getElementById('tlRange');
    assert(r, 'no timeline range input');
    r.value = String(i);
    r.dispatchEvent(new window.Event('input', { bubbles: true }));
  };

  check('past the drift horizon, stranding says "no data", not "none"', () => {
    // day 8 is inside gas_days (17 long) but past days (5 long)
    setLayer('stranding');
    setDay(8);
    const legend = doc.getElementById('legend').textContent;
    const cap = doc.getElementById('mapCaption').textContent;
    assert(/Outside the forecast window/i.test(legend),
           'legend did not flag the gap: ' + legend.slice(0, 160));
    assert(!/None predicted/i.test(legend),
           'legend still offers "None predicted" past the window');
    assert(/not\s+forecast/i.test(cap) || /absence of a prediction/i.test(legend),
           'nothing distinguishes missing data from a clean coast');
  });
  check('inside the window, the normal stranding legend is back', () => {
    setDay(2);
    const legend = doc.getElementById('legend').textContent;
    assert(/None predicted/i.test(legend),
           'severity legend missing inside the window: ' + legend.slice(0, 160));
    assert(!/Outside the forecast window/i.test(legend),
           'still showing the out-of-window legend');
  });
}

// ------------------------------------------------------------ dependencies
check('the withdrawn CARTO basemap is not referenced anywhere', () => {
  const js = readFileSync(join(SITE, 'app.js'), 'utf8');
  const html = readFileSync(join(SITE, 'index.html'), 'utf8');
  // Not a generic third-party ban - Esri is a deliberate dependency now. This
  // guards the specific host that began stamping API KEY REQUIRED on tiles.
  [/cartocdn/i, /basemaps\.carto/i].forEach((re) => {
    assert(!re.test(js), 'app.js still references ' + re);
    assert(!re.test(html), 'index.html still references ' + re);
  });
});

console.log(failures ? `\n${failures} failed` : '\nall passed');
process.exit(failures ? 1 : 0);
