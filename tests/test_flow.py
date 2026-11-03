"""模擬の仲間で流す通しの1本：入札 → 結果 → 仲買どうしの相対 → 範囲の外の相談 → 再開 → 登録 → 照合 → 帳面。

Claude は呼ばない。仲間の返事は決まった模擬の返事で、判定・検査・記録はすべて本物の部品が行う。
"""
import os
import tempfile
import unittest

from agentdock import broadcast, briefing, companion, consult, db, evidence, market, plans, results, runlog, vaultbook
from agentdock.companion import Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
OPEN, CLOSE, AT = f"{D}T06:00:00+09:00", f"{D}T06:30:00+09:00", f"{D}T06:10:00+09:00"
U = dict(input_tokens=5000, output_tokens=1200, cache_read_input_tokens=3000)
EV = [{"kind": "price_history", "ref_id": None, "summary": "直近30日の中央値"}]


def offer(price, qty, msg=None):
    return Reply(content=[{"type": "tool_use", "id": f"o{price}{qty}", "name": "make_offer",
                           "input": dict(unit_price_yen=price, quantity=qty, message=msg or f"1つ {price:,}円で{qty}、いかがでしょうか。",
                                         rationale="材料の相場と注文から", evidence=EV)}], usage=U)


def accept(price, qty):
    return Reply(content=[{"type": "tool_use", "id": f"a{price}{qty}", "name": "accept",
                           "input": dict(unit_price_yen=price, quantity=qty, rationale="範囲の中", evidence=EV)}], usage=U)


class FlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main(["--with-aji-s"]), 0)
        self.cons = {code: db.connect(code) for code in db.OWNER_CODES}

    def tearDown(self):
        for c in self.cons.values():
            c.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def ctx(self, owner, task, item, nid):
        return companion.Context(owner=owner, task_id=task, item_code=item, on_date=D, negotiation_id=nid,
                                 version=market.negotiation(nid)["version"])

    def talk(self, owner, ctx, *replies):
        return companion.run(companion.MockClient(list(replies)), self.cons[owner], ctx, "system",
                             briefing.render(briefing.build(self.cons[owner], ctx.item_code, "negotiation", D)))

    def test_one_morning(self):
        amiya, iwase, c_con = self.cons["amiya"], self.cons["iwase"], self.cons["buyer_c"]

        # ① 漁協が3社に同じ資料を届ける
        bid = broadcast.publish(broadcast.compose(iwase, D))
        self.assertTrue(broadcast.verify(bid))

        # ② シロエビの入札 → 締め → 結果を全員へ
        market.open_lot("T-shiroebi-1", "shiroebi", 20, "iwase", "coop", "tender", D)
        market.open_tender("T-shiroebi-1", OPEN, CLOSE)
        for who, p, q in sc.TODAY_TENDER["bids"]:
            market.submit_bid("T-shiroebi-1", who, p, q, now=AT)
        market.close_tender("T-shiroebi-1", now=CLOSE)
        rows = market.awards_as_results("T-shiroebi-1") + [r for r in results.today_results() if r["method"] == "auction"]
        results.publish(rows)
        self.assertTrue(results.verify())
        line = {x["item"]: x for x in results.reconcile("amiya")["lines"]}
        self.assertEqual((line["shiroebi"]["short"], line["buri_xl"]["short"]), (2, 4))
        self.assertEqual(next(x for x in results.reconcile("iwase")["lines"] if x["item"] == "buri_xl")["remaining"], 6)

        # ③ C社の余り2箱を、網谷水産の仲間が相対で買う（範囲の中＝社長に聞かない）
        market.open_lot("R-shiroebi-c-1", "shiroebi", 2, "buyer_c", "buyer", "negotiation", D)
        plans.approve(amiya, "shiroebi", "negotiation", D, 4800, 2)
        plans.approve(c_con, "shiroebi", "negotiation", D, None, 2, min_unit_price_yen=4350)
        nid_c = market.open_negotiation("R-shiroebi-c-1", "amiya")
        t_a_ebi = consult.open_task(amiya, "negotiation", "shiroebi")
        t_c_ebi = consult.open_task(c_con, "negotiation", "shiroebi")
        self.assertEqual(self.talk("amiya", self.ctx("amiya", t_a_ebi, "shiroebi", nid_c), offer(4500, 2)).outcome, "offered")
        self.assertEqual(self.talk("buyer_c", self.ctx("buyer_c", t_c_ebi, "shiroebi", nid_c), accept(4500, 2)).outcome, "accepted")
        fp = market.propose("TR-1", "R-shiroebi-c-1", "amiya", 4500, 2)
        market.approve("TR-1", "buyer_c", fp)
        self.assertEqual(market.approve("TR-1", "amiya", fp), "registered")

        # ④ ブリ：料亭の注文が後から入る → 漏れる文は止まる → 範囲の外は相談へ
        market.open_lot("N-buri_xl-1", "buri_xl", 6, "iwase", "coop", "negotiation", D)
        plans.approve(amiya, "buri_xl", "negotiation", D, 18500, 4)
        plans.approve(iwase, "buri_xl", "negotiation", D, None, 6, min_unit_price_yen=17000)
        t_buri = consult.open_task(amiya, "negotiation", "buri_xl")
        t_iwase = consult.open_task(iwase, "negotiation", "buri_xl")
        iid = amiya.execute("SELECT id FROM item WHERE code = 'buri_xl'").fetchone()[0]
        cid = amiya.execute("SELECT id FROM customer WHERE name = '料亭'").fetchone()[0]
        with amiya:
            amiya.execute("INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, 2, 24000, ?, ?)",
                          (cid, iid, D, f"{D}T07:10:00+09:00"))
        consult.revise(amiya, t_buri, "料亭から 2本の注文")
        self.assertFalse(consult.can_proceed(amiya, t_buri))                       # 見直しの間は動かない
        consult.revalidated(amiya, t_buri, briefing.build(amiya, "buri_xl", "negotiation", D)["fingerprint"])
        nid_b = market.open_negotiation("N-buri_xl-1", "amiya")
        ctx = self.talk("amiya", self.ctx("amiya", t_buri, "buri_xl", nid_b),
                        offer(18000, 6, msg="18,500円までなら出せます。"), offer(19500, 6))
        self.assertEqual(ctx.outcome, "needs_owner")

        # アジ：注文3箱より多く買う案は範囲の外 → 相談
        plans.approve(amiya, "aji_s", "purchase", D, 3000, 3)
        t_aji = consult.open_task(amiya, "purchase", "aji_s")
        with self.assertRaises(evidence.OutsidePlan):
            evidence.record(amiya, t_aji, "buy", 2500, 6, "安い日は注文の2倍売れる", [("broadcast", bid, "漁協の資料")], D)
        evidence.record(amiya, t_aji, "escalate", 2500, 6, "注文より3箱多く買う案", [("broadcast", bid, "漁協の資料")], D)

        # ⑤ 2件を社長のスマホの1枚にまとめて相談 → 答えと理由 → 帳面へ
        gid = consult.bundle(amiya, "ブリとアジの相談", [
            dict(task_id=t_buri, question="料亭の分を含めて、上限を上げますか",
                 options=[("prefer_restaurant", "料亭の分を含めて上げる", True), ("prefer_supermarket", "スーパーの分だけ", True), ("pass", "見送る", True)]),
            dict(task_id=t_aji, question="注文より3箱多く買いますか",
                 options=[("buy_extra", "多めに買う", True), ("order_only", "注文の数だけ", True)])])
        card = consult.card(amiya, gid)
        self.assertEqual(len(card["items"]), 2)
        ids = {i["task_id"]: i["consultation_id"] for i in card["items"]}
        consult.answer(amiya, ids[t_buri], "prefer_restaurant", "phone-1", "料亭は長い付き合い")
        consult.answer(amiya, ids[t_aji], "buy_extra", "phone-2", "安い日はよく売れる")
        self.assertEqual(consult.card(amiya, gid)["status"], "answered")
        for c in ids.values():
            vaultbook.record_from_consultation(amiya, c)
        plans.approve(amiya, "buri_xl", "negotiation", D, 20500, 6)
        plans.approve(amiya, "aji_s", "purchase", D, 3000, 6)
        self.assertTrue(evidence.sheet(amiya, evidence.record(amiya, t_aji, "buy", 2500, 6, "社長の承認で6箱", [("vault_case", None, "多めに買う")], D))["within_plan"])

        # ⑥ 再開：ブリを申し出 → 漁協の仲間が下限の上なので受ける → 双方の最終承認 → 登録
        self.assertTrue(consult.can_proceed(amiya, t_buri))
        self.assertEqual(self.talk("amiya", self.ctx("amiya", t_buri, "buri_xl", nid_b), offer(19000, 6)).outcome, "offered")
        self.assertEqual(self.talk("iwase", self.ctx("iwase", t_iwase, "buri_xl", nid_b), accept(19000, 6)).outcome, "accepted")
        fp = market.propose("TB-1", "N-buri_xl-1", "amiya", 19000, 6)
        market.approve("TB-1", "iwase", fp)
        self.assertEqual(market.approve("TB-1", "amiya", fp), "registered")

        # ⑦ 照合と帳面と実行の記録
        self.assertEqual({r["result"] for r in market.reconcile_owner("amiya")}, {"match"})
        self.assertEqual(market.reconcile_owner("iwase"), [dict(trade_id="TB-1", result="match")])
        self.assertEqual(market.reconcile_owner("buyer_c"), [dict(trade_id="TR-1", result="match")])
        self.assertEqual(sorted(tuple(r) for r in amiya.execute("SELECT external_trade_id, side, quantity FROM ledger_entry")),
                         [("TB-1", "buy", 6), ("TR-1", "buy", 2)])
        self.assertIn("帳面の事例", briefing.render(briefing.build(amiya, "buri_xl", "negotiation", D)))
        s = runlog.summary()
        self.assertEqual(s["blocked"]["disclosure_blocked"], 1)
        self.assertGreaterEqual(s["blocked"]["outside_plan_blocked"], 2)
        self.assertGreater(s["cost_micro_usd"], 0)
        self.assertEqual(self.cons["buyer_b"].execute("SELECT count(*) FROM ledger_entry").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
