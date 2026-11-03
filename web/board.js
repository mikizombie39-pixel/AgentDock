// Agent Dock: Adaptive Workspace (magnet board).
// The stage and every happening come from /api/flow (facts recorded in the business DBs); the board composition comes from
// /api/board (Claude chose it with the compose tools, code checked it). Every amount is computed by code. Nothing here is scripted:
// a card moves only when a recorded fact arrives, and the processing steps light only from recorded step rows.
(function () {
  "use strict";
  const OWNER = "amiya";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const yen = (v) => (v === null || v === undefined) ? "—" : `${Number(v).toLocaleString("ja-JP")}円`;
  const NOREC = "記録なし";
  const unit = () => esc(board?.item?.unit || "");   // the item's unit from the business DB (本・kg・箱)
  const STAGES = ["idle", "order", "gathering", "organizing", "decision", "processing", "done", "exception"];
  const STAGE_LABEL = { idle: "待機", order: "注文", gathering: "情報収集", organizing: "条件整理", decision: "判断", processing: "正式処理", done: "完了", exception: "例外" };
  const STEP_LABEL = { buy_trade: "仕入れの取引を市場の取引帳簿に登録", seller_stock: "漁協の在庫と仮押さえを確定", terms: "選ばなかった条件と仮押さえを取り消し",
    sell_trades: "B社への売りを市場の取引帳簿に登録", buyer_side: "B社の注文と在庫に反映", own_books: "自社の帳簿（在庫・注文）に反映" };
  const STEP_ORDER = Object.keys(STEP_LABEL);
  const CERT = { order: "注文あり", conditional: "条件付きの合意", needs_staff: "相手の承認待ち", lead: "未打診", none: "売り先なし" };
  const NODE_X = { iwase: 330, amiya: 800, buyer_b: 1150, buyer_c: 1440 };
  const NODE_Y = 40;
  const BEND = { iwase: 0, buyer_b: 34, buyer_c: -34 };   // curve the lines so that no line passes through another node
  const pathFor = (a, b) => {
    const o = a === OWNER ? b : a, c = BEND[o] || 0;
    return `M ${NODE_X[a]} ${NODE_Y} Q ${(NODE_X[a] + NODE_X[b]) / 2} ${NODE_Y + c * 2} ${NODE_X[b]} ${NODE_Y}`;
  };
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const SPEEDS = [{ k: 1, label: "ふつう" }, { k: 0.35, label: "速く" }, { k: 0, label: "なし" }];
  let speedIx = reduce ? 2 : 0;
  const D = (ms) => Math.round(ms * SPEEDS[speedIx].k);

  // ── state ──
  const seen = new Set();
  const queue = [];
  let first = true;
  let flow = null, board = null, busyAsk = null, detailOpen = false, lastBoardKey = "";
  const model = { order: null, orgs: {}, vault: null, ticker: [], decided: null, steps: [], api: [], done: false, question: null };
  const cards = new Map();
  let SCALE = 1;

  function fit() {
    SCALE = Math.min(window.innerWidth / 1600, window.innerHeight / 900);
    $("stage").style.transform = `scale(${SCALE})`;
  }
  window.addEventListener("resize", fit); fit();

  // ── speed ──
  function showSpeed() { $("speed").textContent = `動き：${SPEEDS[speedIx].label}`; }
  $("speed").addEventListener("click", () => { speedIx = (speedIx + 1) % SPEEDS.length; showSpeed(); });
  window.addEventListener("keydown", (e) => { if (e.key === "f" && document.activeElement !== $("ask")) { speedIx = (speedIx + 1) % SPEEDS.length; showSpeed(); } });
  showSpeed();

  // ── network of organizations ──
  function buildNet(orgs) {
    if ($("nodes").childElementCount) return;
    const svg = $("lines");
    for (const o of orgs) {
      if (!o.own) {
        const l = document.createElementNS("http://www.w3.org/2000/svg", "path");
        l.setAttribute("d", pathFor(OWNER, o.code)); l.setAttribute("fill", "none");
        l.id = `line-${o.code}`;
        svg.appendChild(l);
      }
      const n = document.createElement("div");
      n.className = "node" + (o.own ? " own" : "");
      n.id = `node-${o.code}`;
      n.style.left = `${NODE_X[o.code]}px`; n.style.top = `${NODE_Y - (o.own ? 31 : 27)}px`;
      n.innerHTML = `<div class="disc">${o.own ? "自社" : "AI"}</div><div class="nm">${esc(o.name)}</div><div class="st" id="st-${o.code}"></div>`;
      $("nodes").appendChild(n);
    }
  }

  function packet(frm, to, label, animate) {
    const other = frm === OWNER ? to : frm;
    const st = $(`st-${other}`);
    if (st) st.textContent = label;
    if (!animate || !NODE_X[frm] || !NODE_X[to]) return Promise.resolve();
    const line = $(`line-${other}`);
    if (line) line.classList.add("hot");
    const p = document.createElement("div");
    p.className = "packet" + (frm === OWNER ? " out" : "");
    p.textContent = label;
    $("net").appendChild(p);
    p.style.left = "0px"; p.style.top = "0px";
    p.style.offsetPath = `path("${pathFor(frm, to)}")`;
    p.style.offsetRotate = "0deg";
    const dur = Math.max(D(1100), 1);
    const a = p.animate([{ offsetDistance: "0%", opacity: 0 }, { opacity: 1, offset: .12 }, { opacity: 1, offset: .88 },
      { offsetDistance: "100%", opacity: 0 }], { duration: dur, easing: "cubic-bezier(.3,.6,.3,1)" });
    return a.finished.catch(() => {}).then(() => {
      p.remove();
      if (line) line.classList.remove("hot");
      const node = $(`node-${frm === OWNER ? to : frm}`);
      if (node && frm !== OWNER) {
        node.classList.add("lit");
        node.querySelector(".disc").animate([{ transform: "scale(1.25)" }, { transform: "scale(1)" }], { duration: D(420) || 1 });
      }
    });
  }

  // ── applying recorded happenings to the model ──
  function orgOf(item) { return item.frm === OWNER ? item.to : item.frm; }
  function apply(item, animate) {
    let wait = Promise.resolve();
    switch (item.kind) {
      case "order": model.order = item; break;
      case "packet": {
        const o = orgOf(item);
        const org = model.orgs[o] || (model.orgs[o] = { lines: [] });
        org.lines.push(item);
        wait = packet(item.frm, item.to, item.text.replace(/^.*?：/, ""), animate);
        break;
      }
      case "vault": model.vault = item; break;
      case "question": model.question = item; busyAsk = null; break;
      case "decision": model.decided = item; break;
      case "step": model.steps.push(item.step); break;
      case "api": model.api.push(item); break;
      case "done": model.done = true; break;
      default: break;
    }
    model.ticker.push(item);
    return wait;
  }

  function gap(item) {
    return { packet: 250, step: 380, api: 240, order: 500, vault: 420, wake: 300, present: 300, answer: 200 }[item.kind] ?? 160;
  }

  let pumping = false;
  async function pump() {
    if (pumping) return;
    pumping = true;
    try {
      while (queue.length) {
        const item = queue.shift();
        const w = apply(item, true);
        if (item.kind === "present" || item.kind === "answer" || item.kind === "decision") await loadBoard(true);
        render();
        await w;
        if (queue.length && D(gap(item))) await new Promise((r) => setTimeout(r, D(gap(item))));
      }
    } finally { pumping = false; }
    render();
  }

  // ── which stage to draw ──
  function viewStage() {
    if (model.done) return "done";
    if (model.steps.length) return "processing";
    if (!flow) return "idle";
    if (!queue.length) return flow.stage;
    const hasOffer = Object.values(model.orgs).some((o) => o.lines.some((l) => l.to === OWNER && l.msg_kind === "offer"));
    const hasOut = Object.values(model.orgs).some((o) => o.lines.some((l) => l.frm === OWNER));
    if (hasOffer) return "organizing";
    if (hasOut) return "gathering";
    return model.order ? "order" : "idle";
  }

  // ── cards (magnets) with FLIP moves ──
  function rectOf(el) {
    const r = el.getBoundingClientRect();
    return { x: r.left / SCALE, y: r.top / SCALE, w: r.width / SCALE, h: r.height / SCALE };
  }
  function applyLayout(specs) {
    const keep = new Set(specs.map((s) => s.key));
    for (const [k, el] of cards) {
      if (keep.has(k)) continue;
      cards.delete(k);
      if (!D(300)) { el.remove(); continue; }
      el.animate([{ opacity: 1, transform: "scale(1)" }, { opacity: 0, transform: "scale(.88) translateY(30px)" }], { duration: D(320), easing: "ease-in" })
        .finished.catch(() => {}).then(() => el.remove());
    }
    for (const s of specs) {
      let el = cards.get(s.key);
      const isNew = !el;
      if (isNew) {
        el = document.createElement("div");
        el.dataset.key = s.key;
        $("board").appendChild(el);
        cards.set(s.key, el);
      }
      const before = isNew ? null : rectOf(el);
      if (el._html !== s.html) { el.innerHTML = s.html; el._html = s.html; }
      el.className = `mag ${s.cls}`;
      Object.assign(el.style, { left: `${s.x}px`, top: `${s.y}px`, width: `${s.w}px`, height: s.h ? `${s.h}px` : "", zIndex: s.z || 1 });
      if (s.onclick) el.onclick = s.onclick; else el.onclick = null;
      if (!D(500)) continue;
      const after = rectOf(el);
      if (isNew) {
        const src = s.from && cards.get(s.from) && cards.get(s.from) !== el ? rectOf(cards.get(s.from)) : null;
        const fromT = src ? `translate(${src.x - after.x}px, ${src.y - after.y}px) scale(.35)` : "translateY(18px) scale(.94)";
        el.animate([{ transform: fromT, opacity: 0, transformOrigin: "0 0" }, { transform: "none", opacity: 1, transformOrigin: "0 0" }],
          { duration: D(560), easing: "cubic-bezier(.2,.8,.2,1)" });
      } else if (before && (Math.abs(before.x - after.x) > 1 || Math.abs(before.y - after.y) > 1 || Math.abs(before.w - after.w) > 1)) {
        const sx = before.w / after.w, sy = before.h / after.h;
        el.animate([{ transform: `translate(${before.x - after.x}px, ${before.y - after.y}px) scale(${sx}, ${sy})`, transformOrigin: "0 0" },
          { transform: "none", transformOrigin: "0 0" }], { duration: D(620), easing: "cubic-bezier(.2,.8,.2,1)" });
      }
    }
  }

  // ── card contents ──
  function orgCardHtml(code) {
    const org = model.orgs[code];
    const name = (flow?.orgs || []).find((o) => o.code === code)?.name || code;
    const last = org.lines[org.lines.length - 1];
    let status = "やり取り中";
    if (last.to === OWNER && last.msg_kind === "offer") status = "条件が届きました";
    else if (last.to === OWNER && last.msg_kind === "decline") status = "断られました";
    else if (last.frm === OWNER) status = "返事を待っています";
    else if (last.to === OWNER && !last.msg_kind) status = "情報が届きました";
    const rows = org.lines.slice(-3).map((l) => `<div class="n">${esc(l.text)}</div>`).join("");
    return `<div class="k">${esc(name)} の AI とのやり取り</div><div class="mid">${status}</div>${rows}`;
  }
  function vaultHtml(notes, big) {
    const ns = notes || [];
    return `<div class="k">社長の経験（Vault）</div><div class="mid">社長の過去の経験が参考になります</div>` +
      ns.slice(0, big ? 3 : 2).map((n) => `<div class="n"><b>${esc(n.title)}</b>${n.applies_when ? `　適用：${esc(n.applies_when)}` : ""}</div>`).join("");
  }
  function evidenceHtml(c, big) {
    const d = c.data || {};
    const note = c.note ? `<div class="n">${esc(c.note)}</div>` : "";
    switch (c.card_type) {
      case "agreement": return `<div class="k">条件付きの合意</div><div class="mid">${esc(d.buyer)}が ${d.quantity}${unit()} を ${yen(d.unit_price_yen)}／${unit()} で</div>
        <div class="n">${esc(d.status_label)}・期限 ${esc((d.valid_until || "").slice(11, 16))}</div>${big ? `<div class="n">条件：${esc(d.condition)}</div>` : ""}${note}`;
      case "quote": return `<div class="k">届いた条件</div><div class="mid">${esc(d.seller)}：${d.quantity}${unit()}・${yen(d.unit_price_yen)}／${unit()}</div>
        <div class="n">期限 ${esc((d.valid_until || "").slice(11, 16))}</div>${note}`;
      case "vault": return `<div class="k">社長の経験（Vault）</div><div class="mid">${esc(d.title)}</div>${big ? `<div class="n">${esc(d.body)}</div>` : ""}
        <div class="n">${esc(d.author)}・${esc(d.written_on)}　適用：${esc(d.applies_when)}</div>${note}`;
      case "order": return `<div class="k">注文</div><div class="mid">${esc(d.customer)}から ${esc(d.item)} ${d.quantity}${esc(d.unit)}</div>${note}`;
      case "customer": return `<div class="k">売り先</div><div class="mid">${esc(d.name)}</div><div class="n">これまで ${d.times}回・${d.min_sold}〜${d.max_sold}${unit()}</div>${note}`;
      case "post": return `<div class="k">仲買の掲示</div><div class="mid">${esc(d.owner)}：${d.quantity}${unit()}・${yen(d.unit_price_yen)}／${unit()}</div>${note}`;
      default: return `<div class="k">メモ</div>${note}`;
    }
  }
  function evKey(c) {
    const d = c.data || {};
    const byName = Object.fromEntries((flow?.orgs || []).map((o) => [o.name, o.code]));
    if (c.card_type === "vault") return "vault";
    if (c.card_type === "order") return "order";
    if (c.card_type === "agreement" && byName[d.buyer]) return `org:${byName[d.buyer]}`;
    if (c.card_type === "quote" && byName[d.seller]) return `org:${byName[d.seller]}`;
    return `ev:${c.card_type}:${c.ref_id}`;
  }
  function numbersHtml(o, full) {
    const n = o.numbers;
    const gp = n.gross_profit_yen ?? n.gross_profit_without_unconfirmed_yen;
    const rows = [];
    if (n.purchase) rows.push(`<div>仕入れ：${esc(n.purchase.seller_name)} ${n.purchase.quantity}${unit()} × ${yen(n.purchase.unit_price_yen)}</div><div class="v">−${yen(n.purchase_yen)}</div>`);
    if (full) {
      for (const l of n.lines) rows.push(`<div>売り：${esc(l.to)} ${l.quantity}${unit()} × ${yen(l.unit_price_yen)}<span class="cert ${l.certainty}">${CERT[l.certainty] || ""}</span></div><div class="v">${yen(l.revenue_yen)}</div>`);
      const steps = [...new Set(n.lines.map((l) => l.process && l.process.step).filter(Boolean))];
      rows.push(`<div>加工${steps.length ? `（${steps.map(esc).join("・")}）` : ""}</div><div class="v">−${yen(n.process_cost_yen)}</div>`);
    } else {
      rows.push(`<div>売り先：${n.lines.map((l) => `${esc(l.to)} ${l.quantity}${unit()}<span class="cert ${l.certainty}">${CERT[l.certainty] || ""}</span>`).join("　") || "なし"}</div><div class="v"></div>`);
    }
    rows.push(`<div class="t">粗利（${esc(n.profit_kind || "見込み")}）</div><div class="v t">${yen(gp)}</div>`);
    let extra = "";
    if (n.unsold_quantity) extra += `<div class="n">売り先のない ${n.unsold_quantity}${unit()}・売れ残れば −${yen(n.unsold_loss_yen)}</div>`;
    if (full && n.uncounted_costs?.length) extra += `<div class="n">未計上：${n.uncounted_costs.map(esc).join("・")}</div>`;
    return `<div class="kv">${rows.join("")}</div>${extra}`;
  }
  // the grounds, kept apart: numbers computed by code / past experience referenced from the Vault / Claude's proposal
  function groundsHtml(answer, prim, L) {
    const n = prim && prim.numbers;
    const gp = n ? (n.gross_profit_yen ?? n.gross_profit_without_unconfirmed_yen) : null;
    const code = n ? `${n.purchase ? `仕入れ ${n.purchase.quantity}${unit()} × ${yen(n.purchase.unit_price_yen)}・` : ""}粗利（${esc(n.profit_kind || "見込み")}）${yen(gp)}`
      + (n.errors && n.errors.length ? `・実行できない理由：${n.errors.map(esc).join("・")}` : "") : NOREC;
    const vault = (L.cards || []).filter((c) => c.card_type === "vault").map((c) => `${esc(c.data?.title)}（${esc(c.data?.author)}・${esc(c.data?.written_on)}）`);
    return `<div class="grounds"><div><b>コードの計算（業務 DB から）</b>${code}</div>
      <div><b>参照した経験（Vault）</b>${vault.length ? vault.join("／") : NOREC}</div>
      <div class="a"><b>Claude の提案</b>${esc(answer)}</div></div>`;
  }
  function optHtml(o, role, L, full) {
    const tag = role === "rec" ? `<span class="tag">Claude の勧め</span>` : `<span class="tag ${o.executable ? "own" : "off"}">別の選択肢</span>`;
    const why = [];
    if (role === "rec") {
      why.push(`<li>${esc(o.reason)}</li>`);
      for (const c of (L.cards || []).slice(0, 2)) if (c.note) why.push(`<li class="c">${esc(c.note)}</li>`);
      why.push(`<li>金額はコードが業務 DB から計算しました</li>`);
      why.push(`<li>正式な確定には担当者の承認が要ります</li>`);
    } else {
      why.push(`<li>${esc(o.reason)}</li>`);
      if (o.risk) why.push(`<li class="c">${esc(o.risk)}</li>`);
    }
    const errs = !o.executable && !o.changed ? `<div class="err">現在の条件では実行できません：${o.numbers.errors.map(esc).join("・")}</div>` : "";
    const chg = o.changed ? `<div class="chg">表示の後に条件が変わりました。この案は承認できません${o.numbers.errors.length ? "：" + o.numbers.errors.map(esc).join("・") : ""}</div>` : "";
    return `${tag}<div class="${role === "rec" ? "big" : "mid"}">${esc(o.label)}</div><ul class="why">${why.join("")}</ul>${numbersHtml(o, full)}${errs}${chg}`;
  }

  // ── layouts ──
  function preDecision(stage) {
    const specs = [];
    if (!model.order) {
      specs.push({ key: "idle", cls: "idle", x: 500, y: 110, w: 600, h: 150,
        html: `<div class="mid">いまは仕事がありません</div><div class="n">業務の出来事（注文・届いた条件・掲示）が入ると、Claude が動き始めます</div>` });
      return specs;
    }
    specs.push({ key: "order", cls: "order", x: 40, y: 0, w: 440, h: 150,
      html: `<div class="k">注文</div><div class="big">${esc(model.order.text.replace(/^新しい注文を確認しました：/, ""))}</div><div class="n">新しい注文を確認しました</div>` });
    const codes = Object.keys(model.orgs).filter((c) => c !== OWNER);
    codes.forEach((c, i) => specs.push({ key: `org:${c}`, cls: "org", x: 520, y: i * 128, w: 520, h: 116, from: "order", html: orgCardHtml(c) }));
    let ty = 0;
    if (model.vault) { specs.push({ key: "vault", cls: "vault", x: 1080, y: 0, w: 480, h: 190, from: "order", html: vaultHtml(model.vault.notes, true) }); ty = 204; }
    const rows = model.ticker.filter((t) => t.kind !== "packet").slice(-9).reverse()
      .map((t) => `<div class="row"><b>${esc((t.at || "").slice(11, 19))}</b>　${esc(t.text)}</div>`).join("");
    specs.push({ key: "ticker", cls: "ticker", x: 1080, y: ty, w: 480, h: 500 - ty, html: `<div class="k">Claude の作業の記録（実際の処理）</div>${rows}` });
    return specs;
  }

  function decision() {
    const specs = [];
    if (!board || !board.layout) return preDecision("organizing");
    const L = board.layout, opts = board.options;
    const prim = opts.find((o) => o.code === L.primary) || opts.find((o) => o.recommended);
    const alt = opts.find((o) => o.code === L.alternative);
    const ev = (L.cards || []).filter((c) => !(prim && c.card_type === "order"));
    const focus = L.focus || "recommend";
    let px = 40, pw = 760, ax = 830, aw = 400, ex = 1260, ewid = 300;
    if (focus === "compare") { pw = 640; ax = 700; aw = 640; ex = 1370; ewid = 190; }
    const bigEv = (focus === "partner" && ev.find((c) => c.card_type === "agreement")) || (focus === "vault" && ev.find((c) => c.card_type === "vault"));
    if (bigEv) { pw = 600; ax = -1; }
    const turn = (board.turns || []).slice(-1)[0];
    // a recorded question whose answer has not been recorded yet stays on the board while Claude works on it
    const qi = model.ticker.map((t) => t.kind).lastIndexOf("question"), ai = model.ticker.map((t) => t.kind).lastIndexOf("answer");
    const open = qi > ai ? { q: model.ticker[qi].text, a: null } : null;
    const asked = busyAsk || open || (turn && L.answer ? { q: turn.question, a: L.answer } : null);
    const H = asked ? 300 : 390;
    if (prim) specs.push({ key: `opt:${prim.code}`, cls: "rec", x: px, y: 0, w: pw, h: H, z: 2, html: optHtml(prim, "rec", L, detailOpen || focus === "compare") });
    if (alt && ax >= 0) specs.push({ key: `opt:${alt.code}`, cls: "alt" + (alt.executable ? "" : " off"), x: ax, y: 0, w: aw, h: H, html: optHtml(alt, "alt", L, focus === "compare") });
    const used = new Set();
    const keyOf = (c) => { const k = evKey(c); return used.has(k) ? `ev:${c.card_type}:${c.ref_id}` : k; };
    if (bigEv) {
      const k = keyOf(bigEv);
      used.add(k);
      specs.push({ key: k, cls: bigEv.card_type === "vault" ? "vault" : "org", x: 660, y: 0, w: 570, h: H, html: evidenceHtml(bigEv, true) });
    }
    const rest = ev.filter((c) => c !== bigEv).slice(0, 4);
    const eh = rest.length ? Math.floor((H - (rest.length - 1) * 10) / rest.length) : 0;
    rest.forEach((c, i) => { const k = keyOf(c); used.add(k); specs.push({ key: k, cls: (c.card_type === "vault" ? "vault" : "org") + " ev", x: bigEv ? 1250 : ex, y: i * (eh + 10), w: bigEv ? 310 : ewid, h: eh, html: evidenceHtml(c, eh > 240) }); });
    // the answer to the staff's question
    let chipX = 40;
    if (asked) {
      specs.push({ key: asked.a ? `ans:${turn.id}` : "ans:pending", cls: "ans", x: 40, y: H + 12, w: 1000, h: 500 - H - 12,
        html: `<div class="qa"><div class="q">あなた：${esc(asked.q)}</div>${asked.a ? groundsHtml(asked.a, prim, L) : `<div class="a">Claude が業務の状態を確かめ直しています…</div>`}</div>` });
      chipX = 1060;
    }
    // what was gathered, folded into small magnets
    const chips = [];
    if (model.order && !used.has("order")) chips.push(["order", "注文"]);
    for (const c of Object.keys(model.orgs)) if (!used.has(`org:${c}`)) chips.push([`org:${c}`, `${(flow.orgs.find((o) => o.code === c) || {}).name || c}とのやり取り`]);
    if (model.vault && !used.has("vault")) chips.push(["vault", "社長の経験"]);
    chips.push(["ticker", `作業の記録 ${model.ticker.length}件`]);
    let cx = chipX, cy = H + 20;
    for (const [k, label] of chips) {
      const w = 26 + label.length * 13;
      if (cx + w > 1560) { cx = chipX; cy += 40; }
      specs.push({ key: k, cls: "chip", x: cx, y: cy, w, h: 32, html: esc(label), onclick: () => openHistory() });
      cx += w + 8;
    }
    return specs;
  }

  function processing() {
    const specs = [];
    const chosen = board && board.options.find((o) => o.id === (board.decisions.slice(-1)[0] || {}).option_id) || (board && board.options.find((o) => o.code === board.layout?.primary));
    if (chosen) specs.push({ key: `opt:${chosen.code}`, cls: "rec", x: 40, y: 0, w: 460, h: 160,
      html: `<span class="tag ok">承認済み</span><div class="mid">${esc(chosen.label)}</div><div class="n">担当者が承認しました。Agent Dock が正式な処理を進めます</div>` });
    const lis = STEP_ORDER.map((s) => `<li class="${model.steps.includes(s) ? "on" : ""}"><i></i>${STEP_LABEL[s]}</li>`).join("");
    const api = model.api.map((a) => `<div class="n">${esc(a.text)}</div>`).join("");
    specs.push({ key: "steps", cls: "steps", x: 540, y: 0, w: 640, h: 360, html: `<div class="k">正式な処理（記録された手順）</div><ol class="steplist">${lis}</ol>${api}` });
    return specs;
  }

  function done() {
    const specs = [];
    const st = board?.case?.status;
    if (st === "rejected") {
      specs.push({ key: "result", cls: "done", x: 300, y: 0, w: 1000, h: 200,
        html: `<div class="big">見送りました</div><div class="n">仮押さえと条件付きの合意を取り消し、相手に知らせました</div>` });
      return specs;
    }
    const recs = (board?.records || []).map((r) => {
      if (r.kind === "market_trade") {
        const both = r.seller_booked && r.buyer_booked;
        return `<div>${r.side === "buy" ? `${esc(r.counterparty)}から ${r.quantity}${unit()} を仕入れ` : `${esc(r.counterparty)}へ ${r.quantity}${unit()} を売り`}（${yen(r.unit_price_yen)}／${unit()}）
          <div class="s ${both ? "" : "bad"}">市場の取引帳簿に登録・${both ? "売り手と買い手の両方の帳簿" : "帳簿は片側だけ（照合が要ります）"}・${esc(r.trade_id)}</div></div>`;
      }
      if (r.kind === "order_fill") return `<div>${esc(r.counterparty)}の注文に ${r.quantity}${unit()}<div class="s">自社の帳簿で注文に引き当て済み（注文の状態：${{ filled: "充足", partly_filled: "一部充足" }[r.order_status] || esc(r.order_status)}）</div></div>`;
      return `<div>引き当てのない在庫 ${r.quantity}${unit()}<div class="s bad">売れたことにはしていません</div></div>`;
    }).join("");
    const chosen = board?.options.find((o) => o.chosen);
    const gp = chosen ? (chosen.numbers.gross_profit_yen ?? chosen.numbers.gross_profit_without_unconfirmed_yen) : null;
    specs.push({ key: "result", cls: "done", x: 300, y: 0, w: 1000, h: 380,
      html: `<span class="tag ok">完了</span><div class="big">取引が完了しました</div><div class="recs">${recs}</div>
        <div class="n">市場の取引帳簿に登録しました${gp !== null ? `・粗利（見込み）${yen(gp)}・運賃は未計上` : ""}</div>
        <div class="n">次の仕事：待ちの出来事はありません。新しい出来事が入ると、Claude が動き始めます</div>` });
    specs.push({ key: "ticker", cls: "chip", x: 300, y: 396, w: 260, h: 32, html: `これまでの経過（${model.ticker.length}件）`, onclick: () => openHistory() });
    return specs;
  }

  function exception() {
    const dec = board?.decisions?.slice(-1)[0];
    let why = "";
    if (dec) { try { const d = JSON.parse(dec.detail || "{}"); why = (d.reasons || [d.error || ""]).join("・"); } catch (e) { why = ""; } }
    const last = [...model.ticker].reverse().find((t) => t.kind === "error" || t.kind === "exception");
    const werr = flow?.worker?.error;
    const st = board?.case?.status;
    const head = st === "needs_president" ? "担当者の権限を超えるため、社長の承認が要ります" : st === "incomplete" ? "正式な処理が途中で止まりました" : "そのままでは進められない状態です";
    return [{ key: "exc", cls: "exc", x: 300, y: 0, w: 1000, h: 220,
      html: `<span class="tag" style="background:var(--out)">例外</span><div class="big">${esc(head)}</div>
        <div class="n">${esc(why || (last && last.text) || werr || "")}</div>
        <div class="n">${st === "stale" ? "Claude が新しい条件で調べ直し、ボードを出し直します" : werr ? "実演の通しは止まっています（技術の検証の画面から再開できます）" : ""}</div>` }];
  }

  // ── dock (operations the person can do now) ──
  function dock(stage) {
    const el = $("actions");
    const L = board?.layout;
    const thinking = flow?.busy && (flow.busy.event === "staff_question");
    const cons = board?.consultation;
    const waitApprove = board?.case?.status === "needs_president" && (board.decisions || []).slice(-1)[0]?.result === "needs_president";
    $("dock").classList.toggle("hidden", stage !== "decision" && !waitApprove);
    if (stage !== "decision" && waitApprove) {   // the staff's approval stopped at the president (amount or priority between customers)
      const html = `<button class="act main" data-a="pres-approve">網谷さん（社長）として承認する</button>`;
      if (el._html !== html) { el.innerHTML = html; el._html = html; }
      $("lead").textContent = "担当者の承認は社長の承認待ちです（権限の表の決まり）";
      $("ask").disabled = $("askbtn").disabled = true;
      return;
    }
    if (stage !== "decision" || !L) { el.innerHTML = ""; el._html = ""; $("lead").textContent = ""; $("ask").disabled = true; $("askbtn").disabled = true; return; }
    const prim = board.options.find((o) => o.code === L.primary);
    const alt = board.options.find((o) => o.code === L.alternative);
    const b = [];
    for (const a of L.actions) {
      if (a === "approve" && prim) b.push(`<button class="act main" data-a="approve">この案を承認する</button>`);
      if (a === "approve" && alt && L.focus === "compare" && alt.executable && !alt.changed && alt.status === "offered")
        b.push(`<button class="act" data-a="approve-alt">別の案（${esc(alt.code)}）で承認する</button>`);
      if (a === "compare" && alt) b.push(`<button class="act" data-q="${esc(`「${alt.label}」と比べたい`)}">別の案と比べる</button>`);
      if (a === "check_partner") b.push(`<button class="act" data-q="相手との合意の条件と期限は大丈夫？">相手の条件を確かめる</button>`);
      if (a === "open_detail") b.push(`<button class="act" data-a="detail">${detailOpen ? "数字を閉じる" : "詳しい数字"}</button>`);
      if (a === "show_others") b.push(`<button class="act" data-a="others">ほかの案を見る</button>`);
      if (a === "hold") b.push(`<button class="act" data-a="hold">保留</button>`);
      if (a === "reject") b.push(`<button class="act" data-a="reject">見送る</button>`);
    }
    b.push(`<button class="act" data-q="${esc(GROUNDS_Q)}">根拠を聞く</button>`);
    if (cons?.status === "open") b.push(`<button class="act" data-a="change">数量・配分を変えて交渉し直す</button>`);
    if (cons?.status === "needs_president") b.push(`<button class="act main" data-a="pres-change">網谷さん（社長）として、この変更で確定する</button>`);
    for (const q of L.suggested_questions || []) b.push(`<button class="act q" data-q="${esc(q)}">${esc(q)}</button>`);
    $("lead").textContent = cons?.status === "needs_president" ? `社長の答え待ち：${changeText(cons.change)}（顧客の間の優先を変える答えは社長が確定します）` : (L.ask_human || "");
    const html = b.join("");
    if (el._html !== html) { el.innerHTML = html; el._html = html; }
    el.querySelectorAll("button").forEach((x) => { x.disabled = !!(thinking || busyAsk); });
    $("ask").disabled = $("askbtn").disabled = !!(thinking || busyAsk) || !L.actions.includes("ask") && !(L.suggested_questions || []).length && false;
  }

  // ── render ──
  function render() {
    if (!flow) return;
    buildNet(flow.orgs);
    $("mock").hidden = !(flow.worker && flow.worker.run_kind === "mock");
    const stage = viewStage();
    const ix = STAGES.indexOf(stage);
    $("rail").innerHTML = STAGES.filter((s) => s !== "exception" || stage === "exception")
      .map((s) => `<li class="${s === stage ? "cur" + (s === "exception" ? " bad" : "") : STAGES.indexOf(s) < ix && s !== "exception" ? "past" : ""}">${STAGE_LABEL[s]}</li>`).join("");
    const nowEl = $("now");
    const last = [...model.ticker].reverse().find((t) => t.kind !== "look");
    let text = last ? last.text : "業務の出来事を待っています";
    if (stage === "decision" && board?.layout) text = board.layout.headline;
    if (stage === "done") text = model.done ? "取引が完了しました" : (board?.case?.status === "rejected" ? "見送りました" : text);
    if (stage === "processing") text = "承認を受けて、Agent Dock が正式な処理を進めています";
    $("nowtext").textContent = text;
    const busy = flow.busy && stage !== "done";
    nowEl.classList.toggle("busy", !!busy || !!busyAsk);
    $("busy").textContent = busy ? flow.busy.text : busyAsk ? "質問を記録しました。Claude が起きるのを待っています" : "";
    let specs;
    if (stage === "decision") specs = decision();
    else if (stage === "processing") specs = processing();
    else if (stage === "done") specs = done();
    else if (stage === "exception") specs = exception();
    else specs = preDecision(stage);
    applyLayout(specs);
    dock(stage);
  }

  // ── drawers ──
  function openDrawer(title, html, wide) {
    if (!wide) threadOpen = false;
    $("dtitle").textContent = title; $("dbody").innerHTML = html; $("drawer").classList.toggle("wide", !!wide); $("drawer").classList.remove("off");
  }
  $("dclose").addEventListener("click", () => $("drawer").classList.add("off"));
  function openHistory() {
    openDrawer("これまでの経過（記録された事実）", [...model.ticker].reverse().map((t) => `<div class="drow"><div class="t">${esc((t.at || "").slice(11, 19))}</div>${esc(t.text)}</div>`).join(""));
  }
  function openOthers() {
    const L = board.layout;
    openDrawer("ほかの案", board.options.filter((o) => o.code !== L.primary && o.code !== L.alternative)
      .map((o) => `<div class="drow"><b>${esc(o.code)}　${esc(o.label)}</b><div class="t">${esc(o.reason)}</div>${numbersHtml(o, true)}
        ${o.executable ? "" : `<div class="err">現在の条件では実行できません：${o.numbers.errors.map(esc).join("・")}</div>`}</div>`).join("") || "<div class='drow'>ほかの案はありません</div>");
  }

  // ── operations ──
  const key = () => (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
  async function ask(text) {
    text = (text || "").trim();
    if (!text || !board?.case) return;
    busyAsk = { q: text, a: null };
    render();
    const r = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: OWNER, case_id: board.case.id, text, idempotency_key: key() }) });
    if (!r.ok) { busyAsk = null; const j = await r.json().catch(() => ({})); $("busy").textContent = j.detail || "質問を受け付けられませんでした"; }
    $("ask").value = "";
    poll();
  }
  async function decide(action, code, actor) {
    const L = board.layout;
    const prim = board.options.find((o) => o.code === (code || L.primary));
    const body = { owner: OWNER, case_id: board.case.id, option_id: action === "approve" ? prim.id : null, action,
      fingerprint: action === "approve" ? prim.fingerprint : null, idempotency_key: key(), actor: actor || "staff" };
    document.querySelectorAll("#actions button").forEach((x) => (x.disabled = true));
    const r = await fetch("/api/decide", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (j.result && j.result !== "registered" && j.result !== "held" && j.result !== "rejected") {
      $("busy").textContent = { stale: "条件が変わったため確定しませんでした。Claude が調べ直します", not_executable: "この案は現在の条件では実行できません",
        needs_president: "権限を超えるため、社長の承認が要ります" }[j.result] || j.result;
    }
    lastBoardKey = "";
    poll();
  }
  $("actions").addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.q) return ask(b.dataset.q);
    const a = b.dataset.a;
    if (a === "approve" || a === "hold" || a === "reject") return decide(a);
    if (a === "approve-alt") return decide("approve", board.layout.alternative);
    if (a === "detail") { detailOpen = !detailOpen; render(); }
    if (a === "others") openOthers();
    if (a === "change") openChange();
    if (a === "pres-change") sendAnswer(board.consultation.change, "president");
    if (a === "pres-approve") {
      const last = (board.decisions || []).slice(-1)[0];
      const o = board.options.find((x) => x.id === last?.option_id);
      if (o) decide("approve", o.code, "president");
    }
  });
  $("askbar").addEventListener("submit", (e) => { e.preventDefault(); ask($("ask").value); });

  // ── changing quantity / allocation (the board's consultation) and the president's answer ──
  const GROUNDS_Q = "勧める案の根拠を、コードの計算・社長の経験・Claude の提案に分けて教えてください";
  function changeText(ch) {
    if (!ch) return NOREC;
    const parts = (ch.allocations || []).map((x) => `${esc(x.customer)} ${x.quantity}${unit()}`);
    return `仕入れ ${ch.quantity}${unit()}` + (parts.length ? `（${parts.join("・")}）` : "");
  }
  function openChange() {
    const prim = board.options.find((o) => o.code === board.layout?.primary);
    const now = {};
    for (const x of (prim?.plan?.allocations || [])) if (x.kind === "order") now[x.ref_id] = (now[x.ref_id] || 0) + x.quantity;
    const rows = (board.orders || []).map((o) => `<tr><td>${esc(o.customer)}</td><td class="num">注文 ${o.quantity}${unit()}</td>
      <td class="num"><input type="number" min="0" step="1" data-order="${o.order_id}" value="${now[o.order_id] || 0}">${unit()}</td></tr>`)
      .concat((board.leads || []).map((c) => `<tr><td>${esc(c)}</td><td class="num">注文なし</td>
      <td class="num"><input type="number" min="0" step="1" data-customer="${esc(c)}" value="0">${unit()}</td></tr>`)).join("");
    openDrawer("数量・配分を変えて交渉し直す", `<table class="tbl"><thead><tr><th>売り先</th><th class="num">今日の注文</th><th class="num">仕入れて回す数</th></tr></thead>
      <tbody>${rows}</tbody></table><div class="n" id="chgsum"></div>
      <div class="n">答えは記録され、Claude が漁協に条件を変えて問い合わせ直します。売り先の注文を欠いてほかへ回す答えは、社長の答えで確定します。</div>
      <button class="act main" id="chgsend">この数量・配分で交渉し直す</button><div class="n" id="chgres"></div>`);
    const sum = () => { let s = 0; $("dbody").querySelectorAll("input").forEach((i) => (s += Math.max(0, parseInt(i.value || "0", 10) || 0))); $("chgsum").textContent = `仕入れの数量：${s}${board?.item?.unit || ""}`; return s; };
    $("dbody").querySelectorAll("input").forEach((i) => i.addEventListener("input", sum));
    sum();
    $("chgsend").addEventListener("click", () => {
      const allocations = [];
      $("dbody").querySelectorAll("input").forEach((i) => {
        const q = Math.max(0, parseInt(i.value || "0", 10) || 0);
        if (!q) return;
        allocations.push(i.dataset.order ? { order_id: Number(i.dataset.order), quantity: q } : { customer: i.dataset.customer, quantity: q });
      });
      if (!allocations.length) { $("chgres").textContent = "数量を1以上にしてください"; return; }
      sendAnswer({ quantity: sum(), allocations }, "staff");
    });
  }
  async function sendAnswer(change, role) {
    const c = board?.consultation;
    if (!c || !change) return;
    document.querySelectorAll("#actions button, #chgsend").forEach((x) => (x.disabled = true));
    const r = await fetch("/api/answer", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: OWNER, consultation_id: c.consultation_id, option: "change", idempotency_key: key(), change, role }) });
    const j = await r.json().catch(() => ({}));
    const msg = j.result === "answered" ? "答えを記録しました。Claude が条件を変えて問い合わせ直します"
      : j.result === "needs_president" ? "顧客の間の優先を変える答えなので、社長の答えを待ちます"
      : (j.detail || j.error || "答えを記録できませんでした");
    $("busy").textContent = msg;
    if ($("chgres")) $("chgres").textContent = msg;
    lastBoardKey = "";
    poll();
  }

  // ── the negotiation thread (GET /api/thread only: what the market recorded) ──
  const KIND = { inquiry: "問い合わせ", offer: "条件の返事", decline: "断りの返事" };
  const HOLD = { held: "押さえ中", released: "下ろした", confirmed: "確定", expired: "期限切れ" };
  let threadOpen = false;
  const threadItem = () => board?.item?.code || model.order?.item || null;
  async function showThread() {
    const item = threadItem();
    if (!item) { openDrawer("交渉のやり取り", `<div class="drow">${NOREC}</div>`); return; }
    let t;
    try { t = await (await fetch(`/api/thread?owner=${OWNER}&item=${encodeURIComponent(item)}`)).json(); } catch (e) { return; }
    if (!threadOpen) return;
    const u = esc(t.unit || "");
    const cell = (v) => (v === null || v === undefined || v === "") ? NOREC : v;
    const rows = (t.messages || []).map((m) => {
      const ts = m.terms && m.terms.length ? m.terms : [{ quantity: m.quantity, unit_price_yen: m.unit_price_yen, valid_until: m.valid_until, hold_state: m.hold_state }];
      const many = (f) => ts.map(f).join("<br>");
      return `<tr><td>${esc(m.frm)} → ${esc(m.to)}</td><td>${esc(KIND[m.kind] || m.kind)}</td>
        <td class="num">${many((x) => x.quantity == null ? NOREC : `${x.quantity}${u}`)}</td>
        <td class="num">${many((x) => x.unit_price_yen == null ? NOREC : yen(x.unit_price_yen))}</td>
        <td>${many((x) => x.valid_until ? esc(x.valid_until.slice(11, 16)) : NOREC)}</td>
        <td>${esc(cell(m.reason))}</td>
        <td>${many((x) => x.hold_state ? esc(HOLD[x.hold_state] || x.hold_state) : "なし")}</td></tr>`;
    }).join("");
    openDrawer(`交渉のやり取り（${esc(board?.item?.name || item)}）`, rows ? `<table class="tbl"><thead><tr><th>送り手 → 受け手</th><th>種類</th><th class="num">数量</th>
      <th class="num">単価</th><th>期限</th><th>返事の理由</th><th>仮押さえ</th></tr></thead><tbody>${rows}</tbody></table>
      <div class="n">市場の記録（/api/thread）から読んでいます。数量・単価は条件の行ごと</div>` : `<div class="drow">${NOREC}</div>`, true);
  }
  $("threadbtn").addEventListener("click", () => { threadOpen = true; showThread(); });
  $("dclose").addEventListener("click", () => { threadOpen = false; });

  // ── polling ──
  async function loadBoard(force) {
    try {
      const b = await (await fetch(`/api/board?owner=${OWNER}`)).json();
      const k = b.case ? `${b.case.id}-${b.case.version}-${b.case.status}-${b.layout_turn}-${(b.decisions || []).length}-${(b.options || []).map((o) => o.executable + (o.changed ? "c" : "")).join("")}` : "none";
      if (force || k !== lastBoardKey) { board = b.case ? b : null; lastBoardKey = k; return true; }
    } catch (e) { /* keep the last board */ }
    return false;
  }
  async function poll() {
    try {
      flow = await (await fetch(`/api/flow?owner=${OWNER}`)).json();
    } catch (e) { return; }
    const fresh = flow.items.filter((i) => !seen.has(i.key));
    fresh.forEach((i) => seen.add(i.key));
    if (first) {
      fresh.forEach((i) => apply(i, false));
      first = false;
      await loadBoard(true);
      render();
      return;
    }
    if (busyAsk && fresh.some((i) => i.kind === "question")) busyAsk = null;
    queue.push(...fresh);
    const changed = await loadBoard(false);
    if (threadOpen) showThread();
    if (queue.length) pump(); else if (changed || fresh.length) render(); else render();
  }
  poll();
  setInterval(poll, 900);
})();
