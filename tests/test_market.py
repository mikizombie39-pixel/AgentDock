"""参照用の市場：相対、双方の承認と登録、片側の失敗、照合の試験。"""
import os
import tempfile
import unittest

from agentdock import db, market
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE


class MarketTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        # C社が余った2箱を仲買どうしの相対に出す
        market.open_lot("R-shiroebi-c-1", "shiroebi", 2, "buyer_c", "buyer", "negotiation", D)
        market.open_lot("N-buri_xl-1", "buri_xl", 6, "iwase", "coop", "negotiation", D)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def rows(self, code, sql):
        c = db.connect(code)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()

    def test_both_approve_then_registered_and_matched(self):
        fp = market.propose("T1", "R-shiroebi-c-1", "amiya", 4500, 2)
        self.assertEqual(market.approve("T1", "buyer_c", fp), "awaiting_approval")
        self.assertEqual(market.approve("T1", "buyer_c", fp), "awaiting_approval")   # 二度押しても1件
        self.assertEqual(len(market.trade("T1")["approvals"]), 1)
        self.assertEqual(market.approve("T1", "amiya", fp), "registered")
        self.assertEqual(market.approve("T1", "amiya", fp), "registered")              # 登録は二度しない
        self.assertEqual(self.rows(db.MARKET, "SELECT count(*) FROM buyer_ledger")[0][0], 1)
        self.assertEqual(market.reconcile_owner("amiya"), [dict(trade_id="T1", result="match")])
        self.assertEqual(market.reconcile_owner("buyer_c"), [dict(trade_id="T1", result="match")])
        self.assertEqual([tuple(r) for r in self.rows("amiya", "SELECT side, unit_price_yen, quantity FROM ledger_entry")], [("buy", 4500, 2)])
        self.assertEqual([tuple(r) for r in self.rows("buyer_c", "SELECT side, quantity FROM ledger_entry")], [("sell", 2)])

    def test_old_approval_does_not_register_new_terms(self):
        fp1 = market.propose("T2", "N-buri_xl-1", "amiya", 17500, 4)
        market.approve("T2", "iwase", fp1)
        fp2 = market.revise_terms("T2", 18000, 4)
        with self.assertRaises(market.StaleApproval):
            market.approve("T2", "amiya", fp1)
        self.assertEqual(market.trade("T2")["approvals"], [])          # 前の条件の承認は消えた
        self.assertEqual(market.approve("T2", "amiya", fp2), "awaiting_approval")
        self.assertEqual(market.approve("T2", "iwase", fp2), "registered")
        self.assertEqual(market.trade("T2")["unit_price_yen"], 18000)

    def test_one_side_failure_is_incomplete_then_completed(self):
        fp = market.propose("T3", "R-shiroebi-c-1", "amiya", 4500, 2)
        market.approve("T3", "buyer_c", fp)
        with self.assertRaises(market.MarketError):
            market.approve("T3", "amiya", fp, fail_buyer_side=True)
        t = market.trade("T3")
        self.assertEqual((t["status"], t["seller_booked"], t["buyer_booked"]), ("incomplete", True, False))
        self.assertEqual(market.reconcile_owner("amiya"), [dict(trade_id="T3", result="pending")])
        self.assertEqual(self.rows("amiya", "SELECT count(*) FROM ledger_entry")[0][0], 0)   # 未完了は帳簿に入れない
        self.assertEqual(market.complete("T3"), "registered")
        self.assertEqual(market.complete("T3"), "registered")
        self.assertEqual(self.rows(db.MARKET, "SELECT count(*) FROM seller_ledger")[0][0], 1)
        self.assertEqual(market.reconcile_owner("amiya"), [dict(trade_id="T3", result="match")])

    def test_mismatch_is_found(self):
        fp = market.propose("T4", "R-shiroebi-c-1", "amiya", 4500, 2)
        market.approve("T4", "buyer_c", fp)
        market.approve("T4", "amiya", fp)
        m = db.connect(db.MARKET)
        try:
            with m:
                m.execute("UPDATE buyer_ledger SET unit_price_yen = 4600")
        finally:
            m.close()
        self.assertEqual(market.reconcile_owner("amiya"), [dict(trade_id="T4", result="mismatch")])
        self.assertEqual(self.rows("amiya", "SELECT count(*) FROM ledger_entry")[0][0], 0)

    def test_no_deal_is_not_incomplete(self):
        nid = market.open_negotiation("N-buri_xl-1", "amiya")
        v = 1
        for _ in range(market.MAX_ROUNDS):
            v = market.exchange(nid, v)
        with self.assertRaises(market.MarketError):
            market.exchange(nid, v)                                     # 7往復目は no_deal
        self.assertEqual(market.negotiation(nid)["status"], "no_deal")
        self.assertEqual(self.rows(db.MARKET, "SELECT count(*) FROM trade")[0][0], 0)

    def test_no_deal_by_decision_and_stale_offer(self):
        nid = market.open_negotiation("N-buri_xl-1", "buyer_b")
        v2 = market.exchange(nid, 1)
        with self.assertRaises(market.StaleApproval):
            market.exchange(nid, 1)                                     # 古い版への返事
        market.no_deal(nid)
        with self.assertRaises(market.MarketError):
            market.exchange(nid, v2)
        with self.assertRaises(market.MarketError):
            market.no_deal(nid)

    def test_no_oversell_and_parties_only(self):
        with self.assertRaises(market.MarketError):
            market.propose("T5", "R-shiroebi-c-1", "amiya", 4500, 3)
        market.propose("T6", "N-buri_xl-1", "amiya", 18000, 4)
        with self.assertRaises(market.MarketError):
            market.propose("T7", "N-buri_xl-1", "buyer_b", 18000, 3)   # 4 + 3 > 6
        with self.assertRaises(market.MarketError):
            market.revise_terms("T6", 18000, 7)
        with self.assertRaises(market.MarketError):
            market.propose("T8", "R-shiroebi-c-1", "buyer_c", 4500, 1)  # 自分の出品を買わない
        fp = market.trade("T6")["terms_fingerprint"]
        with self.assertRaises(market.MarketError):
            market.approve("T6", "buyer_b", fp)                         # 当事者でない


if __name__ == "__main__":
    unittest.main()
