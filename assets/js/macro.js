// 宏观页：五维评分、关键读数、分维度图表、数据状态。
"use strict";

const RANGES = [
  { key: "2y", label: "2 年", years: 2 },
  { key: "5y", label: "5 年", years: 5 },
  { key: "10y", label: "10 年", years: 10 },
  { key: "all", label: "全部", years: null },
];
const RANGE_KEY = "macro.range";
let rangeKey = "5y";
try { rangeKey = localStorage.getItem(RANGE_KEY) || rangeKey; } catch (_) {}
if (!RANGES.some((r) => r.key === rangeKey)) rangeKey = "5y";

const charts = new Map(); // id -> Chart
const sparks = [];
let DATA = null;

function rangeStartMs() {
  const r = RANGES.find((x) => x.key === rangeKey);
  if (!r || r.years === null) return null;
  const d = new Date(DATA.asof + "T00:00:00Z");
  d.setUTCFullYear(d.getUTCFullYear() - r.years);
  return d.getTime();
}

function renderRange() {
  const el = document.getElementById("range");
  el.innerHTML = RANGES.map((r) => `<button type="button" data-k="${r.key}" aria-pressed="${r.key === rangeKey}">${r.label}</button>`).join("");
  el.onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    rangeKey = b.dataset.k;
    try { localStorage.setItem(RANGE_KEY, rangeKey); } catch (_) {}
    el.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.k === rangeKey)));
    const s = rangeStartMs();
    charts.forEach((c) => applyRange(c, s));
  };
}

// ---- 五维评分 ----
function meterPos(score) {
  const s = Math.max(-2, Math.min(2, score ?? 0));
  return ((s + 2) / 4) * 100;
}

function renderScores() {
  const el = document.getElementById("scores");
  el.innerHTML = DATA.dimensions.map((d) => {
    const delta = d.score !== null && d.score_3m_ago !== null ? d.score - d.score_3m_ago : null;
    const comps = (d.components || []).map((c) =>
      `<span>${esc(c.name)}</span><span class="muted">${esc(c.date.slice(0, 7))}</span><span class="z">${fmtSigned(c.z)}</span>`
    ).join("");
    return `<article class="card score">
      <div class="score-top"><span class="score-name"><a href="#${d.key}">${esc(d.name)}</a></span><span class="score-label">${esc(d.label)}</span></div>
      <div class="score-num">${d.score === null ? "—" : fmtSigned(d.score)}</div>
      <div class="small muted">3 个月前 ${d.score_3m_ago === null ? "—" : fmtSigned(d.score_3m_ago)}${delta === null ? "" : "，变化 " + fmtSigned(delta)} · ${esc((d.month || "").slice(0, 7))}</div>
      <div class="meter" role="img" aria-label="评分 ${fmtSigned(d.score)}，范围 −2 到 +2"><i style="left:${meterPos(d.score)}%"></i></div>
      <div class="meter-scale"><span>−2</span><span>0</span><span>+2</span></div>
      <div class="spark"><canvas id="spark-${d.key}" aria-label="${esc(d.name)}评分历史"></canvas></div>
      <details><summary>${esc(d.desc)}</summary><div class="comp">${comps || "<span>数据不足</span>"}</div></details>
    </article>`;
  }).join("");
  document.getElementById("method").textContent = DATA.score_method;
  drawSparks();
}

function drawSparks() {
  sparks.splice(0).forEach((c) => c.destroy());
  const P = palette();
  for (const d of DATA.dimensions) {
    const cv = document.getElementById(`spark-${d.key}`);
    if (!cv || !d.history.length) continue;
    sparks.push(new Chart(cv, {
      type: "line",
      data: { datasets: [
        { data: d.history.map(([t, v]) => ({ x: isoToMs(t), y: v })), borderColor: P.series[0], borderWidth: 1.5, pointRadius: 0, tension: 0 },
      ] },
      options: {
        animation: false, responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: {
          intersect: false, mode: "nearest", axis: "x", displayColors: false,
          backgroundColor: P.surface, titleColor: P.ink, bodyColor: P.ink2, borderColor: P.axis, borderWidth: 1,
          callbacks: { title: (it) => new Date(it[0].parsed.x).toISOString().slice(0, 7), label: (it) => `评分 ${fmtSigned(it.parsed.y)}` },
        } },
        scales: {
          x: { type: "time", display: false },
          y: { display: true, min: -3, max: 3, grid: { color: (c) => (c.tick.value === 0 ? P.axis : "transparent"), drawTicks: false }, ticks: { display: false }, border: { display: false } },
        },
      },
    }));
  }
}

