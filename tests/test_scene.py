"""本編の場面の試験（模擬の Claude で回す。本物の Claude は呼ばない）。

順1 漁協の資料 → 順2 注文 → 順3 網谷水産が調べる → 順4 B社の掲示 → 順5 漁協に問い合わせ（断り・15kg の条件）
→ 順6 C社に問い合わせ（まとめての条件で断り）→ 順7 B社に問い合わせ（受ける）→ 順8 磁石ボードに候補
"""
import os
import tempfile
import unittest

from agentdock import agents, autonomy, db, events, market, scene
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
U = dict(input_tokens=3000, output_tokens=400)
_n = [0]


def tools(*calls):
    """calls は (name, input) の並び。1回の返事で複数の道具を呼ぶ。"""
    blocks = []
    for name, inp in calls:
        _n[0] += 1
        blocks.append({"type": "tool_use", "id": f"tu{_n[0]}", "name": name, "input": inp})
    return Reply(content=[{"type": "text", "text": "調べます。"}, *blocks], usage=U)


A = {"item_code": "aji"}
OPTS = [   # 条件の番号：漁協の 10kg＝1・15kg＝2、B社の受け入れ＝条件 3（合意 1）。網谷水産のアジの注文＝注文 3
    dict(code="A", label="漁協から 15kg、10kg をスーパー、5kg を B社へ", buy_term_id=2,
         allocations=[dict(kind="order", ref_id=3, customer=None, quantity=10), dict(kind="agreement", ref_id=1, customer=None, quantity=5)],
         reason="B社が受けると返事をくれたので、追加の 5kg の売り先がある", risk="B社との合意は条件付き"),
    dict(code="B", label="漁協から 15kg、追加の 5kg を料亭へ", buy_term_id=2,
         allocations=[dict(kind="order", ref_id=3, customer=None, quantity=10), dict(kind="customer_lead", ref_id=None, customer="料亭", quantity=5)],
         reason="料亭は 4〜6kg を引き取ることが多い", risk="当日の朝の連絡は断られることがある（Vault）"),
    dict(code="E", label="見送る", buy_term_id=None, allocations=[], reason="注文に応えられない", risk="スーパーの注文を落とす"),
]
CARDS = [dict(card_type="order", ref_id=3, note=None), dict(card_type="quote", ref_id=2, note="15kg まとめての条件"),
         dict(card_type="agreement", ref_id=1, note=None), dict(card_type="vault", ref_id=1, note="同じ形で売り切った事例")]

AMIYA = [
    # 起動1（注文）：調べる → 漁協と C社に問い合わせ → 待つ
    tools(("read_orders", A), ("read_material", A), ("read_vault", A), ("read_customers", A), ("read_posts", A)),
    tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=10, unit_price_yen=1400,
                                message="アジを 10kg、1kg 1,400円で譲っていただけますか。", request_id=None)),
          ("send_inquiry", dict(to_owner="buyer_c", side="buy", item_code="aji", quantity=5, unit_price_yen=1600,
                                message="掲示のアジのうち 5kg だけ、1kg 1,600円で分けていただけますか。", request_id=1))),
    tools(("wait_for_replies", dict(reason="漁協と C社の返事を待つ"))),
    # 起動2（B社の掲示）：B社に売りたいと問い合わせる → 待つ
    tools(("send_inquiry", dict(to_owner="buyer_b", side="sell", item_code="aji", quantity=5, unit_price_yen=1650,
                                message="掲示を拝見しました。アジ 5kg を 1kg 1,650円で、8:00 に受け渡しできます。", request_id=2)),
          ("wait_for_replies", dict(reason="B社の返事を待つ"))),
    # 起動3・4（漁協と C社の返事）：待つ
    tools(("read_thread", A), ("wait_for_replies", dict(reason="B社の返事を待つ"))),
    tools(("wait_for_replies", dict(reason="B社の返事を待つ"))),
    # 起動5（B社の返事）：磁石ボードに出す
    tools(("present_options", dict(item_code="aji", summary="漁協は 15kg なら 1,500円。C社は 8kg まとめてのみ。B社は 5kg を 1,650円で受ける。",
                                   cards=CARDS, options=OPTS, recommended="A"))),
]
B = [
    # 起動1（料亭の注文）：調べる → 掲示を出す
    tools(("read_orders", A), ("read_vault", A), ("read_market", A)),
    tools(("post_request", dict(side="buy", item_code="aji", quantity=5, min_qty=5, unit_price_yen=1650,
                                message="アジを 5kg 買いたいです。今朝のうちに受け渡しできれば助かります。"))),
    # 起動2（網谷水産の問い合わせ）：受ける
    tools(("reply", dict(in_reply_to=5, kind="offer", terms=[dict(quantity=5, unit_price_yen=1650)], message="ありがとうございます。5kg を 1kg 1,650円で、8:00 にお願いします。"))),
]
IWASE = [
    tools(("read_stock", A)),
    tools(("reply", dict(in_reply_to=1, kind="offer", terms=[dict(quantity=10, unit_price_yen=1400)], message="10kg を 1,400円でお出しします。"))),   # 決まりが止める
    tools(("reply", dict(in_reply_to=1, kind="offer", terms=[dict(quantity=10, unit_price_yen=1720), dict(quantity=15, unit_price_yen=1500)],
                         message="その値では出せません。10kg なら 1kg 1,720円、15kg まとめてなら 1kg 1,500円でお出しできます。"))),
]
C = [
    tools(("reply", dict(in_reply_to=2, kind="offer", terms=[dict(quantity=5, unit_price_yen=1600)], message="5kg でお分けします。"))),       # 決まりが止める
    tools(("reply", dict(in_reply_to=2, kind="decline", terms=[],
                         message="申し訳ありません。8kg まとめてでのみお譲りしています。"))),
]


class SceneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def q(self, owner, sql, args=()):
        c = db.connect(owner if owner != "market" else db.MARKET)
        try:
            return [tuple(r) for r in c.execute(sql, args)]
        finally:
            c.close()

    def test_whole_scene_with_mock_partners(self):
        scene.start()
        clients = {"amiya": MockClient(AMIYA), "buyer_b": MockClient(B), "iwase": MockClient(IWASE), "buyer_c": MockClient(C)}
        log = scene.run(clients)
        self.assertEqual([(x["owner"], x["event_kind"], x["outcome"]) for x in log], [
            ("amiya", "order_new", "waiting"),
            ("buyer_b", "order_new", "done"),
            ("iwase", "quote_request", "done"),
            ("buyer_c", "quote_request", "done"),
            ("amiya", "info_received", "waiting"),
            ("amiya", "quote_received", "waiting"),
            ("amiya", "quote_received", "waiting"),
            ("buyer_b", "quote_request", "done"),
            ("amiya", "quote_received", "presented"),
        ])
        self.assertIsNone(scene.next_event())                               # 起こすべき出来事は残っていない
        for name, cl in clients.items():
            self.assertEqual(cl.replies, [], name)                         # 台本を使い切った（余分な呼び出しが無い）
        # 漁協：最初の条件（1,400円）は決まりが止め、15kg × 1,500円 で仮押さえ
        self.assertEqual(self.q("iwase", "SELECT buyer, quantity, unit_price_yen, status FROM stock_hold"), [("amiya", 15, 1500, "held")])
        self.assertEqual(self.q("iwase", "SELECT tool, is_error FROM agent_step ORDER BY id"),
                         [("read_stock", 0), ("reply", 1), ("reply", 0)])
        # C社：5kg の条件は「8kg まとめて」の決まりが止め、断りの返事
        self.assertEqual(self.q("buyer_c", "SELECT tool, is_error FROM agent_step ORDER BY id"), [("reply", 1), ("reply", 0)])
        # 組織ごとに別の DB に別の起動の記録（独立の証拠）
        runs = {o: self.q(o, "SELECT count(*) FROM agent_run")[0][0] for o in db.OWNER_CODES}
        self.assertEqual(runs, {"amiya": 5, "iwase": 1, "buyer_b": 2, "buyer_c": 1})
        # 最後の起動は候補を出した
        last = log[-1]
        self.assertEqual([o["code"] for o in last["data"]["presented"]["options"]], ["A", "B", "E"])
        # 磁石ボード：金額はコードが計算する（15kg × 1,500円・加工 15kg × 200円・売り 10 × 2,100 + 5 × 1,650）
        from agentdock import workspace
        c = db.connect("amiya")
        try:
            v = workspace.view(c)
        finally:
            c.close()
        a = next(o for o in v["options"] if o["code"] == "A")["numbers"]
        self.assertEqual((a["revenue_yen"], a["purchase_yen"], a["process_cost_yen"], a["gross_profit_yen"]), (29250, 22500, 3000, 3750))
        self.assertEqual([l["certainty"] for l in a["lines"]], ["order", "conditional"])
        self.assertTrue(a["executable"])
        b = next(o for o in v["options"] if o["code"] == "B")["numbers"]
        self.assertEqual([l["certainty"] for l in b["lines"]], ["order", "lead"])
        self.assertEqual([c["card_type"] for c in v["cards"]], ["order", "quote", "agreement", "vault"])
        self.assertEqual(next(o for o in v["options"] if o["recommended"])["code"], "A")
        # 漁協が受け取った問い合わせの文に、網谷水産の上限・売り先は入っていない（開示の検査を通った文だけ）
        sent = self.q("amiya", "SELECT count(*) FROM agent_step WHERE tool = 'send_inquiry' AND is_error = 0")[0][0]
        self.assertEqual(sent, 3)
        # 条件の番号：漁協の2つの条件は同じ仮押さえ（15kg）に結びつく。B社の受け入れは条件付きの合意（事前の承認の内）
        hold = self.q("iwase", "SELECT id FROM stock_hold")[0][0]
        self.assertEqual(self.q("market", "SELECT seller, buyer, quantity, unit_price_yen, hold_ref, status FROM offer_term WHERE seller = 'iwase' ORDER BY id"),
                         [("iwase", "amiya", 10, 1720, hold, "offered"), ("iwase", "amiya", 15, 1500, hold, "offered")])
        self.assertEqual(self.q("market", "SELECT seller, buyer, quantity, unit_price_yen, status FROM agreement"),
                         [("amiya", "buyer_b", 5, 1650, "conditional")])
        self.assertIn("網谷水産の担当者の承認", self.q("market", "SELECT condition FROM agreement")[0][0])

    def test_prompt_carries_the_thread(self):
        scene.start()
        scene.run({"amiya": MockClient(AMIYA), "buyer_b": MockClient(B), "iwase": MockClient(IWASE), "buyer_c": MockClient(C)}, max_runs=3)
        c = db.connect("amiya")
        try:
            ev = [e for e in events.pending(c) if e["kind"] == "quote_received"][0]
        finally:
            c.close()
        prompt, item = agents.situation("amiya", ev, D)
        self.assertEqual(item, "aji")
        self.assertIn("15kg まとめてなら", prompt)
        self.assertIn("条件 1：10 で 1,720円", prompt)                          # 条件の番号と組が Claude に渡る
        self.assertIn("条件 2：15 で 1,500円", prompt)
        self.assertIn("今日のこの品目のやり取り", prompt)


