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
function assert(cond, msg) { if (!cond) throw new Error(msg); }

/** Boot app.js against a fake DOM with the given latest.json, and hand back
 *  the document so assertions can look at what it rendered. */
async function boot(latest, missing = []) {
  const read = (f) => JSON.parse(readFileSync(join(SITE, 'data', f), 'utf8'));
  const files = {
    'latest.json': latest,
    'forecast_segments.geojson': read('forecast_segments.geojson'),
    'biomass_field.json': read('biomass_field.json'),
    'drift_tracks.json': read('drift_tracks.json'),
    'basemap_land.geojson': read('basemap_land.geojson'),
    'basemap_places.geojson': read('basemap_places.geojson'),
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
  const layers = [], sources = [], markers = [], maps = [];
  window.maplibregl = {
    Map: class {
      constructor() { this._h = {}; maps.push(this); }
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

  return { doc: window.document, window, layers, sources, markers };
}

const base = JSON.parse(readFileSync(join(SITE, 'data', 'latest.json'), 'utf8'));
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
  const { doc, layers, sources, markers } = await boot(clone(base));
  check('land is drawn from the bundled vector source', () => {
    assert(sources.includes('land'), 'no land source; got ' + sources.join(', '));
    ['land-halo', 'land-fill', 'land-outline'].forEach((id) =>
      assert(layers.includes(id), 'missing layer ' + id));
  });
  check('the segment stand-in coastline is gone', () => {
    assert(!layers.includes('coast-shadow') && !layers.includes('coast-edge'),
           'stale coastline layers: ' + layers.join(', '));
  });
  check('place labels are added as markers', () => {
    assert(markers.length > 20, 'only ' + markers.length + ' place markers');
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
  // A missing basemap must cost the coastline and nothing else.
  const { doc, layers } = await boot(clone(base),
    ['basemap_land.geojson', 'basemap_places.geojson']);
  check('a missing basemap does not take the forecast down with it', () => {
    assert(!layers.includes('land-fill'), 'land drew without its source');
    assert(layers.includes('segment-line'),
           'forecast layers missing too: ' + layers.join(', '));
    assert(!/could not start/i.test(doc.getElementById('map').textContent),
           'the map bailed out');
    assert(/t wet|tonnes/i.test(doc.getElementById('stats').textContent),
           'headline figures did not render');
  });
}

// ------------------------------------------------------- no external hosts
check('no third-party tile or glyph host is referenced', () => {
  const js = readFileSync(join(SITE, 'app.js'), 'utf8');
  const html = readFileSync(join(SITE, 'index.html'), 'utf8');
  [/cartocdn/i, /basemaps\./i, /api[_-]?key/i, /tile\.openstreetmap/i]
    .forEach((re) => {
      assert(!re.test(js), 'app.js still references ' + re);
      assert(!re.test(html), 'index.html still references ' + re);
    });
});

console.log(failures ? `\n${failures} failed` : '\nall passed');
process.exit(failures ? 1 : 0);
