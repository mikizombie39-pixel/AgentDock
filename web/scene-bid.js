// Bid scene (about 8 seconds): the owner's notebook and the market data reach the value ruler,
// the computed limit and the approved plan frame the range, and the chosen value lands inside it.
(function () {
  const M = window.MOCK_BID;
  const $ = (s) => document.querySelector(s);
  const NS = "http://www.w3.org/2000/svg";
  const yen = (v) => v.toLocaleString("ja-JP") + "円";

  // ---- stage scaling (fixed 1600x900 for recording) ----
  const stage = $("#stage");
  let scale = 1;
  function fit() {
    scale = Math.min(window.innerWidth / 1600, window.innerHeight / 900);
    stage.style.transform = `scale(${scale})`;
    stage.style.left = `${(window.innerWidth - 1600 * scale) / 2}px`;
    stage.style.top = `${(window.innerHeight - 900 * scale) / 2}px`;
  }
  window.addEventListener("resize", fit);
  fit();

  // ---- ruler geometry (viewBox 800 x 430) ----
  const X = (v) => 150 + ((v - M.scale.min) / (M.scale.max - M.scale.min)) * 620;
  const Y = { plan: 60, recent: 120, lastYear: 170, axis: 240 };
  const RULER_LEFT = 468, RULER_TOP = 104 + 150; // stage position of the ruler SVG

  function el(tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (text) e.textContent = text;
    return e;
  }

  const svg = $("#rsvg");
  function buildRuler() {
    svg.innerHTML = "";
    svg.append(
      el("text", { x: X(M.plan.low) - 8, y: Y.plan + 14, class: "bl", "text-anchor": "end" }, "計画の範囲"),
      el("text", { x: X(M.recent.high) + 8, y: Y.recent + 12, class: "bl" }, "直近の幅"),
      el("text", { x: X(M.lastYear.high) + 8, y: Y.lastYear + 12, class: "bl" }, "去年の幅"),
      el("rect", { id: "b-plan", class: "b-plan", x: X(M.plan.low), y: Y.plan, width: X(M.plan.limit) - X(M.plan.low), height: 18 }),
      el("rect", { id: "b-recent", class: "b-rec", x: X(M.recent.low), y: Y.recent, width: X(M.recent.high) - X(M.recent.low), height: 14 }),
      el("rect", { id: "b-last", class: "b-last", x: X(M.lastYear.low), y: Y.lastYear, width: X(M.lastYear.high) - X(M.lastYear.low), height: 14 }),
      el("line", { x1: 150, x2: 770, y1: Y.axis, y2: Y.axis, stroke: "var(--mute)" })
    );
    for (let v = 3500; v <= 5000; v += 500) {
      svg.append(el("line", { x1: X(v), x2: X(v), y1: Y.axis, y2: Y.axis + 6, stroke: "var(--mute)" }),
        el("text", { x: X(v), y: Y.axis + 24, class: "tk" }, yen(v)));
    }
    const calc = el("g", { id: "g-calc", class: "hide" });
    calc.append(el("line", { class: "l-calc", x1: X(M.computedLimit.value), x2: X(M.computedLimit.value), y1: 36, y2: Y.axis }),
      el("text", { class: "note", x: X(M.computedLimit.value) + 6, y: 32 }, "算出した上限 " + yen(M.computedLimit.value)),
      el("text", { class: "note", x: 150, y: Y.axis + 62 },
        `上限の計算（コード）：売値 ${yen(M.computedLimit.salePrice)} − 加工 ${yen(M.computedLimit.processing)} − 利益 ${yen(M.computedLimit.profit)} ＝ ${yen(M.computedLimit.value)}`));
    const plan = el("g", { id: "g-plan", class: "hide" });
    plan.append(el("line", { class: "l-plan", x1: X(M.plan.limit), x2: X(M.plan.limit), y1: 36, y2: Y.axis }),
      el("text", { class: "note", x: X(M.plan.limit) - 6, y: 32, "text-anchor": "end" },
        `計画の上限 ${yen(M.plan.limit)}・${M.plan.boxes}箱（${M.plan.approvedBy}が承認）`));
    const mark = el("g", { id: "g-mark", class: "hide" });
    mark.append(el("circle", { cx: X(M.choice.price), cy: Y.plan + 9, r: 9, fill: "var(--ink)", stroke: "var(--sf)", "stroke-width": 2 }));
    svg.append(calc, plan, mark);
  }

  // ---- notebook rows ----
  function buildBook() {
    const mk = (list, kind, host) => {
      host.innerHTML = "";
      list.forEach((r, i) => {
        const d = document.createElement("div");
        d.className = "row " + kind;
        d.id = `r-${kind}-${i}`;
        d.innerHTML = `<span class="tag"></span><span class="tx"></span>`;
        d.querySelector(".tag").textContent = r.tag;
        d.querySelector(".tx").textContent = r.text;
        host.appendChild(d);
      });
    };
    mk(M.book.own, "own", $("#rows-own"));
    mk(M.book.data, "data", $("#rows-data"));
  }

  // ---- leader lines from a notebook row to a point on the ruler ----
  const links = $("#links");
  function target(to) {
    if (to === "recent") return [X(M.recent.median), Y.recent + 7];
    if (to === "lastYear") return [X(M.lastYear.median), Y.lastYear + 7];
    return [X(M.choice.price), Y.plan + 9];
  }
  function link(rowId, kind, to) {
    const row = document.getElementById(rowId).getBoundingClientRect();
    const st = stage.getBoundingClientRect();
    const x0 = (row.right - st.left) / scale + 2;
    const y0 = (row.top + row.height / 2 - st.top) / scale;
    const [tx, ty] = target(to);
    const x1 = RULER_LEFT + tx, y1 = RULER_TOP + ty;
    const mx = x0 + (x1 - x0) * 0.45;
    const p = el("path", { class: kind, d: `M${x0},${y0} C${mx},${y0} ${mx},${y1} ${x1},${y1}`, pathLength: 1 });
    links.appendChild(p);
    return p;
  }

  // ---- phone ----
  function resetPhone() {
    $("#toasts").innerHTML = "";
    const b = $("#badge");
    b.style.visibility = "hidden";
    FX.toast($("#toasts"), `開場前の計画を承認しました（上限 ${yen(M.plan.limit)}・${M.plan.boxes}箱）`);
    FX.badge(b, 1);
  }

  function reset() {
    document.getAnimations().forEach((a) => a.cancel());
    buildRuler(); buildBook(); links.innerHTML = "";
    $("#unit").textContent = `${M.item}・${M.unit}・税抜き`;
    const v = $("#val"); v.textContent = `${yen(M.choice.price)}・${M.choice.boxes}箱`; v.style.visibility = "hidden";
    $("#pill").style.visibility = "hidden";
    const r = $("#reason");
    r.innerHTML = "<small>仲間の理由</small>";
    r.append(document.createTextNode(M.choice.reason));
    r.style.visibility = "hidden";
    FX.step([...document.querySelectorAll("#steps .st")], -1);
    resetPhone();
  }

  let running = false;
  async function play() {
    if (running) return;
    running = true;
    reset();
    const steps = [...document.querySelectorAll("#steps .st")];
    const show = (id) => { const g = document.getElementById(id); g.classList.remove("hide"); return FX.fadeIn(g, 350, "translateY(-6px)"); };

    // 1. market data: the two price ranges
    FX.step(steps, 0);
    FX.highlight($("#r-data-0"));
    await FX.lineDraw(link("r-data-0", "data", "recent"), 650);
    FX.growBar($("#b-recent"));
    await FX.wait(200);
    FX.highlight($("#r-data-1"));
    await FX.lineDraw(link("r-data-1", "data", "lastYear"), 650);
    await FX.growBar($("#b-last"));

    // 2. the limit computed by code, then the approved plan
    await show("g-calc");
    await FX.wait(250);
    FX.growBar($("#b-plan"));
    await show("g-plan");

    // 3. the owner's rules and past case
    FX.step(steps, 1);
    FX.highlight($("#r-own-0"));
    FX.lineDraw(link("r-own-0", "own", "mark"), 600);
    await FX.wait(250);
    FX.highlight($("#r-own-1"));
    await FX.lineDraw(link("r-own-1", "own", "mark"), 600);
    FX.step(steps, 2);
    FX.highlight($("#r-own-2"));
    await FX.lineDraw(link("r-own-2", "own", "mark"), 600);

    // 4. today's demand and situation
    FX.highlight($("#r-data-2"));
    FX.lineDraw(link("r-data-2", "data", "mark"), 550);
    await FX.wait(200);
    FX.highlight($("#r-data-3"));
    await FX.lineDraw(link("r-data-3", "data", "mark"), 550);

    // 5. the chosen value lands inside the plan
    FX.step(steps, 3);
    await show("g-mark");
    FX.maskReveal($("#val"));
    await FX.fadeIn($("#reason"), 400);
    FX.step(steps, 5);
    FX.fadeIn($("#pill"), 300);
    FX.burst(svg, X(M.choice.price), Y.plan + 9, ["var(--plan)", "var(--own)", "var(--data)"]);
    await FX.wait(350);
    await FX.toast($("#toasts"), `入札 ${yen(M.choice.price)}・${M.choice.boxes}箱を送りました（計画の範囲の中）`, "ok");
    FX.badge($("#badge"), 2);
    running = false;
  }

  $("#play").addEventListener("click", play);
  $("#reset").addEventListener("click", () => { if (!running) reset(); });
  document.addEventListener("keydown", (e) => {
    if (e.code === "Space") { e.preventDefault(); play(); }
    if (e.key === "r" && !running) reset();
  });

  reset();
  if (location.hash === "#done") { FX.setQuick(true); play().then(() => FX.setQuick(false)); }
  if (location.hash === "#play") setTimeout(play, 600);
})();
