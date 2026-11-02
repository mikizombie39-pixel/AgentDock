"""せりと入札の結果を全員へ送る部品と、各社の突き合わせの試験。"""
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from agentdock import broadcast, db, results
from agentdock.seed import build
from agentdock.seed import scenario as sc


class ResultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.rows = results.today_results()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def count(self, code, table):
        c = db.connect(code)
        try:
            return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            c.close()

    def line(self, report, item):
        return next(x for x in report["lines"] if x["item"] == item)

    def test_tender_allocation(self):
        won = {r["buyer"]: r["quantity"] for r in self.rows if r["item"] == "shiroebi"}
        self.assertEqual(won, {"buyer_b": 6, "buyer_c": 11, "amiya": 3})

    def test_same_public_part_and_own_mark(self):
        results.publish(self.rows)
        self.assertTrue(results.verify())
        for who in results.BUYERS:
            c = db.connect(who)
            try:
                own = [(r[0], r[1]) for r in c.execute(
                    "SELECT i.code, a.quantity FROM auction_result_in a JOIN item i ON i.id = a.item_id WHERE a.is_own = 1")]
                cols = [r[1] for r in c.execute("PRAGMA table_info(auction_result_in)")]
            finally:
                c.close()
            expect = [(r["item"], r["quantity"]) for r in self.rows if r["buyer"] == who]
            self.assertEqual(sorted(own), sorted(expect), who)
            self.assertNotIn("buyer", cols)   # 他社の名前を持つ列がない

    def test_send_twice_adds_no_rows(self):
        results.publish(self.rows)
        before = self.count("amiya", "auction_result_in")
        results.publish(self.rows)
        self.assertEqual(self.count("amiya", "auction_result_in"), before)
        self.assertEqual(self.count(db.MARKET, "auction_result"), len(self.rows))

    def test_tampered_copy_stops_and_records_nothing(self):
        def bump(pub):
            pub[0]["unit_price_yen"] += 100
        with self.assertRaises(results.ResultError):
            results.publish(self.rows, tamper={"buyer_c": bump})
        self.assertEqual(self.count(db.MARKET, "auction_result"), 0)
        for who in (results.SELLER, *results.BUYERS):
            self.assertEqual(self.count(who, "auction_result_in"), 0, who)

    def test_sold_more_than_landed_stops(self):
        rows = [dict(r) for r in self.rows]
        rows[-1]["quantity"] += 10        # ブリを 12本より多く売った形
        with self.assertRaises(results.ResultError):
            results.publish(rows)
        self.assertEqual(self.count(db.MARKET, "auction_result"), 0)

    def test_tampered_after_delivery_is_found(self):
        results.publish(self.rows)
        c = db.connect("buyer_b")
        try:
            with c:
                c.execute("UPDATE auction_result_in SET unit_price_yen = unit_price_yen + 1 WHERE id = 1")
        finally:
            c.close()
        self.assertFalse(results.verify())

    def test_amiya_short_shiroebi_and_buri(self):
        results.publish(self.rows)
        r = results.reconcile("amiya")
        ebi = self.line(r, "shiroebi")
        self.assertEqual((ebi["ordered"], ebi["won"], ebi["short"], ebi["status"]), (5, 3, 2, "short"))
        self.assertEqual(ebi["own_avg_yen"], 4300)
        buri = self.line(r, "buri_xl")
        self.assertEqual((buri["ordered"], buri["won"], buri["short"]), (4, 0, 4))
        aji = self.line(r, "aji_s")
        self.assertEqual(aji["status"], "awaiting_result")   # アジはせりの結果に無い＝不足とは書かない
        self.assertNotIn("short", aji)

    def test_c_has_two_spare(self):
        results.publish(self.rows)
        ebi = self.line(results.reconcile("buyer_c"), "shiroebi")
        self.assertEqual((ebi["won"], ebi["spare"], ebi["status"]), (11, 2, "spare"))

    def test_iwase_has_six_buri_left_with_held_floor(self):
        results.publish(self.rows)
        r = results.reconcile("iwase")
        buri = self.line(r, "buri_xl")
        self.assertEqual((buri["sold"], buri["remaining"], buri["floor_yen"], buri["floor_held_up"]), (6, 6, 17000, True))
        self.assertEqual(self.line(r, "shiroebi")["status"], "sold_out")
        self.assertEqual(self.line(r, "aji_s")["status"], "not_sold_yet")

    def test_before_results_nothing_is_short(self):
        r = results.reconcile("amiya")
        self.assertTrue(all(x["status"] == "awaiting_result" for x in r["lines"]))

    def test_new_restaurant_order_raises_buri_short(self):
        results.publish(self.rows)
        c = db.connect("amiya")
        try:
            iid = c.execute("SELECT id FROM item WHERE code = 'buri_xl'").fetchone()[0]
            cid = c.execute("SELECT id FROM customer WHERE name = '料亭'").fetchone()[0]
            with c:
                c.execute("INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, 2, 24000, ?, ?)",
                          (cid, iid, sc.DEMO_DATE, f"{sc.DEMO_DATE}T07:10:00+09:00"))
        finally:
            c.close()
        buri = self.line(results.reconcile("amiya"), "buri_xl")
        self.assertEqual((buri["ordered"], buri["short"]), (6, 6))
        self.assertEqual({o["customer"] for o in buri["orders"]}, {"スーパー", "料亭"})

    def test_aji_cheap_offer_in_same_report(self):
        con = db.connect("iwase")
        try:
            body = broadcast.compose(con, sc.DEMO_DATE)
        finally:
            con.close()
        broadcast.publish(body)
        results.publish(self.rows)
        offers = results.reconcile("amiya")["offers"]
        self.assertEqual([o["item"] for o in offers], ["aji_s"])
        aji = offers[0]
        self.assertLessEqual(aji["ratio_to_recent"], 0.85)
        sup = next(x for x in aji["customers"] if x["customer"] == "スーパー")
        self.assertEqual((sup["ordered"], sup["cheap_ratio"]), (3, 2.0))
        # B社はアジの売り先がない＝見込みの対象の売り先が空
        b = results.reconcile("buyer_b")["offers"]
        self.assertTrue(all(not o["customers"] for o in b))

    def test_partial_failure_then_resend(self):
        real = db.connect

        def flaky(code):
            if code == "buyer_b":
                raise sqlite3.OperationalError("disk I/O error (test)")
            return real(code)
        with mock.patch.object(db, "connect", side_effect=flaky):
            with self.assertRaises(sqlite3.OperationalError):
                results.publish(self.rows)
        self.assertFalse(results.verify())               # 片側だけ届いた状態を検査が見つける
        self.assertEqual(self.count("buyer_b", "auction_result_in"), 0)
        self.assertEqual(results.resend(), ["buyer_b", "buyer_c"])   # 失敗した社と、その後ろの社
        self.assertTrue(results.verify())
        self.assertEqual(results.resend(), [])            # 二度目は何も足さない
        self.assertEqual(self.count("amiya", "auction_result_in"), len(self.rows))

    def test_resend_does_not_hide_tampering(self):
        results.publish(self.rows)
        c = db.connect("amiya")
        try:
            with c:
                c.execute("UPDATE auction_result_in SET quantity = quantity + 1 WHERE id = 1")
        finally:
            c.close()
        self.assertEqual(results.resend(), [])
        self.assertFalse(results.verify())


if __name__ == "__main__":
    unittest.main()
