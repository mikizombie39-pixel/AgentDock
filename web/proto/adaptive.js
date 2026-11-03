// Agent Dock — Adaptive Workspace prototype: the screen.
// Draws only from the engine state (engine.js). The centre changes only when the human acts or when the
// question on it changes its own state; background work updates in place on the left and the right.
(function () {
  "use strict";
  const E = window.ADEngine;
  const $ = (id) => document.getElementById(id);
  const stage = $("stage");
  const params = new URLSearchParams(location.search);
  const FRAME = params.get("frame");
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const quick = reduce || !!FRAME;
  const yen = (n) => n.toLocaleString("ja-JP") + "円";

  const SV = (body, extra = "") => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ${extra}>${body}</svg>`;
  const ICON = {
    ask: SV('<path d="M12 2 22 12 12 22 2 12Z"/><path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4"/><circle cx="12" cy="16.6" r=".6" fill="currentColor"/>'),
    work: SV('<path d="M12 3a9 9 0 1 1-8.5 6"/>'),
    nego: SV('<path d="M4 8h14l-3-3M20 16H6l3 3"/>'),
    agree: SV('<circle cx="9" cy="12" r="5"/><circle cx="15" cy="12" r="5"/>'),
    seal: SV('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 9v6M9 12h6"/>'),
    lock: SV('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
    check: SV('<path d="m5 12 5 5 9-10"/>'),
    warn: SV('<path d="M12 3 22 20H2Z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".6" fill="currentColor"/>'),
    pause: SV('<path d="M9 5v14M15 5v14"/>'),
    clock: SV('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
    vault: SV('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3Z"/><path d="M5 17a3 3 0 0 1 3-3h11"/>'),
    hand: SV('<path d="M4 12h12M12 6l6 6-6 6"/>'),
    fish: '<svg viewBox="0 0 40 22" fill="currentColor"><path d="M2 11c6-9 20-9 27 0-7 9-21 9-27 0Z"/><path d="M29 11l9-7v14Z"/><circle cx="9" cy="9" r="1.6" fill="#fff"/></svg>',
  };
  const ic = (n) => `<span class="ic">${ICON[n]}</span>`;
  document.querySelectorAll("[data-icon]").forEach((el) => (el.innerHTML = ICON[el.dataset.icon]));

  const ST = {
    idle: ["待機", "pause", ""], preparing: ["準備中", "work", "s-work"], watching: ["セリを見ています", "work", "s-work"],
    bidding: ["入札中", "work", "s-work"], negotiating: ["交渉中", "nego", "s-work"], paused: ["相談中", "ask", "s-ask"],
    won: ["開札", "work", "s-work"], agreed: ["合意・承認の前", "agree", "s-agree"], approved: ["承認済み", "seal", "s-done"],
    done: ["登録済み", "lock", "s-done"], exception: ["例外", "warn", "s-bad"],
  };
  const NEXT = { shiroBid: "シロエビの入札", seriDone: "ブリのセリの終わり", shiroOpen: "シロエビの開札", ryoteiOrder: "", askAlloc: "" };
  const KEEP = ["正式登録", "合意", "注文", "押さえ直し", "開札", "停止"];

  let S = E.create();
  let speed = 1;
  const V = { centerKey: null, sel: null, echo: null, echoUntil: 0, mark: null, digest: null, railCount: 0, sandFor: null,
    flash: {}, settleSrc: {}, linkFor: null, err: null, laneSig: null, popDeal: false, splitFrom: null };

  // ---- scale the 1600x900 stage to the window ----
  let K = 1;
  function fit() { K = Math.min(innerWidth / 1600, innerHeight / 900); stage.style.transform = `scale(${K})`; }
  addEventListener("resize", fit); fit();
  const relTo = (el, base) => { const a = rectIn(el), b = rectIn(base); return { x: a.x - b.x, y: a.y - b.y }; };
  const rectIn = (el) => { const r = el.getBoundingClientRect(), s = stage.getBoundingClientRect();
    return { x: (r.left - s.left) / K, y: (r.top - s.top) / K, w: r.width / K, h: r.height / K }; };

  // ---- actions ----
  function act(a) {
    const ok = E.act(S, a);
    V.err = ok ? null : S.lastError;
    if (ok && a.choice === "later") { V.mark = S.audit.length; V.sel = null; }
    if (ok && a.type === "answer" && a.choice !== "later") V.sel = null;
    if (ok && a.type === "reopen") showDigest();
    V.centerKey = null; // re-draw the centre for this human action
    frame();
  }
  function showDigest() {
    if (V.mark == null) return;
    const short = (x) => x.action === "正式登録" ? { t: x.t, action: "登録", detail: x.detail.split(" ").slice(0, 2).join(" ") }
      : x.action === "合意" ? { t: x.t, action: "合意", detail: x.detail.split("・")[0] } : x;
    const items = S.audit.slice(V.mark).filter((x) => KEEP.includes(x.action) && x.action !== "開札").map(short).slice(-3);
    V.digest = items.length ? items : [{ t: E.hhmm(S.t), action: "変化", detail: "ありません" }];
    V.mark = null;
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { if (E.openQuestions(S).length && V.mark == null) V.mark = S.audit.length; }
    else if (V.mark != null) { showDigest(); V.centerKey = null; }
  });
  document.querySelectorAll("[data-speed]").forEach((b) => b.addEventListener("click", () => {
    speed = +b.dataset.speed; document.querySelectorAll("[data-speed]").forEach((x) => x.classList.toggle("on", x === b));
  }));
  $("dock").addEventListener("click", () => { const q = E.openQuestions(S)[0]; if (q) act({ type: "reopen", q: q.id }); });
  $("logBtn").addEventListener("click", () => { $("log").hidden = false; drawLog(); });
  $("logClose").addEventListener("click", () => ($("log").hidden = true));

  // ---- centre ----
  function centerMode() {
    if (S.phase === "delegate") return { key: "delegate", kind: "delegate" };
    const open = E.openQuestions(S);
    const active = open.find((q) => !q.deferred);
    if (active) return { key: `q:${active.id}:${active.version}:${V.digest ? 1 : 0}`, kind: active.kind, q: active };
    const echoOn = V.echo && performance.now() < V.echoUntil;
    const b = S.buri;
    if (b.alloc && (echoOn || ["negotiating", "agreed", "approved"].includes(b.status)))
      return { key: `deal:${b.status}:${b.msgs.length}:${echoOn ? 1 : 0}`, kind: "deal" };
    const deferred = open.filter((q) => q.deferred);
    const o = S.orders;
    const g = [o.super.got, o.shiro.got, o.ryotei ? o.ryotei.got : "-", o.ryotei && o.ryotei.declined ? "x" : ""].join(",");
    if (S.phase === "done") return { key: `done:${g}`, kind: "done" };
    return { key: `watch:${g}:${deferred.map((q) => q.id).join()}:${nextLabel()}:${V.digest ? 1 : 0}:${b.status}`, kind: "watch", deferred };
  }
  function nextLabel() {
    const n = S.agenda.slice().sort((a, b) => a.at - b.at).find((x) => NEXT[x.name]);
    return n ? `次：${E.hhmm(n.at)} ${NEXT[n.name]}` : "";
  }

  function gaugesHTML() {
    const row = (lbl, got, qty, unit, cls = "", note = "") => {
      let cells = ""; for (let i = 0; i < qty; i++) cells += `<span class="cell ${i < got ? "got" : ""}">${i < got ? "✓" : ""}</span>`;
      return `<div class="gauge ${cls}"><span class="lbl">${lbl}</span><span class="cells">${cells}</span><span class="cnt">${got}/${qty}${unit}${note}</span></div>`;
    };
    const o = S.orders;
    let h = row("ブリ スーパー", o.super.got, o.super.qty, "本") + row("シロエビ むき身", o.shiro.got, o.shiro.qty, "箱");
    if (o.ryotei) h += row("ブリ 料亭", o.ryotei.got, o.ryotei.qty, "本", o.ryotei.got || o.ryotei.declined ? "" : "new",
      o.ryotei.declined ? "・見送り" : S.buri.trade && o.ryotei.got < o.ryotei.qty ? `・${o.ryotei.qty - o.ryotei.got}本は料亭へ連絡が要る` : "");
    return `<div class="gauges">${h}</div>`;
  }
  const digestHTML = () => V.digest ? `<div class="digest"><b>離れている間に</b>${V.digest.map((x) => `<span>${x.t} ${x.action}：${x.detail}</span>`).join("")}</div>` : "";

  function drawCenter(m) {
    const c = $("center");
    clearLinks();
    if (m.kind === "delegate") {
      c.innerHTML = `<div class="panel calm"><div class="deal" style="width:720px;text-align:left">
        <div class="dh">${ic("hand")}今朝、任せる仕事</div>
        <div class="split" data-split="buri" style="margin-top:16px;font-size:19px"><b>ブリ（特大）</b>　スーパー 4本・上限 ${yen(E.ceiling("スーパー"))}/本
          <div style="font-size:13px;color:var(--mute)">上限＝売値 22,000円 − 費用 2,000円 − 利益 1,500円（コードの計算）</div></div>
        <div class="split" data-split="shiro" style="margin-top:12px;font-size:19px"><b>シロエビ</b>　むき身 5箱・入札の上限 4,800円・8箱まで</div>
        <p style="font-size:16px;margin:18px 0 0">範囲の中は AI が進めます。範囲の外・注文の変化・承認は、あなたに聞きます。</p>
        <div class="acts"><button class="go" id="goDelegate">${ic("hand")}任せる</button></div></div></div>`;
      $("goDelegate").onclick = () => { V.splitFrom = [...c.querySelectorAll("[data-split]")].map((el) => [el.dataset.split, rectIn(el)]); act({ type: "delegate" }); };
      return;
    }
    if (m.kind === "alloc") return drawAlloc(c, m.q);
    if (m.kind === "approve" || m.kind === "deal") return drawDeal(c, m);
    if (m.kind === "done") {
      c.innerHTML = `<div class="panel calm">${ic("check").replace('class="ic"', 'class="ic" style="width:64px;height:64px;color:var(--ok)"')}
        <div class="big">今朝の仕入れは終わりました</div><div class="sub">あなたの判断 1件・承認 1件・AI が範囲の中で登録 ${S.settled.filter((x) => x.kind === "trade").length - (S.buri.trade ? 1 : 0)}件</div>${gaugesHTML()}
        ${S.vault.length ? `<div class="next">${ic("vault")} 判断と結果を Vault に残しました</div>` : ""}</div>`;
      return;
    }
    // watch: nothing needs the human — say so, show what the work is for
    const d = m.deferred && m.deferred[0];
    c.innerHTML = `<div class="panel calm">
      ${d ? `<div class="deferbar">${ic("ask")} 判断待ち：${d.title}（${E.hhmm(d.openedAt)} から）<button id="reopen">開く</button></div>` : digestHTML()}
      ${S.buri.status === "exception" ? `<div class="warnmark ${V.flash.buri ? "flash" : ""}">${ic("warn")}例外</div>` : ""}
      <div class="avatar working" style="width:76px;height:76px">${ic("work")}</div>
      <div class="big">AI が進めています</div>
      <div class="sub">${d ? "判断待ちの問いは残してあります。ほかの仕事は進めます。" : "いま、あなたの判断が要る仕事はありません。"}</div>
      ${gaugesHTML()}<div class="next">${nextLabel()}</div></div>`;
    if (d) $("reopen").onclick = () => act({ type: "reopen", q: d.id });
  }

  function drawAlloc(c, q) {
    const opts = E.buriOptions(S);
    const holdLeft = S.buri.hold ? Math.max(0, Math.ceil(S.buri.hold.until - S.t)) : 0;
    const rec = opts.find((o) => o.key === q.rec);
    c.innerHTML = `<div class="panel">${digestHTML()}
      <div class="ask ${V.digest ? "has-digest" : ""}" id="askCard">
        <div class="askHead">${ic("ask")}<span class="ttl">ブリの配分を決めてください</span>
          <span class="timer">${ic("clock")}仮押さえ ${S.buri.hold ? E.hhmm(S.buri.hold.until) : "-"} まで<b id="holdLeft">（残り ${holdLeft}分）</b></span></div>
        <div class="facts">
          <div class="fact new"><small>${E.hhmm(20)} 新着</small><span>料亭から <b>追加 6本</b></span></div>
          <div class="fact"><small>漁協の残り</small><span><b>6本</b> に 注文 <b>10本</b></span></div>
          <div class="fact bad ${V.flash["order-ryotei"] ? "flash" : ""}">${ic("warn")}<span><b>4本</b> 不足</span></div>
        </div>
        <div class="sd" id="sd">
          <div class="side supply"><div class="lbl"><b>漁協の残り 6本</b></div><div class="pool" id="pool">${"<div class='dslot'></div>".repeat(6)}</div></div>
          <div class="side demand">
            <div class="drow"><div class="lbl"><b>スーパー</b> いつもの注文 4本<span class="need" data-need="スーパー"></span></div><div class="slots" data-c="スーパー">${"<div class='dslot'></div>".repeat(4)}</div></div>
            <div class="drow"><div class="lbl"><b>料亭</b> 追加の注文 6本<span class="need" data-need="料亭"></span></div><div class="slots" data-c="料亭">${"<div class='dslot'></div>".repeat(6)}</div></div>
          </div>
        </div>
        <div class="opts">${opts.map((o) => optHTML(o, q)).join("")}</div>
        <div class="recline">${ic("check")}<span><b>AI のおすすめ：${rec.label}</b>　いつものスーパーへの 4本を守ります</span>
          <details class="why2"><summary>根拠</summary>Vault（網谷さんの記録・模擬）：去年 11月、スーパーの定番を欠品させた翌週、棚を半分にされた。</details></div>
        <details class="calc"><summary>単価と計算の根拠</summary>
          <table>${opts.map((o) => `<tr><td>${o.label}</td><td>${o.qty}本 × ${yen(o.unit)}</td><td>${o.basis}</td></tr>`).join("")}</table>
          <div>粗利 ＝ 売値（スーパー 22,000円・料亭 24,000円）− 費用 2,000円 − 仕入れ。コードが計算。</div></details>
        <div class="acts"><button class="go" id="goAlloc" ${V.sel ? "" : "disabled"}>${ic("nego")}${V.sel ? "この配分で再交渉" : "配分を選んでください"}</button>
          <button class="later" id="later">あとで</button><span class="err">${V.err || ""}</span></div>
      </div></div>`;
    const sd = $("sd"), pool = $("pool");
    const home = [...pool.children].map((el) => relTo(el, sd));
    home.forEach((h, i) => { const f = document.createElement("div"); f.className = "fish"; f.dataset.i = i; f.style.left = h.x + "px"; f.style.top = h.y + "px"; f.innerHTML = ICON.fish + '<span class="tag"></span>'; sd.appendChild(f); });
    placeFish(opts.find((o) => o.key === V.sel));
    c.querySelectorAll(".opt").forEach((b) => b.addEventListener("click", () => {
      V.sel = b.dataset.k; V.err = null;
      c.querySelectorAll(".opt").forEach((x) => x.classList.toggle("sel", x === b));
      const go = $("goAlloc"); go.disabled = false; go.innerHTML = ic("nego") + "この配分で再交渉";
      placeFish(opts.find((x) => x.key === V.sel));
    }));
    $("goAlloc").onclick = () => V.sel && act({ type: "answer", q: q.id, choice: V.sel });
    $("later").onclick = () => act({ type: "answer", q: q.id, choice: "later" });
    if (V.sandFor === q.id) { V.sandFor = null; sand($("askCard")); }
  }
  function optHTML(o, q) {
    const bar = [];
    for (let i = 0; i < o.alloc.スーパー; i++) bar.push('<i class="s">ス</i>');
    for (let i = 0; i < o.alloc.料亭; i++) bar.push('<i class="r">料</i>');
    for (let i = o.qty; i < 6; i++) bar.push('<i class="x">－</i>');
    const parts = [o.short.スーパー ? ["スーパー", o.short.スーパー] : null, o.short.料亭 ? ["料亭", o.short.料亭] : null].filter(Boolean);
    const short = parts.length > 1 ? parts.map(([c, n]) => c + n).join("・") : parts.map(([c, n]) => `${c} ${n}本`).join("");
    return `<button class="opt ${V.sel === o.key ? "sel" : ""}" data-k="${o.key}" ${o.executable ? "" : "disabled"}>
      ${q.rec === o.key ? '<span class="rec">おすすめ</span>' : ""}
      <span class="on">${o.label}</span><span class="bar">${bar.join("")}</span>
      <span class="sh">${o.alloc.料亭 ? "不足" : "断る"}：${short}</span>
      <span class="mg">粗利 <b>${yen(o.margin)}</b></span></button>`;
  }
  function placeFish(o) {
    const sd = $("sd"); if (!sd) return;
    const fish = [...sd.querySelectorAll(".fish")];
    const slot = (c, i) => relTo(sd.querySelector(`.slots[data-c="${c}"]`).children[i], sd);
    sd.querySelectorAll(".dslot").forEach((s) => s.classList.remove("empty", "show"));
    let k = 0;
    const assign = [];
    if (o) { for (let i = 0; i < o.alloc.スーパー; i++) assign.push(["スーパー", i]); for (let i = 0; i < o.alloc.料亭; i++) assign.push(["料亭", i]); }
    fish.forEach((f) => {
      const a = assign[k++];
      f.classList.remove("to-s", "to-r"); f.querySelector(".tag").textContent = "";
      if (!a) { f.style.transform = ""; f.style.opacity = o ? ".35" : "1"; if (o) f.querySelector(".tag").textContent = "買わない"; return; }
      const t = slot(a[0], a[1]);
      f.style.opacity = "1";
      f.style.transform = `translate(${t.x - parseFloat(f.style.left)}px, ${t.y - parseFloat(f.style.top)}px)`;
      f.classList.add(a[0] === "スーパー" ? "to-s" : "to-r"); f.querySelector(".tag").textContent = a[0] === "スーパー" ? "ス" : "料";
    });
    sd.querySelectorAll(".need").forEach((n) => {
      const c = n.dataset.need;
      if (!o) { n.className = "need"; n.textContent = ""; return; }
      const short = o.short[c];
      n.className = "need " + (short ? (o.alloc[c] || c === "スーパー" ? "lack" : "decline") : "full");
      n.innerHTML = short ? (o.alloc[c] === 0 && c === "料亭" ? `${ic("warn")}断る` : `${ic("warn")}${short}本 不足`) : `${ic("check")}そろう`;
    });
    if (o) ["スーパー", "料亭"].forEach((c) => { const row = sd.querySelector(`.slots[data-c="${c}"]`); [...row.children].forEach((el, i) => { if (i >= o.alloc[c]) el.classList.add("empty", "show"); }); });
  }

  function drawDeal(c, m) {
    const b = S.buri, a = b.agreement;
    const echoOn = V.echo && performance.now() < V.echoUntil;
    const st = ST[b.status] || ST.negotiating;
    const cls = b.status === "agreed" ? "agreed" : b.status === "approved" ? "approved" : "";
    const bubbles = b.msgs.slice(m.kind === "approve" ? -2 : -4).map((x, i, arr) => `<div class="bub ${x.from === "網谷" ? "me" : "them"} ${i === arr.length - 1 && !quick ? "pop" : ""}"><small>${x.from === "網谷" ? "網谷水産の AI" : "岩瀬漁協の AI"}</small>${x.text}</div>`).join("");
    let body = "";
    if (a && (m.kind === "approve" || b.status === "approved")) {
      const ceilS = E.ceiling("スーパー"), ceilR = E.ceiling("料亭");
      const exp = a.qty === 6 ? E.CFG.buri.expect6 : E.CFG.buri.firstAsk4;
      const holdOk = b.hold && S.t <= b.hold.until;
      body = `<div class="terms">${ic("agree")}<span>ブリ ${a.qty}本 × ${yen(a.unit)}</span><span class="amt">${yen(a.amount)}</span><span>粗利の見込み ${yen(a.margin)}</span></div>
        <div class="links" id="ochips">${a.alloc.スーパー ? `<span class="ochip s ln">スーパー ${a.alloc.スーパー}本 ／ 注文 4本</span>` : ""}${a.alloc.料亭 ? `<span class="ochip r ln">料亭 ${a.alloc.料亭}本 ／ 注文 6本</span>` : ""}${!a.alloc.料亭 ? '<span class="ochip off">料亭：見送り（断りの連絡が要る）</span>' : ""}</div>
        <div class="checks">
          <div class="ok">${ic("check")}上限の内（スーパー ${yen(ceilS)}・料亭 ${yen(ceilR)}）</div>
          <div class="ok">${ic("check")}承認の権限（${E.CFG.approver.name}）</div>
          <div class="${holdOk ? "ok" : "ng"}">${ic(holdOk ? "check" : "warn")}仮押さえ ${b.hold ? E.hhmm(b.hold.until) : "-"} まで<b id="holdLeft"></b></div>
          <div class="ok">${ic("check")}合意の版 ${a.version}（承認はこの版に結びつきます）</div></div>
        ${a.unit !== exp ? `<div class="diff">${ic("warn")} 見込み ${yen(exp)} → 合意 ${yen(a.unit)}（1本 ${a.unit > exp ? "+" : ""}${(a.unit - exp).toLocaleString()}円・粗利 ${a.unit > exp ? "−" : "+"}${Math.abs((a.unit - exp) * a.qty).toLocaleString()}円）</div>` : ""}`;
    }
    const acts = m.kind === "approve" ? `<div class="acts"><button class="go seal" id="approve">${ic("seal")}承認する</button><button class="decline" id="decline">見送る</button><button class="later" id="later2">あとで</button><span class="err">${V.err || ""}</span></div>` : "";
    c.innerHTML = `<div class="panel">${m.kind === "approve" ? digestHTML() : ""}
      ${echoOn ? `<div class="echo ${quick ? "" : "pop"}">${ic("check")}${V.echo}</div>` : ""}
      <div class="${m.kind === "approve" ? "ask" : ""} ${m.kind === "approve" && V.digest ? "has-digest" : ""}" id="askCard" style="${m.kind === "approve" ? "" : "margin-top:0;border:0;padding:0;background:none"}">
      ${m.kind === "approve" ? `<div class="askHead">${ic("seal")}<span class="ttl">承認をお願いします：ブリの取引</span></div>` : ""}
      <div class="deal ${cls} ${V.popDeal && !quick ? "pop" : ""}" id="dealCard">
        <div class="dh">${ic("fish")}ブリ（特大）の相対　<span style="font-size:15px;font-weight:normal;color:var(--mute)">岩瀬漁協の AI と</span>
          <span class="st ${st[2]}">${ic(st[1])}${st[0]}</span></div>
        <div class="bubbles">${bubbles}</div>${body}
        ${b.status === "approved" ? '<div class="stamp">承認</div>' : ""}</div>${acts}</div></div>`;
    V.popDeal = false;
    if (m.kind === "approve") {
      $("approve").onclick = () => act({ type: "answer", q: m.q.id, choice: "approve" });
      $("decline").onclick = () => act({ type: "answer", q: m.q.id, choice: "decline" });
      $("later2").onclick = () => act({ type: "answer", q: m.q.id, choice: "later" });
      if (V.sandFor === m.q.id) { V.sandFor = null; sand($("askCard")); }
      if (V.linkFor) { V.linkFor = null; } drawLinks();
    }
  }

  // relations: order chips tied to the agreed terms
  function clearLinks() { $("links").innerHTML = ""; }
  // drawn inside the card: a rail under the terms, and a short drop to each order the agreement fills
  function drawLinks() {
    const box = document.getElementById("ochips");
    if (!box) return;
    const linked = [...box.querySelectorAll(".ochip.ln")];
    box.querySelectorAll(".rail").forEach((r) => r.remove());
    if (!linked.length) return;
    const mid = (el) => el.offsetLeft + el.offsetWidth / 2;
    const rail = document.createElement("span");
    rail.className = "rail";
    const x1 = Math.min(18, mid(linked[0])), x2 = mid(linked[linked.length - 1]);
    rail.style.left = x1 + "px"; rail.style.width = Math.max(2, x2 - x1) + "px";
    box.appendChild(rail);
    if (!quick) {
      rail.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: 600, easing: "cubic-bezier(.2,.7,.2,1)" });
      linked.forEach((c, i) => c.animate([{ opacity: .3 }, { opacity: 1 }], { duration: 300, delay: 450 + i * 120, fill: "backwards" }));
    }
  }

  // sand gathers into the shape of the question (the AI is asking the human)
  function sand(el) {
    if (quick || !el) return;
    const r = rectIn(el), cv = $("sand"), g = cv.getContext("2d");
    el.style.opacity = "0";
    setTimeout(() => (el.style.opacity = "1"), 1600); // never leave the question hidden if frames stop
    const N = 520, P = [];
    for (let i = 0; i < N; i++) {
      const edge = Math.random() < .7;
      let tx, ty;
      if (edge) { const t = Math.random() * 2 * (r.w + r.h); tx = t < r.w ? r.x + t : t < r.w + r.h ? r.x + r.w : t < 2 * r.w + r.h ? r.x + (t - r.w - r.h) : r.x; ty = t < r.w ? r.y : t < r.w + r.h ? r.y + (t - r.w) : t < 2 * r.w + r.h ? r.y + r.h : r.y + (t - 2 * r.w - r.h); }
      else { tx = r.x + Math.random() * r.w; ty = r.y + Math.random() * r.h; }
      const fromAvatar = Math.random() < .35;
      P.push({ sx: fromAvatar ? 380 + Math.random() * 30 : 350 + Math.random() * 862, sy: fromAvatar ? 850 + Math.random() * 20 : 92 + Math.random() * 712, tx, ty, d: Math.random() * 250 });
    }
    const t0 = performance.now(), D = 950;
    (function step(now) {
      const e = now - t0; g.clearRect(0, 0, 1600, 900); g.fillStyle = "rgba(138,90,18,.75)";
      P.forEach((p) => { let k = Math.min(1, Math.max(0, (e - p.d) / D)); k = 1 - Math.pow(1 - k, 3); g.fillRect(p.sx + (p.tx - p.sx) * k, p.sy + (p.ty - p.sy) * k, 2.2, 2.2); });
      if (e > D * .75) el.style.opacity = String(Math.min(1, (e - D * .75) / (D * .5)));
      if (e < D + 300) requestAnimationFrame(step); else { g.clearRect(0, 0, 1600, 900); el.style.opacity = "1"; }
    })(t0);
  }

  // ---- lanes (background work) ----
  function drawLanes(mode) {
    const b = S.buri, sh = S.shiro, o = S.orders;
    const buriFocus = ["alloc", "approve", "deal"].includes(mode.kind);
    const lastB = b.trade ? `正式登録 ${b.trade.no}` : b.msgs.length ? b.msgs[b.msgs.length - 1].text : b.status === "watching" ? "セリは人が行います。結果を見ています" : "";
    const lastS = sh.status === "done" ? "5箱そろいました（入札 3・相対 2）" : sh.msgs.length ? sh.msgs[sh.msgs.length - 1] : sh.status === "preparing" ? "入札の値を計算しています" : "";
    let bs = ST[b.status] || ST.idle; if (b.status === "done" && !b.trade && b.agreement) bs = ["見送り", "check", "s-done"];
    let ss = ST[sh.status] || ST.idle; if (sh.status === "agreed") ss = ["合意（事前の承認の範囲）", "agree", "s-agree"];
    const slots = (got, qty, cls = "") => { let h = ""; for (let i = 0; i < qty; i++) h += `<span class="slot ${i < got ? "got" : ""} ${cls}"></span>`; return h; };
    const sig = [b.status, sh.status, lastB, lastS, o.super.got, o.shiro.got, o.ryotei && o.ryotei.got, buriFocus, S.phase].join("|");
    if (sig === V.laneSig) return; V.laneSig = sig;
    if (S.phase === "delegate") { $("laneList").innerHTML = '<div class="lane" style="color:var(--mute);font-size:14px">まだ任せていません</div>'; return; }
    $("laneList").innerHTML = `
      <div class="lane ${buriFocus ? "away" : ""} ${b.status === "paused" ? "attn" : ""}" data-lane="buri"><div class="row"><span class="nm">ブリ（特大）</span><span class="kind">相対</span><span class="st ${bs[2]}">${ic(bs[1])}${bs[0]}</span></div>
        <div class="msg">${lastB}</div><div class="fill">${slots(o.super.got, 4)}${o.ryotei ? '<span style="width:8px"></span>' + slots(o.ryotei.got, 6) : ""}</div></div>
      <div class="lane" data-lane="shiro"><div class="row"><span class="nm">シロエビ</span><span class="kind">入札 → 相対</span><span class="st ${ss[2]}">${ic(ss[1])}${ss[0]}</span></div>
        <div class="msg">${lastS}</div><div class="fill">${slots(o.shiro.got, 5)}</div></div>`;
    if (V.splitFrom && !quick) { // the delegation card splits into the two lanes
      V.splitFrom.forEach(([k, from]) => { const el = document.querySelector(`[data-lane="${k}"]`); if (!el) return; const to = rectIn(el);
        el.animate([{ transform: `translate(${from.x - to.x}px, ${from.y - to.y}px)`, opacity: .4 }, { transform: "none", opacity: 1 }], { duration: 650, easing: "cubic-bezier(.2,.7,.2,1)" }); });
    }
    V.splitFrom = null;
  }

  // ---- rail (decided things stay) ----
  function drawRail() {
    const items = [...S.settled.map((x) => ({ ...x })), ...S.vault.map((v, i) => ({ id: "vault" + i, kind: "vault", label: "Vault に記録", how: v.text, t: v.t }))];
    if (items.length === V.railCount) return;
    const list = $("railList");
    items.slice(V.railCount).forEach((x) => {
      const el = document.createElement("div");
      el.className = "rc " + x.kind; el.dataset.id = x.id;
      if (x.kind === "trade") el.innerHTML = `<div class="h">${ic("lock")}${x.label}<span class="t">${x.t}</span></div><div class="d">${yen(x.amount)}・正式登録 <span class="no">${x.no}</span></div><div class="d">${x.how}</div>`;
      else if (x.kind === "decision") el.innerHTML = `<div class="h">${ic("ask")}${x.label}<span class="t">${x.t}</span></div><div class="d">${x.how}</div>`;
      else el.innerHTML = `<div class="h">${ic("vault")}${x.label}<span class="t">${x.t}</span></div><div class="d">${x.how}</div>`;
      list.appendChild(el);
      const src = V.settleSrc[x.id];
      if (!quick) {
        if (src) { const to = rectIn(el); el.animate([{ transform: `translate(${src.x - to.x}px, ${src.y - to.y}px) scale(${Math.min(2, src.w / to.w)})`, opacity: .5, transformOrigin: "0 0" }, { transform: "none", opacity: 1, transformOrigin: "0 0" }], { duration: 700, easing: "cubic-bezier(.2,.7,.2,1)" }); }
        else el.classList.add("pop");
      }
      delete V.settleSrc[x.id];
    });
    V.railCount = items.length;
  }

  function drawLog() {
    $("apiList").innerHTML = S.api.map((x) => `<li>${x.t} ${x.call}　${x.body} → <b>${x.result}</b>${x.checks ? "<br>" + x.checks.map((c) => (c[1] ? "✓" : "✗") + c[0]).join("　") : ""}</li>`).join("");
    $("auditList").innerHTML = S.audit.map((x) => `<li>${x.t} [${x.actor}] ${x.action}：${x.detail}</li>`).join("");
  }

  // ---- one frame ----
  function handleCues() {
    E.takeCues(S).forEach((c) => {
      if (c.type === "ask") V.sandFor = c.target;
      if (c.type === "alert") V.flash[c.target] = true;
      if (c.type === "pop") V.popDeal = true;
      if (c.type === "echo") { V.echo = c.text; V.echoUntil = FRAME ? Infinity : performance.now() + 4500; V.digest = null; }
      if (c.type === "settle") { // capture where the thing is now, so it travels to the rail
        const src = c.target === "buri" ? document.getElementById("dealCard") : document.querySelector(`[data-lane="shiro"]`);
        if (src) V.settleSrc[c.target] = rectIn(src);
      }
    });
  }
  function frame() {
    handleCues();
    const m = centerMode();
    if (m.key !== V.centerKey) { V.centerKey = m.key; drawCenter(m); }
    else { const hl = $("holdLeft"); if (hl && S.buri.hold) hl.textContent = `（残り ${Math.max(0, Math.ceil(S.buri.hold.until - S.t))}分）`; }
    drawLanes(m); drawRail();
    $("clock").textContent = E.hhmm(S.t);
    const open = E.openQuestions(S);
    $("dock").hidden = !open.length; $("dockText").textContent = `判断待ち ${open.length}`;
    $("say").textContent = S.say;
    const av = $("avatar"); av.classList.toggle("asking", open.length > 0); av.classList.toggle("working", !open.length && S.phase === "run" && !quick);
    av.innerHTML = open.length ? ICON.ask : S.phase === "done" ? ICON.check : ICON.work;
    if (!$("log").hidden) drawLog();
    V.flash = {};
  }

  let last = performance.now();
  function loop(now) { step(now); requestAnimationFrame(loop); }
  function step(now) {
    const dt = Math.min(0.25, (now - last) / 1000); last = now;
    if (speed > 0 && S.phase !== "delegate") {
      const asking = E.openQuestions(S).some((q) => !q.deferred);
      E.tick(S, dt * (asking ? 1 / 6 : 1 / 1.2) * speed); // time keeps moving while the human thinks, only slower
    }
    frame();
  }

  // ---- fixed frames for the storyboard (?frame=1..10) ----
  function runTo(t) { while (S.t < t) { E.tick(S, 0.25); handleCues(); } }
  function preset(n) {
    const ans = (choice) => { const q = E.openQuestions(S)[0]; E.act(S, { type: "answer", q: q.id, choice }); handleCues(); };
    if (n >= 2) { E.act(S, { type: "delegate" }); }
    if (n === 2) runTo(12);
    if (n >= 3) runTo(21);
    if (n === 4) V.sel = "super";
    if (n === 5) { ans("super"); runTo(23); }
    if (n >= 6 && n <= 8) { ans("super"); runTo(25); V.echo = null; }
    if (n === 7) { ans("approve"); runTo(25.4); V.echoUntil = Infinity; }
    if (n === 8) { ans("approve"); runTo(26.5); V.echo = null; }
    if (n === 9 || n === 10) { V.mark = S.audit.length; ans("later"); runTo(29); }
    if (n === 10) { const q = E.openQuestions(S)[0]; E.act(S, { type: "reopen", q: q.id }); showDigest(); }
    if (n === 12) { ans("skip"); runTo(25); V.echo = null; } // check view: 料亭は見送る
    if (n === 11) { ans("super"); runTo(25); ans("approve"); runTo(30); V.echo = null; }
    frame();
  }

  if (FRAME) { speed = 0; preset(+FRAME); }
  else if (params.get("auto")) setInterval(() => step(performance.now()), 33); // headless runs have no animation frames
  else requestAnimationFrame(loop);

  // ?auto=1 — self-check of the live path: presses the real buttons the way a person would, then reports in the title.
  if (params.get("auto")) {
    let errors = 0; addEventListener("error", () => errors++);
    const click = (sel) => { const el = document.querySelector(sel); if (el) el.click(); return !!el; };
    const steps = [ [300, () => click("#goDelegate")], [26000, () => click('.opt[data-k="split"]')], [27000, () => click("#goAlloc")],
      [36000, () => click("#approve")], [60000, () => {
        document.title = `AUTO phase=${S.phase} trade=${S.buri.trade ? S.buri.trade.no : "none"} alloc=${JSON.stringify(S.buri.alloc)} rail=${$("railList").children.length} errors=${errors}`; }]];
    steps.forEach(([ms, f]) => setTimeout(f, ms));
  }
})();
