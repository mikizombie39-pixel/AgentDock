"""本編の取引の可能性の試験：漁協の資料・他の仲買からの相対の申し込み（Agent Network の掲示）。

  python -m unittest tests.test_network -v
"""
import os
import tempfile
import unittest

from agentdock import broadcast, criteria, db, disclosure, market
from agentdock.seed import build
from agentdock.seed import scenario as sc

EARLY = "2026-11-02T06:00:00+09:00"
LATE = "2026-11-02T10:30:00+09:00"


class NetworkTest(unittest.TestCase):
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

    def test_coop_material_has_aji_with_condition(self):
        body = broadcast.compose(self.con("iwase"), sc.DEMO_DATE)
        items = {i["item"]: i for i in body["items"]}
        self.assertEqual(set(items), {"buri_xl", "shiroebi", "aji"})
        aji = items["aji"]
        self.assertEqual((aji["quantity"], aji["today_expected_yen"]), (15, 1500))
        self.assertEqual(aji["today_expected_condition"], "15kg まとめての場合")
        self.assertTrue(aji["note"].startswith("今日はアジが多く、値は直近より安めです"))
        self.assertTrue(-15 <= aji["vs_recent_pct"] <= -8, aji)       # 「直近より安め」を数字が裏付ける
        self.assertFalse([k for k in aji if "floor" in k or "min_qty" in k or "quote" in k])   # 数量ごとの最低の値は資料に出さない

    def test_buyer_sees_recent_aji_market(self):
        r = criteria.market_stats(self.con("amiya"), "aji", sc.DEMO_DATE)
        self.assertTrue(1640 <= r["median"] <= 1720, r)

    def test_amiya_sees_both_requests(self):
        got = market.requests_visible_to("amiya", "aji", now=EARLY)
        self.assertEqual([(r["owner"], r["side"], r["quantity"], r["min_qty"], r["unit_price_yen"]) for r in got],
                         [("buyer_b", "buy", 5, 5, 1800), ("buyer_c", "sell", 8, 8, 1600)])

    def test_own_request_is_not_shown_and_expired_is_gone(self):
        self.assertEqual([r["owner"] for r in market.requests_visible_to("buyer_b", "aji", now=EARLY)], ["buyer_c"])
        self.assertEqual(market.requests_visible_to("amiya", "aji", now=LATE), [])

    def test_options_by_the_numbers(self):
        # 注文 10kg を満たす道（模擬の値）：A 漁協 10kg は上限超え・D C社 8kg＋漁協 2kg も上限超え
        ceiling = criteria.ceiling_yen(self.con("amiya"), "aji", "スーパー")
        self.assertEqual(ceiling, 1600)
        self.assertGreater(1700, ceiling)                                  # A
        self.assertGreater((8 * 1600 + 2 * 1700) / 10, ceiling)            # D 平均 1,620円
        self.assertEqual(15 * 1500, 22500)                                 # B・C の仕入れの額

    def test_request_text_is_checked_before_posting(self):
        b = self.con("buyer_b")
        for text in ("上限 1,900円まで出せます", "料亭の注文があります", "原価がかさみます"):
            with self.assertRaises(disclosure.DisclosureError, msg=text):
                market.post_request(b, "buyer_b", "buy", "aji", 5, 5, 1800, text, sc.DEMO_DATE, sc.DEAL_REQUEST_VALID_UNTIL)
        self.assertEqual(len(market.requests_visible_to("amiya", "aji", now=EARLY)), 2)   # 止めた文は掲げていない

    def test_c_has_the_stock_it_offers(self):
        row = self.con("buyer_c").execute(
            "SELECT s.quantity, s.status FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'aji'").fetchone()
        self.assertEqual(tuple(row), (8, "unallocated"))


if __name__ == "__main__":
    unittest.main()
