// waveform.js — logic-analyzer style canvas renderer.
//
// One canvas: a left gutter with lane names (+ live value at the hover
// cursor), a time axis on top, and one lane per selected item. Rendering is
// windowed: only edges inside the visible [t0, t1] are touched (binary
// search into the per-net arrays), so tens of thousands of edges stay cheap.

import {
  bisect, lowerBound, pickStep, fmtTick, fmtTime,
  byteLabel, hex2, hashColor,
} from './format.js';

const AXIS_H = 30;      // time axis strip at the top
const LANE_H = 34;      // per-lane height
const GUTTER = 192;     // label column width
const PAD_R = 10;

const COL = {
  bg: '#0b0e14',
  gutterBg: '#10141c',
  grid: '#1a2130',
  gridMajor: '#232c3f',
  axisText: '#8b98ad',
  laneSep: '#161c28',
  label: '#c9d4e3',
  labelDim: '#6b7891',
  value: '#7ee2ff',
  cursor: '#ffd166',
  z: '#d9b64a',
  x: '#e05561',
  uartBox: 'rgba(76, 141, 240, 0.18)',
  uartEdge: '#6ea8ff',
  uartText: '#cfe1ff',
  i2cBox: 'rgba(178, 130, 255, 0.16)',
  i2cEdge: '#b28cff',
  i2cText: '#e2d5ff',
  busy: 'rgba(140, 160, 190, 0.35)',
};

export class WaveformView {
  /**
   * @param scrollHost element that scrolls vertically and defines the width
   * @param canvas     the canvas to draw into
   * @param durationNs total trace duration
   */
  constructor(scrollHost, canvas, durationNs) {
    this.host = scrollHost;
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.duration = Math.max(1, durationNs);
    this.lanes = [];           // {kind, name, data, color}
    this.t0 = 0;
    this.t1 = this.duration;
    this.hoverT = null;
    this.hoverY = null;
    this.onViewChange = null;  // callback(t0, t1)
    this._raf = 0;

    this._bindEvents();
    new ResizeObserver(() => this.resize()).observe(scrollHost);
    this.resize();
  }

  setLanes(lanes) {
    this.lanes = lanes;
    this.resize();
  }

  fit() { this.setView(0, this.duration); }

  setView(t0, t1) {
    let span = t1 - t0;
    span = Math.min(Math.max(span, 10), this.duration);
    t0 = Math.min(Math.max(t0, 0), this.duration - span);
    this.t0 = t0;
    this.t1 = t0 + span;
    this.requestRender();
    if (this.onViewChange) this.onViewChange(this.t0, this.t1);
  }

  zoomBy(factor, centerT = null) {
    const c = centerT === null ? (this.t0 + this.t1) / 2 : centerT;
    const span = (this.t1 - this.t0) * factor;
    const frac = (c - this.t0) / (this.t1 - this.t0);
    this.setView(c - span * frac, c + span * (1 - frac));
  }

  resize() {
    const w = this.host.clientWidth;
    const h = AXIS_H + this.lanes.length * LANE_H + 8;
    const dpr = window.devicePixelRatio || 1;
    this.w = Math.max(w, GUTTER + 60);
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

  // ---- coordinates ----
  xOf(t) { return GUTTER + (t - this.t0) * this._pxPerNs; }
  tOf(x) { return this.t0 + (x - GUTTER) / this._pxPerNs; }

  // ---- events ----
  _bindEvents() {
    const cv = this.canvas;

    cv.addEventListener('wheel', (e) => {
      e.preventDefault();
      const rect = cv.getBoundingClientRect();
      const x = e.clientX - rect.left;
      if (e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY)) {
        // horizontal scroll pans
        const dt = (e.deltaX || e.deltaY) / this._pxPerNs;
        this.setView(this.t0 + dt, this.t1 + dt);
      } else {
        const f = Math.pow(1.0018, e.deltaY);  // smooth, trackpad friendly
        const center = x > GUTTER ? this.tOf(x) : (this.t0 + this.t1) / 2;
        this.zoomBy(f, center);
      }
    }, { passive: false });

    let drag = null;
    cv.addEventListener('mousedown', (e) => {
      const rect = cv.getBoundingClientRect();
      drag = { x: e.clientX - rect.left, t0: this.t0, t1: this.t1, moved: false };
      cv.classList.add('dragging');
    });
    window.addEventListener('mousemove', (e) => {
      const rect = cv.getBoundingClientRect();
      const x = e.clientX - rect.left, y = e.clientY - rect.top;
      if (drag) {
        const dt = (drag.x - x) / this._pxPerNs;
        if (Math.abs(drag.x - x) > 2) drag.moved = true;
        this.setView(drag.t0 + dt, drag.t1 + dt);
      }
      if (e.target === cv || drag) {
        this.hoverT = x > GUTTER ? this.tOf(x) : null;
        this.hoverY = y;
        this.requestRender();
      }
    });
    window.addEventListener('mouseup', () => { drag = null; cv.classList.remove('dragging'); });
    cv.addEventListener('mouseleave', () => {
      if (!drag) { this.hoverT = null; this.requestRender(); }
    });
    cv.addEventListener('dblclick', () => this.fit());
  }

