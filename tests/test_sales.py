"""売れ方の記録の試験：安い日のアジは、網谷水産のスーパーで注文の約2倍売れる。"""
import os
import tempfile
import unittest

from agentdock import criteria, db
from agentdock.seed import build
from agentdock.seed import scenario as sc


class SalesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = cls.tmp.name
        assert build.main([]) == 0

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("AGENTDOCK_DATA", None)
        cls.tmp.cleanup()

    def ratio(self, code, item, customer):
        c = db.connect(code)
        try:
            return criteria.sell_ratio(c, item, customer, sc.DEMO_DATE)
        finally:
            c.close()

    def test_amiya_aji_sells_double_on_cheap_days(self):
        r = self.ratio("amiya", "aji_s", "スーパー")
        self.assertGreaterEqual(r["cheap_days"], 10, r)
        self.assertTrue(1.7 <= r["cheap_ratio"] <= 2.3, r)
        self.assertTrue(0.9 <= r["normal_ratio"] <= 1.1, r)

    def test_c_aji_sells_less_extra(self):
        r = self.ratio("buyer_c", "aji_s", "スーパー")
        self.assertTrue(1.2 <= r["cheap_ratio"] <= 1.6, r)

    def test_amiya_has_restaurant_buri_history(self):
        c = db.connect("amiya")
        try:
            n = c.execute("SELECT count(*) FROM sales_history s JOIN customer cu ON cu.id = s.customer_id "
                          "JOIN item i ON i.id = s.item_id WHERE cu.name = '料亭' AND i.code = 'buri_xl'").fetchone()[0]
        finally:
            c.close()
        self.assertGreater(n, 50)

    def test_today_aji_is_cheap_day(self):
        c = db.connect("amiya")
        try:
            med = criteria.market_stats(c, "aji_s", sc.DEMO_DATE)["median"]
        finally:
            c.close()
        self.assertLessEqual(sc.TODAY_EXPECTED_YEN["aji_s"] / med, criteria.CHEAP_RATIO)


if __name__ == "__main__":
    unittest.main()
