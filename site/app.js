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
  // a dark basemap. Perceptually uniform and colourblind safe. Offshore only.
  var CIVIDIS = ['#21395f', '#39486b', '#575d6d', '#707173',
                 '#8a8779', '#a69d75', '#c4b56c', '#ffea46'];

  /* Severity ramp for the two prediction layers.
   *
   * This follows air-quality convention rather than a perceptual ramp,
   * because the reader's prior for "amber means caution, red means hazard" is
   * far stronger than any ramp we could teach them in a legend. Hue does the
   * work; every band also carries a written label, so colour is never the only
   * cue. The top band is deliberately the loudest thing on the map.
   */
  var SEVERITY = [
    '#f5b52a',   // amber        - lowest valued band
    '#f07818',   // deep orange
    '#e01e37',   // crimson
    '#ff2e63'    // hot crimson  - highest band, health-alert register
  ];

  /* "Nothing expected here" is a state, not the bottom of the scale.
   *
   * It used to be drawn in a dark slate grey that vanished into the basemap,
   * which made a working layer indistinguishable from an unrendered one. A
   * low-saturation teal reads as "measured, and clean": clearly a drawn
   * feature, clearly not part of the warm hazard ramp. Drawn at reduced
   * opacity so it still recedes behind anything that matters.
   */
  var NONE_COLOR = '#3fa596';
  var NONE_OPACITY = 0.62;
  var VALUE_OPACITY = 0.96;

  var TIER = {
    minimal:  { color: NONE_COLOR,  label: 'Minimal' },
    low:      { color: SEVERITY[0], label: 'Low' },
    moderate: { color: SEVERITY[1], label: 'Moderate' },
    high:     { color: SEVERITY[3], label: 'High' }
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

  // Baseline stroke weights, in CSS pixels at the reference zoom. A ~5 km
  // stretch of coast is only a few pixels long at the island-wide zoom the map
  // opens at, so a hairline washes out to nothing under anti-aliasing on any
  // display. These are the widths at zoom 7.5; see the interpolation below.
  var W_EMPTY = 3.2;
  var W_VALUE = 7.6;

  var PLAY_MS = 780;

  /* How many hours of the drift horizon may run on held-constant wind before
   * the run is called degraded. The CariCOOS 2 km WRF nest is a ~108 h
   * product against a 120 h horizon, so a gap of roughly half a day is the
   * normal, permanent state of a healthy run and must not raise an alarm. */
  var WIND_GAP_ALERT_H = 24;

  /* The basemap is drawn from GeoJSON shipped in this repository rather than
   * fetched as raster tiles from a third party. Two reasons. Raster tiles are
   * baked at fixed zoom levels, so the map goes soft everywhere between them,
   * which is most of the time on a map you pan and pinch. And a hosted tile
   * service is a dependency someone else controls: the previous CDN started
   * returning tiles stamped API KEY REQUIRED across the middle of the island.
   * Vector geometry is resolution independent and cannot be revoked.
   *
   * Geometry comes from GSHHS (Wessel & Smith) via scripts/build_basemap.py.
   */
  // Ocean sits a shade below the page background so the map reads as a
  // recessed panel rather than a floating rectangle of the same colour.
  var OCEAN = '#080b0d';
  var LAND = '#171c20';
  var LAND_EDGE = '#333c43';

  /* No `glyphs` entry, and so no symbol layers: serving font PBFs would mean
   * reintroducing exactly the hosted dependency this change removes. Place
   * names are HTML markers instead, which also renders them at the device's
   * own text resolution rather than from a texture atlas. */
  var BASEMAP = {
    version: 8,
    sources: {},
    layers: [
      { id: 'ocean', type: 'background', paint: { 'background-color': OCEAN } }
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
  /** GeoJSON properties come back stringified from MapLibre query results. */
  function arr(v) {
    if (Array.isArray(v)) return v;
    if (typeof v === 'string') { try { return JSON.parse(v); } catch (e) { return []; } }
    return [];
  }
  function obj(v) {
    if (typeof v === 'string') { try { return JSON.parse(v); } catch (e) { return {}; } }
    return v || {};
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
    var top = y - r.height - 12;
    if (top < 8) top = y + 20;             // flip below the cursor near the top
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
    var gasDaily = sumByDay(segs, 'h2s_kg_per_day');
    var peakDay = gasDaily.indexOf(Math.max.apply(null, gasDaily.concat([0])));

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
      arr(f.properties[key]).forEach(function (v, i) {
        out[i] = (out[i] || 0) + (v || 0);
      });
    });
    return out;
  }

  // ====================================================== CLASSIFICATION
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

  /** H2S tier index (0 minimal .. 3 high) from kg/day/km against the
   *  thresholds the pipeline serves. Computed rather than read off the stored
   *  `risk_tier`, because the scrubber needs a tier for any single day and the
   *  stored one only describes the peak day. */
  function gasBand(v, t) {
    if (!(v > 0) || v < t.low) return 0;
    if (v < t.moderate) return 1;
    if (v < t.high) return 2;
    return 3;
  }

  /** The value a segment shows for the current layer and the current day.
   *  `day < 0` means the whole forecast window. */
  function segValue(p, mode, day) {
    var len = (p.length_m || 5000) / 1000;    // km
    if (mode === 'h2s') {
      if (day < 0) return p.h2s_peak_kg_day_km || 0;
      return (arr(p.h2s_kg_per_day)[day] || 0) / len;
    }
    if (day < 0) return p.kg_per_m_total || 0;
    return (arr(p.tonnes_by_day)[day] || 0) / len;   // t/km === kg/m
  }

  /** Segment features carrying a band index, a colour and a line width.
   *
   *  Magnitude is carried by colour alone. Width is binary - a valued segment
   *  is a thick ribbon, an empty one a thinner but still clearly drawn line -
   *  so it marks presence, not size, and the two never compete.
   */
  function segmentGeoJSON(segs, mode, latest, day) {
    var classes = latest.stranding_classes || [];
    var tiers = latest.h2s_risk_tiers || { low: 0.5, moderate: 3.0, high: 12.0 };
    var isGas = mode === 'h2s';
    var counts = [0, 0, 0, 0], nEmpty = 0, hi = 0;

    var feats = segs.features.map(function (f) {
      var p = f.properties;
      var v = segValue(p, mode, day);
      if (v > hi) hi = v;

      var band, color, label;
      if (isGas) {
        var gi = gasBand(v, tiers);
        color = TIER[TIER_ORDER[gi]].color;
        label = TIER[TIER_ORDER[gi]].label + ' emission';
        if (gi === 0) nEmpty++; else counts[gi]++;
        band = gi === 0 ? -1 : gi;
      } else {
        var sb = strandBand(v, classes);
        color = sb < 0 ? NONE_COLOR : SEVERITY[sb];
        label = sb < 0 ? 'None predicted' : ((classes[sb] || {}).name || 'n/a');
        if (sb < 0) nEmpty++; else counts[sb]++;
        band = sb;
      }

      return {
        type: 'Feature',
        geometry: f.geometry,
        properties: {
          // Scalars only: MapLibre stringifies anything nested, and the
          // tooltip has to be free of parsing on the hover path.
          seg_id: p.seg_id,
          name: p.name,
          coast: p.coast,
          length_m: p.length_m,
          color: color,
          band: band,
          width: band < 0 ? W_EMPTY : W_VALUE,
          _v: v,
          _label: label,
          _unit: isGas ? 'kg/day per km' : 'kg per metre',
          _idx: f.properties.seg_id
        }
      };
    });

    return {
      gj: { type: 'FeatureCollection', features: feats },
      hi: hi, counts: counts, nEmpty: nEmpty
    };
  }

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

  // ================================================================ MAP
  var App = {
    latest: null, segs: null,
    map: null, layer: 'biomass', day: -1,
    charts: {}, segIndex: {},
    // Reassigned during boot. No-ops until then so a click that lands early
    // (or a run with no gas days, which never builds a timeline) cannot throw.
    setLayer: function () {},
    setDay: function () {},
    refreshMap: function () {},
    redraw: null
  };

  /* ------------------------------------------------------------ basemap
   * Land is added as vector polygons under everything else. The layers go on
   * inside `load` rather than in the style object because the GeoJSON arrives
   * asynchronously and the map should not wait on it: if the shoreline file
   * is slow or missing, the forecast still draws over open ocean.
   */
  function addBasemapLayers(map) {
    if (!App.basemapLand) return;

    map.addSource('land', {
      type: 'geojson',
      data: App.basemapLand,
      attribution: 'Shoreline: <a href="https://www.soest.hawaii.edu/pwessel/gshhg/">GSHHG</a>'
    });

    // A soft halo just outside the coast. Shallow water is genuinely brighter
    // than deep water, and the gradient gives the island an edge to sit on
    // instead of a hard cut between two flat greys.
    map.addLayer({
      id: 'land-halo', type: 'line', source: 'land',
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': '#123038',
        // Eased off at the wide zooms: with the whole Caribbean in frame the
        // halo is being drawn around several hundred islands at once, and at
        // full strength that reads as haze rather than as shallow water.
        'line-opacity': ['interpolate', ['linear'], ['zoom'],
          5, 0.22, 7, 0.4, 9, 0.55, 12, 0.6],
        'line-blur': ['interpolate', ['linear'], ['zoom'], 5, 6, 9, 14, 12, 26],
        'line-width': ['interpolate', ['linear'], ['zoom'], 5, 7, 9, 16, 12, 30]
      }
    });
    map.addLayer({
      id: 'land-fill', type: 'fill', source: 'land',
      paint: { 'fill-color': LAND }
    });
    // The waterline itself. Hairline-thin, but it is vector, so it stays a
    // hairline at zoom 12 instead of turning into a four-pixel smear.
    map.addLayer({
      id: 'land-outline', type: 'line', source: 'land',
      layout: { 'line-join': 'round' },
      paint: {
        'line-color': LAND_EDGE,
        'line-width': ['interpolate', ['linear'], ['zoom'], 5, 0.5, 9, 0.9, 12, 1.3]
      }
    });
  }

  /* Place names as DOM markers. They fade in only once the view is tight
   * enough for them not to collide, and they never capture pointer events, so
   * they cannot block a click on the shoreline segment underneath. */
  function addPlaceLabels(map, places) {
    var feats = (places && places.features) || [];
    if (!feats.length || typeof maplibregl.Marker !== 'function') return;

    var markers = feats.map(function (f) {
      var node = el('div', 'map-place');
      node.textContent = f.properties.name;
      return new maplibregl.Marker({ element: node, anchor: 'center' })
        .setLngLat(f.geometry.coordinates)
        .addTo(map);
    });

    function sync() {
      var on = map.getZoom() >= 8.4;
      markers.forEach(function (m) {
        m.getElement().classList.toggle('on', on);
      });
    }
    map.on('zoom', sync);
    sync();
  }

  function buildMap(bio, segs, tracks, latest) {
    var map = new maplibregl.Map({
      container: 'map',
      style: BASEMAP,
      center: [-66.4, 18.15],
      zoom: 7.4,
      minZoom: 5,
      maxZoom: 12,
      attributionControl: { compact: true }
    });
    App.map = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 90, unit: 'metric' }), 'bottom-left');

    var bioData = biomassGeoJSON(bio);
    var current = segmentGeoJSON(segs, 'stranding', latest, -1);

    map.on('load', function () {
      addBasemapLayers(map);
      addPlaceLabels(map, App.basemapPlaces);

      map.addSource('biomass', { type: 'geojson', data: bioData.gj });
      map.addSource('segments', { type: 'geojson', data: current.gj });
      map.addSource('tracks', { type: 'geojson', data: tracksGeoJSON(tracks) });

      /* The 103 segments used to stand in for a coast outline, drawn with a
       * heavy blurred black stroke to lift the island off the water. The
       * basemap now carries a real shoreline with its own edge and halo, so
       * that stand-in has been removed: two coastlines a kilometre apart is
       * worse than one. */

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

      // A dark casing under each ribbon keeps it legible where it crosses the
      // coast outline and stops adjacent segments bleeding into one another.
      map.addLayer({
        id: 'segment-casing', type: 'line', source: 'segments',
        layout: { visibility: 'none', 'line-cap': 'butt' },
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': '#050708',
          'line-opacity': 0.9,
          'line-width': ['interpolate', ['linear'], ['zoom'],
            5, ['+', ['*', ['get', 'width'], 0.85], 3],
            7.5, ['+', ['get', 'width'], 3.2],
            10, ['+', ['*', ['get', 'width'], 1.8], 3.6],
            12, ['+', ['*', ['get', 'width'], 2.8], 4]]
        }
      });

      // Fallback for data written by a pipeline older than the LineString
      // change: those files carry a centre Point per segment, which a line
      // layer would silently draw as nothing.
      map.addLayer({
        id: 'segment-pt', type: 'circle', source: 'segments',
        layout: { visibility: 'none' },
        filter: ['==', ['geometry-type'], 'Point'],
        paint: {
          'circle-radius': ['interpolate', ['linear'], ['zoom'],
            6, ['case', ['<', ['get', 'band'], 0], 3.4, 6.5],
            11, ['case', ['<', ['get', 'band'], 0], 5, 12]],
          'circle-color': ['get', 'color'],
          'circle-opacity': ['case', ['<', ['get', 'band'], 0], NONE_OPACITY, VALUE_OPACITY],
          'circle-stroke-width': 1.2,
          'circle-stroke-color': '#050708'
        }
      });

      map.addLayer({
        id: 'segment-line', type: 'line', source: 'segments',
        layout: { visibility: 'none', 'line-cap': 'butt' },
        filter: ['==', ['geometry-type'], 'LineString'],
        paint: {
          'line-color': ['get', 'color'],
          'line-opacity': ['case', ['<', ['get', 'band'], 0], NONE_OPACITY, VALUE_OPACITY],
          'line-width': ['interpolate', ['linear'], ['zoom'],
            5, ['*', ['get', 'width'], 0.85],
            7.5, ['get', 'width'],
            10, ['*', ['get', 'width'], 1.8],
            12, ['*', ['get', 'width'], 2.8]]
        }
      });

      map.fitBounds([[-67.7, 17.6], [-64.9, 18.75]], { padding: 30, duration: 0 });

      // ------------------------------------------------ segment interaction
      // Hover follows the cursor; tap on a touch device opens the full popup.
      ['segment-line', 'segment-pt'].forEach(function (id) {
        map.on('mousemove', id, function (e) {
          var p = e.features[0].properties;
          showTip(segmentTip(p), e.originalEvent.clientX, e.originalEvent.clientY);
          map.getCanvas().style.cursor = 'pointer';
        });
        map.on('mouseleave', id, function () {
          hideTip();
          map.getCanvas().style.cursor = '';
        });
        map.on('click', id, function (e) {
          hideTip();
          var full = App.segIndex[e.features[0].properties.seg_id];
          if (!full) return;
          new maplibregl.Popup({ offset: 14, maxWidth: '300px' })
            .setLngLat(e.lngLat).setHTML(segmentPopup(full, latest)).addTo(map);
        });
      });

      // hover readout on the biomass field
      map.on('mousemove', 'biomass-pt', function (e) {
        var v = e.features[0].properties.v;
        showTip('<b>' + fmt(v, 3) + ' kg per m&sup2;</b>' +
                '<span class="sub">wet Sargassum at the sea surface</span>',
                e.originalEvent.clientX, e.originalEvent.clientY);
      });
      map.on('mouseleave', 'biomass-pt', hideTip);

      App.setLayer('biomass');
    });

    // ------------------------------------------------------ layer switcher
    var sw = document.getElementById('layerSwitch');
    sw.addEventListener('click', function (e) {
      var b = e.target.closest('button');
      if (!b) return;
      Array.prototype.forEach.call(sw.querySelectorAll('button'), function (x) {
        x.classList.toggle('active', x === b);
      });
      App.setLayer(b.dataset.layer);
    });

    document.getElementById('tracksToggle').addEventListener('change', function (e) {
      if (map.getLayer('tracks-line')) {
        map.setLayoutProperty('tracks-line', 'visibility', e.target.checked ? 'visible' : 'none');
      }
    });

    App.redraw = function () {
      if (!map.getSource || !map.getSource('segments')) return null;
      var data = segmentGeoJSON(segs, App.layer === 'h2s' ? 'h2s' : 'stranding',
                                latest, App.day);
      map.getSource('segments').setData(data.gj);
      return data;
    };
  }

  /** Cursor-pinned readout: where, how much, and what that means. */
  function segmentTip(p) {
    var v = Number(p._v) || 0;
    var strong = Number(p.band) >= 0;
    return '<b>' + p.name + '</b>' +
      '<span class="sub">' + p.seg_id +
        (p.coast ? ' &middot; ' + p.coast + ' facing' : '') + '</span>' +
      '<div style="margin-top:5px;font-size:13px">' +
        '<b>' + fmt(v, v >= 100 ? 0 : v >= 10 ? 1 : 2) + '</b> ' + p._unit +
      '</div>' +
      '<div style="margin-top:3px;font-size:11.5px;color:' +
        (strong ? p.color : 'var(--muted)') + '">' + p._label + '</div>';
  }

  function segmentPopup(f, latest) {
    var p = f.properties;
    var ex = obj(p.exposure);
    var byDay = arr(p.tonnes_by_day);
    var gas = arr(p.h2s_kg_per_day);
    var classes = latest.stranding_classes || [];
    var tiers = latest.h2s_risk_tiers || { low: 0.5, moderate: 3.0, high: 12.0 };
    var gi = gasBand(p.h2s_peak_kg_day_km || 0, tiers);
    var tier = TIER[TIER_ORDER[gi]];
    var sb = strandBand(p.kg_per_m_total || 0, classes);
    var peakGas = gas.length ? Math.max.apply(null, gas) : 0;
    var src = p.source === 'learned'
      ? 'trained on trap measurements at this site'
      : 'physical model with global bias correction';
    return '<div class="pop">' +
      '<h4>' + p.name + '</h4>' +
      '<p class="meta">' + p.seg_id + '  ·  ' + (p.coast || '') + ' facing  ·  ' +
        fmt(p.length_m / 1000, 1) + ' km of shoreline</p>' +
      '<dl>' +
      '<dt>Outlook</dt><dd><b>' + (sb < 0 ? 'None predicted'
          : ((classes[sb] || {}).name || 'n/a')) + '</b></dd>' +
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

  // ============================================================== LEGEND
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
    return fmt(lo, 0) + '–' + fmt(hi, 0) + ' kg/m';
  }

  function dayLabel(latest, day, which) {
    var list = which === 'gas' ? (latest.gas_days || latest.days || []) : (latest.days || []);
    return list[day] ? shortDay(list[day]) : null;
  }

  function renderLegend(name, bioData, segData, bio, latest) {
    var box = document.getElementById('legend');
    var cap = document.getElementById('mapCaption');
    box.innerHTML = '';
    var day = App.day;
    var gasDay = dayLabel(latest, day, 'gas');

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
        'This layer is a single satellite scene, so the timeline below does not move it. ' +
        'Scene time ' + String(bio.time || '').slice(0, 10) + '.';
      return;
    }

    // ------------------------------------------------------------- gas
    if (name === 'h2s') {
      var t = (latest.h2s_risk_tiers) || { low: 0.5, moderate: 3.0, high: 12.0 };
      var gasRange = {
        minimal: 'under ' + fmt(t.low, 1),
        low: fmt(t.low, 1) + '–' + fmt(t.moderate, 1),
        moderate: fmt(t.moderate, 1) + '–' + fmt(t.high, 0),
        high: 'over ' + fmt(t.high, 0)
      };
      box.appendChild(el('span', 'legend-title',
        (day < 0 ? 'Peak hydrogen sulfide over the whole window'
                 : 'Hydrogen sulfide on ' + (gasDay || 'the selected day')) +
        ', kg per day per km of shoreline'));
      var gkeys = el('div', 'keys keys-stacked');
      TIER_ORDER.forEach(function (k) {
        gkeys.appendChild(classRow(TIER[k].color, k === 'minimal',
          TIER[k].label, gasRange[k], TIER_BLURB[k]));
      });
      box.appendChild(gkeys);
      var nTot = segData.nEmpty + segData.counts.reduce(function (a, b) { return a + b; }, 0);
      cap.innerHTML = 'Hydrogen sulfide given off as stranded weed rots' +
        (day < 0 ? ', at its worst day on each stretch of coast'
                 : ', on ' + (gasDay || 'the selected day')) +
        '. Release lags the stranding by about two days — scrub the timeline and you can watch ' +
        'the mass land first and the gas follow. Every tier carries a written label, so colour is ' +
        'never the only cue. Click a stretch for the estimated concentration at the wrack line and ' +
        'how it compares with health guideline levels.' +
        (segData.nEmpty ? ' <b>' + segData.nEmpty + ' of ' + nTot +
          '</b> stretches are in the minimal tier here.' : '');
      return;
    }

    // -------------------------------------------------------- stranding
    var classes = latest.stranding_classes || [];
    var sDay = dayLabel(latest, day, 'strand');
    var beyond = day >= 0 && !sDay;
    box.appendChild(el('span', 'legend-title',
      day < 0 ? 'Predicted stranding over the next ' + ((latest.days || []).length || 5) + ' days'
              : (beyond ? 'Stranding window has ended by ' + (gasDay || 'this day')
                        : 'Stranding on ' + sDay + ', kg per metre')));
    var keys = el('div', 'keys keys-stacked');
    classes.forEach(function (c, i) {
      keys.appendChild(classRow(SEVERITY[i], false, c.name, bandRange(classes, i), c.blurb));
    });
    keys.appendChild(classRow(NONE_COLOR, true, 'None predicted', '0 kg/m',
      'Nothing expected ashore here. The layer is drawn and this stretch is clean.'));
    box.appendChild(keys);

    var nTotal = segData.nEmpty + segData.counts.reduce(function (a, b) { return a + b; }, 0);
    cap.innerHTML = 'Wet mass predicted to come ashore on each ~5 km stretch of coast' +
      (day < 0 ? ' over the whole forecast window'
               : (beyond ? ' — the ' + ((latest.days || []).length || 5) +
                           ' day drift window has already closed by this date, so nothing is added'
                         : ' on ' + sDay)) +
      ', in kilograms per metre of shoreline — the same unit the traps at La Parguera ' +
      'measure, so prediction and observation are directly comparable. The bands are indicative ' +
      'guidance for reading the map, not an official advisory scale. Peak value shown: <b>' +
      fmt(segData.hi, 1) + ' kg/m</b>. <b>' + segData.nEmpty + ' of ' + nTotal +
      '</b> stretches show nothing — note that this run cannot tell "no weed expected" ' +
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
    var dec = tk.step >= 1 ? 0 : tk.step >= 0.1 ? 1 : 2;
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

  /** Returns { setDay(i) } so the timeline can move a cursor through it. */
  function chartBars(mount, labels, values, unit, color) {
    mount.innerHTML = '';
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img' });
    var plotW = W - M.l - M.r, plotH = H - M.t - M.b;
    var tk = ticksFor(Math.max.apply(null, values.concat([0])));
    var max = tk.max;
    axisFrame(svg, tk, plotW, plotH, unit);

    var n = values.length || 1;
    var slot = plotW / n, bw = Math.min(slot - 8, 46);
    var rects = [];
    values.forEach(function (v, i) {
      var h = Math.max(0, plotH * (v / max));
      var x = M.l + slot * i + (slot - bw) / 2;
      var y = M.t + plotH - h;
      var rect = svgEl('rect', {
        x: x, y: y, width: bw, height: h, rx: 4, ry: 4, fill: color, opacity: 0.92
      });
      rects.push(rect);
      svg.appendChild(rect);
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
      hit.addEventListener('click', function () { App.setDay(i); });
      svg.appendChild(hit);
    });
    mount.appendChild(svg);

    return {
      setDay: function (i) {
        rects.forEach(function (r, j) { r.classList.toggle('bar-dim', i >= 0 && j !== i); });
      }
    };
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

    // Day cursor sits under the series so it never obscures a line.
    var dayLine = svgEl('line', {
      x1: 0, x2: 0, y1: M.t - 6, y2: M.t + plotH, class: 'day-cursor', opacity: 0
    });
    svg.appendChild(dayLine);
    var dayDots = series.map(function (s) {
      var c = svgEl('circle', { r: 4.5, fill: s.color, stroke: '#0c0f11',
                                'stroke-width': 2, opacity: 0 });
      svg.appendChild(c);
      return c;
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
    function indexAt(clientX) {
      var r = svg.getBoundingClientRect();
      var px = (clientX - r.left) / r.width * W;
      var i = Math.round((px - M.l) / (plotW / Math.max(1, n - 1)));
      return Math.max(0, Math.min(n - 1, i));
    }
    hit.addEventListener('mousemove', function (e) {
      var i = indexAt(e.clientX);
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
    hit.addEventListener('click', function (e) { App.setDay(indexAt(e.clientX)); });
    svg.appendChild(hit);
    mount.appendChild(svg);

    return {
      setDay: function (i) {
        var on = i >= 0 && i < n;
        dayLine.setAttribute('opacity', on ? 1 : 0);
        if (on) { dayLine.setAttribute('x1', xAt(i)); dayLine.setAttribute('x2', xAt(i)); }
        dayDots.forEach(function (c, k) {
          c.setAttribute('opacity', on ? 1 : 0);
          if (on) { c.setAttribute('cx', xAt(i)); c.setAttribute('cy', yAt(series[k].values[i])); }
        });
      }
    };
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
    App.charts.stranding = chartBars(document.getElementById('chartStranding'),
                                     days, strandDaily, 'tonnes', SEVERITY[1]);

    var gasDays = (latest.gas_days || latest.days || []).map(shortDay);
    var h2s = sumByDay(segs, 'h2s_kg_per_day').map(function (v) { return v / 1000; });
    var nh3 = sumByDay(segs, 'nh3_kg_per_day').map(function (v) { return v / 1000; });
    App.charts.gas = chartLines(document.getElementById('chartGas'), gasDays, [
      { name: 'H₂S', values: h2s, color: SEVERITY[3] },
      { name: 'NH₃', values: nh3, color: '#7f9cf5' }
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

    return { strandDaily: strandDaily, h2s: h2s, nh3: nh3 };
  }

  // =========================================================== TIMELINE
  /* One clock for the map and both charts.
   *
   * The stranding series runs over the 5 day drift window; the gas series runs
   * ~17 days, because weed that lands on day 4 is still outgassing well after
   * the drift horizon closes. Indexing both off the same day means scrubbing
   * makes the ~48 h lag between mass landing and gas peaking visible instead
   * of something the reader has to infer from two charts side by side.
   */
  function initTimeline(latest, totals) {
    var wrap = document.getElementById('timeline');
    var range = document.getElementById('tlRange');
    var play = document.getElementById('tlPlay');
    var totalBtn = document.getElementById('tlTotal');
    var scale = document.getElementById('tlScale');
    var read = document.getElementById('tlRead');
    if (!wrap || !range) return;

    var gasDays = latest.gas_days || latest.days || [];
    var nDays = gasDays.length;
    if (!nDays) return;

    var strandDaily = totals.strandDaily || [];
    var h2sDaily = totals.h2s || [];
    var timer = null;

    range.min = 0;
    range.max = String(nDays - 1);
    range.value = '0';

    scale.innerHTML = '';
    var marks = nDays >= 5 ? [0, Math.floor((nDays - 1) / 2), nDays - 1]
              : nDays > 1 ? [0, nDays - 1] : [0];
    marks.forEach(function (i) {
      scale.appendChild(el('span', null, shortDay(gasDays[i])));
    });

    // Where the two curves peak, so the lag can be stated as a fact.
    var pStrand = strandDaily.indexOf(Math.max.apply(null, strandDaily.concat([0])));
    var pGas = h2sDaily.indexOf(Math.max.apply(null, h2sDaily.concat([0])));

    function readout() {
      var d = App.day;
      if (d < 0) {
        var lag = (pGas >= 0 && pStrand >= 0 && Math.max.apply(null, h2sDaily.concat([0])) > 0)
          ? '<span class="tl-lag">mass peaks ' + shortDay(gasDays[pStrand]) +
            ', gas peaks ' + shortDay(gasDays[pGas]) + '</span>'
          : 'drag to step through the forecast day by day';
        read.innerHTML = '<b>Whole window</b><span class="tl-sep">|</span>' + lag;
        return;
      }
      var inWindow = d < (latest.days || []).length;
      // Island-wide gas is often a fraction of a tonne, and "0.00 t" reads as
      // "nothing happened" when it is really "a few hundred kilos".
      var g = h2sDaily[d] || 0;
      var gasTxt = g >= 1 ? fmt(g, 1) + ' t' : fmt(g * 1000, g > 0 && g < 0.01 ? 2 : 0) + ' kg';
      read.innerHTML =
        '<b>' + shortDay(gasDays[d]) + '</b><span class="tl-sep">|</span>' +
        'ashore ' + (inWindow ? '<b>' + fmt(strandDaily[d] || 0, 0) + ' t</b>'
                              : '<b>window closed</b>') +
        '<span class="tl-sep">|</span>H₂S <b>' + gasTxt + '</b>';
    }

    /** Single entry point: everything that depends on the day goes through it. */
    App.setDay = function (d) {
      App.day = (d === null || d === undefined || d < 0) ? -1 : Math.min(d, nDays - 1);
      totalBtn.classList.toggle('is-on', App.day < 0);
      if (App.day >= 0) range.value = String(App.day);
      if (App.charts.stranding) App.charts.stranding.setDay(App.day);
      if (App.charts.gas) App.charts.gas.setDay(App.day);
      readout();
      App.refreshMap();
    };

    function stop() {
      if (timer) { clearInterval(timer); timer = null; }
      play.classList.remove('is-playing');
      play.setAttribute('aria-label', 'Play the forecast');
    }
    function start() {
      if (timer) return;
      if (App.day < 0) App.setDay(0);
      play.classList.add('is-playing');
      play.setAttribute('aria-label', 'Pause the forecast');
      timer = setInterval(function () {
        App.setDay(App.day >= nDays - 1 ? 0 : App.day + 1);
      }, PLAY_MS);
    }

    play.addEventListener('click', function () { timer ? stop() : start(); });
    totalBtn.addEventListener('click', function () { stop(); App.setDay(-1); });
    range.addEventListener('input', function () { stop(); App.setDay(parseInt(range.value, 10)); });

    wrap.hidden = false;
    App.setDay(-1);
  }

  // ============================================================== ALERT
  /* A degraded run must announce itself, immediately above the thing it
   * degrades. An empty map and a genuinely calm week look identical, and of
   * the two only one is safe to act on. */
  function renderAlert(latest) {
    var box = document.getElementById('runStatus');
    if (!box) return;
    var notes = latest.notes || [];
    var st = latest.status || {};
    var wind = st.wind;

    // Prefer the structured flag the pipeline now writes; fall back to the
    // prose notes so the page still warns on data from an older run.
    var noWind = wind && typeof wind.ok === 'boolean'
      ? !wind.ok
      : notes.some(function (n) {
          return /wind/i.test(n) && /disabled|no WRF|unavailable/i.test(n);
        });

    var extra = [];

    var total = latest.predicted_stranding_tonnes;
    if (typeof total === 'number' && total <= 0) {
      var offshore = latest.offshore_wet_tonnes;
      extra.push('This run predicts <b>no stranding anywhere</b> over the next ' +
        ((latest.days || []).length || 5) + ' days' +
        (offshore > 0 ? ', while detecting ' + fmt(offshore, 0) +
                        ' tonnes of Sargassum offshore' : '') +
        '. Treat that as a gap in the inputs, not as an all clear.');
    }

    /* The 2 km WRF nest is a ~4.5 day run and the drift horizon is 5 days, so
     * the wind record is a few hours short on *every* single run. Treating
     * that as a degraded forecast fired the banner permanently, which is the
     * fastest way to teach someone to ignore a warning that will one day
     * matter. A short tail is a known property of the model and is already
     * described in the methodology; only a materially uncovered horizon is
     * worth interrupting for. */
    var covered = null, horizon = null;
    if (wind && typeof wind.covered_hours === 'number' &&
        typeof wind.horizon_hours === 'number') {
      covered = wind.covered_hours;
      horizon = wind.horizon_hours;
    } else {
      // Older runs carry the numbers only in prose. Recover them so the same
      // threshold applies to archived data instead of it warning either
      // always or never.
      notes.some(function (n) {
        var m = /runs\s+([\d.]+)\s*h of the\s+([\d.]+)\s*h horizon/i.exec(n);
        if (m) { covered = parseFloat(m[1]); horizon = parseFloat(m[2]); }
        return !!m;
      });
    }
    if (covered !== null && horizon !== null &&
        horizon - covered > WIND_GAP_ALERT_H) {
      extra.push('Wind forecast covers only ' + Math.round(covered) +
        ' h of the ' + Math.round(horizon) + ' h horizon, so the last ' +
        Math.round(horizon - covered) + ' h of drift run on <b>held constant ' +
        'wind</b> and should be read as persistence, not forecast.');
    }

    if (!noWind && !extra.length) { box.hidden = true; return; }

    var lead = noWind
      ? '<b>System Note:</b> Wind forecast data (WRF) is currently unavailable. ' +
        'Rafts are drifting on oceanic currents alone, meaning shoreline stranding ' +
        'figures are <b>heavily understated</b>.'
      : '<b>System Note:</b> This forecast run is running on incomplete inputs.';

    box.innerHTML =
      '<span class="ra-ico" aria-hidden="true">⚠️</span>' +
      '<div>' +
        '<span class="ra-title">Forecast run degraded</span>' +
        '<p>' + lead + '</p>' +
        (extra.length ? '<ul><li>' + extra.join('</li><li>') + '</li></ul>' : '') +
      '</div>';
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
    loadJSON('drift_tracks.json').catch(function () { return { tracks: [] }; }),
    // The basemap is decoration: a missing shoreline file should cost the
    // coastline, not the forecast, so both of these degrade to empty.
    loadJSON('basemap_land.geojson').catch(function () { return null; }),
    loadJSON('basemap_places.geojson').catch(function () { return null; })
  ]).then(function (res) {
    var latest = res[0], segs = res[1], bio = res[2], tracks = res[3];
    App.basemapLand = res[4];
    App.basemapPlaces = res[5];
    App.latest = latest;
    App.segs = segs;
    (segs.features || []).forEach(function (f) { App.segIndex[f.properties.seg_id] = f; });

    // Counted from the data rather than written into the prose: the segment
    // list is derived from the shoreline and changes when the shoreline does,
    // and a hardcoded figure had already gone stale once.
    var segCount = document.getElementById('segCount');
    if (segCount && (segs.features || []).length) {
      segCount.textContent = segs.features.length;
    }

    renderAlert(latest);
    renderStamp(latest);
    renderStats(latest, segs);
    var totals = renderCharts(latest, segs);

    // Redraw the segment source and the legend for the current layer + day.
    // Defined here so it works whether or not the map itself came up.
    var bioData = null;
    App.refreshMap = function () {
      var data = App.redraw ? App.redraw() : null;
      if (!data) {
        data = segmentGeoJSON(segs, App.layer === 'h2s' ? 'h2s' : 'stranding', latest, App.day);
      }
      if (bioData) renderLegend(App.layer, bioData, data, bio, latest);
    };
    App.setLayer = function (name) {
      App.layer = name;
      var map = App.map;
      if (map && map.getLayer && map.getLayer('biomass-pt')) {
        var showBio = name === 'biomass';
        var segVis = showBio ? 'none' : 'visible';
        map.setLayoutProperty('biomass-pt', 'visibility', showBio ? 'visible' : 'none');
        map.setLayoutProperty('segment-casing', 'visibility', segVis);
        map.setLayoutProperty('segment-line', 'visibility', segVis);
        map.setLayoutProperty('segment-pt', 'visibility', segVis);
      }
      App.refreshMap();
      hideTip();
    };

    try {
      if (typeof maplibregl === 'undefined') throw new Error('maplibre-gl not loaded');
      buildMap(bio, segs, tracks, latest);
      bioData = biomassGeoJSON(bio);
    } catch (err) {
      mapFail(err);
    }

    initTimeline(latest, totals);

    var notes = (latest.notes || []);
    if (latest.model && latest.model.n_train) {
      notes = notes.concat(['Learned layer trained on ' + latest.model.n_train +
        ' weekly trap measurements, hold out mean absolute error ' +
        fmt(latest.model.mae_kg_per_m, 1) + ' kg per metre.']);
    }
    if (notes.length) {
      document.getElementById('notes').textContent = 'Run notes: ' + notes.join('. ') + '.';
    }
  }).catch(fail);

})();
