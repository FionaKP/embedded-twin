// charts.js — step/line charts on canvas for power, battery and thermal data.
//
// Samples are step semantics ("hold until next sample"), matching the trace
// contract. Each chart owns a card with a title, an HTML legend, a canvas and
// a hover crosshair with a per-series readout.

import { bisect, pickStep, fmtTick, fmtTime } from './format.js';

const M = { l: 62, r: 62, t: 12, b: 26 };

const COL = {
  bg: '#0e121a',
  grid: '#1a2130',
  axisText: '#8b98ad',
  cursor: '#ffd166',
};

export class StepChart {
  /**
   * @param host container element; the chart appends a card to it
   * @param opts {
   *   title, duration,
   *   series: [{name, color, points: [[t, v], ...], axis: 'l'|'r', mode: 'step'|'line'}],
   *   left:  {label, fmt, includeZero},
   *   right: {label, fmt, includeZero} | null,
   * }
   */
  constructor(host, opts) {
    this.o = opts;
    this.card = document.createElement('div');
    this.card.className = 'chart-card';

    const head = document.createElement('div');
    head.className = 'chart-head';
    const h = document.createElement('h3');
    h.textContent = opts.title;
    head.appendChild(h);
    const legend = document.createElement('div');
    legend.className = 'chart-legend';
    for (const s of opts.series) {
      const chip = document.createElement('span');
      chip.className = 'legend-chip';
      const dot = document.createElement('span');
      dot.className = 'legend-dot';
      dot.style.background = s.color;
      chip.appendChild(dot);
      chip.appendChild(document.createTextNode(s.name));
      legend.appendChild(chip);
    }
    head.appendChild(legend);
    this.card.appendChild(head);

    this.wrap = document.createElement('div');
    this.wrap.className = 'chart-canvas-wrap';
    this.canvas = document.createElement('canvas');
    this.wrap.appendChild(this.canvas);
    this.tip = document.createElement('div');
    this.tip.className = 'chart-tip';
    this.tip.hidden = true;
    this.wrap.appendChild(this.tip);
    this.card.appendChild(this.wrap);
    host.appendChild(this.card);

    this.ctx = this.canvas.getContext('2d');
    this.hoverT = null;
    this._raf = 0;

    this._computeDomains();
    this._bindEvents();
    new ResizeObserver(() => this.resize()).observe(this.wrap);
    this.resize();
  }

  _computeDomains() {
    const dom = { l: null, r: null };
    for (const s of this.o.series) {
      const d = dom[s.axis] || (dom[s.axis] = { min: Infinity, max: -Infinity });
      for (const p of s.points) {
        if (p[1] < d.min) d.min = p[1];
        if (p[1] > d.max) d.max = p[1];
      }
    }
    for (const k of ['l', 'r']) {
      const d = dom[k];
      if (!d) continue;
      const spec = k === 'l' ? this.o.left : this.o.right;
      if (spec && spec.includeZero) d.min = Math.min(d.min, 0);
      if (d.min === d.max) { d.min -= 1; d.max += 1; }
      const pad = (d.max - d.min) * 0.08;
      d.max += pad;
      if (!(spec && spec.includeZero && d.min === 0)) d.min -= pad;
    }
    this.dom = dom;
  }

  resize() {
    const w = this.wrap.clientWidth, h = 200;
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.max(w, 200);
    this.h = h;
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(h * dpr);
    this.canvas.style.width = this.w + 'px';
    this.canvas.style.height = h + 'px';
    this.dpr = dpr;
    this.requestRender();
  }

