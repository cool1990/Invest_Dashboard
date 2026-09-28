// 半导体页：共用的三层版式在 board.js，这里放领先指标一览、说明和数据状态。
"use strict";

const LINE_NAME = { ai: "AI 算力", trad: "传统芯片", both: "两条线" };
const DIR_CLASS = { "偏多": "up", "偏空": "down" };

// 领先指标一览：按领先多久分组，每行写方向（对景气偏多 / 偏空 / 中性）
function renderLeading() {
  const items = DATA.leading || [];
  const el = document.getElementById("leading");
  if (!items.length) {
    el.innerHTML = `<h2 id="lead-title" class="v-label">领先指标</h2><p class="small muted">还没有数据。</p>`;
    return;
  }
  const scored = items.filter((x) => x.score);
  const n = (d) => scored.filter((x) => x.dir === d).length;
  const tiers = [...new Set(items.map((x) => x.tier))];
  el.innerHTML = `
    <div class="lead-head"><h2 id="lead-title" class="v-label">领先指标一览</h2>
      <span class="small muted">计入合计 ${scored.length} 项：偏多 ${n("偏多")}、偏空 ${n("偏空")}、中性 ${n("中性")}</span></div>
    ${tiers.map((t) => `<div class="lead-tier">领先 ${esc(t)}</div>
      <ul class="lead-list">${items.filter((x) => x.tier === t).map((x) => `<li>
        <span class="lead-name">${esc(x.name)}<span class="tag tag-ref">${esc(LINE_NAME[x.line] || x.line)}</span>
          <div class="small muted">${esc(x.meaning)}${x.score ? "" : "（不计入合计）"}</div></span>
        <span class="lead-val"><b>${esc(x.value)}</b><div class="small muted">${esc(x.date)}</div></span>
        <span class="lead-dir ${DIR_CLASS[x.dir] || ""}">${esc(x.dir)}</span>
      </li>`).join("")}</ul>`).join("")}`;
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
  renderVerdict();
  renderStateLog();
  renderLeading();
  renderStates();
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
