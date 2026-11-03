"""Agent Network の試験：掲示が他の組織を起こす・問い合わせと返事・読める範囲・開示の検査。"""
import os
import tempfile
import unittest

from agentdock import db, disclosure, events, network
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
UNTIL = sc.DEAL_REQUEST_VALID_UNTIL


class NetworkMessageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def pending(self, owner, kinds=None):
        c = db.connect(owner)
        try:
            return events.pending(c, kinds)
        finally:
            c.close()

    def test_post_wakes_every_other_owner(self):
        rid = network.post("buyer_b", "buy", "aji", 5, 5, 1650, "アジを 5kg 買いたいです。", D, UNTIL)
        for owner in db.OWNER_CODES:
            got = [e["payload"] for e in self.pending(owner, ["info_received"])]
            if owner == "buyer_b":
                self.assertEqual(got, [])
            else:
                self.assertEqual(got, [dict(source="deal_request", request_id=rid, from_owner="buyer_b")])

    def test_blocked_post_wakes_nobody(self):
        with self.assertRaises(disclosure.DisclosureError):
            network.post("buyer_b", "buy", "aji", 5, 5, 1650, "料亭の注文で要ります。", D, UNTIL)
        self.assertEqual(self.pending("amiya", ["info_received"]), [])

    def test_inquiry_and_reply(self):
        q = network.send("amiya", "iwase", "inquiry", "aji", "アジを 10kg、1kg 1,400円で譲っていただけますか。", D, 10, 1400, side="buy")
        e = self.pending("iwase", ["quote_request"])
        self.assertEqual([x["payload"] for x in e], [dict(message_id=q, from_owner="amiya", kind="inquiry")])
        r = network.send("iwase", "amiya", "offer", "aji", "その値では出せません。15kg まとめてなら 1kg 1,500円です。", D, 15, 1500, in_reply_to=q, valid_until=UNTIL)
        e = self.pending("amiya", ["quote_received"])
        self.assertEqual([x["payload"]["message_id"] for x in e], [r])
        self.assertEqual(network.message_for("amiya", r)["unit_price_yen"], 1500)

    def test_reply_must_answer_a_message_to_you(self):
        q = network.send("amiya", "iwase", "inquiry", "aji", "アジを 10kg 譲っていただけますか。", D, 10, None, side="buy")
        with self.assertRaises(network.NetworkError):
            network.send("buyer_c", "amiya", "decline", "aji", "お断りします。", D, in_reply_to=q)   # C社宛ではない
        with self.assertRaises(network.NetworkError):
            network.send("iwase", "amiya", "inquiry", "aji", "ご用件は。", D, in_reply_to=q)          # 問い合わせは返事にならない
        with self.assertRaises(network.NetworkError):
            network.send("iwase", "amiya", "offer", "aji", "どうぞ。", D, 10, 1700, valid_until=UNTIL)                   # 返事に元の文が無い

    def test_third_party_cannot_read(self):
        q = network.send("amiya", "buyer_c", "inquiry", "aji", "8kg のうち 5kg だけ譲っていただけますか。", D, 5, 1600, side="buy")
        self.assertEqual(network.message_for("buyer_c", q)["quantity"], 5)
        with self.assertRaises(network.NetworkError):
            network.message_for("buyer_b", q)

    def test_reply_text_is_checked_on_the_sender_side(self):
        q = network.send("amiya", "iwase", "inquiry", "aji", "アジを 10kg 譲っていただけますか。", D, 10, None, side="buy")
        with self.assertRaises(disclosure.DisclosureError):
            network.send("iwase", "amiya", "decline", "aji", "下限を割るのでお断りします。", D, in_reply_to=q)
        self.assertEqual(self.pending("amiya", ["quote_received"]), [])


if __name__ == "__main__":
    unittest.main()
