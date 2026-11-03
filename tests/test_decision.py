"""人の決定を AI の次の案と承認の制約にする（2026-11-03 撮影 B：社長はスーパー 4本と決めたが、AI は料亭 4本を勧め、担当者だけで確定した）。

  相談に答えて確定した数量・配分（誰が・どの版で）を記録する → 次の present_options で、決定と違う案は道具が断る
  → 決定と違うと明示した案（differs_from_decision）は出せるが、決定した人か網谷さんだけが承認できる
"""
import json

from agentdock import agents, consult, db, deal, workspace
from agentdock.companion import MockClient
from tests import test_buri as tb

B = tb.B


class DecisionTest(tb.Base):
    def decide(self, change, role):
        r = tb.answer(self.cons, f"{role}-1", change, role=role)
        self.assertIn(r["result"], ("answered", "needs_president"))
        if r["result"] == "needs_president":
            r = tb.answer(self.cons, "president-1", change, role="president")
        self.assertEqual(r["result"], "answered")

    def offer_and_present(self, qty, price, present_calls):
        """網谷水産が問い合わせ直し、漁協が答え、網谷水産の AI が案を出す（present_calls は道具の呼び出しの並び）。"""
        clients = {"amiya": MockClient(self.reinquiry(qty, price) + present_calls), "iwase": MockClient([tb.tools(("read_stock", B)), self.offer(qty, price)]),
                   "buyer_b": MockClient([]), "buyer_c": MockClient([])}
        import agentdock.scene as scene
        scene.run(clients)
        return clients

    def present(self, term_id, options, short="どちらで進めますか"):
        return tb.tools(("present_options", dict(item_code="buri_xl", summary="漁協の返事を確かめました。", headline="返事が届きました", short_question=short,
                                                 ask_human="どちらで進めますか", cards=[], options=options, recommended=options[0]["code"])))

    def opt(self, code, term_id, allocs, differs=False, short="案"):
        return dict(code=code, label=f"案 {code}", short_label=short, reason="r", risk="r", buy_term_id=term_id, differs_from_decision=differs,
                    allocations=[dict(kind="order", ref_id=o, customer=None, quantity=n) for o, n in allocs])

    def test_decision_is_recorded_with_who_and_version(self):
        self.decide(dict(quantity=4, allocations=[dict(order_id=self.sup, quantity=4)]), "president")
        rows = tb.q("amiya", "SELECT case_id, consultation_id, version, quantity, allocations_json, priority_change, decided_by FROM human_decision")
        self.assertEqual(len(rows), 1)
        case_id, cid, version, qty, allocs, prio, by = rows[0]
        self.assertEqual((case_id, cid, version, qty, prio, by), (self.case, self.cons, 1, 4, 1, "president"))
        self.assertEqual(json.loads(allocs), [dict(order_id=self.sup, customer="スーパー", quantity=4)])

    def test_take_b_option_against_the_presidents_decision_is_refused_then_routed_to_the_president(self):
        tb.ex("iwase", "UPDATE stock SET quantity = 4 WHERE lot_no = 'S-buri_xl-1'")       # 撮影 B：漁協は 4本だけ
        self.decide(dict(quantity=4, allocations=[dict(order_id=self.sup, quantity=4)]), "president")   # 社長：スーパー 4本
        t4 = self.t6 + 1
        self.offer_and_present(4, 17800, [
            self.present(t4, [self.opt("A", t4, [(self.rest, 4)], short="料亭に 4本")]),                       # 決定と違う（断られる）
            self.present(t4, [self.opt("A", t4, [(self.sup, 4)], short="スーパーに 4本"),
                              self.opt("B", t4, [(self.rest, 4)], differs=True, short="料亭に 4本")])])           # 決定と違うと明示
        steps = tb.q("amiya", "SELECT is_error, result_text FROM agent_step WHERE tool = 'present_options' ORDER BY id")
        self.assertEqual([s[0] for s in steps], [1, 0])
        for text in ("社長", "スーパー", "4本", "differs_from_decision"):
            self.assertIn(text, steps[0][1])                                                 # AI に決定の中身を返す
        v = tb.view()
        a, b = (next(o for o in v["options"] if o["code"] == c) for c in "AB")
        self.assertTrue(b["plan"]["differs_from_decision"])
        # 決定と違う案：担当者は確定できない（決定したのは社長）。社長なら確定できる
        self.assertEqual(deal.decide("amiya", self.case, b["id"], "approve", b["fingerprint"], "s-b")["result"], "needs_president")
        # 決定どおりの案：優先は社長が決めた。担当者の権限の内（71,200円）なので担当者が確定できる
        self.assertEqual(deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "s-a")["result"], "registered")
        self.assertEqual(tb.q("amiya", "SELECT o.status FROM order_book o WHERE o.id = ?", (self.sup,)), [("filled",)])

    def test_l6_ai_split_after_ryotei_six_is_refused(self):
        self.decide(dict(quantity=6, allocations=[dict(order_id=self.rest, quantity=6)]), "president")  # 社長：料亭 6本
        t = self.t6 + 1
        self.offer_and_present(6, 18000, [
            self.present(t, [self.opt("A", t, [(self.sup, 4), (self.rest, 2)], short="分けて 4本・2本")]),      # 決定と違う（断られる）
            self.present(t, [self.opt("A", t, [(self.rest, 6)], short="料亭に 6本")])])
        self.assertEqual(tb.q("amiya", "SELECT is_error FROM agent_step WHERE tool = 'present_options' ORDER BY id"), [(1,), (0,)])
        a = next(o for o in tb.view()["options"] if o["code"] == "A")
        self.assertEqual(deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "s")["result"], "registered")

    def test_less_than_decided_is_consistent_and_staff_decision_lets_staff_approve_a_differing_option(self):
        self.decide(dict(quantity=10, allocations=[dict(order_id=self.sup, quantity=4), dict(order_id=self.rest, quantity=6)]), "staff")
        t = self.t6 + 1
        self.offer_and_present(10, 18000, [])                                                # 漁協は 10本を出せない（返事は来ない）
        c = db.connect("amiya")
        try:
            d = consult.latest_decision(c, self.case)
            self.assertEqual(consult.against_decision(c, d, [dict(kind="order", ref_id=self.sup, quantity=4), dict(kind="order", ref_id=self.rest, quantity=2)]), [])
            self.assertTrue(consult.against_decision(c, d, [dict(kind="unallocated", ref_id=None, quantity=2)]))
            self.assertEqual(consult.against_decision(c, d, []), [])                          # 仕入れない案は決定と違わない
        finally:
            c.close()

    def test_short_fields_are_checked(self):
        t = self.t6
        c = db.connect("amiya")
        try:
            for bad in (dict(short_question="あ" * 41), dict(options=[self.opt("A", t, [(self.sup, 4), (self.rest, 2)], short="あ" * 17)])):
                payload = dict(item_code="buri_xl", summary="s", headline="h", short_question="短い問い", ask_human="a", cards=[], recommended="A",
                               options=[self.opt("A", t, [(self.sup, 4), (self.rest, 2)])])
                payload.update(bad)
                with self.assertRaises(workspace.BoardError):
                    workspace.build(c, "amiya", "buri_xl", tb.D, payload, 9, tb.NOW)
        finally:
            c.close()
