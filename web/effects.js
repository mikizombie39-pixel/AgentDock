// Small motion helpers for the Agent Dock screens.
// Effect names follow motion-prompt (MIT, showheyohtaki) and lucian-uxui-prompt (MIT, lucianlabs).
// The code here is written from scratch for Agent Dock; no code is copied from those projects.
(function () {
  const EASE = "cubic-bezier(.2,.7,.2,1)";
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let quick = reduce; // also set for still captures (#done)
  const wait = (ms) => new Promise((r) => setTimeout(r, quick ? 0 : ms));
  const dur = (ms) => (quick ? 1 : ms);

  // 004 line draw: the path must have pathLength="1".
  function lineDraw(path, ms = 800, delay = 0) {
    path.setAttribute("stroke-dasharray", "1");
    return path.animate([{ strokeDashoffset: 1 }, { strokeDashoffset: 0 }],
      { duration: dur(ms), delay: quick ? 0 : delay, easing: EASE, fill: "forwards" }).finished;
  }

  // 080 highlighter: marks a row from left to right.
  function highlight(el, ms = 600) {
    el.classList.add("hl-on");
    return el.animate([{ backgroundSize: "0% 100%" }, { backgroundSize: "100% 100%" }],
      { duration: dur(ms), easing: EASE, fill: "forwards" }).finished;
  }

  // 005 mask reveal: the value appears once, without intermediate numbers.
  function maskReveal(el, ms = 550) {
    el.style.visibility = "visible";
    return el.animate([{ transform: "translateY(110%)" }, { transform: "translateY(0)" }],
      { duration: dur(ms), easing: EASE, fill: "forwards" }).finished;
  }

  // lucian 163: a band grows from its left edge.
  function growBar(el, ms = 600) {
    el.style.transformBox = "fill-box";
    el.style.transformOrigin = "left center";
    el.style.visibility = "visible";
    return el.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }],
      { duration: dur(ms), easing: EASE, fill: "forwards" }).finished;
  }

  function fadeIn(el, ms = 400, from = "translateY(6px)") {
    el.style.visibility = "visible";
    return el.animate([{ opacity: 0, transform: from }, { opacity: 1, transform: "none" }],
      { duration: dur(ms), easing: EASE, fill: "forwards" }).finished;
  }

  // 003 burst: a ring and small dots around (cx, cy) in an SVG.
  function burst(svg, cx, cy, colors) {
    if (quick) return Promise.resolve();
    const ns = "http://www.w3.org/2000/svg";
    const made = [];
    const ring = document.createElementNS(ns, "circle");
    Object.entries({ cx, cy, r: 10, fill: "none", stroke: colors[0], "stroke-width": 2 })
      .forEach(([k, v]) => ring.setAttribute(k, v));
    svg.appendChild(ring); made.push(ring);
    const anims = [ring.animate([{ r: 10, opacity: 1 }, { r: 46, opacity: 0 }], { duration: 750, easing: EASE, fill: "forwards" })];
    for (let i = 0; i < 12; i++) {
      const a = (i / 12) * Math.PI * 2;
      const d = document.createElementNS(ns, "circle");
      Object.entries({ cx, cy, r: 3, fill: colors[i % colors.length] }).forEach(([k, v]) => d.setAttribute(k, v));
      svg.appendChild(d); made.push(d);
      anims.push(d.animate([{ transform: "translate(0,0)", opacity: 1 },
        { transform: `translate(${Math.cos(a) * 58}px,${Math.sin(a) * 58}px)`, opacity: 0 }],
        { duration: 800, easing: EASE, fill: "forwards" }));
    }
    return Promise.all(anims.map((x) => x.finished)).then(() => made.forEach((m) => m.remove()));
  }

  // lucian 070: stacking toasts.
  function toast(list, text, kind = "") {
    const t = document.createElement("div");
    t.className = "toast " + kind;
    t.textContent = text;
    list.appendChild(t);
    return t.animate([{ opacity: 0, transform: "translateY(-8px)" }, { opacity: 1, transform: "none" }],
      { duration: dur(380), easing: EASE, fill: "forwards" }).finished;
  }

  // 071 badge: count pops when a notice arrives.
  function badge(el, n) {
    el.textContent = String(n);
    el.style.visibility = "visible";
    return el.animate([{ transform: "scale(.4)" }, { transform: "scale(1.25)" }, { transform: "scale(1)" }],
      { duration: dur(360), easing: EASE, fill: "forwards" }).finished;
  }

  // 068 step indicator.
  function step(steps, i) {
    steps.forEach((s, k) => {
      s.classList.toggle("done", k < i);
      s.classList.toggle("now", k === i);
    });
  }

  window.FX = { wait, lineDraw, highlight, maskReveal, growBar, fadeIn, burst, toast, badge, step, setQuick: (v) => { quick = v || reduce; } };
})();
