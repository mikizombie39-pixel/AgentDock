"""磁石ボードの試験：金額はコードが計算する・文の中の誤った金額を止める・実行できない案の印・未確認の加工の費用。

条件は SAL の指示の例（漁協 10kg 1,720円・15kg 1,600円、B社 5kg 1,650円、加工 200円/kg）で作る。
"""
import os
import tempfile
import unittest

from agentdock import db, network, scene, workspace
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
NOW = "2026-11-02T06:30:00+09:00"
LATER = "2026-11-02T07:30:00+09:00"   # 条件の期限（30分）の後
UNTIL = "2026-11-02T07:00:00+09:00"


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        st = scene.start()
        self.order = st["amiya_order"][0]
        q = network.send("amiya", "iwase", "inquiry", "aji", "アジを 15kg、1kg 1,500円でいかがでしょうか。", D, 15, 1500, side="buy")
        r = network.send("iwase", "amiya", "offer", "aji", "10kg なら 1,720円、15kg まとめてなら 1,600円です。", D,
                         terms=[(10, 1720), (15, 1600)], in_reply_to=q, valid_until=UNTIL)
        self.t10, self.t15 = [t["id"] for t in network.terms_for_message(r)]
        network.attach_hold(r, 1)
        q2 = network.send("amiya", "buyer_b", "inquiry", "aji", "5kg を 1kg 1,650円でお譲りできます（仕入れの確定が条件）。", D, 5, 1650, side="sell")
        r2 = network.send("buyer_b", "amiya", "offer", "aji", "お願いします。", D, terms=[(5, 1650)], in_reply_to=q2, valid_until=UNTIL)
        self.ag = network.make_agreement(network.terms_for_message(r2)[0]["id"], "網谷水産の担当者の承認を条件とする", "conditional", UNTIL)
        self.con = db.connect("amiya")

    def tearDown(self):
        self.con.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def plan_a(self):
        return dict(buy_term_id=self.t15, allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10),
                                                      dict(kind="agreement", ref_id=self.ag, customer=None, quantity=5)])

    def plan_10(self):
        return dict(buy_term_id=self.t10, allocations=[dict(kind="order", ref_id=self.order, customer=None, quantity=10)])

    def payload(self, summary="漁協の条件と B社の返事がそろいました。", reason="追加の分の売り先がある"):
        return dict(item_code="aji", summary=summary, recommended="A",
                    cards=[dict(card_type="order", ref_id=self.order, note=None), dict(card_type="quote", ref_id=self.t15, note=None)],
                    options=[dict(code="A", label="15kg 仕入れ・5kg を B社へ", reason=reason, risk="B社の合意は条件付き", **self.plan_a()),
                             dict(code="D", label="10kg だけ", reason="上限を超える", risk="利が薄い", **self.plan_10())])

    def test_sal_example_numbers(self):
        a = workspace.compute(self.con, "amiya", "aji", self.plan_a(), NOW)
        d = workspace.compute(self.con, "amiya", "aji", self.plan_10(), NOW)
        self.assertEqual((a["revenue_yen"], a["purchase_yen"], a["process_cost_yen"], a["gross_profit_yen"]), (29250, 24000, 3000, 2250))
        self.assertEqual((d["revenue_yen"], d["purchase_yen"], d["process_cost_yen"], d["gross_profit_yen"]), (21000, 17200, 2000, 1800))
        self.assertEqual(a["gross_profit_yen"] - d["gross_profit_yen"], 450)
        self.assertEqual(a["profit_kind"], "見込み")
        self.assertEqual(a["uncounted_costs"], ["運賃（受け渡しの方法が未定）"])
        self.assertEqual(a["uncertain_quantity"], 5)                        # B社の 5kg は条件付きの合意

    def test_unconfirmed_process_is_not_a_final_profit(self):
        with self.con:
            self.con.execute("UPDATE route_process SET status = 'unconfirmed' WHERE destination = 'buyer_b'")
        a = workspace.compute(self.con, "amiya", "aji", self.plan_a(), NOW)
        self.assertIsNone(a["gross_profit_yen"])                             # 確定値としては出さない
        self.assertEqual(a["gross_profit_without_unconfirmed_yen"], 29250 - 24000 - 2000)   # B社の分の加工を除いた値（参考）
        self.assertEqual(a["unconfirmed_process"], ["B社"])

    def test_expired_term_is_not_executable(self):
        a = workspace.compute(self.con, "amiya", "aji", self.plan_a(), LATER)
        self.assertFalse(a["executable"])
        self.assertTrue(any("期限切れ" in e for e in a["errors"]))

    def test_allocation_must_match_purchase(self):
        p = self.plan_a()
        p["allocations"] = p["allocations"][:1]                             # 15kg 仕入れて 10kg しか配らない
        a = workspace.compute(self.con, "amiya", "aji", p, NOW)
        self.assertFalse(a["executable"])
        p["allocations"].append(dict(kind="unallocated", ref_id=None, customer=None, quantity=5))
        a = workspace.compute(self.con, "amiya", "aji", p, NOW)
        self.assertTrue(a["executable"])
        self.assertEqual((a["unsold_quantity"], a["unsold_loss_yen"]), (5, 8000))   # 売れ残りの危険は数字で出す

    def test_board_is_built_with_code_numbers(self):
        r = workspace.build(self.con, "amiya", "aji", D, self.payload(), 1, NOW)
        v = workspace.view(self.con, r["case_id"])
        self.assertEqual([o["code"] for o in v["options"]], ["A", "D"])
        self.assertEqual(v["options"][0]["numbers"]["gross_profit_yen"], 2250)
        self.assertEqual([c["card_type"] for c in v["cards"]], ["order", "quote"])
        self.assertEqual(v["cards"][1]["data"]["unit_price_yen"], 1600)
        r2 = workspace.build(self.con, "amiya", "aji", D, self.payload(), 2, NOW)          # 出し直すと版が上がり、古い案は下がる
        self.assertEqual(r2["version"], 2)
        self.assertEqual(self.con.execute("SELECT count(*) FROM board_option WHERE status = 'withdrawn'").fetchone()[0], 2)

    def test_wrong_amount_in_text_is_refused(self):
        with self.assertRaises(workspace.BoardError) as e:
            workspace.build(self.con, "amiya", "aji", D, self.payload(reason="スーパーの粗利は 5,000円"), 1, NOW)   # 加工の費用が抜けた値
        self.assertIn("5,000円", str(e.exception))
        self.assertEqual(self.con.execute("SELECT count(*) FROM board_option").fetchone()[0], 0)
        workspace.build(self.con, "amiya", "aji", D, self.payload(reason="見込みの粗利は 2,250円"), 1, NOW)        # 計算と合う値は書ける

    def test_cards_only_from_own_scope(self):
        other_vault = db.connect("buyer_b")
        try:
            vid = other_vault.execute("SELECT id FROM vault_note").fetchone()[0]
        finally:
            other_vault.close()
        p = self.payload()
        p["cards"] = [dict(card_type="agreement", ref_id=self.ag, note=None)]
        workspace.build(self.con, "amiya", "aji", D, p, 1, NOW)                              # 自社の合意は出せる
        c = db.connect("buyer_c")
        try:
            with self.assertRaises(workspace.BoardError):
                workspace.build(c, "buyer_c", "aji", D, dict(p, cards=[dict(card_type="quote", ref_id=self.t15, note=None)]), 1, NOW)
        finally:
            c.close()
        self.assertIsNotNone(vid)


if __name__ == "__main__":
    unittest.main()
