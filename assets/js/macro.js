// 宏观页：总判断 → 核心指标 → 数据发布 → 明细图表。
"use strict";

const RANGES = [
  { key: "2y", label: "2 年", years: 2 },
  { key: "5y", label: "5 年", years: 5 },
  { key: "10y", label: "10 年", years: 10 },
  { key: "all", label: "全部", years: null },
];
const RANGE_KEY = "macro.range";
const LEVELS = {
  alert: { icon: "▲", text: "警示" },
  watch: { icon: "●", text: "关注" },
  ok: { icon: "○", text: "正常" },
};
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

function badge(level) {
  const l = LEVELS[level] || LEVELS.ok;
  return `<span class="lv lv-${level}"><b aria-hidden="true">${l.icon}</b>${l.text}</span>`;
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

// ---- 第一层：总判断 + 五维 ----
function renderVerdict() {
  const v = DATA.verdict || {};
  const pts = (v.points || []).map((p) =>
    `<li>${badge(p.level)}<span class="pt-name">${esc(p.dim)} · ${esc(p.name)} <b>${esc(p.value)}</b><small>${esc(p.unit)}</small></span><span class="pt-text">${esc(p.text)}</span></li>`
  ).join("");
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">总判断</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    <div class="v-sub">${esc(v.sub || "")}</div>
    ${pts ? `<div class="v-label" style="margin-top:14px">需要注意</div><ul class="points">${pts}</ul>` : ""}
    <div class="small muted" style="margin-top:10px">解读由固定规则生成（阈值见各指标说明），仅供参考，不构成投资建议。</div>`;
}

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
      <div class="small muted">3 个月前 ${d.score_3m_ago === null ? "—" : fmtSigned(d.score_3m_ago)}${delta === null ? "" : "，变化 " + fmtSigned(delta)}</div>
      <div class="meter" role="img" aria-label="评分 ${fmtSigned(d.score)}，范围 −2 到 +2"><i style="left:${meterPos(d.score)}%"></i></div>
      <div class="spark"><canvas id="spark-${d.key}" aria-label="${esc(d.name)}评分历史"></canvas></div>
      <div class="score-sum">${esc(d.summary || "")}</div>
      <details><summary>构成与口径</summary><p class="small">${esc(d.desc)}</p><div class="comp">${comps || "<span>数据不足</span>"}</div></details>
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

// ---- 第二层：核心指标 ----
function trendHTML(t) {
  if (!t) return `<span class="muted">—</span>`;
  const arrow = t.dir === "up" ? "↑" : t.dir === "down" ? "↓" : "→";
  return `<span>${arrow} ${esc(t.label)} ${esc(t.text)}</span>`;
}

function consHTML(c) {
  if (!c) return `<span class="muted">—</span>`;
  const sup = c.surprise_text ? `<div class="small">意外 ${esc(c.surprise_text)}${c.verdict ? " · " + esc(c.verdict) : ""}</div>` : "";
  return `<div>${esc(c.forecast_text)} <span class="muted small">${esc(c.source)}</span></div>${sup}`;
}

function renderSignals() {
  const el = document.getElementById("signals");
  const head = `<div class="sig sig-head"><span>指标</span><span>最新</span><span>预期 / 意外</span><span>前值</span><span>趋势</span><span>这意味着什么</span></div>`;
  el.innerHTML = head + DATA.dimensions.map((d) => {
    const rows = (DATA.signals[d.key] || []).map((s) => `
      <div class="sig">
        <span class="sig-name">${s.chart ? `<a href="#c-${esc(s.chart)}">${esc(s.name)}</a>` : esc(s.name)}</span>
        <span class="sig-val"><b>${esc(s.text)}</b><small>${esc(s.unit)}</small><div class="small muted">${esc(s.date)}${s.pctile === null ? "" : ` · ${s.pctile}% 分位`}</div></span>
        <span class="sig-cons"><i class="m-label">预期</i>${consHTML(s.consensus)}</span>
        <span class="sig-prev"><i class="m-label">前值</i>${esc(s.prev_text ?? "—")}</span>
        <span class="sig-trend"><i class="m-label">趋势</i>${trendHTML(s.trend)}</span>
        <span class="sig-interp">${badge(s.level)} ${esc(s.interp)}</span>
      </div>`).join("");
    return `<div class="sig-dim"><a href="#${d.key}">${esc(d.name)}</a><span class="muted small">${esc(d.label)}</span></div>${rows}`;
  }).join("");
}

// ---- 第三层：数据发布 ----
function renderReleases() {
  const r = DATA.releases || { recent: [], upcoming: [], nowcast: [] };
  const st = DATA.status || {};
  const recentRows = r.recent.map((x) => `<tr>
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}<div class="small muted">${esc(x.ref || "")}</div></td>
      <td><b>${esc(x.actual_text ?? "—")}</b></td><td>${esc(x.forecast_text || "—")}</td>
      <td>${x.verdict ? `${x.dir === "pos" ? "↑" : x.dir === "neg" ? "↓" : "="} ${esc(x.verdict)}<div class="small muted">${esc(x.surprise_text)}</div>` : `<span class="muted">${esc(x.status || "无预期")}</span>`}</td>
      <td>${esc(x.previous_text || "—")}</td></tr>`).join("");
  const upRows = r.upcoming.map((x) => `<tr>
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}${x.impact === "High" ? ' <span class="small muted">高影响</span>' : ""}</td>
      <td>${esc(x.forecast_text || "—")}</td><td>${esc(x.previous_text || "—")}</td></tr>`).join("");
  const ncRows = r.nowcast.map((x) => `<tr><td style="text-align:left">${esc(x.measure)}</td><td>${esc(x.period)}</td>
      <td>${x.nowcast === "" ? "—" : fmtNum(+x.nowcast)}</td><td>${x.actual === "" ? "—" : fmtNum(+x.actual)}</td></tr>`).join("");
  const empty = (msg) => `<p class="small muted">${esc(msg)}</p>`;
  document.getElementById("releases").innerHTML = `
    <div class="card rel">
      <h3>最近发布：实际 vs 预期</h3>
      <p class="small muted">近 30 天。实际值取 FRED 当前值（可能已修订），与发布前最后一次看到的市场一致预期比较。↑ 表示偏强或偏热。</p>
      ${recentRows ? `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>实际</th><th>预期</th><th>判断</th><th>前值</th></tr>${recentRows}</table></div>`
        : empty(st.calendar_events ? "近 30 天没有可对比的发布。" : "预期数据从本站上线后开始累积，下一次重要数据发布后这里会出现对比。")}
    </div>
    <div class="card rel">
      <h3>即将发布</h3>
      <p class="small muted">未来 ${10} 天，预期来自 ForexFactory 公开日历。</p>
      ${upRows ? `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>预期</th><th>前值</th></tr>${upRows}</table></div>`
        : empty(st.calendar_error ? `日历这次没取到：${st.calendar_error}` : "未来几天没有重要发布。")}
      <h3 style="margin-top:16px">模型预测：克利夫兰联储通胀 Nowcast（同比 %）</h3>
      ${ncRows ? `<div class="table-wrap"><table class="data"><tr><th style="text-align:left">口径</th><th>期间</th><th>Nowcast</th><th>实际</th></tr>${ncRows}</table></div>`
        : empty(st.nowcast_error ? `这次没取到：${st.nowcast_error}` : "暂无数据。")}
    </div>`;
}

// ---- 第四层：明细图表 ----
function chartCardHTML(spec) {
  const sub = [spec.unit, spec.last_date ? `最新 ${spec.last_date}` : ""].filter(Boolean).join(" · ");
  const interp = (spec.interp || []).map((x) => `<div class="c-interp">${badge(x.level)} <span class="ci-name">${esc(x.name)}</span>：${esc(x.text)}</div>`).join("");
  return `<article class="card chart" id="c-${spec.id}">
    <h3>${esc(spec.title)}</h3>
    <div class="c-sub">${esc(sub)}</div>
    ${interp}
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
    const all = s.groups.flatMap((g) => g.charts);
    const core = all.filter((id) => DATA.charts[id].core);
    const more = s.groups.map((g) => ({ name: g.name, charts: g.charts.filter((id) => !DATA.charts[id].core) }))
      .filter((g) => g.charts.length);
    const nMore = more.reduce((a, g) => a + g.charts.length, 0);
    return `<section class="dim" id="${s.key}">
      <h2>${esc(s.name)} <span class="score-label">${esc(dim.label || "")}</span></h2>
      <p class="desc">${esc(dim.summary || dim.desc || "")}</p>
      <div class="charts">${core.map((id) => chartCardHTML(DATA.charts[id])).join("")}</div>
      ${nMore ? `<details class="more"><summary>更多图表（${nMore} 张）</summary>
        ${more.map((g) => `<h3 class="group-title">${esc(g.name)}</h3><div class="charts">${g.charts.map((id) => chartCardHTML(DATA.charts[id])).join("")}</div>`).join("")}
      </details>` : ""}
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

  // 滚到附近（或展开折叠区）再画，减轻首屏负担
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

// 从核心指标跳到折叠区里的图时，先展开
function openTarget() {
  const id = decodeURIComponent(location.hash.slice(1));
  const el = id && document.getElementById(id);
  if (!el) return;
  const det = el.closest("details");
  if (det && !det.open) det.open = true;
  el.scrollIntoView();
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
  const extra = [
    st.effr_expect_error && `市场隐含 EFFR：${st.effr_expect_error}`,
    st.calendar_error && `经济日历预期：${st.calendar_error}`,
    st.nowcast_error && `克利夫兰联储 Nowcast：${st.nowcast_error}`,
  ].filter(Boolean);
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${Object.keys(series).length} 条 FRED 序列，${failed.length ? `<span class="bad">${failed.length} 条这次没刷新</span>` : "全部正常"}${extra.length ? `，<span class="bad">${extra.length} 个外部来源失败</span>` : ""}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的序列沿用上次成功下载的数据。${extra.map(esc).join("；")}</p>
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
  renderVerdict();
  renderScores();
  renderSignals();
  renderReleases();
  renderRange();
  renderSections();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
