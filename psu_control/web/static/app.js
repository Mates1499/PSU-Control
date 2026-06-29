"use strict";

const $ = (id) => document.getElementById(id);

let connected = false;
let pollTimer = null;
const channels = {}; // number -> { el, history:[], specs }
const MAX_POINTS = 90;

// ---- API -----------------------------------------------------------------
async function api(path, body) {
  const opts = { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function toast(msg, isError) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isError ? " error" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 3200);
}

// ---- connection ----------------------------------------------------------
async function connect(demo) {
  try {
    const body = demo
      ? { demo: true }
      : { host: $("host").value, port: parseInt($("port").value, 10), visa: $("visa").value };
    onConnected(await api("/api/connect", body));
    toast(demo ? "Connected to simulator" : "Connected");
  } catch (e) { toast(e.message, true); }
}

async function disconnect() {
  try { await api("/api/disconnect", {}); } catch (e) { /* ignore */ }
  onDisconnected();
  toast("Disconnected");
}

function onConnected(st) {
  connected = true;
  $("connDot").classList.add("on");
  $("connLabel").textContent = st.demo ? "Connected (demo)" : "Connected";
  $("idn").textContent = st.idn || "";
  $("btnConnect").disabled = true;
  $("btnDemo").disabled = true;
  $("btnDisconnect").disabled = false;
  ["btnAllOn", "btnAllOff", "btnReset", "tracking"].forEach((id) => ($(id).disabled = false));
  buildChannels(st.channels || []);
  applyState(st);
  startPolling();
}

function onDisconnected() {
  connected = false;
  stopPolling();
  $("connDot").classList.remove("on");
  $("connLabel").textContent = "Disconnected";
  $("idn").textContent = "";
  $("btnConnect").disabled = false;
  $("btnDemo").disabled = false;
  $("btnDisconnect").disabled = true;
  ["btnAllOn", "btnAllOff", "btnReset", "tracking"].forEach((id) => ($(id).disabled = true));
  $("channels").innerHTML = "";
  for (const k in channels) delete channels[k];
}

// ---- build channel cards -------------------------------------------------
function buildChannels(list) {
  const host = $("channels");
  host.innerHTML = "";
  for (const k in channels) delete channels[k];
  const tpl = $("channelTemplate");

  list.forEach((c) => {
    const node = tpl.content.cloneNode(true);
    const el = node.querySelector(".channel");
    el.querySelector(".ch-name").textContent = c.name;
    el.querySelector(".ch-rating").textContent = `0–${c.max_voltage} V · 0–${c.max_current} A · ${c.max_power} W`;

    const sv = el.querySelector(".set-volt");
    const sc = el.querySelector(".set-curr");
    sv.max = c.max_voltage; sv.min = 0;
    sc.max = c.max_current; sc.min = 0;
    sv.value = (c.voltage_set ?? 0).toFixed(3);
    sc.value = (c.current_set ?? 0).toFixed(3);

    el.querySelector(".btn-apply").onclick = () => applySetpoint(c.number, sv, sc);
    el.querySelector(".btn-ovp").onclick = () => applyOvp(c.number, el.querySelector(".set-ovp"));
    el.querySelector(".btn-output").onclick = () => toggleOutput(c.number);

    host.appendChild(node);
    channels[c.number] = { el, history: [] };
  });
}

// ---- rendering -----------------------------------------------------------
function applyState(st) {
  if (!st || !st.connected) return;
  (st.channels || []).forEach((c) => {
    const ch = channels[c.number];
    if (!ch) return;
    ch.el.querySelector(".set-volt").value = (c.voltage_set ?? 0).toFixed(3);
    ch.el.querySelector(".set-curr").value = (c.current_set ?? 0).toFixed(3);
  });
  renderMeasure(st);
}

