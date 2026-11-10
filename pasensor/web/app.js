"use strict";
// Read-only dashboard. It renders what the sensor process reports and decides
// nothing itself. When contact is lost it says so and removes current values.

const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const ICON = { ok: "●", candidate: "◆", warning: "▲", fault: "■", notice: "○" };
let info = null;
let frames = [];
let lastSpectrum = null;

function svg(tag, attrs, text) {
  const node = document.createElementNS(SVG, tag);
  for (const key in attrs) node.setAttribute(key, attrs[key]);
  if (text !== undefined) node.textContent = text;
  return node;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
function fmt(value, digits, unit) {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a";
  return Number(value).toFixed(digits) + (unit ? " " + unit : "");
}
function facts(node, rows) {
  clear(node);
  for (const [name, value] of rows) {
    const dt = document.createElement("dt"); dt.textContent = name;
    const dd = document.createElement("dd"); dd.textContent = value;
    node.append(dt, dd);
  }
}
function evidence(node, rows) {
  clear(node);
  const head = document.createElement("tr");
  for (const title of ["Measure", "Now", "Needs", "Result"]) { const th = document.createElement("th"); th.textContent = title; head.append(th); }
  node.append(head);
  for (const [name, now, needs, pass] of rows) {
    const tr = document.createElement("tr");
    for (const text of [name, now, needs]) { const td = document.createElement("td"); td.textContent = text; tr.append(td); }
    const verdict = document.createElement("td"); verdict.className = "verdict";
    verdict.textContent = pass === null ? "" : (pass ? "✓ met" : "✗ not met");
    tr.append(verdict); node.append(tr);
  }
}
function duration(seconds) {
  if (seconds === null || seconds === undefined) return "open";
  return seconds < 60 ? seconds.toFixed(1) + " s" : Math.floor(seconds / 60) + " min " + Math.round(seconds % 60) + " s";
}

// ---------------------------------------------------------------- charts
function frame(node, w, h, m, yMin, yMax, yStep, yLabel, xTicks) {
  clear(node);
  const px = (x) => m.l + x * (w - m.l - m.r);
  const py = (y) => m.t + (1 - (y - yMin) / (yMax - yMin)) * (h - m.t - m.b);
  for (let y = yMin; y <= yMax + 1e-9; y += yStep) {
    node.append(svg("line", { class: "gridline", x1: m.l, x2: w - m.r, y1: py(y), y2: py(y) }));
    node.append(svg("text", { x: m.l - 6, y: py(y) + 4, "text-anchor": "end" }, String(Math.round(y * 100) / 100)));
  }
  node.append(svg("line", { class: "axis", x1: m.l, x2: w - m.r, y1: h - m.b, y2: h - m.b }));
  for (const [fraction, label] of xTicks) node.append(svg("text", { x: px(fraction), y: h - m.b + 14, "text-anchor": "middle" }, label));
  node.append(svg("text", { x: m.l, y: m.t - 6 }, yLabel));
  return { px, py };
}
function path(points) {
  let d = "", pen = false;
  for (const p of points) {
    if (p === null) { pen = false; continue; }
    d += (pen ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1);
    pen = true;
  }
  return d;
}
function hover(wrapId, svgId, tipId, w, m, lookup) {
  const node = $(svgId), tip = $(tipId), wrap = $(wrapId);
  let cross = null;
  node.addEventListener("mousemove", (event) => {
    const box = node.getBoundingClientRect();
    const fraction = ((event.clientX - box.left) / box.width * w - m.l) / (w - m.l - m.r);
    const hit = fraction >= 0 && fraction <= 1 ? lookup(fraction) : null;
    if (cross) { cross.remove(); cross = null; }
    if (!hit) { tip.hidden = true; return; }
    cross = svg("line", { class: "cross", x1: hit.x, x2: hit.x, y1: m.t, y2: node.viewBox.baseVal.height - m.b });
    node.append(cross);
    tip.textContent = hit.text; tip.hidden = false;
    const wrapBox = wrap.getBoundingClientRect();
    tip.style.left = (hit.x / w * box.width + box.left - wrapBox.left) + "px";
    tip.style.top = (box.top - wrapBox.top + 24) + "px";
  });
  node.addEventListener("mouseleave", () => { tip.hidden = true; if (cross) { cross.remove(); cross = null; } });
}

const SPEC = { w: 640, h: 190, m: { l: 40, r: 10, t: 18, b: 22 } };
function drawSpectrum(status) {
  const node = $("spectrum"), spectrum = status.spectrum;
  lastSpectrum = spectrum;
  if (!spectrum || !spectrum.db.length) {
    clear(node); node.append(svg("text", { x: 320, y: 95, "text-anchor": "middle" }, "No spectrum for the current block."));
    return;
  }
  const db = spectrum.db, top = Math.ceil((Math.max(...db) + 5) / 10) * 10, bottom = top - 80;
  const span = spectrum.hz_per_point * db.length;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => [f, Math.round(f * span) + " Hz"]);
  const { px, py } = frame(node, SPEC.w, SPEC.h, SPEC.m, bottom, top, 20, "dB re full scale", ticks);
  const clip = (v) => Math.max(v, bottom);
  node.append(svg("path", { class: "s1", d: path(db.map((v, i) => [px((i + 0.5) / db.length), py(clip(v))])) }));
  const det = status.detection || {};
  for (const f of det.harmonic_freqs_hz || []) {
    const i = Math.min(Math.floor(f / spectrum.hz_per_point), db.length - 1);
    if (f <= span) node.append(svg("circle", { class: "marker", cx: px(f / span), cy: py(clip(db[i])), r: 4.5 }));
  }
  if (status.mode === "SIMULATED") node.append(svg("text", { class: "watermark", x: 330, y: 60, "text-anchor": "middle" }, "SIMULATED DATA"));
}
function window75(rows) {
  if (!rows.length) return { rows: [], end: 0 };
  const end = rows[rows.length - 1].t;
  return { rows: rows.filter((r) => r.t >= end - 75), end };
}
const LEVEL = { w: 640, h: 170, m: { l: 40, r: 10, t: 18, b: 22 } };
const SCORE = { w: 640, h: 120, m: { l: 40, r: 10, t: 18, b: 22 } };
const TIME_TICKS = [[0, "75 s ago"], [1 / 3, "50 s"], [2 / 3, "25 s"], [1, "now"]];
function drawTrends(status) {
  const { rows, end } = window75(frames);
  const level = $("level"), score = $("score");
  if (!rows.length) {
    for (const n of [level, score]) { clear(n); n.append(svg("text", { x: 320, y: 70, "text-anchor": "middle" }, "No frames yet.")); }
    return;
  }
  const values = rows.flatMap((r) => [r.level, r.floor]).filter((v) => v !== null && v > -150);
  const top = Math.ceil((Math.max(...values, -60) + 3) / 10) * 10;
  const bottom = Math.min(Math.floor((Math.min(...values, top - 30) - 3) / 10) * 10, top - 30);
  const fx = (t) => 1 - (end - t) / 75;
  let a = frame(level, LEVEL.w, LEVEL.h, LEVEL.m, bottom, top, 10, "dB re full scale, analysis band", TIME_TICKS);
  let start = null;
  rows.forEach((r, i) => {
    if (r.cand && start === null) start = r.t;
    if (start !== null && (!r.cand || i === rows.length - 1)) {
      level.insertBefore(svg("rect", { class: "band", x: a.px(fx(start)), y: LEVEL.m.t, width: Math.max(a.px(fx(r.t)) - a.px(fx(start)), 2), height: LEVEL.h - LEVEL.m.t - LEVEL.m.b, rx: 2 }), level.firstChild);
      start = null;
    }
  });
  level.append(svg("path", { class: "s2", d: path(rows.map((r) => r.floor === null ? null : [a.px(fx(r.t)), a.py(r.floor)])) }));
  level.append(svg("path", { class: "s1", d: path(rows.map((r) => r.ok ? [a.px(fx(r.t)), a.py(Math.max(r.level, bottom))] : null)) }));
  a = frame(score, SCORE.w, SCORE.h, SCORE.m, 0, 1, 0.5, "heuristic score, 0 to 1 (not a probability)", TIME_TICKS);
  const threshold = status.detection && status.detection.thresholds ? status.detection.thresholds.harmonic_score : null;
  if (threshold !== null) {
    score.append(svg("line", { class: "thr", x1: SCORE.m.l, x2: SCORE.w - SCORE.m.r, y1: a.py(threshold), y2: a.py(threshold) }));
    score.append(svg("text", { x: SCORE.w - SCORE.m.r - 4, y: a.py(threshold) - 4, "text-anchor": "end" }, "threshold " + threshold));
  }
  score.append(svg("path", { class: "s1", d: path(rows.map((r) => r.ok ? [a.px(fx(r.t)), a.py(r.score)] : null)) }));
  if (status.mode === "SIMULATED") level.append(svg("text", { class: "watermark", x: 330, y: 60, "text-anchor": "middle" }, "SIMULATED DATA"));
}
function nearestRow(fraction) {
  const { rows, end } = window75(frames);
  if (!rows.length) return null;
  const t = end - (1 - fraction) * 75;
  return rows.reduce((best, r) => Math.abs(r.t - t) < Math.abs(best.t - t) ? r : best, rows[0]);
}
hover("spectrum-wrap", "spectrum", "spectrum-tip", SPEC.w, SPEC.m, (fraction) => {
  if (!lastSpectrum) return null;
  const i = Math.min(Math.floor(fraction * lastSpectrum.db.length), lastSpectrum.db.length - 1);
  return { x: SPEC.m.l + fraction * (SPEC.w - SPEC.m.l - SPEC.m.r), text: Math.round((i + 0.5) * lastSpectrum.hz_per_point) + " Hz: " + lastSpectrum.db[i].toFixed(1) + " dB" };
});
hover("level-wrap", "level", "level-tip", LEVEL.w, LEVEL.m, (fraction) => {
  const r = nearestRow(fraction); if (!r) return null;
  return { x: LEVEL.m.l + fraction * (LEVEL.w - LEVEL.m.l - LEVEL.m.r), text: "level " + fmt(r.level, 1, "dB") + ", floor " + fmt(r.floor, 1, "dB") + (r.cand ? ", candidate" : "") };
});
hover("score-wrap", "score", "score-tip", SCORE.w, SCORE.m, (fraction) => {
  const r = nearestRow(fraction); if (!r) return null;
  return { x: SCORE.m.l + fraction * (SCORE.w - SCORE.m.l - SCORE.m.r), text: "score " + fmt(r.score, 2) + ", harmonics " + r.count };
});

