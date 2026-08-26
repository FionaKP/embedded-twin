// app.js — entry point: loads trace.json and builds every panel.

import { fmtTime, fmtDuration, hashColor, paletteColor, hex2 } from './format.js';
import { WaveformView } from './waveform.js';
import { StepChart } from './charts.js';

const MAX_DEFAULT_LANES = 20;

main();

async function main() {
  let trace;
  try {
    const resp = await fetch('trace.json');
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    trace = await resp.json();
  } catch (err) {
    document.getElementById('main').hidden = true;
    document.getElementById('tabs').hidden = true;
    const box = document.getElementById('load-error');
    box.hidden = false;
    document.getElementById('load-error-detail').textContent = String(err);
    return;
  }
  normalize(trace);
  buildHeader(trace);
  buildTabs();
  const waves = buildWaveforms(trace);
  buildPower(trace);
  buildSerial(trace);
  buildAssertions(trace);
  buildLogs(trace);
  buildComponents(trace);
  // canvas widths are 0 while a panel is hidden; refresh on tab switch
  document.getElementById('tabs').addEventListener('click', () => {
    requestAnimationFrame(() => waves.resize());
  });
  // debug handle (e.g. twin.waves.setView(27e9, 27.2e9) from the console)
  window.twin = { trace, waves };
}

/** Defensive cleanup: sort by time, drop same-timestamp duplicates (keep last). */
function normalize(trace) {
  const dedupe = (arr) => {
    arr.sort((a, b) => a[0] - b[0]);
    const out = [];
    for (const e of arr) {
      if (out.length && out[out.length - 1][0] === e[0]) out[out.length - 1] = e;
      else out.push(e);
    }
    return out;
  };
  for (const key of ['signals', 'analog', 'states']) {
    const group = trace[key] || {};
    for (const name of Object.keys(group)) group[name] = dedupe(group[name]);
  }
  for (const key of ['uart', 'i2c']) {
    const group = trace[key] || {};
    for (const name of Object.keys(group)) group[name].sort((a, b) => a[0] - b[0]);
  }
  trace.power = trace.power || {};
  for (const key of ['rails', 'battery', 'thermal']) {
    const group = trace.power[key] || {};
    for (const name of Object.keys(group)) group[name] = dedupe(group[name]);
  }
}

// ---------- header ----------

function buildHeader(trace) {
  const m = trace.meta || {};
  const host = document.getElementById('hdr-meta');
  const badge = el('span', `badge ${m.verdict === 'PASS' ? 'pass' : 'fail'}`, m.verdict || '?');
  host.appendChild(chip('scenario', m.scenario ?? '—', true));
  host.appendChild(badge);
  host.appendChild(chip('duration', fmtDuration(m.duration_ns ?? 0)));
  if (m.sim_hours != null) {
    host.appendChild(chip('sim time', `${trimNum(m.sim_hours * 60, 4)} min`));
  }
  host.appendChild(chip('seed', String(m.seed ?? '—')));
  host.appendChild(chip('lock', String(m.lock_hash ?? '—')));
  document.title = `${m.scenario ?? 'trace'} — embedded-twin`;
}

function chip(label, value, strong = false) {
  const c = el('span', 'meta-chip');
  c.appendChild(el('span', 'meta-label', label));
  c.appendChild(el('span', `meta-value mono${strong ? ' strong' : ''}`, value));
  return c;
}

// ---------- tabs ----------

function buildTabs() {
  const nav = document.getElementById('tabs');
  nav.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (!btn) return;
    for (const t of nav.querySelectorAll('.tab')) t.classList.toggle('active', t === btn);
    for (const p of document.querySelectorAll('#main .panel')) {
      p.classList.toggle('active', p.id === `panel-${btn.dataset.tab}`);
    }
  });
}

// ---------- waveforms ----------

