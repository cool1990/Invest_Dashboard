// 半导体页：共用的版式在 board.js。第一屏按「结论 → 为什么（供需五个维度）→ 分环节 → 后续关注」排，
// 下面是指标详解（每个指标附说明）。
"use strict";

// 周期刻度：上行早 → 中 → 后 → 下行早 → 中 → 后，震荡另标；只标整体所在的一格
function trackHTML(p) {
  const cell = (name) => `<li class="${p.name === name ? "on" : ""} ${name.startsWith("上行") ? "up" : "down"}">
      <span>${esc(name.slice(2))}</span>${p.name === name ? "<b>当前</b>" : ""}</li>`;
  return `<div class="track" role="img" aria-label="周期位置：${esc(p.name)}">
    <div class="track-seg"><div class="track-h">上行</div><ol>${p.cycle.slice(0, 3).map(cell).join("")}</ol></div>
    <div class="track-seg"><div class="track-h">下行</div><ol>${p.cycle.slice(3).map(cell).join("")}</ol></div>
    <div class="track-seg flat"><div class="track-h">&nbsp;</div><ol><li class="${p.name === "震荡" ? "on" : ""}"><span>震荡</span>${p.name === "震荡" ? "<b>当前</b>" : ""}</li></ol></div>
  </div>`;
}

// 为什么：供需五个维度，一行一个：维度 | 标签 | 投哪个阶段 | 理由
function whyHTML(p) {
  const dims = DATA.dimensions.filter((d) => d.side);
  const sides = [...new Set(dims.map((d) => d.side))];
  const rows = sides.map((side) => `<div class="sd-side">${esc(side)}</div>` + dims.filter((d) => d.side === side).map((d) => `
      <a class="sd-row" href="#${esc(d.key)}">
        <span class="sd-name">${esc(d.name)}</span>
        <span class="sd-label"><span class="tag-state">${esc(d.label)}</span></span>
        <span class="sd-vote">${d.vote ? `投${esc(d.vote)}` : "—"}</span>
        <span class="sd-why">${esc(d.vote_why || d.head || "")}</span>
      </a>`).join("")).join("");
  const foot = p.tally ? `五个维度：${esc(p.tally)} → <b>${esc(p.name)}</b>（票最多的阶段胜出，平票取中期）` : "";
  return `<h3 class="sub-h">为什么：供需五个维度</h3>
    <div class="sd">${rows}</div>${foot ? `<p class="sd-foot small">${foot}</p>` : ""}`;
}

