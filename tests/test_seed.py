"""種のデータと判断の基準の試験。

  python -m unittest discover -s tests -v
"""
import datetime
import os
import sqlite3
import tempfile
import unittest

from agentdock import criteria, db
from agentdock.seed import build, prices
from agentdock.seed import scenario as sc


class SeedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = cls.tmp.name
        assert build.main([]) == 0

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("AGENTDOCK_DATA", None)
        cls.tmp.cleanup()

    def con(self, code):
        c = db.connect(code)
        self.addCleanup(c.close)
        return c

    def test_authority_comes_from_the_seed(self):
        rows = {r["role"]: r["max_trade_yen"] for r in self.con("amiya").execute("SELECT role, max_trade_yen FROM authority")}
        self.assertEqual(rows, {"staff": 500000, "president": None})
        self.assertEqual(self.con("iwase").execute("SELECT COUNT(*) FROM authority").fetchone()[0], 0)

    def test_files(self):
        for code in (*db.OWNER_CODES, db.MARKET):
            self.assertTrue(db.db_path(code).exists(), code)

    def test_owner_and_items(self):
        for code in db.OWNER_CODES:
            c = self.con(code)
            self.assertEqual(c.execute("SELECT count(*) FROM owner").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM item").fetchone()[0], 3)

    def test_ceilings(self):
        c = self.con("amiya")
        self.assertEqual(criteria.ceiling_yen(c, "buri_xl", "スーパー"), 18500)
        self.assertEqual(criteria.ceiling_yen(c, "buri_xl", "料亭"), 20500)
        self.assertEqual(criteria.ceiling_yen(c, "shiroebi", "スーパー"), 4800)
        self.assertEqual(criteria.ceiling_yen(c, "aji_s", "スーパー"), 3000)

    def test_floor_main(self):
        c = self.con("iwase")
        self.assertEqual(criteria.floor_yen(c, "buri_xl", sc.DEMO_DATE), (17000, True))

    def test_c_spare(self):
        c = self.con("buyer_c")
        ordered = c.execute("SELECT o.quantity FROM order_book o JOIN item i ON i.id = o.item_id WHERE i.code = 'shiroebi'").fetchone()[0]
        extra = c.execute("SELECT r.extra_qty_on_cheap_day FROM buyer_rule r JOIN item i ON i.id = r.item_id WHERE i.code = 'shiroebi'").fetchone()[0]
        self.assertEqual((ordered, extra), (9, 2))
        self.assertEqual(criteria.spare_quantity(won=11, ordered=ordered), 2)

    def test_tender_allocation_leaves_amiya_short(self):
        # 高い順に割り当て：B社 4,400円・6箱 → C社 4,350円・11箱 → 網谷水産 4,300円・残り
        bids = [("buyer_b", 4400, 6), ("buyer_c", 4350, 11), ("amiya", 4300, 5)]
        left, won = 20, {}
        for who, _, q in sorted(bids, key=lambda b: -b[1]):
            won[who] = min(q, left)
            left -= won[who]
        self.assertEqual(won, {"buyer_b": 6, "buyer_c": 11, "amiya": 3})

    def test_no_sunday_and_shiroebi_season(self):
        c = self.con("amiya")
        days = [r[0] for r in c.execute("SELECT DISTINCT trade_date FROM price_history")]
        self.assertTrue(days)
        self.assertFalse([d for d in days if datetime.date.fromisoformat(d).weekday() == 6])
        winter = c.execute(
            "SELECT count(*) FROM price_history p JOIN item i ON i.id = p.item_id "
            "WHERE i.code = 'shiroebi' AND CAST(substr(p.trade_date, 6, 2) AS INTEGER) IN (12, 1, 2, 3)").fetchone()[0]
        self.assertEqual(winter, 0)

    def test_demo_window_levels(self):
        c = self.con("amiya")
        buri = criteria.market_stats(c, "buri_xl", sc.DEMO_DATE)
        ebi = criteria.market_stats(c, "shiroebi", sc.DEMO_DATE)
        aji = criteria.market_stats(c, "aji_s", sc.DEMO_DATE)
        self.assertTrue(17000 <= buri["median"] <= 18500, buri)
        self.assertTrue(4300 <= ebi["median"] <= 4600, ebi)
        self.assertTrue(3200 <= aji["median"] <= 3800, aji)          # kg 320〜380円
        self.assertLess(2500, aji["median"])                          # 今日の見込み kg 250円は直近より安い
        ly = criteria.last_year_stats(c, "buri_xl", sc.DEMO_DATE)
        self.assertIsNotNone(ly)

    def test_public_results_same_for_all_buyers(self):
        q = "SELECT i.code, trade_date, unit_price_yen, quantity FROM price_history p JOIN item i ON i.id = p.item_id WHERE source = 'public_result' ORDER BY 1, 2, 3, 4"
        sets = [list(map(tuple, self.con(code).execute(q))) for code in ("amiya", "buyer_b", "buyer_c")]
        self.assertEqual(sets[0], sets[1])
        self.assertEqual(sets[0], sets[2])

    def test_deterministic(self):
        a = prices.public_results(sc.HISTORY_FROM, sc.HISTORY_TO)
        b = prices.public_results(sc.HISTORY_FROM, sc.HISTORY_TO)
        self.assertEqual(a, b)

    def test_constraints(self):
        c = self.con("amiya")
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO customer (name, kind) VALUES ('x', 'friend')")
        iid = c.execute("SELECT id FROM item WHERE code = 'shiroebi'").fetchone()[0]
        c.execute("INSERT INTO plan (item_id, kind, trade_date, max_unit_price_yen, max_quantity, status) VALUES (?, 'tender', ?, 4800, 8, 'approved')", (iid, sc.DEMO_DATE))
        with self.assertRaises(sqlite3.IntegrityError):
            c.execute("INSERT INTO plan (item_id, kind, trade_date, max_unit_price_yen, max_quantity, status) VALUES (?, 'tender', ?, 4900, 8, 'approved')", (iid, sc.DEMO_DATE))
        c.rollback()


class SceneCTest(unittest.TestCase):
    def test_floor_scene_c(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AGENTDOCK_DATA"] = tmp
            try:
                self.assertEqual(build.main(["--scene-c"]), 0)
                c = db.connect("iwase")
                try:
                    self.assertEqual(criteria.floor_yen(c, "buri_xl", sc.DEMO_DATE), (19000, True))
                finally:
                    c.close()
            finally:
                os.environ.pop("AGENTDOCK_DATA", None)


class SafetyTest(unittest.TestCase):
    def test_refuse_onedrive(self):
        os.environ["AGENTDOCK_DATA"] = r"C:\Users\mikiz\OneDrive\tmp-agentdock-test"
        try:
            self.assertEqual(build.main([]), 2)
            self.assertFalse(os.path.exists(r"C:\Users\mikiz\OneDrive\tmp-agentdock-test"))
        finally:
            os.environ.pop("AGENTDOCK_DATA", None)


if __name__ == "__main__":
    unittest.main()