  // ---- rendering ----
  render() {
    const ctx = this.ctx;
    const W = this.w, H = this.h;
    this._pxPerNs = (W - GUTTER - PAD_R) / (this.t1 - this.t0);

    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = COL.bg;
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = COL.gutterBg;
    ctx.fillRect(0, 0, GUTTER, H);

    this._drawAxis(ctx, W, H);

    for (let i = 0; i < this.lanes.length; i++) {
      const top = AXIS_H + i * LANE_H;
      // lane separator
      ctx.strokeStyle = COL.laneSep;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, top + LANE_H + 0.5);
      ctx.lineTo(W, top + LANE_H + 0.5);
      ctx.stroke();

      ctx.save();
      ctx.beginPath();
      ctx.rect(GUTTER, top, W - GUTTER, LANE_H);
      ctx.clip();
      const lane = this.lanes[i];
      if (lane.kind === 'digital') this._drawDigital(ctx, lane, top);
      else if (lane.kind === 'state') this._drawStates(ctx, lane, top);
      else if (lane.kind === 'uart') this._drawUart(ctx, lane, top);
      else if (lane.kind === 'i2c') this._drawI2c(ctx, lane, top);
      ctx.restore();

      this._drawLabel(ctx, lane, top);
    }

    this._drawCursor(ctx, W, H);

