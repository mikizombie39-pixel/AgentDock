// Tests for the Adaptive Workspace prototype engine (web/proto/engine.js). Run: node --test tests/
const test = require("node:test");
const assert = require("node:assert");
const E = require("../web/proto/engine.js");

function runTo(s, t) { while (s.t < t) E.tick(s, 0.25); }
function toAsk() { const s = E.create(); E.act(s, { type: "delegate" }); runTo(s, 21); return s; }

test("nothing moves before the human delegates", () => {
  const s = E.create(); runTo(s, 30);
  assert.strictEqual(s.phase, "delegate");
  assert.strictEqual(s.settled.length, 0);
});

test("the extra order stops the buri work and asks; the bid keeps going on its own", () => {
  const s = toAsk();
  assert.strictEqual(s.buri.status, "paused");
  assert.strictEqual(E.openQuestions(s)[0].kind, "alloc");
  runTo(s, 29); // the question stays open while shiroebi finishes inside the approved plan
  assert.strictEqual(E.openQuestions(s).length, 1);
  assert.strictEqual(s.shiro.status, "done");
  assert.strictEqual(s.orders.shiro.got, 5);
  assert.strictEqual(s.settled.filter((c) => c.kind === "trade").length, 2);
  assert.ok(!s.buri.trade);
});

test("options are computed from the rules and change what is negotiated", () => {
  const s = toAsk();
  const o = Object.fromEntries(E.buriOptions(s).map((x) => [x.key, x]));
  assert.strictEqual(o.super.margin, 4 * (22000 - 2000 - 17800) + 2 * (24000 - 2000 - 17800)); // 17,200
  assert.strictEqual(o.skip.margin, 4 * (22000 - 2000 - 18300)); // 6,800
  assert.strictEqual(o.ryotei.short.スーパー, 4);
  assert.strictEqual(E.ceiling("料亭"), 20500);
  E.act(s, { type: "answer", q: "q-alloc", choice: "split" });
  runTo(s, 25);
  assert.strictEqual(s.buri.agreement.qty, 6);
  assert.strictEqual(s.buri.agreement.unit, 17900); // the seller's real answer differs from the estimate
  assert.deepStrictEqual(s.buri.agreement.alloc, { スーパー: 3, 料亭: 3 });
});

test("an agreement is not a trade: no registration without the human's approval", () => {
  const s = toAsk();
  E.act(s, { type: "answer", q: "q-alloc", choice: "super" });
  runTo(s, 26);
  assert.strictEqual(s.buri.status, "agreed");
  assert.strictEqual(E.openQuestions(s)[0].kind, "approve");
  runTo(s, 31);
  assert.ok(!s.buri.trade);
  assert.ok(!s.settled.some((c) => c.id === "buri"));
});

test("approval registers once through the trade ledger with all checks", () => {
  const s = toAsk();
  E.act(s, { type: "answer", q: "q-alloc", choice: "super" });
  runTo(s, 26);
  const q = E.openQuestions(s)[0];
  assert.ok(E.act(s, { type: "answer", q: q.id, choice: "approve" }));
  runTo(s, 28);
  assert.ok(s.buri.trade && s.buri.trade.no.startsWith("M-"));
  const call = s.api.find((c) => c.call === "POST /api/market/trade" && c.body.startsWith("ブリ"));
  assert.ok(call.checks.every((c) => c[1]));
  assert.strictEqual(s.orders.super.got, 4);
  assert.strictEqual(s.orders.ryotei.got, 2);
  assert.strictEqual(s.vault.length, 1);
  assert.strictEqual(E.act(s, { type: "answer", q: q.id, choice: "approve" }), false); // no second approval
  assert.strictEqual(s.settled.filter((c) => c.id === "buri").length, 1);
});

test("an old version cannot be approved after the hold was renewed", () => {
  const s = toAsk();
  E.act(s, { type: "answer", q: "q-alloc", choice: "super" });
  runTo(s, 26);
  const old = E.openQuestions(s)[0];
  runTo(s, s.buri.hold.until + 1.5); // the human stays away; the hold expires and the AI renews it
  const fresh = E.openQuestions(s)[0];
  assert.ok(fresh && fresh.id !== old.id);
  assert.strictEqual(E.act(s, { type: "answer", q: old.id, choice: "approve" }), false);
  assert.ok(E.act(s, { type: "answer", q: fresh.id, choice: "approve" }));
  runTo(s, s.t + 2);
  assert.ok(s.buri.trade);
});

test("deferring keeps the question open and nothing is decided for the human", () => {
  const s = toAsk();
  assert.ok(E.act(s, { type: "answer", q: "q-alloc", choice: "later" }));
  runTo(s, 30);
  const q = E.openQuestions(s).find((x) => x.kind === "alloc");
  assert.ok(q && q.deferred);
  assert.ok(!s.buri.alloc);
});

test("declining at approval cancels the hold and registers nothing", () => {
  const s = toAsk();
  E.act(s, { type: "answer", q: "q-alloc", choice: "ryotei" });
  runTo(s, 26);
  const q = E.openQuestions(s)[0];
  E.act(s, { type: "answer", q: q.id, choice: "decline" });
  runTo(s, 30);
  assert.ok(!s.buri.trade);
  assert.ok(s.api.some((c) => c.call === "POST /api/network/cancel"));
  assert.strictEqual(s.phase, "done");
});
