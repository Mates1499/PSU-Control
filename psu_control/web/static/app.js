"use strict";

const $ = (id) => document.getElementById(id);

let connected = false;
let pollTimer = null;
const channels = {}; // number -> { el, history:[] }
const MAX_POINTS = 90;

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

async function connect() {
  try {
    const model = $("model").value;
    const body = { host: $("host").value, port: parseInt($("port").value, 10) || 0, visa: $("visa").value, model };
    onConnected(await api("/api/connect", body));
    toast("Connected");
  } catch (e) { toast(e.message, true); }
}

async function disconnect() {
  try { await api("/api/disconnect", {}); } catch (e) {}
  onDisconnected();
  toast("Disconnected");
}

const _MODEL_LABELS = {
  "itn6332b": { title: "IT-N6332B", sub: "ITECH Bidirectional DC Power Supply · SCPI" },
  "cpx200dp":  { title: "CPX200DP",  sub: "Aim-TTi Dual-Output DC Power Supply" },
};

function onConnected(st) {
  connected = true;
  $("connDot").classList.add("on");
  $("connLabel").textContent = "Connected";
  $("idn").textContent = st.idn || "";
  $("btnConnect").disabled = true;
  $("btnDisconnect").disabled = false;
  ["btnAllOn", "btnAllOff", "btnReset"].forEach((id) => ($(id).disabled = false));
  const lbl = _MODEL_LABELS[st.model] || { title: "PSU Control", sub: "Programmable DC Power Supply · SCPI" };
  $("modelLabel").textContent = lbl.title;
  $("modelSub").textContent = lbl.sub;
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
  $("btnDisconnect").disabled = true;
  ["btnAllOn", "btnAllOff", "btnReset"].forEach((id) => ($(id).disabled = true));
  $("channels").innerHTML = "";
  for (const k in channels) delete channels[k];
}

function buildChannels(list) {
  const host = $("channels");
  host.innerHTML = "";
  for (const k in channels) delete channels[k];
  const tpl = $("channelTemplate");

  list.forEach((c) => {
    const node = tpl.content.cloneNode(true);
    const el = node.querySelector(".channel");
    el.querySelector(".ch-name").textContent = "Channel " + c.number;

    const sv = el.querySelector(".set-volt");
    const sc = el.querySelector(".set-curr");
    const r = c.ranges || {};
    if (typeof r.v_max === "number") { sv.max = r.v_max; sv.min = r.v_min ?? 0; }
    if (typeof r.i_max === "number") { sc.max = r.i_max; sc.min = r.i_min ?? 0; }

    el.querySelector(".btn-apply").onclick = () => applySetpoint(c.number, el);
    el.querySelector(".btn-prot").onclick = () => applyProtection(c.number, el);
    el.querySelector(".btn-clear").onclick = () => clearProtection(c.number);
    el.querySelector(".btn-output").onclick = () => toggleOutput(c.number);

    host.appendChild(node);
    channels[c.number] = { el, history: [] };
  });
}

function applyState(st) {
  if (!st || !st.connected) return;
  (st.channels || []).forEach((c) => {
    const ch = channels[c.number];
    if (!ch) return;
    ch.el.querySelector(".set-volt").value = (c.voltage_set ?? 0).toFixed(3);
    ch.el.querySelector(".set-curr").value = (c.current_set ?? 0).toFixed(3);
    if (c.priority) ch.el.querySelector(".set-priority").value = c.priority;
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
    if (c.mode) { badge.textContent = c.output ? c.mode : "—"; badge.className = "mode-badge " + (c.output ? c.mode.toLowerCase() : ""); }
    setOutputButton(ch.el.querySelector(".btn-output"), c.output);

    const ps = ch.el.querySelector(".prot-status");
    ps.textContent = "Protection: " + (c.protection_tripped ? "TRIPPED" : "OK");
    ps.classList.toggle("tripped", !!c.protection_tripped);
    ps.classList.toggle("ok", !c.protection_tripped);

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

function numVal(el, sel) { const v = el.querySelector(sel).value.trim(); return v === "" ? null : parseFloat(v); }

async function applySetpoint(n, el) {
  try {
    applyState(await api(`/api/channel/${n}/setpoint`, {
      priority: el.querySelector(".set-priority").value,
      voltage: numVal(el, ".set-volt"),
      current: numVal(el, ".set-curr"),
    }));
    toast(`CH${n} setpoints applied`);
  } catch (e) { toast(e.message, true); }
}

async function applyProtection(n, el) {
  try {
    await api(`/api/channel/${n}/protection`, {
      ovp: numVal(el, ".set-ovp"), ocp: numVal(el, ".set-ocp"), opp: numVal(el, ".set-opp"),
    });
    toast(`CH${n} protection updated`);
  } catch (e) { toast(e.message, true); }
}

async function clearProtection(n) {
  try { renderMeasure(await api(`/api/channel/${n}/clear_protection`, {})); toast(`CH${n} protection cleared`); }
  catch (e) { toast(e.message, true); }
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

async function reset() {
  if (!confirm("Send *RST? This resets the instrument and turns all outputs off.")) return;
  try { applyState(await api("/api/reset", {})); toast("Instrument reset"); }
  catch (e) { toast(e.message, true); }
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(async () => { try { renderMeasure(await api("/api/measure")); } catch (e) {} }, 700);
}
function stopPolling() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }

function drawChart(c, history) {
  const ctx = c.getContext("2d");
  const W = c.width, H = c.height, pad = 14;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = "#2a323d"; ctx.lineWidth = 1;
  for (let g = 0; g <= 2; g++) { const y = pad + (H - 2 * pad) * g / 2; ctx.beginPath(); ctx.moveTo(pad, y); ctx.lineTo(W - pad, y); ctx.stroke(); }
  if (history.length < 2) return;
  const vMax = Math.max(1, ...history.map((p) => Math.abs(p.v)));
  const iMax = Math.max(0.1, ...history.map((p) => Math.abs(p.i)));
  const plot = (key, max, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    history.forEach((p, idx) => {
      const x = pad + (W - 2 * pad) * idx / (MAX_POINTS - 1);
      const y = H - pad - (H - 2 * pad) * (p[key] + max) / (2 * max);
      idx === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  plot("v", vMax, "#38bdf8");
  plot("i", iMax, "#f59e0b");
}

$("btnConnect").onclick = connect;
$("btnDisconnect").onclick = disconnect;
$("btnAllOn").onclick = () => allOutput(true);
$("btnAllOff").onclick = () => allOutput(false);
$("btnReset").onclick = reset;

(async () => {
  try {
    const st = await api("/api/state");
    if (st.connected) onConnected(st); else onDisconnected();
  } catch (e) { onDisconnected(); }
})();
