"""ブリ（本）の交渉し直しの試験（模擬の Claude で回す。本物の Claude は呼ばない）。

  朝の注文：スーパー 4本（22,000円・種）。料亭の追加の注文 6本（24,000円）が出来事として入り、網谷水産の AI が起きる。
  漁協の相対の残りは 6本、最低の値は 17,000円（明日の見込みが多いので 16,500 + 500）。
  網谷水産の仕入れ値の上限：スーパー 22,000 − 2,000 − 1,500 ＝ 18,500円／料亭 24,000 − 3,500 ＝ 20,500円。

前の版（版 1）：網谷水産は 6本を 17,500円で問い合わせ、漁協は 6本 18,000円（仮押さえ 6本）と答えた。
ボードの案 A は 6本をスーパー 4本・料亭 2本に分ける。
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from agentdock import agents, consult, criteria, db, deal, events, network, scene, server, workspace
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
NOW = "2026-11-02T06:30:00+09:00"
U = dict(input_tokens=3000, output_tokens=400)
B = {"item_code": "buri_xl"}
_n = [0]


def tools(*calls):
    blocks = []
    for name, inp in calls:
        _n[0] += 1
        blocks.append({"type": "tool_use", "id": f"bt{_n[0]}", "name": name, "input": inp})
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


def holds():
    return q("iwase", "SELECT h.quantity, h.unit_price_yen, h.status FROM stock_hold h JOIN stock s ON s.id = h.stock_id "
                      "WHERE s.lot_no = 'S-buri_xl-1' ORDER BY h.id")


def view():
    c = db.connect("amiya")
    try:
        return workspace.view(c)
    finally:
        c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.started = scene.start(item="buri_xl")                                   # 始まり：スーパーの注文 4本が出来事として入る
        self.sup = self.started["amiya_order"][0]
        self.wake = scene.next_event()
        c = db.connect("amiya")
        try:                                                                          # 料亭の追加の注文 6本（作業の途中で入る）
            self.rest, _ = events.order_received(c, "料亭", "buri_xl", 6, 24000, D)
        finally:
            c.close()
        settle()
        self.q1 = network.send("amiya", "iwase", "inquiry", "buri_xl", "ブリを 6本、1本 17,500円でいかがでしょうか。", D, 6, 17500, side="buy")
        c = db.connect("iwase")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([tools(("reply", dict(in_reply_to=self.q1, kind="offer", terms=[dict(quantity=6, unit_price_yen=18000)],
                                                          message="6本なら 1本 18,000円でお出しできます。", reason="今朝の相対の残りは 6本です")))]),
                         "iwase", ev, D)
        self.t6 = network.terms_for_message(self.q1 + 1)[0]["id"]
        settle()
        payload = dict(item_code="buri_xl", summary="漁協は 6本なら 18,000円。注文の 10本には足りません。", recommended="A",
                       headline="ブリが 4本足りません", ask_human="この配分で進めますか",
                       cards=[dict(card_type="order", ref_id=self.sup, note=None), dict(card_type="order", ref_id=self.rest, note=None)],
                       options=[dict(code="A", label="漁協から 6本、スーパーに 4本・料亭に 2本", reason="朝の注文を先に満たす", risk="料亭は 4本足りない",
                                     buy_term_id=self.t6, allocations=[dict(kind="order", ref_id=self.sup, customer=None, quantity=4),
                                                                      dict(kind="order", ref_id=self.rest, customer=None, quantity=2)]),
                                dict(code="E", label="見送る", reason="条件が合わない", risk="注文を落とす", buy_term_id=None, allocations=[])])
        c = db.connect("amiya")
        try:
            r = workspace.build(c, "amiya", "buri_xl", D, payload, 1, NOW)
        finally:
            c.close()
        self.case, self.cons = r["case_id"], r["consultation_id"]

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def change10(self):   # 両方の注文を満たす数量（優先は変えない・担当者が決められる）
        return dict(quantity=10, allocations=[dict(order_id=self.sup, quantity=4), dict(order_id=self.rest, quantity=6)])

    def rest6(self):      # スーパーを欠いて料亭へ 6本（顧客の間の優先を変える・社長が要る）
        return dict(quantity=6, allocations=[dict(order_id=self.rest, quantity=6)])

    def reinquiry(self, qty, price):
        return [tools(("read_orders", B), ("read_thread", B)),
                tools(("send_inquiry", dict(to_owner="iwase", side="buy", item_code="buri_xl", quantity=qty, unit_price_yen=price, request_id=None,
                                            message=f"条件を変えて、ブリを {qty}本、1本 {price:,}円でお願いできますか。", reason="注文の数が変わったため"))),
                tools(("wait_for_replies", dict(reason="漁協の返事を待つ")))]

    def offer(self, qty, price, reason=None):
        return tools(("reply", dict(in_reply_to=self.q1 + 2, kind="offer", terms=[dict(quantity=qty, unit_price_yen=price)],
                                    message=f"{qty}本なら 1本 {price:,}円でお出しします。", reason=reason)))

    def present(self, term_id, allocs, recommended="A"):
        opts = [dict(code="E", label="見送る", reason="条件が合わない", risk="注文を落とす", buy_term_id=None, allocations=[])]
        if term_id:
            opts.insert(0, dict(code="A", label="漁協の返事の条件で仕入れる", reason="担当者が変えた条件", risk="足りない注文がある",
                                buy_term_id=term_id, allocations=[dict(kind="order", ref_id=o, customer=None, quantity=n) for o, n in allocs]))
        return tools(("present_options", dict(item_code="buri_xl", summary="漁協の返事を確かめました。", headline="変えた条件の返事が届きました",
                                              ask_human="この条件で進めますか", cards=[dict(card_type="order", ref_id=self.sup, note=None)],
                                              options=opts, recommended=recommended)))

    def run_all(self, amiya, iwase):
        clients = {"amiya": MockClient(amiya), "iwase": MockClient(iwase), "buyer_b": MockClient([]), "buyer_c": MockClient([])}
        log = scene.run(clients)
        for name, cl in clients.items():
            self.assertEqual(cl.replies, [], name)
        return log, clients

    def last_steps(self, owner):
        return q(owner, "SELECT tool, is_error, result_text FROM agent_step WHERE run_id = (SELECT max(id) FROM agent_run) ORDER BY seq")


class SeedTest(Base):
    def test_buri_seed_values(self):
        self.assertEqual(q("iwase", "SELECT s.quantity, s.status FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'buri_xl'"), [(6, "for_sale")])
        c = db.connect("iwase")
        try:
            self.assertEqual(criteria.floor_yen(c, "buri_xl", D), (17000, True))
        finally:
            c.close()
        self.assertEqual(q("amiya", "SELECT c.name, o.quantity, o.sell_price_yen FROM order_book o JOIN customer c ON c.id = o.customer_id "
                                    "JOIN item i ON i.id = o.item_id WHERE i.code = 'buri_xl' ORDER BY o.id"), [("スーパー", 4, 22000), ("料亭", 6, 24000)])
        owner, ev = self.wake                                                              # 料亭の注文の出来事で網谷水産が起きる
        self.assertEqual((owner, ev["kind"], ev["payload"]["item"], ev["payload"]["quantity"]), ("amiya", "order_new", "buri_xl", 4))
        prompt, item = agents.situation(owner, ev, D)
        self.assertEqual(item, "buri_xl")
        self.assertIn("4本", prompt)
        # ブリの加工の費用（1本 2,000円）がボードの計算に入る。上限は売り先ごと
        a = next(o for o in view()["options"] if o["code"] == "A")
        n = a["numbers"]
        self.assertTrue(a["executable"])
        self.assertEqual((n["process_cost_yen"], n["gross_profit_yen"]), (12000, 4 * 22000 + 2 * 24000 - 6 * 18000 - 12000))
        self.assertEqual(sorted(l["ceiling_yen"] for l in n["limits"]["buy_ceiling"]), [18500, 20500])
        self.assertEqual(view()["item"], dict(code="buri_xl", name="ブリ（特大）", unit="本"))

    def test_iwase_reply_below_the_floor_is_stopped_in_code(self):
        q2 = network.send("amiya", "iwase", "inquiry", "buri_xl", "ブリを 6本、1本 16,000円でいかがでしょうか。", D, 6, 16000, side="buy")
        c = db.connect("iwase")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([
            tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=6, unit_price_yen=16900)], message="6本なら 1本 16,900円です。", reason=None))),
            tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=6, unit_price_yen=17000)], message="6本なら 1本 17,000円です。", reason=None)))]),
            "iwase", ev, D)
        steps = self.last_steps("iwase")
        self.assertEqual([(s[0], s[1]) for s in steps], [("reply", 1), ("reply", 0)])
        self.assertIn("最低の値を下回ります", steps[0][2])
        self.assertEqual(q("market", "SELECT count(*) FROM network_message WHERE unit_price_yen = 16900"), [(0,)])
        self.assertEqual(holds(), [(6, 18000, "released"), (6, 17000, "held")])
        c = db.connect("iwase")
        try:
            stock = json.loads(agents.read_stock(type("C", (), dict(con=c, on_date=D, owner="iwase"))(), B))
        finally:
            c.close()
        self.assertEqual(stock["floor_yen"], 17000)                                        # 自社の最低の値（相手には書かない）


class RenegotiationTest(Base):
    def test_staff_quantity_change_reaches_iwase_and_replaces_the_hold(self):
        r = answer(self.cons, "staff-1", self.change10())
        self.assertEqual((r["result"], r["priority_change"]), ("answered", False))
        log, clients = self.run_all(
            self.reinquiry(10, 18000) + [tools(("compare_options", dict(options=[dict(code="A", buy_term_id=self.t6 + 1, allocations=[
                dict(kind="order", ref_id=self.sup, customer=None, quantity=4), dict(kind="order", ref_id=self.rest, customer=None, quantity=2)])]))),
                                         self.present(self.t6 + 1, [(self.sup, 4), (self.rest, 2)])],
            [tools(("read_stock", B)), self.offer(10, 18000), self.offer(6, 18000, "今朝の相対の残りは 6本です")])
        self.assertEqual([(x["owner"], x["event_kind"], x["outcome"]) for x in log], [
            ("amiya", "human_choice", "waiting"), ("iwase", "quote_request", "done"), ("amiya", "quote_received", "presented")])
        self.assertIsNone(scene.next_event())                                              # 二度目の交渉し直しは起きない
        prompt = clients["amiya"].requests[0]["messages"][0]["content"]
        self.assertIn("仕入れの数量 10本", prompt)
        self.assertIn(f"注文 {self.rest}（料亭）に 6本", prompt)
        self.assertEqual(q("market", "SELECT from_owner, to_owner, kind, quantity, unit_price_yen FROM network_message WHERE id = ?", (self.q1 + 2,)),
                         [("amiya", "iwase", "inquiry", 10, 18000)])
        steps = self.last_steps("iwase")
        self.assertEqual([(s[0], s[1]) for s in steps], [("read_stock", 0), ("reply", 1), ("reply", 0)])
        self.assertIn("出せるのは 6本", steps[1][2])
        self.assertEqual(holds(), [(6, 18000, "released"), (6, 18000, "held")])           # 前の押さえは下ろし、新しい返事で押さえる
        self.assertEqual(q("market", "SELECT id, status FROM offer_term ORDER BY id"), [(self.t6, "released"), (self.t6 + 1, "offered")])
        cmp_ = q("amiya", "SELECT is_error, result_text FROM agent_step WHERE tool = 'compare_options'")[0]
        self.assertEqual(cmp_[0], 0)                                                       # 品目は出来事から（aji にしない）
        self.assertEqual(json.loads(cmp_[1])["options"][0]["purchase_yen"], 108000)
        self.assertEqual(q("amiya", "SELECT task_version, status FROM consultation WHERE case_id = ? ORDER BY id", (self.case,)),
                         [(1, "answered"), (2, "open")])
        self.assertEqual(q("amiya", "SELECT count(*) FROM deal_case"), [(1,)])           # 同じ案件の版 2（注文の並びが変わっても）

    def test_less_stock_changes_iwase_answer_and_amiya_consults_again(self):
        ex("iwase", "UPDATE stock SET quantity = 4 WHERE lot_no = 'S-buri_xl-1'")
        answer(self.cons, "staff-1", self.change10())
        log, _ = self.run_all(self.reinquiry(10, 18000) + [self.present(self.t6 + 1, [(self.sup, 4)])],
                              [tools(("read_stock", B)), self.offer(6, 18000), self.offer(4, 18000, "今朝お出しできるのは 4本です")])
        self.assertEqual([x["outcome"] for x in log], ["waiting", "done", "presented"])
        self.assertIn("出せるのは 4本", self.last_steps("iwase")[1][2])
        self.assertEqual(holds(), [(6, 18000, "released"), (4, 18000, "held")])
        self.assertEqual(q("amiya", "SELECT task_version, status FROM consultation WHERE case_id = ? ORDER BY id", (self.case,)),
                         [(1, "answered"), (2, "open")])

    def test_higher_floor_goes_over_the_ceiling_then_the_president_reallocates_and_it_closes(self):
        ex("iwase", "UPDATE seller_rule SET hold_up_yen = ?", (sc.SCENE_C_HOLD_UP_YEN,))      # 最低の値 19,000円
        answer(self.cons, "staff-1", self.change10())
        self.run_all(self.reinquiry(10, 18000) + [self.present(self.t6 + 1, [(self.sup, 4), (self.rest, 2)])],
                     [tools(("read_stock", B)), self.offer(6, 18000), self.offer(6, 19000, "明日も多い見込みのため")])
        self.assertIn("最低の値を下回ります", self.last_steps("iwase")[1][2])
        v = view()
        a = next(o for o in v["options"] if o["code"] == "A")
        self.assertFalse(a["executable"])                                                   # 19,000円はスーパーの上限 18,500円を超える
        self.assertTrue(any("18,500円" in e for e in a["numbers"]["errors"]))
        self.assertEqual((v["consultation"]["version"], v["consultation"]["status"]), (2, "open"))   # 担当者に相談し直す
        # 担当者が料亭へ 6本に変える → 優先の変更なので社長の答えで確定する
        cons2 = v["consultation"]["consultation_id"]
        self.assertEqual(answer(cons2, "staff-2", self.rest6())["result"], "needs_president")
        self.assertEqual(view()["consultation"]["change"], dict(quantity=6, allocations=[dict(order_id=self.rest, customer="料亭", quantity=6)]))
        self.assertEqual(answer(cons2, "president-2", self.rest6(), role="president")["result"], "answered")
        self.q1 += 2                                                                          # 次の問い合わせは 5 番
        self.run_all(self.reinquiry(6, 19000) + [self.present(self.t6 + 2, [(self.rest, 6)])],
                     [tools(("read_stock", B)), self.offer(6, 19000)])
        self.assertEqual(holds(), [(6, 18000, "released"), (6, 19000, "released"), (6, 19000, "held")])
        a = next(o for o in view()["options"] if o["code"] == "A")
        self.assertTrue(a["executable"])                                                    # 料亭の上限 20,500円の内
        # 承認：スーパーを欠く案だが、その優先は社長が相談の答えで決めた（人の決定どおりの案）。担当者の権限の内（114,000円）なので担当者が確定できる
        r = deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "staff-approve")
        self.assertEqual(r["result"], "registered")
        self.assertEqual(q("market", "SELECT t.seller, t.buyer, l.item_code, t.quantity, t.unit_price_yen, t.status FROM trade t JOIN lot l ON l.id = t.lot_id"),
                         [("iwase", "amiya", "buri_xl", 6, 19000, "registered")])
        self.assertEqual(holds()[-1], (6, 19000, "confirmed"))
        self.assertEqual(q("amiya", "SELECT o.status FROM order_book o WHERE o.id IN (?, ?) ORDER BY o.id", (self.sup, self.rest)),
                         [("open",), ("filled",)])


class AnswerTest(Base):
    def test_priority_change_needs_the_president(self):
        r = answer(self.cons, "staff-1", self.rest6())
        self.assertEqual((r["result"], r["priority_change"]), ("needs_president", True))
        self.assertEqual(human_events(), [])
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "staff-2", self.rest6())
        r = answer(self.cons, "president-1", self.rest6(), role="president")
        self.assertEqual(r["result"], "answered")
        (_, _, payload), = human_events()
        self.assertEqual(json.loads(payload)["answered_by"], "president")

    def test_staff_approval_of_a_split_that_shorts_a_customer_needs_the_president(self):
        a = next(o for o in view()["options"] if o["code"] == "A")                        # スーパー 4本・料亭 2本（料亭が 4本足りない）
        self.assertEqual(deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "s1")["result"], "needs_president")
        self.assertEqual(deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "p1", actor="president")["result"], "registered")

    def test_short_on_both_orders_is_still_a_priority_change(self):
        # 漁協が 4本だけの時：料亭に 4本・スーパーに 0本は、料亭も欠けているが、スーパーを欠いて料亭へ回す答え
        self.assertEqual(answer(self.cons, "staff-1", dict(quantity=4, allocations=[dict(order_id=self.rest, quantity=4)]))["result"], "needs_president")
        self.assertEqual(human_events(), [])
        self.assertEqual(answer(self.cons, "president-1", dict(quantity=4, allocations=[dict(order_id=self.rest, quantity=4)]),
                                role="president")["result"], "answered")

    def test_staff_cannot_approve_a_board_option_that_shorts_the_other_customer(self):
        c = db.connect("amiya")
        try:                                                                                # 版 2：料亭に 4本だけ回す案（スーパーは 0本）
            workspace.build(c, "amiya", "buri_xl", D, dict(item_code="buri_xl", summary="出し直し", recommended="A", cards=[], options=[
                dict(code="A", label="料亭に 4本", reason="r", risk="r", buy_term_id=self.t6,
                     allocations=[dict(kind="order", ref_id=self.rest, customer=None, quantity=4), dict(kind="unallocated", ref_id=None, customer=None, quantity=2)])]), 2, NOW)
        finally:
            c.close()
        a = next(o for o in view()["options"] if o["code"] == "A")
        self.assertEqual(deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "s1")["result"], "needs_president")
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(0,)])

    def test_duplicate_key_and_stale_version_do_not_renegotiate_twice(self):
        r1 = answer(self.cons, "k1", self.change10())
        r2 = answer(self.cons, "k1", self.change10())
        self.assertEqual((r1["duplicate"], r2["duplicate"]), (False, True))
        self.assertEqual(len(human_events()), 1)
        c = db.connect("amiya")
        try:                                                                                # ボードが版 2 に出し直された後の、版 1 への答え
            workspace.build(c, "amiya", "buri_xl", D, dict(item_code="buri_xl", summary="出し直し", recommended="E", cards=[],
                                                            options=[dict(code="E", label="見送る", reason="r", risk="r", buy_term_id=None, allocations=[])]), 2, NOW)
        finally:
            c.close()
        with self.assertRaises(consult.ConsultError):
            answer(self.cons, "k2", self.rest6())
        self.assertEqual(len(human_events()), 1)

    def test_take_change_with_allocations_by_customer(self):   # 撮影の入口（scene.staff_change）も同じ答えの道
        r = scene.staff_change(6, {"料亭": 6})
        self.assertEqual((r["result"], r["change"]["allocations"]), ("needs_president", [dict(order_id=self.rest, quantity=6)]))
        r = scene.president_answer()
        self.assertEqual(r["result"], "answered")
        self.assertEqual(len(human_events()), 1)


class WordingTest(Base):
    def test_no_aji_in_a_buri_case(self):
        answer(self.cons, "staff-1", self.change10())
        _, clients = self.run_all(self.reinquiry(10, 18000) + [self.present(self.t6 + 1, [(self.sup, 4), (self.rest, 2)])],
                                  [tools(("read_stock", B)), self.offer(6, 18000)])
        for name in ("amiya", "iwase"):
            for req in clients[name].requests:
                text = json.dumps(dict(system=req["system"], tools=req["tools"], messages=req["messages"]), ensure_ascii=False)
                for bad in ("aji", "アジ"):
                    self.assertNotIn(bad, text, name)
            self.assertNotIn("kg", clients[name].requests[0]["messages"][0]["content"], name)
            self.assertIn("品目のコード buri_xl", clients[name].requests[0]["messages"][0]["content"], name)   # コードを推測させない（2026-11-03 実測）
            self.assertNotIn("fish", clients[name].requests[0]["messages"][0]["content"], name)


    def test_internal_buri_values_do_not_leave(self):
        from agentdock import disclosure
        for body, reason in (("ブリを 6本、1本 18,000円で。18,500円までなら出せます。", None),        # スーパーの上限
                             ("ブリを 6本、1本 18,000円でお願いします。", "料亭の注文が入ったため"),     # 売り先の名前
                             ("ブリを 6本、1本 18,000円でお願いします。", "1本 2,000円の加工がかかるため")):   # 加工の費用
            with self.assertRaises(disclosure.DisclosureError):
                network.send("amiya", "iwase", "inquiry", "buri_xl", body, D, 6, 18000, side="buy", reason=reason)
        with self.assertRaises(disclosure.DisclosureError):                                 # 漁協の最低の値も出せない
            network.send("iwase", "amiya", "offer", "buri_xl", "1本 18,000円です。17,000円より下はお出しできません。", D,
                         terms=[(6, 18000)], in_reply_to=self.q1, valid_until="2026-11-02T07:00:00+09:00")
        self.assertEqual(q("market", "SELECT count(*) FROM network_message"), [(2,)])


class ApiTest(Base):
    def setUp(self):
        super().setUp()
        self.srv = self.serve()

    def serve(self):
        srv = server.make_server(0)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        srv._th = th
        self.base = f"http://127.0.0.1:{srv.server_address[1]}"
        return srv

    def stop(self, srv):
        srv.shutdown()
        srv.server_close()
        srv._th.join(timeout=5)

    def tearDown(self):
        self.stop(self.srv)
        super().tearDown()

    def call(self, path, obj=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8") if obj is not None else None,
                                     method="POST" if obj is not None else "GET", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_thread_board_and_answers_survive_a_restart(self):
        s, r = self.call("/api/answer", dict(owner="amiya", consultation_id=self.cons, option="change", idempotency_key="scr-1", change=self.rest6()))
        self.assertEqual((s, r["result"]), (200, "needs_president"))
        s, b = self.call("/api/board?owner=amiya")
        self.assertEqual((b["consultation"]["status"], b["item"]["unit"]), ("needs_president", "本"))
        self.assertEqual([(o["customer"], o["quantity"]) for o in b["orders"]], [("スーパー", 4), ("料亭", 6)])
        s, r = self.call("/api/answer", dict(owner="amiya", consultation_id=self.cons, option="change", idempotency_key="scr-2",
                                            change=b["consultation"]["change"], role="president"))
        self.assertEqual((s, r["result"]), (200, "answered"))
        s, t = self.call("/api/thread?owner=amiya&item=buri_xl")
        self.assertEqual(t["unit"], "本")
        rows = [(m["from_owner"], m["to_owner"], m["kind"], m["quantity"], m["unit_price_yen"], m["hold_state"], m["reason"]) for m in t["messages"]]
        self.assertEqual(rows, [("amiya", "iwase", "inquiry", 6, 17500, None, None),
                                ("iwase", "amiya", "offer", 6, 18000, "held", "今朝の相対の残りは 6本です")])
        before = (b["case"]["id"], t["messages"])
        self.stop(self.srv)                                                                 # サーバーを止めて起こし直しても、記録から同じ画面になる
        self.srv = self.serve()
        s, b2 = self.call("/api/board?owner=amiya")
        s, t2 = self.call("/api/thread?owner=amiya&item=buri_xl")
        self.assertEqual((b2["case"]["id"], t2["messages"]), before)
        self.assertEqual(b2["consultation"]["status"], "answered")
        self.assertEqual(len(human_events()), 1)
        self.assertEqual(self.call("/api/thread?owner=buyer_b&item=buri_xl")[1]["messages"], [])   # ほかの組織には見えない

    def test_start_by_item(self):
        os.environ["AGENTDOCK_DATA"] = tempfile.mkdtemp(dir=self.tmp.name)
        self.assertEqual(build.main([]), 0)
        s, r = self.call("/api/start", dict(item="buri_xl"))
        self.assertEqual((s, r["item"]), (200, "buri_xl"))
        self.assertEqual(q("amiya", "SELECT c.name, o.quantity FROM order_book o JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id "
                                    "WHERE i.code = 'buri_xl' ORDER BY o.id"), [("スーパー", 4)])   # 料亭の注文は途中で入る
        self.assertEqual(self.call("/api/start", dict(item="nori"))[0], 400)


if __name__ == "__main__":
    unittest.main()
