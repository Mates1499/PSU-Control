"use strict";

// ---- tiny DOM helpers ----------------------------------------------------
const $ = (id) => document.getElementById(id);
const num = (id) => {
  const v = $(id).value.trim();
  return v === "" ? null : parseFloat(v);
};

let connected = false;
let pollTimer = null;
const history = []; // {t, v, i}
const MAX_POINTS = 120;

// ---- API -----------------------------------------------------------------
async function api(path, body) {
  const opts = { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) {
    throw new Error(data.error || `Request failed (${res.status})`);
  }
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
    const st = await api("/api/connect", body);
    onConnected(st);
    toast(demo ? "Connected to simulator" : "Connected");
  } catch (e) {
    toast(e.message, true);
  }
}

async function disconnect() {
  try {
    await api("/api/disconnect", {});
  } catch (e) { /* ignore */ }
  onDisconnected();
  toast("Disconnected");
}

function onConnected(st) {
  connected = true;
  history.length = 0;
  $("connDot").classList.add("on");
  $("connLabel").textContent = st.demo ? "Connected (demo)" : "Connected";
  $("idn").textContent = st.idn || "";
  setControlsEnabled(true);
  $("btnConnect").disabled = true;
  $("btnDemo").disabled = true;
  $("btnDisconnect").disabled = false;
  applyState(st);
  startPolling();
}

function onDisconnected() {
  connected = false;
  stopPolling();
  $("connDot").classList.remove("on");
  $("connLabel").textContent = "Disconnected";
  $("idn").textContent = "";
  setControlsEnabled(false);
  $("btnConnect").disabled = false;
  $("btnDemo").disabled = false;
  $("btnDisconnect").disabled = true;
  $("mVoltage").textContent = $("mCurrent").textContent = $("mPower").textContent = "—";
}

function setControlsEnabled(on) {
  ["btnOutput", "btnApply", "btnProt", "btnClearProt", "btnReset"].forEach((id) => {
    $(id).disabled = !on;
  });
}

// ---- state rendering -----------------------------------------------------
function applyState(st) {
  if (!st || !st.connected) return;
  if (typeof st.voltage_set === "number") $("setVoltage").value = st.voltage_set.toFixed(2);
  if (typeof st.current_set === "number") $("setCurrent").value = st.current_set.toFixed(2);
  if (st.mode) $("mode").value = st.mode;
  renderMeasure(st);
}

function renderMeasure(st) {
  if (!st || !st.connected) {
    if (connected) { onDisconnected(); toast("Instrument disconnected", true); }
    return;
  }
  const m = st.measurement || {};
  $("mVoltage").textContent = fmt(m.voltage);
  $("mCurrent").textContent = fmt(m.current);
  $("mPower").textContent = fmt(m.power);

  setOutputButton(st.output);
  renderProtection(st.protection);

  history.push({ v: m.voltage || 0, i: m.current || 0 });
  while (history.length > MAX_POINTS) history.shift();
  drawChart();
}

function fmt(x) { return typeof x === "number" ? x.toFixed(3) : "—"; }

function setOutputButton(on) {
  const b = $("btnOutput");
  b.classList.toggle("on", !!on);
  b.classList.toggle("off", !on);
  b.querySelector(".state").textContent = on ? "OUTPUT ON" : "OUTPUT OFF";
}

function renderProtection(p) {
  const el = $("protStatus");
  if (!p) { el.textContent = "Protection: —"; return; }
  el.textContent = "Protection: " + (p.text || (p.any ? "TRIPPED" : "OK"));
  el.classList.toggle("tripped", !!p.any);
  el.classList.toggle("ok", !p.any);
}

// ---- actions -------------------------------------------------------------
async function toggleOutput() {
  const turningOn = !$("btnOutput").classList.contains("on");
  try {
    const st = await api("/api/output", { on: turningOn });
    renderMeasure(st);
  } catch (e) { toast(e.message, true); }
}

async function applySetpoints() {
  try {
    const st = await api("/api/setpoint", {
      mode: $("mode").value,
      voltage: num("setVoltage"),
      current: num("setCurrent"),
    });
    applyState(st);
    toast("Setpoints applied");
  } catch (e) { toast(e.message, true); }
}

async function applyProtection() {
  try {
    await api("/api/protection", { ovp: num("ovp"), ocp: num("ocp"), opp: num("opp") });
    toast("Protection updated");
  } catch (e) { toast(e.message, true); }
}

async function clearProtection() {
  try { renderMeasure(await api("/api/clear_protection", {})); toast("Protection cleared"); }
  catch (e) { toast(e.message, true); }
}

async function reset() {
  if (!confirm("Send *RST? This resets the instrument and turns the output off.")) return;
  try { applyState(await api("/api/reset", {})); toast("Instrument reset"); }
  catch (e) { toast(e.message, true); }
}

// ---- polling -------------------------------------------------------------
function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => {
    try { renderMeasure(await api("/api/measure")); }
    catch (e) { /* transient; keep polling */ }
  }, 600);
}
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

// ---- chart (pure canvas, dual axis V & I) --------------------------------
function drawChart() {
  const c = $("chart");
  const ctx = c.getContext("2d");
  const W = c.width, H = c.height, pad = 28;
  ctx.clearRect(0, 0, W, H);

  // grid
  ctx.strokeStyle = "#2a323d"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const y = pad + (H - 2 * pad) * g / 4;
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
$("btnOutput").onclick = toggleOutput;
$("btnApply").onclick = applySetpoints;
$("btnProt").onclick = applyProtection;
$("btnClearProt").onclick = clearProtection;
$("btnReset").onclick = reset;

// On load, reflect any pre-existing connection (e.g. --demo startup).
(async () => {
  try {
    const st = await api("/api/state");
    if (st.connected) onConnected(st); else onDisconnected();
  } catch (e) { onDisconnected(); }
})();
