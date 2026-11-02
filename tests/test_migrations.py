"""番号つきの SQL を、行が入った古い DB に後から当てても中身が残ることの試験。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentdock import db


class MigrationTest(unittest.TestCase):
    def test_plan_rows_survive_006(self):
        with tempfile.TemporaryDirectory() as tmp:
            con = sqlite3.connect(Path(tmp) / "old.sqlite")
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys = ON")
            con.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT")
            for f in sorted((db.MIGRATIONS / "owner").glob("*.sql")):
                v = int(f.name.split("_", 1)[0])
                if v >= 6:
                    continue
                con.executescript(f.read_text(encoding="utf-8"))
                con.execute("INSERT INTO schema_version VALUES (?, 'x')", (v,))
            con.execute("INSERT INTO item (code, name, unit, unit_weight_g) VALUES ('buri_xl', 'ブリ', 'fish', 9000)")
            con.execute("INSERT INTO plan (item_id, kind, trade_date, max_unit_price_yen, max_quantity, status) VALUES (1, 'negotiation', '2026-11-02', 18500, 4, 'approved')")
            con.commit()
            self.assertIn(6, db.migrate(con, "owner"))
            row = con.execute("SELECT max_unit_price_yen, min_unit_price_yen, max_quantity, status FROM plan").fetchone()
            self.assertEqual(tuple(row), (18500, None, 4, "approved"))
            with self.assertRaises(sqlite3.IntegrityError):   # 承認済みは1件の索引も戻っている
                con.execute("INSERT INTO plan (item_id, kind, trade_date, max_unit_price_yen, max_quantity, status) VALUES (1, 'negotiation', '2026-11-02', 19000, 4, 'approved')")
            con.close()


if __name__ == "__main__":
    unittest.main()
