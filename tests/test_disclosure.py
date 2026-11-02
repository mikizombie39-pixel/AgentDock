"""送る前の開示の検査の試験。"""
import os
import tempfile
import unittest

from agentdock import consult, db, disclosure, plans
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE


class DisclosureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.amiya = db.connect("amiya")
        self.iwase = db.connect("iwase")
        self.task = consult.open_task(self.amiya, "negotiation", "buri_xl")

    def tearDown(self):
        self.amiya.close()
        self.iwase.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def blocked(self, text, price=17500, qty=4, con=None):
        return disclosure.check(con or self.amiya, "buri_xl", price, qty, text, D)

    def test_amounts_are_read_in_many_forms(self):
        self.assertEqual(disclosure.amounts("18,500円"), {18500})
        self.assertEqual(disclosure.amounts("１８，５００円"), {18500})
        self.assertEqual(disclosure.amounts("1万8500円"), {18500})
        self.assertEqual(disclosure.amounts("1万8,500円と2万円"), {18500, 20000})
        self.assertEqual(disclosure.amounts("4本"), set())

    def test_clean_offer_is_sent_and_recorded(self):
        seq = disclosure.send(self.amiya, self.task, "buri_xl", 17500, 4, "1本 17,500円で4本、いかがでしょうか。合計 70,000円です。", D)
        self.assertEqual(seq, 1)
        row = self.amiya.execute("SELECT seq, passed_disclosure_check, body FROM message").fetchone()
        self.assertEqual((row[0], row[1]), (1, 1))
        self.assertIn("17,500円", row[2])
        self.assertEqual(disclosure.send(self.amiya, self.task, "buri_xl", 17800, 4, "17,800円ではいかがでしょう。", D), 2)

    def test_own_ceiling_is_blocked_in_any_form(self):
        for text in ("18,500円までなら出せます。", "１８５００円までは大丈夫です。", "1万8500円までなら。"):
            self.assertEqual(self.blocked(text), ["amount 18500 (ceiling:スーパー)"], text)

    def test_other_secrets_and_words(self):
        self.assertIn("amount 22000 (sales_price:スーパー)", self.blocked("店では 22,000円で売ります"))
        self.assertIn("amount 2000 (process_cost)", self.blocked("手間が 2,000円かかるので"))
        self.assertIn("word 上限", self.blocked("こちらの上限に近い値です"))
        self.assertIn("customer 料亭", self.blocked("料亭の分も欲しいので"))

    def test_offering_at_the_ceiling_itself_is_allowed(self):
        self.assertEqual(self.blocked("18,500円でいかがでしょう。", price=18500), [])

    def test_approved_plan_value_is_secret(self):
        plans.approve(self.amiya, "buri_xl", "negotiation", D, 19000, 6)
        self.assertEqual(self.blocked("19,000円までは考えます"), ["amount 19000 (plan_max)"])

    def test_seller_floor_is_blocked(self):
        t = consult.open_task(self.iwase, "negotiation", "buri_xl")
        self.assertEqual(self.blocked("17,000円より下は無理です", price=18000, qty=6, con=self.iwase), ["amount 17000 (floor)"])
        with self.assertRaises(disclosure.DisclosureError) as e:
            disclosure.send(self.iwase, t, "buri_xl", 18000, 6, "下限は 17,000円です", D)
        self.assertEqual(e.exception.reasons, ["amount 17000 (floor)", "word 下限"])
        self.assertEqual(self.iwase.execute("SELECT count(*) FROM message").fetchone()[0], 0)
        ev = self.iwase.execute("SELECT actor, event FROM audit_event WHERE event = 'disclosure_blocked'").fetchone()
        self.assertEqual(tuple(ev), ("hook", "disclosure_blocked"))
        self.assertEqual(disclosure.send(self.iwase, t, "buri_xl", 18000, 6, "1本 18,000円で6本をお出しできます。", D), 1)


if __name__ == "__main__":
    unittest.main()
