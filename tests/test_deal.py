"""承認と正式な確定の試験：再検証・二重の操作・古い案・期限切れ・在庫切れ・拒否・途中の失敗と再開・権限。

条件は SAL の指示の例（漁協 10kg 1,720円・15kg 1,600円、B社 5kg 1,650円）。
"""
import os
import tempfile
import unittest

from agentdock import db, deal, events, market, network, scene, workspace
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
NOW = "2026-11-02T06:30:00+09:00"
LATER = "2026-11-02T07:30:00+09:00"
UNTIL = "2026-11-02T07:00:00+09:00"


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


class DealTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        st = scene.start()
        self.order = st["amiya_order"][0]
        q1 = network.send("amiya", "iwase", "inquiry", "aji", "アジを 15kg、1kg 1,500円でいかがでしょうか。", D, 15, 1500, side="buy")
        r = network.send("iwase", "amiya", "offer", "aji", "10kg なら 1,720円、15kg まとめてなら 1,600円です。", D,
                         terms=[(10, 1720), (15, 1600)], in_reply_to=q1, valid_until=UNTIL)
        c = db.connect("iwase")
        try:
            with c:
                self.hold = c.execute("INSERT INTO stock_hold (stock_id, buyer, quantity, unit_price_yen, negotiation_id, status, expires_at, created_at, updated_at) "
                                      "VALUES (1, 'amiya', 15, 1600, ?, 'held', ?, ?, ?)", (r, UNTIL, NOW, NOW)).lastrowid
        finally:
            c.close()
        network.attach_hold(r, self.hold)
        self.t10, self.t15 = [t["id"] for t in network.terms_for_message(r)]
        q2 = network.send("amiya", "buyer_b", "inquiry", "aji", "5kg を 1kg 1,650円でお譲りできます（仕入れの確定が条件）。", D, 5, 1650, side="sell")
        r2 = network.send("buyer_b", "amiya", "offer", "aji", "お願いします。", D, terms=[(5, 1650)], in_reply_to=q2, valid_until=UNTIL)
        self.ag = network.make_agreement(network.terms_for_message(r2)[0]["id"], "網谷水産の担当者の承認を条件とする", "conditional", UNTIL)
        payload = dict(item_code="aji", summary="条件がそろいました。", recommended="A",
                       cards=[dict(card_type="order", ref_id=self.order, note=None)],
                       options=[dict(code="A", label="15kg 仕入れ・5kg を B社へ", reason="売り先がある", risk="条件付き", buy_term_id=self.t15,
                                     allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10),
                                                  dict(kind="agreement", ref_id=self.ag, customer=None, quantity=5)]),
                                dict(code="E", label="見送る", reason="注文に応えられない", risk="注文を落とす", buy_term_id=None, allocations=[])])
        c = db.connect("amiya")
        try:
            self.case = workspace.build(c, "amiya", "aji", D, payload, 1, NOW)["case_id"]
            self.opt = {r[0]: (r[1], r[2]) for r in c.execute("SELECT code, id, fingerprint FROM board_option WHERE case_id = ?", (self.case,))}
        finally:
            c.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def approve(self, key="k1", now=NOW, fp=None, **kw):
        oid, f = self.opt["A"]
        return deal.decide("amiya", self.case, oid, "approve", fp or f, key, now=now, **kw)

    def no_trades(self):
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(0,)])
        self.assertEqual(q("amiya", "SELECT count(*) FROM proposal"), [(0,)])

    def test_approve_registers_every_book(self):
        r = self.approve()
        self.assertEqual(r["result"], "registered", r)
        self.assertEqual(q("market", "SELECT trade_id, seller, buyer, quantity, unit_price_yen, status FROM trade ORDER BY id"), [
            (f"case{self.case}-buy", "iwase", "amiya", 15, 1600, "registered"),
            (f"case{self.case}-sell-{self.ag}", "amiya", "buyer_b", 5, 1650, "registered")])
        self.assertEqual(q("market", "SELECT count(*) FROM seller_ledger"), [(2,)])
        self.assertEqual(q("market", "SELECT count(*) FROM buyer_ledger"), [(2,)])
        self.assertEqual(q("market", "SELECT id, status FROM offer_term WHERE seller = 'iwase' ORDER BY id"), [(self.t10, "released"), (self.t15, "confirmed")])
        self.assertEqual(q("market", "SELECT status FROM agreement"), [("confirmed",)])
        self.assertEqual(q("iwase", "SELECT status, quantity FROM stock_hold"), [("confirmed", 15)])
        self.assertEqual(q("iwase", "SELECT status FROM stock WHERE id = 1"), [("sold",)])           # 15kg 全部が売れた
        self.assertEqual(q("buyer_b", "SELECT quantity, status FROM stock"), [(5, "allocated")])
        self.assertEqual(q("buyer_b", "SELECT status FROM preapproval"), [("used",)])
        self.assertEqual(q("buyer_b", "SELECT o.status FROM order_book o JOIN item i ON i.id = o.item_id WHERE i.code = 'aji'"), [("filled",)])
        self.assertEqual(sorted(q("amiya", "SELECT quantity, status FROM stock")), [(5, "sold"), (10, "allocated")])
        self.assertEqual(q("amiya", "SELECT status FROM order_book WHERE id = ?", (self.order,)), [("filled",)])
        self.assertEqual(q("amiya", "SELECT status FROM deal_case"), [("registered",)])
        self.assertEqual(r["gross_profit_expected_yen"], 2250)
        for owner in ("amiya", "iwase", "buyer_b"):                                                   # 各組織の監査の記録
            self.assertTrue(q(owner, "SELECT count(*) FROM audit_event WHERE event IN ('case_registered', 'sold_by_preapproved_term', 'bought_by_preapproval')")[0][0])

    def test_same_key_runs_once(self):
        self.approve("same")
        r = self.approve("same")
        self.assertTrue(r["repeated"])
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(2,)])

    def test_second_approval_with_new_key_is_refused(self):
        self.approve("a")
        r = self.approve("b")
        self.assertEqual(r["result"], "stale")                                                        # もう条件は確定・合意も確定
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(2,)])

    def test_tampered_fingerprint_is_stale(self):
        r = self.approve(fp="0000000000000000")
        self.assertEqual(r["result"], "stale")
        self.no_trades()

    def test_changed_price_is_stale_and_wakes_the_partner(self):
        ex("market", "UPDATE offer_term SET unit_price_yen = 1650 WHERE id = ?", (self.t15,))      # 漁協の値が変わった
        r = self.approve()
        self.assertEqual(r["result"], "stale")
        self.no_trades()
        c = db.connect("amiya")
        try:
            self.assertEqual([e["kind"] for e in events.pending(c, ["human_choice"])], ["human_choice"])
        finally:
            c.close()

    def test_expired_terms_cannot_be_approved(self):
        r = self.approve(now=LATER)
        self.assertEqual(r["result"], "stale")
        self.assertTrue(any("期限切れ" in x for x in r["reasons"]))
        self.no_trades()
        self.assertEqual(q("iwase", "SELECT status FROM stock_hold"), [("expired",)])

    def test_stock_gone_is_stale(self):
        ex("iwase", "INSERT INTO stock_hold (stock_id, buyer, quantity, unit_price_yen, status, expires_at, created_at, updated_at) "
                      "VALUES (1, 'buyer_c', 5, 1700, 'held', ?, ?, ?)", (UNTIL, NOW, NOW))                # 同じ在庫を別の相手が押さえた
        r = self.approve()
        self.assertEqual(r["result"], "stale")
        self.assertTrue(any("在庫が足りない" in x for x in r["reasons"]))
        self.no_trades()

    def test_b_beyond_preapproval_is_not_confirmed(self):
        ex("market", "UPDATE agreement SET status = 'needs_staff' WHERE id = ?", (self.ag,))
        r = self.approve()
        self.assertEqual(r["result"], "stale")
        self.assertTrue(any("担当者の承認待ち" in x for x in r["reasons"]))
        self.no_trades()

    def test_reject_releases_everything(self):
        oid, _ = self.opt["A"]
        r = deal.decide("amiya", self.case, oid, "reject", None, "rej")
        self.assertEqual(r["result"], "rejected")
        self.no_trades()
        self.assertEqual(q("iwase", "SELECT status FROM stock_hold"), [("released",)])
        self.assertEqual(q("market", "SELECT status FROM offer_term WHERE seller = 'iwase'"), [("released",), ("released",)])
        self.assertEqual(q("market", "SELECT status FROM agreement"), [("cancelled",)])
        self.assertEqual(q("market", "SELECT from_owner, to_owner, kind FROM network_message ORDER BY id DESC LIMIT 1"), [("amiya", "buyer_b", "decline")])

    def test_hold_keeps_terms(self):
        oid, _ = self.opt["A"]
        self.assertEqual(deal.decide("amiya", self.case, oid, "hold", None, "h")["result"], "held")
        self.no_trades()
        self.assertEqual(q("iwase", "SELECT status FROM stock_hold"), [("held",)])

    def test_no_decision_no_trade(self):
        self.no_trades()
        self.assertEqual(q("amiya", "SELECT status FROM deal_case"), [("presented",)])

    def test_failure_midway_is_incomplete_and_resumes(self):
        r = self.approve(fail_at="sell_trades")
        self.assertEqual(r["result"], "incomplete")
        self.assertEqual(r["done"], ["buy_trade", "seller_stock", "terms"])
        self.assertEqual(q("amiya", "SELECT status FROM deal_case"), [("incomplete",)])
        rec = deal.reconcile([f"case{self.case}-buy", f"case{self.case}-sell-{self.ag}"])
        self.assertEqual([x["status"] for x in rec], ["registered", "missing"])
        r2 = deal.resume("amiya", self.case)
        self.assertEqual(r2["result"], "registered")
        self.assertEqual(q("market", "SELECT count(*) FROM trade"), [(2,)])
        self.assertEqual(q("iwase", "SELECT count(*) FROM stock WHERE status = 'sold'"), [(1,)])   # 在庫は二重に減らない
        self.assertEqual(deal.resume("amiya", self.case)["repeated"], True)

    def test_one_sided_ledger_is_completed(self):
        market.open_lot(f"T{self.t15}", "aji", 15, "iwase", "coop", "negotiation", D)
        fp = market.propose(f"case{self.case}-buy", f"T{self.t15}", "amiya", 1600, 15)
        market.approve(f"case{self.case}-buy", "iwase", fp)
        with self.assertRaises(market.MarketError):
            market.approve(f"case{self.case}-buy", "amiya", fp, fail_buyer_side=True)                # 売り手の帳簿だけ書けた
        self.assertEqual(deal.reconcile([f"case{self.case}-buy"])[0]["buyer_booked"], False)
        r = self.approve()
        self.assertEqual(r["result"], "registered")
        self.assertEqual(deal.reconcile([f"case{self.case}-buy"])[0]["buyer_booked"], True)

    def test_over_authority_needs_president(self):
        ex("amiya", "UPDATE authority SET max_trade_yen = 20000 WHERE role = 'staff'")
        r = self.approve()
        self.assertEqual(r["result"], "needs_president")
        self.no_trades()

    def test_choosing_pass_releases(self):
        oid, f = self.opt["E"]
        r = deal.decide("amiya", self.case, oid, "approve", f, "pass")
        self.assertEqual(r["result"], "rejected")
        self.no_trades()
        self.assertEqual(q("market", "SELECT status FROM agreement"), [("cancelled",)])


if __name__ == "__main__":
    unittest.main()