function renderPosition() {
  const p = DATA.position;
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">景气位置</div>
    <h2 class="v-head">${esc(p.head)}</h2>
    ${p.meaning ? `<p class="v-meaning">${esc(p.meaning)}</p>` : ""}
    ${p.split ? `<p class="v-split small">${esc(p.split)}</p>` : ""}
    ${trackHTML(p)}
    ${whyHTML(p)}`;
}

// 分环节：每个环节先列领先指标（有才列），再列上市公司的营收同比、环比、毛利率（各带上期）
const pct = (v, d = 1) => (v === null || v === undefined ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const gmTxt = (v) => (v === null || v === undefined ? "—" : `${v.toFixed(1)}%`);
function cellHTML(v, prev, fmt) {
  return `<td><b>${fmt(v)}</b>${prev === null || prev === undefined ? "" : `<small>上期 ${fmt(prev)}</small>`}</td>`;
}

function renderSegments() {
  const segs = DATA.segments || [];
  const cls = (s) => (s.startsWith("上行") ? "up" : s === "下行" ? "down" : s === "触底回升" ? "turn" : "");
  const block = (x) => {
    const lead = x.leading.length ? `<ul class="sg-lead">${x.leading.map((l) => `<li><span class="tag tag-lead">领先</span>
        <b>${esc(l.name)}</b> ${esc(l.value)}<span class="muted"> · ${esc(l.note)}</span></li>`).join("")}</ul>` : "";
    const rows = x.companies.map((c) => `<tr><td>${esc(c.name)}<small>${esc(c.period)}</small></td>
        ${cellHTML(c.yoy, c.yoy_prev, pct)}${cellHTML(c.qoq, c.qoq_prev, pct)}${cellHTML(c.gm, c.gm_prev, gmTxt)}</tr>`).join("");
    const table = rows ? `<div class="table-wrap"><table class="data co"><tr><th>公司 · 期间</th><th>营收同比</th><th>环比</th><th>毛利率</th></tr>${rows}</table></div>` : "";
    return `<div class="sg-block">
      <div class="sg-head"><span class="sg-name">${esc(x.name)}</span><span class="sg-state ${cls(x.state)}">${esc(x.state)}</span>
        <span class="small muted">${esc(x.basis || "")}</span></div>
      <div class="small muted sg-what">${esc(x.what)}</div>
      ${lead}${table}
    </div>`;
  };
  document.getElementById("segments").innerHTML = `<h3 class="sub-h">分环节：各环节现在怎样</h3>
    <p class="small muted">每个环节先看领先指标，再看上市公司最新一期的营收同比、环比和毛利率（小字是上一期）。状态按主指标同比及其变化定，不进整体判断。</p>
    ${segs.map(block).join("")}
    <p class="small muted sg-foot">季报期间按财报期中所在的日历季度写；台湾公司是月营收，没有毛利率；「—」表示暂无数据。</p>`;
}

function renderWatch() {
  const w = (DATA.position || {}).watch || [];
  const el = document.getElementById("watch");
  if (!w.length) { el.hidden = true; return; }
  const phase = (DATA.position || {}).phase;
  const sub = phase === "上行" ? "出现下面的情况，说明上行可能见顶" : phase === "下行" ? "出现下面的情况，说明下行可能见底" : "哪边先出现，方向就往哪边走";
  el.innerHTML = `<h3 class="sub-h">后续关注</h3><p class="small muted">${esc(sub)}</p>
    <ul class="wl">${w.map((x) => `<li><div><span class="w-name">${esc(x.name)}</span><span class="w-now">现在 ${esc(x.now)}</span></div>
      <div class="w-sig"><b>${esc(x.cond)}</b> → ${esc(x.meaning)}</div></li>`).join("")}</ul>`;
}

function renderNotes() {
  document.getElementById("notes").innerHTML = `<p>${esc(DATA.method || "")}</p>
    <p>由固定规则按经济锚点判断，仅供参考，不构成投资建议。</p>`;
}

// 数据状态：先列各来源（笔记、台湾、SEC、KOSIS、手工），再列 FRED 序列
function renderHealth() {
  const st = DATA.status || {};
  const src = st.sources || [];
  const series = st.series || {};
  const bad = src.filter((s) => !s.ok).length + (st.failed || []).length;
  const srcRows = src.map((s) => `<tr><td style="text-align:left">${esc(s.name)}</td><td>${esc(s.last_obs || "—")}</td>
      <td class="${s.ok ? "" : "bad"}">${s.ok ? (s.last_obs ? "正常" : "无数据") : "失败"}</td><td style="text-align:left" class="small">${esc(s.note || "")}</td></tr>`).join("");
  const fredRows = Object.entries(series).map(([id, v]) =>
    `<tr><td style="text-align:left">${esc(id)} ${esc(v.name)}</td><td>${esc(v.last_obs || "—")}</td><td class="${v.ok ? "" : "bad"}">${v.ok ? "正常" : "失败/缺失"}</td><td></td></tr>`).join("");
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${src.length} 个来源、${Object.keys(series).length} 条 FRED 序列，${bad ? `<span class="bad">${bad} 项这次没刷新或失败</span>` : "全部正常"}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的来源沿用上次成功下载的数据；KOSIS 没有 key 时库存周期改用美国数据。</p>
      <div class="table-wrap"><table class="data"><tr><th style="text-align:left">来源</th><th>最新观测</th><th>状态</th><th style="text-align:left">说明</th></tr>${srcRows}${fredRows}</table></div>
    </details>`;
}

async function main() {
  renderNav("semis.html");
  try {
    DATA = await loadJSON("data/semis/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新半导体数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  renderPosition();
  renderSegments();
  renderWatch();
  renderStateLog();
  renderRange();
  renderSections();
  renderUpcoming();
  renderNotes();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