function drawMeters(audio) {
  const node = $("meters"); clear(node);
  const levels = audio && audio.per_mic_dbfs ? audio.per_mic_dbfs : [];
  if (!levels.length) { node.append(svg("text", { x: 150, y: 50, "text-anchor": "middle" }, "No current audio block.")); return; }
  levels.forEach((value, i) => {
    const y = 6 + i * 22, width = Math.max(0, Math.min(1, (value + 100) / 100)) * 190;
    node.append(svg("text", { x: 0, y: y + 12 }, "mic " + (i + 1)));
    node.append(svg("rect", { class: "track", x: 44, y: y, width: 190, height: 14, rx: 4 }));
    node.append(svg("rect", { class: "bar", x: 44, y: y, width: width, height: 14, rx: 4 }));
    node.append(svg("text", { class: "val", x: 240, y: y + 12 }, value.toFixed(0) + " dBFS"));
  });
}

function drawDial(status) {
  const node = $("dial"); clear(node);
  const R = 100, view = status.operator.bearing, b = status.bearing || {};
  const point = (deg, r) => [r * Math.cos(deg * Math.PI / 180), -r * Math.sin(deg * Math.PI / 180)];
  node.append(svg("circle", { class: "rim", cx: 0, cy: 0, r: R }));
  for (let deg = 0; deg < 360; deg += 30) {
    const [x1, y1] = point(deg, R - (deg % 90 === 0 ? 10 : 5)), [x2, y2] = point(deg, R);
    node.append(svg("line", { class: "tick", x1, y1, x2, y2 }));
  }
  for (const [deg, label] of [[0, "+X 0"], [90, "+Y 90"], [180, "180"], [270, "270"]]) {
    const [x, y] = point(deg, R + 16);
    node.append(svg("text", { class: "axis-label", x, y: y + 4, "text-anchor": "middle" }, label));
  }
  if (info && info.calibrated && info.site && info.site.heading_deg !== null && status.mode !== "SIMULATED") {
    const north = info.site.face_up ? 90 - info.site.heading_deg : info.site.heading_deg - 90;
    const [x, y] = point(north, R - 18);
    node.append(svg("text", { class: "axis-label", x, y: y + 4, "text-anchor": "middle" }, "N"));
  }
  if (!status.stale && frames.length) {
    const end = frames[frames.length - 1].t;
    for (const r of frames) {
      if (r.brg === null || end - r.t > 30) continue;
      const [x, y] = point(r.brg, R);
      node.append(svg("circle", { class: "trail", cx: x, cy: y, r: 3, opacity: (0.15 + 0.6 * (1 - (end - r.t) / 30)).toFixed(2) }));
    }
  }
  if (view.show_needle && b.bearing_local_deg !== null && b.bearing_local_deg !== undefined) {
    const [x, y] = point(b.bearing_local_deg, R - 6);
    node.append(svg("line", { class: "needle", x1: 0, y1: 0, x2: x, y2: y }));
  } else {
    node.append(svg("text", { class: "none", x: 0, y: 34, "text-anchor": "middle" }, "no bearing"));
  }
  node.append(svg("circle", { class: "hub", cx: 0, cy: 0, r: 5 }));
  if (status.mode === "SIMULATED") node.append(svg("text", { class: "watermark", x: 0, y: -30, "text-anchor": "middle" }, "SIMULATED"));
}

