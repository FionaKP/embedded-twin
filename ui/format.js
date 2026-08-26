// format.js — time formatting, binary search, colors. No DOM here.

// ---------- time ----------

const UNITS = [
  { div: 1,   suffix: 'ns' },
  { div: 1e3, suffix: 'µs' },
  { div: 1e6, suffix: 'ms' },
  { div: 1e9, suffix: 's'  },
];

/** Pick the display unit for a magnitude in ns. */
export function unitFor(ns) {
  const a = Math.abs(ns);
  let u = UNITS[0];
  for (const c of UNITS) if (a >= c.div) u = c;
  return u;
}

/** Humanize a single timestamp/interval, e.g. 27000000000 -> "27 s". */
export function fmtTime(ns, sig = 4) {
  if (!isFinite(ns)) return '—';
  if (ns === 0) return '0 s';
  const u = unitFor(ns);
  const v = ns / u.div;
  let s = v.toPrecision(sig);
  if (s.includes('.')) s = s.replace(/\.?0+$/, '');
  return `${s} ${u.suffix}`;
}

/** Long-form duration for the header: "2 min 0 s", "1 h 12 min", "340 ms". */
export function fmtDuration(ns) {
  const s = ns / 1e9;
  if (s < 1) return fmtTime(ns);
  if (s < 60) return `${trim(s, 3)} s`;
  const min = Math.floor(s / 60);
  if (min < 60) return `${min} min ${trim(s - min * 60, 3)} s`;
  const h = Math.floor(min / 60);
  return `${h} h ${min - h * 60} min`;
}

function trim(v, sig) {
  let s = v.toPrecision(sig);
  if (s.includes('.')) s = s.replace(/\.?0+$/, '');
  return s;
}

/** Nice tick step (1/2/5 * 10^k ns) for a target spacing. */
export function pickStep(targetNs) {
  const pow = Math.pow(10, Math.floor(Math.log10(Math.max(targetNs, 1))));
  for (const m of [1, 2, 5, 10]) {
    if (pow * m >= targetNs) return pow * m;
  }
  return pow * 10;
}

/** Label a tick value given the step it was generated from (unit from step). */
export function fmtTick(ns, stepNs) {
  const u = unitFor(stepNs);
  const decimals = Math.max(0, -Math.floor(Math.log10(stepNs / u.div) + 1e-9));
  return `${(ns / u.div).toFixed(decimals)} ${u.suffix}`;
}

// ---------- search ----------

/** Rightmost index i with arr[i][0] <= t, else -1. Arrays are [[t, ...], ...]. */
export function bisect(arr, t) {
  let lo = 0, hi = arr.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid][0] <= t) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return ans;
}

/** First index i with arr[i][0] >= t (may be arr.length). */
export function lowerBound(arr, t) {
  let lo = 0, hi = arr.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (arr[mid][0] < t) lo = mid + 1; else hi = mid;
  }
  return lo;
}

// ---------- bytes ----------

/** Short label for a UART byte: printable char, or hex. */
export function byteLabel(b) {
  if (b >= 0x21 && b <= 0x7e) return String.fromCharCode(b);
  if (b === 0x20) return '␣';
  if (b === 0x0a) return '\\n';
  if (b === 0x0d) return '\\r';
  if (b === 0x09) return '\\t';
  return b.toString(16).padStart(2, '0').toUpperCase();
}

export function hex2(b) { return '0x' + b.toString(16).padStart(2, '0').toUpperCase(); }

// ---------- colors ----------

/** Trace/series palette tuned for a near-black background. */
export const PALETTE = [
  '#4cc9f0', '#80ed99', '#ffd166', '#f28fad', '#b39ddb', '#f4a261',
  '#64dfdf', '#a3e635', '#7dd3fc', '#e5989b', '#c4b5fd', '#f9c74f',
];

export function paletteColor(i) { return PALETTE[i % PALETTE.length]; }

/** Deterministic color for arbitrary strings (state names, log sources). */
export function hashColor(str) {
  if (str === 'off' || str === 'idle') return '#5c6878';
  let h = 0;
  for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}
