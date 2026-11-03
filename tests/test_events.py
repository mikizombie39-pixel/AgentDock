"""業務の出来事（Hooks）の試験：注文と出来事を一緒に書く・引き受けは1回だけ・済みと戻し。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agentdock import db, events

DAY = "2026-11-02"


class EventsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        con = sqlite3.connect(Path(self.tmp.name) / "o.sqlite")
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        db.migrate(con, "owner")
        with con:
            con.execute("INSERT INTO item (code, name, unit, unit_weight_g) VALUES ('aji', 'アジ', 'kg', 1000)")
            con.execute("INSERT INTO customer (name, kind) VALUES ('スーパー', 'supermarket')")
        self.con = con
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(con.close)

    def count(self, table):
        return self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_order_and_event_are_written_together(self):
        oid, eid = events.order_received(self.con, "スーパー", "aji", 10, 2100, DAY)
        e = events.get(self.con, eid)
        self.assertEqual((e["kind"], e["status"]), ("order_new", "pending"))
        self.assertEqual(e["payload"], dict(order_id=oid, customer="スーパー", item="aji", quantity=10, order_date=DAY))
        self.assertEqual(self.con.execute("SELECT quantity FROM order_book WHERE id = ?", (oid,)).fetchone()[0], 10)

    def test_bad_order_leaves_nothing(self):
        with self.assertRaises(events.EventError):
            events.order_received(self.con, "料亭", "aji", 10, 2100, DAY)       # 知らない売り先
        with self.assertRaises(sqlite3.IntegrityError):
            events.order_received(self.con, "スーパー", "aji", 0, 2100, DAY)    # 数量 0 は表の検査が止める
        self.assertEqual((self.count("order_book"), self.count("biz_event")), (0, 0))

    def test_unknown_kind(self):
        with self.assertRaises(events.EventError):
            events.emit(self.con, "order_deleted", {})
        self.assertEqual(self.count("biz_event"), 0)

    def test_pending_is_oldest_first_and_filtered(self):
        a = events.emit(self.con, "quote_received", {"n": 1})
        b = events.emit(self.con, "order_new", {"n": 2})
        self.assertEqual([e["id"] for e in events.pending(self.con)], [a, b])
        self.assertEqual([e["id"] for e in events.pending(self.con, ["order_new"])], [b])

    def test_claim_once(self):
        eid = events.emit(self.con, "order_new", {})
        self.assertTrue(events.claim(self.con, eid, 1))
        self.assertFalse(events.claim(self.con, eid, 2))                   # 二つ目の起動は引き受けられない
        self.assertEqual(events.pending(self.con), [])                     # 引き受け中は待ちに出ない

    def test_only_the_claimer_finishes(self):
        eid = events.emit(self.con, "order_new", {})
        with self.assertRaises(events.EventError):
            events.done(self.con, eid, 1)                                   # 引き受けていない
        events.claim(self.con, eid, 1)
        with self.assertRaises(events.EventError):
            events.done(self.con, eid, 2)                                   # 別の起動
        events.done(self.con, eid, 1)
        e = events.get(self.con, eid)
        self.assertEqual((e["status"], e["run_id"]), ("handled", 1))
        self.assertIsNotNone(e["handled_at"])
        self.assertFalse(events.claim(self.con, eid, 3))                   # 済みは引き受けられない
        with self.assertRaises(events.EventError):
            events.release(self.con, eid, 1)                                # 済みは待ちに戻さない

    def test_release_returns_to_pending(self):
        eid = events.emit(self.con, "order_new", {})
        events.claim(self.con, eid, 1)
        events.release(self.con, eid, 1)
        self.assertEqual([e["id"] for e in events.pending(self.con)], [eid])
        self.assertTrue(events.claim(self.con, eid, 2))


class SeedOrderTest(unittest.TestCase):
    """本編の注文（scenario.AJI_ORDER）を本編の種の DB に入れると、出来事が1件だけ待ちに出る。"""

    def test_aji_order_wakes_one_event(self):
        import os
        from agentdock.seed import build
        from agentdock.seed import scenario as sc
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AGENTDOCK_DATA"] = tmp
            try:
                self.assertEqual(build.main([]), 0)
                o = sc.AJI_ORDER
                c = db.connect(o["owner"])
                try:
                    oid, eid = events.order_received(c, o["customer"], o["item"], o["quantity"], o["sell_price_yen"], sc.DEMO_DATE)
                    self.assertEqual([e["id"] for e in events.pending(c)], [eid])
                    self.assertEqual(events.get(c, eid)["payload"]["quantity"], 10)
                finally:
                    c.close()
            finally:
                os.environ.pop("AGENTDOCK_DATA", None)


if __name__ == "__main__":
    unittest.main()
