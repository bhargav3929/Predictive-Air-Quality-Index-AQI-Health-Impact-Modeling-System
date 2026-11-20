/* AeroPulse front-end */
const $ = (s) => document.querySelector(s);
const PRESETS = [
  { city: "New Delhi", profile: "Asthma / Respiratory", title: "New Delhi, India", sub: "Asthma / Sensitive Group" },
  { city: "Hyderabad", profile: "General Adult", title: "Hyderabad, India", sub: "General Adult / Outdoor Runner" },
  { city: "Zurich", profile: "General Adult", title: "Zurich, Switzerland", sub: "Clean Alpine Baseline" },
  { city: "Beijing", profile: "Elderly / Cardiac", title: "Beijing, China", sub: "Elderly / Cardiovascular Sensitivity" },
];
const COLORS = { good: "#10B981", mod: "#F59E0B", sens: "#F97316", bad: "#EF4444", vbad: "#7E22CE" };
const WHO = { pm2_5: 15, pm10: 45, no2: 25, o3: 100 };
const EPA = { pm2_5: 35, pm10: 150, no2: 188, o3: 137 };
let current = null, timerId = null, running = false, rawLog = [];

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (v, d = 1) => (v == null ? "—" : Number(v).toFixed(d));
function cat(v) {
  if (v == null) return { label: "Unknown", color: "#A8A29E" };
  if (v <= 50) return { label: "Good", color: COLORS.good };
  if (v <= 100) return { label: "Moderate", color: COLORS.mod };
  if (v <= 150) return { label: "Sensitive Groups", color: COLORS.sens };
  if (v <= 200) return { label: "Unhealthy", color: COLORS.bad };
  return { label: "Very Unhealthy", color: COLORS.vbad };
}
const hhmm = (t) => { const d = new Date(t); return d.toLocaleDateString(undefined, { weekday: "short" }) + " " + t.slice(11, 16); };

/* ---------- navigation ---------- */
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
function switchView(v) {
  document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b.dataset.view === v));
  document.querySelectorAll(".view").forEach((s) => s.classList.toggle("active", s.id === "view-" + v));
  if (v === "compare") loadCompareOptions();
}

/* ---------- quota ---------- */
async function loadQuota() {
  try { setQuota((await (await fetch("/api/quota")).json()).remaining); } catch { /* ignore */ }
}
function setQuota(n) {
  const p = $("#quotaPill");
  p.textContent = `${n}/15 Free Demo Forecasts Left This Hour`;
  p.className = "pill" + (n === 0 ? " zero" : n <= 2 ? " low" : "");
}

/* ---------- presets ---------- */
$("#presets").innerHTML = PRESETS.map((p, i) => `<button class="preset" data-i="${i}"><b>${esc(p.title)}</b><span>${esc(p.sub)}</span></button>`).join("");
document.querySelectorAll(".preset").forEach((b) => b.addEventListener("click", () => {
  const p = PRESETS[+b.dataset.i];
  $("#city").value = p.city; $("#profile").value = p.profile;
  runForecast(p.city, p.profile);
}));
$("#form").addEventListener("submit", (e) => { e.preventDefault(); runForecast($("#city").value, $("#profile").value); });

