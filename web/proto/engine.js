// Agent Dock — Adaptive Workspace prototype: the work engine.
// The screen draws only from this state. Human decisions and approvals are actions the engine waits for;
// nothing here advances past a decision by itself. Money is computed here from the rules (not written by the AI).
// All organizations, prices and quantities are simulated.
(function (root) {
  "use strict";

  const T0 = 5 * 60 + 50; // 05:50 (simulated market morning)

  const CFG = {
    // 承認する人と権限（実証用の設定）。役職の呼び名と権限は分ける。limitYen: null は上限の設定なし
    approver: { name: "網谷さん", limitYen: null },
    buri: {
      name: "ブリ（特大）", unit: "本", cost: 2000, profit: 1500,
      price: { スーパー: 22000, 料亭: 24000 },
      lotLeft: 6,                 // セリの後に残る本数（相対の対象）
      firstAsk4: 18300,           // 漁協の AI：4本なら
      expect6: 17800,             // 漁協の資料：6本まとめての見込み
      final6: 17900,              // 漁協の AI の実際の答え（6本まとめて）
      holdMin: 25,                // 仮押さえの長さ（分）
    },
    shiroebi: { name: "シロエビ", unit: "箱", planCeiling: 4800, planMax: 8, bidPrice: 4300, bidQty: 5, won: 3, cPrice: 4500, cQty: 2 },
  };

  const hhmm = (t) => {
    const m = T0 + Math.floor(t);
    return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
  };
  const withinAuthority = (yen) => CFG.approver.limitYen == null || yen <= CFG.approver.limitYen;
  const ceiling = (cust) => CFG.buri.price[cust] - CFG.buri.cost - CFG.buri.profit; // 上限 = 売値 − 費用 − 利益

  // Options for the extra-order question. Quantities and money are computed, never typed.
  function buriOptions(state) {
    const b = CFG.buri;
    const defs = [
      { key: "super", label: "スーパー優先", icon: "S", alloc: { スーパー: 4, 料亭: 2 } },
      { key: "split", label: "数量を分ける", icon: "÷", alloc: { スーパー: 3, 料亭: 3 } },
      { key: "ryotei", label: "料亭優先", icon: "R", alloc: { スーパー: 0, 料亭: 6 } },
      { key: "skip", label: "料亭は見送る", icon: "×", alloc: { スーパー: 4, 料亭: 0 } },
    ];
    return defs.map((d) => {
      const qty = d.alloc.スーパー + d.alloc.料亭;
      const known = qty === 4 && state.buri.offer && state.buri.offer.qty === 4;
      const unit = qty === 6 ? b.expect6 : b.firstAsk4;
      const basis = qty === 6 ? "見込み（漁協の資料・6本まとめて）" : "漁協の AI の回答（4本）";
      const margin = d.alloc.スーパー * (b.price.スーパー - b.cost - unit) + d.alloc.料亭 * (b.price.料亭 - b.cost - unit);
      const short = { スーパー: 4 - d.alloc.スーパー, 料亭: state.orders.ryotei ? 6 - d.alloc.料亭 : 0 };
      const withinCeiling = ["スーパー", "料亭"].every((c) => d.alloc[c] === 0 || unit <= ceiling(c));
      return { ...d, qty, unit, basis, known, margin, amount: unit * qty, short, withinCeiling,
        executable: withinCeiling && withinAuthority(unit * qty) };
    });
  }

  function create() {
    return {
      t: 0, phase: "delegate", seq: 0, version: 1,
      orders: { super: { cust: "スーパー", item: "ブリ", qty: 4, got: 0 }, shiro: { cust: "むき身", item: "シロエビ", qty: 5, got: 0 }, ryotei: null },
      buri: { status: "idle", offer: null, alloc: null, agreement: null, approval: null, trade: null, hold: null, msgs: [] },
      shiro: { status: "idle", trades: [], msgs: [] },
      questions: [],          // open and resolved questions
      settled: [],            // cards that stay on the board
      audit: [], api: [], vault: [],
      cues: [],               // one-shot signals for the screen (ask / pop / link / settle / alert / echo)
      agenda: [],             // scheduled engine steps {at, fn}
      say: "任せる仕事を確かめてください。",
      lastError: null,
    };
  }

  const cue = (s, type, target, extra) => s.cues.push({ id: ++s.seq, type, target, ...extra });
  const audit = (s, actor, action, detail) => s.audit.push({ t: hhmm(s.t), actor, action, detail });
  const at = (s, dt, name, arg) => s.agenda.push({ at: s.t + dt, name, arg });

  // ---- scheduled engine steps (autonomous work) ----
  const STEPS = {
    shiroBid(s) {
      const c = CFG.shiroebi;
      s.shiro.status = "bidding";
      s.shiro.msgs.push(`入札 ${c.bidPrice.toLocaleString()}円 × ${c.bidQty}箱（承認済みの範囲）`);
      s.api.push({ t: hhmm(s.t), call: "POST /api/bid", body: `shiroebi ${c.bidPrice}×${c.bidQty}`, result: "受付" });
      audit(s, "AI", "入札", `シロエビ ${c.bidPrice}円×${c.bidQty}箱・計画の上限 ${c.planCeiling}円の内`);
      s.say = "シロエビの入札を出しました。開札は 06:15 です。";
    },
    seriDone(s) {
      s.buri.status = "negotiating";
      s.buri.msgs.push({ from: "漁協", text: "セリの後の残り 6本を相対に出します" });
      s.say = "ブリの残り 6本の相対を始めます。";
      at(s, 1, "buriAsk4");
    },
    buriAsk4(s) {
      s.buri.msgs.push({ from: "網谷", text: "4本を 17,800円で" });
      s.api.push({ t: hhmm(s.t), call: "POST /api/network/inquiry", body: "buri 4本 17,800", result: "送信（開示の検査 合格）" });
      at(s, 1, "buriCounter4");
    },
    buriCounter4(s) {
      const b = CFG.buri;
      s.buri.offer = { qty: 4, unit: b.firstAsk4, from: "漁協" };
      s.buri.hold = { qty: 4, until: s.t + b.holdMin };
      s.buri.msgs.push({ from: "漁協", text: `4本なら ${b.firstAsk4.toLocaleString()}円（仮押さえ ${hhmm(s.t + b.holdMin)} まで）` });
      s.say = `ブリ 4本 ${b.firstAsk4.toLocaleString()}円は上限 ${ceiling("スーパー").toLocaleString()}円の内です。合意の手前です。`;
      at(s, 2, "ryoteiOrder");
    },
    ryoteiOrder(s) {
      s.orders.ryotei = { cust: "料亭", item: "ブリ", qty: 6, got: 0, isNew: true };
      cue(s, "alert", "order-ryotei", { text: "4本 足りない" });
      audit(s, "出来事", "注文", "料亭からブリ 6本（24,000円/本）");
      s.buri.status = "paused";
      s.say = "料亭から 6本の注文が入りました。残りは 6本で、合わせて 10本には 4本足りません。";
      at(s, 0.5, "askAlloc");
    },
    askAlloc(s) {
      const q = { id: "q-alloc", kind: "alloc", version: s.version, openedAt: s.t, title: "ブリの配分", status: "open",
        why: "料亭の追加注文で、注文が残りの本数を 4本こえた", rec: "super",
        recWhy: "スーパーの定番を切らさない（Vault：去年 11月、欠品の翌週に棚を半分にされた）" };
      s.questions.push(q);
      s.phase = "ask";
      cue(s, "ask", "q-alloc");
      s.say = "判断をお願いします。どの配分で漁協と交渉し直しますか。";
    },
    shiroOpen(s) {
      const c = CFG.shiroebi;
      s.shiro.status = "won";
      s.shiro.trades.push({ kind: "入札", qty: c.won, unit: c.bidPrice });
      s.shiro.msgs.push(`開札：${c.won}箱を落札・あと ${c.bidQty - c.won}箱`);
      audit(s, "市場", "開札", `シロエビ 網谷水産 ${c.won}箱 ${c.bidPrice}円`);
      register(s, "shiro-bid", `シロエビ ${c.won}箱`, c.won * c.bidPrice, "入札の結果（市場の確定）");
      s.orders.shiro.got += c.won;
      at(s, 1, "shiroAskC");
    },
    shiroAskC(s) {
      s.shiro.status = "negotiating";
      s.shiro.msgs.push("C社へ 2箱の相対を申し込み");
      s.api.push({ t: hhmm(s.t), call: "POST /api/network/inquiry", body: "shiroebi 2箱 → C社", result: "送信" });
      at(s, 1, "shiroCReply");
    },
    shiroCReply(s) {
      const c = CFG.shiroebi;
      s.shiro.status = "agreed";
      s.shiro.msgs.push(`C社：2箱 ${c.cPrice.toLocaleString()}円 → 合意（承認済みの範囲 ${c.planCeiling.toLocaleString()}円の内）`);
      audit(s, "AI", "合意", `シロエビ C社 ${c.cQty}箱 ${c.cPrice}円・事前の承認の範囲`);
      at(s, 1, "shiroRegister");
    },
    shiroRegister(s) {
      const c = CFG.shiroebi;
      register(s, "shiro-c", `シロエビ ${c.cQty}箱（C社）`, c.cQty * c.cPrice, "事前の承認の範囲で AI が登録");
      s.shiro.trades.push({ kind: "相対", qty: c.cQty, unit: c.cPrice });
      s.orders.shiro.got += c.cQty;
      s.shiro.status = "done";
      s.say = "シロエビは 5箱そろいました。";
      checkDone(s);
    },
    renegotiate(s, alloc) {
      const qty = alloc.スーパー + alloc.料亭;
      s.buri.status = "negotiating";
      if (qty === 6) {
        s.buri.msgs.push({ from: "網谷", text: "6本まとめて 17,600円で" });
        s.api.push({ t: hhmm(s.t), call: "POST /api/network/inquiry", body: "buri 6本 17,600", result: "送信（開示の検査 合格）" });
        at(s, 1.2, "counter6");
      } else {
        s.buri.msgs.push({ from: "網谷", text: `先ほどの ${CFG.buri.firstAsk4.toLocaleString()}円・4本で` });
        at(s, 1.2, "agree", { unit: CFG.buri.firstAsk4, qty: 4 });
      }
    },
    counter6(s) {
      const b = CFG.buri;
      s.buri.msgs.push({ from: "漁協", text: `6本まとめてなら ${b.final6.toLocaleString()}円（仮押さえ ${hhmm(s.t + b.holdMin)} まで）` });
      s.buri.hold = { qty: 6, until: s.t + b.holdMin };
      at(s, 1, "agree", { unit: b.final6, qty: 6 });
    },
    agree(s, { unit, qty }) {
      const a = s.buri.alloc;
      const okCeil = ["スーパー", "料亭"].every((c) => a[c] === 0 || unit <= ceiling(c));
      if (!okCeil) { exception(s, "上限をこえる値の答えでした。合意しません。"); return; }
      s.version += 1;
      s.buri.agreement = { qty, unit, alloc: { ...a }, version: s.version, amount: unit * qty,
        margin: a.スーパー * (CFG.buri.price.スーパー - CFG.buri.cost - unit) + a.料亭 * (CFG.buri.price.料亭 - CFG.buri.cost - unit) };
      s.buri.status = "agreed";
      if (!s.buri.hold || s.buri.hold.qty !== qty) s.buri.hold = { qty, until: s.t + CFG.buri.holdMin };
      audit(s, "AI", "合意", `ブリ ${qty}本 ${unit}円・承認の前（条件付き）`);
      cue(s, "pop", "agreement");
      cue(s, "link", "agreement");
      at(s, 0.8, "askApprove");
    },
    askApprove(s) {
      const q = { id: "q-approve-" + s.version, kind: "approve", version: s.version, openedAt: s.t, title: "ブリの取引の承認", status: "open" };
      s.questions.push(q);
      s.phase = "approve";
      cue(s, "ask", q.id);
      s.say = "漁協と合意しました。承認すると、市場の取引帳簿に正式に登録します。";
    },
    rehold(s, kind) {
      // The AI asks the seller to hold again. Anything shown before is now an old version.
      const qty = s.buri.agreement ? s.buri.agreement.qty : 4;
      s.buri.hold = { qty, until: s.t + CFG.buri.holdMin };
      s.buri.warned = false;
      s.buri.msgs.push({ from: "漁協", text: `押さえ直しました（${hhmm(s.t + CFG.buri.holdMin)} まで）` });
      audit(s, "AI", "押さえ直し", `ブリ ${qty}本`);
      s.version += 1;
      if (kind === "approve" && s.buri.agreement) {
        s.buri.agreement.version = s.version;
        s.buri.status = "agreed";
        STEPS.askApprove(s);
      } else if (kind === "alloc") {
        s.buri.status = "paused";
        s.questions = s.questions.filter((q) => q.id !== "q-alloc" || q.status === "resolved");
        STEPS.askAlloc(s);
      } else {
        s.buri.status = "paused";
      }
    },
    holdWarn(s) {
      if (s.buri.hold && !s.buri.trade && s.t < s.buri.hold.until) cue(s, "alert", "hold", { text: "残り 5分" });
    },
    registerBuri(s) {
      const a = s.buri.agreement;
      const ok = register(s, "buri", `ブリ ${a.qty}本`, a.amount, "網谷さんの承認 → 市場の取引帳簿");
      if (!ok) return;
      s.buri.trade = { no: ok, ...a };
      s.buri.status = "done";
      s.orders.super.got = a.alloc.スーパー;
      if (s.orders.ryotei) s.orders.ryotei.got = a.alloc.料亭;
      s.vault.push({ t: hhmm(s.t), text: `料亭の追加注文 → ${optLabel(a.alloc)}（網谷さんの判断）。結果は ${a.qty}本 ${a.unit.toLocaleString()}円。` });
      audit(s, "AI", "Vault に記録", "判断と結果");
      s.say = "ブリを正式に登録しました。判断を Vault に残しました。";
      checkDone(s);
    },
  };

  const optLabel = (alloc) => (buriOptions({ buri: {}, orders: { ryotei: true } }).find((o) => o.alloc.スーパー === alloc.スーパー && o.alloc.料亭 === alloc.料亭) || {}).label || "";

  function exception(s, why) {
    s.buri.status = "exception";
    s.lastError = why;
    cue(s, "alert", "buri", { text: "例外" });
    audit(s, "コード", "停止", why);
    s.say = why;
  }

  // Formal registration: the only path into the trade ledger. Re-checks everything at the moment of writing.
  function register(s, key, label, amount, how) {
    const checks = [];
    if (key === "buri") {
      const a = s.buri.agreement, ap = s.buri.approval;
      checks.push(["合意がある", !!a]);
      checks.push(["承認が合意の版と一致", !!(a && ap && ap.version === a.version)]);
      checks.push(["仮押さえの期限の内", !!(s.buri.hold && s.t <= s.buri.hold.until)]);
      checks.push(["上限の内", !!(a && ["スーパー", "料亭"].every((c) => a.alloc[c] === 0 || a.unit <= ceiling(c)))]);
      checks.push([`承認の権限（${CFG.approver.name}・実証用の設定）`, withinAuthority(amount)]);
      checks.push(["二重の登録でない", !s.buri.trade]);
    } else {
      checks.push(["承認済みの計画の範囲", true]);
    }
    const bad = checks.find((c) => !c[1]);
    const no = "M-" + hhmm(s.t).replace(":", "") + "-" + String(s.settled.filter((x) => x.kind === "trade").length + 1).padStart(2, "0");
    s.api.push({ t: hhmm(s.t), call: "POST /api/market/trade", body: `${label} ${amount.toLocaleString()}円`, result: bad ? "拒否：" + bad[0] : "登録 " + no, checks });
    if (bad) { exception(s, "登録できません：" + bad[0]); return null; }
    s.settled.push({ id: key, kind: "trade", label, amount, no, how, t: hhmm(s.t) });
    audit(s, "市場の取引帳簿", "正式登録", `${label} ${amount}円 ${no}`);
    cue(s, "settle", key);
    return no;
  }

  function checkDone(s) {
    if (s.shiro.status === "done" && s.buri.status === "done") {
      s.phase = "done";
      s.say = "今朝の仕入れは終わりました。判断 1件、承認 1件、AI の登録 2件です。";
    }
  }

  // ---- human actions ----
  function act(s, a) {
    s.lastError = null;
    if (a.type === "delegate") {
      if (s.phase !== "delegate") return reject(s, "すでに任せています");
      s.phase = "run";
      audit(s, "網谷さん", "委任", "ブリ 4本（上限 18,500円）・シロエビ 5箱（上限 4,800円・8箱まで）");
      s.say = "任されました。承認済みの範囲で進め、範囲の外は相談します。";
      s.shiro.status = "preparing"; s.buri.status = "watching";
      at(s, 2, "shiroBid"); at(s, 16, "seriDone"); at(s, 25, "shiroOpen");
      return true;
    }
    if (a.type === "answer") {
      const q = s.questions.find((x) => x.id === a.q);
      if (!q || q.status !== "open") return reject(s, "この問いは閉じています");
      if (q.version !== s.version) return reject(s, "古い版の候補です。画面を新しくしました");
      if (q.kind === "alloc") {
        if (a.choice === "later") { q.deferred = true; audit(s, "網谷さん", "保留", "ブリの配分"); s.say = "保留にしました。判断待ちに残します。"; return true; }
        const opt = buriOptions(s).find((o) => o.key === a.choice);
        if (!opt) return reject(s, "候補にない選択です");
        if (!opt.executable) return reject(s, "現在の条件では実行できません");
        q.status = "resolved"; q.choice = opt.key; q.choiceLabel = opt.label; q.resolvedAt = s.t;
        s.buri.alloc = { ...opt.alloc };
        if (opt.alloc.料亭 === 0) s.orders.ryotei.declined = true;
        s.version += 1;
        s.phase = "run";
        audit(s, "網谷さん", "判断", `${opt.label}（スーパー ${opt.alloc.スーパー}・料亭 ${opt.alloc.料亭}）`);
        s.settled.push({ id: "q-alloc", kind: "decision", label: `判断：${opt.label}`, how: `スーパー ${opt.alloc.スーパー}・料亭 ${opt.alloc.料亭}`, t: hhmm(s.t) });
        cue(s, "echo", "q-alloc", { text: `受け取りました：${opt.label}（スーパー ${opt.alloc.スーパー}・料亭 ${opt.alloc.料亭}）` });
        s.say = opt.qty === 6 ? "6本まとめての条件で、漁協と交渉し直します。" : "4本の条件で漁協に戻ります。料亭には断りの連絡が要ります。";
        at(s, 0.6, "renegotiate", opt.alloc);
        return true;
      }
      if (q.kind === "approve") {
        if (a.choice === "later") { q.deferred = true; audit(s, "網谷さん", "保留", "ブリの承認"); s.say = "保留にしました。仮押さえの期限に注意します。"; return true; }
        if (a.choice === "approve") {
          q.status = "resolved"; q.choice = "approve"; q.choiceLabel = "承認"; q.resolvedAt = s.t;
          s.buri.approval = { by: "網谷さん", version: q.version, t: hhmm(s.t) };
          s.buri.status = "approved";
          s.phase = "run";
          audit(s, "網谷さん", "承認", `ブリ ${s.buri.agreement.qty}本 ${s.buri.agreement.unit}円（版 ${q.version}）`);
          cue(s, "echo", q.id, { text: "受け取りました：承認。市場の取引帳簿に登録します" });
          s.say = "承認を受けました。市場の取引帳簿に登録します。";
          at(s, 0.8, "registerBuri");
          return true;
        }
        if (a.choice === "decline") {
          q.status = "resolved"; q.choice = "decline"; q.choiceLabel = "見送り"; q.resolvedAt = s.t;
          s.buri.status = "done"; s.buri.hold = null; s.buri.agreement.cancelled = true;
          audit(s, "網谷さん", "見送り", "仮押さえを取り消し、漁協に知らせる");
          s.api.push({ t: hhmm(s.t), call: "POST /api/network/cancel", body: "buri hold", result: "取り消し" });
          s.settled.push({ id: "q-approve", kind: "decision", label: "判断：見送り", how: "仮押さえを取り消し", t: hhmm(s.t) });
          cue(s, "echo", q.id, { text: "受け取りました：見送り。仮押さえを取り消します" });
          s.say = "見送りました。漁協に仮押さえの取り消しを知らせました。";
          checkDone(s);
          return true;
        }
      }
      return reject(s, "候補にない選択です");
    }
    if (a.type === "reopen") {
      const q = s.questions.find((x) => x.id === a.q && x.status === "open");
      if (!q) return reject(s, "この問いは閉じています");
      q.deferred = false;
      audit(s, "網谷さん", "再開", q.title);
      s.say = q.kind === "alloc" ? "判断をお願いします。どの配分で漁協と交渉し直しますか。" : "承認をお願いします。";
      return true;
    }
    return reject(s, "知らない操作です");
  }

  function reject(s, why) { s.lastError = why; audit(s, "コード", "操作を断る", why); return false; }

  // Advance simulated minutes and run due steps (in time order).
  function tick(s, dt) {
    s.t += dt;
    for (;;) {
      s.agenda.sort((x, y) => x.at - y.at);
      if (!s.agenda.length || s.agenda[0].at > s.t) break;
      const job = s.agenda.shift();
      STEPS[job.name](s, job.arg);
    }
    if (s.buri.hold && !s.buri.trade && !s.buri.warned && s.buri.hold.until - s.t <= 5 && s.buri.status !== "done") {
      s.buri.warned = true; STEPS.holdWarn(s);
    }
    if (s.buri.hold && !s.buri.trade && s.t > s.buri.hold.until && s.buri.status !== "done" && s.buri.status !== "exception") {
      s.buri.hold = null;
      const openQ = s.questions.find((q) => q.status === "open");
      if (openQ) openQ.status = "expired";
      exception(s, "仮押さえの期限が切れました。漁協に押さえ直しを頼みます。");
      at(s, 1, "rehold", openQ ? openQ.kind : null);
    }
  }

  const openQuestions = (s) => s.questions.filter((q) => q.status === "open");
  const takeCues = (s) => s.cues.splice(0);

  const api = { CFG, create, act, tick, hhmm, ceiling, buriOptions, openQuestions, takeCues };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.ADEngine = api;
})(this);