function renderMeasure(st) {
  if (!st || !st.connected) {
    if (connected) { onDisconnected(); toast("Instrument disconnected", true); }
    return;
  }
  (st.channels || []).forEach((c) => {
    const ch = channels[c.number];
    if (!ch) return;
    const m = c.measurement || {};
    ch.el.querySelector(".m-volt").textContent = fmt(m.voltage);
    ch.el.querySelector(".m-curr").textContent = fmt(m.current);
    ch.el.querySelector(".m-pow").textContent = fmt(m.power);

    const badge = ch.el.querySelector(".mode-badge");
    if (c.mode) {
      badge.textContent = c.output ? c.mode : "—";
      badge.className = "mode-badge " + (c.output ? c.mode.toLowerCase() : "");
    }
    setOutputButton(ch.el.querySelector(".btn-output"), c.output);

    ch.history.push({ v: m.voltage || 0, i: m.current || 0 });
    while (ch.history.length > MAX_POINTS) ch.history.shift();
    drawChart(ch.el.querySelector(".chart"), ch.history);
  });
}

function fmt(x) { return typeof x === "number" ? x.toFixed(3) : "—"; }

function setOutputButton(btn, on) {
  btn.classList.toggle("on", !!on);
  btn.classList.toggle("off", !on);
  btn.querySelector(".state").textContent = on ? "OUTPUT ON" : "OUTPUT OFF";
}

// ---- actions -------------------------------------------------------------
async function applySetpoint(n, sv, sc) {
  try {
    applyState(await api(`/api/channel/${n}/setpoint`, {
      voltage: parseFloat(sv.value), current: parseFloat(sc.value),
    }));
    toast(`CH${n} setpoints applied`);
  } catch (e) { toast(e.message, true); }
}

async function applyOvp(n, input) {
  const v = input.value.trim();
  if (v === "") { toast("Enter an OVP level first", true); return; }
  try {
    await api(`/api/channel/${n}/ovp`, { level: parseFloat(v), enable: true });
    toast(`CH${n} OVP set to ${v} V`);
  } catch (e) { toast(e.message, true); }
}

async function toggleOutput(n) {
  const btn = channels[n].el.querySelector(".btn-output");
  const turningOn = !btn.classList.contains("on");
  try { renderMeasure(await api(`/api/channel/${n}/output`, { on: turningOn })); }
  catch (e) { toast(e.message, true); }
}

async function allOutput(on) {
  try { renderMeasure(await api("/api/all_output", { on })); toast(on ? "All outputs ON" : "All outputs OFF"); }
  catch (e) { toast(e.message, true); }
}

async function setTracking(mode) {
  try { applyState(await api("/api/tracking", { mode })); toast(`Tracking: ${mode}`); }
  catch (e) { toast(e.message, true); }
}

async function reset() {
  if (!confirm("Send *RST? This resets the instrument and turns all outputs off.")) return;
  try { applyState(await api("/api/reset", {})); toast("Instrument reset"); }
  catch (e) { toast(e.message, true); }
}

// ---- polling -------------------------------------------------------------
function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    try { renderMeasure(await api("/api/measure")); } catch (e) { /* transient */ }
  }, 700);
}
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

// ---- per-channel chart (dual trace V & I) --------------------------------
function drawChart(c, history) {
  const ctx = c.getContext("2d");
  const W = c.width, H = c.height, pad = 14;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#2a323d"; ctx.lineWidth = 1;
  for (let g = 0; g <= 2; g++) {
    const y = pad + (H - 2 * pad) * g / 2;
    ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(W - pad, y); ctx.stroke();
  }
  if (history.length < 2) return;
  const vMax = Math.max(1, ...history.map((p) => Math.abs(p.v)));
  const iMax = Math.max(0.1, ...history.map((p) => Math.abs(p.i)));
  const plot = (key, max, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    history.forEach((p, idx) => {
      const x = pad + (W - 2 * pad) * idx / (MAX_POINTS - 1);
      const y = H - pad - (H - 2 * pad) * (p[key] / max);
      idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  plot("v", vMax, "#38bdf8");
  plot("i", iMax, "#f59e0b");
}

// ---- wire up -------------------------------------------------------------
$("btnConnect").onclick = () => connect(false);
$("btnDemo").onclick = () => connect(true);
$("btnDisconnect").onclick = disconnect;
$("btnAllOn").onclick = () => allOutput(true);
$("btnAllOff").onclick = () => allOutput(false);
$("btnReset").onclick = reset;
$("tracking").onchange = (e) => setTracking(e.target.value);

(async () => {
  try {
    const st = await api("/api/state");
    if (st.connected) onConnected(st); else onDisconnected();
  } catch (e) { onDisconnected(); }
})();
