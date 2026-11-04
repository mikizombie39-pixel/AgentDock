// Agent Dock: the companion screen. Everything comes from one value per moment (GET /api/mate): the work it is about, the stance,
// the execution state, what the companion says (with where each sentence came from), what the person can do, and the reason card.
// The figure, the words and the buttons are drawn from that same value, so they always point at the same situation.
// The morning (before the person delegates) is built from GET /api/morning: today's work, ordered by the code's rule.
// Amounts, quantities and times never count up: only final values are shown.
(function () {
  "use strict";
  const OWNER = "amiya";
  const Q = new URLSearchParams(location.search);
  const ITEM = Q.get("item") || "buri_xl";
  const ROLE = Q.get("role") === "president" ? "president" : "staff";
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const icon = (n) => `<span class="ic">${(window.DOCK_ICONS || {})[n] || ""}</span>`;
  const hm = (s) => (s ? String(s).slice(11, 16) : "");
  const key = () => (crypto.randomUUID && crypto.randomUUID()) || String(Date.now()) + Math.random();
  document.querySelectorAll("[data-ic]").forEach((e) => { e.innerHTML = (window.DOCK_ICONS || {})[e.dataset.ic] || ""; });

  const EXEC_ICON = { before_start: "message-circle", running: "loader", your_turn: "hand", president: "hourglass", stopped: "octagon-x", unconnected: "octagon-pause",
    registered: "circle-check", idle: "eye" };
  const STANCE_ICON = { bold: "arrow-up-right", struggle: "arrow-up-right", careful: "hand", sorry: "arrow-right", relief: "circle-check" };

  let V = null, M = null, phase = "intro", fig = null, mini = null, popFor = null;
  const seenNet = new Set(); let netReady = false;
  let waitReceipt = null, ownAt = null;   // {prev, t}: the person acted; the ✓ waits for the server's record

  if (ROLE === "president") { $("rolebar").hidden = false; $("rolebar").textContent = "社長（網谷さん）の画面・別の浜から。担当者と AI から届いた確認に答えます"; }
  $("evlink").href = `dock.html?view=evidence&item=${encodeURIComponent(ITEM)}`;

  async function getJSON(u) { const r = await fetch(u); return r.json(); }
  async function post(path, body) {
    const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.assign({ owner: OWNER, idempotency_key: key() }, body)) });
    let j = {}; try { j = await r.json(); } catch (e) { /* empty */ }
    return { ok: r.ok, status: r.status, j };
  }

  // ── the loop: one value per second ──
  async function tick() {
    try {
      V = await getJSON(`/api/mate?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`);
      if (V.exec === "before_start" && ROLE === "staff") await drawMorning(); else drawTalk();
    } catch (e) { /* keep the last view */ }
    setTimeout(tick, 1000);
  }

  // ═════ the morning ═════
  async function drawMorning() {
    $("talk").hidden = true; $("morning").hidden = false; $("flowbtn").hidden = true;
    if (!M) { M = await getJSON(`/api/morning?owner=${OWNER}`); flowOnce(); }
    const key2 = phase;
    if ($("morning").dataset.phase === key2) return;
    $("morning").dataset.phase = key2;
    if (phase === "intro") { drawJobs(); drawGreeting(); }
    if (phase === "check") { drawCheck(); }
    if (phase === "delegate") { drawSideOrders(); drawPropose(); }
  }

  // 朝の見通し：注文を確認 → 買い付け → 振分け → 出荷（流れが走る絵で一度だけ）
  function flowOnce() {
    const steps = M.steps;
    $("flowline").innerHTML = steps.map((s, i) => `<span class="node" data-i="${i}">${esc(s)}</span>` + (i < steps.length - 1 ? `<span class="seg"><i></i></span>` : "")).join("") +
      `<span class="lbl">今朝の見通し</span>`;
    const nodes = [...$("flowline").querySelectorAll(".node")], segs = [...$("flowline").querySelectorAll(".seg i")];
    if (REDUCED) { nodes.forEach((n) => n.classList.add("lit")); segs.forEach((s) => (s.style.width = "100%")); return; }
    nodes.forEach((n, i) => setTimeout(() => {
      n.classList.add("lit");
      if (segs[i]) segs[i].animate([{ width: "0%" }, { width: "100%" }], { duration: 420, fill: "forwards", easing: "ease-in-out" });
      if (i > 0) setTimeout(() => nodes[i - 1].classList.remove("lit"), 300);
    }, 300 + i * 520));
    setTimeout(() => { nodes.forEach((n) => n.classList.remove("lit")); nodes[0].classList.add("lit"); }, 300 + steps.length * 520 + 400);
  }

  // 今日の仕事：何もない所から、一つずつ現れる（砂が集まる）
  function drawJobs() {
    const side = $("mside");
    side.innerHTML = `<h3>今日の仕事（手の要る順）</h3>` + M.jobs.map((j) => `<div class="job ${j.done ? "done" : ""}" data-k="${esc(j.key)}">
      <span class="step">${esc(j.step)}</span><span class="who2 ${j.who}">${esc(j.done ? "済み" : j.who_label)}</span><span class="ti">${esc(j.title)}</span>
      <span class="rs">${esc(j.reason)}${j.deadline ? "・締切 " + hm(j.deadline) : ""}${j.connected ? "" : '　<b class="nc">未接続</b>'}</span></div>`).join("");
    const rows = [...side.querySelectorAll(".job")];
    rows.forEach((r, i) => setTimeout(() => sand(r), REDUCED ? 0 : 600 + i * 520));
  }
  function sand(el) {
    if (REDUCED) { el.classList.add("in"); return; }
    const card = $("card"), cr = card.getBoundingClientRect(), r = el.getBoundingClientRect();
    const n = 46, grains = [];
    for (let k = 0; k < n; k++) {
      const g = document.createElement("i");
      g.className = "confetti";
      g.style.width = g.style.height = (2 + Math.random() * 2.5) + "px";
      g.style.background = ["#C9B48A", "#B9A57C", "#D8C9A6", "#A99467"][k % 4];
      const sx = r.left - cr.left + Math.random() * r.width + (Math.random() - .5) * 160, sy = r.top - cr.top + r.height + 40 + Math.random() * 120;
      const tx = r.left - cr.left + 6 + Math.random() * (r.width - 12), ty = r.top - cr.top + 4 + Math.random() * (r.height - 8);
      g.style.left = "0px"; g.style.top = "0px";
      card.appendChild(g); grains.push(g);
      g.animate([{ transform: `translate(${sx}px,${sy}px)`, opacity: 0 }, { opacity: .95, offset: .25 },
        { transform: `translate(${tx}px,${ty}px)`, opacity: .95, offset: .8 }, { transform: `translate(${tx}px,${ty}px)`, opacity: 0 }],
        { duration: 1000 + Math.random() * 300, easing: "cubic-bezier(.3,.7,.3,1)" });
    }
    setTimeout(() => el.classList.add("in"), 760);
    setTimeout(() => grains.forEach((g) => g.remove()), 1400);
  }

  function miniAgent() {
    return `<div class="agent"><div class="mini" id="mini"></div><span>あなたの仲間・網谷水産のAI</span></div>`;
  }
  function mountMini() { const h = $("mini"); if (h) { mini = window.MateFigure.create(h); mini.set(null); } }

  function drawGreeting() {
    $("mmain").innerHTML = miniAgent() + `<div class="propose"><div class="k">${icon("message-circle")}今朝の始まり</div>
      <h2>${esc(V.headline.text)}</h2>
      ${V.sub.map((s) => `<p>${esc(s.text)}</p>`).join("")}
      <div class="btnrow" style="margin-top:20px"><button class="pbtn" id="go-check">${icon("clipboard-list")}一緒に確認する</button></div></div>`;
    mountMini();
    $("go-check").onclick = () => { phase = "check"; drawMorning(); };
    $("youcan").hidden = true;
  }

  function drawCheck() {
    const c = M.check;
    $("mmain").innerHTML = miniAgent() + `<div class="checklist"><div class="k" style="color:var(--teal);font-size:14px">${icon("clipboard-list")}一緒に確かめます</div>
      <h2>今朝の注文と、社長の指示です</h2>
      ${c.orders.map((o) => `<div class="crow">${icon(o.tender ? "package" : "fish")}<span>${esc(o.shop)} · ${esc(o.item)} ${o.quantity}${esc(o.unit)}</span>
        <small>${o.tender ? (o.closes_at ? "入札の締切 " + hm(o.closes_at) : '入札の受付は<b style="color:var(--stop)">未接続</b>') : "これから買い付け"}</small></div>`).join("")}
      ${c.directives.map((d) => `<div class="crow dir">${icon("user-round")}<span>社長の指示：「${esc(d.body)}」</span><small>${esc(d.issued_from)}から・${esc(d.via)}・${hm(d.received_at)}</small></div>`).join("")}
      <div class="btnrow" style="margin-top:18px"><button class="pbtn" id="go-ok">${icon("circle-check")}確かめました</button>
        <button class="sbtn" id="go-back">${icon("list-checks")}今日の仕事に戻る</button></div></div>`;
    mountMini();
    $("go-ok").onclick = () => { phase = "delegate"; drawMorning(); };
    $("go-back").onclick = () => { phase = "intro"; $("morning").dataset.phase = ""; drawMorning(); };
  }

  function drawSideOrders() {
    const c = M.check;
    $("mside").innerHTML = `<h3>今朝の注文</h3>` + c.orders.map((o) => `<div class="ocard ${o.tender && !o.connected ? "off" : ""}"><div class="t">${icon(o.tender ? "package" : "shopping-cart")}${esc(o.shop)} · ${esc(o.item)} ${o.quantity}${esc(o.unit)}</div>
      <div class="s">${o.tender ? (o.closes_at ? "入札の締切 " + hm(o.closes_at) : '入札の受付：<b style="color:var(--stop)">未接続</b>') : "社長の指示：落とさない"}</div>
      <div class="s2">${o.tender ? "これから入札" : "これから買い付け"}</div></div>`).join("") +
      `<h3 style="margin-top:22px">任せる範囲</h3>
      <div class="scope">${icon("bot")}<div>AIに任せる<small>${c.scopes.map((s) => esc(s.label)).join("・")}</small></div></div>
      <div class="scope">${icon("user-round")}<div>社長（網谷さん）に確かめる<small>${c.ask_president.map(esc).join("・")}</small></div></div>`;
  }

  function drawPropose() {
    const c = M.check;
    const byShop = {};
    c.orders.forEach((o) => (byShop[o.shop] = byShop[o.shop] || []).push(`${o.item} ${o.quantity}${o.unit}`));
    const phrase = Object.entries(byShop).map(([s, xs]) => `${s}の${xs.join("と")}`).join("、");
    $("mmain").innerHTML = miniAgent() + `<div class="propose"><div class="k">${icon("message-circle")}まず、私に任せてください</div>
      <h2>買い付けは、私が進めましょうか？</h2>
      <p>今朝は、${esc(phrase)}ですね。</p><p>注文と採算を確認して、入札と漁協への照会を進めます。</p>
      <div class="chips">${c.orders.map((o) => `<span class="chip ${o.tender && !o.connected ? "nc" : ""}">${icon(o.tender ? "package" : "fish")}${esc(o.item)} → ${o.tender ? "条件内で入札" : "漁協に照会"}${o.tender && !o.connected ? "<small>（未接続）</small>" : ""}</span>`).join("")}</div>
      <div class="hint">${icon("hand")}この範囲で任せると、買い付けを始めます。</div>
      <div class="btnrow"><button class="pbtn" id="go-dele">${icon("arrow-up-right")}この範囲で任せる</button><button class="sbtn" id="go-cond">${icon("shield-check")}任せる条件を確認</button></div>
      <div id="dele-res"></div></div>`;
    mountMini();
    $("youcan").hidden = false;
    $("youcan-b").textContent = "あなたは、魚を見てきてください";
    $("youcan-s").textContent = "買い付けに張り付かず、必要な判断だけ引き受けます。";
    $("go-cond").onclick = () => openPop($("mini"), condCard());
    $("go-dele").onclick = async () => {
      $("go-dele").disabled = true;
      $("dele-res").innerHTML = `<div class="receipt pending">${icon("loader")}送っています…</div>`;
      const r = await post("/api/delegate", { scope: c.scopes.map((s) => s.code), checked: { orders: c.orders.map((o) => o.order_id), directives: c.directives.map((d) => d.directive_id) }, role: "staff", item: ITEM });
      if (r.ok) { waitReceipt = { prev: null, t: Date.now() }; $("dele-res").innerHTML = `<div class="receipt pending">${icon("loader")}サーバーが記録を確かめています…</div>`; }
      else { $("go-dele").disabled = false; $("dele-res").innerHTML = `<div class="receipt bad">${icon("circle-alert")}任せられませんでした：${esc(r.j.detail || r.j.error || r.status)}</div>`; }
    };
  }
  function condCard() {
    const c = M.check;
    return { lead: "任せる範囲と、社長に確かめることです。範囲の外は、私が相談します。", sections: [
      { title: "AI に任せる", rows: c.scopes.map((s) => ({ text: s.label, origin: "code" })) },
      { title: "社長（網谷さん）に確かめる", rows: c.ask_president.map((t) => ({ text: t, origin: "record" })) },
      { title: "あなたが決める", rows: [{ text: "購入の承認（権限の内）・配分", origin: "code" }] },
      { title: "社長の指示", rows: c.directives.map((d) => ({ text: `「${d.body}」（${d.issued_from}から・${d.via}）`, origin: "directive", at: d.received_at })) }],
      more: [], speech_origin: null, origins: { code: "コードの決まった文", record: "業務の記録", directive: "社長の指示（記録）" } };
  }

  // ═════ the conversation ═════
  function drawTalk() {
    $("morning").hidden = true; $("talk").hidden = false; $("flowbtn").hidden = false;
    if (!fig) { fig = window.MateFigure.create($("figure")); }
    // the stance: 残念 stays long enough to be seen before the companion turns to the next thing
    const want = V.stance || null;
    if (!(fig.stance === "sorry" && want !== "sorry" && fig.shownFor() < 4800)) fig.set(want);
    $("subject").textContent = V.subject;
    $("subjic").innerHTML = (window.DOCK_ICONS || {})[V.tender ? "package" : "fish"];
    const item = V.item.short || V.item.name;
    const tag = V.stance ? `${icon(STANCE_ICON[V.stance])}<span>${esc(item)}の買い付け：<b>${esc(V.stance_label)}</b> · ${esc(V.rule)}</span>`
      : `${icon(EXEC_ICON[V.exec] || "eye")}<span>${esc(item)}：<b>${esc(V.exec_label)}</b></span>`;
    setHTML("tag", tag);
    const isPres = ROLE === "president";
    const head = isPres ? (V.president ? "担当者と AI から、確認が届きました。" : "今、社長の判断を待つ仕事はありません。") : V.headline.text;
    if ($("headline").textContent !== head) {
      $("headline").textContent = head;
      if (!REDUCED) $("headline").animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 380, easing: "ease-out" });
    }
    let subs = isPres ? presSubs() : V.sub.map((s) => `<p class="${s.origin === "reply" ? "reply" : ""}">${esc(s.text)}</p>`).join("");
    if (V.tender) subs += `<div class="tenderrows">${V.tender.rows.map((r) => `<div><b>${esc(r.label)}</b><span class="${r.connected ? "" : "nc"}">${esc(r.text)}</span></div>`).join("")}</div>`;
    setHTML("subs", subs);
    setHTML("stateline", V.exec === "stopped" ? `<div class="stopline">${icon("octagon-x")}停止：${esc(V.stop)}</div>`
      : V.exec === "unconnected" ? `<div class="stopline nc">${icon("octagon-pause")}未接続：${esc(V.stop)}</div>`
      : V.exec === "president" && !isPres ? `<div class="waitline">${icon("hourglass")}社長確認中：社長の答えが来るまで、この案は進めません</div>` : "");
    setHTML("execmark", `${icon(EXEC_ICON[V.exec] || "eye")}${esc(V.exec_label)}`);
    $("execmark").className = "execmark " + V.exec;
    drawNet();
    drawAsk(isPres);
    drawReceipt();
    setHTML("taken", (V.taken || []).length ? `${icon("bot")}${esc(V.taken.join("　/　"))}` : "");
    setHTML("others", (V.others || []).length ? `<span>ほかの仕事：</span>` + V.others.map((o) => `<button data-item="${esc(o.item)}">${icon(o.connected ? "fish" : "package")}${esc(o.name)} · <span class="${o.connected ? "" : "nc"}">${esc(o.status)}</span></button>`).join("")
      + (V.tender ? `<button data-item="buri_xl">${icon("fish")}ブリに戻る</button>` : "") : (V.tender ? `<button data-item="buri_xl">${icon("fish")}ブリに戻る</button>` : ""));
    $("opbtn").hidden = !(V.op && !isPres);
    if (V.op) $("opbtn").textContent = V.op.label;
    if (popFor === "why" && !$("pop").hidden) renderPop(V.why, true);
  }
  function setHTML(id, html) { const e = $(id); if (e._h !== html) { e.innerHTML = html; e._h = html; } }

  function presSubs() {
    const p = V.president;
    if (!p) return `<p>担当者と AI が、任された範囲で仕事を進めています。</p>`;
    const why = [p.priority_change ? "得意先の間の優先を変える案（社長の朝の指示と違う）" : "", p.over_limit ? "担当者の権限の額を超える案" : ""].filter(Boolean).join("・");
    return `<p>${esc(why || "社長の判断が要る案")}です。</p>` + (p.label ? `<p style="color:var(--ink)">担当者が選んだ案：${esc(p.label)}</p>` : "") +
      (p.change ? `<p>担当者の答え：仕入れ ${p.change.quantity}（${(p.change.allocations || []).map((a) => `${esc(a.customer)} ${a.quantity}`).join("・")}）</p>` : "");
  }

  function drawAsk(isPres) {
    let ask = "", acts = "";
    if (isPres) {
      const p = V.president;
      ask = p ? `<b>この案を進めてよいか、決めてください。</b><small>承認すると、AI がこの条件で登録まで進めます。</small>` : `<b>答える仕事はありません。</b>`;
      if (p && p.kind === "approval") acts = `<button class="dbtn" data-pres="approve">${icon("stamp")}社長として承認する</button>`;
      if (p && p.kind === "consultation") acts = `<button class="dbtn" data-pres="consult">${icon("stamp")}この答えで確定する</button>`;
    } else {
      ask = V.ask ? `<b>${esc(V.ask.text)}</b>` : "";
      if (V.ask && V.ask.origin === "ai") ask = `<b>どれで進めるか、選んでください。</b><small class="ai">${esc(V.ask.text)}</small>`;
      if (V.exec === "your_turn" || V.exec === "running") ask += V.exec === "running" ? `<small>判断が要る時は、私から声をかけます。</small>` : "";
      acts = (V.actions || []).map((a) => `<button class="dbtn ${a.president ? "pres" : a.kind === "primary" ? "" : "alt"}" data-act="${esc(a.id)}" ${a.disabled ? "disabled" : ""} title="${esc(a.note || "")}">
        ${icon(a.president ? "user-round" : a.kind === "primary" ? "arrow-up-right" : "arrow-right")}<span>${esc(a.label)}${a.note ? `<small>${esc(a.note)}</small>` : ""}</span></button>`).join("");
    }
    setHTML("whyslot", `<button class="whybtn" data-why="1">${icon("circle-help")}${V.exec === "your_turn" ? "なぜ？" : "条件を見る"}</button>`);
    setHTML("ask", ask);
    setHTML("acts", acts);
  }

  function drawReceipt() {
    const r = V.receipt;
    if (waitReceipt && r && r.at !== waitReceipt.prev) {   // the server has recorded the person's act: ✓ and a small celebration
      waitReceipt = null; ownAt = r.at;
      setHTML("receipt", receiptHTML(r));
      celebrate($("receipt").querySelector(".ic"));
      return;
    }
    if (waitReceipt) {
      if (Date.now() - waitReceipt.t > 15000) { waitReceipt = null; setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}サーバーの記録が確かめられません。画面を開き直してください</div>`); }
      return;
    }
    const mine = ROLE !== "president" || (r && r.at === ownAt);   // 社長の画面には、社長自身の操作の受領だけを出す
    setHTML("receipt", r && mine ? receiptHTML(r) : "");
  }
  function receiptHTML(r) {
    return `<div class="receipt">${icon("circle-check")}<div>${esc(r.text)}${r.ai ? `<small>AI の文：${esc(r.ai.text)}</small>` : ""}<small>サーバーの記録 ${esc(r.ref.table)} ${r.ref.id ?? ""}・${hm(r.at)}</small></div></div>`;
  }
  function celebrate(anchor) {
    if (REDUCED || !anchor) return;
    const card = $("card"), cr = card.getBoundingClientRect(), a = anchor.getBoundingClientRect();
    const x0 = a.left - cr.left + a.width / 2, y0 = a.top - cr.top + a.height / 2;
    for (let k = 0; k < 16; k++) {
      const d = document.createElement("i");
      d.className = "confetti";
      d.style.background = ["#2F6B4F", "#3B6E77", "#C9A24A", "#E9B9A6"][k % 4];
      d.style.left = x0 + "px"; d.style.top = y0 + "px";
      card.appendChild(d);
      const ang = (k / 16) * Math.PI * 2, dist = 28 + Math.random() * 30;
      d.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 }, { transform: `translate(${Math.cos(ang) * dist}px,${Math.sin(ang) * dist - 10}px) scale(.6)`, opacity: 0 }],
        { duration: 750, easing: "cubic-bezier(.2,.7,.3,1)" }).onfinish = () => d.remove();
    }
    anchor.animate([{ transform: "scale(.4)" }, { transform: "scale(1.25)" }, { transform: "scale(1)" }], { duration: 520, easing: "ease-out" });
  }

  // letters between the companion and the other organisations (the AI at work)
  function drawNet() {
    const orgs = V.orgs || [];
    setHTML("net", orgs.length ? `<span class="me">照会先</span>` + orgs.map((o) => `<span class="org ${o.state}" data-org="${esc(o.code)}">${icon(o.code === "iwase" ? "ship" : "store")}${esc(o.name)}
      <small>${{ asked: "返事待ち", replied: "返事あり", declined: "断り" }[o.state]}</small></span>`).join("") : "");
    const fresh = (V.net || []).filter((m) => !seenNet.has(m.id));
    fresh.forEach((m) => seenNet.add(m.id));
    if (!netReady) { netReady = true; return; }
    fresh.forEach((m, i) => setTimeout(() => fly(m), i * 350));
  }
  function fly(m) {
    if (REDUCED) return;
    const out = m.frm === OWNER, org = out ? m.to : m.frm;
    const badge = document.querySelector(`.org[data-org="${org}"]`), me = $("figure");
    if (!badge || !me) return;
    const card = $("card"), cr = card.getBoundingClientRect(), b = badge.getBoundingClientRect(), f = me.getBoundingClientRect();
    const A = { x: f.left - cr.left + f.width * .62, y: f.top - cr.top + f.height * .42 }, B = { x: b.left - cr.left + 12, y: b.top - cr.top + 4 };
    const [s, e] = out ? [A, B] : [B, A];
    const el = document.createElement("div");
    el.className = "letter" + (out ? "" : " back");
    el.innerHTML = icon(m.kind === "decline" ? "x" : "mail");
    el.style.left = "0px"; el.style.top = "0px";
    card.appendChild(el);
    const mx = (s.x + e.x) / 2, my = Math.min(s.y, e.y) - 70;
    el.animate([{ transform: `translate(${s.x}px,${s.y}px) scale(.6)`, opacity: 0 }, { transform: `translate(${mx}px,${my}px) scale(1.1)`, opacity: 1, offset: .5 },
      { transform: `translate(${e.x}px,${e.y}px) scale(.8)`, opacity: .9 }], { duration: 1100, easing: "cubic-bezier(.4,.1,.3,1)" }).onfinish = () => {
      el.remove();
      const tgt = out ? badge : me;
      tgt.animate([{ transform: "scale(1)" }, { transform: "scale(1.08)" }, { transform: "scale(1)" }], { duration: 380 });
    };
  }

  // ── the reason card: opens from the companion, goes back to it ──
  function openPop(from, card) {
    popFor = card === V?.why ? "why" : "other";
    renderPop(card, false);
    const pop = $("pop"), cardEl = $("card");
    pop.hidden = false;
    const cr = cardEl.getBoundingClientRect(), anchor = $("talk").hidden ? $("mmain") : document.querySelector(".talk");
    const ar = anchor.getBoundingClientRect();
    pop.style.top = (ar.top - cr.top + ($("talk").hidden ? 60 : ar.height + 6)) + "px";
    const f = from.getBoundingClientRect(), pr = pop.getBoundingClientRect();
    pop.style.transformOrigin = `${f.left + f.width / 2 - pr.left}px ${f.top + f.height / 2 - pr.top}px`;
    if (!REDUCED) pop.animate([{ transform: "scale(.08)", opacity: 0 }, { transform: "scale(1)", opacity: 1 }], { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" });
    pop._from = from;
  }
  function renderPop(c, keepMore) {
    const pop = $("pop"), open = keepMore && pop.querySelector(".moresecs") && !pop.querySelector(".moresecs").hidden;
    const sec = (s) => `<h4>${esc(s.title)}</h4>` + s.rows.map((r) => `<div class="prow"><span class="o ${esc(r.origin)}">${esc((c.origins || {})[r.origin] || r.origin)}</span><span>${esc(r.text)}</span>
      ${r.at ? `<span class="at">${hm(r.at)}</span>` : ""}</div>`).join("");
    const html = `<div class="ph">${icon("notebook-text")}こう考えた理由<button id="popclose">${icon("x")}閉じる</button></div>
      <div class="lead">${esc(c.lead)}</div>${c.sections.map(sec).join("")}
      ${c.more && c.more.length ? `<button class="more" id="popmore">${icon("chevron-down")}詳しい経緯と Vault の記録</button><div class="moresecs" ${open ? "" : "hidden"}>${c.more.map(sec).join("")}</div>` : ""}
      ${c.speech_origin ? `<div class="origin">いまの語りかけの出所：${esc(c.speech_origin.label)}${c.speech_origin.ref ? `（${esc(c.speech_origin.ref.table)} ${c.speech_origin.ref.id}）` : ""}
        ${c.speech_origin.replaced ? `<br>${esc(c.speech_origin.replaced.reason)}（AI の文「${esc(c.speech_origin.replaced.text)}」・${esc(c.speech_origin.replaced.ref.table)} ${c.speech_origin.replaced.ref.id}）` : ""}</div>` : ""}`;
    if (pop._h !== html) { pop.innerHTML = html; pop._h = html; }
  }
  function closePop() {
    const pop = $("pop"), from = pop._from;
    const done = () => {
      pop.hidden = true; popFor = null;
      // the eyes go back to the decision that was open
      const b = document.querySelector("#acts .dbtn:not([disabled])") || document.querySelector("#mmain .pbtn");
      if (b) { b.focus({ preventScroll: true }); b.classList.add("focusring"); setTimeout(() => b.classList.remove("focusring"), 1400); }
    };
    if (REDUCED || !from) return done();
    const pr = pop.getBoundingClientRect(), f = from.getBoundingClientRect();
    pop.style.transformOrigin = `${f.left + f.width / 2 - pr.left}px ${f.top + f.height / 2 - pr.top}px`;
    pop.animate([{ transform: "scale(1)", opacity: 1 }, { transform: "scale(.08)", opacity: 0 }], { duration: 320, easing: "cubic-bezier(.5,0,.8,.4)" }).onfinish = done;
  }
  $("pop").addEventListener("click", (e) => {
    if (e.target.closest("#popclose")) return closePop();
    if (e.target.closest("#popmore")) { const m = $("pop").querySelector(".moresecs"); m.hidden = !m.hidden; }
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("pop").hidden) closePop(); });
  $("matebox").addEventListener("click", () => { if (V) openPop($("figure"), V.why); });
  $("whyslot").addEventListener("click", (e) => { if (e.target.closest("[data-why]") && V) openPop($("figure"), V.why); });

  // ── the person's acts (the existing APIs; the server re-checks everything) ──
  $("acts").addEventListener("click", async (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled) return;
    if (b.dataset.why) return openPop($("figure"), V.why);
    let path, body;
    if (b.dataset.pres) {
      const p = V.president;
      if (p.kind === "approval") { path = "/api/decide"; body = { case_id: p.case_id, option_id: p.option_id, action: "approve", fingerprint: p.fingerprint, actor: "president" }; }
      else { path = "/api/answer"; body = { consultation_id: p.consultation_id, option: "change", change: p.change, role: "president" }; }
    } else {
      const a = (V.actions || []).find((x) => x.id === b.dataset.act);
      if (!a) return;
      if (a.id === "check") return;
      if (a.id === "resume") { path = "/api/worker/resume"; body = {}; }
      else { path = a.api.path; body = a.api.body; }
    }
    document.querySelectorAll("#acts .dbtn").forEach((x) => (x.disabled = true));
    setHTML("receipt", `<div class="receipt pending">${icon("loader")}送っています…</div>`);
    const prev = V.receipt ? V.receipt.at : null;
    const r = await post(path, body);
    const res = r.j.result;
    if (r.ok && res !== "stale" && res !== "not_executable") {
      waitReceipt = { prev, t: Date.now() };
      setHTML("receipt", `<div class="receipt pending">${icon("loader")}サーバーが記録を確かめています…</div>`);
    } else {
      setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}確定しませんでした：${esc((r.j.reasons || [r.j.detail || r.j.error || r.status]).join("・"))}</div>`);
      document.querySelectorAll("#acts .dbtn").forEach((x) => (x.disabled = false));
    }
  });
  $("others").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-item]");
    if (b) location.search = `?item=${encodeURIComponent(b.dataset.item)}` + (ROLE === "president" ? "&role=president" : "");
  });
  $("opbtn").addEventListener("click", async () => {
    $("opbtn").disabled = true;
    const r = await fetch("/api/order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(V.op.body) });
    if (!r.ok) $("opbtn").textContent = "（実演の操作）入れられませんでした";
    $("opbtn").disabled = false;
  });
  $("flowbtn").addEventListener("click", async () => {
    const m = await getJSON(`/api/morning?owner=${OWNER}`);
    openPop($("flowbtn"), { lead: "今日の仕事です。手の要る順に並べています（コードの決まり）。", sections: [{ title: "今日の仕事", rows: m.jobs.map((j) => ({
      text: `［${j.step}］${j.title}（${j.done ? "済み" : j.who_label}）${j.connected ? "" : "・未接続"}`, origin: j.who === "you" ? "record" : "code" })) }],
      more: [{ title: "並べ方の決まり", rows: [{ text: m.rule, origin: "code" }] }], speech_origin: null, origins: { record: "あなたが決める", code: "業務の記録から" } });
  });

  // a small hook for the screenshot script: which stance and state is on screen
  window.__mate = () => ({ exec: V && V.exec, stance: fig && fig.stance, phase, pop: !$("pop").hidden, busy: !!(V && V.busy) });
  tick();
})();