/* ---------- pipeline run ---------- */
const NODE_NAMES = ["Location Gatekeeper & Geocoder", "Live Multi-Pollutant Telemetry", "72-Hour Trend & Peak Analyzer", "AI Respiratory Advisory", "Clinical Brief & SQLite"];
function renderPipeline(state = {}) {
  $("#pipeline").innerHTML = NODE_NAMES.map((n, i) => {
    const s = state[i + 1] || {};
    return `<div class="node ${s.status || ""}"><b>${i + 1}. ${esc(n)}</b><span class="ms">${s.status === "done" ? s.ms + " ms" : s.status === "running" ? "running…" : s.status === "err" ? "failed" : "waiting"}</span></div>`;
  }).join("");
}
function startTimer() {
  const t0 = performance.now();
  clearInterval(timerId);
  timerId = setInterval(() => { $("#timer").textContent = ((performance.now() - t0) / 1000).toFixed(1) + "s"; }, 100);
  return t0;
}
async function runForecast(city, profile) {
  if (running) return;
  running = true; rawLog = []; $("#btnRun").disabled = true;
  $("#alert").classList.add("hidden"); $("#pipeline").classList.remove("hidden"); $("#empty").classList.add("hidden");
  const state = {}; renderPipeline(state);
  const t0 = startTimer();
  let gotResult = false;
  try {
    const res = await fetch("/api/forecast", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ city, profile }) });
    const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        rawLog.push(line);
        const ev = JSON.parse(line);
        if (ev.quota_remaining != null) setQuota(ev.quota_remaining);
        if (ev.type === "node") { state[ev.node] = { status: ev.status, ms: ev.ms }; renderPipeline(state); }
        else if (ev.type === "error") {
          if (ev.node) { state[ev.node] = { status: "err" }; renderPipeline(state); }
          showAlert(ev.message, ev.code === "rate_limited" || ev.code === "city_not_found" || ev.code === "invalid_city" ? "" : "err");
          if (ev.code === "rate_limited") setQuota(0);
        } else if (ev.type === "result") {
          gotResult = true; current = ev.data; render(ev.data);
        }
      }
    }
  } catch (e) {
    showAlert("Request failed: " + e.message, "err");
  } finally {
    clearInterval(timerId);
    $("#timer").textContent = ((performance.now() - t0) / 1000).toFixed(1) + "s";
    running = false; $("#btnRun").disabled = false;
    renderLogs(gotResult ? current : null);
    if (!gotResult && !current) $("#empty").classList.remove("hidden");
  }
}
function showAlert(msg, cls) {
  const a = $("#alert"); a.className = "alert " + (cls || ""); a.textContent = msg;
}

