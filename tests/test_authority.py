"""権限（Rules）の試験：上限は表から読む・「1件」の範囲・分割による迂回の防止・社長の承認。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentdock import authority, db

DAY = "2026-11-03"


class AuthorityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        con = sqlite3.connect(Path(self.tmp.name) / "o.sqlite")
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        db.migrate(con, "owner")
        with con:
            con.execute("INSERT INTO item (code, name, unit, unit_weight_g) VALUES ('aji', 'アジ', 'kg', 1000)")
            con.executemany("INSERT INTO authority (role, person_name, max_trade_yen) VALUES (?, ?, ?)",
                            [("staff", "担当者", 500000), ("president", "社長", None)])
            con.execute("INSERT INTO task (kind, item_id, state, version, updated_at) VALUES ('purchase', 1, 'running', 1, ?)", (db.now_iso(),))
        self.con = con
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(con.close)

    def approved(self, counterparty, amount_qty, price, day=DAY):
        """確定済みの買い付けを1件入れる。"""
        with self.con:
            self.con.execute(
                "INSERT INTO proposal (side, item_id, counterparty, trade_date, quantity, unit_price_yen, status, decided_by, decided_at) "
                "VALUES ('buy', 1, ?, ?, ?, ?, 'registered', 'staff', ?)", (counterparty, day, amount_qty, price, db.now_iso()))

    def test_demo_case_is_within_staff_limit(self):
        r = authority.check(self.con, "staff", "iwase", 1, DAY, 15 * 1500)
        self.assertTrue(r["allowed"])
        self.assertEqual((r["limit_yen"], r["total_yen"]), (500000, 22500))

    def test_over_limit_needs_president(self):
        r = authority.check(self.con, "staff", "iwase", 1, DAY, 400 * 1500)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["needs"], "president")
        self.assertTrue(authority.check(self.con, "president", "iwase", 1, DAY, 400 * 1500)["allowed"])

    def test_limit_comes_from_the_table(self):
        with self.con:
            self.con.execute("UPDATE authority SET max_trade_yen = 20000 WHERE role = 'staff'")
        self.assertFalse(authority.check(self.con, "staff", "iwase", 1, DAY, 22500)["allowed"])

    def test_splitting_cannot_bypass_the_limit(self):
        self.approved("iwase", 200, 1500)          # 300,000円を確定済み
        r = authority.check(self.con, "staff", "iwase", 1, DAY, 200 * 1500)   # もう 300,000円
        self.assertEqual(r["already_yen"], 300000)
        self.assertFalse(r["allowed"])

    def test_other_counterparty_or_day_is_another_case(self):
        self.approved("iwase", 200, 1500)
        self.assertTrue(authority.check(self.con, "staff", "other_coop", 1, DAY, 300000)["allowed"])
        self.assertTrue(authority.check(self.con, "staff", "iwase", 1, "2026-11-04", 300000)["allowed"])

    def test_unknown_role_is_refused(self):
        with self.assertRaises(authority.AuthorityError):
            authority.check(self.con, "intern", "iwase", 1, DAY, 1000)


if __name__ == "__main__":
    unittest.main()
