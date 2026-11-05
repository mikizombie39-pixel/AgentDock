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

  // 行き止まり（見張り中でも、当たり直すかは人が決める）の札は手の印（2026-11-05 デイブ：見出しと札を食い違わせない）
  function execIcon() { return V.exec === "idle" && V.who === "you" ? "hand" : (EXEC_ICON[V.exec] || "eye"); }
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
      if (V.clock) $("clocktext").textContent = `模擬の時計 ${V.clock.now_shown}`;
      if (V.band) drawBand(V.band, V.band_others || []);
      if (V.exec === "before_start" && ROLE === "staff") await drawMorning(); else drawTalk();
      const sig = JSON.stringify([V.exec, (V.net || []).length, V.tally, (V.receipt || {}).at]);   // 業務の状態が変わったら、チャットの答えが古くなったかを読み直す
      if (ROLE !== "president" && chatTurns.length && !chatBusy && sig !== chatSig) { chatSig = sig; loadChat(); }
    } catch (e) { /* keep the last view */ }
    setTimeout(tick, 1000);
  }

  // ═════ the morning ═════
  async function drawMorning() {
    $("talk").hidden = true; $("morning").hidden = false; $("flowbtn").hidden = true;
    if (!M) { M = await getJSON(`/api/morning?owner=${OWNER}`); }
    const key2 = phase;
    if ($("morning").dataset.phase === key2) return;
    $("morning").dataset.phase = key2;
    if (phase === "intro") { drawJobs(); drawGreeting(); }
    if (phase === "check") { drawCheck(); }
    if (phase === "delegate") { drawSideOrders(); drawPropose(); }
  }

  // ═════ 工程の帯（上部に常時）═════
  // 形は記録から決まる（GET /api/mate の band）。動き：最初に線が現在地まで走る／待つ間も現在地の輪が脈打ち、済みの線の上を光が流れる／
  // 記録が変わって段が進んだ時だけ、済みになった丸が跳ね、線が次の段まで伸びる。入力欄や読んでいる内容は動かさない
  const BAND_WORD = { done: "済み", partial: "照会完了", current: "いま", todo: "これから", skipped: "今日は無し" };   // partial＝照会は終えたが不足が残る（済みと言わない）
  const BAND_ICON = { you: "hand", president: "hourglass", working: "loader", stopped: "octagon-x", unconnected: "octagon-pause", watch: "eye", before: "clipboard-list", done: "circle-check" };
  let BAND = null;
  function bandPct(b) {
    const n = b.steps.length;
    let at = b.current;
    if (at == null) { at = -1; b.steps.forEach((s, i) => { if (s.state === "done" || s.state === "partial") at = i; }); }
    return at <= 0 ? 0 : (at / (n - 1)) * 100;
  }
  function drawBand(b, others) {
    const el = $("band"), n = b.steps.length;
    const first = el._item !== b.item;
    if (first) {
      el.style.gridTemplateColumns = `repeat(${n}, 1fr)`;
      el.innerHTML = `<div class="track" style="left:${50 / n}%;right:${50 / n}%"><div class="fill"><i class="comet"></i></div><div class="ahead"><i></i></div></div>` +
        b.steps.map((s, i) => `<div class="bstep" data-i="${i}"><div class="dot"></div><div class="bname">${esc(s.name)}</div><div class="bword"></div><div class="bnote"></div></div>`).join("");
      el._item = b.item; BAND = null;
    }
    el.dataset.mode = b.mode;
    const nodes = [...el.querySelectorAll(".bstep")];
    b.steps.forEach((s, i) => {
      const nd = nodes[i];
      const cls = `bstep ${s.state}${s.connected ? "" : " nc"}${i === b.current ? " m-" + b.mode : ""}`;
      if (nd.className !== cls) nd.className = cls;
      const ic = s.state === "done" ? "circle-check" : s.state === "partial" ? "circle-alert" : i === b.current ? BAND_ICON[b.mode] || "eye" : !s.connected ? "octagon-pause" : "";
      const dot = nd.querySelector(".dot");
      if (dot._ic !== ic) { dot.innerHTML = ic ? icon(ic) : ""; dot._ic = ic; }
      const word = !s.connected && s.state !== "done" ? "未接続" : s.word || BAND_WORD[s.state];   // 段ごとの言い方（例：照合済み）はサーバーが渡す
      const w = nd.querySelector(".bword"); if (w.textContent !== word) w.textContent = word;
      const note = s.state === "current" || s.state === "done" || s.state === "partial" || s.state === "skipped" ? s.note || "" : "";
      const nt = nd.querySelector(".bnote"); if (nt.textContent !== (note === word ? "" : note)) nt.textContent = note === word ? "" : note;
    });
    const fill = el.querySelector(".fill"), ahead = el.querySelector(".ahead");
    const pct = bandPct(b);
    const seg = 100 / (n - 1);
    ahead.style.left = pct + "%"; ahead.style.width = (b.current != null && b.current < n - 1 ? seg : 0) + "%";
    const prev = BAND;
    if (first || !prev) {   // 開いた時：線が現在地まで走り、済みの段が順に灯る
      if (REDUCED) fill.style.width = pct + "%";
      else {
        fill.animate([{ width: "0%" }, { width: pct + "%" }], { duration: 300 + pct * 9, easing: "ease-in-out", fill: "forwards" }).onfinish = () => { fill.style.width = pct + "%"; };
        nodes.forEach((nd, i) => { if (b.steps[i].state === "done" || i === b.current) pop(nd.querySelector(".dot"), 180 + i * (pct * 9 / Math.max(1, b.current ?? n))); });
      }
    } else if (prev.current !== b.current || bandPct(prev) !== pct) {   // 記録が変わって段が動いた
      const from = bandPct(prev);
      if (REDUCED) fill.style.width = pct + "%";
      else {
        fill.animate([{ width: from + "%" }, { width: pct + "%" }], { duration: 700, easing: "cubic-bezier(.3,.7,.3,1)", fill: "forwards" }).onfinish = () => { fill.style.width = pct + "%"; };
        nodes.forEach((nd, i) => { if (b.steps[i].state === "done" && prev.steps[i] && prev.steps[i].state !== "done") { pop(nd.querySelector(".dot"), 0); burst(nd.querySelector(".dot")); } });
        if (b.current != null) pop(nodes[b.current].querySelector(".dot"), 650);
      }
    } else if (prev.mode !== b.mode && b.current != null && !REDUCED) {   // 同じ段で状態が変わった（判断待ち → 社長確認中 など）
      pop(nodes[b.current].querySelector(".dot"), 0);
    }
    BAND = b;
    const oh = others.length ? `<span>ほかの仕事：</span>` + others.map((o) => {
      const nc = o.mode === "unconnected" || o.mode === "stopped";
      return `<button data-item="${esc(o.item)}" class="${nc ? "nc" : ""}">${icon(o.tender ? "package" : "fish")}${esc(o.summary)}${o.mode === "done" ? "" : '<i class="pip"></i>'}</button>`;
    }).join("") : "";
    setHTML("bandothers", oh);
  }
  function pop(dot, delay) {
    if (REDUCED || !dot) return;
    dot.animate([{ transform: "scale(.6)" }, { transform: "scale(1.3)" }, { transform: "" }], { duration: 520, delay, easing: "ease-out" });
  }
  function burst(anchor) {
    if (REDUCED || !anchor) return;
    const card = $("card"), cr = card.getBoundingClientRect(), a = anchor.getBoundingClientRect();
    const x0 = a.left - cr.left + a.width / 2, y0 = a.top - cr.top + a.height / 2;
    for (let k = 0; k < 12; k++) {
      const d = document.createElement("i");
      d.className = "confetti";
      d.style.background = ["#3B6E77", "#2F6B4F", "#C9A24A"][k % 3];
      d.style.left = x0 + "px"; d.style.top = y0 + "px";
      card.appendChild(d);
      const ang = (k / 12) * Math.PI * 2, dist = 22 + Math.random() * 18;
      d.animate([{ transform: "translate(0,0) scale(1)", opacity: 1 }, { transform: `translate(${Math.cos(ang) * dist}px,${Math.sin(ang) * dist}px) scale(.5)`, opacity: 0 }],
        { duration: 650, easing: "cubic-bezier(.2,.7,.3,1)" }).onfinish = () => d.remove();
    }
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
    // 挨拶の枠の中で、仲間の顔と言葉を一緒に見せる（SAL 2026-11-04）
    $("mmain").innerHTML = `<div class="propose greet"><div class="gface"><div class="gmini" id="mini"></div><span>あなたの仲間<br>網谷水産のAI</span></div>
      <div class="gtext"><div class="k">${icon("message-circle")}今朝の始まり</div>
      <h2>${esc(V.headline.text)}</h2>
      ${V.sub.map((s) => `<p>${esc(s.text)}</p>`).join("")}
      <div class="btnrow" style="margin-top:20px"><button class="pbtn" id="go-check">${icon("clipboard-list")}一緒に確認する</button></div></div></div>`;
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
      <div class="s">${o.tender ? (o.closes_at ? "入札の締切 " + hm(o.closes_at) : '入札の受付：<b style="color:var(--stop)">未接続</b>') : o.directive ? `社長の指示：「${esc(o.directive)}」` : "社長の指示：なし"}</div>
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
    // 進めると言うのは、つながっている仕事だけ（未接続の入札は言わない・SAL 2026-11-04）
    const acts = [...new Set(c.orders.filter((o) => !o.tender || o.connected).map((o) => (o.tender ? `${o.item}の入札` : `${o.item}の漁協への照会`)))];
    $("mmain").innerHTML = miniAgent() + `<div class="propose"><div class="k">${icon("message-circle")}まず、私に任せてください</div>
      <h2>買い付けは、私が進めましょうか？</h2>
      <p>今朝は、${esc(phrase)}ですね。</p><p>注文と採算を確認して、${acts.length ? esc(acts.join("と")) + "を進めます。" : "配分と購入の判断を相談します。"}</p>
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
      : `${icon(execIcon())}<span>${esc(item)}：<b>${esc(V.exec_label)}</b></span>`;
    setHTML("tag", tag);
    const isPres = ROLE === "president";
    const head = isPres ? (V.president ? "担当者と AI から、確認が届きました。" : "今、社長の判断を待つ仕事はありません。") : V.headline.text;
    if ($("headline").textContent !== head) {
      $("headline").textContent = head;
      if (!REDUCED) $("headline").animate([{ opacity: 0, transform: "translateY(6px)" }, { opacity: 1, transform: "none" }], { duration: 380, easing: "ease-out" });
      // 状況が変わった：枠が一度ふくらんで光る（目を問題へ向ける）
      if (!REDUCED) $("focus").animate([{ boxShadow: "0 0 0 0 rgba(201,162,74,0)" }, { boxShadow: "0 0 0 10px rgba(201,162,74,.28)", offset: .35 }, { boxShadow: "0 0 0 0 rgba(201,162,74,0)" }],
        { duration: 1100, easing: "ease-out" });
    }
    let subs = isPres ? presSubs() : V.sub.map((s) => `<p class="${s.origin === "reply" ? "reply" : ""}">${esc(s.text)}</p>`).join("");
    if (V.tender) subs += `<div class="tenderrows">${V.tender.rows.map((r) => `<div><b>${esc(r.label)}</b><span class="${r.connected ? "" : "nc"}">${esc(r.text)}</span></div>`).join("")}</div>`;
    setHTML("subs", subs);
    setHTML("stateline", V.exec === "stopped" ? `<div class="stopline">${icon("octagon-x")}停止：${esc(V.stop)}</div>`
      : V.exec === "unconnected" ? `<div class="stopline nc">${icon("octagon-pause")}未接続：${esc(V.stop)}</div>`
      : V.exec === "president" && !isPres ? `<div class="waitline">${icon("hourglass")}社長確認中：社長の答えが来るまで、この案は進めません</div>` : "");
    setHTML("execmark", `${icon(execIcon())}${esc(V.exec_label)}`);
    const fc = "focus " + V.exec;
    if ($("focus").className !== fc) $("focus").className = fc;
    $("execmark").className = "execmark " + V.exec;
    drawNet();
    drawAsk(isPres);
    drawReceipt();
    setHTML("taken", (V.taken || []).length ? `${icon("bot")}${esc(V.taken.join("　/　"))}` : "");
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
      (p.change ? `<p>担当者の答え：仕入れ ${p.change.quantity}（${(p.change.allocations || []).map((a) => `${esc((V.shops || {})[a.customer] || a.customer)} ${a.quantity}`).join("・")}）</p>` : "");
  }

  let RET = false, RET_TEXT = "";   // 社長の差し戻しの理由の入力欄（開いている間は描き直しで消さない）
  function drawAsk(isPres) {
    let ask = "", acts = "";
    if (isPres) {
      const p = V.president;
      ask = p ? `<b>この案を承認するか、理由を添えて差し戻すかを選んでください。</b><small>承認すると、AI がこの条件で登録まで進めます。${p.kind === "approval" ? "差し戻すと、元の案は登録できなくなり、AI が理由に沿って案を組み直します。登録済みの取引はそのままです。" : ""}</small>` : `<b>答える仕事はありません。</b>`;
      // 社長の差し戻し（2026-11-04 デイブ・SAL）：取引の取り消しとは別。理由を添えて担当者と AI に見直しを求める
      if (p && p.kind === "approval") acts = RET ? `<div class="retbox"><label for="retr">差し戻しの理由</label>
          <textarea id="retr" rows="2" maxlength="200" placeholder="例：魚廣の 4本を先に確保して">${esc(RET_TEXT)}</textarea>
          <div class="retacts"><button class="dbtn" data-pres="return-send">${icon("rotate-ccw")}この理由で差し戻す</button><button class="linkbtn" data-pres="return-cancel">やめる</button></div></div>`
        : `<button class="dbtn" data-pres="approve">${icon("stamp")}承認する</button><button class="dbtn alt" data-pres="return">${icon("rotate-ccw")}理由を添えて差し戻す</button>`;
      if (p && p.kind === "consultation") acts = `<button class="dbtn" data-pres="consult">${icon("stamp")}この答えで確定する</button>`;
    } else {
      ask = V.ask ? `<b>${esc(V.ask.text)}</b>` : "";
      if (V.ask && V.ask.origin === "ai") ask = `<b>どれで進めるか、選んでください。</b><small class="ai">${esc(V.ask.text)}</small>`;
      if (V.exec === "your_turn" || V.exec === "running") ask += V.exec === "running" ? `<small>判断が要る時は、私から声をかけます。</small>` : "";
      const decideMode = V.exec === "your_turn" && (V.actions || []).some((a) => String(a.id).startsWith("opt"));
      $("acts").closest(".bottom").classList.toggle("decidemode", decideMode);
      if (decideMode) { ask = ""; acts = decideHTML(V.actions); } else
      acts = (V.actions || []).map((a) => `<button class="dbtn ${a.president ? "pres" : a.kind === "primary" ? "" : "alt"}" data-act="${esc(a.id)}" ${a.disabled ? "disabled" : ""} title="${esc(a.note || "")}">
        ${icon(a.president ? "user-round" : a.kind === "primary" ? "arrow-up-right" : "arrow-right")}<span>${esc(a.label)}${a.note ? `<small>${esc(a.note)}</small>` : ""}</span></button>`).join("");
    }
    setHTML("whyslot", `<button class="whybtn" data-why="1">${icon("circle-help")}${V.exec === "your_turn" ? "なぜ？" : "条件を見る"}</button>`);
    setHTML("ask", ask);
    setHTML("acts", acts);
  }

  // ═════ 判断のブロック（SAL 2026-11-04 §2・§3／デイブ「関連性・重要度・操作の方法を分かりやすく、矢印を動かす」）═════
  // ① 案を選ぶ（画面の下見だけ・サーバーには書かない）→ ② 配分を確かめる（右に店ごとの確保と不足）→ ③ 承認のボタンで初めて登録（既存の処理）。
  // AI のおすすめは★の札で示し、選択中（丸の印と枠）とは別の形にする。社長の確認が要る案は、承認のボタンが「社長に確かめる」になる
  let SEL = null, RFOLD = false, OTHER_TEXT = "";   // OTHER_TEXT：「ほかの指示を出す」の入力（描き直しで消さない）
  const yen = (v) => (v == null ? "" : `${Number(v).toLocaleString("ja-JP")}円`);
  function decideHTML(acts) {
    const u = esc((V.item && V.item.unit) || "");
    const opts = acts.filter((a) => String(a.id).startsWith("opt") || a.id === "other");
    if (SEL && !opts.some((a) => a.id === SEL && !a.disabled)) SEL = null;
    const sel = opts.find((a) => a.id === SEL);
    const rec = opts.find((a) => a.recommended && !a.disabled);
    const at = sel ? 2 : 1;
    const reg = (V.tally && V.tally.registered) || 0;   // 登録済みがある時は「何も登録しない」と言わず、追加の買い付けに限って言う（SAL 2026-11-04）
    const step = (n, t, s) => `<li class="${n < at ? "done" : n === at ? "on" : ""}"><b>${["", "①", "②", "③"][n]} ${esc(t)}</b><small>${esc(s)}</small></li>`;
    const chev = `<span class="chev" aria-hidden="true"><i>›</i><i>›</i><i>›</i></span>`;
    const card = (a) => {
      const short = (a.alloc || []).filter((x) => x.short > 0);
      const cls = ["opt", a.choice, a.recommended && !a.disabled ? "rec" : "", a.id === SEL ? "sel" : "", a.disabled ? "off" : ""].filter(Boolean).join(" ");
      return `<div class="optrow">${!sel && rec && a.id === rec.id ? `<span class="pointer" aria-hidden="true">${icon("arrow-right")}</span>` : `<span class="pointer"></span>`}
        <button class="${cls}" data-pick="${esc(a.id)}" aria-pressed="${a.id === SEL}" ${a.disabled ? "disabled" : ""}>
        <span class="radio" aria-hidden="true"></span>
        <span class="letter imp-${esc(a.importance || "low")}" aria-label="案 ${esc(a.letter || "")}">${esc(a.letter || "")}</span>
        <span class="ot"><b><span class="impic imp-${esc(a.importance || "low")}">${icon(a.icon || "circle-check")}</span>${esc(a.title || a.label)}</b>
          <span class="badges">${a.recommended && !a.disabled ? `<em class="recb">★ AI のおすすめ</em>` : ""}${a.president ? `<em class="presb">${icon("user-round")}社長の確認が要る</em>` : ""}</span>
          <span class="os ${a.disabled ? "bad" : a.choice === "other" ? "" : short.length ? "short" : "ok"}">${a.disabled ? esc(a.note) : a.choice === "other" ? esc(a.result)
            : `選ぶと：${esc(a.result || "")}`}</span></span></button>
        ${a.id === SEL ? `<span class="tab" aria-hidden="true">${chev}</span>` : ""}</div>`;
    };
    let pv;
    if (sel && sel.choice === "other") {   // 選択肢にない進め方：文で AI に頼む（AI は任された範囲で進めるか、できない理由を答える）
      pv = `<div class="pvh"><small>${esc(sel.letter)}：ほかの指示を出す（まだ何も登録しません）</small><b>どう進めたいかを、文で書いてください</b></div>
        <div class="retbox"><label for="othertext">AI への指示</label>
          <textarea id="othertext" rows="3" maxlength="200" placeholder="例：B社に 1本 18,000円まで下げられないか聞いて／魚廣に 2本、順風満帆に 0本で">${esc(OTHER_TEXT)}</textarea></div>
        <div class="eff">${icon("message-circle")}送ると：AI が、任された範囲（照会・条件内の入札・再交渉）で指示に沿って進めるか、できない理由と代わりの案を答えます。登録はしません。</div>
        <div class="go"><span class="down" aria-hidden="true">${icon("arrow-right")}</span>
          <button class="dbtn" data-other-send="1">${icon("send")}<span>この指示を AI に送る</span></button>
          <button class="linkbtn" data-unpick="1">選び直す</button></div>`;
    } else if (!sel) {
      pv = `<div class="pv0"><span class="back">${icon("arrow-right")}</span><div><b>左の案を選ぶと、進めた時の配分と金額をここに出します。</b><small>${reg ? "案を選ぶだけでは、追加の買い付けは登録しません。" : "選んでも、まだ何も登録しません。"}</small></div></div>`;
    } else {
      const most = Math.max(1, ...(sel.alloc || []).map((x) => x.ordered));
      const bars = (sel.alloc || []).map((x) => `<div class="bar"><span class="nm">${esc(x.shop)}</span>
        <span class="lane"><span class="track" style="width:${(x.ordered / most) * 100}%"><i style="width:${x.ordered ? (x.secured / x.ordered) * 100 : 0}%"></i></span></span>
        <span class="q">${x.secured} / ${x.ordered}${u}</span><span class="st ${x.short ? "short" : "ok"}">${x.short ? `${x.short}${u} 足りない` : "確保"}</span></div>`).join("");
      pv = `<div class="pvh"><small>② ${esc(sel.letter)} を選んで進めると（${reg ? "追加の買い付けはまだ登録していません" : "まだ登録していません"}）</small><b>${esc(sel.title)}</b></div>
        ${(sel.buys || []).length ? `<table class="buyt"><thead><tr><th>仕入先</th><th>数量</th><th>単価</th><th>金額</th></tr></thead><tbody>` +
          sel.buys.map((b) => `<tr><td><span class="sn">${icon("ship")}${esc(b.seller)}</span></td><td>${b.quantity}${u}</td><td>${yen(b.unit_price_yen)}</td><td>${yen(b.amount_yen)}</td></tr>`).join("") +
          `</tbody><tfoot><tr><td colspan="3">合計</td><td>${yen(sel.purchase_yen)}</td></tr></tfoot></table>` : ""}
        <div class="bars">${bars}</div>
        <div class="eff">${icon(sel.choice === "president" ? "user-round" : sel.choice === "reject" ? "x" : "stamp")}押すと：${esc(sel.effect)}</div>
        <div class="go"><span class="down" aria-hidden="true">${icon("arrow-right")}</span>
          <button class="dbtn ${sel.choice === "president" ? "pres" : sel.choice === "reject" ? "alt" : ""}" data-act="${esc(sel.id)}">${icon(sel.choice === "president" ? "user-round" : sel.choice === "reject" ? "x" : "stamp")}<span>③ ${esc(sel.confirm)}</span></button>
          <button class="linkbtn" data-unpick="1">選び直す</button></div>`;
    }
    return `<div class="decide" data-at="${at}">
      <div class="dk">${icon("hand")}${esc((V.decide && V.decide.block) || "どの案で進めるか、決めてください")}</div>
      <div class="dq">${!sel ? `${rec ? `私のおすすめは ${esc(rec.letter)}「${esc(rec.title || rec.label)}」です。${esc((V.decide && V.decide.reason) || "")}` : ""}<span class="nw">A〜${esc(opts[opts.length - 1].letter)}</span> から選んでください。選択肢にない進め方は、${esc(opts[opts.length - 1].letter)} から文で頼めます。`
        : sel.choice === "other" ? "どう進めたいかを書いて送ってください。AI が、できるかどうかを確かめて答えます。"
        : sel.choice === "president" ? `${esc(sel.letter)}「${esc(sel.title)}」を選びました。社長の確認が要る案なので、よければ私が社長に確かめます。`
        : sel.choice === "reject" ? `${esc(sel.letter)}「今回は仕入れを見送る」を選びました。よければ見送ります。仮押さえは手放します。`
        : `${esc(sel.letter)}「${esc(sel.title)}」を選びました。右の内容を確認して、よければ購入を承認してください。`}</div>
      <ol class="dsteps">${sel && sel.choice === "other"
        ? `${step(1, "案を選ぶ", "C：ほかの指示")}${chev}${step(2, "指示を書く", "右の欄に書きます")}${chev}${step(3, "AI に送る", "登録はしません")}`
        : `${step(1, "案を選ぶ", reg ? "追加の買い付けは登録しません" : "まだ何も登録しません")}${chev}${step(2, "配分を確かめる", "右に下見が出ます")}${chev}${step(3, "承認する", reg ? "ここで追加分を登録します" : "ここで初めて登録します")}`}</ol>
      <div class="dgrid"><div class="opts">${opts.map(card).join("")}</div><div class="pv ${sel ? "on" : ""}">${pv}</div></div></div>`;
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
    const deciding = V.exec === "your_turn" && (V.actions || []).some((a) => String(a.id).startsWith("opt"));
    if (r && mine && deciding && r.at !== ownAt) {
      setHTML("receipt", `<button class="linkbtn rfold" data-rfold="1">${icon("chevron-down")}${RFOLD ? "これまでの受け取りを閉じる" : `これまでの受け取りを見る（${esc(r.at_shown || hm(r.at))}）`}</button>` + (RFOLD ? receiptHTML(r) : ""));
      return;
    }
    setHTML("receipt", r && mine ? receiptHTML(r) : "");
  }
  function receiptHTML(r) {
    return `<div class="receipt">${icon("circle-check")}<div>${esc(r.text)}${r.ai ? `<small>AI の文：${esc(r.ai.text)}</small>` : ""}<small>サーバーの記録 ${esc(r.ref.table)} ${r.ref.id ?? ""}・${esc(r.at_shown || hm(r.at))}（模擬の時計）</small></div></div>`;
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
      <small>${{ asked: "返事待ち", replied: "返事あり", declined: "断り", expired: "期限切れ", failed: "登録できず" }[o.state]}</small></span>`).join("") : "");
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
    // 時刻は模擬の時計（at_shown）。詳しい経緯の中では、実際に記録した時刻も並べる
    const sec = (s, real) => `<h4>${esc(s.title)}</h4>` + s.rows.map((r) => `<div class="prow"><span class="o ${esc(r.origin)}">${esc((c.origins || {})[r.origin] || r.origin)}</span><span>${esc(r.text)}</span>
      ${r.at ? `<span class="at">${esc(r.at_shown || hm(r.at))}${real && r.at_shown && !String(r.at).startsWith(c.on_date || "2026-11-02") ? `<span class="real">（実際の記録 ${esc(String(r.at).slice(11, 19))}）</span>` : ""}</span>` : ""}</div>`).join("");
    const html = `<div class="ph">${icon("notebook-text")}こう考えた理由<button id="popclose">${icon("x")}閉じる</button></div>
      <div class="lead">${esc(c.lead)}</div>${c.sections.map((s) => sec(s, false)).join("")}
      ${c.more && c.more.length ? `<button class="more" id="popmore">${icon("chevron-down")}詳しい経緯と Vault の記録</button><div class="moresecs" ${open ? "" : "hidden"}>${c.more.map((s) => sec(s, true)).join("")}</div>` : ""}
      ${c.speech_origin ? `<div class="origin">いまの語りかけの出所：${esc(c.speech_origin.label)}${c.speech_origin.ref ? `（${esc(c.speech_origin.ref.table)} ${c.speech_origin.ref.id}）` : ""}
        ${c.speech_origin.replaced ? `<br>${esc(c.speech_origin.replaced.reason)}（AI の文「${esc(c.speech_origin.replaced.text)}」・${esc(c.speech_origin.replaced.ref.table)} ${c.speech_origin.replaced.ref.id}）` : ""}</div>` : ""}`;
    if (pop._h !== html) { pop.innerHTML = html; pop._h = html; }
  }
  function closePop() {
    const pop = $("pop"), from = pop._from;
    const done = () => {
      pop.hidden = true; popFor = null;
      // the eyes go back to the decision that was open
      const b = document.querySelector("#acts .dbtn:not([disabled])") || document.querySelector("#acts .opt.rec") || document.querySelector("#mmain .pbtn");
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
    if (b.dataset.pick || b.dataset.unpick) {
      const was = SEL;
      SEL = b.dataset.pick || null;
      if (SEL === was) return;
      drawAsk(ROLE === "president");
      const pv = $("acts").querySelector(".pv");
      if (pv && !REDUCED) pv.animate([{ opacity: 0, transform: "translateX(-14px)" }, { opacity: 1, transform: "none" }], { duration: 360, easing: "ease-out" });
      return;
    }
    if (b.dataset.why) return openPop($("figure"), V.why);
    if (b.dataset.otherSend) {   // ほかの指示：担当者の質問の入口（/api/ask・kind=instruction）へ。受け取りは AI が動き出したことで画面に出る
      const a = (V.actions || []).find((x) => x.id === "other");
      OTHER_TEXT = ($("othertext").value || "").trim();
      if (!OTHER_TEXT || !a) { $("othertext").focus(); $("othertext").classList.add("need"); return; }
      b.disabled = true;
      const r = await post("/api/ask", Object.assign({}, a.api.body, { text: OTHER_TEXT }));
      if (r.ok) { SEL = null; OTHER_TEXT = ""; setHTML("receipt", `<div class="receipt pending">${icon("loader")}AI に指示を送りました。指示に沿って進め方を組み直しています…</div>`); }
      else { b.disabled = false; setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}送れませんでした：${esc(r.j.detail || r.j.error || r.status)}</div>`); }
      return;
    }
    let path, body;
    if (b.dataset.pres) {
      const p = V.president;
      if (b.dataset.pres === "return" || b.dataset.pres === "return-cancel") { RET = b.dataset.pres === "return"; RET_TEXT = ""; return drawAsk(true); }
      if (b.dataset.pres === "return-send") {
        RET_TEXT = ($("retr").value || "").trim();
        if (!RET_TEXT) { $("retr").focus(); $("retr").classList.add("need"); return; }
        path = "/api/decide"; body = { case_id: p.case_id, option_id: p.option_id, action: "return", fingerprint: p.fingerprint, actor: "president", reason: RET_TEXT };
        RET = false;
      } else if (p.kind === "approval") { path = "/api/decide"; body = { case_id: p.case_id, option_id: p.option_id, action: "approve", fingerprint: p.fingerprint, actor: "president" }; }
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
      SEL = null;
      waitReceipt = { prev, t: Date.now() };
      setHTML("receipt", `<div class="receipt pending">${icon("loader")}サーバーが記録を確かめています…</div>`);
    } else {
      setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}確定しませんでした：${esc((r.j.reasons || [r.j.detail || r.j.error || r.status]).join("・"))}</div>`);
      document.querySelectorAll("#acts .dbtn").forEach((x) => (x.disabled = false));
    }
  });
  $("acts").addEventListener("input", (e) => { if (e.target.id === "othertext") OTHER_TEXT = e.target.value; });
  $("receipt").addEventListener("click", (e) => { if (e.target.closest("[data-rfold]")) { RFOLD = !RFOLD; drawReceipt(); } });
  $("bandothers").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-item]");
    if (b) location.search = `?item=${encodeURIComponent(b.dataset.item)}` + (ROLE === "president" ? "&role=president" : "");
  });
  $("opbtn").addEventListener("click", async () => {
    $("opbtn").disabled = true;
    const r = await fetch("/api/order", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(V.op.body) });
    if (!r.ok) $("opbtn").textContent = "（実演の操作）入れられませんでした";
    $("opbtn").disabled = false;
  });
  // 模擬の時計：触れると、実際に記録した時刻とは違うことと、写し方が開く
  $("clockbtn").addEventListener("click", () => {
    const k = V && V.clock;
    if (!k) return;
    openPop($("clockbtn"), { lead: k.note, sections: [{ title: "時刻", rows: [
      { text: `画面の時刻（模擬の時計）：${k.now_shown}`, origin: "code" },
      { text: `実際の時刻：${String(k.now_real).slice(11, 19)}`, origin: "record" },
      { text: k.anchor ? `業務の開始の実際の時刻：${String(k.anchor).slice(11, 19)} → 画面では ${String(k.start).slice(11, 16)}` : "業務はまだ始まっていません", origin: "record" },
      { text: "朝の注文・社長の指示・条件の期限は、もともと実演の朝の時刻で記録しているので、そのまま出します", origin: "code" }] }],
      more: [], speech_origin: null, origins: { code: "コードの決まり", record: "業務の記録" } });
  });
  $("flowbtn").addEventListener("click", async () => {
    const m = await getJSON(`/api/morning?owner=${OWNER}`);
    openPop($("flowbtn"), { lead: "今日の仕事です。手の要る順に並べています（コードの決まり）。", sections: [{ title: "今日の仕事", rows: m.jobs.map((j) => ({
      text: `［${j.step}］${j.title}（${j.done ? "済み" : j.who_label}）${j.connected ? "" : "・未接続"}`, origin: j.who === "you" ? "record" : "code" })) }],
      more: [{ title: "並べ方の決まり", rows: [{ text: m.rule, origin: "code" }] }], speech_origin: null, origins: { record: "あなたが決める", code: "業務の記録から" } });
  });


  // ═════ 聞く・頼む（仲間へのチャット）═════
  // 答えはサーバーが検査した後のものだけを描く（根拠＝この回に取得した記録・数字＝コードと照合済み）。
  // 送る途中で失敗したら、入力と冪等のキーを残す：もう一度送ると同じキーで送り直し、サーバーは二重に処理しない。
  const EV_ICON = { directive: "user-round", rule: "shield-check", knowledge: "notebook-text", experience: "notebook-text", not_current: "notebook-text",
    reply: "mail", record: "clipboard-list", calc: "receipt-japanese-yen" };
  const ORG = { "岩瀬漁協": "漁協の回答", "B社": "B社の回答", "C社": "C社の回答" };
  let chatTurns = [], chatPending = null, chatBusy = false, chatKind = "mock", chatRun = false, histOpen = false, chatSig = null;
  const runKeys = {};

  function evLabel(e) {
    if (e.type === "reply") { const o = Object.keys(ORG).find((n) => e.text.startsWith(n)); return o ? ORG[o] : "相手の回答"; }
    if (e.type === "calc") return e.ref.startsWith("calc:preview") ? "下見の計算" : e.ref.startsWith("calc:ceiling") ? "採算の計算" : "数の計算";
    if (e.type === "not_current") return (e.src && e.src.category === "社長の指示") ? "期限切れの指示" : "今は効力のない記録";
    return e.type_label;
  }
  // 根拠の束：main＝結論と次の行動を支える根拠（最初に見せる）／more＝詳しい説明を支える根拠（「詳しく」の中）
  const isMain = (e) => !e.past && (e.supports === "conclusion" || e.supports === "next_action" || !e.supports);
  function evGroups(a, which) {
    const g = [];
    a.evidence.filter((e) => !e.past && (which === "more" ? !isMain(e) : isMain(e))).forEach((e) => { const l = evLabel(e); let x = g.find((y) => y.label === l); if (!x) { x = { label: l, type: e.type, items: [] }; g.push(x); } x.items.push(e); });
    return g;
  }
  // 根拠を開く：該当の文・日付・確認の時刻・出所（記録にある事実／コードの計算／AI の解釈）
  function evCard(a, group) {
    const list = group ? group.items : a.evidence;
    const secs = list.map((e) => {
      const s = e.src || {};
      const rows = [{ text: e.quote, origin: e.origin === "calc" ? "calc" : "fact", q: true }];
      if (e.quote_from === "record" && e.type !== "calc" && e.quote !== e.text) rows.push({ text: e.text, origin: "fact" });
      if (e.merged) rows.push({ text: "同じ指示の記録（1つにまとめて表示）：" + e.merged.map((m) => `${m.label} ${m.ref}`).join("・"), origin: "code" });
      if (e.conflict) rows.push({ text: `食い違い：${e.conflict} と内容が違います。社長に確かめてください`, origin: "code" });
      if (s.vault) rows.push({ text: `Vault ${s.vault}・${s.category}・出所：${s.source}・対象：${s.subject}`, origin: "fact" },
        { text: `記録日 ${s.recorded_on}・適用 ${s.valid_from}〜${s.valid_to || "期限なし"}（実演の日 ${"2026-11-02"} に${e.validity}）`, origin: "fact" },
        { text: s.fictional, origin: "code" });
      else if (e.at) rows.push({ text: `記録の時刻 ${String(e.at).slice(0, 10)} ${String(e.at).slice(11, 16)}`, origin: "fact" });
      if (s.db) rows.push({ text: `業務 DB：${s.db === "market" ? "市場" : "自社"}の ${s.table}${s.id != null ? " " + s.id : ""}`, origin: "code" });
      rows.push({ text: `確認の時刻 ${String(e.checked_at).slice(11, 19)}（この答えのために、サーバーが記録を読んだ時刻）`, origin: "code" });
      return { title: `${evLabel(e)}・${e.title || e.ref}`, rows };
    });
    if (!group && a.interpretation) secs.push({ title: "AI が記録から考えたこと", rows: [{ text: a.interpretation, origin: "ai" }] });
    if (!group && a.notes && a.notes.length) secs.push({ title: "サーバーの検査", rows: a.notes.map((n) => ({ text: n, origin: "code" })) });
    return { lead: a.conclusion, sections: secs, more: [], speech_origin: null,
      origins: { fact: "記録にある事実", calc: "コードの計算", ai: "AI の解釈", code: "サーバーが書いた文" } };
  }

  function bubbleHTML(t, latest) {
    const a = t.answer;
    if (!a) return "";
    if (t.since && t.since.length) {   // 聞いた後に業務の状態が変わった：その時の答えとして見せ、古い次の手と依頼は出さない
      return `<div class="q">${icon("user-round")}${esc(t.question)}</div><div class="bubble past" data-turn="${t.turn_id ?? t.id}">
        <div class="pastnote">${icon("clock")}${t.asked_shown ? esc(t.asked_shown) + " の" : "前の"}答えです。その後、${esc(t.since.join("・"))}がありました。いまの状態は、上の画面のとおりです。もう一度聞くと、いまの記録で答えます。</div>
        <div class="con">${esc(a.conclusion)}</div></div>`;
    }
    let h = `<div class="q">${icon("user-round")}${esc(t.question)}</div><div class="bubble" data-turn="${t.turn_id ?? t.id}">
      <span class="kind ${esc(a.kind)}">${esc(a.kind_label)}</span>${chatKind === "mock" ? `<span class="mock">模擬の AI の答え</span>` : ""}
      <div class="con">${esc(a.conclusion)}</div><div class="nx">${icon("arrow-right")}${esc(a.next_action)}</div>` +
      (a.deadline ? `<div class="dl">${icon("clock")}${esc(a.deadline)}</div>` : "");
    const moreG = evGroups(a, "more");
    if (latest && (a.detail || a.interpretation || moreG.length)) h += `<div class="det" hidden>${a.detail ? `<div>${esc(a.detail)}</div>` : ""}${a.interpretation ? `<div class="ai">${esc(a.interpretation)}</div>` : ""}` +
      (moreG.length ? `<div class="row2">` + moreG.map((g, i) => `<button class="evchip ${esc(g.type)}" data-evmore="${i}">${icon(EV_ICON[g.type] || "file-search")}${esc(g.label)}${g.items.length > 1 ? `<small>${g.items.length}件</small>` : ""}</button>`).join("") + `</div>` : "") + `</div>`;
    if (latest && a.entry && a.entry.kind === "why") h += `<div class="row2"><button class="pbtn" data-entry="why">${icon("circle-help")}${esc(a.entry.label)}</button></div>`;
    if (latest && a.notes && a.notes.length) h += `<div class="notes">${a.notes.map(esc).join("<br>")}</div>`;
    if (latest) {
      h += `<div class="row2">` + evGroups(a).map((g, i) => `<button class="evchip ${esc(g.type)}" data-ev="${i}">${icon(EV_ICON[g.type] || "file-search")}${esc(g.label)}${g.items.length > 1 ? `<small>${g.items.length}件</small>` : ""}</button>`).join("") +
        (a.detail || a.interpretation || moreG.length ? `<button class="linkbtn" data-det="1">${icon("chevron-down")}詳しく</button>` : "") +
        (a.evidence.length ? `<button class="linkbtn" data-evall="1">${icon("file-search")}根拠を全部開く</button>` : "") +
        (a.evidence.some((e) => e.past) ? `<button class="linkbtn" data-past="1">${icon("notebook-text")}過去の指示との違い</button>` : "") + `</div>`;
      const r = a.request;
      if (r && !t.executed && !chatRun) {
        h += `<div class="reqbox"><p>${esc(r.confirm_text)}</p><small>チャットからの実行は、まだ使えません。進める時は、画面のボタン（${esc(r.label)}）を押してください。</small></div>`;
      } else if (r && !t.executed) {
        h += `<div class="reqbox"><p>${esc(r.confirm_text)}</p><small>実行する操作：${esc(r.label)}${r.president ? "（社長の確認へ進みます）" : ""}・押すまで、何も実行しません</small>
          <div class="btnrow"><button class="pbtn" data-run="1">${icon("stamp")}この内容で頼む</button><button class="sbtn" data-cancel="1">${icon("x")}やめる</button></div></div>`;
      } else if (r && t.executed) {
        h += `<div class="notes">［この内容で頼む］を押しました（サーバーの結果：${esc((t.executed.result || {}).result || (t.executed.result || {}).detail || t.executed.status)}）</div>`;
      }
    }
    return h + `</div>`;
  }

  function drawChat() {
    const latest = chatTurns.find((t) => t.status === "answered");
    const log = $("chatlog");
    const html = latest ? bubbleHTML(latest, true) : "";
    if (log._h !== html) {
      log.innerHTML = html; log._h = html;
      const b = log.querySelector(".bubble");
      if (b && !REDUCED && log._last !== (latest.turn_id ?? latest.id)) b.animate([{ opacity: 0, transform: "translateY(8px)" }, { opacity: 1, transform: "none" }], { duration: 360, easing: "ease-out" });
      log._last = latest && (latest.turn_id ?? latest.id);
    }
    const older = chatTurns.filter((t) => t !== latest && t.status === "answered");
    $("chatmore").hidden = !older.length;
    $("chatmore").className = "linkbtn chatmore";
    $("chatmore").innerHTML = `${icon("chevron-down")}${histOpen ? "これまでの対話を閉じる" : `これまでの対話（${older.length}件）を開く`}`;
    $("chathist").hidden = !histOpen || !older.length;
    $("chathist").innerHTML = older.map((t) => `<div class="h"><b>${esc(t.question)}</b>${esc(t.answer.kind_label)}：${esc(t.answer.conclusion)}</div>`).join("");
  }

  async function loadChat() {
    try { const j = await getJSON(`/api/chat?owner=${OWNER}&item=${encodeURIComponent(ITEM)}`); chatTurns = j.turns.map((t) => Object.assign({ turn_id: t.id }, t)); chatKind = j.run_kind || chatKind; chatRun = !!j.run_enabled; drawChat(); }
    catch (e) { /* keep */ }
  }

  function chatState(html, bad) { $("chatstate").innerHTML = html; $("chatstate").className = "chatstate" + (bad ? " bad" : ""); }

  $("chatform").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const text = $("chatin").value.trim();
    if (!text || chatBusy) return;
    if (!chatPending || chatPending.text !== text) chatPending = { text, key: key() };   // 同じ文の送り直しは同じキー
    chatBusy = true; $("chatsend").disabled = true;
    chatState(`<span class="ic spin">${(window.DOCK_ICONS || {})["loader"] || ""}</span>考えています…（業務の記録と Vault を読んでいます）`);
    let r;
    try { r = await post("/api/chat", { item: ITEM, text, idempotency_key: chatPending.key }); }
    catch (e) { r = { ok: false, status: 0, j: {} }; }
    chatBusy = false; $("chatsend").disabled = false;
    if (r.ok && r.j.answer) {
      chatPending = null; $("chatin").value = ""; chatState("");
      chatKind = r.j.run_kind || chatKind; chatRun = !!r.j.run_enabled;
      chatTurns = [r.j].concat(chatTurns.filter((t) => (t.turn_id ?? t.id) !== r.j.turn_id));
      drawChat();
    } else if (r.status === 400) {
      chatPending = null; chatState(`${icon("circle-alert")}送れませんでした：${esc(r.j.detail || r.j.error)}`, true);
    } else {
      chatState(`${icon("circle-alert")}届きませんでした。入力はそのままです。もう一度［送る］を押すと、同じ内容で送り直します（二重には処理しません）`, true);
    }
  });

  $("chatlog").addEventListener("click", async (e) => {
    const t = chatTurns.find((x) => x.status === "answered");
    if (!t) return;
    const a = t.answer;
    const ev = e.target.closest("[data-ev]");
    if (ev) return openPop(ev, evCard(a, evGroups(a)[+ev.dataset.ev]));
    const evm = e.target.closest("[data-evmore]");
    if (evm) return openPop(evm, evCard(a, evGroups(a, "more")[+evm.dataset.evmore]));
    if (e.target.closest("[data-entry]") && V) return openPop(e.target.closest("[data-entry]"), V.why);   // 見るだけ（配分の比較・何も実行しない）
    if (e.target.closest("[data-evall]")) return openPop(e.target.closest("[data-evall]"), evCard(a, null));
    if (e.target.closest("[data-past]")) return openPop(e.target.closest("[data-past]"), evCard(a, { items: a.evidence.filter((x) => x.past) }));
    if (e.target.closest("[data-det]")) { const d = $("chatlog").querySelector(".det"); if (d) d.hidden = !d.hidden; return; }
    if (e.target.closest("[data-cancel]")) { t.executed = { result: { result: "やめました（何も実行していません）" }, status: 0 }; $("chatlog")._h = null; drawChat(); return; }
    const run = e.target.closest("[data-run]");
    if (run) {
      run.disabled = true;
      const id = t.turn_id ?? t.id;
      runKeys[id] = runKeys[id] || key();   // 押し直しは同じキー（二重に実行しない）
      const prev = V && V.receipt ? V.receipt.at : null;
      setHTML("receipt", `<div class="receipt pending">${icon("loader")}送っています…</div>`);
      let r;
      try { r = await post("/api/chat/run", { turn_id: id, idempotency_key: runKeys[id] }); } catch (x) { r = { ok: false, status: 0, j: {} }; }
      if (r.ok && r.j.result !== "stale" && r.j.result !== "not_executable") {
        t.executed = { result: r.j, status: r.status };
        waitReceipt = { prev, t: Date.now() };
        setHTML("receipt", `<div class="receipt pending">${icon("loader")}サーバーが記録を確かめています…</div>`);
      } else if (r.status === 0) {
        run.disabled = false;
        setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}届きませんでした。もう一度押すと、同じ依頼を送り直します（二重には実行しません）</div>`);
      } else {
        t.executed = { result: r.j, status: r.status };
        setHTML("receipt", `<div class="receipt bad">${icon("circle-alert")}実行しませんでした：${esc((r.j.reasons || [r.j.detail || r.j.error || r.j.result || r.status]).join("・"))}</div>`);
      }
      $("chatlog")._h = null; drawChat();
    }
  });
  $("chatmore").addEventListener("click", () => { histOpen = !histOpen; drawChat(); });
  if (ROLE === "president") $("chat").hidden = true; else loadChat();

  // a small hook for the screenshot script: which stance and state is on screen
  window.__mate = () => ({ exec: V && V.exec, stance: fig && fig.stance, phase, pop: !$("pop").hidden, busy: !!(V && V.busy), chatBusy, chatTurns: chatTurns.length });
  tick();
})();
