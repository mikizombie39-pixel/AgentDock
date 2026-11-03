"""本編の場面の試験（模擬の Claude で回す。本物の Claude は呼ばない）。

順1 漁協の資料 → 順2 注文 → 順3 網谷水産が調べる → 順4 B社の掲示 → 順5 漁協に問い合わせ（断り・15kg の条件）
→ 順6 C社に問い合わせ（まとめての条件で断り）→ 順7 B社に問い合わせ（受ける）→ 順8 磁石ボードに候補
"""
import os
import tempfile
import unittest

from agentdock import agents, autonomy, db, events, scene
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
OPTS = [
    dict(code="B", label="漁協から 15kg、追加の 5kg を料亭へ", counterparty="iwase", quantity=15, unit_price_yen=1500, resell_to="料亭", reason="料亭は 4〜6kg を引き取ることが多い"),
    dict(code="C", label="漁協から 15kg、追加の 5kg を B社へ", counterparty="iwase", quantity=15, unit_price_yen=1500, resell_to="buyer_b", reason="B社が 1,650円で受けると返事"),
    dict(code="E", label="見送る", counterparty=None, quantity=None, unit_price_yen=None, resell_to=None, reason="注文に応えられない"),
]

AMIYA = [
    # 起動1（注文）：調べる → 漁協と C社に問い合わせ → 待つ
    tools(("read_orders", A), ("read_material", A), ("read_vault", A), ("read_customers", A), ("read_posts", A)),
    tools(("send_inquiry", dict(to_owner="iwase", item_code="aji", quantity=10, unit_price_yen=1400,
                                message="アジを 10kg、1kg 1,400円で譲っていただけますか。", request_id=None)),
          ("send_inquiry", dict(to_owner="buyer_c", item_code="aji", quantity=5, unit_price_yen=1600,
                                message="掲示のアジのうち 5kg だけ、1kg 1,600円で分けていただけますか。", request_id=1))),
    tools(("wait_for_replies", dict(reason="漁協と C社の返事を待つ"))),
    # 起動2（B社の掲示）：B社に売りたいと問い合わせる → 待つ
    tools(("send_inquiry", dict(to_owner="buyer_b", item_code="aji", quantity=5, unit_price_yen=1650,
                                message="掲示を拝見しました。アジ 5kg を 1kg 1,650円で、8:00 に受け渡しできます。", request_id=2)),
          ("wait_for_replies", dict(reason="B社の返事を待つ"))),
    # 起動3・4（漁協と C社の返事）：待つ
    tools(("read_thread", A), ("wait_for_replies", dict(reason="B社の返事を待つ"))),
    tools(("wait_for_replies", dict(reason="B社の返事を待つ"))),
    # 起動5（B社の返事）：磁石ボードに出す
    tools(("present_options", dict(summary="漁協は 15kg なら 1,500円。C社は 8kg まとめてのみ。B社は 5kg を 1,650円で受ける。", options=OPTS, recommended="C"))),
]
B = [
    # 起動1（料亭の注文）：調べる → 掲示を出す
    tools(("read_orders", A), ("read_vault", A), ("read_market", A)),
    tools(("post_request", dict(side="buy", item_code="aji", quantity=5, min_qty=5, unit_price_yen=1650,
                                message="アジを 5kg 買いたいです。今朝のうちに受け渡しできれば助かります。"))),
    # 起動2（網谷水産の問い合わせ）：受ける
    tools(("reply", dict(in_reply_to=5, kind="offer", quantity=5, unit_price_yen=1650, message="ありがとうございます。5kg を 1kg 1,650円で、8:00 にお願いします。"))),
]
IWASE = [
    tools(("read_stock", A)),
    tools(("reply", dict(in_reply_to=1, kind="offer", quantity=10, unit_price_yen=1400, message="10kg を 1,400円でお出しします。"))),   # 決まりが止める
    tools(("reply", dict(in_reply_to=1, kind="offer", quantity=15, unit_price_yen=1500,
                         message="その値では出せません。15kg まとめてなら 1kg 1,500円でお出しできます。"))),
]
C = [
    tools(("reply", dict(in_reply_to=2, kind="offer", quantity=5, unit_price_yen=1600, message="5kg でお分けします。"))),       # 決まりが止める
    tools(("reply", dict(in_reply_to=2, kind="decline", quantity=None, unit_price_yen=None,
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
        c = db.connect(owner)
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
        self.assertEqual([o["code"] for o in last["data"]["presented"]["options"]], ["B", "C", "E"])
        # 漁協が受け取った問い合わせの文に、網谷水産の上限・売り先は入っていない（開示の検査を通った文だけ）
        sent = self.q("amiya", "SELECT count(*) FROM agent_step WHERE tool = 'send_inquiry' AND is_error = 0")[0][0]
        self.assertEqual(sent, 3)

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
        self.assertIn("今日のこの品目のやり取り", prompt)


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

    def test_second_run_cannot_take_the_same_event(self):
        agents.run_event(MockClient([tools(("do_nothing", dict(reason="試験")))]), "amiya", self.ev, D)
        r = agents.run_event(MockClient([]), "amiya", self.ev, D)
        self.assertIsNone(r["outcome"])

    def test_tools_after_the_end_are_not_run(self):
        both = MockClient([tools(("wait_for_replies", dict(reason="待つ")),
                                 ("send_inquiry", dict(to_owner="iwase", item_code="aji", quantity=10, unit_price_yen=1400,
                                                       message="10kg いかがですか。", request_id=None)))])
        r = agents.run_event(both, "amiya", self.ev, D)
        self.assertEqual(r["outcome"], "waiting")
        c = db.connect("iwase")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM biz_event WHERE kind = 'quote_request'").fetchone()[0], 0)
        finally:
            c.close()

    def test_blocked_text_is_returned_as_an_error(self):
        leak = MockClient([tools(("send_inquiry", dict(to_owner="iwase", item_code="aji", quantity=10, unit_price_yen=1400,
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