// ---------------------------------------------------------------- render
function render(payload) {
  const status = payload.status, view = status.operator;
  document.body.classList.remove("no-contact");
  document.body.classList.toggle("stale", !!status.stale);
  const mode = $("mode"); mode.textContent = view.mode_label; mode.dataset.mode = status.mode || "";
  $("node").textContent = status.node_id || "node";
  $("version").textContent = status.software_version ? "software " + status.software_version : "";
  $("data-time").textContent = status.frame_end_utc ? "data time " + status.frame_end_utc.replace("T", " ") : "no data yet";
  $("age").textContent = "result age " + fmt(status.age_s, 1, "s") + (status.time_quality ? " | clock: " + status.time_quality.replace("_", " ") : "");
  const headline = $("headline"); headline.dataset.severity = view.severity;
  $("headline-icon").textContent = ICON[view.severity] || ICON.notice;
  $("headline-text").textContent = view.headline;
  const flags = $("flags"); clear(flags);
  for (const flag of view.flags) { const span = document.createElement("span"); span.className = "flag"; span.textContent = flag.replace(/_/g, " "); flags.append(span); }

  const acq = status.acquisition || {}, source = acq.source || {};
  $("acq-status").textContent = { OK: "● Receiving audio", OVERFLOW: "▲ Samples lost", DISCONNECTED: "■ Not connected", STALLED: "■ No new audio", FAULT: "■ Fault", IDLE: "○ Idle" }[acq.status] || "unknown";
  facts($("acq-facts"), [
    ["Source", source.kind === "live" ? (source.device || "capture device") + " (" + (source.hostapi || "") + ")" : source.kind === "replay" ? "file " + source.file + ", " + fmt(source.position_seconds, 0) + " of " + fmt(source.total_seconds, 0, "s") : source.kind === "simulated" ? "synthetic scene '" + source.scenario + "'" : "none"],
    ["Format", (acq.capture_channels || "?") + " channels at " + (acq.sample_rate || "?") + " Hz, raw microphones on " + JSON.stringify(acq.raw_indices || [])],
    ["Audio received", fmt((acq.frames_total || 0) / (acq.sample_rate || 1), 0, "s")],
    ["Overflows / gaps", (acq.overflow_count || 0) + " / " + (acq.gap_count || 0)],
    ["Samples dropped in queue", String(acq.queue_dropped_frames || 0)],
    ["Restarts / faults", (acq.restarts || 0) + " / " + (acq.fault_count || 0)],
    ["Last fault", acq.last_fault ? acq.last_fault_kind + " at " + acq.last_fault_utc + ": " + acq.last_fault : "none"],
  ]);

  const audio = status.audio;
  $("audio-status").textContent = !audio ? "○ No current block" : audio.status === "OK" ? "● Usable" : "▲ " + audio.status.replace(/_/g, " ");
  drawMeters(audio);
  facts($("audio-facts"), audio ? [
    ["Peak", fmt(audio.peak_dbfs, 1, "dBFS")], ["Clipped samples", String(audio.clipped_samples)],
    ["Band level", fmt(audio.band_level_dbfs, 1, "dBFS")], ["Warnings", (audio.warnings || []).join(", ").replace(/_/g, " ") || "none"],
  ] : []);

  const proc = status.processing || {}, rec = status.recording || {}, config = status.config || {};
  facts($("proc-facts"), [
    ["Processing load", fmt(100 * (proc.load_fraction || 0), 1, "% of real time")],
    ["Analysis time", fmt(proc.dsp_ms, 1, "ms") + " now, " + fmt(proc.dsp_ms_max, 1, "ms") + " max, per " + fmt(proc.hop_ms, 0, "ms") + " step"],
    ["Raw audio recording", rec.enabled ? "ON, " + fmt(rec.seconds, 0, "s") + " in " + rec.directory : (rec.error ? "STOPPED: " + rec.error : "off")],
    ["Bearing calibration", config.calibrated ? "record " + config.calibration_record : "not done"],
    ["Configuration", (config.sha256 || "").slice(0, 12)],
  ]);
  $("identity-label").textContent = view.identity.label;
  $("identity-caption").textContent = view.identity.caption;

  $("cand-label").textContent = view.candidate.label;
  $("cand-caption").textContent = view.candidate.caption;
  const det = status.detection, th = det && det.thresholds;
  evidence($("cand-evidence"), det && th ? [
    ["Level above ambient floor", fmt(det.level_above_floor_db, 1, "dB"), "at least " + th.minimum_db_above_floor + " dB", det.level_above_floor_db === null ? false : det.level_above_floor_db >= th.minimum_db_above_floor],
    ["Harmonics standing out", det.harmonic_count + " of " + det.harmonics_considered, "at least " + th.min_harmonics, det.harmonic_count >= th.min_harmonics],
    ["Harmonic comb score (0 to 1)", fmt(det.harmonic_score, 2), "at least " + th.harmonic_score, det.harmonic_score >= th.harmonic_score],
    ["Qualifying frames", det.qualifying_frames + " of last " + det.window_frames, "at least " + th.onset_frames + " to start", det.qualifying_frames >= th.onset_frames],
    ["Estimated fundamental", fmt(det.f0_hz, 1, "Hz"), "information only", null],
    ["Ambient floor", det.floor_ready ? fmt(det.floor_dbfs, 1, "dBFS") + " from " + fmt(det.floor_seconds, 0, "s") : "measuring", "information only", null],
  ] : []);
  drawSpectrum(status);

  $("bearing-label").textContent = view.bearing.label;
  $("bearing-caption").textContent = view.bearing.caption;
  const badges = $("bearing-badges"); clear(badges);
  for (const badge of view.bearing.badges) { const span = document.createElement("span"); span.className = "badge"; span.textContent = badge; badges.append(span); }
  const reasons = $("bearing-reasons"); clear(reasons);
  for (const reason of view.bearing.reasons) { const li = document.createElement("li"); li.textContent = reason; reasons.append(li); }
  drawDial(status);
  const b = status.bearing, m = b && b.metrics, t = b && b.thresholds;
  evidence($("bearing-evidence"), m && t ? [
    ["Steered on", b.basis === "HARMONIC_BINS" ? "harmonic bins" : "all coherent bins", "information only", null],
    ["Peak fit (0 to 1)", fmt(m.srp_peak, 2), "at least " + t.srp_peak, m.srp_peak >= t.srp_peak],
    ["Peak versus noise", fmt(m.srp_z, 1), "at least " + t.srp_z, m.srp_z >= t.srp_z],
    ["Contrast", fmt(m.srp_contrast, 2), "at least " + t.srp_contrast, m.srp_contrast >= t.srp_contrast],
    ["Second direction ratio", fmt(m.second_peak_ratio, 2), "at most " + t.second_peak_ratio, m.second_peak_ratio !== null && m.second_peak_ratio <= t.second_peak_ratio],
    ["Coherent bandwidth", fmt(m.coherent_bandwidth_hz, 0, "Hz"), "at least " + t.coherent_bandwidth_hz + " Hz", m.coherent_bandwidth_hz >= t.coherent_bandwidth_hz],
    ["Arrival-time misfit", fmt(m.tdoa_residual_us, 1, "us"), "at most " + t.tdoa_residual_us + " us", m.tdoa_residual_us !== null && m.tdoa_residual_us <= t.tdoa_residual_us],
    ["Recent frames agreeing", String(m.frames_agreeing), "at least " + t.frames_agreeing, m.frames_agreeing >= t.frames_agreeing],
    ["Elevation hint (coarse)", fmt(m.elevation_hint_deg, 0, "deg"), "not an output", null],
  ] : []);
  drawTrends(status);

  const body = $("events").tBodies[0]; clear(body);
  for (const event of payload.events || []) {
    const tr = document.createElement("tr"), ev = event.evidence || {}, br = event.bearing || {};
    const cells = [
      (event.onset_utc || "").replace("T", " "), event.mode, duration(event.open ? null : event.duration_s), fmt(ev.median_f0_hz, 0, "Hz"),
      fmt(ev.max_harmonic_score, 2), fmt(ev.max_level_above_floor_db, 1, "dB"),
      br.mean_deg === null || br.mean_deg === undefined ? "none" : fmt(br.mean_deg, 0, "deg") + (event.mode === "SIMULATED" ? " (simulated)" : ""),
      br.unavailable_fraction === null || br.unavailable_fraction === undefined ? "n/a" : fmt(100 * br.unavailable_fraction, 0, "%"),
      event.identity && event.identity.available ? "model score " + fmt(event.identity.max_model_score, 2) : "not assessed",
    ];
    for (const text of cells) { const td = document.createElement("td"); td.textContent = text; tr.append(td); }
    body.append(tr);
  }
  $("events-empty").hidden = (payload.events || []).length > 0;
}

