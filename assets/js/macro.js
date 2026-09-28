// 宏观页分三层：结论（整体环境 + 五句状态）→ 依据（每维几个数 + 对应的图）→ 时间（即将发布、最近发布）。
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

// ---- 第一层：结论 ----
function renderVerdict() {
  const v = DATA.verdict || {};
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">整体环境</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    <div class="v-sub">${esc(v.sub || "")}</div>`;
}

// 判断变化日志：出现第一条「从 A 变成 B」之前不占位置，说明放在页脚
function renderStateLog() {
  const changes = (DATA.state_log || []).filter((r) => r.from);
  const el = document.getElementById("statelog");
  if (!changes.length) return;
  el.hidden = false;
  el.innerHTML = `<div class="v-label">判断变化（近 30 天）</div>
    <ul class="log">${changes.map((r) => `<li><span class="muted">${esc(r.date)}</span>
      <span><b>${esc(r.name)}</b>：${esc(r.from)} → <b>${esc(r.to)}</b></span>
      <span class="small muted">${esc(r.trigger || "当天没有匹配到发布，可能是数据修订或市场变量变化")}</span></li>`).join("")}</ul>`;
}

// 五个维度一行一个：名字 | 一句话 | 标签（靠右），每行格式一样
function renderStates() {
  document.getElementById("states").innerHTML = DATA.dimensions.map((d) => `
    <a class="st-row" href="#${d.key}">
      <span class="st-name">${esc(d.name)}</span>
      <span class="st-head">${esc(d.head)}</span>
      <span class="st-label tag-state">${esc(d.label)}</span>
    </a>`).join("");
}

// ---- 第二层：每维的几个数 ----
function chgHTML(c) {
  if (!c) return `<span class="muted">—</span>`;
  const arrow = c.dir === "up" ? "↑" : c.dir === "down" ? "↓" : "→";
  return `<span>${arrow} ${esc(c.text)}</span><div class="small muted">${esc(c.label)} ${esc(c.base)}</div>`;
}

function metricsHTML(d) {
  const rows = (d.metrics || []).map((m) => `
    <div class="ev">
      <span class="ev-name">${m.chart ? `<a href="#c-${esc(m.chart)}">${esc(m.name)}</a>` : esc(m.name)}${m.model ? ' <span class="tag">预测</span>' : ""}${m.note ? `<div class="small muted">${esc(m.note)}</div>` : ""}</span>
      <span class="ev-val"><i class="m-label">最新</i><b>${esc(m.text)}</b><small>${esc(m.unit)}</small><div class="small muted">${esc(m.date)}</div></span>
      <span class="ev-chg"><i class="m-label">较上期</i>${chgHTML(m.chg)}</span>
      <span class="ev-anchor"><i class="m-label">对照</i>${esc(m.anchor || "—")}</span>
    </div>`).join("");
  if (!rows) return "";
  return `<div class="card evid"><div class="ev ev-head"><span>指标</span><span>最新</span><span>较上期</span><span>对照的锚点</span></div>${rows}</div>`;
}

// 克利夫兰联储 Nowcast 与实际的对照，只放在通胀一块
function nowcastHTML() {
  const rows = ((DATA.releases || {}).nowcast || []).map((x) => `<tr><td>${esc(x.measure)}</td><td>${esc(x.period)}</td>
      <td>${x.nowcast === "" ? "—" : fmtNum(+x.nowcast)}</td><td>${x.actual === "" ? "—" : fmtNum(+x.actual)}</td></tr>`).join("");
  if (!rows) return "";
  return `<details class="more"><summary>克利夫兰联储 Nowcast 与实际对照（同比 %）</summary>
    <div class="card rel"><div class="table-wrap"><table class="data"><tr><th>口径</th><th>期间</th><th>Nowcast</th><th>实际</th></tr>${rows}</table></div></div>
  </details>`;
}