/* ---------- render result ---------- */
function polar(cx, cy, r, deg) { const a = (Math.PI * (180 - deg)) / 180; return [cx + r * Math.cos(a), cy - r * Math.sin(a)]; }
function arc(cx, cy, r, d0, d1) {
  const [x0, y0] = polar(cx, cy, r, d0), [x1, y1] = polar(cx, cy, r, d1);
  return `M${x0.toFixed(1)} ${y0.toFixed(1)} A${r} ${r} 0 ${d1 - d0 > 180 ? 1 : 0} 1 ${x1.toFixed(1)} ${y1.toFixed(1)}`;
}
function gaugeSVG(v) {
  const segs = [[0, 50, COLORS.good], [50, 100, COLORS.mod], [100, 150, COLORS.sens], [150, 200, COLORS.bad], [200, 500, COLORS.vbad]];
  const cx = 150, cy = 140, r = 110, val = Math.max(0, Math.min(500, v ?? 0));
  const deg = (x) => (x / 500) * 180;
  const paths = segs.map(([a, b, c]) => `<path d="${arc(cx, cy, r, deg(a) + 0.4, deg(b) - 0.4)}" stroke="${c}" stroke-width="18" fill="none" opacity=".25"/>`).join("");
  const filled = segs.map(([a, b, c]) => (val > a ? `<path d="${arc(cx, cy, r, deg(a) + 0.4, deg(Math.min(b, val)) - 0.2)}" stroke="${c}" stroke-width="18" fill="none"/>` : "")).join("");
  const [nx, ny] = polar(cx, cy, r - 30, deg(val));
  return `<svg viewBox="0 0 300 175" width="100%" style="max-width:340px">${paths}${filled}
    <line x1="${cx}" y1="${cy}" x2="${nx.toFixed(1)}" y2="${ny.toFixed(1)}" stroke="#1C1917" stroke-width="3" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="6" fill="#1C1917"/>
    <text x="${cx}" y="${cy - 44}" text-anchor="middle" font-family="ui-monospace,Consolas,monospace" font-size="42" font-weight="700" fill="#1C1917">${fmt(v, 0)}</text>
    <text x="${cx}" y="${cy - 26}" text-anchor="middle" font-size="11" fill="#78716C">US AQI (0–500)</text>
    <text x="${cx - r}" y="${cy + 18}" text-anchor="middle" font-size="10" fill="#78716C" font-family="monospace">0</text><text x="${cx + r}" y="${cy + 18}" text-anchor="middle" font-size="10" fill="#78716C" font-family="monospace">500</text></svg>`;
}
function metricCard(name, key, val, unit) {
  const max = EPA[key] * 1.5, pct = Math.min(100, ((val ?? 0) / max) * 100), who = (WHO[key] / max) * 100, epa = (EPA[key] / max) * 100;
  const col = val == null ? "#A8A29E" : val <= WHO[key] ? COLORS.good : val <= EPA[key] ? COLORS.mod : COLORS.bad;
  return `<div class="metric"><div class="n">${name}</div><div class="v">${fmt(val)} <small>${unit}</small></div>
    <div class="bar"><i style="width:${pct}%;background:${col}"></i><u style="left:${who}%" title="WHO"></u><u style="left:${epa}%;background:#78716C" title="EPA"></u></div>
    <div class="bar-l"><span>WHO ${WHO[key]}</span><span>EPA ${EPA[key]}</span></div></div>`;
}
function render(r) {
  $("#results").classList.remove("hidden"); $("#empty").classList.add("hidden");
  const L = r.location, c = r.current, an = r.analysis, k = cat(c.us_aqi);
  $("#locTitle").textContent = `${L.name}, ${L.country} · ${r.profile}`;
  $("#gauge").innerHTML = gaugeSVG(c.us_aqi);
  const cb = $("#catBadge"); cb.textContent = k.label; cb.style.background = k.color;
  $("#domBadge").textContent = "Driver: " + an.dominant.label;
  $("#trendBadge").textContent = "Trend: " + an.trend.label;
  $("#metrics").innerHTML = metricCard("PM2.5", "pm2_5", c.pm2_5, "µg/m³") + metricCard("PM10", "pm10", c.pm10, "µg/m³") + metricCard("NO₂", "no2", c.no2, "µg/m³") + metricCard("O₃", "o3", c.o3, "µg/m³");
  const ri = r.risk_index; if (ri) $("#riskIdx").textContent = `${ri.score}/10 · ${ri.label}`;
  drawStrip(r);
  drawChart(r);
  const bw = an.best_window, pw = an.peak_window;
  $("#windows").innerHTML =
    `<div class="callout best"><h4>Best Safe Outdoor Window (next 24h)</h4><div class="mono">${bw ? esc(bw.label) : "n/a"}</div><div class="muted small">${bw ? `Mean AQI ${bw.mean_aqi} · ${esc(bw.category.label)}` : ""}</div></div>` +
    `<div class="callout peak"><h4>Peak Pollution Alert (72h)</h4><div class="mono">${pw ? esc(pw.label) : "n/a"}</div><div class="muted small">${pw ? `Mean AQI ${pw.mean_aqi} · ${esc(pw.category.label)}` : ""}</div></div>`;
  const W = an.windows;
  $("#stats").innerHTML = `<table><tr><th>Window</th><th>Mean AQI</th><th>Peak AQI</th><th>Mean PM2.5</th><th>Peak PM2.5</th><th>Mean PM10</th><th>Mean NO₂</th></tr>` +
    `<tr><td>Current</td><td>${fmt(c.us_aqi, 0)}</td><td>—</td><td>${fmt(c.pm2_5)}</td><td>—</td><td>${fmt(c.pm10)}</td><td>${fmt(c.no2)}</td></tr>` +
    ["24h", "48h", "72h"].map((n) => `<tr><td>Next ${n}</td><td>${fmt(W[n].us_aqi.mean)}</td><td>${fmt(W[n].us_aqi.peak, 0)}</td><td>${fmt(W[n].pm2_5.mean)}</td><td>${fmt(W[n].pm2_5.peak)}</td><td>${fmt(W[n].pm10.mean)}</td><td>${fmt(W[n].no2.mean)}</td></tr>`).join("") + "</table>";
  const a = r.advisory;
  $("#advTitle").textContent = `Respiratory Health Advisory — ${r.profile}`;
  $("#advisory").innerHTML =
    `<div class="adv headline"><h4>${esc(a.headline)}</h4><span class="tag">${esc(a.risk_level)}</span> <span class="muted small">via ${esc(r.provider.label)}</span></div>` +
    `<div class="adv"><h4>Outdoor Activity</h4><p>${esc(a.outdoor_activity)}</p><h4>Exercise Guidance</h4><p>${esc(a.exercise_guidance)}</p></div>` +
    `<div class="adv"><h4>Mask &amp; Filtration Protocol</h4><p>${esc(a.mask_protocol)}</p><p><span class="tag">${esc(a.mask_threshold)}</span></p><h4>Indoor HEPA</h4><p>${esc(a.indoor_hepa)}</p></div>` +
    `<div class="adv"><h4>Symptom Watch</h4><ul>${(a.symptom_watch || []).map((s) => `<li>${esc(s)}</li>`).join("")}</ul></div>`;
  $("#briefText").textContent = r.brief_md;
}

