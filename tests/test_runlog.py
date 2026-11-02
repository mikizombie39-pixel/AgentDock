"""実行の記録の集計と費用の計算の試験。"""
import os
import tempfile
import unittest

from agentdock import consult, db, disclosure, runlog
from agentdock.seed import build
from agentdock.seed import scenario as sc

M = "claude-opus-5-5"


class RunlogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_cost(self):
        self.assertEqual(runlog.cost_micro_usd(M, 1_000_000, 0), 4_000_000)          # 入力 100万 = 4ドル
        self.assertEqual(runlog.cost_micro_usd(M, 0, 1_000_000), 20_000_000)         # 出力 100万 = 20ドル
        self.assertEqual(runlog.cost_micro_usd(M, 0, 0, cache_read=1_000_000), 200_000)
        self.assertEqual(runlog.cost_micro_usd(M, 0, 0, cache_write=1_000_000), 5_000_000)
        self.assertEqual(runlog.cost_micro_usd(M, 1, 0), 4)
        self.assertEqual(runlog.cost_micro_usd(M, 0, 0, cache_read=1), 1)           # 端数は切り上げ
        with self.assertRaises(runlog.UnknownModel):
            runlog.cost_micro_usd("claude-unknown", 1, 1)

    def test_record_and_summary(self):
        c = db.connect("amiya")
        try:
            cost = runlog.record_usage(c, M, dict(input_tokens=3000, output_tokens=800, cache_read_input_tokens=10000), "mock")
            self.assertEqual(cost, 3000 * 4 + 800 * 20 + 10000 * 0.2)
            t = consult.open_task(c, "negotiation", "buri_xl")
            with self.assertRaises(disclosure.DisclosureError):
                disclosure.send(c, t, "buri_xl", 17500, 4, "18,500円までなら", sc.DEMO_DATE)
        finally:
            c.close()
        s = runlog.summary()
        self.assertEqual((s["calls"], s["cost_micro_usd"]), (1, cost))
        self.assertEqual(s["blocked"]["disclosure_blocked"], 1)
        self.assertEqual(s["owners"]["amiya"]["cost_by_kind"], {"mock": cost})
        self.assertIsNotNone(s["last_event_at"])
        self.assertEqual(runlog.spent_micro_usd(), cost)

    def test_report_steps(self):
        usd = 1_000_000
        self.assertEqual(runlog.crossed(49 * usd, 51 * usd), [50])
        self.assertEqual(runlog.crossed(0, 49 * usd), [])
        self.assertEqual(runlog.crossed(10 * usd, 160 * usd), [50, 100, 150])


if __name__ == "__main__":
    unittest.main()