// ---- 分维度 ----
function kpiHTML(k) {
  const pct = k.pctile === null ? "" : ` · 2000 年来 ${k.pctile}% 分位`;
  const prev = k.prev_text ? `前值 ${esc(k.prev_text)}` : "";
  return `<div class="card kpi">
    <div class="k-name">${esc(k.name)}</div>
    <div class="k-val">${esc(k.text)}<small>${esc(k.unit)}</small></div>
    <div class="k-meta">${esc(k.date)}${prev ? " · " + prev : ""}${pct}</div>
  </div>`;
}

function chartCardHTML(spec) {
  const sub = [spec.unit, spec.last_date ? `最新 ${spec.last_date}` : ""].filter(Boolean).join(" · ");
  return `<article class="card chart" id="c-${spec.id}">
    <h3>${esc(spec.title)}</h3>
    <div class="c-sub">${esc(sub)}</div>
    ${spec.series.length > 1 ? `<div class="legend">${legendHTML(spec)}</div>` : ""}
    <div class="c-body"><canvas role="img" aria-label="${esc(spec.title)}"></canvas></div>
    ${spec.note ? `<div class="c-note">${esc(spec.note)}</div>` : ""}
    <div class="c-foot"><span></span><button type="button" data-table="${spec.id}">看数据表</button></div>
    <div class="c-table" hidden></div>
  </article>`;
}

function renderSections() {
  const root = document.getElementById("sections");
  document.getElementById("chips").innerHTML = DATA.sections.map((s) => `<a href="#${s.key}">${esc(s.name)}</a>`).join("");
  root.innerHTML = DATA.sections.map((s) => {
    const dim = DATA.dimensions.find((d) => d.key === s.key) || {};
    const kpis = (DATA.kpis[s.key] || []).map(kpiHTML).join("");
    const groups = s.groups.map((g) => `
      <h3 class="group-title">${esc(g.name)}</h3>
      <div class="charts">${g.charts.map((id) => chartCardHTML(DATA.charts[id])).join("")}</div>`).join("");
    return `<section class="dim" id="${s.key}">
      <h2>${esc(s.name)}</h2>
      <p class="desc">${esc(dim.desc || "")}</p>
      <div class="kpis">${kpis}</div>
      ${groups}
    </section>`;
  }).join("");

  root.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-table]");
    if (!b) return;
    const box = b.closest(".chart").querySelector(".c-table");
    if (box.hidden) {
      box.innerHTML = tableHTML(DATA.charts[b.dataset.table]);
      box.hidden = false;
      b.textContent = "收起数据表";
    } else {
      box.hidden = true;
      b.textContent = "看数据表";
    }
  });

  // 滚到附近再画，减轻首屏负担
  const io = new IntersectionObserver((entries) => {
    for (const en of entries) {
      if (!en.isIntersecting) continue;
      io.unobserve(en.target);
      const id = en.target.id.slice(2);
      if (!charts.has(id)) charts.set(id, drawChart(en.target.querySelector("canvas"), DATA.charts[id], { startMs: rangeStartMs() }));
    }
  }, { rootMargin: "400px 0px" });
  root.querySelectorAll(".chart").forEach((el) => io.observe(el));
}

function redrawAll() {
  drawSparks();
  const s = rangeStartMs();
  charts.forEach((c, id) => {
    const cv = c.canvas;
    c.destroy();
    charts.set(id, drawChart(cv, DATA.charts[id], { startMs: s }));
  });
  document.querySelectorAll(".chart").forEach((el) => {
    const spec = DATA.charts[el.id.slice(2)];
    const lg = el.querySelector(".legend");
    if (lg) lg.innerHTML = legendHTML(spec);
  });
}

// ---- 数据状态 ----
function renderHealth() {
  const st = DATA.status || {};
  const series = st.series || {};
  const failed = st.failed || [];
  const rows = Object.entries(series).map(([id, v]) =>
    `<tr><td>${esc(id)}</td><td style="text-align:left">${esc(v.name)}</td><td>${esc(v.freq)}</td><td>${esc(v.last_obs || "—")}</td><td class="${v.ok ? "" : "bad"}">${v.ok ? "正常" : "失败/缺失"}</td></tr>`
  ).join("");
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${Object.keys(series).length} 条 FRED 序列，${failed.length ? `<span class="bad">${failed.length} 条这次没刷新</span>` : "全部正常"}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的序列沿用上次成功下载的数据。
      ${st.effr_expect_error ? `市场隐含 EFFR 这次读取失败：${esc(st.effr_expect_error)}` : ""}</p>
      <div class="table-wrap"><table class="data"><tr><th>FRED ID</th><th style="text-align:left">名称</th><th>频率</th><th>最新观测</th><th>状态</th></tr>${rows}</table></div>
    </details>`;
}

async function main() {
  renderNav("macro.html");
  try {
    DATA = await loadJSON("data/macro/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新宏观数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  renderRange();
  renderScores();
  renderSections();
  renderHealth();
  onSchemeChange(redrawAll);
}

main();
