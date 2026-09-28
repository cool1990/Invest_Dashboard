// 加密货币页：共用的三层版式在 board.js。右侧是已整理指标的最新读数，没有经济数据发布日历。
"use strict";

function renderReadings() {
  const dims = DATA.dimensions || [];
  const el = document.getElementById("fresh");
  const blocks = dims.map((d) => {
    const rows = (d.metrics || []).map((m) => {
      const w = m.watch;
      const name = m.chart ? `<a href="#c-${esc(m.chart)}">${esc(m.name)}</a>` : esc(m.name);
      const ref = m.ref ? ` <span class="tag tag-ref">参考</span>` : "";
      const tag = w ? ` <span class="tag${w.hi ? " tag-alert" : ""}">${esc(w.mark || "注意")}</span>` : "";
      return `<li class="${w && w.hi ? "hi" : ""}">
        <span class="up-title">${name}${ref}${tag}</span>
        <span class="up-num"><b>${esc(m.text)}</b> ${esc(m.unit)}<small>${esc(m.date || "")}</small></span>
        <div class="read-chg"><span class="muted">较上期</span> ${chgHTML(m.chg)}</div>
        ${w ? `<p class="read-why">${esc(w.why)}</p>` : ""}
      </li>`;
    }).join("");
    return rows ? `<h3 class="read-dim">${esc(d.name)}</h3><ul class="reads">${rows}</ul>` : "";
  }).join("");
  el.innerHTML = blocks || `<p class="small muted">还没有数据。</p>`;
}

function renderNotes() {
  document.getElementById("notes").innerHTML = `<p>${esc(DATA.method || "")}</p>
    <p>由固定规则按锚点判断，仅供参考，不构成投资建议。Coin Metrics 社区数据以 CC BY-NC 使用；持有者成本、币龄和主导率来自 BGeometrics。</p>`;
}

function renderHealth() {
  const st = DATA.status || {};
  const src = st.sources || [];
  const bad = src.filter((s) => !s.ok).length;
  const rows = src.map((s) => `<tr><td style="text-align:left">${esc(s.name)}</td><td>${esc(s.last_obs || "—")}</td>
      <td class="${s.ok ? "" : "bad"}">${s.ok ? (s.last_obs ? "正常" : "无数据") : "失败"}</td>
      <td style="text-align:left" class="small">${esc(s.note || "")}</td></tr>`).join("");
  document.getElementById("health").innerHTML = `
    <details>
      <summary>数据状态：${src.length} 个来源，${bad ? `<span class="bad">${bad} 项这次没刷新或失败</span>` : "全部正常"}</summary>
      <p class="small muted">更新时间 ${esc(st.updated_at || "—")}（UTC）。没刷新的来源沿用上次成功下载的数据。</p>
      <div class="table-wrap"><table class="data"><tr><th style="text-align:left">来源</th><th>最新观测</th><th>状态</th><th style="text-align:left">说明</th></tr>${rows}</table></div>
    </details>`;
}

async function main() {
  renderNav("crypto.html");
  try {
    DATA = await loadJSON("data/crypto/dashboard.json");
  } catch (err) {
    document.getElementById("app").innerHTML = `<div class="card empty">数据还没生成：${esc(err.message)}<br>在 GitHub Actions 里运行一次「更新加密货币数据」即可。</div>`;
    return;
  }
  document.getElementById("asof").textContent = `数据更新于 ${(DATA.status?.updated_at || DATA.asof).replace("T", " ").replace("Z", " UTC")}`;
  renderVerdict();
  renderStateLog();
  renderStates();
  renderReadings();
  renderRange();
  renderSections();
  renderNotes();
  renderHealth();
  window.addEventListener("hashchange", openTarget);
  if (location.hash) openTarget();
  onSchemeChange(redrawAll);
}

main();
