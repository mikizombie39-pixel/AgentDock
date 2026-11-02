"""範囲の判定、相談の束、回答の安全の条件の試験。"""
import os
import sqlite3
import tempfile
import unittest

from agentdock import consult, db, plans
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
BURI_OPTS = [("prefer_restaurant", "料亭の分を優先して上限を上げる", True),
             ("prefer_supermarket", "スーパーの分だけ買う", True),
             ("pass", "今日は見送る", True)]
AJI_OPTS = [("buy_extra", "注文より多く買う", True),
            ("order_only", "注文の数だけ買う", True),
            ("split", "半分ずつ", False)]


class ConsultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.con = db.connect("amiya")

    def tearDown(self):
        self.con.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def two(self):
        buri = consult.open_task(self.con, "negotiation", "buri_xl")
        aji = consult.open_task(self.con, "purchase", "aji_s")
        gid = consult.bundle(self.con, "ブリとアジの相談", [
            dict(task_id=buri, question="料亭の分で上限を上げますか", options=BURI_OPTS),
            dict(task_id=aji, question="注文より多く買いますか", options=AJI_OPTS)])
        card = consult.card(self.con, gid)
        return buri, aji, gid, {i["task_id"]: i["consultation_id"] for i in card["items"]}

    # 範囲の判定 ------------------------------------------------------------
    def test_plan_check(self):
        self.assertEqual(plans.check(self.con, "aji_s", "purchase", D, 2500, 3)["reasons"], ["no_approved_plan"])
        plans.approve(self.con, "aji_s", "purchase", D, 3000, 3)
        self.assertTrue(plans.check(self.con, "aji_s", "purchase", D, 2500, 3)["within"])
        self.assertEqual(plans.check(self.con, "aji_s", "purchase", D, 2500, 6)["reasons"], ["quantity_over"])
        self.assertEqual(plans.check(self.con, "aji_s", "purchase", D, 3100, 6)["reasons"], ["price_over", "quantity_over"])

    def test_plan_reapproval_supersedes(self):
        plans.approve(self.con, "buri_xl", "negotiation", D, 18500, 4)
        plans.approve(self.con, "buri_xl", "negotiation", D, 20500, 6)
        self.assertTrue(plans.check(self.con, "buri_xl", "negotiation", D, 20000, 6)["within"])
        n = self.con.execute("SELECT count(*) FROM plan WHERE status = 'approved'").fetchone()[0]
        self.assertEqual(n, 1)

    # 相談の束 --------------------------------------------------------------
    def test_bundle_is_one_card_and_waits(self):
        buri, aji, gid, _ = self.two()
        card = consult.card(self.con, gid)
        self.assertEqual((len(card["items"]), card["status"]), (2, "open"))
        self.assertFalse(consult.can_proceed(self.con, buri))   # 無回答のうちは進めない
        self.assertFalse(consult.can_proceed(self.con, aji))

    def test_answer_both_closes_card(self):
        buri, aji, gid, cid = self.two()
        consult.answer(self.con, cid[buri], "prefer_restaurant", "k-buri-1", "料亭は付き合いが長い")
        self.assertEqual(consult.card(self.con, gid)["status"], "open")
        self.assertTrue(consult.can_proceed(self.con, buri))
        consult.answer(self.con, cid[aji], "buy_extra", "k-aji-1")
        self.assertEqual(consult.card(self.con, gid)["status"], "answered")

    def test_double_answer_acts_once(self):
        buri, _, _, cid = self.two()
        r1 = consult.answer(self.con, cid[buri], "pass", "k-1")
        r2 = consult.answer(self.con, cid[buri], "pass", "k-1")        # 押し直し・通信の再送
        self.assertEqual((r1["duplicate"], r2["duplicate"]), (False, True))
        n = self.con.execute("SELECT count(*) FROM audit_event WHERE event = 'consultation_answered'").fetchone()[0]
        self.assertEqual(n, 1)
        with self.assertRaises(consult.ConsultError):
            consult.answer(self.con, cid[buri], "prefer_restaurant", "k-1")   # 同じ鍵で別の答え
        with self.assertRaises(consult.ConsultError):
            consult.answer(self.con, cid[buri], "prefer_restaurant", "k-2")   # 答えた相談にもう一度

    def test_stale_answer_is_rejected_after_new_order(self):
        buri, aji, gid, cid = self.two()
        self.assertEqual(consult.revise(self.con, buri, "料亭から新しい注文 2本"), 2)
        with self.assertRaises(consult.StaleError):
            consult.answer(self.con, cid[buri], "prefer_supermarket", "k-old")
        self.assertEqual(consult.task(self.con, buri)["state"], "revalidating")
        self.assertFalse(consult.can_proceed(self.con, buri))
        consult.answer(self.con, cid[aji], "order_only", "k-aji")
        self.assertEqual(consult.card(self.con, gid)["status"], "stale")   # 1枚の中に古い相談が残る
        g2 = consult.bundle(self.con, "ブリの相談（新しい注文の後）",
                            [dict(task_id=buri, question="料亭の分を含めて上限を上げますか", options=BURI_OPTS)])
        c2 = consult.card(self.con, g2)["items"][0]
        self.assertEqual(c2["version"], 2)
        consult.answer(self.con, c2["consultation_id"], "prefer_restaurant", "k-new")
        self.assertTrue(consult.can_proceed(self.con, buri))

    def test_invalid_option_cannot_be_chosen(self):
        _, aji, _, cid = self.two()
        with self.assertRaises(consult.ConsultError):
            consult.answer(self.con, cid[aji], "split", "k-x")
        with self.assertRaises(consult.ConsultError):
            consult.answer(self.con, cid[aji], "hold", "k-y")              # 出していない選択肢
        self.assertFalse(consult.can_proceed(self.con, aji))

    def test_bundle_all_or_nothing(self):
        buri = consult.open_task(self.con, "negotiation", "buri_xl")
        aji = consult.open_task(self.con, "purchase", "aji_s")
        with self.assertRaises(consult.ConsultError):
            consult.bundle(self.con, "x", [dict(task_id=buri, question="q", options=BURI_OPTS),
                                           dict(task_id=aji, question="q", options=[("split", "x", False)])])
        self.assertEqual(self.con.execute("SELECT count(*) FROM consultation").fetchone()[0], 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM consultation_group").fetchone()[0], 0)
        self.assertEqual(consult.task(self.con, buri)["state"], "running")

    def test_same_version_cannot_be_asked_twice(self):
        buri = consult.open_task(self.con, "negotiation", "buri_xl")
        consult.bundle(self.con, "a", [dict(task_id=buri, question="q", options=BURI_OPTS)])
        with self.assertRaises(sqlite3.IntegrityError):
            consult.bundle(self.con, "b", [dict(task_id=buri, question="q", options=BURI_OPTS)])
        self.assertEqual(self.con.execute("SELECT count(*) FROM consultation_group").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