function buildWaveforms(trace) {
  const duration = trace.meta?.duration_ns ?? 1;
  const view = new WaveformView(
    document.getElementById('wave-scroll'),
    document.getElementById('wave-canvas'),
    duration,
  );

  // Candidate lanes, in canonical order: digital nets (nets[] order, grouped
  // by class), then states, then uart, then i2c.
  const netClass = {};
  for (const n of trace.nets || []) netClass[n.name] = n.class;
  const classOrder = { signal: 0, power: 1, ground: 2 };
  const digitalNets = Object.keys(trace.signals || {})
    .sort((a, b) => (classOrder[netClass[a]] ?? 3) - (classOrder[netClass[b]] ?? 3)
      || a.localeCompare(b));

  const lanes = [];
  let colorIdx = 0;
  for (const net of digitalNets) {
    lanes.push({
      kind: 'digital', name: net, group: netClass[net] ?? 'signal',
      data: trace.signals[net], color: paletteColor(colorIdx++),
    });
  }
  for (const name of Object.keys(trace.states || {}).sort()) {
    lanes.push({
      kind: 'state', name, group: 'states',
      data: trace.states[name], color: '#9aa7ba',
    });
  }
  for (const net of Object.keys(trace.uart || {}).sort()) {
    lanes.push({
      kind: 'uart', name: `${net} (uart)`, group: 'uart',
      data: trace.uart[net], color: '#6ea8ff',
    });
  }
  for (const net of Object.keys(trace.i2c || {}).sort()) {
    lanes.push({
      kind: 'i2c', name: `${net} (i2c)`, group: 'i2c',
      data: trace.i2c[net], color: '#b28cff',
    });
  }

  // Defaults: signal-class digital nets + states + uart, capped.
  let budget = MAX_DEFAULT_LANES;
  for (const l of lanes) {
    const wanted = l.group === 'signal' || l.group === 'states' || l.group === 'uart';
    l.checked = wanted && budget > 0;
    if (l.checked) budget--;
  }

  const apply = () => {
    view.setLanes(lanes.filter((l) => l.checked));
    view.requestRender();
  };

  buildNetPicker(lanes, apply);
  apply();
  view.fit();

  // toolbar
  const rangeEl = document.getElementById('wave-range');
  view.onViewChange = (t0, t1) => {
    rangeEl.textContent = `${fmtTime(t0, 4)} – ${fmtTime(t1, 4)}  (span ${fmtTime(t1 - t0, 4)})`;
  };
  view.onViewChange(view.t0, view.t1);
  document.getElementById('btn-fit').addEventListener('click', () => view.fit());
  document.getElementById('btn-zoom-in').addEventListener('click', () => view.zoomBy(0.5));
  document.getElementById('btn-zoom-out').addEventListener('click', () => view.zoomBy(2));
  return view;
}

function buildNetPicker(lanes, apply) {
  const host = document.getElementById('net-picker');
  const groups = [
    ['signal', 'Signals'], ['power', 'Power nets'], ['ground', 'Ground nets'],
    ['states', 'State machines'], ['uart', 'UART'], ['i2c', 'I2C'],
  ];
  const actions = el('div', 'picker-actions');
  const btnAll = el('button', 'mini-btn', 'All');
  const btnNone = el('button', 'mini-btn', 'None');
  actions.appendChild(btnAll);
  actions.appendChild(btnNone);
  host.appendChild(actions);
  const boxes = [];
  btnAll.addEventListener('click', () => {
    for (const b of boxes) b.checkbox.checked = b.lane.checked = true;
    apply();
  });
  btnNone.addEventListener('click', () => {
    for (const b of boxes) b.checkbox.checked = b.lane.checked = false;
    apply();
  });

  for (const [key, title] of groups) {
    const members = lanes.filter((l) => l.group === key);
    if (!members.length) continue;
    host.appendChild(el('div', 'picker-group', title));
    for (const lane of members) {
      const row = el('label', 'picker-row');
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.checked = lane.checked;
      cb.addEventListener('change', () => { lane.checked = cb.checked; apply(); });
      const key2 = el('span', 'picker-key');
      key2.style.background = lane.kind === 'digital' ? lane.color : 'transparent';
      row.appendChild(cb);
      row.appendChild(key2);
      row.appendChild(el('span', 'picker-name', lane.name));
      host.appendChild(row);
      boxes.push({ checkbox: cb, lane });
    }
  }
}

