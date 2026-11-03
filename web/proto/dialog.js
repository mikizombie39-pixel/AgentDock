// Agent Dock — dialogue prototype (design only; the product does not load this file).
// Story: Amitani-san cannot sit on the bid and the negotiation, so he hands the morning to the AI and goes to
// other work. The AI calls him back only when the situation leaves what he trusted it with. His reply sets the
// conditions the AI works under next. Preview stays on the screen; an instruction is sent to the server, and the
// AI shows it got it (✓) only after the server has recorded it and answered. A retry reuses the same key, so an
// instruction is never carried out twice.
// What is real and what is not, in this prototype: the AI's words and the other parties' answers are scripted;
// money, shortages and limits are computed by code; the server is a mock that behaves like the official API.
(function () {
  "use strict";
  const ICONS = window.ICONS;
  const NAME = { ai: "AI", human: "網谷さん", super: "スーパー", ryotei: "料亭", buri: "ブリ", shiro: "シロエビ", coop: "漁協",
    csha: "C社", bid: "入札", deal: "合意", stamp: "承認", reg: "登録", shield: "守る", memo: "経験", hand: "手", slip: "伝票" };
  const $ = (id) => document.getElementById(id);
  const stage = $("stage");
  const params = new URLSearchParams(location.search);
  const fmt = (n) => n.toLocaleString("ja-JP");
  const yen = (n) => `${fmt(n)}円`;
  const ic = (k, cls = "") => `<img class="ic ${cls}" src="${ICONS[k]}" alt="${NAME[k]}">`;
  document.querySelectorAll("img[data-ic]").forEach((el) => { el.src = ICONS[el.dataset.ic]; el.alt = NAME[el.dataset.ic]; });
  // where each thing comes from (shown with 「仕組みを見る」)
  const SRC = { script: "台本", ai: "本番は Claude の判断（試作は台本）", calc: "コードの計算", api: "正式 API（試作は模擬）" };
  const src = (k) => `<span class="srcb s-${k}">${SRC[k]}</span>`;

  // ---- rules (simulated) ----
  const T0 = 5 * 60 + 50; // 05:50
  const CFG = {
    price: { super: 22000, ryotei: 24000 }, cost: 2000, profit: 1500,
    order: { super: 4, ryotei: 6 }, left: 6,
    ask4: 18300, expect6: 17800, offer6: 17600, final6: 17900, holdMin: 30, // hold length = agentdock HOLD_MINUTES
    shiro: { ceiling: 4800, max: 8, bid: 4300, qty: 5, won: 3, cPrice: 4500, cQty: 2, close: 16 },
    slips: 12,
  };
  const ceiling = (c) => CFG.price[c] - CFG.cost - CFG.profit; // super 18,500 / ryotei 20,500
  // the limit for one lot is the lowest limit among the customers it is bought for
  const lotCeiling = (a) => Math.min(...["super", "ryotei"].filter((c) => a[c] > 0).map(ceiling));
  const unitFor = (qty) => (qty === 6 ? CFG.expect6 : CFG.ask4);
  const margin = (a, unit) => a.super * (CFG.price.super - CFG.cost - unit) + a.ryotei * (CFG.price.ryotei - CFG.cost - unit);
  const shortOf = (a) => ({ super: CFG.order.super - a.super, ryotei: CFG.order.ryotei - a.ryotei });
  const REC = "super";
  const OPTS = [
    { key: "super", alloc: { super: 4, ryotei: 2 }, phrase: "スーパーは守って。残りを料亭へ", intent: "スーパーを守る" },
    { key: "split", alloc: { super: 3, ryotei: 3 }, phrase: "半分ずつで", intent: "半分ずつ" },
    { key: "ryotei", alloc: { super: 0, ryotei: 6 }, phrase: "今回は料亭を優先", intent: "料亭を優先" },
    { key: "skip", alloc: { super: 4, ryotei: 0 }, phrase: "料亭は今回断って", intent: "料亭は断る" },
  ].map((o) => {
    const qty = o.alloc.super + o.alloc.ryotei, unit = unitFor(qty);
    return { ...o, qty, unit, margin: margin(o.alloc, unit), short: shortOf(o.alloc), limit: lotCeiling(o.alloc) };
  });

  // ---- state ----
  const S = {
    t: 0, speed: Number(params.get("speed") || 1), started: false, phase: "start", scale: 1,
    buri: "idle", ryotei: false, shiroGot: 0, shiroDone: false, buriDone: false,
    lanes: {}, tally: { reply: 0, ai: 0, reg: 0 }, hist: [], decisions: [], agenda: [], api: [],
    tableMode: "none", tokens: [], preview: null, told: null, deal: null,
    away: false, slips: 0, sending: false, executed: new Set(), resolved: 0, errors: 0,
  };
  const hhmm = (t) => { const m = T0 + Math.floor(t); return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0"); };
  const at = (dt, fn) => S.agenda.push({ at: S.t + dt, fn });
  const later = (ms, fn) => setTimeout(fn, ms / Math.max(S.speed, 1));
  const log = (who, text) => S.hist.push({ t: hhmm(S.t), who, text });
  const bump = (k) => { S.tally[k]++; renderTally(k); };

  // ---- mock server: records an instruction once per key and answers with a receipt ----
  const Server = {
    rec: new Map(), seq: 11, loseNext: params.get("fail") === "1",
    post(path, key, body) {
      return new Promise((resolve, reject) => later(650, () => {
        let r = this.rec.get(key); const dup = !!r;
        if (!r) { r = { receipt: `R-${String(++this.seq).padStart(4, "0")}`, body }; this.rec.set(key, r); }
        S.api.push({ t: hhmm(S.t), path, key, result: dup ? `同じ指示（${r.receipt}）・実行は1回のまま` : `記録 ${r.receipt}` });
        if (this.loseNext) { this.loseNext = false; reject(new Error("応答が届かない")); return; }
        resolve({ receipt: r.receipt, dup });
      }));
    },
  };

  // ---- goal band ----
  function renderOrders(arrive) {
    const chip = (cls, k, who, what, state) => `<div class="order ${cls}">${ic(k)}<div class="t">${who}<b>${what}</b></div><span class="fill">${state}</span></div>`;
    const st = (cust) => {
      if (S.buriDone) return S.told.alloc[cust] > 0 ? `${S.told.alloc[cust]}本 登録` : "見送り";
      return { idle: "", gather: "そろえ中", hold: "相談中", nego: "交渉中", agreed: "承認待ち" }[S.buri] || "";
    };
    let h = chip("sup", "super", "スーパー", "ブリ 4本", st("super"));
    h += chip(S.shiroDone ? "done" : "", "shiro", "むき身用", "シロエビ 5箱", S.started ? `${S.shiroGot}/5箱` : "");
    if (S.ryotei) h += `<div class="order ryo ${arrive ? "arrive" : ""}">${ic("ryotei")}<div class="t">料亭<b>ブリ 6本</b></div>${arrive ? '<span class="new">追加 ＋6</span>' : `<span class="fill">${st("ryotei")}</span>`}${src("script")}</div>`;
    $("orders").innerHTML = h;
  }
  function renderTally(hot) {
    const it = (k, icon, label, unit) => `<span>${ic(icon)}${label}<b class="${hot === k ? "bump" : ""}">${S.tally[k]}</b>${unit}</span>`;
    $("tally").innerHTML = it("reply", "human", "返事", "回") + it("ai", "ai", "AI のやり取り", "回") + it("reg", "reg", "登録", "件");
  }

  // ---- left column ----
  function lane(key, name, st, stCls, msg) {
    const pics = { shiro: ic("shiro") + '<span class="arrow">→</span>' + ic("bid"), buri: ic("buri") + '<span class="arrow">→</span>' + ic("coop") }[key];
    S.lanes[key] = { pics, name, st, stCls, msg, spawn: !S.lanes[key] };
    $("lanes").innerHTML = Object.keys(S.lanes).map((k) => {
      const l = S.lanes[k]; const sp = l.spawn; l.spawn = false;
      return `<div class="lane ${sp ? "spawn" : ""} ${l.stCls === "pause" ? "pause" : ""}" id="lane-${k}"><div class="row"><span class="pics">${l.pics}</span><span class="nm">${l.name}</span><span class="st ${l.stCls}">${l.st}</span></div><div class="msg">${l.msg}</div></div>`;
    }).join("");
  }
  function renderKnows(lit = []) {
    const k = (id, pics, label) => `<button class="know ${lit.includes(id) ? "lit" : ""}" data-k="${id}">${pics}<span>${label}</span></button>`;
    $("knows").innerHTML =
      k("orders", ic("super") + ic("shiro") + (S.ryotei ? ic("ryotei") : ""), `注文 ${S.ryotei ? 3 : 2}件`) +
      k("stock", ic("coop"), "漁協の残り") + k("price", ic("buri"), "売値と上限") + k("memo", ic("memo"), "過去の経験");
    $("knows").querySelectorAll(".know").forEach((b) => (b.onclick = () => openDetail(b.dataset.k)));
  }
  function renderScope(hot) {
    const row = (id, k, tx, by) => `<div class="sc ${hot === id ? "hot" : ""}" id="sc-${id}">${ic(k, "what")}<span class="tx">${tx}</span><span class="by ${by}">${ic(by === "ai" ? "ai" : "human")}${by === "ai" ? "AI が決める" : "網谷さん"}</span></div>`;
    $("scope").innerHTML =
      row("buri", "buri", `<b>ブリの値の交渉</b><br>上限は売値から計算`, "ai") +
      row("shiro", "shiro", `<b>シロエビの購入と登録</b><br>1箱 ${yen(CFG.shiro.ceiling)}・${CFG.shiro.max}箱まで`, "ai") +
      row("prio", "shield", "<b>どの得意先を優先するか</b>", "me") +
      row("approve", "stamp", "<b>ブリの取引の承認</b>", "me");
  }

  // ---- center ----
  function setFocus(inner, cls = "", who = "ai") {
    const av = who === "ai" ? `<div class="avatar" id="aiAv">${ic("ai")}<div>AI</div><span class="seal">✓</span></div>`
      : `<div class="avatar me">${ic("human")}<div>網谷さん</div></div>`;
    $("focus").innerHTML = `<div class="say focus-in">${av}<div class="bubble ${who === "ai" ? "ai" : "me"} ${cls}" id="fbub">${inner}</div></div>`;
  }
  function setTalk(lines) {
    $("talk").innerHTML = lines.map((l) => {
      const tag = l.tag ? `<span class="tag ${l.tagCls || ""}">${l.tag}</span>` : "";
      const body = `<span class="b ${l.draft ? "draft" : ""} ${l.cls || ""}" ${l.id ? `id="${l.id}"` : ""}>${l.html}</span>`;
      return l.who === "me" ? `<div class="tline me">${tag}${body}${ic("human", "av")}</div>` : `<div class="tline ai">${ic("ai", "av")}${body}${tag}</div>`;
    }).join("");
  }
  function rel(el) {
    const r = el.getBoundingClientRect(), s = stage.getBoundingClientRect();
    return { x: (r.left - s.left) / S.scale, y: (r.top - s.top) / S.scale, w: r.width / S.scale, h: r.height / S.scale };
  }
  function fly(fromEl, toEl, then) {
    if (!fromEl || !toEl) { then && then(); return; }
    const a = rel(fromEl), b = rel(toEl);
    const g = fromEl.cloneNode(true); g.removeAttribute("id");
    Object.assign(g.style, { position: "absolute", left: a.x + "px", top: a.y + "px", width: a.w + "px", height: a.h + "px", margin: 0, zIndex: 7,
      transition: "all .7s cubic-bezier(.4,0,.2,1)", pointerEvents: "none", boxSizing: "border-box", overflow: "hidden" });
    stage.appendChild(g);
    requestAnimationFrame(() => requestAnimationFrame(() => Object.assign(g.style, {
      left: b.x + "px", top: b.y + "px", width: b.w + "px", height: b.h + "px", opacity: "0.15" })));
    later(720, () => { g.remove(); then && then(); });
  }
  function ackAI() { const v = $("aiAv"); if (v) { v.classList.remove("ack", "nod"); void v.offsetWidth; v.classList.add("ack", "nod"); } }

  // Send the person's instruction. ✓ only after the server recorded it; a failure keeps the input; a retry reuses the key.
  function sendInstruction({ path, key, html, tag, onReceipt }) {
    if (S.sending) return;
    S.sending = true; setInputsDisabled(true);
    setTalk([{ who: "me", id: "said", html, tag: "送信中…", tagCls: "t-send" }]);
    Server.post(path, key, html).then((r) => {
      S.sending = false;
      if (S.executed.has(key)) return; // the same instruction never runs twice
      S.executed.add(key);
      setTalk([{ who: "me", id: "said", html, tag: `${tag}・受領 ${r.receipt}${r.dup ? "（再送）" : ""}`, tagCls: "t-go" }]);
      const av = $("aiAv");
      fly($("said"), av && av.querySelector("img"), () => { ackAI(); onReceipt(r); });
    }).catch(() => {
      S.sending = false; setInputsDisabled(false);
      setTalk([{ who: "me", id: "said", html, cls: "failed", tag: `届いたか確かめられません <button class="retry" id="retry">もう一度送る</button>`, tagCls: "t-fail" }]);
      $("retry").onclick = () => sendInstruction({ path, key, html, tag, onReceipt });
    });
  }
  function setInputsDisabled(v) { $("replies").querySelectorAll("button").forEach((b) => (b.disabled = v)); }
  // a finished question leaves the centre for the history; what the AI does now takes its place
  function retire(then) {
    const f = $("fbub");
    S.resolved++;
    fly(f, $("historyBtn"), () => { $("histN").hidden = false; $("histN").textContent = S.resolved; then(); });
    if (f) f.style.visibility = "hidden";
  }

  // ---- table ----
  function slots(area, n, cls = () => "") { let h = ""; for (let i = 0; i < n; i++) h += `<div class="slot ${cls(i)}" data-slot="${area}-${i}"></div>`; return h; }
  function renderTable() {
    const m = S.tableMode;
    if (m === "none") { $("table").innerHTML = `<div class="quiet" style="height:100%">${ic("buri")}${ic("shiro")}魚と注文は、任せるとここに並びます</div>`; return; }
    const a = S.told || S.preview, sh = a ? a.short : null;
    const slotCls = (cust) => (i) => {
      if (!sh || i < CFG.order[cust] - sh[cust]) return "";
      return a.key === "skip" && cust === "ryotei" ? "declined" : "short";
    };
    const stTxt = S.preview && !S.told ? "下見" : ({ gather: "AI がそろえ中", hold: "止めています", nego: "交渉し直し中", agreed: "合意（承認の前）", done: "正式登録" }[S.buri] || "");
    const note = (cust) => {
      if (!sh || !sh[cust]) return '<div class="note"></div>';
      if (a.key === "skip" && cust === "ryotei") return `<div class="note mute">見送り ${sh[cust]}本</div>`;
      return `<div class="note">足りない ${sh[cust]}本</div>`;
    };
    const kept = a && a.alloc.super === CFG.order.super;
    const pool = `<div class="poolwrap">${ic("coop")}<span class="lb">配り先<br>未定</span><div class="slots">${slots("pool", CFG.left)}</div></div>`;
    const eq = m === "gather"
      ? `<div class="eq">注文 ${ic("super")}<b>4</b>本 ／ 残り <b>6</b>本</div>`
      : `<div class="eq">注文 <b>10</b>本 ／ 残り <b>6</b>本 → <span class="short"><b>4</b>本 足りない</span>${src("calc")}</div>`;
    let trays = `<div class="tray sup ${kept ? "kept" : ""}"><div class="cap">${ic("super")}<b>スーパー 4本</b><span class="st">${stTxt}</span></div><div class="slots">${slots("super", 4, slotCls("super"))}</div>${note("super")}<span class="keepb">${ic("shield")}<b>守る</b></span></div>`;
    if (m === "conflict") trays += `<div class="tray ryo ${a && a.alloc.ryotei === 6 ? "kept" : ""}"><div class="cap">${ic("ryotei")}<b>料亭 6本</b><span class="st">${a ? stTxt : "注文が届きました"}</span></div><div class="slots">${slots("ryotei", 6, slotCls("ryotei"))}</div>${note("ryotei")}<span class="keepb">${ic("shield")}<b>優先</b></span></div>`;
    let metric = "";
    if (S.deal) metric = `<div class="metric"><small>粗利（合意の単価）</small><b class="yen">${yen(S.deal.margin)}</b><div class="sub">単価 ${yen(S.deal.unit)}・${S.deal.qty}本</div>${src("calc")}</div>`;
    else if (a) metric = `<div class="metric flash"><small>粗利の見込み</small><b class="yen">${yen(a.margin)}</b><div class="sub">単価 ${yen(a.unit)}（${a.qty === 6 ? "漁協の資料" : "漁協の回答"}）</div>${src("calc")}</div>`;
    $("table").innerHTML = `<div class="head">${pool}${eq}</div><div class="trays">${trays}${metric}</div>`;
    placeTokens();
  }
  function makeTokens() {
    $("tokens").innerHTML = ""; S.tokens = [];
    for (let i = 0; i < CFG.left; i++) {
      const el = document.createElement("div"); el.className = "tok pool off"; el.innerHTML = ic("buri");
      $("tokens").appendChild(el); S.tokens.push({ el, area: "pool", idx: i, cls: "pool" });
    }
  }
  function placeTokens() {
    for (const tk of S.tokens) {
      const slot = document.querySelector(`[data-slot="${tk.area}-${tk.idx}"]`);
      if (!slot) continue;
      const r = rel(slot);
      tk.el.style.left = r.x + (r.w - 52) / 2 + "px"; tk.el.style.top = r.y + (r.h - 38) / 2 + "px";
      tk.el.className = `tok ${tk.cls}`;
    }
  }
  function assign(alloc, cls) {
    let si = 0, ri = 0, pi = 0;
    S.tokens.forEach((tk) => {
      if (si < alloc.super) { tk.area = "super"; tk.idx = si++; tk.cls = cls; }
      else if (ri < alloc.ryotei) { tk.area = "ryotei"; tk.idx = ri++; tk.cls = cls; }
      else { tk.area = "pool"; tk.idx = 2 + pi++; tk.cls = "pool"; }
    });
  }
  const setTokCls = (cls) => S.tokens.forEach((tk) => { if (tk.area !== "pool") tk.cls = cls; });

  // ---- replies ----
  function renderOptions() {
    const R = $("replies");
    R.innerHTML = OPTS.map((o) => {
      const p = (k, n) => (n > 0 ? `${ic(k)}<span class="n">${n}</span>` : `${ic(k)}<span class="x">×</span>`);
      const sel = S.preview && S.preview.key === o.key ? "sel" : "";
      return `<button class="card ${sel}" data-k="${o.key}">${o.key === REC ? `<span class="rec">AI のおすすめ${src("ai")}</span>` : ""}<span class="pics">${p("super", o.alloc.super)}${p("ryotei", o.alloc.ryotei)}</span><span class="phrase">${o.phrase}</span><span class="gain">粗利<b class="yen">${yen(o.margin)}</b></span></button>`;
    }).join("") + `<button class="go" id="go" ${S.preview ? "" : "disabled"}>${ic("coop")}<span>${S.preview ? "この配分で<br>交渉し直して" : "配分を<br>選んでください"}</span></button>`;
    R.querySelectorAll(".card").forEach((b) => (b.onclick = () => preview(b.dataset.k)));
    $("go").onclick = instruct;
  }

  // ---- 1. the person's morning, and handing it over ----
  function start() {
    setFocus(`<div class="kicker">網谷さんの朝（05:50）</div>
      <div class="pic3"><span>${ic("shiro")}${ic("bid")}<em>シロエビの入札<br>締め ${hhmm(CFG.shiro.close)}</em></span><span>${ic("buri")}${ic("coop")}<em>ブリの交渉<br>漁協と</em></span><span>${ic("slip")}<em>伝票の整理<br>${CFG.slips}枚</em></span></div>
      <div class="ask" style="font-size:24px">同じ時間に 3つ。入札と交渉に張り付いていられない</div>`, "", "me");
    setTalk([{ who: "ai", html: `今朝の注文は 2件です。${ic("shiro")}入札と ${ic("buri")}交渉は、私が進めましょうか？`, tag: "AI の提案" }]);
    $("replies").innerHTML = `<button class="card big" id="delegate">${ic("human", "av")}<span class="phrase">「今朝の注文、そろえておいて」<small>任せる範囲は左の「任せた範囲」のとおり</small></span></button>`;
    $("delegate").onclick = delegate;
    log("網谷さんの事情", "シロエビの入札・ブリの交渉・伝票の整理が同じ時間に重なる");
    log("AI", "今朝の注文は 2件。入札と交渉は私が進めましょうか");
  }
  function delegate() {
    if (S.phase !== "start") return;
    setFocus(`<div class="pic">${ic("shiro")}${ic("bid")}<span class="op">・</span>${ic("buri")}${ic("coop")}<span class="w">今朝の注文 2件</span></div><div class="ask" style="font-size:24px">入札と交渉を AI に任せますか？</div>`);
    sendInstruction({ path: "POST /api/delegations", key: "delegate:morning", html: "今朝の注文、そろえておいて", tag: "任せる", onReceipt: (r) => {
      S.phase = "working"; S.started = true; bump("reply");
      S.decisions.push({ t: hhmm(S.t), what: "入札と交渉を AI に任せた" });
      log("網谷さん", `今朝の注文、そろえておいて（任せる・${r.receipt}）`);
      setTalk([{ who: "me", html: "今朝の注文、そろえておいて", tag: `任せる・受領 ${r.receipt}`, tagCls: "t-go" },
        { who: "ai", html: `分かりました。${ic("shiro")}シロエビは${ic("bid")}入札、${ic("buri")}ブリは${ic("coop")}漁協と交渉します`, tag: "受け取りました", tagCls: "t-got" }]);
      log("AI", "分かりました。シロエビは入札、ブリは漁協と交渉します");
      later(900, () => setFocus(`<div class="pic">${ic("shiro")}<span class="op">→</span>${ic("bid")}<span class="w">入札</span><span class="op">・</span>${ic("buri")}<span class="op">→</span>${ic("coop")}<span class="w">漁協と交渉</span></div>
        <div class="ask" style="font-size:24px">任された仕事を進めています</div><div class="line">相談が要る時だけ、お呼びします。</div>`));
      lane("shiro", "シロエビ", "準備中", "work", "入札の値を決めています");
      lane("buri", "ブリ", "準備中", "work", "漁協の残りを確かめています");
      renderOrders(); renderKnows(["orders"]);
      $("replies").innerHTML = `<button class="card big" id="leave">${ic("slip", "av")}<span class="phrase">ほかの仕事へ<small>伝票の整理をする（呼ばれたら戻る）</small></span></button>`;
      $("leave").onclick = leave;
      S.tableMode = "gather"; renderTable();
      const c = CFG.shiro.close;
      at(1.5, shiroBid); at(3, buriAsk); at(6, buriReply); at(10, arrival);
      at(c, shiroOpen); at(c + 1.2, shiroReg1); at(c + 2.5, cAsk); at(c + 4, cReply); at(c + 5, cReg);
    } });
  }

  // ---- 2. other work while the AI works; the AI calls him back ----
  function leave() { S.away = true; renderAway(); log("網谷さん", "ほかの仕事へ（伝票の整理）"); }
  function renderAway() {
    if (!S.away) return;
    let s = ""; for (let i = 0; i < CFG.slips; i++) s += `<span class="sl ${i < S.slips ? "done" : ""}"></span>`;
    const call = S.phase === "ask";
    $("replies").innerHTML = `<div class="awayPanel">${ic("slip")}<div class="tx"><b>網谷さんは伝票の整理中</b><div class="slips">${s}<b>${S.slips}/${CFG.slips}枚</b></div></div>
      <div class="call">${call ? `<div class="awayDock call">${ic("ai")}AI から相談 1件：ブリの配分</div><button id="backBtn" class="ghost big">戻る</button>` : `<div class="awayDock">${ic("ai")}AI が入札と交渉を進めています</div>`}</div></div>`;
    if (call) $("backBtn").onclick = back;
  }
  function back() {
    S.away = false;
    log("網谷さん", `呼ばれて戻る（伝票 ${S.slips}枚 整理済み）`);
    if (S.phase === "ask") renderOptions();
    const f = $("focus").firstElementChild; if (f) { f.classList.remove("focus-in"); void f.offsetWidth; f.classList.add("focus-in"); }
  }

  // ---- negotiation arena: our AI and another organization's AI, each with its own conditions ----
  const ORG = { coop: { ic: "coop", name: "岩瀬漁協の AI", sub: "ブリを売る側" } };
  function arena(o) {
    $("focus").classList.add("arena-on"); setTalk([]); $("talk").style.visibility = "hidden";
    const row = (r) => `<div class="row ${r.cls || ""}">${r.ic ? ic(r.ic) : ""}<span class="k">${r.k}</span><span>${r.v}</span></div>`;
    const g = ORG[o.org];
    $("focus").innerHTML = `<div class="arena">
      <div class="party ours" id="pOurs"><div class="who">${ic("ai")}<div><b>網谷水産の AI</b><small>ブリを買う側</small></div></div>${o.human ? `<div class="human">${ic("human")}<span>${o.human}</span></div>` : ""}${o.ours.map(row).join("")}</div>
      <div class="channel" id="chan"><div class="ttl">${o.title}</div></div>
      <div class="party theirs" id="pTheirs"><div class="who">${ic(g.ic)}<div><b>${g.name}</b><small>${g.sub}</small></div></div>${o.theirs.map(row).join("")}<div class="row private">非公開：${o.priv}</div></div></div>`;
  }
  function closeArena() { $("focus").classList.remove("arena-on"); $("talk").style.visibility = ""; }
  // a proposal travels to the other organization; the answer travels back
  function say(kind, text, why, who, s) {
    const chan = $("chan"); if (!chan) return;
    const from = { out: $("pOurs"), in: $("pTheirs") }[kind], to = { out: $("pTheirs"), in: $("pOurs") }[kind];
    const show = () => {
      if (!$("chan")) return;
      chan.insertAdjacentHTML("beforeend", `<div class="cmsg ${kind}"><div class="who">${who}</div><div class="t">${text}${src(s)}</div>${why ? `<div class="why">${why}</div>` : ""}</div>`);
      const ms = chan.querySelectorAll(".cmsg"); if (ms.length > 3) ms[0].remove();
      if (to) { to.classList.remove("pulse"); void to.offsetWidth; to.classList.add("pulse"); }
    };
    if (!from) { show(); return; }
    const a = rel(from), b = rel(to);
    const env = document.createElement("div"); env.className = "envelope " + (kind === "in" ? "in" : "");
    Object.assign(env.style, { left: a.x + a.w / 2 - 17 + "px", top: a.y + 70 + "px" });
    stage.appendChild(env);
    requestAnimationFrame(() => requestAnimationFrame(() => Object.assign(env.style, { left: b.x + b.w / 2 - 17 + "px", top: b.y + 70 + "px" })));
    later(650, () => { env.remove(); show(); });
  }
  function result(html) { const chan = $("chan"); if (chan) chan.insertAdjacentHTML("beforeend", `<div class="result">${html}</div>`); }
  const coopRows = (extra = []) => [
    { ic: "buri", k: "持ち", v: "<b>ブリ 6本</b>（セリの残り）" },
    { k: "条件", v: "まとめて買えば値を下げる" },
    { k: "仮押さえ", v: `答えから ${CFG.holdMin}分` }, ...extra];
  const COOP_PRIV = "最低の値（岩瀬漁協の AI だけが持つ）";

  function shiroBid() {
    bump("ai"); log("AI → 市場", `シロエビ 入札 ${yen(CFG.shiro.bid)} × ${CFG.shiro.qty}箱（締め ${hhmm(CFG.shiro.close)}）`);
    lane("shiro", "シロエビ", "入札済み", "work", `<span class="from">AI → 市場</span>${yen(CFG.shiro.bid)} × 5箱・締め ${hhmm(CFG.shiro.close)}`);
  }
  function buriAsk() {
    bump("ai"); log("網谷水産の AI → 岩瀬漁協の AI", "ブリ 4本、1本 18,000円でどうですか");
    S.buri = "gather"; renderOrders();
    lane("buri", "ブリ", "交渉中", "work", `<span class="org">${ic("coop")}岩瀬漁協</span>4本・18,000円で問い合わせ`);
    arena({ title: "<b>ブリ</b>の問い合わせ", org: "coop",
      ours: [{ ic: "super", k: "要る数", v: "<b>4本</b>（スーパー）" }, { k: "上限", v: `<b>${yen(ceiling("super"))}</b>／本` }],
      theirs: coopRows(), priv: COOP_PRIV });
    later(400, () => say("out", "4本、1本 18,000円でどうですか", `上限 ${yen(ceiling("super"))}の内で、希望の値を添える`, "網谷水産の AI → 岩瀬漁協の AI", "ai"));
    makeTokens(); renderTable();
    later(60, () => S.tokens.forEach((tk) => tk.el.classList.remove("off")));
  }
  function buriReply() {
    log("岩瀬漁協の AI → 網谷水産の AI", `4本なら 1本 ${yen(CFG.ask4)}`);
    lane("buri", "ブリ", "合意の手前", "work", `<span class="org">${ic("coop")}岩瀬漁協</span>4本なら ${yen(CFG.ask4)}`);
    say("in", `4本なら 1本 ${yen(CFG.ask4)}`, "18,000円では受けない（漁協の値付け）", "岩瀬漁協の AI → 網谷水産の AI", "script");
    later(1500, () => say("chk", `上限 ${yen(ceiling("super"))}の内 → 合意の手前で待つ`, "", "網谷水産のコードの確認", "calc"));
    renderKnows(["price"]);
    assign({ super: 4, ryotei: 0 }, "work"); placeTokens();
  }
  function arrival() {
    S.ryotei = true; S.buri = "hold";
    log("出来事", "料亭からブリ 6本の追加注文（1本 24,000円）");
    renderOrders(true); renderKnows(["orders"]);
    lane("buri", "ブリ", "止めています", "pause", "料亭の追加で、網谷さんに相談");
    result("止めています：料亭から 6本の追加");
    S.tableMode = "conflict"; renderTable();
    later(900, () => sand([document.querySelector(".order.ryo"), $("lane-buri"), $("table")], $("focus"), askAlloc));
  }

  // ---- 3. the AI asks; the person's reply is a preview until he sends it ----
  function askAlloc() {
    S.phase = "ask";
    closeArena();
    setFocus(`<div class="pic">${ic("super")}<span class="n">4</span><span class="w">そろえ中</span><span class="op">＋</span>${ic("ryotei")}<span class="n">＋6</span><span class="w">追加</span><span class="op">→</span>${ic("buri")}<span class="w">残り</span><span class="n">6</span><span class="w">本</span></div>
      <div class="line">スーパー分 4本をそろえている所へ、料亭から 6本の追加です。残りは 6本で、4本足りません。</div>
      <div class="ask">いつものスーパー分は守りますか？${src("ai")}</div>
      <div class="why">${ic("human")}得意先の優先は網谷さんが決めることなので、お聞きします<button id="whyBtn">根拠を開く</button></div>`, "ask-mode");
    $("whyBtn").onclick = () => openDetail("compare");
    setTalk([]);
    renderScope("prio"); renderKnows(["orders", "stock", "memo"]);
    if (S.away) renderAway(); else renderOptions();
    log("AI", "スーパー分 4本をそろえている所へ料亭から 6本の追加。残りは 6本。いつものスーパー分は守りますか");
  }
  function echoText(o) {
    const a = o.alloc, sh = o.short;
    const head = o.key === REC ? "" : "おすすめと違いますが、";
    if (o.key === "skip") return `${head}スーパー 4本だけですね。料亭の 6本はお断りします`;
    if (a.super === 0) return `${head}料亭に 6本ですね。スーパーの 4本はそろいません`;
    const parts = [];
    if (sh.super) parts.push(`スーパーに ${sh.super}本`);
    if (sh.ryotei) parts.push(`料亭に ${sh.ryotei}本`);
    return `${head}スーパー ${a.super}本、料亭 ${a.ryotei}本ですね。${parts.join("、")}足りません`;
  }
  function preview(key) {
    if (S.phase !== "ask" || S.sending || S.away) return;
    const o = OPTS.find((x) => x.key === key);
    S.preview = o;
    assign(o.alloc, "draft");
    renderTable(); renderOptions();
    setTalk([{ who: "me", html: o.phrase, draft: true, tag: "下見（まだ送っていません）" }]);
    later(450, () => {
      if (S.preview !== o || S.phase !== "ask" || S.sending) return;
      setTalk([{ who: "me", html: o.phrase, draft: true, tag: "下見（まだ送っていません）" },
        { who: "ai", html: `${echoText(o)}。粗利の見込み <b class="yen">${yen(o.margin)}</b>`, draft: true, tag: "下見" }]);
    });
  }
  function instruct() {
    if (S.phase !== "ask" || !S.preview) return;
    const o = S.preview;
    sendInstruction({ path: "POST /api/questions/q-alloc/answer", key: "q-alloc:v1", html: `${o.phrase}。この配分で交渉し直して`, tag: "指示", onReceipt: (r) => {
      S.phase = "nego"; S.told = o; S.buri = "nego"; bump("reply");
      S.decisions.push({ t: hhmm(S.t), what: o.key === REC ? `${o.intent}（AI のおすすめどおり）` : `${o.intent}（AI のおすすめはスーパーを守る）` });
      log("網谷さん", `${o.phrase}。この配分で交渉し直して（スーパー ${o.alloc.super}・料亭 ${o.alloc.ryotei}・${r.receipt}）`);
      setTalk([{ who: "me", html: `${o.phrase}。この配分で交渉し直して`, tag: `指示・受領 ${r.receipt}`, tagCls: "t-go" },
        { who: "ai", html: `受け取りました。${o.key === REC ? "" : `今回は${o.intent}ですね。`}${ic("super")}${o.alloc.super}本・${ic("ryotei")}${o.alloc.ryotei}本で、${ic("coop")}漁協と交渉し直します`, tag: "受け取りました", tagCls: "t-got" }]);
      [...$("replies").children].forEach((c) => c.classList.add("folded"));
      renderScope();
      retire(() => {
        // the person's reply becomes the condition our AI carries to the cooperative
        renderOrders(); setTokCls("nego"); renderTable();
        const six = o.qty === 6, unit = six ? CFG.final6 : CFG.ask4;
        lane("buri", "ブリ", "交渉し直し中", "work", `<span class="org">${ic("coop")}岩瀬漁協</span>${o.qty}本・上限 ${yen(o.limit)}`);
        arena({ title: "<b>ブリ</b>の交渉し直し", org: "coop", human: `網谷さんの返事 ${r.receipt}：${o.phrase}`,
          ours: [{ ic: "super", k: "配分", v: `<b>${o.alloc.super}本</b>　${ic("ryotei")}<b>${o.alloc.ryotei}本</b>` },
            { k: "要る数", v: six ? `<s>4本</s> → <b>${o.qty}本</b>` : `<b>${o.qty}本</b>（変わらず）`, cls: six ? "new" : "" },
            { k: "上限", v: o.limit !== ceiling("super") ? `<s>${yen(ceiling("super"))}</s> → <b>${yen(o.limit)}</b>` : `<b>${yen(o.limit)}</b>`, cls: o.limit !== ceiling("super") ? "new" : "" }],
          theirs: coopRows([{ k: "前の答え", v: `4本 ${yen(CFG.ask4)}` }]), priv: COOP_PRIV });
        $("replies").innerHTML = `<div class="quiet">${ic("ai")}網谷水産の AI が、網谷さんの返事を岩瀬漁協の AI へ伝えています</div>`;
        at(0.6, () => { bump("ai"); log("網谷水産の AI → 岩瀬漁協の AI", six ? `${o.qty}本まとめて 1本 ${yen(CFG.offer6)}で` : `先ほどの 4本 ${yen(CFG.ask4)}で`);
          say("out", six ? `${o.qty}本まとめて、1本 ${yen(CFG.offer6)}で` : `先ほどの 4本、1本 ${yen(CFG.ask4)}で`,
            six ? `網谷さんの返事で 4本 → 6本。上限 ${yen(o.limit)}の内で、見込み ${yen(CFG.expect6)}より下から` : "網谷さんの返事で料亭は断る。数は変わらない", "網谷水産の AI → 岩瀬漁協の AI", "ai"); });
        at(2.2, () => { bump("ai"); const until = hhmm(S.t + CFG.holdMin);
          log("岩瀬漁協の AI → 網谷水産の AI", `${o.qty}本なら ${yen(unit)}・${until} まで仮押さえ`);
          say("in", six ? `6本なら 1本 ${yen(unit)}。${until} まで仮押さえ` : `4本 ${yen(unit)}で承知。${until} まで仮押さえ`,
            six ? `4本の ${yen(CFG.ask4)}から、まとめ買いで ${yen(CFG.ask4 - CFG.final6)}下げた` : "前の答えのまま", "岩瀬漁協の AI → 網谷水産の AI", "script");
          lane("buri", "ブリ", "交渉し直し中", "work", `<span class="org">${ic("coop")}岩瀬漁協</span>${o.qty}本なら ${yen(unit)}`); });
        at(3.6, () => say("chk", `上限 ${yen(o.limit)}の内 → 網谷さんの承認へ`, "", "網谷水産のコードの確認", "calc"));
        at(4.6, () => result(`合意（承認の前）${o.qty}本 × ${yen(unit)}`));
        at(6.0, () => agree(unit));
      });
    } });
  }

  // ---- 4. agreement, the person's approval, then the official registration ----
  function agree(unit) {
    const o = S.told;
    closeArena();
    if (unit > o.limit) { lane("buri", "ブリ", "止めています", "pause", "上限をこえる答え・合意しない"); return; }
    S.deal = { unit, qty: o.qty, amount: unit * o.qty, margin: margin(o.alloc, unit) };
    S.buri = "agreed"; S.phase = "approve";
    log("AI", `合意（承認の前）：ブリ ${o.qty}本 × ${yen(unit)} = ${yen(S.deal.amount)}・粗利 ${yen(S.deal.margin)}`);
    setTokCls("agreed"); renderTable(); renderOrders();
    lane("buri", "ブリ", "承認待ち", "pause", `${o.qty}本 × ${yen(unit)}・網谷さんの承認待ち`);
    setFocus(`<div class="pic">${ic("deal")}<span class="w">漁協と合意</span><span class="op">→</span>${ic("stamp")}<span class="w">網谷さんの承認</span><span class="op">→</span>${ic("reg")}<span class="w">正式登録</span></div>
      <div class="ask" style="font-size:26px">合意しました。承認しますか？</div>
      <div class="line">スーパー ${o.alloc.super}本・料亭 ${o.alloc.ryotei}本、${o.qty}本 × ${yen(unit)} = ${yen(S.deal.amount)}。承認の後に市場の取引帳簿へ登録します</div>`, "ask-mode");
    setTalk([]);
    renderScope("approve");
    $("replies").innerHTML = `<div class="deal">${ic("deal", "big")}<div><div class="k">合意（承認の前・まだ登録していません）</div><div class="v">${ic("buri")}${o.qty}本 × ${yen(unit)} = ${yen(S.deal.amount)}</div></div>
      <div class="r"><div class="k">粗利 ${src("calc")}</div><div class="v">${yen(S.deal.margin)}</div></div></div><button class="go" id="approve">${ic("stamp")}<span>承認する</span></button>`;
    $("approve").onclick = approve;
  }
  function approve() {
    if (S.phase !== "approve") return;
    const o = S.told;
    sendInstruction({ path: "POST /api/approvals", key: "approve:buri:v2", html: `承認します（${o.qty}本 × ${yen(S.deal.unit)}）`, tag: "承認", onReceipt: (r) => {
      S.phase = "registering"; bump("reply");
      S.decisions.push({ t: hhmm(S.t), what: `ブリ ${o.qty}本の取引を承認` });
      log("網谷さん", `承認（${r.receipt}）`);
      setTalk([{ who: "me", html: `承認します（${o.qty}本 × ${yen(S.deal.unit)}）`, tag: `承認・受領 ${r.receipt}`, tagCls: "t-go" },
        { who: "ai", html: `受け取りました。市場の取引帳簿に登録します`, tag: "受け取りました", tagCls: "t-got" }]);
      [...$("replies").children].forEach((c) => c.classList.add("folded"));
      renderScope();
      retire(() => {
        setFocus(`<div class="pic">${ic("reg")}<span class="w">市場の取引帳簿</span></div><div class="ask" style="font-size:24px">登録しています…</div>`);
        pending("buri", `${ic("buri")} ブリ ${o.qty}本を登録しています…`);
        later(1200, () => {
          S.buriDone = true; S.buri = "done";
          settle("buri", "buri", `ブリ ${o.qty}本（${o.alloc.super ? `スーパー ${o.alloc.super}・` : ""}料亭 ${o.alloc.ryotei}）`, "網谷さんの承認で登録", S.deal.amount, 0);
          setTokCls("reg"); renderTable();
          lane("buri", "ブリ", "登録済み", "done", `${o.qty}本 × ${yen(S.deal.unit)}`);
          setFocus(`<div class="pic">${ic("reg")}<span class="w">正式に登録しました</span></div><div class="ask" style="font-size:24px">今朝の仕事をまとめます</div>`);
          later(1500, whenDone);
        });
      });
    } });
  }

  // shiroebi keeps going inside the plan it was trusted with
  function shiroOpen() {
    log("市場 → AI", `シロエビ 開札：${CFG.shiro.won}箱 落札（${yen(CFG.shiro.bid)}）`);
    lane("shiro", "シロエビ", "登録中", "work", `<span class="from">市場 → AI</span>${CFG.shiro.won}箱 落札・登録しています`);
    pending("shiro1", `${ic("shiro")} シロエビ ${CFG.shiro.won}箱を登録しています…`);
  }
  function shiroReg1() { settle("shiro1", "shiro", `シロエビ ${CFG.shiro.won}箱`, "入札の結果（市場の確定）", CFG.shiro.won * CFG.shiro.bid, CFG.shiro.won); }
  function cAsk() {
    bump("ai"); log("AI → C社", `残りの ${CFG.shiro.cQty}箱を ${yen(CFG.shiro.cPrice)}で`);
    lane("shiro", "シロエビ", "交渉中", "work", `<span class="org">${ic("csha")}C社（仲買）</span>${CFG.shiro.cQty}箱を ${yen(CFG.shiro.cPrice)}で持ちかけ`);
  }
  function cReply() {
    log("C社 → AI", "承知しました。計画の範囲の内（1箱 4,800円・8箱まで）なので AI が登録");
    lane("shiro", "シロエビ", "登録中", "work", `<span class="org">${ic("csha")}C社（仲買）</span>成立・範囲の内なので登録`);
    pending("shiro2", `${ic("shiro")} シロエビ ${CFG.shiro.cQty}箱（C社）を登録しています…`);
  }
  function cReg() { settle("shiro2", "shiro", `シロエビ ${CFG.shiro.cQty}箱（C社）`, "計画の範囲の内で AI が登録", CFG.shiro.cQty * CFG.shiro.cPrice, CFG.shiro.cQty); }
  function pending(id, html) {
    const box = $("settled"); if (box.querySelector(".empty")) box.innerHTML = "";
    box.insertAdjacentHTML("beforeend", `<div class="pending" id="p-${id}">${html}</div>`);
  }
  // registration is confirmed first; only then the card moves to the right
  function settle(id, k, title, how, amount, qty) {
    const p = $("p-" + id); if (p) p.remove();
    bump("reg"); if (k === "shiro") bump("ai");
    log("市場の取引帳簿", `正式登録：${title} ${yen(amount)}`);
    $("settled").insertAdjacentHTML("beforeend", `<div class="reg land">${ic(k)}<div class="t"><b>${title}</b><small>${how}</small></div><div class="amt">${yen(amount)}</div>${src("api")}</div>`);
    if (k === "shiro") {
      S.shiroGot += qty; S.shiroDone = S.shiroGot >= CFG.shiro.qty;
      if (S.shiroDone) lane("shiro", "シロエビ", "そろいました", "done", `5箱・正式登録 2件`);
    }
    renderOrders();
    if (k === "shiro" && S.phase === "registered") whenDone();
  }

  // ---- 5. back to the person: what he handed over, what he decided, what else he got done ----
  function whenDone() {
    if (S.phase === "registering") S.phase = "registered";
    if (!S.buriDone || !S.shiroDone || S.phase === "summary") return;
    S.phase = "summary";
    const o = S.told;
    const shortTxt = [o.short.super ? `スーパー ${o.short.super}本` : "", o.short.ryotei ? `料亭 ${o.short.ryotei}本` : ""].filter(Boolean).join("・");
    const shiroYen = CFG.shiro.won * CFG.shiro.bid + CFG.shiro.cQty * CFG.shiro.cPrice;
    $("summary").innerHTML = `<div class="sumHead">${ic("human")}<b>網谷さんの今朝（05:50〜${hhmm(S.t)}）</b></div>
      <div class="cols">
        <div class="col"><h3>${ic("ai")}AI に任せたこと</h3>
          <div class="it">${ic("shiro")}<span>シロエビ 5箱：入札と C社との交渉、登録 2件</span><b class="yen">${yen(shiroYen)}</b></div>
          <div class="it">${ic("buri")}<span>ブリ ${o.qty}本：漁協との交渉と合意</span><b class="yen">${yen(S.deal.amount)}</b></div>
          <div class="it">${ic("ai")}<span>AI のやり取り</span><b>${S.tally.ai}回</b></div></div>
        <div class="col me"><h3>${ic("human")}網谷さんが決めたこと（${S.decisions.length}回）</h3>
          ${S.decisions.map((d) => `<div class="it"><span class="tm">${d.t}</span><span>${d.what}</span></div>`).join("")}</div>
        <div class="col"><h3>${ic("slip")}その間にできたこと</h3>
          <div class="it">${ic("slip")}<span>伝票の整理</span><b>${S.slips}枚</b></div>
          ${shortTxt ? `<div class="it warn"><span>残っていること：${shortTxt}の不足（網谷さんの判断の結果）</span></div>` : ""}</div>
      </div>`;
    $("summary").hidden = false; $("tokens").style.visibility = "hidden";
    log("まとめ", `任せた 2件・決めた ${S.decisions.length}回・伝票 ${S.slips}枚`);
  }

  // ---- sand: scattered signals gather into the question ----
  function sand(fromEls, toEl, done) {
    const cv = $("sand"), cx = cv.getContext("2d"), to = rel(toEl), parts = [];
    fromEls.filter(Boolean).forEach((el) => {
      const r = rel(el);
      for (let i = 0; i < 70; i++) parts.push({ x: r.x + Math.random() * r.w, y: r.y + Math.random() * r.h,
        tx: to.x + 110 + Math.random() * (to.w - 160), ty: to.y + 20 + Math.random() * (to.h - 40), d: Math.random() * 0.25 });
    });
    const t0 = performance.now(), dur = 950 / Math.max(S.speed, 1);
    let finished = false;
    const end = () => { if (finished) return; finished = true; cx.clearRect(0, 0, 1600, 900); done(); };
    (function frame(now) {
      const k = Math.min(1, (now - t0) / dur);
      cx.clearRect(0, 0, 1600, 900);
      for (const p of parts) {
        const q = Math.max(0, Math.min(1, (k - p.d) / (1 - p.d))); const e = 1 - Math.pow(1 - q, 3);
        cx.fillStyle = `rgba(29,102,112,${0.25 + 0.6 * (1 - q)})`;
        cx.fillRect(p.x + (p.tx - p.x) * e, p.y + (p.ty - p.y) * e, 3, 3);
      }
      if (k < 1) requestAnimationFrame(frame); else end();
    })(t0);
    setTimeout(end, dur + 400);
  }

  // ---- the person's hand: every press shows where and by whom ----
  const handOn = params.get("hand") === "1";
  if (params.get("note")) $("marks").insertAdjacentHTML("afterend", `<div class="demo-note">この動画の操作は台本どおりに再現しています（実演では人が押します）。AI と相手の返事は台本・数字はコードの計算</div>`);
  if (handOn) $("marks").insertAdjacentHTML("beforeend", `<div id="hand">${ic("hand")}</div>`);
  const toStage = (e) => { const s = stage.getBoundingClientRect(); return { x: (e.clientX - s.left) / S.scale, y: (e.clientY - s.top) / S.scale }; };
  addEventListener("pointermove", (e) => { if (!handOn) return; const p = toStage(e); Object.assign($("hand").style, { left: p.x + "px", top: p.y + "px" }); });
  addEventListener("pointerdown", (e) => {
    const p = toStage(e);
    const m = document.createElement("div"); m.className = "ripple"; m.style.left = p.x + "px"; m.style.top = p.y + "px"; m.innerHTML = "<span>網谷さん</span>";
    $("marks").appendChild(m); setTimeout(() => m.remove(), 1400);
  });

  // ---- detail, history, legend ----
  function openDetail(which) {
    const d = $("detail");
    const rows = OPTS.map((o) => `<tr><td>${o.phrase}${o.key === REC ? "（AI のおすすめ）" : ""}</td><td>スーパー ${o.alloc.super}・料亭 ${o.alloc.ryotei}</td><td class="num">${yen(o.unit)}</td><td class="num">${yen(o.limit)}</td><td class="num">${yen(o.margin)}</td><td>${[o.short.super ? `スーパー ${o.short.super}本` : "", o.short.ryotei ? `料亭 ${o.short.ryotei}本` : ""].filter(Boolean).join("・") || "なし"}</td></tr>`).join("");
    const head = { compare: "なぜお聞きするか", orders: "AI が見ている注文", stock: "漁協の残り", price: "売値と上限", memo: "過去の経験（Vault）" }[which];
    d.innerHTML = `<button class="ghost small close" id="dClose">閉じる</button><h3>${head}</h3>
      <p>粗利は「料亭を優先」が最も大きくなります。どの得意先を優先するかは任せた範囲の外なので、網谷さんに戻しました。</p>
      <table><tr><th>返事</th><th>配分</th><th>単価</th><th>交渉の上限</th><th>粗利</th><th>足りない先</th></tr>${rows}</table>
      <p style="font-size:13px;color:var(--mute)">上限＝売値 − 費用 − 利益：スーパー ${yen(ceiling("super"))}・料亭 ${yen(ceiling("ryotei"))}。まとめて買う時は、配る先の低い方の上限。粗利・不足・上限はコードの計算です。</p>
      <div class="vault">${ic("memo")}<div><b>過去の経験</b>：去年 11月、スーパーでブリが欠品した翌週、売り場を半分にされた（網谷水産の記録・模擬）</div></div>`;
    d.hidden = false; $("dClose").onclick = () => (d.hidden = true);
  }
  $("historyBtn").onclick = () => {
    const h = $("history");
    h.innerHTML = `<button class="ghost small close" id="hClose">閉じる</button><h3>これまでのやり取り</h3><ol>${S.hist.map((x) => `<li>${x.t} <span class="who">${x.who}</span>：${x.text}</li>`).join("")}</ol>
      <h3>サーバーの記録（模擬）</h3><ol>${S.api.map((x) => `<li>${x.t} ${x.path}（キー ${x.key}）：${x.result}</li>`).join("") || "<li>まだありません</li>"}</ol>`;
    h.hidden = false; $("hClose").onclick = () => (h.hidden = true);
  };
  $("srcBtn").onclick = () => {
    const on = document.body.classList.toggle("showsrc");
    $("srcBtn").classList.toggle("on", on);
    $("legend").hidden = !on;
    $("legend").innerHTML = `<b>この試作の仕組み</b>${Object.keys(SRC).map((k) => `<span class="srcb s-${k}">${SRC[k]}</span>`).join("")}<span class="lg">相手の返事と時刻は台本。AI の言葉は本番では Claude が決める。数量・粗利・上限はコード。受領と登録は本番では正式 API</span>`;
  };

  // ---- clock ----
  document.querySelectorAll("[data-speed]").forEach((b) => (b.onclick = () => {
    S.speed = Number(b.dataset.speed);
    document.querySelectorAll("[data-speed]").forEach((x) => x.classList.toggle("on", x === b));
  }));
  let last = performance.now();
  setInterval(() => {
    const now = performance.now(), dt = (now - last) / 1000; last = now;
    if (!S.started || !S.speed) return;
    const before = S.t;
    S.t += dt * S.speed;
    $("clock").textContent = hhmm(S.t);
    if (S.away && Math.floor(S.t) > Math.floor(before) && S.slips < CFG.slips) { S.slips++; renderAway(); }
    S.agenda.sort((a, b) => a.at - b.at);
    while (S.agenda.length && S.agenda[0].at <= S.t) S.agenda.shift().fn();
  }, 50);

  function fit() {
    S.scale = Math.min(innerWidth / 1600, innerHeight / 900);
    stage.style.transform = `scale(${S.scale})`;
    placeTokens();
  }
  addEventListener("resize", fit);
  addEventListener("error", () => S.errors++);
  window.__S = S;
  window.__server = Server;

  renderOrders(); renderTally(); renderKnows(); renderScope(); renderTable(); fit(); start();

  // ?auto=super|ryotei — self-check only (not the demo): presses the real buttons and reports in the title.
  const auto = params.get("auto");
  if (auto) {
    const pick = auto === "1" ? "super" : auto;
    const click = (sel) => { const el = document.querySelector(sel); if (el && !el.disabled) el.click(); return !!el; };
    const when = (cond, f, wait = 0) => { const iv = setInterval(() => { if (cond()) { clearInterval(iv); setTimeout(f, wait); } }, 100); };
    setTimeout(() => click("#delegate"), 1500);
    when(() => S.phase === "working" && $("leave"), () => click("#leave"), 1200);
    when(() => S.phase === "ask" && S.away, () => click("#backBtn"), 1500);
    when(() => S.phase === "ask" && !S.away, () => {
      setTimeout(() => click(`.card[data-k="${pick === "super" ? "split" : "super"}"]`), 1500);
      setTimeout(() => click(`.card[data-k="${pick}"]`), 3500);
      setTimeout(() => click("#go"), 5500);
    });
    const retryIv = setInterval(() => { if ($("retry")) $("retry").click(); }, 1500);
    when(() => S.phase === "approve", () => click("#approve"), 1500);
    when(() => S.phase === "summary", () => {
      clearInterval(retryIv);
      document.title = `AUTO pick=${S.told && S.told.key} limit=${S.told && S.told.limit} deal=${S.deal && S.deal.amount} margin=${S.deal && S.deal.margin} decisions=${S.decisions.length} executed=${S.executed.size} serverRecords=${Server.rec.size} reg=${S.tally.reg} slips=${S.slips} errors=${S.errors}`;
    }, 1500);
  }
})();