  requestRender() {
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => { this._raf = 0; this.render(); });
  }

  xOf(t) { return M.l + (t / this.o.duration) * (this.w - M.l - M.r); }
  tOf(x) { return ((x - M.l) / (this.w - M.l - M.r)) * this.o.duration; }
  yOf(v, axis) {
    const d = this.dom[axis];
    return M.t + (1 - (v - d.min) / (d.max - d.min)) * (this.h - M.t - M.b);
  }

  _bindEvents() {
    this.canvas.addEventListener('mousemove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      this.hoverT = (x >= M.l && x <= this.w - M.r)
        ? Math.min(Math.max(this.tOf(x), 0), this.o.duration) : null;
      this._updateTip(x);
      this.requestRender();
    });
    this.canvas.addEventListener('mouseleave', () => {
      this.hoverT = null;
      this.tip.hidden = true;
      this.requestRender();
    });
  }

  _updateTip(px) {
    if (this.hoverT === null) { this.tip.hidden = true; return; }
    const rows = [`<div class="tip-time">${fmtTime(this.hoverT, 5)}</div>`];
    for (const s of this.o.series) {
      const i = bisect(s.points, this.hoverT);
      const spec = s.axis === 'l' ? this.o.left : this.o.right;
      const val = i < 0 ? '—' : spec.fmt(s.points[i][1]);
      rows.push(
        `<div class="tip-row"><span class="legend-dot tip-dot"></span>` +
        `<span class="tip-name">${escapeHtml(s.name)}</span>` +
        `<span class="tip-val">${escapeHtml(val)}</span></div>`,
      );
    }
    this.tip.innerHTML = rows.join('');
    // color the dots (innerHTML with inline style would violate strict CSP)
    const dots = this.tip.querySelectorAll('.tip-dot');
    this.o.series.forEach((s, i) => { if (dots[i]) dots[i].style.background = s.color; });
    this.tip.hidden = false;
    const left = Math.min(px + 14, this.w - 190);
    this.tip.style.left = left + 'px';
    this.tip.style.top = '10px';
  }

  render() {
    const ctx = this.ctx, W = this.w, H = this.h;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = COL.bg;
    ctx.fillRect(0, 0, W, H);

    ctx.font = '10.5px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.fillStyle = COL.axisText;

    // X grid + labels
    const step = pickStep(this.o.duration / Math.max(1, (W - M.l - M.r) / 90));
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    for (let t = 0; t <= this.o.duration + 1; t += step) {
      const x = Math.round(this.xOf(t)) + 0.5;
      ctx.strokeStyle = COL.grid;
      ctx.beginPath();
      ctx.moveTo(x, M.t);
      ctx.lineTo(x, H - M.b);
      ctx.stroke();
      ctx.fillText(fmtTick(t, step), x, H - M.b + 6);
    }

    // Y grids/labels per axis
    this._drawYAxis(ctx, 'l');
    if (this.o.right && this.dom.r) this._drawYAxis(ctx, 'r');

    // series
    for (const s of this.o.series) this._drawSeries(ctx, s);

    // crosshair
    if (this.hoverT !== null) {
      const x = Math.round(this.xOf(this.hoverT)) + 0.5;
      ctx.strokeStyle = COL.cursor;
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(x, M.t);
      ctx.lineTo(x, H - M.b);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  _drawYAxis(ctx, axis) {
    const d = this.dom[axis];
    if (!d) return;
    const spec = axis === 'l' ? this.o.left : this.o.right;
    const step = niceNum((d.max - d.min) / 4);
    const first = Math.ceil(d.min / step) * step;
    ctx.textBaseline = 'middle';
    ctx.textAlign = axis === 'l' ? 'right' : 'left';
    for (let v = first; v <= d.max + step * 1e-9; v += step) {
      const y = Math.round(this.yOf(v, axis)) + 0.5;
      if (axis === 'l') {
        ctx.strokeStyle = COL.grid;
        ctx.beginPath();
        ctx.moveTo(M.l, y);
        ctx.lineTo(this.w - M.r, y);
        ctx.stroke();
        ctx.fillStyle = COL.axisText;
        ctx.fillText(spec.fmt(v), M.l - 8, y);
      } else {
        ctx.fillStyle = COL.axisText;
        ctx.fillText(spec.fmt(v), this.w - M.r + 8, y);
      }
    }
  }

  _drawSeries(ctx, s) {
    const pts = s.points;
    if (!pts.length) return;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.6;
    ctx.lineJoin = 'round';
    ctx.beginPath();
    let started = false;
    for (let i = 0; i < pts.length; i++) {
      const x = this.xOf(pts[i][0]);
      const y = this.yOf(pts[i][1], s.axis);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else if (s.mode === 'line') { ctx.lineTo(x, y); }
      else { ctx.lineTo(x, this.yOf(pts[i - 1][1], s.axis)); ctx.lineTo(x, y); }
    }
    // hold last value to end of trace
    const last = pts[pts.length - 1];
    ctx.lineTo(this.xOf(this.o.duration), this.yOf(last[1], s.axis));
    ctx.stroke();
    // sample markers when sparse
    if (pts.length <= 80) {
      ctx.fillStyle = s.color;
      for (const p of pts) {
        ctx.beginPath();
        ctx.arc(this.xOf(p[0]), this.yOf(p[1], s.axis), 2.2, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }
}

function niceNum(x) {
  const pow = Math.pow(10, Math.floor(Math.log10(Math.abs(x) || 1)));
  const f = x / pow;
  const nf = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
  return nf * pow;
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]
  ));
}