/* ---------- hourly strip ---------- */
function drawStrip(r) {
  const s = r.series;
  $("#strip").style.gridTemplateColumns = `repeat(${s.time.length},1fr)`;
  $("#strip").innerHTML = s.time.map((t, i) => { const k = cat(s.us_aqi[i]);
    return `<i class="${i === s.now_index ? "now" : ""}" style="background:${k.color};opacity:${i < s.now_index ? 0.4 : 1}" title="${hhmm(t)} · AQI ${fmt(s.us_aqi[i], 0)} · ${k.label}"></i>`; }).join("");
}

/* ---------- SVG chart ---------- */
function drawChart(r) {
  const s = r.series, n = s.time.length, W = 900, H = 300, ml = 44, mr = 44, mt = 14, mb = 30;
  const aqi = s.us_aqi.map((v) => v ?? 0), pm = s.pm2_5.map((v) => v ?? 0);
  const maxA = Math.max(160, Math.max(...aqi) * 1.1), maxP = Math.max(40, Math.max(...pm) * 1.1);
  const x = (i) => ml + (i / (n - 1)) * (W - ml - mr), yA = (v) => mt + (1 - v / maxA) * (H - mt - mb), yP = (v) => mt + (1 - v / maxP) * (H - mt - mb);
  const zones = [[0, 50, COLORS.good], [50, 100, COLORS.mod], [100, 150, COLORS.sens], [150, 200, COLORS.bad], [200, 500, COLORS.vbad]]
    .filter(([a]) => a < maxA).map(([a, b, c]) => `<rect x="${ml}" y="${yA(Math.min(b, maxA))}" width="${W - ml - mr}" height="${yA(a) - yA(Math.min(b, maxA))}" fill="${c}" opacity=".10"/>`).join("");
  const line = (arr, f) => arr.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${f(v).toFixed(1)}`).join(" ");
  const an = r.analysis, hl = (w, c) => (w ? `<rect x="${x(w.start_idx)}" y="${mt}" width="${x(w.end_idx + 1) - x(w.start_idx)}" height="${H - mt - mb}" fill="${c}" opacity=".28" stroke="${c}"/>` : "");
  let ticks = "";
  for (let i = 0; i < n; i += 6) ticks += `<line x1="${x(i)}" x2="${x(i)}" y1="${H - mb}" y2="${H - mb + 4}" stroke="#A8A29E"/>` + (i % 12 === 0 ? `<text x="${x(i)}" y="${H - 10}" text-anchor="middle" font-size="10" fill="#78716C" font-family="monospace">${i % 24 === 0 ? hhmm(s.time[i]) : s.time[i].slice(11, 16)}</text>` : "");
  const yt = [0, 50, 100, 150, 200, 300].filter((v) => v <= maxA).map((v) => `<text x="${ml - 6}" y="${yA(v) + 3}" text-anchor="end" font-size="10" fill="#78716C" font-family="monospace">${v}</text>`).join("");
  const yr = [0, 0.5, 1].map((f) => `<text x="${W - mr + 6}" y="${yP(maxP * f) + 3}" font-size="10" fill="#2563EB" font-family="monospace">${(maxP * f).toFixed(0)}</text>`).join("");
  const ni = s.now_index;
  $("#chart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" id="svgc">${zones}${hl(an.best_window, COLORS.good)}${hl(an.peak_window, COLORS.bad)}
    <path d="${line(pm, yP)}" stroke="#2563EB" stroke-width="2" fill="none" stroke-dasharray="5 4"/>
    <path d="${line(aqi, yA)}" stroke="#1C1917" stroke-width="2.5" fill="none" stroke-linejoin="round"/>
    <line x1="${x(ni)}" x2="${x(ni)}" y1="${mt}" y2="${H - mb}" stroke="#1C1917" stroke-dasharray="2 3"/><text x="${x(ni) + 4}" y="${mt + 10}" font-size="10" fill="#1C1917" font-family="monospace">now</text>
    ${ticks}${yt}${yr}<line id="cur" x1="0" x2="0" y1="${mt}" y2="${H - mb}" stroke="#78716C" opacity="0"/><circle id="dot" r="4.5" fill="#1C1917" stroke="#fff" stroke-width="2" opacity="0"/>
    <rect id="hit" x="${ml}" y="${mt}" width="${W - ml - mr}" height="${H - mt - mb}" fill="transparent"/></svg>`;
  const svg = $("#svgc"), tip = $("#tip");
  const move = (e) => {
    const b = svg.getBoundingClientRect(), px = ((e.clientX - b.left) / b.width) * W;
    const i = Math.max(0, Math.min(n - 1, Math.round(((px - ml) / (W - ml - mr)) * (n - 1))));
    const k = cat(s.us_aqi[i]);
    $("#cur").setAttribute("x1", x(i)); $("#cur").setAttribute("x2", x(i)); $("#cur").setAttribute("opacity", 1);
    $("#dot").setAttribute("cx", x(i)); $("#dot").setAttribute("cy", yA(aqi[i])); $("#dot").setAttribute("opacity", 1);
    tip.innerHTML = `<b>${hhmm(s.time[i])}</b><br>US AQI <span class="mono">${fmt(s.us_aqi[i], 0)}</span> <span style="color:${k.color}">● ${k.label}</span><br>PM2.5 <span class="mono">${fmt(s.pm2_5[i])}</span> µg/m³`;
    tip.style.left = (x(i) / W) * b.width + "px"; tip.style.top = (yA(aqi[i]) / H) * b.height + "px"; tip.classList.remove("hidden");
  };
  $("#hit").addEventListener("mousemove", move);
  $("#hit").addEventListener("mouseleave", () => { tip.classList.add("hidden"); $("#cur").setAttribute("opacity", 0); $("#dot").setAttribute("opacity", 0); });
}

