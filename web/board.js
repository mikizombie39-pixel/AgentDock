// Agent Dock: Adaptive Workspace (magnet board). Every amount comes from /api/board, computed by code from the business DBs.
(function () {
  const OWNER = "amiya";
  const $ = (id) => document.getElementById(id);
  const yen = (v) => (v === null || v === undefined) ? "—" : `${Number(v).toLocaleString("ja-JP")}円`;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const CERT = { order: "注文あり", conditional: "条件付きの合意", needs_staff: "相手の承認待ち", lead: "未打診", none: "売り先なし" };
  const TERM = { offered: "受付中", confirmed: "確定", released: "取り消し", expired: "期限切れ" };
  const CASE = { open: "調べ中", presented: "判断待ち", registered: "確定済み", incomplete: "確定の途中", rejected: "見送り", held: "保留", stale: "出し直し待ち", needs_president: "社長の承認待ち" };
  const KIND = { order: "注文", quote: "届いた条件", agreement: "条件付きの合意", vault: "社長の経験（Vault）", customer: "売り先", post: "仲買の掲示", market: "相場", risk: "注意", note: "メモ" };
  let shown = "", busy = false, current = null;

  function fit() {
    const s = Math.min(window.innerWidth / 1600, window.innerHeight / 900);
    $("stage").style.transform = `scale(${s})`;
  }
  window.addEventListener("resize", fit); fit();

  function cardBody(c) {
    const d = c.data || {};
    switch (c.card_type) {
      case "order": return `${esc(d.customer)}から ${esc(d.item)} ${d.quantity}${esc(d.unit)}<br><span class="k">売値</span> ${yen(d.sell_price_yen)}／${esc(d.unit)}`;
      case "quote": return `${esc(d.seller)}：${d.quantity}kg で ${yen(d.unit_price_yen)}／kg<span class="chip">条件 ${d.term_id}・${TERM[d.status] || esc(d.status)}</span><br><span class="k">期限 ${esc((d.valid_until || "").slice(11, 16))}</span>`;
      case "agreement": return `${esc(d.buyer)}が ${d.quantity}kg を ${yen(d.unit_price_yen)}／kg で<span class="chip">${esc(d.status_label)}</span><br><span class="k">${esc(d.condition)}</span>`;
      case "vault": return `<b>${esc(d.title)}</b><br>${esc(d.body)}<br><span class="k">${esc(d.author)}・${esc(d.source)}・${esc(d.written_on)}　適用の条件：${esc(d.applies_when)}</span>`;
      case "customer": return `${esc(d.name)}：これまで ${d.times}回・${d.min_sold}〜${d.max_sold}kg（最後 ${esc(d.last)}）${d.may_contact ? "" : "<span class=chip>案内は不可</span>"}`;
      case "post": return `${esc(d.owner)}（${d.side === "buy" ? "買いたい" : "譲りたい"}）：${d.quantity}kg・${yen(d.unit_price_yen)}／kg<br><span class="k">「${esc(d.message)}」</span>`;
      default: return "";
    }
  }

  function numbers(n) {
    const rows = [];
    if (n.purchase) rows.push(`<tr><td>仕入れ：${esc(n.purchase.seller_name)} ${n.purchase.quantity}kg × ${yen(n.purchase.unit_price_yen)}</td><td class="v">−${yen(n.purchase_yen)}</td></tr>`);
    for (const l of n.lines) {
      rows.push(`<tr><td>売り：${esc(l.to)} ${l.quantity}kg × ${yen(l.unit_price_yen)} <span class="cert ${l.certainty}">${CERT[l.certainty] || l.certainty}</span></td><td class="v">${yen(l.revenue_yen)}</td></tr>`);
    }
    rows.push(`<tr><td>加工（下処理と箱詰め）</td><td class="v">−${yen(n.process_cost_yen)}</td></tr>`);
    const gp = n.gross_profit_yen !== null && n.gross_profit_yen !== undefined
      ? `<tr class="total"><td>粗利（${esc(n.profit_kind || "見込み")}）</td><td class="v">${yen(n.gross_profit_yen)}</td></tr>`
      : `<tr class="total"><td>粗利（未確認の加工の費用を除く参考の値）</td><td class="v">${yen(n.gross_profit_without_unconfirmed_yen)}</td></tr>`;
    rows.push(gp);
    let extra = "";
    if (n.unsold_quantity) extra += `<div class="small">売り先のない ${n.unsold_quantity}kg・売れ残れば −${yen(n.unsold_loss_yen)}</div>`;
    if (n.uncounted_costs && n.uncounted_costs.length) extra += `<div class="small">未計上：${n.uncounted_costs.map(esc).join("・")}</div>`;
    return `<table class="num">${rows.join("")}</table>${extra}`;
  }

  function render(b) {
    current = b;
    if (!b.case) return;
    const key = `${b.case.id}-${b.case.version}-${b.case.status}-${(b.decisions || []).length}`;
    if (key === shown) return;
    shown = key;
    $("summary").textContent = b.summary || "";
    $("caseinfo").textContent = `案件 ${b.case.id}・版 ${b.case.version}・${CASE[b.case.status] || b.case.status}`;
    $("cards").innerHTML = b.cards.map((c, i) => `<div class="mag ${c.card_type}" style="animation-delay:${i * 90}ms">
      <div class="k">${KIND[c.card_type] || c.card_type}</div>${cardBody(c)}${c.note ? `<div class="n">${esc(c.note)}</div>` : ""}</div>`).join("");
    const open = b.case.status === "presented" || b.case.status === "held";
    $("options").innerHTML = b.options.map((o, i) => `<div class="opt ${o.recommended ? "rec" : ""} ${o.executable ? "" : "off"}" style="animation-delay:${200 + i * 110}ms">
      <div class="code">${esc(o.code)}${o.recommended ? '<span class="rec-tag">勧める</span>' : ""}</div>
      <div><div class="label">${esc(o.label)}</div><div class="why">${esc(o.reason)}</div><div class="risk">${esc(o.risk)}</div>
        ${o.executable ? "" : `<div class="err">実行できません：${o.numbers.errors.map(esc).join("・")}</div>`}</div>
      <div>${numbers(o.numbers)}<div class="act"><button data-opt="${o.id}" data-fp="${o.fingerprint}" ${open && o.executable ? "" : "disabled"}>この案を承認する</button></div></div>
    </div>`).join("");
    $("hold").disabled = !open; $("reject").disabled = !open;
    const last = (b.decisions || []).slice(-1)[0];
    if (last) showResult(JSON.parse(last.detail || "{}"));
  }

  function showResult(r) {
    const el = $("result");
    const M = {
      registered: () => `正式に確定しました。取引 ${(r.trades || []).map(esc).join("・")} を、各組織の帳簿に登録しました。粗利 ${yen(r.gross_profit_expected_yen)}（運賃は未計上）`,
      stale: () => `確定できませんでした。表示した時から条件が変わっています：${(r.reasons || []).map(esc).join("・")}。Claude AI パートナーがボードを出し直します`,
      rejected: () => `見送りました。仮押さえと条件付きの合意を取り消し、相手に知らせました`,
      held: () => `保留にしました。条件の期限まで仮押さえは残ります`,
      needs_president: () => `担当者の権限（1件 ${yen(r.authority && r.authority.limit_yen)}）を超えるので、社長の承認が要ります`,
      incomplete: () => `確定の途中で止まりました（済み：${(r.done || []).map(esc).join("・")}）。照合して残りを進めます`,
    };
    el.className = "result on" + (["stale", "incomplete", "needs_president"].includes(r.result) ? " bad" : "");
    el.textContent = (M[r.result] || (() => r.result))();
  }

  async function decide(action, optionId, fp, btn) {
    if (busy) return;
    busy = true;
    document.querySelectorAll("button").forEach((x) => (x.disabled = true));
    const key = (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
    try {
      const res = await fetch("/api/decide", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner: OWNER, case_id: current.case.id, option_id: optionId, action, fingerprint: fp, idempotency_key: key }) });
      showResult(await res.json());
    } finally { busy = false; shown = ""; poll(); }
  }

  $("options").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-opt]");
    if (b) decide("approve", Number(b.dataset.opt), b.dataset.fp, b);
  });
  $("hold").addEventListener("click", () => current && current.case && decide("hold", null, null));
  $("reject").addEventListener("click", () => current && current.case && decide("reject", null, null));

  async function poll() {
    try { const r = await fetch(`/api/board?owner=${OWNER}`); render(await r.json()); } catch (e) { /* keep last */ }
  }
  poll(); setInterval(poll, 2000);
})();
