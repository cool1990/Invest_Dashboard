// 半导体页：共用的三层版式在 board.js。这里放景气位置（周期刻度、两条线、投票依据、观察清单）、说明和数据状态。
"use strict";

const LINE_NAME = { ai: "AI 算力", trad: "传统芯片" };

// 周期刻度：上行早 → 中 → 后 → 下行早 → 中 → 后，标出两条线各在哪；震荡另标
function trackHTML(p) {
  const at = (name) => Object.entries(p.lines).filter(([, l]) => l.name === name).map(([k]) => LINE_NAME[k]);
  const cell = (name) => {
    const who = at(name);
    return `<li class="${who.length ? "on" : ""} ${name.startsWith("上行") ? "up" : "down"}">
      <span>${esc(name.slice(2))}</span>${who.map((w) => `<b>${esc(w)}</b>`).join("")}</li>`;
  };
  const flat = Object.entries(p.lines).filter(([, l]) => (l.name || "").startsWith("震荡")).map(([k]) => LINE_NAME[k]);
  return `<div class="track" role="img" aria-label="周期位置：${esc(p.head)}">
    <div class="track-seg"><div class="track-h">上行</div><ol>${p.cycle.slice(0, 3).map(cell).join("")}</ol></div>
    <div class="track-seg"><div class="track-h">下行</div><ol>${p.cycle.slice(3).map(cell).join("")}</ol></div>
    <div class="track-seg flat"><div class="track-h">&nbsp;</div><ol><li class="${flat.length ? "on" : ""}"><span>震荡</span>${flat.map((w) => `<b>${esc(w)}</b>`).join("")}</li></ol></div>
  </div>`;
}

// 投票依据：两条线的需求各一行，库存、价格、产能两条线共用
function votesHTML(p) {
  const ai = p.lines.ai.votes || [], trad = p.lines.trad.votes || [];
  const rows = [];
  if (ai[0] && ai[0][0].includes("需求")) rows.push(ai[0]);
  if (trad[0] && trad[0][0].includes("需求")) rows.push(trad[0]);
  const shared = (ai.length ? ai : trad).filter((v) => !v[0].includes("需求"));
  rows.push(...shared);
  if (!rows.length) return "";
  return `<details class="votes"><summary>为什么是这个阶段：各维度投票</summary>
    <ul>${rows.map(([dim, stage, why]) => `<li><span class="vote-dim">${esc(dim)}</span><span class="vote-st">${esc(stage)}</span><span>${esc(why)}</span></li>`).join("")}</ul>
    <p class="small muted">每个维度按经典半导体周期给早期 / 中期 / 后期投一票，票最多的阶段胜出，平票取中期。库存、价格、产能两条线共用。</p>
  </details>`;
}

function watchHTML(p) {
  const w = p.watch || [];
  if (!w.length) return "";
  return `<div class="watch"><div class="v-label">接下来盯这些：出现什么说明位置在变</div>
    <ul>${w.map((x) => `<li><span class="w-name">${esc(x.name)}</span><span class="w-now">现在 ${esc(x.now)}</span><span class="w-sig">${esc(x.signal)}</span></li>`).join("")}</ul></div>`;
}

function renderPosition() {
  const p = DATA.position;
  const v = DATA.verdict || {};
  if (!p) { renderVerdict(); return; }
  document.getElementById("verdict").innerHTML = `
    <div class="v-label">景气位置</div>
    <h2 class="v-head">${esc(v.headline || "—")}</h2>
    ${trackHTML(p)}
    <ul class="v-lines">${(v.lines || []).map((x) => `<li><span class="v-k">${esc(x.k)}</span><span>${esc(x.t)}</span></li>`).join("")}</ul>
    ${votesHTML(p)}
    ${watchHTML(p)}`;
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
  renderStateLog();
  // 第一屏的理由只列五个维度；出货在景气位置里作同步验证，依据层仍有一块
  renderStates(DATA.dimensions.filter((d) => d.key !== "shipments"));
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