// ---------- power ----------

function buildPower(trace) {
  const host = document.getElementById('power-charts');
  const duration = trace.meta?.duration_ns ?? 1;
  const p = trace.power || {};
  const mA = (v) => `${trimNum(v, 4)} mA`;

  const rails = Object.keys(p.rails || {});
  if (rails.length) {
    new StepChart(host, {
      title: 'Rail current',
      duration,
      series: rails.map((r, i) => ({
        name: r, color: paletteColor(i), axis: 'l', mode: 'step',
        points: p.rails[r],
      })),
      left: { label: 'mA', fmt: mA, includeZero: true },
      right: null,
    });
  }

  for (const ref of Object.keys(p.battery || {})) {
    const pts = p.battery[ref];
    new StepChart(host, {
      title: `Battery ${ref} — state of charge & voltage`,
      duration,
      series: [
        {
          name: 'SoC', color: '#80ed99', axis: 'l', mode: 'line',
          points: pts.map(([t, soc]) => [t, soc * 100]),
        },
        {
          name: 'voltage', color: '#ffd166', axis: 'r', mode: 'line',
          points: pts.map(([t, , v]) => [t, v]),
        },
      ],
      left: { label: '%', fmt: (v) => `${trimNum(v, 6)} %`, includeZero: false },
      right: { label: 'V', fmt: (v) => `${trimNum(v, 4)} V`, includeZero: false },
    });
  }

  const therm = Object.keys(p.thermal || {});
  if (therm.length) {
    new StepChart(host, {
      title: 'Thermal',
      duration,
      series: therm.map((ref, i) => ({
        name: ref, color: paletteColor(i + 3), axis: 'l', mode: 'line',
        points: p.thermal[ref],
      })),
      left: { label: '°C', fmt: (v) => `${trimNum(v, 4)} °C`, includeZero: false },
      right: null,
    });
  }

  if (!host.children.length) {
    host.appendChild(el('div', 'empty-note', 'No power data in this trace.'));
  }
}

// ---------- serial ----------

function buildSerial(trace) {
  const tabsHost = document.getElementById('serial-tabs');
  const consoles = document.getElementById('serial-consoles');
  const nets = Object.keys(trace.uart || {}).sort();
  if (!nets.length) {
    consoles.appendChild(el('div', 'empty-note', 'No UART traffic in this trace.'));
    return;
  }

  const panes = new Map();
  nets.forEach((net, idx) => {
    const btn = el('button', `tab sub${idx === 0 ? ' active' : ''}`, net);
    btn.dataset.net = net;
    tabsHost.appendChild(btn);

    const pane = el('div', `serial-pane${idx === 0 ? ' active' : ''}`);
    for (const line of decodeUartLines(trace.uart[net])) {
      const row = el('div', 'serial-line');
      row.appendChild(el('span', 'serial-ts mono', fmtTime(line.t, 6)));
      row.appendChild(el('span', 'serial-text mono', line.text));
      pane.appendChild(row);
    }
    consoles.appendChild(pane);
    panes.set(net, { btn, pane });
  });

  tabsHost.addEventListener('click', (e) => {
    const btn = e.target.closest('.tab');
    if (!btn) return;
    for (const { btn: b, pane } of panes.values()) {
      const on = b === btn;
      b.classList.toggle('active', on);
      pane.classList.toggle('active', on);
    }
  });
}

/** Turn [[t, byte], ...] into display lines split on \n (CR stripped). */
function decodeUartLines(bytes) {
  const lines = [];
  let cur = null;
  for (const [t, b] of bytes) {
    if (cur === null) cur = { t, text: '' };
    if (b === 0x0a) { lines.push(cur); cur = null; continue; }
    if (b === 0x0d) continue;
    cur.text += (b >= 0x20 && b <= 0x7e) || b === 0x09
      ? String.fromCharCode(b)
      : `⟨${b.toString(16).padStart(2, '0').toUpperCase()}⟩`;
  }
  if (cur !== null) lines.push(cur);
  return lines;
}

