/* GroundTruth live telemetry dashboard.
 * Subscribes to the dashboard server's Server-Sent Events stream (server.py, GET /stream) and draws
 * ax/ay/az over time. The phone POSTs its rows to the same server about four times a second; the server
 * fans them out to every open browser, so only rows received after "Start Reading Session" are shown.
 * Every row also goes through the live bump filter (filter.js): |a| -> 3 Hz high-pass -> threshold.
 */
(() => {
  "use strict";

  const PALETTE = {
    deepGreen: "#151B1A", moss: "#80957B", earthBrown: "#897560", stoneGray: "#A7ABA4", chalk: "#F0F1EA",
    surface: "#F7F8F3", grid: "#DDE0D8", text2: "#4A524E", text3: "#7C827E", alertWash: "rgba(137,117,96,0.16)",
  };
  const STORAGE_KEY = "gt.dashboard.config";
  const MAX_POINTS = 60_000;
  // ?demo=1 synthesises a local 200 Hz stream (no server) for layout checks and hardware-free demos.
  // ?autostart=1 clicks Start Reading Session on load (kiosk screens).
  const params = new URLSearchParams(location.search);
  const DEMO = params.has("demo");
  const AUTOSTART = params.has("autostart");

  // ---------- config ----------
  const defaults = Object.assign(
    { serverUrl: "", windowSeconds: 20, hpThreshold: 0.8, hpAdaptive: false },
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
    source: null,          // EventSource on /stream while running
    demoTimer: null,
    t0: null,              // ms epoch of the first row of this session
    points: [],            // {id, t, ax, ay, az, mag, hp, thr, bump, lat, lon, session, at}
    spans: [],             // {t0, t1} bump observations (app flag)
    events: [],            // app flags
    detEvents: [],         // live filter detections {t, at, lat, lon, session, g}
    detector: null,        // GTFilter.BumpFilter for this session
    inBump: false,
    rateWindow: [],        // receive timestamps for Hz
    hoverT: null,
    windowSeconds: Number(cfg.windowSeconds) || 20,
    lastSession: null,
    needsDraw: false,
    // Smooth scrolling: the x axis follows the wall clock, held `displayLag` s behind the newest row so that
    // rows arriving in batches slide in from the right edge instead of the chart jumping once per batch.
    clockOffsetMs: null,   // Date.now() - `at` of the newest row (phone clock -> browser clock)
    lastArrival: null,     // Date.now() of the last non-empty poll
    batchGap: 1.0,         // longest interval between batches seen recently (s)
    displayLag: 1.5,       // eased value actually used for the axis (s)
    rafId: null,
  };

  // ---------- DOM ----------
  const $ = (id) => document.getElementById(id);
  const el = {
    conn: $("conn"), connText: $("connText"), startBtn: $("startBtn"), stopBtn: $("stopBtn"),
    sessionChip: $("sessionChip"), windowSel: $("windowSel"), soundChk: $("soundChk"),
    thrInput: $("thrInput"), adaptChk: $("adaptChk"), thrNow: $("thrNow"),
    statRows: $("statRows"), statRate: $("statRate"), statLag: $("statLag"), statBumps: $("statBumps"), statDet: $("statDet"),
    signal: $("signal"), signalText: $("signalText"), signalSub: $("signalSub"), flash: $("flash"),
    tooltip: $("tooltip"), charts: $("charts"), events: $("events"), eventsCount: $("eventsCount"),
    table: $("table").querySelector("tbody"),
    settings: $("settings"), settingsBtn: $("settingsBtn"), settingsForm: $("settingsForm"),
    cfgUrl: $("cfgUrl"), cfgReset: $("cfgReset"), cfgCancel: $("cfgCancel"),
    now: { ax: $("axNow"), ay: $("ayNow"), az: $("azNow"), hp: $("hpNow") },
  };

  // ---------- dashboard server (server.py) ----------
  // Blank serverUrl = the server this page came from. A page opened from the file system has no origin,
  // so it falls back to localhost:8090 until Settings says otherwise.
  function serverBase() {
    const u = String(cfg.serverUrl || "").trim().replace(/\/+$/, "");
    if (u) return u;
    return location.protocol.startsWith("http") ? location.origin : "http://localhost:8090";
  }
  function serverHost() { try { return new URL(serverBase()).host; } catch { return serverBase(); } }
  async function fetchStatus() {
    const res = await fetch(`${serverBase()}/status`, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return res.json();
  }
  function openStream() {
    const es = new EventSource(`${serverBase()}/stream`);
    es.onopen = () => { if (state.running) setConn("live", "Live · waiting for rows from the phone"); };
    es.onmessage = (e) => {
      if (!state.running) return;
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (Array.isArray(msg.rows) && msg.rows.length) ingest(msg.rows);
      else if (msg.event === "start") {
        state.lastSession = msg.session || state.lastSession;
        el.sessionChip.textContent = state.lastSession || "Session";
        setConn("live", `Live · phone started ${msg.session || "a session"}`);
      } else if (msg.event === "stop") {
        setConn("live", `Live · phone stopped ${msg.session || "the session"}${Number.isFinite(msg.rows_sent) ? ` · ${Number(msg.rows_sent).toLocaleString()} rows` : ""}`);
      }
    };
    // EventSource reconnects by itself and sends Last-Event-ID, so the server replays what was missed.
    es.onerror = () => { if (state.running) setConn("err", `Reconnecting to ${serverHost()}…`); };
    return es;
  }

  // ---------- demo generator ----------
  const demo = { id: 0, start: null, lastGen: null, nextBumpAt: 3, bumpUntil: -1, bumpT0: 0 };
  function demoRows() {
    const now = Date.now();
    if (demo.start === null) { demo.start = now; demo.lastGen = now; return []; }
    const rows = [];
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
        id: ++demo.id, at: ts, ax, ay, az, gx: 0, gy: 0, gz: 0,
        lat: 40.7128 + t * 1e-5, lon: -74.006 + t * 1e-5, flag: inBump ? 1 : 0, session: "demo",
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
    if (DEMO) { setConn("ok", "Demo · synthetic data, no server"); return true; }
    try {
      const s = await fetchStatus();
      const phone = s.phone && Number.isFinite(s.phone.seen_ms_ago)
        ? ` · phone seen ${s.phone.seen_ms_ago < 5000 ? "just now" : `${Math.round(s.phone.seen_ms_ago / 1000)} s ago`}`
        : " · no phone yet";
      setConn("ok", `Connected · ${serverHost()}${phone}`);
      return true;
    } catch (e) {
      setConn("err", `No dashboard server at ${serverHost()}: run server.py`);
      return false;
    }
  }

  // ---------- session ----------
  async function start() {
    if (state.running) return;
    el.startBtn.disabled = true;
    if (!(await checkConnection())) { el.startBtn.disabled = false; return; }
    Object.assign(state, {
      running: true, t0: null, points: [], spans: [], events: [], detEvents: [], inBump: false,
      rateWindow: [], hoverT: null, lastSession: null,
      detector: new GTFilter.BumpFilter({ threshold: currentThreshold(), adaptive: el.adaptChk.checked }),
      clockOffsetMs: null, lastArrival: null, batchGap: 1.0, displayLag: 1.5,
    });
    startFrames();
    el.stopBtn.disabled = false;
    el.stopBtn.classList.add("btn-stop");
    el.sessionChip.textContent = "Waiting for rows…";
    setSignalIdle();
    renderEvents();
    renderTable();
    updateStats();
    setConn("live", "Live · waiting for rows from the phone");
    requestDraw();
    if (DEMO) {
      demoRows();   // primes the generator clock
      state.demoTimer = setInterval(() => { const rows = demoRows(); if (rows.length) ingest(rows); }, 200);
    } else {
      state.source = openStream();
    }
  }

  function stop() {
    state.running = false;
    stopFrames();
    if (state.source) { state.source.close(); state.source = null; }
    if (state.demoTimer) { clearInterval(state.demoTimer); state.demoTimer = null; }
    el.startBtn.disabled = false;
    el.stopBtn.disabled = true;
    el.stopBtn.classList.remove("btn-stop");
    setConn("ok", `Stopped · ${state.points.length.toLocaleString()} rows this session`);
    requestDraw();
  }

  function ingest(rows) {
    const now = Date.now();
    const newest = Number(rows[rows.length - 1].at);
    if (Number.isFinite(newest)) {
      // Phone clock -> browser clock. Smoothed, so latency jitter does not nudge the axis.
      const off = now - newest;
      state.clockOffsetMs = state.clockOffsetMs === null ? off : state.clockOffsetMs + (off - state.clockOffsetMs) * 0.2;
      // Longest gap between batches recently: the axis must trail the data by at least this much to stay smooth.
      if (state.lastArrival !== null) state.batchGap = Math.max((now - state.lastArrival) / 1000, state.batchGap * 0.9);
      state.lastArrival = now;
    }
    for (const r of rows) {
      const at = Number(r.at);
      if (!Number.isFinite(at)) continue;
      if (state.t0 === null) state.t0 = at;
      const bump = Number(r.flag) === 1;
      const p = {
        id: r.id, t: (at - state.t0) / 1000, at,
        ax: Number(r.ax), ay: Number(r.ay), az: Number(r.az), bump,
        lat: r.lat ?? null, lon: r.lon ?? null,
        session: r.session || "session",
      };
      if (state.detector && [p.ax, p.ay, p.az].every(Number.isFinite)) {
        const o = state.detector.step(p.t, p.ax, p.ay, p.az);
        p.mag = o.mag; p.hp = o.ahp; p.thr = o.threshold;
        if (o.event) {
          state.detEvents.push({ t: p.t, at, lat: p.lat, lon: p.lon, session: p.session, g: o.ahp });
          signalDetect(p, o);
        }
      }
      state.points.push(p);
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
    if (state.detEvents.length > 500) state.detEvents.splice(0, state.detEvents.length - 500);

    el.sessionChip.textContent = state.lastSession;
    setConn("live", `Live · ${state.lastSession}`);
    renderEvents();
    renderTable();
    updateStats();
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
    el.statDet.textContent = state.detEvents.length;
    el.eventsCount.textContent = state.events.length + state.detEvents.length;
    if (last) {
      el.now.ax.textContent = fmt(last.ax);
      el.now.ay.textContent = fmt(last.ay);
      el.now.az.textContent = fmt(last.az);
      el.now.hp.textContent = Number.isFinite(last.hp) ? `${fmt(last.hp)} g · bar ${Number.isFinite(last.thr) ? last.thr.toFixed(2) : "—"} g` : "—";
    }
  }
  const fmt = (v) => (Number.isFinite(v) ? v.toFixed(3) : "—");

  // ---------- anomaly signal ----------
  let signalTimer = null;
  function setSignalIdle() {
    el.signal.classList.remove("active");
    el.signalText.textContent = state.running ? "No bump" : "Idle";
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
      el.signalText.textContent = "No bump";
      el.signalSub.textContent = `last bump at t = ${p.t.toFixed(1)} s`;
    }, 2500);
  }
  function signalDetect(p, o) {
    el.signal.classList.remove("active");
    void el.signal.offsetWidth;
    el.signal.classList.add("active");
    el.signalText.textContent = "Bump detected · filter";
    el.signalSub.textContent = `|hp| ${o.ahp.toFixed(2)} g > ${o.threshold.toFixed(2)} g · t = ${p.t.toFixed(1)} s`;
    el.flash.classList.remove("on");
    void el.flash.offsetWidth;
    el.flash.classList.add("on");
    if (el.soundChk.checked) beep();
    clearTimeout(signalTimer);
    signalTimer = setTimeout(() => {
      el.signal.classList.remove("active");
      el.signalText.textContent = "No bump";
      el.signalSub.textContent = `last filter detection at t = ${p.t.toFixed(1)} s`;
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
    const all = [
      ...state.events.map((e) => Object.assign({ kind: "app" }, e)),
      ...state.detEvents.map((e) => Object.assign({ kind: "filter" }, e)),
    ].sort((a, b) => a.t - b.t);
    if (!all.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "No bumps yet this session.";
      el.events.appendChild(li);
      return;
    }
    for (const ev of all.slice(-30).reverse()) {
      const li = document.createElement("li"); li.className = ev.kind;
      const t = document.createElement("span"); t.className = "t"; t.textContent = `t = ${ev.t.toFixed(1)} s`;
      const when = document.createElement("span");
      when.textContent = ev.kind === "filter" ? `filter · ${ev.g.toFixed(2)} g · ${new Date(ev.at).toLocaleTimeString()}` : `phone flag · ${new Date(ev.at).toLocaleTimeString()}`;
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
      td.colSpan = 6; td.className = "empty"; td.textContent = state.running ? "Waiting for rows…" : "Start a session to see rows.";
      tr.appendChild(td); el.table.appendChild(tr);
      return;
    }
    for (const p of rows) {
      const tr = document.createElement("tr");
      if (p.bump) tr.className = "bump";
      for (const v of [p.t.toFixed(2), fmt(p.ax), fmt(p.ay), fmt(p.az), fmt(p.hp), p.bump ? "1" : "0"]) {
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
      let tEnd = last ? last.t : 0;
      if (state.running && last && state.clockOffsetMs !== null) {
        // Phone time "now" as seen from the browser, minus the display lag, in session seconds.
        const phoneNow = Date.now() - state.clockOffsetMs;
        const tClock = (phoneNow - state.t0) / 1000 - state.displayLag;
        tEnd = Math.max(tClock, last.t - 0.25);   // never hide more than a quarter second of real data
      }
      tEnd = Math.max(tEnd, W);
      return [tEnd - W, tEnd];
    }
    xOf(t, [x0, x1]) { return this.pad.l + ((t - x0) / (x1 - x0)) * (this.w - this.pad.l - this.pad.r); }
    yOf(v) { return this.pad.t + (1 - (v - this.yMin) / (this.yMax - this.yMin)) * (this.h - this.pad.t - this.pad.b); }
    updateScale(visible) {
      let lo = Infinity, hi = -Infinity;
      for (const p of visible) {
        const v = p[this.key]; if (!Number.isFinite(v)) continue; if (v < lo) lo = v; if (v > hi) hi = v;
        if (this.key === "hp" && Number.isFinite(p.thr) && p.thr > hi) hi = p.thr;
      }
      if (!visible.length || lo === Infinity) { lo = this.key === "hp" ? 0 : -1; hi = 1; }
      if (this.key === "hp") lo = 0;
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
          if (!Number.isFinite(p[this.key])) continue;
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

  const charts = [new LineChart($("axChart"), "ax"), new LineChart($("ayChart"), "ay"), new LineChart($("azChart"), "az"), new LineChart($("hpChart"), "hp")];

  function showTooltip(e) {
    const xr = charts[0].xRange();
    const visible = state.points.filter((p) => p.t >= xr[0] && p.t <= xr[1]);
    if (!visible.length || state.hoverT === null) { hideTooltip(); return; }
    const p = nearest(visible, state.hoverT);
    el.tooltip.replaceChildren();
    const t = document.createElement("div"); t.className = "tt-t"; t.textContent = `t = ${p.t.toFixed(2)} s`;
    el.tooltip.appendChild(t);
    for (const [label, v] of [["ax", p.ax], ["ay", p.ay], ["az", p.az], ["|hp|", p.hp]]) {
      const row = document.createElement("div"); row.className = "tt-row";
      const s = document.createElement("strong"); s.textContent = `${fmt(v)} g`;
      const l = document.createElement("span"); l.textContent = label;
      row.append(s, l); el.tooltip.appendChild(row);
    }
    if (p.bump) { const b = document.createElement("div"); b.className = "tt-bump"; b.textContent = "Bump observation"; el.tooltip.appendChild(b); }
    if (state.detEvents.some((ev) => Math.abs(ev.t - p.t) < 0.15)) { const b = document.createElement("div"); b.className = "tt-bump"; b.textContent = "Filter detection"; el.tooltip.appendChild(b); }
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
    if (state.rafId !== null) return;          // the frame loop is already drawing every frame
    if (state.needsDraw) return;
    state.needsDraw = true;
    requestAnimationFrame(() => { state.needsDraw = false; for (const ch of charts) ch.draw(); });
  }
  let lastFrame = 0;
  function frame(ts) {
    if (!state.running) { state.rafId = null; return; }
    const dt = lastFrame ? Math.min(0.1, (ts - lastFrame) / 1000) : 0;
    lastFrame = ts;
    // Ease the display lag toward the measured batch latency plus a small margin, so the axis never jumps.
    const target = Math.min(8, Math.max(0.4, state.batchGap * 1.15 + 0.25));
    state.displayLag += (target - state.displayLag) * (1 - Math.exp(-dt / 1.5));
    for (const ch of charts) ch.draw();
    state.rafId = requestAnimationFrame(frame);
  }
  function startFrames() { if (state.rafId === null) { lastFrame = 0; state.rafId = requestAnimationFrame(frame); } }
  function stopFrames() { if (state.rafId !== null) { cancelAnimationFrame(state.rafId); state.rafId = null; } requestDraw(); }
  window.addEventListener("resize", () => { for (const ch of charts) ch.resize(); requestDraw(); });

  // ---------- settings dialog ----------
  function fillSettings() {
    el.cfgUrl.value = cfg.serverUrl || "";
    el.cfgUrl.placeholder = serverBase();
  }
  function applyConfig() {
    checkConnection();
  }
  el.settingsBtn.addEventListener("click", () => { fillSettings(); el.settings.showModal(); });
  el.cfgCancel.addEventListener("click", () => el.settings.close());
  el.cfgReset.addEventListener("click", () => { localStorage.removeItem(STORAGE_KEY); cfg = loadConfig(); fillSettings(); });
  el.settingsForm.addEventListener("submit", () => {
    cfg = Object.assign({}, cfg, { serverUrl: el.cfgUrl.value.trim() });
    localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg));
    applyConfig();
  });

  // ---------- bump filter controls ----------
  function currentThreshold() { return Math.max(0.05, Number(el.thrInput.value) || Number(cfg.hpThreshold) || 0.8); }
  function applyFilterControls() {
    el.thrInput.disabled = el.adaptChk.checked;
    el.thrNow.textContent = el.adaptChk.checked ? "median + 15 × MAD, last 10 s" : `${currentThreshold().toFixed(2)} g`;
    if (state.detector) { state.detector.setThreshold(currentThreshold()); state.detector.setAdaptive(el.adaptChk.checked); }
  }
  el.thrInput.value = Number(cfg.hpThreshold) || 0.8;
  el.adaptChk.checked = Boolean(cfg.hpAdaptive);
  el.thrInput.addEventListener("input", applyFilterControls);
  el.adaptChk.addEventListener("change", applyFilterControls);
  applyFilterControls();

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