/* ---------- logs ---------- */
function renderLogs(r) {
  const rows = [];
  rawLog.forEach((l) => {
    try {
      const e = JSON.parse(l);
      if (e.type === "node" && e.status === "done") rows.push(`Node ${e.node} ${e.name}: ${e.ms} ms${e.api_ms != null ? ` (Open-Meteo API ${e.api_ms} ms)` : ""}`);
    } catch { /* ignore */ }
  });
  if (r) {
    const p = r.provider;
    rows.push(`AI provider: ${p.provider} / ${p.model}${p.fallback ? " (fallback)" : ""}${p.latency_ms ? " · " + p.latency_ms + " ms" : ""}`);
    (p.attempts || []).forEach((a) => rows.push("  · " + a));
    rows.push(`Total pipeline: ${r.timings.total_ms} ms`);
  }
  $("#logSummary").innerHTML = rows.map((x) => `<div>${esc(x)}</div>`).join("");
  $("#logRaw").textContent = rawLog.map((l) => (l.length > 600 ? l.slice(0, 600) + " …[truncated]" : l)).join("\n");
}

/* ---------- history ---------- */
async function openHistory() {
  $("#drawer").classList.add("open"); $("#scrim").classList.remove("hidden");
  const list = await (await fetch("/api/history")).json();
  $("#historyList").innerHTML = list.length ? list.map((h) => { const k = cat(h.us_aqi);
    return `<button class="hist" data-id="${h.id}"><span class="dot" style="background:${k.color}"></span><span><b>${esc(h.city)}, ${esc(h.country)}</b><br><span class="muted small">${esc(h.profile)} · ${new Date(h.created_at * 1000).toLocaleString()}</span></span><span class="mono">${fmt(h.us_aqi, 0)}</span><span class="del" data-del="${h.id}" title="Delete">×</span></button>`; }).join("") : '<p class="muted">No forecasts yet.</p>';
  document.querySelectorAll(".hist").forEach((b) => b.addEventListener("click", async (e) => {
    if (e.target.dataset.del) { await fetch("/api/forecast/" + e.target.dataset.del, { method: "DELETE" }); openHistory(); return; }
    current = await (await fetch("/api/forecast/" + b.dataset.id)).json(); closeDrawer(); switchView("forecast");
    $("#pipeline").classList.add("hidden"); $("#alert").classList.add("hidden"); render(current);
  }));
}
function closeDrawer() { $("#drawer").classList.remove("open"); $("#scrim").classList.add("hidden"); }
$("#btnHistory").addEventListener("click", openHistory);
$("#closeDrawer").addEventListener("click", closeDrawer); $("#scrim").addEventListener("click", closeDrawer);

