// 宏观页：整体环境 → 五维状态 → 即将发布 → 核心指标 → 最近发布 → 明细图表。
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

// ---- 总判断 ----
function renderVerdict() {
  const v = DATA.verdict || {};
  const byDim = new Map();
  for (const p of v.points || []) {
    if (!byDim.has(p.dim)) byDim.set(p.dim, []);
    byDim.get(p.dim).push(p);
  }
  const groups = [...byDim.entries()].map(([dim, ps]) => `
    <div class="risk-dim"><div class="risk-name">${esc(dim)}</div><ul class="points">${ps.map((p) =>
      `<li>${badge(p.level)}<span class="pt-name">${esc(p.name)}${p.value ? ` <b>${esc(p.value)}</b><small>${esc(p.unit)}</small>` : ""}</span><span class="pt-text">${esc(p.text)}</span></li>`
    ).join("")}</ul></div>`).join("");
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">整体环境</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    <div class="v-sub">${esc(v.sub || "")}</div>
    ${groups ? `<details class="risks"><summary>分歧与风险（${(v.points || []).length} 条）</summary>${groups}</details>` : ""}
    <div class="small muted" style="margin-top:10px">由固定规则按经济锚点判断（阈值见各指标说明），仅供参考，不构成投资建议。</div>`;
}

// ---- 判断变化日志 ----
function renderStateLog() {
  const rows = DATA.state_log || [];
  const el = document.getElementById("statelog");
  const changes = rows.filter((r) => r.from);
  const first = rows.length && !changes.length;
  el.innerHTML = `<div class="v-label">判断变化日志（近 30 天）</div>
    ${changes.length ? `<ul class="log">${changes.map((r) => `<li><span class="muted">${esc(r.date)}</span>
        <span><b>${esc(r.name)}</b>：${esc(r.from)} → <b>${esc(r.to)}</b></span>
        <span class="small muted">${esc(r.trigger || "当天没有匹配到发布，可能是数据修订或市场变量变化")}</span></li>`).join("")}</ul>`
      : `<p class="small muted">${first ? `从 ${esc(rows[rows.length - 1].date)} 开始记录，之后任一维度或整体环境的状态变化都会列在这里，并注明当时发布了什么数据。` : "暂无记录。"}</p>`}`;
}

// ---- 五维状态 ----
function renderStates() {
  document.getElementById("states").innerHTML = DATA.dimensions.map((d) => `
    <article class="card state">
      <div class="score-top"><span class="score-name"><a href="#${d.key}">${esc(d.name)}</a></span><span class="score-label">${esc(d.label)}</span></div>
      <div class="state-head">${esc(d.head)}</div>
      <ul class="state-pts">${d.points.map((p) => `<li>${badge(p.level)} <span>${esc(p.text)}</span></li>`).join("")}</ul>
      ${d.next.length ? `<div class="state-next"><span class="muted">接下来：</span>${d.next.map((n) =>
        `<span>${esc(n.bj)} ${esc(n.title)}${n.forecast_text ? `（预期 ${esc(n.forecast_text)}）` : ""}</span>`).join("；")}</div>` : ""}
    </article>`).join("");
  document.getElementById("method").textContent = DATA.method;
}

// ---- 即将发布 ----
const DIM_NAME = { growth: "增长", inflation: "通胀", liquidity: "流动性", fiscal: "财政", policy: "货币政策" };
function renderUpcoming() {
  const r = DATA.releases || { upcoming: [] };
  const st = DATA.status || {};
  const scen = (sc) => `<tr class="scen"><td></td><td colspan="5" style="text-align:left">
      <div class="small muted">现在：${esc(sc.current)}</div>
      <ul class="scen-list">${sc.segments.map((g) => `<li class="${g.changed ? "chg" : ""}${g.forecast ? " fc" : ""}">
        <span class="scen-rng">新值 ${esc(g.range)}</span>
        <span>${g.changed ? "<b>" + esc(g.result) + "</b>" : esc(g.result)}${g.detail ? `<span class="small muted">（${esc(g.detail)}）</span>` : ""}${g.forecast ? ' <span class="fc-tag">← 预期在这里</span>' : ""}</span>
      </li>`).join("")}</ul></td></tr>`;
  const rows = r.upcoming.map((x) => `<tr class="${x.scenario ? "has-scen" : ""}">
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}${x.impact === "High" ? ' <span class="small muted">高影响</span>' : ""}<div class="small muted">${esc(x.ref || "")}</div></td>
      <td>${esc(DIM_NAME[x.dim] || "—")}</td><td><b>${esc(x.forecast_text || "—")}</b></td><td>${esc(x.previous_text || "—")}</td>
      <td style="text-align:left" class="small">${esc(x.nowcast_text || "")}</td></tr>${x.scenario ? scen(x.scenario) : ""}`).join("");
  document.getElementById("upcoming").innerHTML = rows
    ? `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>影响</th><th>预期</th><th>前值</th><th style="text-align:left">模型预测</th></tr>${rows}</table></div>`
    : `<p class="small muted">${esc(st.calendar_error ? `日历这次没取到：${st.calendar_error}` : "未来几天没有重要发布。")}</p>`;
}

// ---- 核心指标 ----
function changeHTML(c) {
  if (!c) return `<span class="muted">—</span>`;
  const arrow = c.dir === "up" ? "↑" : c.dir === "down" ? "↓" : "→";
  return `<span>${arrow} ${esc(c.text)}</span><div class="small muted">${esc(c.label)} ${esc(c.base)}</div>`;
}

function nextHTML(s) {
  const bits = [];
  if (s.next) bits.push(`<div>${esc(s.next.bj)}${s.next.forecast_text ? ` · 预期 <b>${esc(s.next.forecast_text)}</b>` : ""}</div>`);
  if (s.nowcast && s.nowcast.length) bits.push(`<div class="small">Nowcast ${s.nowcast.map((n) => `${esc(n.period)} ${fmtNum(n.value)}%`).join("、")}</div>`);
  if (s.last_surprise && s.last_surprise.surprise_text) bits.push(`<div class="small muted">上次意外 ${esc(s.last_surprise.surprise_text)} · ${esc(s.last_surprise.verdict || "")}</div>`);
  return bits.join("") || `<span class="muted">—</span>`;
}

function renderSignals() {
  const el = document.getElementById("signals");
  const head = `<div class="sig sig-head"><span>指标</span><span>最新</span><span>较上期</span><span>较一年前</span><span>下一次发布</span><span>这意味着什么</span></div>`;
  el.innerHTML = head + DATA.dimensions.map((d) => {
    const rows = (DATA.signals[d.key] || []).map((s) => `
      <div class="sig">
        <span class="sig-name">${s.chart ? `<a href="#c-${esc(s.chart)}">${esc(s.name)}</a>` : esc(s.name)}${s.cmp.note ? `<div class="small muted">变化${esc(s.cmp.note)}${s.cmp.now ? `（现 ${esc(s.cmp.now)}）` : ""}</div>` : ""}</span>
        <span class="sig-val"><b>${esc(s.text)}</b><small>${esc(s.unit)}</small><div class="small muted">${esc(s.date)}${s.pctile === null ? "" : ` · 历史 ${s.pctile}% 分位`}</div></span>
        <span class="sig-chg"><i class="m-label">较上期</i>${changeHTML(s.cmp.short)}</span>
        <span class="sig-chg"><i class="m-label">较一年前</i>${changeHTML(s.cmp.long)}</span>
        <span class="sig-cons"><i class="m-label">下一次发布</i>${nextHTML(s)}</span>
        <span class="sig-interp">${badge(s.level)} ${esc(s.interp)}</span>
      </div>`).join("");
    return `<div class="sig-dim"><a href="#${d.key}">${esc(d.name)}</a><span class="muted small">${esc(d.label)}</span></div>${rows}`;
  }).join("");
}

// ---- 最近发布 ----
function renderReleases() {
  const r = DATA.releases || { recent: [], nowcast: [] };
  const st = DATA.status || {};
  const recentRows = r.recent.map((x) => `<tr>
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}<div class="small muted">${esc(x.ref || "")}</div></td>
      <td><b>${esc(x.actual_text ?? "—")}</b></td><td>${esc(x.forecast_text || "—")}</td>
      <td>${x.verdict ? `${x.dir === "pos" ? "↑" : x.dir === "neg" ? "↓" : "="} ${esc(x.verdict)}<div class="small muted">${esc(x.surprise_text)}</div>` : `<span class="muted">${esc(x.status || "无预期")}</span>`}</td>
      <td>${esc(x.previous_text || "—")}</td>
      <td style="text-align:left" class="small">${x.impact ? (x.impact.changed ? "<b>" + esc(x.impact.text) + "</b>" : esc(x.impact.text)) : '<span class="muted">—</span>'}</td>
      <td class="small">${esc(x.market || "—")}</td></tr>`).join("");
  const ncRows = r.nowcast.map((x) => `<tr><td style="text-align:left">${esc(x.measure)}</td><td>${esc(x.period)}</td>
      <td>${x.nowcast === "" ? "—" : fmtNum(+x.nowcast)}</td><td>${x.actual === "" ? "—" : fmtNum(+x.actual)}</td></tr>`).join("");
  const empty = (msg) => `<p class="small muted">${esc(msg)}</p>`;
  document.getElementById("releases").innerHTML = `
    <div class="card rel">
      <p class="small muted">近 30 天。实际值取 FRED 当前值（可能已修订），与发布前最后一次看到的市场一致预期比较，↑ 表示偏强或偏热。「对判断的影响」是去掉这一期和加上这一期各按规则算一次的差别；「市场反应」是发布当天 2 年期国债的变化和年底 EFFR 隐含值在发布前后的变化。</p>
      ${recentRows ? `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>实际</th><th>预期</th><th>意外</th><th>前值</th><th style="text-align:left">对判断的影响</th><th>市场反应</th></tr>${recentRows}</table></div>`
        : empty("预期从 2026-09-28 开始累积，9/29 起的发布会陆续出现在这里。")}
    </div>
    <div class="card rel">
      <h3>克利夫兰联储通胀 Nowcast（同比 %）</h3>
      <p class="small muted">模型预测，实际值出来后可以对照它准不准。</p>
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
      <p class="desc">${esc(dim.head || "")}</p>
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
  renderStateLog();
  renderStates();
  renderUpcoming();
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
