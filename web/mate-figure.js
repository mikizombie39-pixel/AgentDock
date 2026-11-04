// Agent Dock: the companion's figure (one SVG, the same figure in every stance).
// The stance changes the outline (pose), posture, eyes, brows, mouth, arms and the motion; colour is only a soft halo.
// Stances: calm (the basic figure) / bold / struggle / careful / sorry / relief. Geometry lives in mate.css (CSS `d`, rx/ry and
// transforms), so a change of stance morphs the shapes smoothly instead of swapping pictures.
// The execution state (running / your_turn / president / stopped / registered / idle) is a separate mark beside the figure.
// prefers-reduced-motion: no motion, but every stance still differs in shape (brows, eyes, mouth, arms, pose, drop, palm).
(function () {
  "use strict";
  const SVG = `
<svg class="mate-svg" viewBox="0 0 240 240" role="img" aria-hidden="true">
  <circle class="halo" cx="120" cy="116" r="94"/>
  <ellipse class="shadow" cx="120" cy="218" rx="54" ry="7"/>
  <g class="motion">
    <g class="pose">
      <g class="arm arm-l"><rect x="40" y="114" width="20" height="46" rx="10"/></g>
      <g class="arm arm-r"><rect x="180" y="114" width="20" height="46" rx="10"/><circle class="palm" cx="190" cy="164" r="11"/>
        <path class="palm-lines" d="M184 160 L184 168 M190 158 L190 168 M196 160 L196 168"/></g>
      <path class="antenna" d="M120 58 Q120 44 120 32"/>
      <circle class="ball" cx="120" cy="28" r="6.5"/>
      <rect class="head" x="56" y="56" width="128" height="116" rx="46"/>
      <ellipse class="cheek cheek-l" cx="84" cy="134" rx="9" ry="5"/>
      <ellipse class="cheek cheek-r" cx="156" cy="134" rx="9" ry="5"/>
      <g class="eyes">
        <ellipse class="eye eye-l" cx="98" cy="112" rx="7" ry="10"/>
        <ellipse class="eye eye-r" cx="142" cy="112" rx="7" ry="10"/>
        <circle class="glint glint-l" cx="100" cy="108" r="2"/>
        <circle class="glint glint-r" cx="144" cy="108" r="2"/>
        <path class="lid lid-l" d="M90 114 Q98 106 106 114"/>
        <path class="lid lid-r" d="M134 114 Q142 106 150 114"/>
      </g>
      <path class="brow brow-l" d="M86 90 Q98 86 110 90"/>
      <path class="brow brow-r" d="M130 90 Q142 86 154 90"/>
      <path class="mouth" d="M108 138 Q120 144 132 138"/>
      <path class="drop" d="M178 70 Q172 80 178 84 Q184 80 178 70 Z"/>
      <g class="puff"><circle cx="146" cy="142" r="4"/><circle cx="156" cy="136" r="5.5"/><circle cx="168" cy="128" r="7"/></g>
    </g>
  </g>
</svg>`;
  const REDUCED = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function create(host) {
    host.insertAdjacentHTML("afterbegin", SVG);
    const root = host.querySelector(".mate-svg");
    let current = null, sorryTimer = 0, since = 0;
    root.classList.add("st-calm");
    return {
      el: root,
      get stance() { return current; },
      // how long the current stance has been on screen (ms) — the screen keeps 「残念」 long enough to be seen
      shownFor() { return performance.now() - since; },
      set(stance) {
        const s = stance || "calm";
        if (s === current) return;
        root.classList.remove("st-" + (current || "calm"), "sorry-turn");
        clearTimeout(sorryTimer);
        root.classList.add("st-" + s);
        current = s; since = performance.now();
        // 残念：うつむいて沈む → 間を置く → 顔を上げて次へ向き直る
        // 「動きを減らす」では向き直りの動きを見せず、うつむいた形のまま残す（形で残念と分かるように）
        if (s === "sorry" && !REDUCED) sorryTimer = setTimeout(() => root.classList.add("sorry-turn"), 2600);
      },
    };
  }
  window.MateFigure = { create, SVG };
})();
