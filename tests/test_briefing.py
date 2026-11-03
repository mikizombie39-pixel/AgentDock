"""仲間に渡す材料の組み立ての試験。"""
import os
import tempfile
import unittest

from agentdock import briefing, broadcast, criteria, db, plans, results
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE


class BriefingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main(["--with-aji-s"]), 0)
        c = db.connect("iwase")
        try:
            broadcast.publish(broadcast.compose(c, D))
        finally:
            c.close()
        results.publish(results.today_results())
        self.amiya = db.connect("amiya")
        self.iwase = db.connect("iwase")

    def tearDown(self):
        self.amiya.close()
        self.iwase.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_buyer_material_numbers_come_from_code(self):
        plans.approve(self.amiya, "aji_s", "purchase", D, 3000, 3)
        m = briefing.build(self.amiya, "aji_s", "purchase", D)
        self.assertEqual(m["market_recent"], criteria.market_stats(self.amiya, "aji_s", D))
        self.assertEqual(m["approved_plan"], dict(max_unit_price_yen=3000, max_quantity=3))
        o = m["orders"][0]
        self.assertEqual((o["customer"], o["quantity"], o["ceiling_yen"]), ("スーパー", 3, 3000))
        self.assertEqual(o["sell_ratio"]["cheap_ratio"], 2.0)
        self.assertEqual(m["cheap_offer"]["expected_yen"], 2500)
        self.assertEqual(m["results"]["status"], "awaiting_result")
        text = briefing.render(m)
        self.assertIn("3,000円 まで・3箱 まで", text)
        self.assertIn("安い日は注文の 2.0倍売れた", text)
        self.assertIn("直近の中央値の 74%", text)
        self.assertIn("まだ届いていない", text)

    def test_buyer_shiroebi_short(self):
        text = briefing.render(briefing.build(self.amiya, "shiroebi", "tender", D))
        self.assertIn("落札 3箱・不足 2箱・余り 0箱", text)
        self.assertIn("承認済みの計画：なし", text)

    def test_seller_material(self):
        m = briefing.build(self.iwase, "buri_xl", "negotiation", D)
        self.assertEqual(m["floor"], dict(floor_yen=17000, held_up_for_tomorrow=True))
        self.assertEqual(m["landing"]["tomorrow_forecast"], 30)
        text = briefing.render(m)
        self.assertIn("下限：17,000円（明日の水揚げが多いので引き上げ）", text)
        self.assertIn("水揚げ 12本・売れた 6本・残り 6本", text)
        self.assertNotIn("注文", text)

    def test_no_other_owner_names(self):
        for con, item in ((self.amiya, "buri_xl"), (self.amiya, "shiroebi"), (self.iwase, "buri_xl")):
            text = briefing.render(briefing.build(con, item, "negotiation", D))
            for name in ("B社", "C社", "buyer_b", "buyer_c", "other"):
                self.assertNotIn(name, text)

    def test_fingerprint_changes_with_new_order(self):
        a = briefing.build(self.amiya, "buri_xl", "negotiation", D)["fingerprint"]
        self.assertEqual(a, briefing.build(self.amiya, "buri_xl", "negotiation", D)["fingerprint"])
        iid = self.amiya.execute("SELECT id FROM item WHERE code = 'buri_xl'").fetchone()[0]
        cid = self.amiya.execute("SELECT id FROM customer WHERE name = '料亭'").fetchone()[0]
        with self.amiya:
            self.amiya.execute("INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, 2, 24000, ?, ?)",
                               (cid, iid, D, f"{D}T07:10:00+09:00"))
        m = briefing.build(self.amiya, "buri_xl", "negotiation", D)
        self.assertNotEqual(a, m["fingerprint"])
        self.assertIn("料亭 2本・売値 24,000円・上限 20,500円", briefing.render(m))


if __name__ == "__main__":
    unittest.main()
