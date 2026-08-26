/* Sargassum Forecast - Puerto Rico
 * Static front end. Reads the JSON the Python pipeline writes into site/data/.
 * No dependencies beyond MapLibre GL. No cookies, no analytics, no requests
 * other than the map tiles.
 */
(function () {
  'use strict';

  // ------------------------------------------------------------ constants
  var DATA_DIRS = ['data/', '../examples/data/'];

  // cividis, clipped away from its darkest end so low values stay readable on
  // a dark basemap. Perceptually uniform and colourblind safe.
  var CIVIDIS = ['#21395f', '#39486b', '#575d6d', '#707173',
                 '#8a8779', '#a69d75', '#c4b56c', '#ffea46'];

  // Ordered severity ramp for the two prediction layers. One hue family,
  // monotonically lighter with severity, so on a dark basemap "brighter" always
  // reads as "worse". Validated as an ordinal ramp against this page's surface
  // (#0c0f11): lightness monotone, adjacent dL >= 0.06, hue spread 40 deg, and
  // the darkest step still clears 2:1 against the background.
  var SEVERITY = ['#8c4a20', '#c26a1d', '#e89a2a', '#ffd24a'];

  // Not part of the ramp. A segment the model expects nothing on is drawn as a
  // thin, desaturated hairline - it must recede, because the loudest thing on a
  // map should never be the places where nothing happens.
  var NONE_COLOR = '#546070';

  var TIER = {
    minimal:  { color: NONE_COLOR,   label: 'Minimal' },
    low:      { color: SEVERITY[1],  label: 'Low' },
    moderate: { color: SEVERITY[2],  label: 'Moderate' },
    high:     { color: SEVERITY[3],  label: 'High' }
  };
  var TIER_ORDER = ['minimal', 'low', 'moderate', 'high'];

  // Plain-language meaning for each H2S tier, keyed to the thresholds in
  // config.yaml (kg per day per km of shoreline).
  var TIER_BLURB = {
    minimal:  'No detectable smell expected.',
    low:      'Faint rotten-egg smell close to the wrack line.',
    moderate: 'Clear smell along the beach. Sensitive people may react.',
    high:     'Strong smell. Above health guideline levels near the pile.'
  };

  var BASEMAP = {
    version: 8,
    sources: {
      carto: {
        type: 'raster',
        tiles: [
          'https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
          'https://b.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
          'https://c.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png',
          'https://d.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'
        ],
        tileSize: 256,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
          '&copy; <a href="https://carto.com/attributions">CARTO</a>'
      }
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#0c0f11' } },
      { id: 'carto', type: 'raster', source: 'carto' }
    ]
  };

  // ------------------------------------------------------------- helpers
  function hexToRgb(h) {
    return [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  }
  function rgbToHex(c) {
    return '#' + c.map(function (v) {
      return Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0');
    }).join('');
  }
  /** Sample a colour ramp at t in [0, 1]. */
  function ramp(stops, t) {
    if (!isFinite(t)) t = 0;
    t = Math.max(0, Math.min(1, t));
    var x = t * (stops.length - 1);
    var i = Math.floor(x), f = x - i;
    if (i >= stops.length - 1) return stops[stops.length - 1];
    var a = hexToRgb(stops[i]), b = hexToRgb(stops[i + 1]);
    return rgbToHex([a[0] + (b[0] - a[0]) * f,
                     a[1] + (b[1] - a[1]) * f,
                     a[2] + (b[2] - a[2]) * f]);
  }
  function gradientCss(stops) {
    return 'linear-gradient(90deg,' + stops.join(',') + ')';
  }
  function quantile(sorted, q) {
    if (!sorted.length) return 0;
    var p = (sorted.length - 1) * q, lo = Math.floor(p), hi = Math.ceil(p);
    return sorted[lo] + (sorted[hi] - sorted[lo]) * (p - lo);
  }
  function fmt(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return 'n/a';
    if (digits === undefined) digits = Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2;
    return v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  function shortDay(iso) {
    var d = new Date(iso + 'T12:00:00Z');
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
  }
  function el(tag, cls, html) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (html !== undefined) n.innerHTML = html;
    return n;
  }
  function svgEl(tag, attrs) {
    var n = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (var k in attrs) if (attrs[k] !== undefined && attrs[k] !== null) n.setAttribute(k, attrs[k]);
    return n;
  }

  /** Try each candidate directory so the page works from site/ or from a
   *  local checkout before the first pipeline run has written site/data/. */
  function loadJSON(name) {
    var i = 0;
    function attempt() {
      if (i >= DATA_DIRS.length) return Promise.reject(new Error('missing ' + name));
      var url = DATA_DIRS[i++] + name;
      return fetch(url, { cache: 'no-store' })
        .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
        .catch(attempt);
    }
    return attempt();
  }

  // ---------------------------------------------------------- tooltip
  var tip = el('div', 'tip');
  document.body.appendChild(tip);
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.classList.add('on');
    var r = tip.getBoundingClientRect();
    var left = Math.min(Math.max(8, x + 14), window.innerWidth - r.width - 8);
    var top = Math.max(8, y - r.height - 12);
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { tip.classList.remove('on'); }

  // ============================================================== STATS
  function renderStamp(latest) {
    var issued = new Date(latest.issued_at);
    var scene = latest.afai_scene_time ? String(latest.afai_scene_time).slice(0, 10) : 'unknown';
    var parts = [
      'Run issued ' + issued.toLocaleString(undefined, {
        dateStyle: 'medium', timeStyle: 'short'
      }) + ' local time',
      'satellite scene ' + scene,
      latest.horizon_hours / 24 + ' day outlook'
    ];
    document.getElementById('stamp').textContent = parts.join('  ·  ');
  }

  function renderStats(latest, segs) {
    var box = document.getElementById('stats');
    box.innerHTML = '';
    var worst = (latest.worst_segments || [])[0];
    var peakGas = 0;
    (segs.features || []).forEach(function (f) {
      (f.properties.h2s_kg_per_day || []).forEach(function (v, i) {
        peakGas = Math.max(peakGas, v);
      });
    });
    var gasDaily = sumByDay(segs, 'h2s_kg_per_day');
    var peakDay = gasDaily.indexOf(Math.max.apply(null, gasDaily));

    var tiles = [
      {
        k: 'Offshore now',
        v: fmt(latest.offshore_wet_tonnes, 0), u: 't wet',
        s: 'Detected in the model domain'
      },
      {
        k: 'Predicted stranding',
        v: fmt(latest.predicted_stranding_tonnes, 0), u: 't wet',
        s: 'Island wide over ' + (latest.horizon_hours / 24) + ' days'
      },
      {
        k: 'Hydrogen sulfide',
        v: fmt(latest.predicted_h2s_tonnes, 1), u: 't',
        s: 'Peaks ' + (latest.gas_days && latest.gas_days[peakDay]
              ? shortDay(latest.gas_days[peakDay]) : 'later this week')
      },
      {
        k: 'Worst affected',
        v: worst ? worst.name : 'n/a', u: '',
        s: worst ? fmt(worst.kg_per_m, 1) + ' kg per metre of shoreline' : '',
        small: true
      }
    ];
    tiles.forEach(function (t) {
      var n = el('div', 'tile');
      n.appendChild(el('div', 'k', t.k));
      var v = el('div', 'v');
      v.style.fontSize = t.small ? '19px' : '';
      v.innerHTML = t.v + (t.u ? ' <small>' + t.u + '</small>' : '');
      n.appendChild(v);
      n.appendChild(el('div', 's', t.s));
      box.appendChild(n);
    });
  }

  function sumByDay(segs, key) {
    var out = [];
    (segs.features || []).forEach(function (f) {
      (f.properties[key] || []).forEach(function (v, i) {
        out[i] = (out[i] || 0) + (v || 0);
      });
    });
    return out;
  }

  // ================================================================ MAP
  var map, currentLayer = 'biomass', tracksOn = false;

  function biomassGeoJSON(bio) {
    var vals = bio.points.map(function (p) { return p[2]; }).sort(function (a, b) { return a - b; });
    var lo = Math.max(quantile(vals, 0.05), 1e-4);
    var hi = Math.max(quantile(vals, 0.995), lo * 4);
    var lLo = Math.log(lo), lHi = Math.log(hi);
    var feats = bio.points.map(function (p) {
      var t = (Math.log(Math.max(p[2], lo)) - lLo) / (lHi - lLo);
      t = Math.max(0, Math.min(1, t));
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [p[1], p[0]] },
        properties: { v: p[2], color: ramp(CIVIDIS, t), r: 2.2 + 3.4 * t }
      };
    });
    return { gj: { type: 'FeatureCollection', features: feats }, lo: lo, hi: hi };
  }

  /** Classify a stranding value (kg per m of shoreline) into one of the bands
   *  declared in config.yaml and served in latest.json. Returns -1 for "the
   *  model expects nothing here", which is a state, not the bottom of a scale. */
  function strandBand(v, classes) {
    if (!(v > 0)) return -1;
    for (var i = 0; i < classes.length; i++) {
      var hi = classes[i].max;
      if (hi === null || hi === undefined || v <= hi) return i;
    }
    return classes.length - 1;
  }

  /** Segment features carrying a band index, a colour and a line width.
   *
   *  Magnitude is carried by colour alone. Width is binary - a valued segment
   *  is a thick ribbon, an empty one a hairline - so it marks presence, not
   *  size, and the two never compete. The old version scaled both radius and
   *  colour off `quantile(vals, 0.97)`, which collapsed to the 1e-3 floor
   *  whenever most segments were zero: every non-zero segment then saturated
   *  at the top of the ramp and the legend read "0 to 0+".
   */
  function segmentGeoJSON(segs, mode, classes) {
    var isGas = mode === 'h2s';
    var key = isGas ? 'h2s_peak_kg_day_km' : 'kg_per_m_total';
    var counts = [0, 0, 0, 0], nEmpty = 0, hi = 0;
    var feats = segs.features.map(function (f) {
      var p = f.properties;
      var v = p[key] || 0;
      if (v > hi) hi = v;
      var band, color;
      if (isGas) {
        var tierKey = p.risk_tier || 'minimal';
        band = TIER_ORDER.indexOf(tierKey);
        if (band < 0) band = 0;
        color = (TIER[tierKey] || TIER.minimal).color;
        if (band === 0) nEmpty++; else counts[band]++;
        band = band === 0 ? -1 : band;
      } else {
        band = strandBand(v, classes);
        color = band < 0 ? NONE_COLOR : SEVERITY[band];
        if (band < 0) nEmpty++; else counts[band]++;
      }
      // The popup is shared between layers, so it always names the stranding
      // band regardless of which layer is on screen.
      var sb = strandBand(p.kg_per_m_total || 0, classes);
      return {
        type: 'Feature',
        geometry: f.geometry,
        properties: Object.assign({}, p, {
          color: color,
          band: band,
          width: band < 0 ? 1.6 : 5.5,
          _v: v,
          _bandName: sb < 0 ? 'None predicted'
                            : ((classes[sb] || {}).name || 'n/a')
        })
      };
    });
    return {
      gj: { type: 'FeatureCollection', features: feats },
      hi: hi, counts: counts, nEmpty: nEmpty
    };
  }

  function tracksGeoJSON(tr) {
    var feats = (tr.tracks || []).filter(function (t) { return t.lat && t.lat.length > 1; })
      .map(function (t) {
        return {
          type: 'Feature',
          geometry: {
            type: 'LineString',
            coordinates: t.lat.map(function (la, i) { return [t.lon[i], la]; })
          },
          properties: { mass_kg: t.mass_kg }
        };
      });
    return { type: 'FeatureCollection', features: feats };
  }

  function buildMap(bio, segs, tracks, latest) {
    map = new maplibregl.Map({
      container: 'map',
      style: BASEMAP,
      center: [-66.4, 18.15],
      zoom: 7.4,
      minZoom: 5,
      maxZoom: 12,
      attributionControl: { compact: true }
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'metric' }), 'bottom-left');

    var bioData = biomassGeoJSON(bio);
    var classes = latest.stranding_classes || [];
    var strandData = segmentGeoJSON(segs, 'stranding', classes);
    var gasData = segmentGeoJSON(segs, 'h2s', classes);

    map.on('load', function () {
      map.addSource('biomass', { type: 'geojson', data: bioData.gj });
      map.addSource('segments', { type: 'geojson', data: strandData.gj });
      map.addSource('tracks', { type: 'geojson', data: tracksGeoJSON(tracks) });

      map.addLayer({
        id: 'tracks-line', type: 'line', source: 'tracks',
        layout: { 'line-cap': 'round', visibility: 'none' },
        paint: {
          'line-color': '#8a8779',
          'line-width': 1.1,
          'line-opacity': 0.45
        }
      });

      map.addLayer({
        id: 'biomass-pt', type: 'circle', source: 'biomass',
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            5, ['*', ['get', 'r'], 0.55],
            8, ['get', 'r'],
            11, ['*', ['get', 'r'], 2.2]],
          'circle-color': ['get', 'color'],
          'circle-opacity': 0.82,
          'circle-blur': 0.25
        }
      });

      // Each segment is ~5 km of shoreline, so it is drawn as the shoreline it
      // covers. A dark casing underneath keeps the ribbon legible where it
      // crosses the coast outline.
      map.addLayer({
        id: 'segment-casing', type: 'line', source: 'segments',
        layout: { visibility: 'none', 'line-cap': 'butt' },
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': '#07090a',
          'line-opacity': 0.85,
          'line-width': ['interpolate', ['linear'], ['zoom'],
            6, ['+', ['get', 'width'], 2.5],
            9, ['+', ['*', ['get', 'width'], 1.5], 3],
            12, ['+', ['*', ['get', 'width'], 2.6], 3]]
        }
      });

      // Fallback for data written by a pipeline older than the LineString
      // change: those files carry a centre Point per segment, which a line
      // layer would silently draw as nothing. Filtered by geometry type so the
      // two layers never both draw the same feature.
      map.addLayer({
        id: 'segment-pt', type: 'circle', source: 'segments',
        layout: { visibility: 'none' },
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            6, ['case', ['<', ['get', 'band'], 0], 2.2, 5],
            11, ['case', ['<', ['get', 'band'], 0], 3.5, 10]],
          'circle-color': ['get', 'color'],
          'circle-opacity': ['case', ['<', ['get', 'band'], 0], 0.55, 0.95],
          'circle-stroke-width': 1,
          'circle-stroke-color': '#07090a'
        }
      });

      map.addLayer({
        id: 'segment-line', type: 'line', source: 'segments',
        layout: { visibility: 'none', 'line-cap': 'butt' },
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': ['get', 'color'],
          'line-opacity': ['case', ['<', ['get', 'band'], 0], 0.55, 0.95],
          'line-width': ['interpolate', ['linear'], ['zoom'],
            6, ['get', 'width'],
            9, ['*', ['get', 'width'], 1.5],
            12, ['*', ['get', 'width'], 2.6]]
        }
      });

      map.fitBounds([[-67.7, 17.6], [-64.9, 18.75]], { padding: 30, duration: 0 });

      // popups on coast segments
      map.on('click', 'segment-line', function (e) {
        var p = e.features[0].properties;
        new maplibregl.Popup({ offset: 12, maxWidth: '290px' })
          .setLngLat(e.lngLat).setHTML(segmentPopup(p, latest)).addTo(map);
      });
      map.on('mouseenter', 'segment-line', function () { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'segment-line', function () { map.getCanvas().style.cursor = ''; });
      map.on('click', 'segment-pt', function (e) {
        var p = e.features[0].properties;
        new maplibregl.Popup({ offset: 12, maxWidth: '290px' })
          .setLngLat(e.lngLat).setHTML(segmentPopup(p, latest)).addTo(map);
      });

      // hover readout on the biomass field
      map.on('mousemove', 'biomass-pt', function (e) {
        var v = e.features[0].properties.v;
        showTip('<b>' + fmt(v, 3) + ' kg per m&sup2;</b><span class="sub">wet Sargassum at the sea surface</span>',
                e.originalEvent.clientX, e.originalEvent.clientY);
      });
      map.on('mouseleave', 'biomass-pt', hideTip);

      setLayer('biomass');
    });

    // layer switcher
    var sw = document.getElementById('layerSwitch');
    sw.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      Array.prototype.forEach.call(sw.querySelectorAll('button'), function (x) {
        x.classList.toggle('active', x === b);
      });
      setLayer(b.dataset.layer);
    });

    document.getElementById('tracksToggle').addEventListener('change', function (e) {
      tracksOn = e.target.checked;
      if (map.getLayer('tracks-line')) {
        map.setLayoutProperty('tracks-line', 'visibility', tracksOn ? 'visible' : 'none');
      }
    });

    function setLayer(name) {
      currentLayer = name;
      if (!map.getLayer('biomass-pt')) return;
      var showBio = name === 'biomass';
      var segVis = showBio ? 'none' : 'visible';
      map.setLayoutProperty('biomass-pt', 'visibility', showBio ? 'visible' : 'none');
      map.setLayoutProperty('segment-casing', 'visibility', segVis);
      map.setLayoutProperty('segment-line', 'visibility', segVis);
      map.setLayoutProperty('segment-pt', 'visibility', segVis);
      if (!showBio) {
        map.getSource('segments').setData(name === 'h2s' ? gasData.gj : strandData.gj);
      }
      renderLegend(name, bioData, strandData, gasData, bio, latest);
      hideTip();
    }
  }

  function segmentPopup(p, latest) {
    var ex = typeof p.exposure === 'string' ? JSON.parse(p.exposure) : (p.exposure || {});
    var byDay = typeof p.tonnes_by_day === 'string' ? JSON.parse(p.tonnes_by_day) : (p.tonnes_by_day || []);
    var gas = typeof p.h2s_kg_per_day === 'string' ? JSON.parse(p.h2s_kg_per_day) : (p.h2s_kg_per_day || []);
    var tier = TIER[p.risk_tier] || TIER.minimal;
    var peakGas = gas.length ? Math.max.apply(null, gas) : 0;
    var src = p.source === 'learned'
      ? 'trained on trap measurements at this site'
      : 'physical model with global bias correction';
    return '<div class="pop">' +
      '<h4>' + p.name + '</h4>' +
      '<p class="meta">' + p.seg_id + '  ·  ' + (p.coast || '') + ' facing  ·  ' +
        fmt(p.length_m / 1000, 1) + ' km of shoreline</p>' +
      '<dl>' +
      '<dt>Outlook</dt><dd><b>' + (p._bandName || 'n/a') + '</b></dd>' +
      '<dt>Accumulation</dt><dd><b>' + fmt(p.kg_per_m_total, 1) + '</b> kg/m</dd>' +
      '<dt>Total mass</dt><dd>' + fmt(p.tonnes_total, 1) + ' t</dd>' +
      '<dt>Peak day</dt><dd>' + (byDay.length
          ? shortDay((latest.days || [])[byDay.indexOf(Math.max.apply(null, byDay))] || '') : 'n/a') + '</dd>' +
      '<dt>Peak H&#8322;S</dt><dd>' + fmt(peakGas, 1) + ' kg/day</dd>' +
      '<dt>At the wrack line</dt><dd>' + fmt(ex.ppm, 3) + ' ppm</dd>' +
      '</dl>' +
      '<div class="tag"><span class="dot" style="background:' + tier.color + '"></span>' +
        tier.label + ' emission tier. ' + (ex.label || '') + '</div>' +
      '<div class="tag" style="color:var(--muted)">Source: ' + src + '</div>' +
      '</div>';
  }

  /** One legend row: a colour swatch shaped like the mark it describes,
   *  a name, the numeric range it covers, and what it means on a beach. */
  function classRow(color, thin, label, range, blurb) {
    var row = el('div', 'key' + (thin ? ' key-thin' : ''));
    var sw = el('span', 'swatch');
    sw.style.background = color;
    if (thin) sw.classList.add('swatch-thin');
    row.appendChild(sw);
    var txt = el('span', 'key-text');
    txt.appendChild(el('b', null, label));
    if (range) txt.appendChild(el('span', 'key-range', range));
    if (blurb) txt.appendChild(el('span', 'key-blurb', blurb));
    row.appendChild(txt);
    return row;
  }

  function bandRange(classes, i) {
    var lo = i === 0 ? 0 : classes[i - 1].max;
    var hi = classes[i].max;
    if (hi === null || hi === undefined) return 'over ' + fmt(lo, 0) + ' kg/m';
    return fmt(lo, 0) + '\u2013' + fmt(hi, 0) + ' kg/m';
  }

  function renderLegend(name, bioData, strandData, gasData, bio, latest) {
    var box = document.getElementById('legend');
    var cap = document.getElementById('mapCaption');
    box.innerHTML = '';

    // ---------------------------------------------------------- biomass
    if (name === 'biomass') {
      var stack = el('div', 'stack');
      var bar = el('div', 'bar');
      bar.style.background = gradientCss(CIVIDIS);
      var ticks = el('div', 'ticks');
      ticks.innerHTML = '<span>' + fmt(bioData.lo, 3) + '</span><span>' +
                        fmt(bioData.hi, 2) + '</span>';
      box.appendChild(el('span', 'legend-title', 'Wet Sargassum, kg per m&sup2;'));
      stack.appendChild(bar);
      stack.appendChild(ticks);
      box.appendChild(stack);
      cap.innerHTML = 'Sargassum detected at the sea surface, from the USF and NOAA Alternative Floating ' +
        'Algae Index. Scale is logarithmic. Colours use <b>cividis</b>, a perceptually uniform ramp ' +
        'designed to read the same way for people with red green colour vision deficiency. ' +
        'Scene time ' + String(bio.time || '').slice(0, 10) + '.';
      return;
    }

    // ------------------------------------------------------------- gas
    if (name === 'h2s') {
      var t = (latest.h2s_risk_tiers) || { low: 0.5, moderate: 3.0, high: 12.0 };
      var gasRange = {
        minimal: 'under ' + fmt(t.low, 1),
        low: fmt(t.low, 1) + '\u2013' + fmt(t.moderate, 1),
        moderate: fmt(t.moderate, 1) + '\u2013' + fmt(t.high, 0),
        high: 'over ' + fmt(t.high, 0)
      };
      box.appendChild(el('span', 'legend-title',
        'Peak hydrogen sulfide, kg per day per km of shoreline'));
      var gkeys = el('div', 'keys keys-stacked');
      TIER_ORDER.forEach(function (k) {
        gkeys.appendChild(classRow(TIER[k].color, k === 'minimal',
          TIER[k].label, gasRange[k], TIER_BLURB[k]));
      });
      box.appendChild(gkeys);
      cap.innerHTML = 'Hydrogen sulfide given off as stranded weed rots, at its worst day on each ' +
        'stretch of coast. Release lags the stranding by about two days. Every tier carries a written ' +
        'label, so colour is never the only cue. Click a stretch for the estimated concentration at ' +
        'the wrack line and how it compares with health guideline levels.' +
        (gasData.nEmpty ? ' <b>' + gasData.nEmpty + ' of ' +
          (gasData.nEmpty + gasData.counts.reduce(function (a, b) { return a + b; }, 0)) +
          '</b> stretches are in the minimal tier for this run.' : '');
      return;
    }

    // -------------------------------------------------------- stranding
    var classes = latest.stranding_classes || [];
    box.appendChild(el('span', 'legend-title',
      'Predicted stranding over the next 5 days'));
    var keys = el('div', 'keys keys-stacked');
    classes.forEach(function (c, i) {
      keys.appendChild(classRow(SEVERITY[i], false, c.name, bandRange(classes, i), c.blurb));
    });
    keys.appendChild(classRow(NONE_COLOR, true, 'None predicted', '0 kg/m',
      'No weed expected to come ashore here in this run.'));
    box.appendChild(keys);

    var nTotal = strandData.nEmpty + strandData.counts.reduce(function (a, b) { return a + b; }, 0);
    cap.innerHTML = 'Wet mass predicted to come ashore on each ~5 km stretch of coast over the forecast ' +
      'window, in kilograms per metre of shoreline \u2014 the same unit the traps at La Parguera ' +
      'measure, so prediction and observation are directly comparable. The bands are indicative ' +
      'guidance for reading the map, not an official advisory scale. Peak value this run: <b>' +
      fmt(strandData.hi, 1) + ' kg/m</b>. <b>' + strandData.nEmpty + ' of ' + nTotal +
      '</b> stretches show nothing at all \u2014 note that this run cannot tell "no weed expected" ' +
      'apart from "no current or wind data covering this coast". Click any stretch for the daily breakdown.';
  }

  // ============================================================= CHARTS
  var W = 520, H = 232, M = { t: 30, r: 16, b: 26, l: 46 };

  /** Round ticks: step is 1, 2, 2.5 or 5 times a power of ten. */
  function ticksFor(maxValue, target) {
    if (!(maxValue > 0)) return { max: 1, step: 0.25, values: [0, 0.25, 0.5, 0.75, 1] };
    target = target || 4;
    var raw = maxValue / target;
    var e = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / e;
    var step = (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
    var max = Math.ceil(maxValue / step) * step;
    var values = [];
    for (var v = 0; v <= max + step / 1e6; v += step) values.push(Math.round(v / step) * step);
    return { max: max, step: step, values: values };
  }

  function axisFrame(svg, tk, plotW, plotH, unit) {
    var dec = tk.step >= 10 ? 0 : tk.step >= 1 ? 0 : tk.step >= 0.1 ? 1 : 2;
    tk.values.forEach(function (v, i) {
      var y = M.t + plotH - plotH * (v / tk.max);
      svg.appendChild(svgEl('line', {
        x1: M.l, x2: M.l + plotW, y1: y, y2: y,
        class: i === 0 ? 'axis-line' : 'grid-line'
      }));
      var tx = svgEl('text', { x: M.l - 8, y: y + 3.5, class: 'axis-text', 'text-anchor': 'end' });
      tx.textContent = fmt(v, dec);
      svg.appendChild(tx);
    });
    if (unit) {
      var u = svgEl('text', { x: M.l - 8, y: M.t - 12, class: 'axis-text', 'text-anchor': 'start' });
      u.textContent = unit;
      svg.appendChild(u);
    }
  }

  function chartBars(mount, labels, values, unit, color) {
    mount.innerHTML = '';
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img' });
    var plotW = W - M.l - M.r, plotH = H - M.t - M.b;
    var tk = ticksFor(Math.max.apply(null, values.concat([0])));
    var max = tk.max;
    axisFrame(svg, tk, plotW, plotH, unit);

    var n = values.length || 1;
    var slot = plotW / n, bw = Math.min(slot - 8, 46);
    values.forEach(function (v, i) {
      var h = Math.max(0, plotH * (v / max));
      var x = M.l + slot * i + (slot - bw) / 2;
      var y = M.t + plotH - h;
      svg.appendChild(svgEl('rect', {
        x: x, y: y, width: bw, height: h, rx: 4, ry: 4, fill: color, opacity: 0.92
      }));
      var lb = svgEl('text', {
        x: M.l + slot * i + slot / 2, y: M.t + plotH + 16,
        class: 'axis-text', 'text-anchor': 'middle'
      });
      lb.textContent = labels[i];
      svg.appendChild(lb);

      var hit = svgEl('rect', { x: M.l + slot * i, y: M.t, width: slot, height: plotH, class: 'hit' });
      hit.addEventListener('mousemove', function (e) {
        showTip('<b>' + fmt(v) + ' ' + unit + '</b><span class="sub">' + labels[i] + '</span>',
                e.clientX, e.clientY);
      });
      hit.addEventListener('mouseleave', hideTip);
      svg.appendChild(hit);
    });
    mount.appendChild(svg);
  }

  function chartLines(mount, labels, series, unit) {
    mount.innerHTML = '';
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img' });
    var plotW = W - M.l - M.r - 34, plotH = H - M.t - M.b;
    var all = series.reduce(function (a, s) { return a.concat(s.values); }, [0]);
    var tk = ticksFor(Math.max.apply(null, all));
    var max = tk.max;
    axisFrame(svg, tk, plotW, plotH, unit);

    var n = labels.length;
    var xAt = function (i) { return M.l + (n > 1 ? plotW * i / (n - 1) : plotW / 2); };
    var yAt = function (v) { return M.t + plotH - plotH * (v / max); };

    // x labels, thinned so they never collide
    var every = Math.max(1, Math.ceil(n / 7));
    var lastDrawn = -Infinity, minGap = 46;
    labels.forEach(function (l, i) {
      var forced = i === n - 1;
      if (i % every && !forced) return;
      if (xAt(i) - lastDrawn < minGap && !forced) return;
      if (forced && xAt(i) - lastDrawn < minGap && svg.lastElementChild) {
        svg.removeChild(svg.lastElementChild);   // drop the crowded neighbour
      }
      lastDrawn = xAt(i);
      var t = svgEl('text', { x: xAt(i), y: M.t + plotH + 16, class: 'axis-text', 'text-anchor': 'middle' });
      t.textContent = l;
      svg.appendChild(t);
    });

    series.forEach(function (s) {
      var d = s.values.map(function (v, i) { return (i ? 'L' : 'M') + xAt(i) + ' ' + yAt(v); }).join(' ');
      svg.appendChild(svgEl('path', {
        d: d, fill: 'none', stroke: s.color, 'stroke-width': 2,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      }));
      var last = s.values.length - 1;
      svg.appendChild(svgEl('circle', {
        cx: xAt(last), cy: yAt(s.values[last]), r: 4, fill: s.color,
        stroke: '#14181b', 'stroke-width': 2
      }));
      var lab = svgEl('text', {
        x: xAt(last) + 9, y: yAt(s.values[last]) + 4, class: 'series-label', fill: s.color
      });
      lab.textContent = s.name;
      svg.appendChild(lab);
    });

    var cross = svgEl('line', {
      x1: 0, x2: 0, y1: M.t, y2: M.t + plotH, class: 'grid-line', opacity: 0
    });
    svg.appendChild(cross);
    var hit = svgEl('rect', { x: M.l, y: M.t, width: plotW, height: plotH, class: 'hit' });
    hit.addEventListener('mousemove', function (e) {
      var r = svg.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width * W;
      var i = Math.round((px - M.l) / (plotW / Math.max(1, n - 1)));
      i = Math.max(0, Math.min(n - 1, i));
      cross.setAttribute('x1', xAt(i)); cross.setAttribute('x2', xAt(i));
      cross.setAttribute('opacity', 1);
      var rows = series.map(function (s) {
        return '<span style="color:' + s.color + '">&#9679;</span> ' + s.name + ' <b>' +
               fmt(s.values[i]) + '</b>';
      }).join('<br>');
      showTip('<span class="sub" style="margin:0 0 4px">' + labels[i] + ' &middot; ' + unit + '</span>' + rows,
              e.clientX, e.clientY);
    });
    hit.addEventListener('mouseleave', function () { cross.setAttribute('opacity', 0); hideTip(); });
    svg.appendChild(hit);
    mount.appendChild(svg);
  }

  function chartHBars(mount, rows, unit) {
    mount.innerHTML = '';
    // Wider viewBox than the small charts so that, stretched across the full
    // page width, the type renders at the same visual size as theirs.
    var WW = 1040;
    var rowH = 26, padT = 12, padB = 26, labelW = 250, valW = 76;
    var h = padT + padB + rows.length * rowH;
    var svg = svgEl('svg', { viewBox: '0 0 ' + WW + ' ' + h, role: 'img' });
    var plotW = WW - labelW - valW - 10;
    var tk = ticksFor(Math.max.apply(null, rows.map(function (r) { return r.value; }).concat([0])), 3);
    var max = tk.max;

    tk.values.forEach(function (v, i) {
      var x = labelW + plotW * (v / max);
      svg.appendChild(svgEl('line', {
        x1: x, x2: x, y1: padT, y2: padT + rows.length * rowH,
        class: i === 0 ? 'axis-line' : 'grid-line'
      }));
      var t = svgEl('text', {
        x: x, y: padT + rows.length * rowH + 15, class: 'axis-text', 'text-anchor': 'middle'
      });
      t.textContent = fmt(v, tk.step >= 1 ? 0 : 1);
      svg.appendChild(t);
    });

    rows.forEach(function (r, i) {
      var y = padT + i * rowH;
      var bw = Math.max(2, plotW * (r.value / max));
      var lb = svgEl('text', { x: labelW - 10, y: y + rowH / 2 + 4, class: 'value-text', 'text-anchor': 'end' });
      lb.textContent = r.label.length > 34 ? r.label.slice(0, 33) + '…' : r.label;
      svg.appendChild(lb);
      svg.appendChild(svgEl('rect', {
        x: labelW, y: y + 4, width: bw, height: rowH - 10, rx: 4, ry: 4,
        fill: r.color, opacity: 0.92
      }));
      var vt = svgEl('text', { x: labelW + bw + 8, y: y + rowH / 2 + 4, class: 'value-text' });
      vt.textContent = fmt(r.value, r.value >= 100 ? 0 : 1);
      svg.appendChild(vt);

      var hit = svgEl('rect', { x: 0, y: y, width: WW, height: rowH, class: 'hit' });
      hit.addEventListener('mousemove', function (e) {
        showTip('<b>' + r.label + '</b><span class="sub">' + fmt(r.value, 1) + ' ' + unit +
                (r.sub ? ' &middot; ' + r.sub : '') + '</span>', e.clientX, e.clientY);
      });
      hit.addEventListener('mouseleave', hideTip);
      svg.appendChild(hit);
    });
    mount.appendChild(svg);
  }

  function renderCharts(latest, segs) {
    var days = (latest.days || []).map(shortDay);
    var strandDaily = sumByDay(segs, 'tonnes_by_day');
    chartBars(document.getElementById('chartStranding'), days, strandDaily, 'tonnes', '#e35933');

    var gasDays = (latest.gas_days || latest.days || []).map(shortDay);
    var h2s = sumByDay(segs, 'h2s_kg_per_day').map(function (v) { return v / 1000; });
    var nh3 = sumByDay(segs, 'nh3_kg_per_day').map(function (v) { return v / 1000; });
    chartLines(document.getElementById('chartGas'), gasDays, [
      { name: 'H₂S', values: h2s, color: '#fac228' },
      { name: 'NH₃', values: nh3, color: '#a52c60' }
    ], 'tonnes per day');

    // One row per place: adjacent segments often share a landmark name, and
    // ten rows all reading "Luquillo" tells the reader nothing.
    var best = {};
    segs.features.forEach(function (f) {
      var n = f.properties.name;
      if (!best[n] || f.properties.kg_per_m_total > best[n].properties.kg_per_m_total) best[n] = f;
    });
    var top = Object.keys(best).map(function (k) { return best[k]; })
      .sort(function (a, b) { return b.properties.kg_per_m_total - a.properties.kg_per_m_total; })
      .slice(0, 10);
    // Bars are coloured by the same band as the map, not by their own length -
    // bar length already encodes magnitude, so a value-ramp would spend the
    // colour channel restating it. Sharing the bands ties chart to map instead.
    var classes = latest.stranding_classes || [];
    chartHBars(document.getElementById('chartTop'), top.map(function (f) {
      var p = f.properties;
      var b = strandBand(p.kg_per_m_total, classes);
      return {
        label: p.name,
        value: p.kg_per_m_total,
        color: b < 0 ? NONE_COLOR : SEVERITY[b],
        sub: (b < 0 ? 'None predicted' : classes[b].name) + ', worst stretch ' + p.seg_id
      };
    }), 'kg per metre');
  }

  /** A degraded run must announce itself. An empty map and a genuinely calm
   *  week look identical, and of the two only one is safe to act on. */
  function renderRunStatus(latest, segs) {
    var box = document.getElementById('runStatus');
    if (!box) return;
    var problems = [];

    var noWind = (latest.notes || []).some(function (n) {
      return /wind/i.test(n) && /disabled|no WRF/i.test(n);
    });
    if (noWind) {
      problems.push('The WRF wind forecast did not load, so windage is switched off and ' +
        'rafts drift on currents alone. Windage is the main mechanism that pushes ' +
        'Sargassum onto a beach, so this run <b>understates stranding</b>.');
    }

    var total = latest.predicted_stranding_tonnes;
    if (typeof total === 'number' && total <= 0) {
      var offshore = latest.offshore_wet_tonnes;
      problems.push('This run predicts <b>no stranding anywhere</b> over the next five days' +
        (offshore > 0 ? ', while detecting ' + fmt(offshore, 0) + ' tonnes of Sargassum offshore' : '') +
        '. Treat that as a gap in the inputs, not as an all clear.');
    }

    var covered = (latest.notes || []).filter(function (n) { return /held constant/i.test(n); });
    covered.forEach(function (n) { problems.push(n); });

    if (!problems.length) { box.hidden = true; return; }

    box.innerHTML = '<b>This forecast run is incomplete.</b>' +
      '<ul><li>' + problems.join('</li><li>') + '</li></ul>';
    box.hidden = false;
  }

  // =============================================================== BOOT
  function mapFail(err) {
    var m = document.getElementById('map');
    m.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
      'padding:24px;text-align:center;color:var(--muted);font-size:14px">' +
      'The map could not start. MapLibre is loaded from a CDN, so this usually means the browser ' +
      'could not reach unpkg.com. Everything else on this page still works.</div>';
    document.getElementById('legend').innerHTML = '';
    document.getElementById('mapCaption').textContent = '';
    if (window.console) console.warn('map failed:', err);
  }

  function fail(err) {
    var main = document.querySelector('main');
    var box = el('div', 'err',
      '<b>No forecast data found.</b><br>' +
      'This page reads <code>site/data/latest.json</code> and its companions, which the pipeline ' +
      'writes on every run. Run <code>python scripts/update.py</code> once, or open the page from a ' +
      'checkout that still has the <code>examples/</code> folder alongside it. ' +
      'Note that browsers block <code>fetch</code> on <code>file://</code> pages, so serve the folder ' +
      'with <code>python -m http.server</code> rather than double clicking the file.' +
      '<br><br><span style="color:var(--muted)">' + (err && err.message ? err.message : err) + '</span>');
    main.insertBefore(box, main.children[1]);
    document.getElementById('stamp').textContent = 'No data loaded';
  }

  Promise.all([
    loadJSON('latest.json'),
    loadJSON('forecast_segments.geojson'),
    loadJSON('biomass_field.json'),
    loadJSON('drift_tracks.json').catch(function () { return { tracks: [] }; })
  ]).then(function (res) {
    var latest = res[0], segs = res[1], bio = res[2], tracks = res[3];
    renderStamp(latest);
    renderStats(latest, segs);
    renderCharts(latest, segs);
    try {
      if (typeof maplibregl === 'undefined') throw new Error('maplibre-gl not loaded');
      buildMap(bio, segs, tracks, latest);
    } catch (err) {
      mapFail(err);
    }

    var notes = (latest.notes || []);
    if (latest.model && latest.model.n_train) {
      notes = notes.concat(['Learned layer trained on ' + latest.model.n_train +
        ' weekly trap measurements, hold out mean absolute error ' +
        fmt(latest.model.mae_kg_per_m, 1) + ' kg per metre.']);
    }
    if (notes.length) {
      document.getElementById('notes').textContent = 'Run notes: ' + notes.join('. ') + '.';
    }
    renderRunStatus(latest, segs);
  }).catch(fail);

})();