// ---- 第三层：时间 ----
// 即将发布：放在第一屏右边，按北京时间的日期分组；可能改写标签的那几期可以展开看情景
const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
function renderUpcoming() {
  const r = DATA.releases || { upcoming: [] };
  const st = DATA.status || {};
  const el = document.getElementById("upcoming");
  if (!r.upcoming.length) {
    el.innerHTML = `<p class="small muted">${esc(st.calendar_error ? `日历这次没取到：${st.calendar_error}` : "未来几天没有重要发布。")}</p>`;
    return;
  }
  const scen = (sc) => `<details class="scen">
      <summary>可能改写「${sc.affects.map(esc).join("」「")}」</summary>
      <div class="small muted">现在：${esc(sc.current)}</div>
      <ul class="scen-list">${sc.segments.map((g) => `<li class="${g.changed ? "chg" : ""}${g.forecast ? " fc" : ""}">
        <span class="scen-rng">新值 ${esc(g.range)}${g.forecast ? ' <span class="fc-tag">← 预期</span>' : ""}</span>
        <span>${g.changed ? "<b>" + esc(g.result) + "</b>" : esc(g.result)}${g.detail ? `<span class="small muted">（${esc(g.detail)}）</span>` : ""}</span>
      </li>`).join("")}</ul></details>`;
  const days = new Map();
  for (const x of r.upcoming) {
    const [md, hm] = (x.bj || "").split(" ");
    if (!days.has(md)) days.set(md, []);
    days.get(md).push({ ...x, hm });
  }
  const year = (DATA.asof || "").slice(0, 4);
  el.innerHTML = [...days.entries()].map(([md, xs]) => {
    const wd = new Date(`${year}-${md}T00:00:00Z`).getUTCDay();
    return `<div class="up-day">${esc(md)}<span>周${WEEK[wd] ?? ""}</span></div>
      <ul class="up-list">${xs.map((x) => {
        const sc = x.scenario && x.scenario.affects && x.scenario.affects.length ? x.scenario : null;
        return `<li class="${x.impact === "High" ? "hi" : ""}">
          <span class="up-time">${esc(x.hm || "")}</span>
          <span class="up-title">${esc(x.title)}${x.ref ? `<small>${esc(x.ref)}</small>` : ""}</span>
          <span class="up-num"><b>${esc(x.forecast_text || "—")}</b><small>前值 ${esc(x.previous_text || "—")}</small></span>
          ${sc ? scen(sc) : ""}
        </li>`;
      }).join("")}</ul>`;
  }).join("");
}

// 最近发布：还没有累积数据时整段不显示
function renderRecent() {
  const recent = (DATA.releases || {}).recent || [];
  if (!recent.length) return;
  document.getElementById("recent-wrap").hidden = false;
  const rows = recent.map((x) => `<tr>
      <td>${esc(x.bj)}</td><td style="text-align:left">${esc(x.title)}<div class="small muted">${esc(x.ref || "")}</div></td>
      <td><b>${esc(x.actual_text ?? "—")}</b></td><td>${esc(x.forecast_text || "—")}</td>
      <td>${x.verdict ? `${x.dir === "pos" ? "↑" : x.dir === "neg" ? "↓" : "="} ${esc(x.verdict)}<div class="small muted">${esc(x.surprise_text)}</div>` : `<span class="muted">${esc(x.status || "无预期")}</span>`}</td>
      <td>${esc(x.previous_text || "—")}</td>
      <td style="text-align:left" class="small">${x.impact ? (x.impact.changed ? "<b>" + esc(x.impact.text) + "</b>" : esc(x.impact.text)) : '<span class="muted">—</span>'}</td>
      <td class="small">${esc(x.market || "—")}</td></tr>`).join("");
  document.getElementById("recent").innerHTML = `<div class="table-wrap"><table class="data"><tr><th>北京时间</th><th style="text-align:left">指标</th><th>实际</th><th>预期</th><th>意外</th><th>前值</th><th style="text-align:left">对判断的影响</th><th>市场反应</th></tr>${rows}</table></div>`;
}

// ---- 页脚：方法与说明 ----
function renderNotes() {
  const log = DATA.state_log || [];
  const bits = [`<p>${esc(DATA.method || "")}</p>`];
  if (log.length && !log.some((r) => r.from)) {
    bits.push(`<p>判断变化日志从 ${esc(log[log.length - 1].date)} 开始记录；任一维度或整体环境的状态标签变了，会出现在页面最上面，并注明当时发布了什么数据。</p>`);
  }
  bits.push("<p>由固定规则按经济锚点判断，仅供参考，不构成投资建议。</p>");
  document.getElementById("notes").innerHTML = bits.join("");
}

// ---- 第二层：依据 ----
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
    const all = s.groups.flatMap((g) => g.charts);
    const core = all.filter((id) => DATA.charts[id].core);
    const more = s.groups.map((g) => ({ name: g.name, charts: g.charts.filter((id) => !DATA.charts[id].core) }))
      .filter((g) => g.charts.length);
    const nMore = more.reduce((a, g) => a + g.charts.length, 0);
    return `<section class="dim" id="${s.key}">
      <h2>${esc(s.name)}<span class="tag-state">${esc(dim.label || "")}</span></h2>
      ${metricsHTML(dim)}
      ${s.key === "inflation" ? nowcastHTML() : ""}
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

// 从指标名跳到折叠区里的图时，先展开
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
  renderRange();
  renderSections();
  renderUpcoming();
  renderRecent();
  renderNotes();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