/* ---------- compare ---------- */
async function loadCompareOptions() {
  const list = await (await fetch("/api/history")).json();
  const opts = list.map((h) => `<option value="${h.id}">${esc(h.city)}, ${esc(h.country)} — AQI ${fmt(h.us_aqi, 0)} (${new Date(h.created_at * 1000).toLocaleTimeString()})</option>`).join("");
  $("#cmpA").innerHTML = opts; $("#cmpB").innerHTML = opts;
  if (list.length > 1) $("#cmpB").selectedIndex = 1;
}
$("#btnCompare").addEventListener("click", async () => {
  const a = $("#cmpA").value, b = $("#cmpB").value;
  if (!a || !b) { $("#cmpOut").innerHTML = '<div class="alert">Run at least two forecasts first.</div>'; return; }
  const d = await (await fetch(`/api/compare?a=${a}&b=${b}`)).json();
  overlay(a, b);
  const side = (f) => { const k = cat(f.current.us_aqi); return `<div class="card cmpCity"><h3>${esc(f.location.name)}, ${esc(f.location.country)}</h3><div class="muted small">${esc(f.profile)}</div><div class="big" style="color:${k.color}">${fmt(f.current.us_aqi, 0)}</div><span class="badge" style="background:${k.color}">${k.label}</span><p class="small muted">Driver: ${esc(f.analysis.dominant.label)} · Trend: ${f.analysis.trend.label}</p></div>`; };
  const rows = [["US AQI", "us_aqi", 0], ["PM2.5 (µg/m³)", "pm2_5", 1], ["PM10 (µg/m³)", "pm10", 1], ["NO₂ (µg/m³)", "no2", 1], ["O₃ (µg/m³)", "o3", 1]]
    .map(([n, k, dp]) => { const dl = d.delta_b_minus_a[k]; return `<tr><td>${n}</td><td>${fmt(d.a.current[k], dp)}</td><td>${fmt(d.b.current[k], dp)}</td><td class="delta ${dl > 0 ? "up" : dl < 0 ? "down" : ""}">${dl == null ? "—" : (dl > 0 ? "+" : "") + dl}</td></tr>`; }).join("");
  $("#cmpOut").innerHTML = `<div class="card rec"><div class="cardTitle">Safer-air recommendation</div><b>${esc(d.recommendation)}</b></div><div class="cmpGrid">${side(d.a)}${side(d.b)}</div>` +
    `<div class="card"><div class="tableWrap"><table><tr><th>Metric</th><th>${esc(d.a.location.name)}</th><th>${esc(d.b.location.name)}</th><th>Δ (B − A)</th></tr>${rows}</table></div></div>`;
});