// ---------- assertions ----------

function buildAssertions(trace) {
  const asserts = (trace.assertions || []).slice()
    .sort((a, b) => Number(a.passed) - Number(b.passed));  // failures first
  const nFail = asserts.filter((a) => !a.passed).length;

  const summary = document.getElementById('asserts-summary');
  summary.appendChild(el(
    'span',
    `badge ${nFail ? 'fail' : 'pass'}`,
    nFail ? `${nFail} FAILED` : 'ALL PASSED',
  ));
  summary.appendChild(el('span', 'asserts-count',
    `${asserts.length - nFail} of ${asserts.length} assertions passed`));

  const tbody = document.querySelector('#asserts-table tbody');
  for (const a of asserts) {
    const tr = document.createElement('tr');
    tr.className = a.passed ? 'row-pass' : 'row-fail';
    const td0 = document.createElement('td');
    td0.appendChild(el('span', `dot ${a.passed ? 'pass' : 'fail'}`, a.passed ? '✓' : '✗'));
    tr.appendChild(td0);
    tr.appendChild(el('td', 'mono assert-type', a.type ?? ''));
    tr.appendChild(el('td', 'assert-evidence', a.evidence ?? ''));
    tbody.appendChild(tr);
  }
  if (!asserts.length) {
    const tr = document.createElement('tr');
    tr.appendChild(el('td', 'empty-note', 'No assertions in this trace.'));
    tbody.appendChild(tr);
  }

  // failure count badge on the tab itself
  if (nFail) {
    const tab = document.querySelector('.tab[data-tab="asserts"]');
    tab.appendChild(el('span', 'tab-badge', String(nFail)));
  }
}

// ---------- logs & events ----------

function buildLogs(trace) {
  const host = document.getElementById('log-list');
  const rows = [];
  for (const [t, source, message] of trace.logs || []) {
    rows.push({ t, kind: 'log', source, text: message });
  }
  for (const [t, kind, detail] of trace.events || []) {
    rows.push({ t, kind: 'event', source: kind, text: detailText(detail) });
  }
  rows.sort((a, b) => a.t - b.t);

  if (!rows.length) {
    host.appendChild(el('div', 'empty-note', 'No logs or events in this trace.'));
    return;
  }
  for (const r of rows) {
    const row = el('div', 'log-row');
    row.appendChild(el('span', 'serial-ts mono', fmtTime(r.t, 6)));
    const badge = el('span', `src-badge${r.kind === 'event' ? ' event' : ''}`, r.source);
    if (r.kind !== 'event') badge.style.color = hashColor(r.source);
    row.appendChild(badge);
    row.appendChild(el('span', 'log-text mono', r.text));
    host.appendChild(row);
  }
}

function detailText(detail) {
  if (detail === null || detail === undefined) return '';
  if (typeof detail !== 'object') return String(detail);
  return Object.entries(detail).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join('  ');
}

// ---------- components ----------

function buildComponents(trace) {
  const host = document.getElementById('components-table');
  const comps = trace.components || [];
  if (!comps.length) {
    host.appendChild(el('div', 'empty-note', 'none'));
    return;
  }
  const table = document.createElement('table');
  for (const c of comps) {
    const tr = document.createElement('tr');
    tr.appendChild(el('td', 'mono comp-ref', c.ref ?? ''));
    tr.appendChild(el('td', 'comp-model', c.model ?? ''));
    tr.appendChild(el('td', 'mono comp-value', c.value ?? ''));
    table.appendChild(tr);
  }
  host.appendChild(table);
}

// ---------- small helpers ----------

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function trimNum(v, sig) {
  let s = Number(v).toPrecision(sig);
  if (s.includes('.') && !s.includes('e')) s = s.replace(/\.?0+$/, '');
  return s;
}