class PreapprovalTest(unittest.TestCase):
    """B社の AI は、担当者が事前に承認した条件（5kg・1,700円まで）の範囲で掲示と合意ができる。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        scene.start()
        c = db.connect("buyer_b")
        try:
            self.ev = [e for e in events.pending(c) if e["kind"] == "order_new"][0]
        finally:
            c.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_post_above_preapproval_is_refused(self):
        cl = MockClient([tools(("post_request", dict(side="buy", item_code="aji", quantity=5, min_qty=5, unit_price_yen=1750, message="アジを 5kg 買いたいです。"))),
                         tools(("do_nothing", dict(reason="試験")))])
        agents.run_event(cl, "buyer_b", self.ev, D)
        c = db.connect("buyer_b")
        try:
            row = c.execute("SELECT is_error, result_text FROM agent_step WHERE tool = 'post_request'").fetchone()
        finally:
            c.close()
        self.assertEqual(row[0], 1)
        self.assertIn("事前に承認した条件", row[1])

    def test_acceptance_above_preapproval_waits_for_staff(self):
        from agentdock import network
        q = network.send("amiya", "buyer_b", "inquiry", "aji", "アジ 5kg を 1kg 1,750円でお譲りできます。", D, 5, 1750, side="sell")
        c = db.connect("buyer_b")
        try:
            ev = [e for e in events.pending(c) if e["kind"] == "quote_request"][0]
        finally:
            c.close()
        cl = MockClient([tools(("reply", dict(in_reply_to=q, kind="offer", terms=[dict(quantity=5, unit_price_yen=1750)], message="お願いします。")))])
        agents.run_event(cl, "buyer_b", ev, D)
        m = db.connect("market")
        try:
            self.assertEqual(m.execute("SELECT status FROM agreement").fetchone()[0], "needs_staff")
        finally:
            m.close()


class LoopSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        o = sc.AJI_ORDER
        c = db.connect("amiya")
        try:
            _, self.eid = events.order_received(c, o["customer"], o["item"], o["quantity"], o["sell_price_yen"], D)
            self.ev = events.get(c, self.eid)
        finally:
            c.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_call_cap_stops_the_run(self):
        endless = MockClient([tools(("read_market", A)) for _ in range(30)])
        r = agents.run_event(endless, "amiya", self.ev, D)
        self.assertEqual((r["outcome"], r["calls"]), ("max_turns", autonomy.MAX_CALLS))
        self.assertEqual(autonomy.MAX_CALLS, 20)
        self.assertEqual(len(endless.replies), 10)
        c = db.connect("amiya")
        try:
            self.assertEqual(events.get(c, self.eid)["status"], "handled")
            self.assertEqual(c.execute("SELECT count(*) FROM api_usage").fetchone()[0], 20)
        finally:
            c.close()

    def test_failure_returns_the_event(self):
        class Broken:
            def create(self, **kw):
                raise ConnectionError("network down")
        r = agents.run_event(Broken(), "amiya", self.ev, D)
        self.assertEqual(r["outcome"], "error")
        c = db.connect("amiya")
        try:
            self.assertEqual([e["id"] for e in events.pending(c)], [self.eid])
            self.assertEqual(c.execute("SELECT outcome FROM agent_run").fetchone()[0], "error")
        finally:
            c.close()

    def test_scene_stops_at_the_first_error(self):
        class Broken:
            calls = 0

            def create(self, **kw):
                Broken.calls += 1
                raise ConnectionError("network down")
        log = scene.run({o: Broken() for o in db.OWNER_CODES})
        self.assertEqual([x["outcome"] for x in log], ["error"])
        self.assertEqual(Broken.calls, 1)                                    # 同じ出来事を呼び直さない

    def test_real_usage_is_recorded(self):
        r = agents.run_event(MockClient([tools(("do_nothing", dict(reason="試験")))]), "amiya", self.ev, D, run_kind="real")
        self.assertEqual(r["outcome"], "nothing")
        c = db.connect("amiya")
        try:
            self.assertEqual(c.execute("SELECT run_kind FROM api_usage").fetchall()[0][0], "full_run")
            self.assertEqual(c.execute("SELECT run_kind FROM agent_run").fetchall()[0][0], "real")
        finally:
            c.close()

    def test_second_run_cannot_take_the_same_event(self):
        agents.run_event(MockClient([tools(("do_nothing", dict(reason="試験")))]), "amiya", self.ev, D)
        r = agents.run_event(MockClient([]), "amiya", self.ev, D)
        self.assertIsNone(r["outcome"])

    def test_tools_after_the_end_are_not_run(self):
        both = MockClient([tools(("wait_for_replies", dict(reason="待つ")),
                                 ("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=10, unit_price_yen=1400,
                                                       message="10kg いかがですか。", request_id=None)))])
        r = agents.run_event(both, "amiya", self.ev, D)
        self.assertEqual(r["outcome"], "waiting")
        c = db.connect("iwase")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM biz_event WHERE kind = 'quote_request'").fetchone()[0], 0)
        finally:
            c.close()

    def test_blocked_text_is_returned_as_an_error(self):
        leak = MockClient([tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=10, unit_price_yen=1400,
                                                       message="上限は 1,600円です。", request_id=None))),
                           tools(("do_nothing", dict(reason="試験")))])
        agents.run_event(leak, "amiya", self.ev, D)
        c = db.connect("amiya")
        try:
            row = c.execute("SELECT is_error, result_text FROM agent_step WHERE tool = 'send_inquiry'").fetchone()
        finally:
            c.close()
        self.assertEqual(row[0], 1)
        self.assertIn("書き直してください", row[1])
        self.assertTrue(leak.requests[1]["messages"][-1]["content"][0]["is_error"])   # Claude には誤りとして返った


if __name__ == "__main__":
    unittest.main()


class TermsTest(unittest.TestCase):
    """返事の条件を2つ以上示す：条件ごとの決まりの検査・仮押さえは最も多い数量の1件・送れなければ押さえない。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        from agentdock import network
        self.network = network
        self.q = network.send("amiya", "iwase", "inquiry", "aji", "アジを 10kg、1kg 1,400円でいかがでしょうか。", D, 10, 1400, side="buy")
        c = db.connect("iwase")
        try:
            self.ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def iwase(self, sql):
        c = db.connect("iwase")
        try:
            return [tuple(r) for r in c.execute(sql)]
        finally:
            c.close()

    def answer(self, *replies):
        return agents.run_event(MockClient(list(replies) + [tools(("do_nothing", dict(reason="試験")))]), "iwase", self.ev, D)

    def test_one_term_below_floor_blocks_the_whole_reply(self):
        self.answer(tools(("reply", dict(in_reply_to=self.q, kind="offer", message="10kg 1,720円・15kg 1,450円でどうぞ。",
                                         terms=[dict(quantity=10, unit_price_yen=1720), dict(quantity=15, unit_price_yen=1450)]))))
        self.assertEqual(self.iwase("SELECT count(*) FROM stock_hold"), [(0,)])
        self.assertEqual([m["kind"] for m in agents.thread("amiya", "aji", D)], ["inquiry"])   # 返事は送られていない

    def test_blocked_text_leaves_no_hold(self):
        self.answer(tools(("reply", dict(in_reply_to=self.q, kind="offer", message="最低でも 1,700円です。15kg なら 1,600円。",
                                         terms=[dict(quantity=15, unit_price_yen=1600)]))))
        self.assertEqual(self.iwase("SELECT count(*) FROM stock_hold"), [(0,)])        # 開示の検査で止まった文は押さえを作らない

    def test_two_terms_travel_and_hold_the_larger(self):
        self.answer(tools(("reply", dict(in_reply_to=self.q, kind="offer", message="10kg なら 1,720円、15kg まとめてなら 1,600円です。",
                                         terms=[dict(quantity=10, unit_price_yen=1720), dict(quantity=15, unit_price_yen=1600)]))))
        self.assertEqual(self.iwase("SELECT buyer, quantity, unit_price_yen, status FROM stock_hold"), [("amiya", 15, 1600, "held")])
        reply = agents.thread("amiya", "aji", D)[-1]
        self.assertEqual(reply["terms"], [(10, 1720), (15, 1600)])

    def test_decline_has_no_terms(self):
        r = self.answer(tools(("reply", dict(in_reply_to=self.q, kind="decline", message="お断りします。",
                                             terms=[dict(quantity=10, unit_price_yen=1720)]))),
                        tools(("reply", dict(in_reply_to=self.q, kind="decline", message="今朝は見送らせてください。", terms=[]))))
        self.assertEqual(r["outcome"], "done")
        self.assertEqual(agents.thread("amiya", "aji", D)[-1]["kind"], "decline")

    def test_amiya_is_told_to_add_a_price(self):
        self.assertIn("問い合わせには希望の値を添えます。", agents.SYSTEMS["amiya"])


