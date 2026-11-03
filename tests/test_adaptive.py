"""Adaptive Workspace の組み直し（2026-11-03 SAL の再設計の指示）の試験。Claude は模擬（本物は呼ばない）。

- 仕入れ値の上限は決まり（強制）：超える案は実行できず、承認しても確定しない
- 画面の組み方は Claude が選び、コードが確かめる（金額・操作・カードの数）
- 操作の候補は案件の状態でコードが絞る：確定の後は承認・保留・見送りを出さない
- 担当者の質問は出来事になり、Claude が答えて画面を組み直す
- 段階と経過は業務 DB の事実から決まる。確定の各手順と市場の取引帳簿の API が記録に残る
- 条件が変わったら、表示の時に計算し直し、承認の操作を出さない
"""
import os
import tempfile
import unittest

from agentdock import agents, db, deal, events, network, scene, stage, workspace
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
NOW = "2026-11-02T06:30:00+09:00"
UNTIL = "2026-11-02T07:00:00+09:00"
U = dict(input_tokens=3000, output_tokens=400)


def q(owner, sql, args=()):
    c = db.connect(owner if owner != "market" else db.MARKET)
    try:
        return [tuple(r) for r in c.execute(sql, args)]
    finally:
        c.close()


def ex(owner, sql, args=()):
    c = db.connect(owner if owner != "market" else db.MARKET)
    try:
        with c:
            c.execute(sql, args)
    finally:
        c.close()


def view():
    c = db.connect("amiya")
    try:
        return workspace.view(c)
    finally:
        c.close()


class AdaptiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        st = scene.start()
        self.order = st["amiya_order"][0]
        q1 = network.send("amiya", "iwase", "inquiry", "aji", "アジを 15kg、1kg 1,500円でいかがでしょうか。", D, 15, 1500, side="buy")
        r = network.send("iwase", "amiya", "offer", "aji", "10kg なら 1,700円、15kg まとめてなら 1,500円です。", D,
                         terms=[(10, 1700), (15, 1500)], in_reply_to=q1, valid_until=UNTIL)
        c = db.connect("iwase")
        try:
            with c:
                hold = c.execute("INSERT INTO stock_hold (stock_id, buyer, quantity, unit_price_yen, negotiation_id, status, expires_at, created_at, updated_at) "
                                 "VALUES (1, 'amiya', 15, 1500, ?, 'held', ?, ?, ?)", (r, UNTIL, NOW, NOW)).lastrowid
        finally:
            c.close()
        network.attach_hold(r, hold)
        self.t10, self.t15 = [t["id"] for t in network.terms_for_message(r)]
        q2 = network.send("amiya", "buyer_b", "inquiry", "aji", "5kg を 1kg 1,650円でお譲りできます（仕入れの確定が条件）。", D, 5, 1650, side="sell")
        r2 = network.send("buyer_b", "amiya", "offer", "aji", "お願いします。", D, terms=[(5, 1650)], in_reply_to=q2, valid_until=UNTIL)
        self.ag = network.make_agreement(network.terms_for_message(r2)[0]["id"], "網谷水産の担当者の承認を条件とする", "conditional", UNTIL)
        self.payload = dict(
            item_code="aji", summary="条件がそろいました。", headline="15kg まとめて仕入れる案を勧めます。どちらで進めますか",
            recommended="A", alternative="B", ask_human="どちらの方針で進めますか？",
            actions=["approve", "compare", "check_partner", "show_others", "ask"],
            suggested_questions=["B社との取引は本当に大丈夫？", "10kg だけにした場合と比べたい"],
            cards=[dict(card_type="agreement", ref_id=self.ag, note="追加の 5kg の売り先"), dict(card_type="order", ref_id=self.order, note=None)],
            options=[dict(code="A", label="15kg 仕入れ・5kg を B社へ", reason="売り先がある", risk="条件付き", buy_term_id=self.t15,
                          allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10),
                                       dict(kind="agreement", ref_id=self.ag, customer=None, quantity=5)]),
                     dict(code="B", label="10kg だけ仕入れる", reason="余りが出ない", risk="B社との合意は流れる", buy_term_id=self.t10,
                          allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10)]),
                     dict(code="E", label="見送る", reason="注文に応えられない", risk="注文を落とす", buy_term_id=None, allocations=[])])
        c = db.connect("amiya")
        try:
            self.case = workspace.build(c, "amiya", "aji", D, self.payload, 1, NOW)["case_id"]
            self.opt = {r[0]: (r[1], r[2]) for r in c.execute("SELECT code, id, fingerprint FROM board_option WHERE case_id = ?", (self.case,))}
        finally:
            c.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def approve(self, code="A", key="k1"):
        oid, fp = self.opt[code]
        return deal.decide("amiya", self.case, oid, "approve", fp, key, now=NOW)

    # ── 仕入れ値の上限（決まり）と承認の権限 ──
    def test_option_above_the_buy_ceiling_cannot_be_executed(self):
        v = view()
        b = next(o for o in v["options"] if o["code"] == "B")
        self.assertFalse(b["executable"])
        self.assertTrue(any("仕入れ値の上限 1,600円" in e for e in b["numbers"]["errors"]))
        lim = b["numbers"]["limits"]["buy_ceiling"][0]
        self.assertEqual((lim["sell_price_yen"], lim["process_cost_yen"], lim["target_profit_yen"], lim["ceiling_yen"]), (2100, 200, 300, 1600))
        r = self.approve("B")
        self.assertEqual(r["result"], "not_executable")
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(0,)])
        self.assertEqual(q("amiya", "SELECT status FROM deal_case WHERE id = ?", (self.case,)), [("presented",)])

    def test_authority_is_a_separate_limit(self):
        a = next(o for o in view()["options"] if o["code"] == "A")
        self.assertTrue(a["executable"])
        self.assertEqual(a["numbers"]["limits"]["authority"], dict(limit_yen=500000, amount_yen=22500, within=True))

    # ── 画面の組み方 ──
    def test_layout_is_stored_and_actions_are_gated(self):
        v = view()
        lay = v["layout"]
        self.assertEqual((lay["primary"], lay["alternative"], lay["focus"]), ("A", "B", "recommend"))
        self.assertEqual(lay["actions"], ["approve", "compare", "check_partner", "show_others", "ask"])
        self.assertEqual(lay["cards"][0]["data"]["agreement_id"], self.ag)
        self.assertEqual(self.approve()["result"], "registered")
        v = view()
        self.assertEqual(v["layout"]["actions"], [])
        self.assertEqual(v["layout"]["suggested_questions"], [])

    def test_layout_rules(self):
        c = db.connect("amiya")
        try:
            codes = {"A": True, "B": False}
            allowed = {1600, 2100}
            base = dict(headline="判断してください", focus="recommend", primary="A", alternative="B", cards=[], actions=["approve"])
            workspace.check_layout(c, "amiya", base, codes, allowed)
            for bad in (dict(base, headline="粗利は 9,999円です"), dict(base, actions=["delete_all"]), dict(base, focus="html"),
                        dict(base, primary="Z"), dict(base, alternative="A"),
                        dict(base, cards=[dict(card_type="order", ref_id=self.order, note=None)] * 5)):
                with self.assertRaises(workspace.BoardError):
                    workspace.check_layout(c, "amiya", bad, codes, allowed)
        finally:
            c.close()

    def test_registered_case_lists_each_counterparty(self):
        self.assertEqual(self.approve()["result"], "registered")
        recs = view()["records"]
        trades = {r["side"]: r for r in recs if r["kind"] == "market_trade"}
        self.assertEqual((trades["buy"]["counterparty"], trades["buy"]["quantity"], trades["buy"]["market_status"]), ("岩瀬漁協", 15, "registered"))
        self.assertEqual((trades["sell"]["counterparty"], trades["sell"]["quantity"]), ("B社", 5))
        self.assertTrue(trades["sell"]["seller_booked"] and trades["sell"]["buyer_booked"])
        fill = [r for r in recs if r["kind"] == "order_fill"]
        self.assertEqual([(r["counterparty"], r["quantity"], r["order_status"]) for r in fill], [("スーパー", 10, "filled")])

    # ── 条件が変わった時 ──
    def test_changed_price_removes_the_approve_action(self):
        ex("market", "UPDATE offer_term SET unit_price_yen = 1550 WHERE id = ?", (self.t15,))
        v = view()
        a = next(o for o in v["options"] if o["code"] == "A")
        self.assertTrue(a.get("changed"))
        self.assertEqual(a["numbers"]["purchase"]["unit_price_yen"], 1550)
        self.assertNotIn("approve", v["layout"]["actions"])

    def test_cancelled_agreement_removes_the_approve_action(self):
        ex("market", "UPDATE agreement SET status = 'cancelled' WHERE id = ?", (self.ag,))
        v = view()
        self.assertNotIn("approve", v["layout"]["actions"])
        self.assertEqual(self.approve()["result"], "stale")

    # ── 担当者の質問 → Claude が答えて組み直す ──
    def test_question_wakes_claude_and_relayouts(self):
        c = db.connect("amiya")
        try:
            eid = events.emit(c, "staff_question", dict(case_id=self.case, text="10kg だけにした場合と比べたい", key="q1"))
        finally:
            c.close()
        self.assertTrue(agents.wakes("amiya", dict(kind="staff_question", payload={})))
        c = db.connect("amiya")
        try:
            ev = events.get(c, eid)
        finally:
            c.close()
        ans = dict(answer="10kg だけの条件は仕入れ値の上限を超えるので、今の条件では実行できません。",
                   headline="15kg の案と 10kg だけの案を並べました", focus="compare", primary="A", alternative="B",
                   cards=[dict(card_type="quote", ref_id=self.t10, note="10kg の条件")], actions=["approve", "ask"],
                   suggested_questions=[], ask_human="15kg の案で進めますか？")
        client = MockClient([
            Reply(content=[{"type": "tool_use", "id": "t1", "name": "read_thread", "input": {"item_code": "aji"}}], usage=U),
            Reply(content=[{"type": "tool_use", "id": "t2", "name": "answer_staff", "input": ans}], usage=U)])
        r = agents.run_event(client, "amiya", ev, D)
        self.assertEqual(r["outcome"], "done")
        self.assertIn("10kg だけにした場合と比べたい", client.requests[0]["messages"][0]["content"])
        v = view()
        self.assertEqual((v["layout"]["focus"], v["layout"]["alternative"]), ("compare", "B"))
        self.assertEqual(v["turns"][-1]["question"], "10kg だけにした場合と比べたい")
        f = stage.flow()
        self.assertEqual(f["stage"], "decision")
        self.assertTrue(any(i["kind"] == "question" for i in f["items"]))
        self.assertTrue(any(i["kind"] == "answer" and i["focus"] == "compare" for i in f["items"]))

    def test_answer_with_a_wrong_amount_is_refused(self):
        c = db.connect("amiya")
        try:
            eid = events.emit(c, "staff_question", dict(case_id=self.case, text="利益は？", key="q2"))
        finally:
            c.close()
        bad = dict(answer="粗利は 99,999円です。", headline="答えました", focus="recommend", primary="A", alternative=None,
                   cards=[], actions=[], suggested_questions=[], ask_human=None)
        client = MockClient([Reply(content=[{"type": "tool_use", "id": "t1", "name": "answer_staff", "input": bad}], usage=U)])
        c = db.connect("amiya")
        try:
            ev = events.get(c, eid)
        finally:
            c.close()
        agents.run_event(client, "amiya", ev, D)
        steps = q("amiya", "SELECT tool, is_error FROM agent_step ORDER BY rowid")
        self.assertIn(("answer_staff", 1), steps)
        self.assertEqual(view()["turns"], [])

    # ── 段階と経過 ──
    def test_stage_and_processing_steps_come_from_records(self):
        self.assertEqual(stage.flow()["stage"], "decision")
        self.assertEqual(self.approve()["result"], "registered")
        f = stage.flow()
        self.assertEqual(f["stage"], "done")
        steps = [i["step"] for i in f["items"] if i["kind"] == "step"]
        self.assertEqual(steps, list(deal.STEPS))
        api = [i for i in f["items"] if i["kind"] == "api"]
        self.assertEqual(len(api), 2)
        self.assertTrue(all("両方の帳簿" in i["text"] for i in api))
        v = stage.verify()
        self.assertEqual({t["trade_id"] for t in v["trades"]}, {f"case{self.case}-buy", f"case{self.case}-sell-{self.ag}"})
        self.assertTrue(all(r["seller_booked"] and r["buyer_booked"] for r in v["reconcile"]))
        self.assertIn("market.propose", v["market_api"][0]["calls"])

    def test_flow_shows_only_amiya_facts(self):
        network.send("buyer_c", "iwase", "inquiry", "aji", "C社から漁協への問い合わせ", D, 5, 1600, side="buy")
        texts = [i["text"] for i in stage.flow()["items"]]
        self.assertFalse(any("C社 → 岩瀬漁協" in t for t in texts))
        self.assertTrue(any("網谷水産 → 岩瀬漁協" in t for t in texts))


class StageBeforeOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_idle_then_order(self):
        self.assertEqual(stage.flow()["stage"], "idle")
        scene.start()
        f = stage.flow()
        self.assertEqual(f["stage"], "order")
        self.assertTrue(any(i["kind"] == "order" and "スーパー" in i["text"] for i in f["items"]))


if __name__ == "__main__":
    unittest.main()