function lostContact() {
  document.body.classList.add("no-contact");
  const headline = $("headline"); headline.dataset.severity = "fault";
  $("headline-icon").textContent = ICON.fault;
  $("headline-text").textContent = "DASHBOARD LOST CONTACT WITH THE SENSOR PROCESS. Nothing below is current.";
  clear($("flags"));
  $("bearing-label").textContent = "BEARING UNAVAILABLE";
  $("cand-label").textContent = "Not assessed";
  $("acq-status").textContent = "unknown";
  $("audio-status").textContent = "unknown";
  $("age").textContent = "";
  const dial = $("dial"); clear(dial);
  dial.append(svg("text", { class: "none", x: 0, y: 4, "text-anchor": "middle" }, "no contact"));
}

async function getJson(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 1500);
  try {
    const response = await fetch(url, { cache: "no-store", signal: controller.signal });
    if (!response.ok) throw new Error("HTTP " + response.status);
    return await response.json();
  } finally { clearTimeout(timer); }
}
async function tick() {
  try {
    if (!info) info = await getJson("/api/info");
    render(await getJson("/api/status"));
  } catch (error) { lostContact(); }
  setTimeout(tick, 250);
}
async function tickHistory() {
  try { frames = (await getJson("/api/history")).history; } catch (error) { frames = []; }
  setTimeout(tickHistory, 1000);
}
tick();
tickHistory();
