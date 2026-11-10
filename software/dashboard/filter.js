/* GroundTruth bump filter: acceleration magnitude -> 3 Hz high-pass -> threshold.
 *
 * Validated on data analysis/sessions/2026-11-07/session_07.csv (nine hits on a bench):
 * every hit exceeds 1.2 g after the high-pass, nothing else exceeds 0.77 g, so any fixed
 * threshold between those catches 9/9 with 0 false. Default 0.8 g.
 *
 * Works in the browser (window.GTFilter) and in Node (module.exports) so it can be unit-tested.
 */
(function (root) {
  "use strict";

  /** Second-order Butterworth high-pass, bilinear transform, direct form II transposed. Streaming. */
  class HighPass {
    constructor(cutoffHz, sampleHz) { this.design(cutoffHz, sampleHz); this.z1 = 0; this.z2 = 0; this.primed = false; }
    design(cutoffHz, sampleHz) {
      this.fc = cutoffHz; this.fs = sampleHz;
      const K = Math.tan(Math.PI * cutoffHz / sampleHz), K2 = K * K, s2 = Math.SQRT2;
      const norm = 1 / (1 + s2 * K + K2);
      this.b0 = norm; this.b1 = -2 * norm; this.b2 = norm;
      this.a1 = 2 * (K2 - 1) * norm; this.a2 = (1 - s2 * K + K2) * norm;
    }
    /** Start the filter at rest on a constant input (e.g. 1 g) so there is no start-up transient. */
    prime(x0) { this.z1 = -this.b0 * x0; this.z2 = this.b2 * x0; this.primed = true; }
    step(x) {
      if (!this.primed) this.prime(x);
      const y = this.b0 * x + this.z1;
      this.z1 = this.b1 * x - this.a1 * y + this.z2;
      this.z2 = this.b2 * x - this.a2 * y;
      return y;
    }
  }

  /** Magnitude -> high-pass -> threshold with a refractory period. Feed one reading at a time. */
  class BumpFilter {
    constructor(opts = {}) {
      this.cutoffHz = opts.cutoffHz ?? 3;
      this.sampleHz = opts.sampleHz ?? 50;
      this.threshold = opts.threshold ?? 0.8;     // g, on |high-passed magnitude|
      this.refractoryS = opts.refractoryS ?? 1.0;
      this.adaptive = opts.adaptive ?? false;      // threshold = median + k * 1.4826 * MAD over the last windowS
      this.k = opts.k ?? 15;
      this.windowS = opts.windowS ?? 10;
      this.hp = new HighPass(this.cutoffHz, this.sampleHz);
      this.hist = [];                              // recent |hp| for the adaptive threshold
      this.lastEventT = -Infinity;
      this.dts = [];                               // for the sample-rate estimate
      this.lastT = null;
    }
    setThreshold(g) { this.threshold = g; }
    setAdaptive(on) { this.adaptive = on; }
    /** Re-design the filter if the observed sample rate differs a lot from the assumed one. */
    observeRate(t) {
      if (this.lastT !== null) { const dt = t - this.lastT; if (dt >= 0 && dt < 1) this.dts.push(dt); }   // keep dt = 0: rows can share a timestamp
      this.lastT = t;
      if (this.dts.length === 60) {
        const fs = this.dts.length / this.dts.reduce((a, b) => a + b, 0);   // mean rate over the window
        if (Math.abs(fs - this.sampleHz) / this.sampleHz > 0.2) { this.sampleHz = fs; this.hp.design(this.cutoffHz, fs); }
        this.dts = [];
      }
    }
    currentThreshold() {
      if (!this.adaptive) return this.threshold;
      const n = Math.round(this.windowS * this.sampleHz);
      if (this.hist.length < n) return Infinity;
      const a = this.hist.slice(-n).sort((x, y) => x - y);
      const med = a[a.length >> 1];
      const dev = a.map((v) => Math.abs(v - med)).sort((x, y) => x - y);
      return med + this.k * 1.4826 * dev[dev.length >> 1];
    }
    /** @returns {{mag:number, hp:number, ahp:number, threshold:number, event:boolean}} */
    step(t, ax, ay, az) {
      this.observeRate(t);
      const mag = Math.sqrt(ax * ax + ay * ay + az * az);
      const hp = this.hp.step(mag);
      const ahp = Math.abs(hp);
      const threshold = this.currentThreshold();
      this.hist.push(ahp);
      if (this.hist.length > 2000) this.hist.splice(0, this.hist.length - 2000);
      let event = false;
      if (ahp > threshold && t - this.lastEventT > this.refractoryS) { event = true; this.lastEventT = t; }
      return { mag, hp, ahp, threshold, event };
    }
  }

  const api = { HighPass, BumpFilter };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GTFilter = api;
})(typeof window !== "undefined" ? window : globalThis);