    // gutter right border
    ctx.strokeStyle = COL.gridMajor;
    ctx.beginPath();
    ctx.moveTo(GUTTER + 0.5, 0);
    ctx.lineTo(GUTTER + 0.5, H);
    ctx.stroke();
  }

  _drawAxis(ctx, W, H) {
    const span = this.t1 - this.t0;
    ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
    // Adapt tick density to label width (deep-zoom labels get long).
    let step = pickStep(span / Math.max(1, (W - GUTTER) / 90));
    for (let pass = 0; pass < 3; pass++) {
      const lw = ctx.measureText(fmtTick(this.t1, step)).width + 28;
      if (lw <= step * this._pxPerNs) break;
      step = pickStep(lw / this._pxPerNs);
    }
    const first = Math.ceil(this.t0 / step) * step;

    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';

    for (let t = first; t <= this.t1; t += step) {
      const x = Math.round(this.xOf(t)) + 0.5;
      ctx.strokeStyle = COL.grid;
      ctx.beginPath();
      ctx.moveTo(x, AXIS_H);
      ctx.lineTo(x, H);
      ctx.stroke();
      ctx.strokeStyle = COL.gridMajor;
      ctx.beginPath();
      ctx.moveTo(x, AXIS_H - 6);
      ctx.lineTo(x, AXIS_H);
      ctx.stroke();
      ctx.fillStyle = COL.axisText;
      ctx.fillText(fmtTick(t, step), x, AXIS_H / 2);
    }
    ctx.strokeStyle = COL.gridMajor;
    ctx.beginPath();
    ctx.moveTo(GUTTER, AXIS_H + 0.5);
    ctx.lineTo(W, AXIS_H + 0.5);
    ctx.stroke();
  }

  _drawLabel(ctx, lane, top) {
    const cy = top + LANE_H / 2;
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    // colored key bullet
    ctx.fillStyle = lane.color;
    ctx.fillRect(8, cy - 4, 3, 8);
    ctx.font = '12px system-ui, -apple-system, "Segoe UI", sans-serif';
    ctx.fillStyle = COL.label;
    let name = lane.name;
    if (ctx.measureText(name).width > GUTTER - 78) {
      while (name.length > 3 && ctx.measureText(name + '…').width > GUTTER - 78) {
        name = name.slice(0, -1);
      }
      name += '…';
    }
    ctx.fillText(name, 16, cy);

    // live value at hover cursor (Saleae style)
    if (this.hoverT !== null) {
      const v = this._valueAt(lane, this.hoverT);
      if (v !== null) {
        ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
        ctx.textAlign = 'right';
        ctx.fillStyle = COL.value;
        let s = v;
        if (ctx.measureText(s).width > 72) {
          while (s.length > 2 && ctx.measureText(s + '…').width > 72) s = s.slice(0, -1);
          s += '…';
        }
        ctx.fillText(s, GUTTER - 8, cy);
      }
    }
  }

  _valueAt(lane, t) {
    const d = lane.data;
    if (lane.kind === 'digital' || lane.kind === 'state') {
      const i = bisect(d, t);
      return i < 0 ? null : String(d[i][1]);
    }
    // event-style lanes: show nearest event within 12px of the cursor
    const win = 12 / this._pxPerNs;
    const i = bisect(d, t + win);
    if (i < 0) return null;
    if (t - d[i][0] > win) return null;
    if (lane.kind === 'uart') return byteLabel(d[i][1]);
    const tr = d[i][1];
    return `${tr.op} ${hex2(tr.addr)}`;
  }

  /** Iterate hold-value regions of `data` overlapping the view; cb(tA,tB,v). */
  _regions(data, cb) {
    let i = bisect(data, this.t0);
    if (i < 0) i = 0;
    const n = data.length;
    while (i < n && data[i][0] < this.t1) {
      const tA = data[i][0];
      const tB = i + 1 < n ? data[i + 1][0] : this.duration;
      cb(Math.max(tA, this.t0), Math.min(Math.max(tB, tA), this.t1), data[i][1], tA);
      i++;
    }
  }

  _drawDigital(ctx, lane, top) {
    const yHi = top + 8, yLo = top + LANE_H - 10;
    const yMid = (yHi + yLo) / 2;
    ctx.lineWidth = 1.4;
    let prevY = null, prevX2 = null;
    let busyX0 = null, busyX1 = null;

    const flushBusy = () => {
      if (busyX0 === null) return;
      ctx.fillStyle = COL.busy;
      ctx.fillRect(busyX0, yHi, Math.max(busyX1 - busyX0, 1), yLo - yHi);
      busyX0 = null;
      prevY = null;
    };

    this._regions(lane.data, (tA, tB, v) => {
      const x1 = this.xOf(tA), x2 = this.xOf(tB);
      if (x2 - x1 < 0.7) {
        // sub-pixel region: coalesce into a "busy" band
        if (busyX0 === null) busyX0 = x1;
        busyX1 = x2 + 0.7;
        return;
      }
      flushBusy();
      if (v === 'X') {
        ctx.fillStyle = 'rgba(224, 85, 97, 0.22)';
        ctx.fillRect(x1, yHi, x2 - x1, yLo - yHi);
        ctx.strokeStyle = COL.x;
        ctx.strokeRect(x1 + 0.5, yHi + 0.5, x2 - x1 - 1, yLo - yHi - 1);
        // hatch
        ctx.save();
        ctx.beginPath();
        ctx.rect(x1, yHi, x2 - x1, yLo - yHi);
        ctx.clip();
        ctx.beginPath();
        for (let hx = x1 - (yLo - yHi); hx < x2; hx += 6) {
          ctx.moveTo(hx, yLo);
          ctx.lineTo(hx + (yLo - yHi), yHi);
        }
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.restore();
        ctx.lineWidth = 1.4;
        prevY = null; prevX2 = x2;
        return;
      }
      if (v === 'Z') {
        ctx.strokeStyle = COL.z;
        ctx.setLineDash([4, 3]);
        ctx.beginPath();
        ctx.moveTo(x1, yMid);
        ctx.lineTo(x2, yMid);
        ctx.stroke();
        ctx.setLineDash([]);
        prevY = yMid; prevX2 = x2;
        return;
      }
      const y = v === '1' ? yHi : yLo;
      if (v === '1') {
        // faint fill under the high level for readability
        ctx.fillStyle = lane.color.length === 7 ? lane.color + '14' : lane.color;
        ctx.fillRect(x1, yHi, x2 - x1, yLo - yHi);
      }
      ctx.strokeStyle = lane.color;
      ctx.beginPath();
      if (prevY !== null && prevX2 !== null && Math.abs(prevX2 - x1) < 1 && prevY !== y) {
        ctx.moveTo(x1, prevY);   // transition edge
        ctx.lineTo(x1, y);
      } else {
        ctx.moveTo(x1, y);
      }
      ctx.lineTo(x2, y);
      ctx.stroke();
      prevY = y; prevX2 = x2;
    });
    flushBusy();
  }

  _drawStates(ctx, lane, top) {
    const y1 = top + 7, y2 = top + LANE_H - 9;
    ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    this._regions(lane.data, (tA, tB, v) => {
      const x1 = this.xOf(tA), x2 = this.xOf(tB);
      const w = x2 - x1;
      if (w < 0.5) return;
      const c = hashColor(String(v));
      ctx.fillStyle = c + '33';
      ctx.fillRect(x1, y1, w, y2 - y1);
      ctx.strokeStyle = c;
      ctx.lineWidth = 1;
      ctx.strokeRect(x1 + 0.5, y1 + 0.5, Math.max(w - 1, 0.5), y2 - y1 - 1);
      if (w > 26) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x1 + 3, y1, w - 6, y2 - y1);
        ctx.clip();
        ctx.fillStyle = '#dde6f2';
        ctx.fillText(String(v), x1 + 6, (y1 + y2) / 2 + 0.5);
        ctx.restore();
      }
    });
  }

  /** Shared renderer for instantaneous event boxes (UART bytes, I2C xfers). */
  _drawEventBoxes(ctx, lane, top, boxCol, edgeCol, textCol, labelOf, wide) {
    const y1 = top + 7, y2 = top + LANE_H - 9;
    const data = lane.data;
    const i0 = Math.max(lowerBound(data, this.t0) - 1, 0);
    const i1 = bisect(data, this.t1);
    if (i1 < 0) return;

    ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';

    // Each event gets a box sized to its label but never overlapping the next
    // event. When events are packed tighter than ~5px they merge into a
    // single "N ev" density block instead.
    ctx.lineWidth = 1;
    let run = null;  // pending density block {x1, x2, count}
    const flushRun = () => {
      if (!run) return;
      const w = Math.max(run.x2 - run.x1, 2);
      ctx.fillStyle = boxCol;
      ctx.fillRect(run.x1, y1, w, y2 - y1);
      ctx.strokeStyle = edgeCol;
      ctx.strokeRect(run.x1 + 0.5, y1 + 0.5, Math.max(w - 1, 1), y2 - y1 - 1);
      const label = `${run.count} ev`;
      if (ctx.measureText(label).width <= w - 6) {
        ctx.fillStyle = textCol;
        ctx.fillText(label, run.x1 + 3, (y1 + y2) / 2 + 0.5);
      }
      run = null;
    };

    for (let i = i0; i <= i1; i++) {
      const x = this.xOf(data[i][0]);
      const nextX = i + 1 < data.length ? this.xOf(data[i + 1][0]) : Infinity;
      const avail = nextX - x - 1;
      if (avail < 5) {
        if (run && x <= run.x2 + 5) { run.x2 = Math.max(x + 2, run.x2); run.count++; }
        else { flushRun(); run = { x1: x, x2: x + 2, count: 1 }; }
        continue;
      }
      // this event fits on its own; a preceding dense run absorbs it if open
      if (run) { run.x2 = Math.max(x + 2, run.x2); run.count++; flushRun(); continue; }
      const label = labelOf(data[i][1]);
      const desired = wide
        ? Math.min(Math.max(ctx.measureText(label).width + 8, 20), 280)
        : Math.max(ctx.measureText(label).width + 7, 12);
      const w = Math.min(desired, avail);
      ctx.fillStyle = boxCol;
      ctx.fillRect(x, y1, w, y2 - y1);
      ctx.strokeStyle = edgeCol;
      ctx.strokeRect(x + 0.5, y1 + 0.5, Math.max(w - 1, 1), y2 - y1 - 1);
      if (ctx.measureText(label).width <= w - 4) {
        ctx.fillStyle = textCol;
        ctx.fillText(label, x + 3, (y1 + y2) / 2 + 0.5);
      }
    }
    flushRun();
  }

  _drawUart(ctx, lane, top) {
    this._drawEventBoxes(ctx, lane, top, COL.uartBox, COL.uartEdge, COL.uartText,
      (b) => byteLabel(b), false);
  }

  _drawI2c(ctx, lane, top) {
    this._drawEventBoxes(ctx, lane, top, COL.i2cBox, COL.i2cEdge, COL.i2cText,
      (tr) => {
        const bytes = (tr.data || []).map((b) => b.toString(16).padStart(2, '0')).join(' ');
        return `${tr.op} ${hex2(tr.addr)} [${bytes}]${tr.ack ? '' : ' NAK'}`;
      }, true);
  }

  _drawCursor(ctx, W, H) {
    if (this.hoverT === null) return;
    const x = this.xOf(this.hoverT);
    if (x < GUTTER || x > W) return;
    ctx.strokeStyle = COL.cursor;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath();
    ctx.moveTo(Math.round(x) + 0.5, AXIS_H);
    ctx.lineTo(Math.round(x) + 0.5, H);
    ctx.stroke();
    ctx.setLineDash([]);
    // time chip in the axis strip
    const label = fmtTime(this.hoverT, 6);
    ctx.font = '11px ui-monospace, SFMono-Regular, Menlo, monospace';
    const tw = ctx.measureText(label).width + 10;
    let bx = x - tw / 2;
    bx = Math.min(Math.max(bx, GUTTER + 2), W - tw - 2);
    ctx.fillStyle = '#2b2413';
    ctx.fillRect(bx, 3, tw, AXIS_H - 9);
    ctx.strokeStyle = COL.cursor;
    ctx.strokeRect(bx + 0.5, 3.5, tw - 1, AXIS_H - 10);
    ctx.fillStyle = COL.cursor;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, bx + tw / 2, AXIS_H / 2 - 1);
  }
}
