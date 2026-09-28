// 美股页：共用的三层版式在 board.js。这里放观察名单、说明和数据状态。
"use strict";

function namesHTML() {
  const rows = DATA.names || [];
  if (!rows.length) return "";
  const body = rows.map((r) => `<tr>
      <td style="text-align:left">${esc(r.ticker)}${r.core ? ' <span class="tag tag-ref">核心</span>' : ""}</td>
      <td>${esc(r.forward_pe)}</td>
      <td>${esc(r.revision)}</td>
      <td style="text-align:left">${esc(r.signal)}</td>
      <td>${esc(r.rsi)}</td>
    </tr>`).join("");
  return `<div class="card evid"><p class="small muted" style="padding:10px 16px 0">观察名单 ${esc(DATA.names_date || "")}，只列美股。标「核心」的 8 家进入上面的中位数，其余不进判断。</p>
    <div class="table-wrap"><table class="data"><tr><th style="text-align:left">代码</th><th>远期市盈率</th><th>30 日修正</th><th style="text-align:left">修正信号</th><th>RSI</th></tr>${body}</table></div></div>`;
}

function renderNotes() {
  document.getElementById("notes").innerHTML = `<p>${esc(DATA.method || "")}</p>
    <p>由固定规则按经济锚点判断，仅供参考，不构成投资建议。</p>`;
}

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
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的来源沿用上次成功下载的数据。10 年实际利率和 GDP 用宏观板块已经下载的文件，不在上面重复列。</p>
      <div class="table-wrap"><table class="data"><tr><th style="text-align:left">来源</th><th>最新观测</th><th>状态</th><th style="text-align:left">说明</th></tr>${srcRows}${fredRows}</table></div>
    </details>`;
}

async function main() {
  renderNav("us.html");
  try {
    DATA = await loadJSON("data/us/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新美股数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  sectionExtra = (key) => key === "earnings" ? namesHTML() : "";
  renderVerdict();
  renderStateLog();
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
