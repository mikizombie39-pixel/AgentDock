// Agent Dock: the work screen. Everything shown comes from GET /api/work (facts derived by code from the business DBs).
// Two modes: live (follow the server) and replay (reveal the same items in time order, compressed).
// Effect: a school of small fish gathers into the outline of a card before its content appears; information from another
// organisation's AI swims in from that organisation's side; when the situation changes under the person, the school scatters
// with a ripple and the card re-forms; leftover work settles at the bottom. Motion happens only when something changes.
(function () {
  "use strict";
  const OWNER = "amiya";
  const Q = new URLSearchParams(location.search);
  const MODE = Q.get("mode") === "replay" ? "replay" : "live";
  const ITEM = Q.get("item") || "buri_xl";
  // both modes open on the worker's current state; the five sections are the 「仕組みと根拠」 side. Replay rebuilds the current state
  // at each recorded time from the records (GET /api/current_timeline)
  let VIEW = Q.get("view") !== "evidence" ? "now" : "evidence";
  let REPLAY_T = null;          // replay clock (seconds); null in live
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const NOREC = "記録なし";
  const yen = (v) => (v === null || v === undefined) ? NOREC : `${Number(v).toLocaleString("ja-JP")}円`;
  const hms = (s) => (s ? String(s).slice(11, 19) : "");
  const hm = (s) => (s ? String(s).slice(11, 16) : NOREC);
  const sec = (s) => { const t = Date.parse(s); return isNaN(t) ? 0 : Math.floor(t / 1000); };
  const icon = (n) => `<span class="ic">${(window.DOCK_ICONS || {})[n] || ""}</span>`;
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.querySelectorAll("[data-ic]").forEach((e) => { e.innerHTML = (window.DOCK_ICONS || {})[e.dataset.ic] || ""; });
  $(MODE === "replay" ? "mreplay" : "mlive").classList.add("on");
  $("mlive").href = `?mode=live&item=${encodeURIComponent(ITEM)}`;
  $("mreplay").href = `?mode=replay&item=${encodeURIComponent(ITEM)}`;

  let SCALE = 1;
  function fit() { SCALE = Math.min(window.innerWidth / 1600, window.innerHeight / 900); $("stage").style.transform = `scale(${SCALE})`; }
  window.addEventListener("resize", fit); fit();
  function rectOf(el) {
    const r = el.getBoundingClientRect(), s = $("stage").getBoundingClientRect();
    return { x: (r.left - s.left) / SCALE, y: (r.top - s.top) / SCALE, w: r.width / SCALE, h: r.height / SCALE };
  }

  // ── labels (fixed words for recorded states and kinds) ──
  const STATE = { held: "押さえ中", released: "下ろした", confirmed: "確定", expired: "期限切れ", open: "答え待ち", needs_president: "網谷さんを待つ",
    answered: "答えた", stale: "古い版", closed: "閉じた", registered: "登録済み", incomplete: "途中", partly_filled: "一部充足", filled: "充足",
    sold: "売れた", for_sale: "売りに出している", approved: "承認済み", allocated: "注文に充てた" };
  const KIND_MSG = { inquiry: "問い合わせ", offer: "条件の返事", decline: "断りの返事" };
  const RUN = { order_new: "注文を読み、仕入れ先に問い合わせています", quote_received: "相手の返事を読み、案を組んでいます",
    human_choice: "あなたの答えを受けて、交渉し直しています", staff_question: "あなたの質問に答えています", quote_request: "問い合わせに答えています",
    trade_registered: "登録を受けて、残った注文を確かめています",
    "info_received": "届いた情報を確かめています" };
  const RUN_SOURCE = { stock_question: "あなたの答えを受けて、引当のない在庫の続きを進めています" };
  const runText = (kind, source) => RUN_SOURCE[source] || RUN[kind];
  const WAIT = new Set(["open", "needs_president"]);
  const PAST = new Set(["released", "expired", "answered", "closed", "stale", "allocated"]);
  const ORG_ICON = { iwase: "ship", buyer_b: "package", buyer_c: "package" };

  // ── model ──
  let unit = "", owner = null;
  const shown = new Map();      // key -> {item, el}
  const orgsShown = new Map();  // code -> el
  let activity = [];
  let formOpen = null;          // consultation key whose change form is open (live)

  function badge(state) {
    const cls = { released: "off", expired: "off", answered: "off", closed: "off", stale: "bad", needs_president: "warn", open: "warn",
      confirmed: "ok", registered: "ok", filled: "ok" }[state] || "";
    return state && STATE[state] ? `<span class="badge ${cls}">${STATE[state]}</span>` : "";
  }
  function presidentWaited() { return [...shown.values()].some((s) => s.item.kind === "approval" && s.state === "needs_president"); }
  function srcBtn(it) { return `<button class="src" data-src="${esc(it.key)}">${icon("file-search")}記録</button>`; }
  function terms(d) { return d.terms && d.terms.length ? d.terms : [{ quantity: null, unit_price_yen: null, valid_until: null }]; }

  function cardHtml(it, state) {
    const d = it.data, live = MODE === "live";
    const hd = (ic, label) => `<div class="hd">${icon(ic)}<span>${label}</span><span class="at">${hms(it.at)}</span>${srcBtn(it)}</div>`;
    switch (it.kind) {
      case "order": return hd("shopping-cart", "注文") + `<div class="main">${esc(d.customer)}　${d.quantity}${unit}${badge(state === "open" ? null : state)}</div>
        <div class="kv"><div>売値（社内）</div><div class="v">${yen(d.sell_price_yen)}</div></div>`;
      case "material": return hd("ship", `${esc(d.sender)}の資料`) + `<div class="kv">
        <div>今朝の水揚げ</div><div class="v">${d.quantity ?? NOREC}${d.quantity != null ? unit : ""}</div>
        <div>明日の見込み</div><div class="v">${d.tomorrow_forecast ?? NOREC}${d.tomorrow_forecast != null ? unit : ""}</div>
        <div>直近30日の相場（中央値）</div><div class="v">${yen(d.recent_median_yen)}</div></div>${d.note ? `<div class="sub">${esc(d.note)}</div>` : ""}`;
      case "vault": return hd("notebook-text", "社長の経験（Vault）") + d.notes.map((n) => `<div class="main" style="font-size:13px">${esc(n.title)}</div>
        <div class="sub">${esc(n.author)}・${esc(n.written_on)}　適用：${esc(n.applies_when)}</div>`).join("") + ``;
      case "stock": return hd("package", "在庫") + `<div class="main">${d.quantity}${unit}${badge(state)}</div>`;
      case "inquiry": case "reply": {
        const t = terms(d)[0];
        return hd("send", it.kind === "inquiry" ? `${esc(d.to)}に問い合わせた` : `${esc(d.to)}に返事を送った`) +
          `<div class="kv"><div>${KIND_MSG[d.msg_kind] || ""}</div><div class="v">${t.quantity != null ? t.quantity + unit : NOREC} × ${yen(t.unit_price_yen)}</div></div>
          <div class="sub">理由：${esc(d.reason || NOREC)}</div>`;
      }
      case "hold": return hd("hourglass", d.side === "buy" ? `${esc(d.counterparty)}の仮押さえ` : `${esc(d.counterparty)}への仮押さえ`) +
        `<div class="main">${d.quantity}${unit} × ${yen(d.unit_price_yen)}${badge(state)}</div><div class="sub">期限 ${hm(d.valid_until)}</div>`;
      case "trade": return hd("receipt-japanese-yen", "登録した取引") + `<div class="main">${esc(d.counterparty)}${d.side === "buy" ? "から仕入れ" : "へ売り"}${badge(state)}</div>
        <div class="kv"><div>${d.quantity}${unit} × ${yen(d.unit_price_yen)}</div><div class="v">${yen(d.quantity * d.unit_price_yen)}</div>
        <div>承認</div><div class="v">${d.decided_by === "president" ? "網谷さん" : d.decided_by === "staff" ? "担当者" : esc(d.decided_by)}</div></div>`;
      case "consultation": return consultHtml(it, state, live);
      case "stock_question": {
        const ch = (d.choices || []).map((c) => esc(c.label) + (c.code === d.recommended ? "（AIの勧め）" : "")).join("・");
        return hd("package", "引当のない在庫の扱いを問われています") + `<div class="main">${esc(d.question)}</div>
          <div class="sub">引当のない在庫 ${d.quantity}${unit}　選択肢：${ch}</div>${d.reason ? `<div class="sub">理由：${esc(d.reason)}</div>` : ""}`;
      }
      case "stock_alloc": return hd("package", "引当のない在庫を注文に充てた") + `<div class="main">${esc(d.customer)}の注文に ${d.quantity}${unit}</div>`;
      case "approval": return hd("stamp", "網谷さんの承認を待っています") + `<div class="main">案 ${esc(d.option_code)}：${esc(d.option_label || NOREC)}</div>
        <div class="sub">${d.priority_change ? "売り先の注文を欠いてほかへ回す案です（顧客の間の優先）" : ""}${d.over_limit ? `担当者の上限 ${yen(d.limit_yen)} を超えます（${yen(d.amount_yen)}）` : ""}</div>
        <div class="foot">${state === "needs_president" ? `<button class="act main" data-approve="${esc(it.key)}" ${live ? "" : "disabled"}>網谷さん（社長）として承認する</button>` : badge(state)}
        <span class="res" data-res="${esc(it.key)}"></span></div>`;
      default: return "";
    }
  }

  function consultHtml(it, state, live) {
    const d = it.data;
    const opts = (d.options || []).map((o) => {
      const buy = o.purchase ? `${esc(o.purchase.seller)}から ${o.purchase.quantity}${unit} × ${yen(o.purchase.unit_price_yen)}` : "仕入れない";
      const to = (o.lines || []).map((l) => `${esc(l.to)} ${l.quantity}${unit}`).join("・");
      const can = live && state === "open" && o.executable && o.status === "offered" && !presidentWaited();
      return `<div class="opt"><div class="lb">案 ${esc(o.code)}${o.recommended ? '<span class="badge">AIの勧め</span>' : ""}　${esc(o.label)}</div>
        <div class="kv"><div>${buy}${to ? "　→ " + to : ""}</div><div class="v">${o.purchase ? yen(o.purchase.amount_yen) : ""}</div>
        <div>粗利（見込み・コードの計算）</div><div class="v">${yen(o.gross_profit_yen)}</div></div>
        ${o.executable ? "" : `<div class="err">実行できません：${(o.errors || []).map(esc).join("・")}</div>`}
        ${can ? `<button class="act" data-decide="${esc(it.key)}" data-opt="${o.option_id}">${o.purchase ? "この案で承認する" : "見送る"}</button>` : ""}</div>`;
    }).join("");
    const ch = d.change ? `<div class="sub">答え：仕入れ ${d.change.quantity}${unit}（${(d.change.allocations || []).map((a) => `${esc(a.customer)} ${a.quantity}${unit}`).join("・")}）・${d.answered_by === "president" ? "網谷さん" : "担当者"}</div>` : "";
    let ctl = "";
    if (state === "open") ctl = `<button class="act" data-change="${esc(it.key)}" ${live ? "" : "disabled"}>数量・配分を変える</button>`;
    if (state === "needs_president") ctl = `<button class="act main" data-pres="${esc(it.key)}" ${live ? "" : "disabled"}>網谷さん（社長）として、この答えで確定する</button>`;
    return `<div class="hd">${icon("user-round")}<span>相談（版 ${d.version}）</span>${badge(state)}<span class="at">${hms(it.at)}</span>${srcBtn(it)}</div>
      <div class="main">${esc(d.question)}</div>${ch}${opts}<div class="foot">${ctl}<span class="res" data-res="${esc(it.key)}"></span></div>
      <div class="formslot"></div>`;
  }

  function leftHtml(it) {
    if (it.kind === "stock_left") {
      const d = it.data;
      return `<span class="shoal">${icon("package")}</span><span>引当のない在庫 ${d.quantity}${unit}（${d.intended_customer ? `${esc(d.intended_customer)}に回すつもり・注文なし` : "売り先なし"}）</span>${srcBtn(it)}`;
    }
    const n = Math.max(1, Math.min(6, it.data.remaining));
    return `<span class="shoal">${icon("fish").repeat(n)}</span><span>${esc(it.data.customer)}　残り ${it.data.remaining}${unit}（注文 ${it.data.quantity}${unit}）</span>${srcBtn(it)}`;
  }

  function msgHtml(it) {
    const d = it.data, ts = terms(d);
    return `<div class="t">${hms(it.at)}</div><div class="w">${esc(d.frm)} → ${esc(d.to)}</div>
      <div class="q">${ts.map((t) => (t.quantity != null ? t.quantity + unit : NOREC)).join("<br>")}</div>
      <div class="p">${ts.map((t) => yen(t.unit_price_yen)).join("<br>")}</div><div class="d">${ts.map((t) => (t.valid_until ? "期限 " + hm(t.valid_until) : "")).join("<br>")}</div>
      <div class="r"><b>${KIND_MSG[d.msg_kind] || esc(d.msg_kind)}</b>　理由：${esc(d.reason || NOREC)}${srcBtn(it)}</div>`;
  }

  // ── the sea: a school of small fish on a canvas ──
  const Sea = (() => {
    const cv = $("sea"), g = cv.getContext("2d");
    let fish = [], rings = [], raf = 0;
    const COLORS = { ai: "29,102,112", org: "29,102,112", human: "138,90,18", left: "29,102,112" };
    function perimeter(r, n) {
      const pts = [], P = 2 * (r.w + r.h);
      for (let i = 0; i < n; i++) {
        let s = (i / n) * P;
        if (s < r.w) pts.push([r.x + s, r.y]); else if ((s -= r.w) < r.h) pts.push([r.x + r.w, r.y + s]);
        else if ((s -= r.h) < r.w) pts.push([r.x + r.w - s, r.y + r.h]); else pts.push([r.x, r.y + r.h - (s - r.w)]);
      }
      return pts;
    }
    function school({ from, to, n = 26, tone = "ai", stagger = 280, hold = 260, onFormed, inward = false }) {
      if (REDUCED) { onFormed && onFormed(); return; }
      const pts = to.points || perimeter(to, n), now = performance.now(), group = { n: pts.length, left: pts.length, done: false, onFormed, t0: now };
      pts.forEach(([tx, ty], i) => {
        const a = Math.random() * Math.PI * 2, r = 6 + Math.random() * 18;
        fish.push({ x: from.x + Math.cos(a) * r, y: from.y + Math.sin(a) * r, vx: 0, vy: 0, tx, ty, ph: Math.random() * 6.28, born: now + Math.random() * stagger,
          arrived: 0, tone, group, hold, alpha: 1, inward, size: 0.85 + Math.random() * 0.4 });
      });
      kick();
    }
    function ripple(r) {
      if (REDUCED) return;
      const t0 = performance.now();
      rings.push({ x: r.x + r.w / 2, y: r.y + r.h / 2, t0, max: Math.max(r.w, r.h) * 0.8 }, { x: r.x + r.w / 2, y: r.y + r.h / 2, t0: t0 + 160, max: Math.max(r.w, r.h) * 0.6 });
      // the school scatters away from the card, then comes back to re-form it
      const pts = perimeter(r, 30), now = performance.now(), group = { n: pts.length, left: pts.length, done: false, onFormed: null, t0: now };
      pts.forEach(([x, y]) => {
        const dx = x - (r.x + r.w / 2), dy = y - (r.y + r.h / 2), L = Math.hypot(dx, dy) || 1;
        fish.push({ x, y, vx: dx / L * 6, vy: dy / L * 6, tx: x, ty: y, ph: Math.random() * 6.28, born: now, arrived: 0, tone: "human", group, hold: 200,
          alpha: 1, flee: now + 320, size: 0.9 + Math.random() * 0.3 });
      });
      kick();
    }
    function kick() { if (!raf) raf = requestAnimationFrame(step); }
    function draw(f) {
      const h = Math.atan2(f.vy, f.vx || 0.0001), s = f.size;
      g.save(); g.translate(f.x, f.y); g.rotate(f.arrived ? (f.heading ?? h) : h);
      g.fillStyle = `rgba(${COLORS[f.tone]},${f.alpha})`;
      g.beginPath(); g.ellipse(0, 0, 4.4 * s, 1.8 * s, 0, 0, Math.PI * 2); g.fill();
      g.beginPath(); g.moveTo(-3.6 * s, 0); g.lineTo(-7.2 * s, -2.2 * s); g.lineTo(-7.2 * s, 2.2 * s); g.closePath(); g.fill();
      g.restore();
    }
    function step(t) {
      raf = 0;
      g.clearRect(0, 0, 1600, 900);
      for (const f of fish) {
        if (t < f.born) continue;
        if (f.flee && t < f.flee) { f.x += f.vx; f.y += f.vy; f.vx *= 0.93; f.vy *= 0.93; draw(f); continue; }
        if (!f.arrived) {
          const dx = f.tx - f.x, dy = f.ty - f.y, d = Math.hypot(dx, dy);
          if (d < 2.5) { f.arrived = t; f.x = f.tx; f.y = f.ty; f.heading = Math.atan2(f.vy, f.vx); f.group.left--; }
          else {
            const sp = Math.min(13, 0.8 + d * 0.11), wig = d > 30 ? Math.sin(f.ph + t * 0.018) * 0.55 : 0;
            const ax = (dx / d * sp - f.vx) * 0.14 - dy / d * wig, ay = (dy / d * sp - f.vy) * 0.14 + dx / d * wig;
            f.vx += ax; f.vy += ay; f.x += f.vx; f.y += f.vy;
          }
        } else if (t - f.arrived > f.hold) f.alpha = Math.max(0, f.alpha - 0.06);
        if (!f.arrived && f.group.done && t - f.group.t0 > 1700) f.alpha = Math.max(0, f.alpha - 0.08);
        const gr = f.group;
        if (!gr.done && (gr.left <= Math.ceil(0.1 * gr.n) || t - gr.t0 > 1500)) {
          gr.done = true; gr.onFormed && gr.onFormed();
        }
        draw(f);
      }
      for (const r of rings) {
        const k = (t - r.t0) / 900;
        if (k > 0 && k < 1) { g.strokeStyle = `rgba(138,90,18,${0.7 * (1 - k)})`; g.lineWidth = 3; g.beginPath(); g.arc(r.x, r.y, r.max * k, 0, Math.PI * 2); g.stroke(); }
      }
      rings = rings.filter((r) => t - r.t0 < 900);
      fish = fish.filter((f) => f.alpha > 0.02);
      for (const gr of new Set(fish.map((f) => f.group))) if (!gr.done && t - gr.t0 > 1500) { gr.done = true; gr.onFormed && gr.onFormed(); }
      if (fish.length || rings.length) kick();
    }
    return { school, ripple };
  })();

  // where a school starts: the AI in the header, the counterpart's node, or the depth below the card
  function originFor(it, r) {
    const d = it.data || {};
    const org = it.kind === "message" ? (d.outgoing ? null : d.from_owner) : it.kind === "hold" ? d.counterparty_code : null;
    if (org && orgsShown.get(org)) { const o = rectOf(orgsShown.get(org)); return { x: o.x + o.w - 10, y: o.y + o.h / 2 }; }
    if (["order", "material", "vault", "stock"].includes(it.kind)) return { x: r.x + r.w / 2, y: 900 };
    const from = it.kind === "order_left" && shown.get(`order:${d.order_id}`);
    if (from) { const o = rectOf(from.el); return { x: o.x + o.w / 2, y: o.y + o.h / 2 }; }
    const a = rectOf($("ai")); return { x: a.x + 12, y: a.y + a.h / 2 };
  }
  function tone(it) { return it.section === "waiting" ? "human" : it.section === "left" ? "left" : "ai"; }

  // ── rendering a set of items (diff by key and state) ──
  function container(it) {
    return { waiting: "sec-waiting", read: "sec-read", done: "sec-done", network: "msgs", left: "leftlist" }[it.section];
  }
  function visible(it, state) { return it.section !== "waiting" || WAIT.has(state); }

  function ensureOrg(code, name) {
    if (!code || code === OWNER || orgsShown.has(code)) return;
    const el = document.createElement("div");
    el.className = "org";
    el.innerHTML = `${icon(ORG_ICON[code] || "package")}<div>${esc(name)}<small>AI</small></div>`;
    $("orglist").appendChild(el);
    orgsShown.set(code, el);
    $("orgs").hidden = false;
  }

  function place(it, state, animate) {
    const parent = $(container(it));
    const el = document.createElement("div");
    el.dataset.key = it.key;
    el.className = it.section === "network" ? `msg ${it.data.outgoing ? "out" : "in"}` : it.section === "left" ? "leftover" : `card ${it.section === "waiting" ? "human" : "ai"}`;
    el.innerHTML = it.section === "network" ? msgHtml(it) : it.section === "left" ? leftHtml(it) : cardHtml(it, state);
    if (PAST.has(state) && it.section !== "waiting") el.classList.add("past");
    // newest first in the card columns; the conversation reads top to bottom
    if (it.section === "network" || it.section === "left") parent.appendChild(el); else parent.insertBefore(el, parent.firstChild);
    $(it.section === "network" ? "network" : it.section).hidden = false;
    shown.set(it.key, { item: it, state, el });
    // cards and rows appear at once; the school swims only when information arrives from another organisation
    if (!animate || REDUCED || VIEW !== "evidence") return;
    if (it.section === "network" && !it.data.outgoing) {
      const r = rectOf(el);
      Sea.school({ from: originFor(it, r), to: r, n: 14, stagger: 360, hold: 120 });
    }
  }

  function update(it, state, animate) {
    const s = shown.get(it.key);
    const was = s.state;
    s.item = it; s.state = state;
    if (it.section === "waiting" && !WAIT.has(state)) {   // the person's work is done: the school leaves, the card goes
      if (formOpen === it.key) closeForm(it.key, true);
      const gone = () => { s.el.remove(); if (!$("sec-waiting").children.length) $("waiting").hidden = true; };
      if (animate && !REDUCED) { s.el.classList.add("forming"); setTimeout(gone, 200); } else gone();
      shown.delete(it.key);
      return;
    }
    if (it.section !== "network" && it.section !== "left") {
      const keepForm = formOpen === it.key && state === was;
      const slot = keepForm ? s.el.querySelector(".formslot").innerHTML : null;
      s.el.innerHTML = cardHtml(it, state);
      if (keepForm) s.el.querySelector(".formslot").innerHTML = slot;
    }
    s.el.classList.toggle("past", PAST.has(state) && it.section !== "waiting");
    // the situation changed under the person: stop them (ripple, scatter, re-form)
    if (it.section === "waiting" && was !== state && (state === "needs_president" || state === "stale")) {
      if (formOpen === it.key) closeForm(it.key, true);
      if (animate) Sea.ripple(rectOf(s.el));
    }
    if (it.kind === "hold" && state === "confirmed" && was !== state && animate) {
      const r = rectOf(s.el); Sea.school({ from: originFor(it, r), to: r, n: 18 });
    }
  }

  function apply(items, animate) {
    for (const it of items) {
      if (it.kind === "message") { ensureOrg(it.data.from_owner, it.data.frm); ensureOrg(it.data.to_owner, it.data.to); }
    }
    for (const it of items) {
      const state = it._state ?? it.state;
      if (!shown.has(it.key)) { if (visible(it, state)) place(it, state, animate); }
      else if (shown.get(it.key).state !== state || JSON.stringify(shown.get(it.key).item.data) !== JSON.stringify(it.data)) update(it, state, animate);
    }
  }

  function aiText(runs, now) {
    const on = runs.filter((r) => (now == null ? !r.ended_at : sec(r.started_at) <= now && (!r.ended_at || sec(r.ended_at) > now)));
    const r = on[on.length - 1];
    $("ai").classList.toggle("busy", !!r);
    $("aitext").textContent = r ? `AI：${runText(r.event_kind, r.event_source) || "出来事を確かめています"}` : (runs.length || shown.size ? "AI：次の出来事を見張っています" : "");
  }

  function header(v) {
    unit = v.item.unit; owner = v.owner;
    $("itemchip").innerHTML = `${icon("fish")}${esc(v.item.name)}`;
  }

  // ── replay: reveal the derived items in time order, compressed ──
  function replay(v, timeline) {
    $("replayctl").hidden = false;
    activity = v.activity;
    const evs = [];
    for (const it of v.items) it.history.forEach((h, i) => evs.push({ t: sec(h.at), it, state: h.state, first: i === 0 }));
    for (const r of v.activity) { evs.push({ t: sec(r.started_at), run: true }); if (r.ended_at) evs.push({ t: sec(r.ended_at), run: true }); }
    // the current state rebuilt by the code at each recorded time (applied after the items of the same second)
    for (const s of (timeline && timeline.snapshots) || []) evs.push({ t: sec(s.at), snap: s.current });
    evs.sort((a, b) => a.t - b.t || (a.snap ? 1 : 0) - (b.snap ? 1 : 0));
    let i = 0, timer = 0, playing = false;
    const states = new Map();
    function reset() {
      clearTimeout(timer); i = 0; states.clear(); shown.clear(); orgsShown.clear(); formOpen = null;
      for (const id of ["sec-waiting", "sec-read", "sec-done", "msgs", "leftlist", "orglist"]) $(id).innerHTML = "";
      for (const id of ["orgs", "waiting", "read", "done", "network", "left"]) $(id).hidden = true;
      for (const el of nowState.lines.values()) el.remove();
      nowState.lines.clear();
      for (const id of ["nowholds", "nowdecision", "nowask", "nowdrafts"]) { $(id).innerHTML = ""; $(id)._html = null; }
      Object.assign(nowState, { key: "", open: null, cur: null, stop: "", ack: null });
      REPLAY_T = evs.length ? evs[0].t : null;
      if (evs.length) clock(evs[0].t); else $("rpclock").textContent = NOREC;
      aiText([], 0);
    }
    function clock(t) {
      const d = new Date(t * 1000), p = (n) => String(n).padStart(2, "0");
      $("rpclock").textContent = `記録の時刻 ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    }
    function tick() {
      if (i >= evs.length) { playing = false; $("rpplay").querySelector(".ic").innerHTML = window.DOCK_ICONS.play; $("rpplay").lastChild.textContent = "再生"; return; }
      const e = evs[i++];
      clock(e.t);
      REPLAY_T = e.t;
      if (e.snap) {
        const changed = renderNow(e.snap, true);
        if (VIEW === "now" && !REDUCED) for (const el of changed) { const r = rectOf(el); Sea.school({ from: { x: r.x - 30, y: r.y + r.h / 2 }, to: r, n: 18, hold: 160 }); }
      } else if (!e.run) {
        states.set(e.it.key, e.state);
        apply([Object.assign({}, e.it, { _state: e.state })], true);
      }
      aiText(v.activity, e.t);
      if (nowState.cur && !nowState.cur.waiting_for) drawAsk();   // the AI's turn line follows the replay clock
      const nx = evs[i];
      const gap = nx ? nx.t - e.t : 0;
      const wait = nx ? Math.min(2400, Math.max(gap === 0 ? 260 : 650, gap * 110)) : 0;   // compressed time
      if (playing) timer = setTimeout(tick, wait);
    }
    $("rpplay").addEventListener("click", () => {
      playing = !playing;
      $("rpplay").querySelector(".ic").innerHTML = window.DOCK_ICONS[playing ? "pause" : "play"];
      $("rpplay").lastChild.textContent = playing ? "止める" : "再生";
      if (playing) { if (i >= evs.length) reset(); tick(); } else clearTimeout(timer);
    });
    $("rprestart").addEventListener("click", () => { const p = playing; reset(); if (p) tick(); });
    reset();
    window.__replay = { events: evs.length, at: () => i, play: () => $("rpplay").click() };
    if (Q.get("autoplay") === "1") $("rpplay").click();
  }

  // ── live: follow the server (one request at a time: the audit stream, then the current state and the work view) ──
  async function live() {
    let cursor = 0, n = 0, first = true;
    async function loop() {
      try {
        const e = await (await fetch(`/api/events?owner=${OWNER}&since=${cursor}`)).json();
        const moved = e.cursor !== cursor; cursor = e.cursor;
        if (first || moved || n++ % 3 === 0 || nowState.refresh) {
          nowState.refresh = false;
          const cur = await (await fetch(`/api/current?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`)).json();
          const v = await (await fetch(`/api/work?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`)).json();
          if (first) header(v);
          // information that arrived from another organisation since the last look
          const fresh = first ? [] : v.items.filter((it) => it.kind === "message" && !it.data.outgoing && !shown.has(it.key));
          apply(v.items, !first);
          activity = v.activity; aiText(activity, null);
          const changed = renderNow(cur, !first);
          if (!first && VIEW === "now" && !REDUCED) {
            if (fresh.length) for (const m of fresh) {   // (a) from that organisation's badge to the lines its answer changed (and its hold line)
              const holds = [...document.querySelectorAll("#nowholds .holdline")].filter((e) => e.innerText.includes(m.data.frm));
              const to = [...changed, ...holds];
              streamFrom(m.data.from_owner, to.length ? to : [$("nowask")]);
            }
            else for (const el of changed) { const r = rectOf(el); Sea.school({ from: { x: r.x - 30, y: r.y + r.h / 2 }, to: r, n: 18, hold: 160 }); }   // (b)
          }
          first = false;
        }
      } catch (err) { /* keep the last view; try again */ }
      setTimeout(loop, nowState.refresh ? 50 : 1000);
    }
    loop();
  }

  // ── the worker's current state (code-made status lines; the AI's short question and its options) ──
  const nowState = { lines: new Map(), key: "", open: null, cur: null, stop: "", ack: null, refresh: false };
  function streamFrom(org, targets) {
    const o = orgsShown.get(org);
    if (!o) return;
    const f = rectOf(o);
    for (const el of targets) Sea.school({ from: { x: f.x + f.w - 8, y: f.y + f.h / 2 }, to: rectOf(el), n: 16, stagger: 420, hold: 180 });
  }
  function renderNow(cur, animate) {
    if (!cur || !cur.lines) return [];
    unit = cur.item.unit;
    const changed = [], seen = new Set(), box = $("nowlines");
    // order lines, then the stock with no order behind it (売り先の当てのない数量) — both written by the code
    const rows = cur.lines.map((l) => ({ k: "o" + l.order_id, cls: l.state, ic: "fish", text: l.text }))
      .concat((cur.stock || []).map((s) => ({ k: "s" + (s.intended_customer || ""), cls: "spare", ic: "package", text: s.text })));
    for (const l of rows) {
      seen.add(l.k);
      let el = nowState.lines.get(l.k);
      if (!el) { el = document.createElement("div"); el._text = null; box.appendChild(el); nowState.lines.set(l.k, el); }
      if (el._text !== l.text) {
        el.className = `line ${l.cls}`;
        el.innerHTML = `${icon(l.ic)}<span>${esc(l.text)}</span>`;
        if (el._text !== null) changed.push(el);
        el._text = l.text;
      }
    }
    for (const [k, el] of nowState.lines) if (!seen.has(k)) { el.remove(); nowState.lines.delete(k); }
    $("nowholds").innerHTML = (cur.holds || []).map((x) => `<div class="holdline">${icon("hourglass")}<span>${esc(x.text)}</span></div>`).join("");
    $("nowdecision").textContent = [cur.decision ? `人の決定：${cur.decision.text}` : "", cur.stock_decision ? cur.stock_decision.text : ""].filter(Boolean).join("　／　");
    drawDrafts(cur.drafts || []);
    const k = JSON.stringify([cur.waiting_for, cur.case && cur.case.version, cur.case && cur.case.status, cur.consultation && cur.consultation.status,
      (cur.options || []).map((o) => o.option_id + ":" + o.fingerprint + ":" + o.executable), cur.stock_question && cur.stock_question.question_id]);
    if (k !== nowState.key) {
      // (c) the situation changed while the person was choosing: stop them, show what changed, re-form
      if (nowState.open && nowState.key && animate) { Sea.ripple(rectOf($("nowask"))); nowState.stop = "状況が変わったので、選んでいた内容を止めました。新しい内容を見てください"; nowState.stopT = Date.now(); }
      else if (nowState.stop && Date.now() - (nowState.stopT || 0) > ACK_GRACE) nowState.stop = "";
      // the result of the person's last action belongs to the situation it answered: once the situation moves on, it goes
      if (nowState.ack && Date.now() - nowState.ack.t > ACK_GRACE) nowState.ack = null;
      nowState.key = k; nowState.open = null;
    }
    nowState.cur = cur;
    drawAsk();
    return changed;
  }
  function aiNow() {
    const on = (x) => (REPLAY_T == null ? !x.ended_at : sec(x.started_at) <= REPLAY_T && (!x.ended_at || sec(x.ended_at) > REPLAY_T));
    const r = [...activity].reverse().find(on);
    if (r) return `AI：${runText(r.event_kind, r.event_source) || "出来事を確かめています"}`;
    const qd = (nowState.cur && nowState.cur.ai_queue) || [];
    if (qd.length) return `AIの番です：${runText(qd[0].kind, qd[0].source) || "出来事を確かめます"}（起きるのを待っています）`;
    return "あなたを待つことはありません。AIが次の出来事を見張っています";
  }
  const ACK_GRACE = 1500, ACK_KEEP = 8000;
  function ackLine() {
    // a ✓ line is the answer to the person's own action: it stays a few seconds, not after the work has moved on
    if (nowState.ack && !nowState.ack.bad && Date.now() - nowState.ack.t > ACK_KEEP) nowState.ack = null;
    return (nowState.stop ? `<div class="ack bad">${esc(nowState.stop)}</div>` : "") + (nowState.ack ? `<div class="ack ${nowState.ack.bad ? "bad" : ""}">${esc(nowState.ack.text)}</div>` : "");
  }
  function drawStockQuestion(q) {
    return `<div class="ask-q">${esc(q.text)}</div><div class="ask-sub">AIの問い（引当のない在庫 ${q.quantity}${unit}）${q.reason ? "　理由：" + esc(q.reason) : ""}</div>
      <div class="choices">${(q.choices || []).map((c) => `<button class="choice" data-sq="${esc(c.code)}">${esc(c.label)}<small>${c.code === q.recommended ? "AIの勧め" : ""}</small></button>`).join("")}</div>`;
  }
  function drawAsk() {
    const cur = nowState.cur, box = $("nowask");
    if (!cur) return;
    if (!cur.waiting_for && cur.stock_question) { box.innerHTML = drawStockQuestion(cur.stock_question) + ackLine(); return; }
    if (!cur.waiting_for) { box.innerHTML = `<div class="aiturn">${icon("bot")}<span>${esc(aiNow())}</span></div>${ackLine()}`; return; }
    if (cur.waiting_for === "president") {
      const p = cur.pending || {};
      const why = [p.priority_change ? "売り先の注文を欠いてほかへ回すため（顧客の間の優先）" : "", p.over_limit ? `担当者の上限 ${yen(p.limit_yen)} を超えるため` : "",
        p.differs_from_decision ? "人の決定と違う案のため" : ""].filter(Boolean).join("・");
      const o = p.kind === "approval" ? (cur.options || []).find((x) => x.option_id === p.option_id) : null;
      const ch = p.kind === "consultation" && p.change ? `仕入れ ${p.change.quantity}${unit}（${(p.change.allocations || []).map((a) => `${esc(a.customer)} ${a.quantity}${unit}`).join("・")}）` : "";
      box.innerHTML = `<div class="ask-q">網谷さん（社長）の判断を待っています</div><div class="ask-sub">${esc(why || NOREC)}</div>
        ${o ? `<div class="choices"><button class="choice on" disabled>${esc(o.label)}<small>担当者が選んだ案</small></button></div>${panelHtml(o, "president")}` : ""}
        ${p.kind === "consultation" ? `<div class="waitpres"><span>担当者の答え：${ch}</span><button class="act main" data-pgo="consult">網谷さん（社長）として、この答えで確定する</button></div>` : ""}${ackLine()}`;
      return;
    }
    const q = cur.question;
    box.innerHTML = `<div class="ask-q">${esc(q ? q.text : NOREC)}</div><div class="ask-sub">AIの問い（版 ${cur.case.version}）</div>
      <div class="choices">${(cur.options || []).map((o) => `<button class="choice ${nowState.open === o.option_id ? "on" : ""} ${o.executable || o.action === "reject" ? "" : "off"}" data-choice="${o.option_id}">
        ${esc(o.label)}<small>${o.recommended ? "AIの勧め" : ""}${o.differs_from_decision ? "　人の決定と違う案" : ""}</small></button>`).join("")}</div>
      ${nowState.open ? panelHtml((cur.options || []).find((o) => o.option_id === nowState.open), "staff") : ""}${ackLine()}`;
  }
  function panelHtml(o, actor) {
    if (!o) return "";
    const n = o.numbers;
    const after = (o.after || []).map((a) => `<div>${esc(a.customer)}の注文 ${a.quantity}${unit}</div><div class="v">${a.short_after ? `確保 ${a.secured_after}${unit}・${a.short_after}${unit} 足りない` : `確保 ${a.secured_after}${unit}`}</div>`).join("");
    const buy = n.purchase ? `<div>仕入れ ${esc(n.purchase.seller)} ${n.purchase.quantity}${unit} × ${yen(n.purchase.unit_price_yen)}</div><div class="v">${yen(n.purchase_yen)}</div>` : `<div>仕入れない</div><div class="v"></div>`;
    const d = o.gross_profit_diff_yen;
    const gp = `<div>粗利（見込み）</div><div class="v">${yen(n.gross_profit_yen)}</div><div>勧める案との差</div><div class="v">${d === 0 ? "0円" : (d > 0 ? "+" : "−") + yen(Math.abs(d))}</div>`;
    const errs = n.errors && n.errors.length ? `<div class="err">実行できません：${n.errors.map(esc).join("・")}</div>` : "";
    const vault = o.grounds.vault.length ? o.grounds.vault.map((v) => `<div>${esc(v.title)}</div><div class="sub">${esc(v.author)}・${esc(v.written_on)}</div>`).join("") : NOREC;
    const label = actor === "president" ? "網谷さん（社長）として承認する" : o.action === "approve" ? "これで進める" : "見送る";
    const live = MODE === "live" && (o.action === "reject" || o.executable);
    return `<div class="panel"><div><h4>変わること（コードの計算）</h4><div class="kv">${after}${buy}${gp}</div>${errs}</div>
      <div><h4>参照した経験（Vault）</h4>${vault}</div><div><h4>AIの理由</h4><div>${esc(o.grounds.reason || NOREC)}</div><div class="sub">${esc(o.grounds.risk || "")}</div></div></div>
      <div class="go"><button class="act main" data-go="${o.option_id}" data-actor="${actor}" ${live ? "" : "disabled"}>${label}</button></div>`;
  }
  async function confirm(o, actor) {
    const cur = nowState.cur;
    const body = o.action === "approve" ? { owner: OWNER, case_id: cur.case.id, option_id: o.option_id, action: "approve", fingerprint: o.fingerprint, idempotency_key: key(), actor }
      : { owner: OWNER, case_id: cur.case.id, option_id: null, action: "reject", fingerprint: null, idempotency_key: key(), actor };
    nowState.ack = { text: "送っています…", bad: false, t: Date.now() }; drawAsk();
    let r, j;
    try { r = await fetch("/api/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); j = await r.json(); }
    catch (e) { nowState.ack = { text: "送れませんでした（サーバーに届いていません）", bad: true, t: Date.now() }; drawAsk(); return; }
    settle(r.status, j);
  }
  function settle(status, j) {
    if (status === 409 || j.result === "stale" || j.result === "not_executable") {   // what changed, from the code's re-check
      nowState.ack = { text: `確定しませんでした。変わったこと：${(j.reasons || [j.detail || j.error || ""]).join("・")}`, bad: true };
      if (!REDUCED) Sea.ripple(rectOf($("nowask")));
    } else if (j.result === "needs_president") nowState.ack = { text: "網谷さん（社長）の承認を待ちます", bad: false };
    else if (j.result === "registered") nowState.ack = { text: "✓ 登録しました（サーバーが受け付けました）", bad: false };
    else if (j.result === "rejected") nowState.ack = { text: "✓ 見送りました（サーバーが受け付けました）", bad: false };
    else if (j.result === "answered" && j.question_id) nowState.ack = { text: "✓ 答えを記録しました。AIが続けます", bad: false };
    else if (j.result === "answered") nowState.ack = { text: "✓ 答えを記録しました。AIが交渉し直します", bad: false };
    else nowState.ack = { text: j.detail || j.error || j.result || NOREC, bad: true };
    nowState.ack.t = Date.now();
    nowState.open = null; nowState.stop = ""; nowState.refresh = true; drawAsk();
  }
  $("nowask").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled || MODE !== "live") return;
    const cur = nowState.cur;
    if (b.dataset.choice) { nowState.open = Number(b.dataset.choice) === nowState.open ? null : Number(b.dataset.choice); nowState.stop = ""; nowState.ack = null; return drawAsk(); }
    if (b.dataset.sq && cur.stock_question) {   // the person answers the AI's question about the stock with no order behind it
      b.disabled = true;
      let r, j;
      try {
        r = await fetch("/api/stock_answer", { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ owner: OWNER, question_id: cur.stock_question.question_id, choice: b.dataset.sq, idempotency_key: key(), role: "staff" }) });
        j = await r.json().catch(() => ({}));
      } catch (err) { nowState.ack = { text: "送れませんでした（サーバーに届いていません）", bad: true, t: Date.now() }; return drawAsk(); }
      return settle(r.status, j);
    }
    if (b.dataset.go) {
      const o = cur.options.find((x) => x.option_id === Number(b.dataset.go));
      b.disabled = true;
      return confirm(o, b.dataset.actor);
    }
    if (b.dataset.pgo === "consult") {
      b.disabled = true;
      const p = cur.pending;
      const r = await fetch("/api/answer", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner: OWNER, consultation_id: p.consultation_id, option: "change", idempotency_key: key(), change: p.change, role: "president" }) });
      settle(r.status, await r.json().catch(() => ({})));
    }
  });
  // the AI's drafts to a customer: nothing is sent by Agent Dock; the person decides and tells the customer
  function drawDrafts(list) {
    const html = list.map((d) => d.status === "draft"
      ? `<div class="draft"><div class="dk">${esc(d.customer)}への連絡の下書き（AIが作成・まだ送っていません）</div><div class="db">${esc(d.body)}</div>
         <div class="ds">代わりの品：${esc(d.substitute || "なし（AIが読める品目に無い）")}${d.reason ? "　理由：" + esc(d.reason) : ""}</div>
         <div class="go"><button class="act main" data-draft="${d.draft_id}" data-act="approve" ${MODE === "live" ? "" : "disabled"}>この文で伝える（あなたが伝えます）</button>
         <button class="act" data-draft="${d.draft_id}" data-act="discard" ${MODE === "live" ? "" : "disabled"}>使わない</button></div></div>`
      : `<div class="draft done"><div class="dk">${esc(d.customer)}への連絡：${d.status === "stale" ? "古くなったため使えません（" + esc(d.stale_reason || "") : (d.status === "approved" ? "伝えることにしました" : "使わないことにしました") + "（" + (d.decided_by === "president" ? "網谷さん" : "担当者")}）</div><div class="db">${esc(d.body)}</div></div>`).join("");
    if ($("nowdrafts")._html !== html) { $("nowdrafts").innerHTML = html; $("nowdrafts")._html = html; }
  }
  $("nowdrafts").addEventListener("click", async (e) => {
    const b = e.target.closest("button[data-draft]");
    if (!b || b.disabled || MODE !== "live") return;
    b.disabled = true;
    await fetch("/api/draft", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: OWNER, draft_id: Number(b.dataset.draft), action: b.dataset.act, role: "staff" }) });
    nowState.refresh = true;
  });
  // the demonstration operator: a customer's additional order arrives mid-flow (a business event, like any order)
  $("opbtn").addEventListener("click", async () => {
    $("opbtn").disabled = true;
    const r = await fetch("/api/order", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: OWNER, customer: "料亭", item: "buri_xl", quantity: 6, idempotency_key: "demo-ryotei-buri-6" }) });
    const j = await r.json().catch(() => ({}));
    $("opbtn").textContent = r.ok ? `（実演の操作）料亭の追加注文を入れました（注文 ${j.order_id}）` : `（実演の操作）入れられませんでした：${j.error || r.status}`;
    nowState.refresh = true;
  });
  function setView(v) {
    VIEW = v;
    document.body.dataset.view = v;
    $("nowview").hidden = v !== "now";
    $("viewbtn").textContent = v === "now" ? "仕組みと根拠を見る" : "今の状態に戻る";
  }
  $("viewbtn").addEventListener("click", () => setView(VIEW === "now" ? "evidence" : "now"));

  // ── the person's operations (live only; the existing APIs) ──
  const key = () => (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
  const RESULT = { registered: "登録しました", needs_president: "網谷さんの承認を待ちます", stale: "条件が変わりました。AIが調べ直します",
    not_executable: "この案は今の条件では実行できません", answered: "答えを記録しました。AIが交渉し直します", held: "保留にしました", rejected: "見送りました" };
  async function post(path, body, k) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    const res = document.querySelector(`[data-res="${CSS.escape(k)}"]`);
    if (res) res.textContent = RESULT[j.result] || j.detail || j.error || "";
  }
  function openForm(k) {
    const s = shown.get(k), orders = [...shown.values()].filter((x) => x.item.kind === "order").map((x) => x.item.data);
    const opt = (s.item.data.options || []).find((o) => o.recommended) || {};
    const now = {}; for (const a of opt.allocations || []) if (a.kind === "order") now[a.ref_id] = (now[a.ref_id] || 0) + a.quantity;
    s.el.querySelector(".formslot").innerHTML = `<div class="form"><table>${orders.map((o) => `<tr><td>${esc(o.customer)}</td><td class="num">注文 ${o.quantity}${unit}</td>
      <td class="num"><input type="number" min="0" step="1" data-order="${o.order_id}" value="${now[o.order_id] || 0}">${unit}</td></tr>`).join("")}</table>
      <div class="foot"><button class="act main" data-send="${esc(k)}">この数量・配分で交渉し直す</button><button class="act" data-cancel="${esc(k)}">やめる</button></div></div>`;
    formOpen = k;
  }
  function closeForm(k, stopped) {
    const s = shown.get(k);
    if (s) s.el.querySelector(".formslot").innerHTML = stopped ? `<div class="stop">状況が変わったので、入力を止めました。新しい内容を見てください</div>` : "";
    formOpen = null;
  }
  $("stage").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.src) return openSource(b.dataset.src);
    if (MODE !== "live") return;
    const it = shown.get(b.dataset.decide || b.dataset.change || b.dataset.pres || b.dataset.approve || b.dataset.send || b.dataset.cancel || "")?.item;
    if (!it) return;
    const d = it.data;
    if (b.dataset.decide) {
      const o = d.options.find((x) => String(x.option_id) === b.dataset.opt);
      b.disabled = true;
      return post("/api/decide", o.purchase ? { owner: OWNER, case_id: d.case_id, option_id: o.option_id, action: "approve", fingerprint: o.fingerprint, idempotency_key: key(), actor: "staff" }
        : { owner: OWNER, case_id: d.case_id, option_id: null, action: "reject", fingerprint: null, idempotency_key: key(), actor: "staff" }, it.key);
    }
    if (b.dataset.approve) { b.disabled = true; return post("/api/decide", { owner: OWNER, case_id: d.case_id, option_id: d.option_id, action: "approve", fingerprint: d.fingerprint, idempotency_key: key(), actor: "president" }, it.key); }
    if (b.dataset.pres) { b.disabled = true; return post("/api/answer", { owner: OWNER, consultation_id: d.consultation_id, option: "change", idempotency_key: key(), change: d.change, role: "president" }, it.key); }
    if (b.dataset.change) return openForm(it.key);
    if (b.dataset.cancel) return closeForm(it.key, false);
    if (b.dataset.send) {
      const allocations = [];
      shown.get(it.key).el.querySelectorAll(".formslot input").forEach((i) => { const q = Math.max(0, parseInt(i.value || "0", 10) || 0); if (q) allocations.push({ order_id: Number(i.dataset.order), quantity: q }); });
      if (!allocations.length) return;
      b.disabled = true;
      return post("/api/answer", { owner: OWNER, consultation_id: d.consultation_id, option: "change", idempotency_key: key(),
        change: { quantity: allocations.reduce((s, a) => s + a.quantity, 0), allocations }, role: "staff" }, it.key).then(() => closeForm(it.key, false));
    }
  });

  // ── evidence on demand ──
  async function openSource(k) {
    const s = shown.get(k);
    if (!s) return;
    const parts = await Promise.all(s.item.source.map((x) => fetch(`/api/source?owner=${OWNER}&db=${x.db}&table=${x.table}&id=${x.id}`).then((r) => r.json()).catch(() => null)));
    const rows = (o) => Object.entries(o).map(([k2, v]) => `<tr><td>${esc(k2)}</td><td>${esc(typeof v === "object" ? JSON.stringify(v) : v)}</td></tr>`).join("");
    $("dbody").innerHTML = parts.map((p, i) => p && p.row ? `<h3>${esc(s.item.source[i].db)}.${esc(p.table)} ${p.row.id}</h3><table>${rows(p.row)}</table>` +
      (p.step ? `<h3>この文を送った AI の道具の呼び出し（agent_step ${p.step.id}）</h3><table>${rows(p.step)}</table>` : "") : `<h3>${NOREC}</h3>`).join("");
    $("drawer").hidden = false;
  }
  $("dclose").addEventListener("click", () => ($("drawer").hidden = true));

  // ── start ──
  fetch(`/api/work?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`).then((r) => r.json()).then((v) => {
    if (!v.item) { $("aitext").textContent = NOREC; return; }
    header(v);
    $("viewbtn").hidden = false;
    setView(VIEW);
    if (MODE === "live") { $("opbtn").hidden = ITEM !== "buri_xl"; live(); return; }
    fetch(`/api/current_timeline?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`).then((r) => (r.ok ? r.json() : null)).catch(() => null)
      .then((tl) => replay(v, tl));
  });
})();
