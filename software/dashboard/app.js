/* GroundTruth live telemetry dashboard.
 * Polls the Supabase `readings` table (PostgREST) and draws ax/ay/az over time.
 * Only rows inserted after "Start Reading Session" are shown: on start we read the current
 * max id and then ask for id > watermark on every poll.
 */
(() => {
  "use strict";

  const PALETTE = {
    deepGreen: "#151B1A", moss: "#80957B", earthBrown: "#897560", stoneGray: "#A7ABA4", chalk: "#F0F1EA",
    surface: "#F7F8F3", grid: "#DDE0D8", text2: "#4A524E", text3: "#7C827E", alertWash: "rgba(137,117,96,0.16)",
  };
  const STORAGE_KEY = "gt.dashboard.config";
  const MAX_POINTS = 60_000;
  // ?demo=1 synthesises a local 200 Hz stream (no Supabase) for layout checks and hardware-free demos.
  // ?autostart=1 clicks Start Reading Session on load (kiosk screens).
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const AUTOSTART = params.has("autostart");

  // ---------- config ----------
  const defaults = Object.assign(
    { supabaseUrl: "", supabaseKey: "", table: "readings", pollMs: 500, windowSeconds: 20 },
    window.GROUNDTRUTH_CONFIG || {}
  );
  function loadConfig() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
      return Object.assign({}, defaults, saved || {});
    } catch { return Object.assign({}, defaults); }
  }
  let cfg = loadConfig();

  // ---------- state ----------
  const state = {
    running: false,
    lastId: 0,
    t0: null,              // ms epoch of the first row of this session
    points: [],            // {id, t, ax, ay, az, bump, lat, lon, session, at}
    spans: [],             // {t0, t1} bump observations
    events: [],
    inBump: false,
    rateWindow: [],        // receive timestamps for Hz
    hoverT: null,
    windowSeconds: Number(cfg.windowSeconds) || 20,
    pollTimer: null,
    lastSession: null,
    needsDraw: false,
  };

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const el = {
    conn: $("conn"), connText: $("connText"), startBtn: $("startBtn"), stopBtn: $("stopBtn"),
    sessionChip: $("sessionChip"), windowSel: $("windowSel"), soundChk: $("soundChk"),
    statRows: $("statRows"), statRate: $("statRate"), statLag: $("statLag"), statBumps: $("statBumps"),
    signal: $("signal"), signalText: $("signalText"), signalSub: $("signalSub"), flash: $("flash"),
    tooltip: $("tooltip"), charts: $("charts"), events: $("events"), eventsCount: $("eventsCount"),
    table: $("table").querySelector("tbody"), tableName: $("tableName"), pollMs: $("pollMs"),
    settings: $("settings"), settingsBtn: $("settingsBtn"), settingsForm: $("settingsForm"),
    cfgUrl: $("cfgUrl"), cfgKey: $("cfgKey"), cfgTable: $("cfgTable"), cfgPoll: $("cfgPoll"),
    cfgReset: $("cfgReset"), cfgCancel: $("cfgCancel"),
    now: { ax: $("axNow"), ay: $("ayNow"), az: $("azNow") },
  };

  // ---------- Supabase (PostgREST) ----------
  function baseUrl() {
    const u = String(cfg.supabaseUrl || "").trim().replace(/\/+$/, "");
    return `${u}/rest/v1/${encodeURIComponent(String(cfg.table || "readings").trim())}`;
  }
  function headers() {
    return { apikey: cfg.supabaseKey, Authorization: `Bearer ${cfg.supabaseKey}`, Accept: "application/json" };
  }
  async function query(params) {
    const res = await fetch(`${baseUrl()}?${params}`, { headers: headers() });
    const text = await res.text();
    if (!res.ok) {
      let msg = text;
      try { msg = JSON.parse(text).message || text; } catch {}
      throw new Error(`${res.status} ${msg}`.slice(0, 160));
    }
    return text ? JSON.parse(text) : [];
  }
  const maxId = async () => DEMO ? 0 : (await query("select=id&order=id.desc&limit=1"))[0]?.id ?? 0;
  const fetchNew = (afterId) => DEMO ? demoRows() : query(
    "select=id,session_date,session_number,recorded_at,ax,ay,az,latitude,longitude,anomaly_detected" +
    `&id=gt.${afterId}&order=id.asc&limit=2000`
  );

  // ---------- demo generator ----------
  const demo = { id: 0, start: null, lastGen: null, nextBumpAt: 3, bumpUntil: -1, bumpT0: 0 };
  function demoRows() {
    const now = Date.now();
    if (demo.start === null) { demo.start = now; demo.lastGen = now; return []; }
    const rows = [];
    const today = new Date().toISOString().slice(0, 10);
    for (let ts = demo.lastGen + 5; ts <= now; ts += 5) {
      const t = (ts - demo.start) / 1000;
      if (t >= demo.nextBumpAt) {
        demo.bumpT0 = t; demo.bumpUntil = t + 0.45;
        demo.nextBumpAt = t + 4 + Math.random() * 4;
      }
      const inBump = t < demo.bumpUntil;
      const road = 0.04 * Math.sin(t * 2.1) + 0.03 * Math.sin(t * 7.3);
      let ax = 0.02 * Math.sin(t * 1.3) + gauss() * 0.02;
      let ay = road + gauss() * 0.02;
      let az = 1 + 0.5 * road + gauss() * 0.03;
      if (inBump) {
        const k = Math.exp(-(t - demo.bumpT0) * 8);
        az += 2.2 * k * Math.sin((t - demo.bumpT0) * 60);
        ax += 0.6 * k * Math.sin((t - demo.bumpT0) * 45);
        ay += 0.4 * k * gauss();
      }
      rows.push({
        id: ++demo.id, session_date: today, session_number: 0, recorded_at: new Date(ts).toISOString(),
        ax, ay, az, latitude: 40.7128 + t * 1e-5, longitude: -74.006 + t * 1e-5, anomaly_detected: inBump ? 1 : 0,
      });
    }
    demo.lastGen = now;
    return rows;
  }
  function gauss() { let u = 0, v = 0; while (u === 0) u = Math.random(); while (v === 0) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }

  // ---------- connection pill ----------
  function setConn(kind, text) {
    el.conn.className = `pill pill-${kind}`;
    el.connText.textContent = text;
  }
  async function checkConnection() {
    if (DEMO) { setConn("ok", "Demo · synthetic data, no Supabase"); return true; }
    if (!cfg.supabaseUrl || !cfg.supabaseKey) { setConn("err", "No credentials: open Settings"); return false; }
    try {
      await query("select=id&limit=1");
      setConn("ok", `Connected · ${new URL(cfg.supabaseUrl).host}`);
      return true;
    } catch (e) {
      setConn("err", /PGRST205|schema cache/.test(e.message) ? `Table "${cfg.table}" missing: run schema.sql` : `Error: ${e.message}`);
      return false;
    }
  }

  // ---------- session ----------
  async function start() {
    if (state.running) return;
    el.startBtn.disabled = true;
    if (!(await checkConnection())) { el.startBtn.disabled = false; return; }
    try {
      state.lastId = await maxId();
    } catch (e) {
      setConn("err", `Error: ${e.message}`);
      el.startBtn.disabled = false;
      return;
    }
    Object.assign(state, {
      running: true, t0: null, points: [], spans: [], events: [], inBump: false,
      rateWindow: [], hoverT: null, lastSession: null,
    });
    el.stopBtn.disabled = false;
    el.stopBtn.classList.add("btn-stop");
    el.sessionChip.textContent = "Waiting for rows…";
    setSignalIdle();
    renderEvents();
    renderTable();
    updateStats();
    setConn("live", "Live · waiting for new rows");
    requestDraw();
    poll();
  }

  function stop() {
    state.running = false;
    clearTimeout(state.pollTimer);
    state.pollTimer = null;
    el.startBtn.disabled = false;
    el.stopBtn.disabled = true;
    el.stopBtn.classList.remove("btn-stop");
    setConn("ok", `Stopped · ${state.points.length.toLocaleString()} rows this session`);
    requestDraw();
  }

  async function poll() {
    if (!state.running) return;
    const started = performance.now();
    try {
      const rows = await fetchNew(state.lastId);
      if (rows.length) ingest(rows);
      if (state.running) setConn("live", rows.length ? `Live · ${state.lastSession || "session"}` : "Live · waiting for new rows");
    } catch (e) {
      if (state.running) setConn("err", `Retrying: ${e.message}`);
    }
    updateStats();
    if (!state.running) return;
    const elapsed = performance.now() - started;
    state.pollTimer = setTimeout(poll, Math.max(50, Number(cfg.pollMs) - elapsed));
  }

  function ingest(rows) {
    const now = Date.now();
    for (const r of rows) {
      const at = Date.parse(r.recorded_at);
      if (!Number.isFinite(at)) continue;
      if (state.t0 === null) state.t0 = at;
      const bump = Number(r.anomaly_detected) === 1;
      const p = {
        id: r.id, t: (at - state.t0) / 1000, at,
        ax: Number(r.ax), ay: Number(r.ay), az: Number(r.az), bump,
        lat: r.latitude, lon: r.longitude,
        session: `${r.session_date} #${r.session_number}`,
      };
      state.points.push(p);
      state.lastId = Math.max(state.lastId, r.id);
      state.lastSession = p.session;

      if (bump) {
        const last = state.spans[state.spans.length - 1];
        if (state.inBump && last) last.t1 = p.t;
        else {
          state.spans.push({ t0: p.t, t1: p.t });
          state.events.push({ t: p.t, at, lat: p.lat, lon: p.lon, session: p.session });
          signalBump(p);
        }
        state.inBump = true;
      } else {
        state.inBump = false;
      }
      state.rateWindow.push(now);
    }
    // Trim to the drawable window (plus slack) and a hard cap.
    const tNow = state.points[state.points.length - 1]?.t ?? 0;
    const cutoff = tNow - state.windowSeconds - 5;
    let i = 0;
    while (i < state.points.length && state.points[i].t < cutoff) i++;
    if (i > 0) state.points.splice(0, i);
    if (state.points.length > MAX_POINTS) state.points.splice(0, state.points.length - MAX_POINTS);
    state.spans = state.spans.filter((s) => s.t1 >= cutoff);

    el.sessionChip.textContent = state.lastSession;
    renderEvents();
    renderTable();
    requestDraw();
  }

  // ---------- stats ----------
  function updateStats() {
    const now = Date.now();
    state.rateWindow = state.rateWindow.filter((ts) => now - ts <= 2000);
    const last = state.points[state.points.length - 1];
    el.statRows.textContent = state.points.length.toLocaleString();
    el.statRate.textContent = state.running ? Math.round(state.rateWindow.length / 2) : "0";
    el.statLag.textContent = last && state.running ? Math.max(0, (now - last.at) / 1000).toFixed(1) : "—";
    el.statBumps.textContent = state.events.length;
    el.eventsCount.textContent = state.events.length;
    if (last) {
      el.now.ax.textContent = fmt(last.ax);
      el.now.ay.textContent = fmt(last.ay);
      el.now.az.textContent = fmt(last.az);
    }
  }
  const fmt = (v) => (Number.isFinite(v) ? v.toFixed(3) : "—");

  // ---------- anomaly signal ----------
  let signalTimer = null;
  function setSignalIdle() {
    el.signal.classList.remove("active");
    el.signalText.textContent = state.running ? "Watching for anomalies" : "Idle";
    el.signalSub.textContent = "Detect Bump on the phone flags rows";
  }
  function signalBump(p) {
    el.signal.classList.remove("active");
    void el.signal.offsetWidth; // restart animation
    el.signal.classList.add("active");
    el.signalText.textContent = "Bump detected";
    el.signalSub.textContent = `t = ${p.t.toFixed(1)} s · ${p.session}`;
    el.flash.classList.remove("on");
    void el.flash.offsetWidth;
    el.flash.classList.add("on");
    if (el.soundChk.checked) beep();
    clearTimeout(signalTimer);
    signalTimer = setTimeout(() => {
      el.signal.classList.remove("active");
      el.signalText.textContent = "Watching for anomalies";
      el.signalSub.textContent = `last bump at t = ${p.t.toFixed(1)} s`;
    }, 2500);
  }
  let audioCtx = null;
  function beep() {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const o = audioCtx.createOscillator();
      const g = audioCtx.createGain();
      o.type = "sine"; o.frequency.value = 660;
      g.gain.setValueAtTime(0.0001, audioCtx.currentTime);
      g.gain.exponentialRampToValueAtTime(0.25, audioCtx.currentTime + 0.01);
      g.gain.exponentialRampToValueAtTime(0.0001, audioCtx.currentTime + 0.25);
      o.connect(g).connect(audioCtx.destination);
      o.start(); o.stop(audioCtx.currentTime + 0.26);
    } catch {}
  }

  // ---------- lists ----------
  function renderEvents() {
    el.events.replaceChildren();
    if (!state.events.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No bumps yet this session.";
      el.events.appendChild(li);
      return;
    }
    for (const ev of state.events.slice(-30).reverse()) {
      const li = document.createElement("li");
      const t = document.createElement("span"); t.className = "t"; t.textContent = `t = ${ev.t.toFixed(1)} s`;
      const when = document.createElement("span"); when.textContent = new Date(ev.at).toLocaleTimeString();
      const loc = document.createElement("span"); loc.className = "loc";
      loc.textContent = Number.isFinite(Number(ev.lat)) && ev.lat !== null
        ? `${Number(ev.lat).toFixed(5)}, ${Number(ev.lon).toFixed(5)}` : "no GPS fix";
      li.append(t, when, loc);
      el.events.appendChild(li);
    }
  }
  function renderTable() {
    el.table.replaceChildren();
    const rows = state.points.slice(-12).reverse();
    if (!rows.length) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5; td.className = "empty"; td.textContent = state.running ? "Waiting for rows…" : "Start a session to see rows.";
      tr.appendChild(td); el.table.appendChild(tr);
      return;
    }
    for (const p of rows) {
      const tr = document.createElement("tr");
      if (p.bump) tr.className = "bump";
      for (const v of [p.t.toFixed(2), fmt(p.ax), fmt(p.ay), fmt(p.az), p.bump ? "1" : "0"]) {
        const td = document.createElement("td"); td.textContent = v; tr.appendChild(td);
      }
      el.table.appendChild(tr);
    }
  }

  // ---------- charts ----------
  class LineChart {
    constructor(canvas, key) {
      this.canvas = canvas;
      this.key = key;
      this.ctx = canvas.getContext("2d");
      this.pad = { l: 48, r: 14, t: 10, b: 24 };
      this.yMin = -1; this.yMax = 1;       // eased scale
      this.resize();
      canvas.addEventListener("pointermove", (e) => this.onPointer(e));
      canvas.addEventListener("pointerleave", () => { state.hoverT = null; hideTooltip(); requestDraw(); });
    }
    resize() {
      const dpr = window.devicePixelRatio || 1;
      const rect = this.canvas.getBoundingClientRect();
      this.w = Math.max(200, rect.width); this.h = Math.max(120, rect.height);
      this.canvas.width = Math.round(this.w * dpr); this.canvas.height = Math.round(this.h * dpr);
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    xRange() {
      const W = state.windowSeconds;
      const last = state.points[state.points.length - 1];
      const tEnd = last ? Math.max(last.t, W) : W;
      return [tEnd - W, tEnd];
    }
    xOf(t, [x0, x1]) { return this.pad.l + ((t - x0) / (x1 - x0)) * (this.w - this.pad.l - this.pad.r); }
    yOf(v) { return this.pad.t + (1 - (v - this.yMin) / (this.yMax - this.yMin)) * (this.h - this.pad.t - this.pad.b); }
    updateScale(visible) {
      let lo = Infinity, hi = -Infinity;
      for (const p of visible) { const v = p[this.key]; if (v < lo) lo = v; if (v > hi) hi = v; }
      if (!visible.length) { lo = -1; hi = 1; }
      if (hi - lo < 0.2) { const m = (hi + lo) / 2; lo = m - 0.1; hi = m + 0.1; }
      const pad = (hi - lo) * 0.12;
      const tLo = lo - pad, tHi = hi + pad;
      // Grow instantly so nothing clips; shrink over ~2 s (time-based, so it does not depend on redraw rate).
      const now = performance.now();
      const dt = this.lastScaleAt ? (now - this.lastScaleAt) / 1000 : 0;
      this.lastScaleAt = now;
      const a = 1 - Math.exp(-dt / 0.8);
      this.yMin = tLo < this.yMin ? tLo : this.yMin + (tLo - this.yMin) * a;
      this.yMax = tHi > this.yMax ? tHi : this.yMax + (tHi - this.yMax) * a;
      // Keep easing while the scale is still settling even if no new rows arrive.
      if (Math.abs(tLo - this.yMin) > 1e-4 || Math.abs(tHi - this.yMax) > 1e-4) setTimeout(requestDraw, 60);
    }
    draw() {
      const c = this.ctx, w = this.w, h = this.h, pad = this.pad;
      const xr = this.xRange();
      const visible = state.points.filter((p) => p.t >= xr[0] && p.t <= xr[1]);
      this.updateScale(visible);

      c.clearRect(0, 0, w, h);
      c.font = "11px 'JetBrains Mono', Menlo, monospace";
      c.textBaseline = "middle";

      // bump observation bands
      c.fillStyle = PALETTE.alertWash;
      c.strokeStyle = PALETTE.earthBrown;
      c.lineWidth = 1;
      for (const s of state.spans) {
        if (s.t1 < xr[0] || s.t0 > xr[1]) continue;
        const x0 = this.xOf(Math.max(s.t0, xr[0]), xr), x1 = this.xOf(Math.min(s.t1, xr[1]), xr);
        c.fillRect(x0, pad.t, Math.max(2, x1 - x0), h - pad.t - pad.b);
        c.beginPath(); c.moveTo(x0 + 0.5, pad.t); c.lineTo(x0 + 0.5, h - pad.b); c.stroke();
      }

      // y grid + labels
      const ticks = niceTicks(this.yMin, this.yMax, 4);
      c.strokeStyle = PALETTE.grid; c.fillStyle = PALETTE.text3; c.textAlign = "right";
      for (const v of ticks) {
        const y = Math.round(this.yOf(v)) + 0.5;
        if (y < pad.t || y > h - pad.b) continue;
        c.beginPath(); c.moveTo(pad.l, y); c.lineTo(w - pad.r, y); c.stroke();
        c.fillText(formatTick(v, ticks), pad.l - 8, y);
      }
      // x labels
      c.textAlign = "center"; c.textBaseline = "top";
      const xStep = niceStep((xr[1] - xr[0]) / 5);
      for (let t = Math.ceil(xr[0] / xStep) * xStep; t <= xr[1] + 1e-9; t += xStep) {
        const x = this.xOf(t, xr);
        c.fillStyle = PALETTE.text3;
        c.fillText(`${t.toFixed(xStep < 1 ? 1 : 0)} s`, x, h - pad.b + 6);
      }
      // baseline (0 g) a touch darker if in range
      if (this.yMin < 0 && this.yMax > 0) {
        const y = Math.round(this.yOf(0)) + 0.5;
        c.strokeStyle = PALETTE.stoneGray; c.beginPath(); c.moveTo(pad.l, y); c.lineTo(w - pad.r, y); c.stroke();
      }

      // line
      if (visible.length) {
        c.strokeStyle = PALETTE.moss; c.lineWidth = 2; c.lineJoin = "round"; c.lineCap = "round";
        c.beginPath();
        for (let i = 0; i < visible.length; i++) {
          const p = visible[i];
          const x = this.xOf(p.t, xr), y = this.yOf(p[this.key]);
          if (i === 0) c.moveTo(x, y); else c.lineTo(x, y);
        }
        c.stroke();
        // end marker with surface ring
        const last = visible[visible.length - 1];
        drawDot(c, this.xOf(last.t, xr), this.yOf(last[this.key]), PALETTE.moss);
      } else {
        c.fillStyle = PALETTE.text3; c.textAlign = "center"; c.textBaseline = "middle"; c.font = "13px Inter, sans-serif";
        c.fillText(state.running ? "Waiting for rows…" : "Start a session to stream data", (pad.l + w - pad.r) / 2, (pad.t + h - pad.b) / 2);
      }

      // crosshair
      if (state.hoverT !== null && visible.length) {
        const p = nearest(visible, state.hoverT);
        const x = Math.round(this.xOf(p.t, xr)) + 0.5;
        c.strokeStyle = "rgba(21,27,26,0.45)"; c.lineWidth = 1;
        c.beginPath(); c.moveTo(x, pad.t); c.lineTo(x, h - pad.b); c.stroke();
        drawDot(c, this.xOf(p.t, xr), this.yOf(p[this.key]), PALETTE.deepGreen);
      }
    }
    onPointer(e) {
      const rect = this.canvas.getBoundingClientRect();
      const xr = this.xRange();
      const frac = (e.clientX - rect.left - this.pad.l) / (this.w - this.pad.l - this.pad.r);
      state.hoverT = xr[0] + Math.min(1, Math.max(0, frac)) * (xr[1] - xr[0]);
      showTooltip(e);
      requestDraw();
    }
  }
  function drawDot(c, x, y, color) {
    c.beginPath(); c.arc(x, y, 6, 0, Math.PI * 2); c.fillStyle = PALETTE.surface; c.fill();
    c.beginPath(); c.arc(x, y, 4, 0, Math.PI * 2); c.fillStyle = color; c.fill();
  }
  function nearest(arr, t) {
    let lo = 0, hi = arr.length - 1;
    while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid].t < t) lo = mid + 1; else hi = mid; }
    if (lo > 0 && Math.abs(arr[lo - 1].t - t) < Math.abs(arr[lo].t - t)) lo -= 1;
    return arr[lo];
  }
  function niceStep(raw) {
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }
  function niceTicks(min, max, count) {
    const step = niceStep((max - min) / count);
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Math.round(v / step) * step);
    return out;
  }
  function formatTick(v, ticks) {
    const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
    const d = step >= 1 ? 0 : step >= 0.1 ? 1 : step >= 0.01 ? 2 : 3;
    return v.toFixed(d);
  }

  const charts = [new LineChart($("axChart"), "ax"), new LineChart($("ayChart"), "ay"), new LineChart($("azChart"), "az")];

  function showTooltip(e) {
    const xr = charts[0].xRange();
    const visible = state.points.filter((p) => p.t >= xr[0] && p.t <= xr[1]);
    if (!visible.length || state.hoverT === null) { hideTooltip(); return; }
    const p = nearest(visible, state.hoverT);
    el.tooltip.replaceChildren();
    const t = document.createElement("div"); t.className = "tt-t"; t.textContent = `t = ${p.t.toFixed(2)} s`;
    el.tooltip.appendChild(t);
    for (const [label, v] of [["ax", p.ax], ["ay", p.ay], ["az", p.az]]) {
      const row = document.createElement("div"); row.className = "tt-row";
      const s = document.createElement("strong"); s.textContent = `${fmt(v)} g`;
      const l = document.createElement("span"); l.textContent = label;
      row.append(s, l); el.tooltip.appendChild(row);
    }
    if (p.bump) { const b = document.createElement("div"); b.className = "tt-bump"; b.textContent = "Bump observation"; el.tooltip.appendChild(b); }
    const host = el.charts.getBoundingClientRect();
    let x = e.clientX - host.left + 14, y = e.clientY - host.top + 14;
    el.tooltip.hidden = false;
    const tw = el.tooltip.offsetWidth, th = el.tooltip.offsetHeight;
    if (x + tw > host.width - 8) x = e.clientX - host.left - tw - 14;
    if (y + th > host.height - 8) y = e.clientY - host.top - th - 14;
    el.tooltip.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
  }
  function hideTooltip() { el.tooltip.hidden = true; }

  function requestDraw() {
    if (state.needsDraw) return;
    state.needsDraw = true;
    requestAnimationFrame(() => { state.needsDraw = false; for (const ch of charts) ch.draw(); });
  }
  window.addEventListener("resize", () => { for (const ch of charts) ch.resize(); requestDraw(); });

  // ---------- settings dialog ----------
  function fillSettings() {
    el.cfgUrl.value = cfg.supabaseUrl || ""; el.cfgKey.value = cfg.supabaseKey || "";
    el.cfgTable.value = cfg.table || "readings"; el.cfgPoll.value = cfg.pollMs || 500;
  }
  function applyConfig() {
    el.tableName.textContent = cfg.table;
    el.pollMs.textContent = cfg.pollMs;
    checkConnection();
  }
  el.settingsBtn.addEventListener("click", () => { fillSettings(); el.settings.showModal(); });
  el.cfgCancel.addEventListener("click", () => el.settings.close());
  el.cfgReset.addEventListener("click", () => { localStorage.removeItem(STORAGE_KEY); cfg = loadConfig(); fillSettings(); });
  el.settingsForm.addEventListener("submit", () => {
    cfg = Object.assign({}, cfg, {
      supabaseUrl: el.cfgUrl.value.trim(), supabaseKey: el.cfgKey.value.trim(),
      table: el.cfgTable.value.trim() || "readings", pollMs: Math.max(100, Number(el.cfgPoll.value) || 500),
    });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
    applyConfig();
  });

  // ---------- wire up ----------
  el.startBtn.addEventListener("click", start);
  el.stopBtn.addEventListener("click", stop);
  el.windowSel.value = String(state.windowSeconds);
  el.windowSel.addEventListener("change", () => { state.windowSeconds = Number(el.windowSel.value); requestDraw(); });
  setInterval(() => { if (state.running) updateStats(); }, 500);

  applyConfig();
  requestDraw();
  if (AUTOSTART) start();
})();
