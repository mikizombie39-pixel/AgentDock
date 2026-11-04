"""担当者の答えで条件を変え、交渉し直す試験（アジ・模擬の Claude で回す。本物の Claude は呼ばない）。

  磁石ボードの相談に担当者が数量・配分を変えて答える（API）→ 出来事 human_choice → 網谷水産の AI が問い合わせ直す
  → 岩瀬漁協の AI が自社の在庫・最低の値・権限で答える → 前の仮押さえは新しい返事に置き換わる
  → 網谷水産の AI が返事を確かめ、続けられればボードを出し直し、できなければ担当者に相談し直す

前の版（版 1）：網谷水産は 10kg を 1,400円で問い合わせ、漁協は 10kg なら 1,700円（仮押さえ 10kg）と答えた。
10kg × 1,700円は網谷水産の仕入れ値の上限（1,600円）を超えるので、ボードの案は実行できない。
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from agentdock import agents, consult, db, deal, disclosure, events, network, scene, server, workspace
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
NOW = "2026-11-02T06:30:00+09:00"
U = dict(input_tokens=3000, output_tokens=400)
A = {"item_code": "aji"}
_n = [0]


def tools(*calls):
    blocks = []
    for name, inp in calls:
        _n[0] += 1
        blocks.append({"type": "tool_use", "id": f"rt{_n[0]}", "name": name, "input": inp})
    return Reply(content=[{"type": "text", "text": "調べます。"}, *blocks], usage=U)


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


def settle():
    """前の版までの出来事は済みにする（この試験は、担当者の答えから後を見る）。"""
    for o in db.OWNER_CODES:
        ex(o, "UPDATE biz_event SET status = 'handled', handled_at = ? WHERE status = 'pending'", (NOW,))


def answer(cid, key, change, role="staff"):
    c = db.connect("amiya")
    try:
        return consult.answer(c, cid, "change", key, change=change, role=role)
    finally:
        c.close()


def human_events():
    return q("amiya", "SELECT id, status, payload_json FROM biz_event WHERE kind = 'human_choice' ORDER BY id")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        c = db.connect("amiya")
        try:
            self.order, _ = events.order_received(c, "スーパー", "aji", 10, 2100, D)
        finally:
            c.close()
        self.q1 = network.send("amiya", "iwase", "inquiry", "aji", "アジを 10kg、1kg 1,400円でいかがでしょうか。", D, 10, 1400, side="buy")
        c = db.connect("iwase")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([tools(("reply", dict(in_reply_to=self.q1, kind="offer", terms=[dict(quantity=10, unit_price_yen=1700)],
                                                          message="10kg なら 1kg 1,700円でお出しできます。", reason="10kg では値を下げられません")))]),
                         "iwase", ev, D)
        self.t10 = network.terms_for_message(self.q1 + 1)[0]["id"]
        self.hold10 = q("iwase", "SELECT id FROM stock_hold")[0][0]
        settle()
        payload = dict(item_code="aji", summary="漁協は 10kg なら 1,700円。仕入れ値の上限を超えます。", recommended="E", headline="10kg では条件が合いません",
                       ask_human="数量を変えて交渉し直しますか", cards=[dict(card_type="order", ref_id=self.order, note=None)],
                       options=[dict(code="A", label="漁協から 10kg", reason="注文の数", risk="上限を超える", buy_term_id=self.t10,
                                     allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10)]),
                                dict(code="E", label="見送る", reason="条件が合わない", risk="注文を落とす", buy_term_id=None, allocations=[])])
        c = db.connect("amiya")
        try:
            r = workspace.build(c, "amiya", "aji", D, payload, 1, NOW)
        finally:
            c.close()
        self.case, self.cons = r["case_id"], r["consultation_id"]

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def change15(self):
        return dict(quantity=15, allocations=[dict(order_id=self.order, quantity=10), dict(customer="料亭", quantity=5)])

    def amiya_reinquiry(self, *before):
        """網谷水産の AI：変えた条件を確かめ直し、漁協に 15kg を 1,500円で問い合わせ直して待つ。"""
        return [tools(("read_orders", A), ("read_thread", A)), *before,
                tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=15, unit_price_yen=1500, request_id=None,
                                            message="条件を変えて、アジを 15kg まとめて 1kg 1,500円でお願いできますか。",
                                            reason="まとめて引き取る先の見込みが立ったため"))),
                tools(("wait_for_replies", dict(reason="漁協の返事を待つ")))]

    def renegotiate(self, iwase_script, amiya_after):
        r = answer(self.cons, "staff-1", self.change15())
        self.assertEqual((r["result"], r["duplicate"], r["priority_change"]), ("answered", False, False))
        owner, ev = scene.next_event()
        self.assertEqual((owner, ev["kind"], ev["payload"]["source"]), ("amiya", "human_choice", "consultation"))
        clients = {"amiya": MockClient(self.amiya_reinquiry() + amiya_after), "iwase": MockClient(iwase_script),
                   "buyer_b": MockClient([]), "buyer_c": MockClient([])}
        log = scene.run(clients)
        for name, cl in clients.items():
            self.assertEqual(cl.replies, [], name)
        return log, clients

    def present(self, term_id, recommended="A"):
        opts = [dict(code="E", label="見送る", reason="条件が合わない", risk="注文を落とす", buy_term_id=None, allocations=[])]
        cards = [dict(card_type="order", ref_id=self.order, note=None)]
        if term_id:
            opts.insert(0, dict(code="A", label="漁協から 15kg、10kg をスーパー、5kg を料亭へ", reason="担当者が変えた配分", risk="料亭は未打診",
                                buy_term_id=term_id, allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10),
                                                                   dict(kind="customer_lead", ref_id=None, customer="料亭", quantity=5)]))
            cards.append(dict(card_type="quote", ref_id=term_id, note=None))
        return tools(("present_options", dict(item_code="aji", summary="漁協の返事を確かめました。", headline="変えた条件の返事が届きました",
                                              ask_human="この条件で進めますか", cards=cards, options=opts, recommended=recommended)))


class RenegotiationTest(Base):
    def test_quantity_change_drives_a_renegotiation(self):
        log, clients = self.renegotiate(
            [tools(("read_stock", A)),
             tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1500)],
                                  message="15kg まとめてなら 1kg 1,500円でお出しします。", reason="まとめてなら値を下げられます")))],
            [self.present(self.t10 + 1)])
        self.assertEqual([(x["owner"], x["event_kind"], x["outcome"]) for x in log], [
            ("amiya", "human_choice", "waiting"), ("iwase", "quote_request", "done"), ("amiya", "quote_received", "presented")])
        self.assertIsNone(scene.next_event())                                         # 二度目の交渉し直しは起きない
        # 網谷水産の AI に渡った状況：変えた数量と配分（答えの記録からコードが書く）
        prompt = clients["amiya"].requests[0]["messages"][0]["content"]
        self.assertIn("仕入れの数量 15kg", prompt)
        self.assertIn("料亭（注文なし・未打診）に 5kg", prompt)
        # 漁協の仮押さえ：前の 10kg は下ろし、新しい 15kg を押さえる
        self.assertEqual(q("iwase", "SELECT id, quantity, unit_price_yen, status FROM stock_hold ORDER BY id"),
                         [(self.hold10, 10, 1700, "released"), (self.hold10 + 1, 15, 1500, "held")])
        self.assertEqual(q("market", "SELECT id, quantity, unit_price_yen, hold_ref, status FROM offer_term ORDER BY id"),
                         [(self.t10, 10, 1700, self.hold10, "released"), (self.t10 + 1, 15, 1500, self.hold10 + 1, "offered")])
        det = json.loads(q("iwase", "SELECT detail FROM audit_event WHERE event = 'hold_replaced'")[0][0])
        self.assertEqual((det["released"], det["new_hold"]), ([self.hold10], self.hold10 + 1))
        # 相談：版 1 は答え済み（担当者・変えた中身つき）、版 2 の相談が開いている
        rows = q("amiya", "SELECT task_version, status, answered_by, change_json FROM consultation WHERE case_id = ? ORDER BY id", (self.case,))
        self.assertEqual([r[:3] for r in rows], [(1, "answered", "staff"), (2, "open", None)])
        self.assertEqual(json.loads(rows[0][3])["quantity"], 15)
        self.assertEqual([(r[1]) for r in human_events()], ["handled"])
        # 続けられる：版 2 のボードの案 A は実行できる。担当者の権限の内（22,500円 ≤ 表の 500,000円）で確定できる
        c = db.connect("amiya")
        try:
            v = workspace.view(c)
        finally:
            c.close()
        self.assertEqual((v["case"]["version"], v["case"]["status"], v["consultation"]["version"]), (2, "presented", 2))
        a = next(o for o in v["options"] if o["code"] == "A")
        self.assertTrue(a["executable"])
        self.assertEqual(a["numbers"]["purchase"]["amount_yen"], 22500)
        r = deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "approve-1")
        self.assertEqual(r["result"], "registered")
        self.assertEqual(q("market", "SELECT seller, buyer, quantity, unit_price_yen, status FROM trade"), [("iwase", "amiya", 15, 1500, "registered")])
        self.assertEqual(q("iwase", "SELECT status FROM stock_hold ORDER BY id"), [("released",), ("confirmed",)])

    def test_less_stock_changes_iwase_answer_and_amiya_consults_again(self):
        ex("iwase", "UPDATE stock SET quantity = 12 WHERE lot_no = 'S-aji-1'")          # 漁協の在庫だけを変える
        log, clients = self.renegotiate(
            [tools(("read_stock", A)),
             tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1500)],
                                  message="15kg まとめてなら 1kg 1,500円でお出しします。", reason=None))),          # 在庫の決まりが止める
             tools(("reply", dict(in_reply_to=self.q1 + 2, kind="decline", terms=[], message="申し訳ありません。今朝は 15kg をお出しできません。",
                                  reason="在庫が足りないため")))],
            [self.present(None, recommended="E")])
        self.assertEqual([x["outcome"] for x in log], ["waiting", "done", "presented"])
        steps = q("iwase", "SELECT tool, is_error, result_text FROM agent_step WHERE run_id = (SELECT max(id) FROM agent_run) ORDER BY seq")
        self.assertEqual([(s[0], s[1]) for s in steps], [("read_stock", 0), ("reply", 1), ("reply", 0)])
        self.assertIn("出せるのは 12kg", steps[1][2])
        stock = json.loads(steps[0][2])                                                  # 12kg − 網谷水産の押さえ 10kg。同じ相手には 12kg まで出せる
        self.assertEqual((stock["available"], [(h["buyer"], h["quantity"]) for h in stock["holds"]]), (2, [("amiya", 10)]))
        # 断りの返事も、前の仮押さえに代わる（古い 10kg の押さえは残らない）
        self.assertEqual(q("iwase", "SELECT quantity, status FROM stock_hold ORDER BY id"), [(10, "released")])
        self.assertEqual(q("market", "SELECT status FROM offer_term ORDER BY id"), [("released",)])
        # 網谷水産は担当者に相談し直す：版 2 の相談が開き、ボードを出し直した
        self.assertEqual(q("amiya", "SELECT task_version, status FROM consultation WHERE case_id = ? ORDER BY id", (self.case,)),
                         [(1, "answered"), (2, "open")])
        self.assertEqual(q("amiya", "SELECT version, status FROM deal_case"), [(2, "presented")])

    def test_higher_floor_changes_iwase_terms_and_amiya_consults_again(self):
        ex("iwase", "UPDATE quote_rule SET floor_yen = 1640 WHERE min_qty = 15")         # 漁協の最低の値だけを変える
        log, _ = self.renegotiate(
            [tools(("read_stock", A)),
             tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1500)],
                                  message="15kg まとめてなら 1kg 1,500円でお出しします。", reason=None))),          # 最低の値の決まりが止める
             tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1640)],
                                  message="15kg まとめてなら 1kg 1,640円でお出しします。", reason="今朝の水揚げの値が上がったため")))],
            [self.present(self.t10 + 1, recommended="E")])
        self.assertEqual([x["outcome"] for x in log], ["waiting", "done", "presented"])
        self.assertEqual(q("iwase", "SELECT quantity, unit_price_yen, status FROM stock_hold ORDER BY id"), [(10, 1700, "released"), (15, 1640, "held")])
        c = db.connect("amiya")
        try:
            v = workspace.view(c)
        finally:
            c.close()
        a = next(o for o in v["options"] if o["code"] == "A")
        self.assertFalse(a["executable"])                                               # 1,640円は網谷水産の仕入れ値の上限を超える（コードが止める）
        self.assertTrue(any("上限" in e for e in a["numbers"]["errors"]))
        self.assertEqual(v["consultation"]["status"], "open")                           # 担当者に相談し直す

    def test_internal_limit_cannot_leave_in_the_renegotiation(self):
        leaks = [tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=15, unit_price_yen=1500, request_id=None,
                                             message="アジを 15kg、1kg 1,500円でお願いできますか。", reason="上限が 1,600円なので"))),
                 tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="aji", quantity=15, unit_price_yen=1500, request_id=None,
                                             message="アジを 15kg、1kg 1,500円でお願いできますか。1,600円までなら出せます。", reason=None)))]
        answer(self.cons, "staff-1", self.change15())
        owner, ev = scene.next_event()
        cl = MockClient(self.amiya_reinquiry(*leaks))
        agents.run_event(cl, owner, ev, D)
        self.assertEqual(q("amiya", "SELECT is_error FROM agent_step WHERE tool = 'send_inquiry' ORDER BY id"), [(1,), (1,), (0,)])
        sent = q("market", "SELECT body, coalesce(reason, '') FROM network_message WHERE from_owner = 'amiya' ORDER BY id")
        self.assertEqual(len(sent), 2)                                                  # 前の版の 1 通と、検査を通った 1 通だけ
        for body, reason in sent:
            for bad in ("1,600", "1600", "上限", "スーパー", "料亭"):
                self.assertNotIn(bad, body + reason)
        with self.assertRaises(disclosure.DisclosureError):
            network.send("amiya", "iwase", "inquiry", "aji", "アジを 15kg お願いします。", D, 15, 1500, side="buy", reason="仕入れの上限が 1,600円のため")
        self.assertEqual(len(q("market", "SELECT id FROM network_message WHERE from_owner = 'amiya'")), 2)


class AnswerOnceTest(Base):
    def test_duplicate_key_does_not_renegotiate_twice(self):
        r1 = answer(self.cons, "k1", self.change15())
        r2 = answer(self.cons, "k1", self.change15())
        self.assertEqual((r1["duplicate"], r2["duplicate"], r2["result"]), (False, True, "answered"))
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "k2", dict(quantity=12))                                   # 答え済みの相談に別の鍵で答えても通らない
        self.assertEqual(len(human_events()), 1)

    def test_stale_version_does_not_renegotiate(self):
        c = db.connect("amiya")
        try:                                                                              # 答える前に、ボードが版 2 に出し直された
            payload = dict(item_code="aji", summary="出し直し", recommended="E", cards=[],
                           options=[dict(code="E", label="見送る", reason="r", risk="r", buy_term_id=None, allocations=[])])
            new = workspace.build(c, "amiya", "aji", D, payload, 2, NOW)["consultation_id"]
        finally:
            c.close()
        with self.assertRaises(consult.StaleError):
            answer(self.cons, "old-1", self.change15())
        self.assertEqual(human_events(), [])
        self.assertEqual(q("amiya", "SELECT id, status FROM consultation WHERE case_id = ? ORDER BY id", (self.case,)),
                         [(self.cons, "stale"), (new, "open")])

    def test_bad_change_is_refused(self):
        for bad in (None, dict(quantity=0), dict(quantity="15"), dict(quantity=15, allocations=[dict(order_id=self.order, quantity=10)]),
                    dict(quantity=5, allocations=[dict(order_id=999, quantity=5)]), dict(quantity=5, allocations=[dict(customer="誰か", quantity=5)])):
            with self.assertRaises(consult.ConsultError, msg=bad):
                answer(self.cons, f"bad-{bad}", bad)
        self.assertEqual(human_events(), [])

    def test_no_change_after_the_case_is_closed(self):
        deal.decide("amiya", self.case, None, "reject", None, "reject-1")
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "late-1", self.change15())
        self.assertEqual(human_events(), [])


    def test_take_change_uses_the_same_answer_path(self):
        r = scene.staff_change(10)                                                        # 撮影の途中の担当者の操作
        self.assertEqual((r["result"], r["change"]), ("answered", dict(quantity=10, allocations=[dict(order_id=self.order, quantity=10)])))
        self.assertEqual(len(human_events()), 1)
        self.assertEqual(scene.staff_change(10)["result"], "no_open_consultation")      # 答え済みの相談に二度は答えない
        self.assertEqual(len(human_events()), 1)

class AuthorityTest(Base):
    def setUp(self):
        super().setUp()
        c = db.connect("amiya")
        try:
            self.order_r, _ = events.order_received(c, "料亭", "aji", 5, 2400, D)     # 料亭の注文も入る（売り先が2つ）
        finally:
            c.close()
        settle()

    def priority_change(self):   # 12kg しか無い時に、料亭の注文を欠いてスーパーを満たす
        return dict(quantity=12, allocations=[dict(order_id=self.order, quantity=10), dict(order_id=self.order_r, quantity=2)])

    def test_staff_cannot_finalize_a_priority_change_president_can(self):
        c = db.connect("amiya")   # 社長の今日の指示が無い朝（指示どおりの優先は test_mate で確かめる）
        try:
            with c:
                c.execute("UPDATE directive SET status = 'withdrawn'")
        finally:
            c.close()
        r = answer(self.cons, "staff-1", self.priority_change())
        self.assertEqual((r["result"], r["priority_change"]), ("needs_president", True))
        self.assertEqual(human_events(), [])                                           # 交渉し直しは起きない
        self.assertEqual(q("amiya", "SELECT status FROM deal_case"), [("presented",)])
        self.assertEqual(answer(self.cons, "staff-1", self.priority_change())["result"], "needs_president")   # 押し直し
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "staff-2", self.priority_change())                         # 担当者がもう一度答えても確定しない
        r = answer(self.cons, "president-1", self.priority_change(), role="president")
        self.assertEqual(r["result"], "answered")
        (eid, st, payload), = human_events()
        p = json.loads(payload)
        self.assertEqual((p["answered_by"], p["priority_change"], p["change"]["quantity"]), ("president", True, 12))
        self.assertEqual(q("amiya", "SELECT status, answered_by FROM consultation WHERE id = ?", (self.cons,)), [("answered", "president")])

    def test_change_without_priority_is_the_staffs(self):
        r = answer(self.cons, "staff-1", dict(quantity=15, allocations=[dict(order_id=self.order, quantity=10), dict(order_id=self.order_r, quantity=5)]))
        self.assertEqual((r["result"], r["priority_change"]), ("answered", False))

    def test_unsplit_cut_below_both_orders_is_a_priority_change(self):
        self.assertEqual(answer(self.cons, "staff-1", dict(quantity=12))["result"], "needs_president")

    def test_who_may_change_priority_comes_from_the_table(self):
        ex("amiya", "UPDATE authority SET may_change_priority = 1 WHERE role = 'staff'")
        self.assertEqual(answer(self.cons, "staff-1", self.priority_change())["result"], "answered")
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "intern-1", self.priority_change(), role="intern")


class ApprovalLimitTest(Base):
    """交渉し直した後の承認：担当者は表の上限（500,000円）まで。超えれば社長。"""

    def board_after(self):
        self.renegotiate([tools(("read_stock", A)),
                          tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1500)],
                                               message="15kg まとめてなら 1kg 1,500円でお出しします。", reason=None)))],
                         [self.present(self.t10 + 1)])
        c = db.connect("amiya")
        try:
            return next(o for o in workspace.view(c)["options"] if o["code"] == "A")
        finally:
            c.close()

    def test_over_the_staff_limit_needs_the_president(self):
        a = self.board_after()
        ex("amiya", "UPDATE authority SET max_trade_yen = 20000 WHERE role = 'staff'")   # 金額はコードでなく表から
        r = deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "staff-approve")
        self.assertEqual(r["result"], "needs_president")
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(0,)])
        r = deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "president-approve", actor="president")
        self.assertEqual(r["result"], "registered")


class ApiTest(Base):
    """画面の操作（API）：相談への答え・やり取りの読み出し。"""

    def setUp(self):
        super().setUp()
        self.srv = server.make_server(0)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(timeout=5)
        super().tearDown()

    def call(self, path, obj=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8") if obj is not None else None,
                                     method="POST" if obj is not None else "GET", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_answer_by_api_and_read_the_thread(self):
        body = dict(owner="amiya", consultation_id=self.cons, option="change", idempotency_key="screen-1", change=self.change15())
        s, r = self.call("/api/answer", body)
        self.assertEqual((s, r["result"], r["duplicate"]), (200, "answered", False))
        s, r = self.call("/api/answer", body)
        self.assertEqual((s, r["duplicate"]), (200, True))                               # 押し直しは同じ結果・出来事は1件
        self.assertEqual(len(human_events()), 1)
        clients = {"amiya": MockClient(self.amiya_reinquiry() + [self.present(self.t10 + 1)]),
                   "iwase": MockClient([tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=15, unit_price_yen=1500)],
                                                             message="15kg まとめてなら 1kg 1,500円でお出しします。", reason="まとめてなら値を下げられます")))]),
                   "buyer_b": MockClient([]), "buyer_c": MockClient([])}
        scene.run(clients)
        s, r = self.call("/api/thread?owner=amiya&item=aji")
        self.assertEqual(s, 200)
        got = [(m["from_owner"], m["to_owner"], m["kind"], m["quantity"], m["unit_price_yen"], m["valid_until"], m["hold_state"], m["reason"])
               for m in r["messages"]]
        until = "2026-11-02T07:00:00+09:00"
        self.assertEqual(got, [
            ("amiya", "iwase", "inquiry", 10, 1400, None, None, None),
            ("iwase", "amiya", "offer", 10, 1700, until, "released", "10kg では値を下げられません"),
            ("amiya", "iwase", "inquiry", 15, 1500, None, None, "まとめて引き取る先の見込みが立ったため"),
            ("iwase", "amiya", "offer", 15, 1500, until, "held", "まとめてなら値を下げられます"),
        ])
        self.assertEqual([m["frm"] for m in r["messages"]], ["網谷水産", "岩瀬漁協", "網谷水産", "岩瀬漁協"])
        self.assertEqual(r["messages"][3]["terms"][0]["hold_state"], "held")
        self.assertEqual(self.call("/api/thread?owner=buyer_c&item=aji")[1]["messages"], [])   # 自社が送り手・受け手の文だけ
        self.assertEqual(self.call("/api/thread?owner=market&item=aji")[0], 400)
        # 承認の API：社長の役割は表にある役割だけ
        self.assertEqual(self.call("/api/decide", dict(owner="amiya", case_id=self.case, option_id=1, action="approve",
                                                       fingerprint="x", idempotency_key="d1", actor="intern"))[0], 400)

    def test_stale_answer_by_api_is_409(self):
        c = db.connect("amiya")
        try:
            workspace.build(c, "amiya", "aji", D, dict(item_code="aji", summary="出し直し", recommended="E", cards=[],
                                                        options=[dict(code="E", label="見送る", reason="r", risk="r", buy_term_id=None, allocations=[])]), 2, NOW)
        finally:
            c.close()
        s, r = self.call("/api/answer", dict(owner="amiya", consultation_id=self.cons, option="change", idempotency_key="old", change=self.change15()))
        self.assertEqual((s, r["error"]), (409, "stale"))
        self.assertEqual(human_events(), [])


if __name__ == "__main__":
    unittest.main()