async function overlay(a, b) {
  const [fa, fb] = await Promise.all([a, b].map(async (id) => (await fetch("/api/forecast/" + id)).json()));
  const W = 900, H = 240, ml = 40, mr = 14, mt = 12, mb = 24, n = Math.max(fa.series.time.length, fb.series.time.length);
  const mx = Math.max(150, ...fa.series.us_aqi.map((v) => v || 0), ...fb.series.us_aqi.map((v) => v || 0)) * 1.1;
  const x = (i) => ml + (i / (n - 1)) * (W - ml - mr), y = (v) => mt + (1 - v / mx) * (H - mt - mb);
  const p = (f) => f.series.us_aqi.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v || 0).toFixed(1)}`).join(" ");
  const grid = [0, 50, 100, 150, 200].filter((v) => v < mx).map((v) => `<line x1="${ml}" x2="${W - mr}" y1="${y(v)}" y2="${y(v)}" stroke="#E7E5E4"/><text x="${ml - 5}" y="${y(v) + 3}" text-anchor="end" font-size="10" fill="#78716C" font-family="monospace">${v}</text>`).join("");
  $("#lgA").textContent = fa.location.name; $("#lgB").textContent = fb.location.name;
  $("#cmpChart").innerHTML = `<svg viewBox="0 0 ${W} ${H}" style="width:100%">${grid}<path d="${p(fa)}" stroke="#1C1917" stroke-width="2.5" fill="none"/><path d="${p(fb)}" stroke="#2563EB" stroke-width="2.5" fill="none"/></svg>`;
  $("#cmpChartCard").classList.remove("hidden");
}

/* ---------- brief ---------- */
$("#btnCopy").addEventListener("click", async () => {
  if (!current) return;
  try { await navigator.clipboard.writeText(current.brief_md); $("#btnCopy").textContent = "Copied ✓"; } catch { $("#btnCopy").textContent = "Copy failed"; }
  setTimeout(() => ($("#btnCopy").textContent = "Copy Brief"), 1600);
});
$("#btnDownload").addEventListener("click", () => {
  if (!current) return;
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([current.brief_md], { type: "text/markdown" }));
  a.download = `aeropulse-brief-${current.location.name.toLowerCase().replace(/\W+/g, "-")}.md`; a.click(); URL.revokeObjectURL(a.href);
});

/* ---------- keys modal ---------- */
async function openKeys() {
  const s = await (await fetch("/api/settings")).json();
  $("#groqHint").textContent = s.GROQ_API_KEY.set ? `(saved ${s.GROQ_API_KEY.hint})` : "(not set)";
  $("#kGroq").value = ""; $("#keysModal").classList.remove("hidden");
}
$("#btnKeys").addEventListener("click", openKeys);
$("#keysCancel").addEventListener("click", () => $("#keysModal").classList.add("hidden"));
$("#keysSave").addEventListener("click", async () => {
  const body = {}; if ($("#kGroq").value.trim()) body.GROQ_API_KEY = $("#kGroq").value;
  await fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  $("#keysModal").classList.add("hidden");
});

loadQuota();
(async () => {
  const q = new URLSearchParams(location.search);
  if (q.get("city")) { $("#city").value = q.get("city"); if (q.get("profile")) $("#profile").value = q.get("profile"); return; }
  try {
    const h = await (await fetch("/api/history?limit=1")).json();
    if (h.length) { current = await (await fetch("/api/forecast/" + h[0].id)).json(); render(current); }
  } catch { /* ignore */ }
})();
