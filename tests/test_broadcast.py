"""漁協が全員に同じ資料を届ける部品と、公平の検査の試験。"""
import json
import os
import tempfile
import unittest

from agentdock import broadcast, db
from agentdock.seed import build
from agentdock.seed import scenario as sc


class BroadcastTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        con = db.connect("iwase")
        try:
            self.body = broadcast.compose(con, sc.DEMO_DATE)
        finally:
            con.close()

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def count(self, code, table):
        c = db.connect(code)
        try:
            return c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        finally:
            c.close()

    def test_compose_has_three_items_and_aji_is_cheaper(self):
        items = {i["item"]: i for i in self.body["items"]}
        self.assertEqual(set(items), {"buri_xl", "shiroebi", "aji_s"})
        aji = items["aji_s"]
        self.assertEqual((aji["quantity"], aji["today_expected_yen"]), (60, 2500))
        self.assertLess(aji["vs_recent_pct"], 0)
        self.assertEqual(items["buri_xl"]["tomorrow_forecast"], 30)

    def test_same_material_to_all(self):
        bid = broadcast.publish(self.body)
        self.assertTrue(broadcast.verify(bid))
        fps = set()
        for who in broadcast.BUYERS:
            c = db.connect(who)
            try:
                fps.add(c.execute("SELECT fingerprint FROM inbox_broadcast WHERE broadcast_id = ?", (bid,)).fetchone()[0])
            finally:
                c.close()
        self.assertEqual(len(fps), 1)

    def test_refuse_one_recipient(self):
        with self.assertRaises(broadcast.FairnessError):
            broadcast.publish(self.body, recipients=("amiya",))
        self.assertEqual(self.count("iwase", "broadcast"), 0)
        self.assertEqual(self.count("amiya", "inbox_broadcast"), 0)

    def test_refuse_tampered_copy(self):
        def sweeten(b):
            b["items"][0]["note"] = "あなたにだけ特別に"
        with self.assertRaises(broadcast.FairnessError):
            broadcast.publish(self.body, tamper={"buyer_b": sweeten})
        self.assertEqual(self.count("iwase", "broadcast"), 0)
        self.assertEqual(self.count("buyer_b", "inbox_broadcast"), 0)

    def test_verify_detects_changed_inbox(self):
        bid = broadcast.publish(self.body)
        c = db.connect("buyer_c")
        try:
            row = c.execute("SELECT body_json FROM inbox_broadcast WHERE broadcast_id = ?", (bid,)).fetchone()
            b = json.loads(row[0])
            b["title"] = "書き換え"
            with c:
                c.execute("UPDATE inbox_broadcast SET body_json = ? WHERE broadcast_id = ?", (json.dumps(b, ensure_ascii=False), bid))
        finally:
            c.close()
        self.assertFalse(broadcast.verify(bid))


if __name__ == "__main__":
    unittest.main()