class VariantTest(unittest.TestCase):
    """条件を変えた通しの種（業務 DB の事実だけを変える。Claude への指示は変えない）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def one(self, owner, sql):
        c = db.connect(owner)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_price_up(self):
        scene.apply_variant("price_up")
        self.assertEqual([tuple(r) for r in self.one("iwase", "SELECT min_qty, floor_yen FROM quote_rule ORDER BY min_qty")], [(1, 1700), (15, 1640)])

    def test_vault_changed(self):
        scene.apply_variant("vault_changed")
        self.assertIn("休業", self.one("amiya", "SELECT body FROM vault_note WHERE kind = 'customer'")[0][0])

    def test_b_declines(self):
        scene.apply_variant("b_declines")
        st = scene.start(variant="b_declines")
        self.assertNotIn("b_order", st)
        self.assertEqual(self.one("buyer_b", "SELECT status FROM preapproval")[0][0], "cancelled")
        self.assertEqual(self.one("buyer_b", "SELECT count(*) FROM biz_event WHERE kind = 'order_new'")[0][0], 0)
        self.assertEqual(len(market.requests_visible_to("amiya", "aji", now=agents.demo_now(D))), 2)   # C社と B社の掲示
        with self.assertRaises(ValueError):
            scene.apply_variant("unknown")
