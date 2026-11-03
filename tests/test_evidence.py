"""判断と根拠の記録、範囲の外の止め方の試験。"""
import os
import tempfile
import unittest

from agentdock import consult, db, evidence, plans
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
EV = [("sales_history", None, "x")]   # 種類の検査で止まる形（試験用）
GOOD = [("order", 1, "スーパーの注文 3箱"), ("price_history", None, "直近30日の中央値 3,400円")]


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main(["--with-aji-s"]), 0)
        self.con = db.connect("amiya")
        plans.approve(self.con, "aji_s", "purchase", D, 3000, 3)
        self.task = consult.open_task(self.con, "purchase", "aji_s")

    def tearDown(self):
        self.con.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def count(self, table):
        return self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def test_within_plan_is_recorded_with_evidence(self):
        did = evidence.record(self.con, self.task, "buy", 2500, 3, "注文の数を見込みの値で買う", GOOD, D)
        s = evidence.sheet(self.con, did)
        self.assertTrue(s["within_plan"])
        self.assertEqual(s["plan"], dict(max_unit_price_yen=3000, max_quantity=3))
        self.assertEqual([e["kind"] for e in s["evidence"]], ["order", "price_history"])
        self.assertFalse(s["stale"])

    def test_outside_plan_cannot_be_acted_on(self):
        with self.assertRaises(evidence.OutsidePlan) as e:
            evidence.record(self.con, self.task, "buy", 2500, 6, "安い日は2倍売れる", GOOD, D)
        self.assertEqual(e.exception.reasons, ["quantity_over"])
        self.assertEqual(self.count("decision"), 0)
        self.assertEqual(self.con.execute("SELECT count(*) FROM audit_event WHERE event = 'outside_plan_blocked'").fetchone()[0], 1)

    def test_outside_plan_can_be_escalated(self):
        did = evidence.record(self.con, self.task, "escalate", 2500, 6, "注文より3箱多く買う案。安い日は注文の2倍売れている",
                              GOOD + [("broadcast", 1, "漁協の資料：アジ 1箱 2,500円")], D)
        s = evidence.sheet(self.con, did)
        self.assertEqual((s["action"], s["within_plan"]), ("escalate", False))

    def test_no_plan_means_outside(self):
        t = consult.open_task(self.con, "negotiation", "buri_xl")
        with self.assertRaises(evidence.OutsidePlan) as e:
            evidence.record(self.con, t, "offer", 17500, 4, "相場の中央値の少し下", GOOD, D)
        self.assertEqual(e.exception.reasons, ["no_approved_plan"])

    def test_no_evidence_or_rationale_is_refused(self):
        with self.assertRaises(evidence.EvidenceError):
            evidence.record(self.con, self.task, "buy", 2500, 3, "理由", [], D)
        with self.assertRaises(evidence.EvidenceError):
            evidence.record(self.con, self.task, "buy", 2500, 3, "  ", GOOD, D)
        with self.assertRaises(Exception):
            evidence.record(self.con, self.task, "buy", 2500, 3, "理由", EV, D)   # 根拠の種類は CHECK の中だけ
        self.assertEqual(self.count("decision"), 0)                                # 途中まで書いた行も残らない

    def test_decision_becomes_stale_after_revision(self):
        did = evidence.record(self.con, self.task, "buy", 2500, 3, "注文の数", GOOD, D)
        consult.revise(self.con, self.task, "スーパーから追加の注文")
        self.assertTrue(evidence.sheet(self.con, did)["stale"])


if __name__ == "__main__":
    unittest.main()
