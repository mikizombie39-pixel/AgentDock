"""2つ以上の相手の条件を1つの案にまとめる（2026-11-04 デイブ・案B）の試験（模擬の Claude・本物の Claude は呼ばない）。

場面：魚廣 4本・順風満帆 6本。岩瀬漁協が 6本（17,500円）、B社が 2本（18,400円）を仮押さえ。
案1＝岩瀬漁協 6本＋B社 2本：魚廣 4本・順風満帆 4本（この案では足りない：順風満帆 2本）
決まり：配分・金額・不足はコードが計算／権限は相手ごと／社長の指示と優先は案全体の配分で判定／
人の承認は案全体に1回・登録は相手ごと（相手ごとに再検証）／登録できなかった行があれば案件は partial（一部登録）・残りは相談し直す／二重に登録しない
"""
import json
import time

from agentdock import agents, db, deal, market, mate, network, workspace
from tests import test_mate as tm

D = tm.D
SUPER, RYOTEI = tm.SUPER, tm.RYOTEI


def q(owner, sql, args=()):
    c = db.connect(owner if owner != "market" else db.MARKET)
    try:
        return [tuple(r) for r in c.execute(sql, args)]
    finally:
        c.close()


class MultiBase(tm.Base):
    def consult(self):
        v = tm.NumbersTest.consult(self)
        self.combo = next(a for a in v["actions"] if a.get("recommended"))
        self.assertEqual(self.combo["label"], f"岩瀬漁協 6本＋B社 2本：{SUPER} 4本・{RYOTEI} 4本")
        b = self.combo["api"]["body"]
        self.case, self.opt = b["case_id"], b["option_id"]
        self.numbers = json.loads(q("amiya", "SELECT numbers_json FROM board_option WHERE id = ?", (self.opt,))[0][0])
        self.terms = {p["seller"]: p["term_id"] for p in self.numbers["purchases"]}
        time.sleep(1.05)
        return v

    def approve(self, key="m1", actor="staff", fail_at=None, option=None):
        o = option or self.combo
        b = o["api"]["body"]
        return deal.decide("amiya", b["case_id"], b["option_id"], "approve", b["fingerprint"], key, actor=actor, fail_at=fail_at)

    def b_hold(self, status):
        h = network.term(self.terms["buyer_b"])["hold_ref"]
        tm.ex("buyer_b", "UPDATE stock_hold SET status = ? WHERE id = ?", (status, h))

    def amiya_stock(self):
        return q("amiya", "SELECT order_id, quantity, unit_cost_yen, trade_id FROM stock WHERE status = 'allocated' ORDER BY id")

    def proposals(self):
        return q("amiya", "SELECT counterparty, quantity, status, trade_id, fail_reason FROM proposal WHERE case_id = ? AND side = 'buy' ORDER BY id", (self.case,))


class ComputeTest(MultiBase):
    def test_two_counterparties_are_computed_by_the_code(self):
        self.consult()
        n = self.numbers
        self.assertEqual([(p["seller"], p["quantity"], p["unit_price_yen"]) for p in n["purchases"]], [("iwase", 6, 17500), ("buyer_b", 2, 18400)])
        self.assertEqual(n["purchase_yen"], 6 * 17500 + 2 * 18400)
        sup = [(s["seller"], s["to"], s["quantity"]) for s in n["supply"]]   # 安い仕入れから、上限の低い注文へ
        self.assertEqual(sup, [("iwase", "スーパー", 4), ("iwase", "料亭", 2), ("buyer_b", "料亭", 2)])
        self.assertEqual(n["gross_profit_yen"], 4 * 22000 + 4 * 24000 - (6 * 17500 + 2 * 18400) - 8 * 2000)
        self.assertTrue(n["executable"])
        self.assertEqual(self.combo["short_after"], [dict(shop=RYOTEI, quantity=2)])
        self.assertFalse(self.combo["president"])                          # 指示どおり（魚廣を全部満たす）・相手ごとの権限の内

    def test_one_counterparty_twice_is_not_one_option(self):
        self.consult()
        alt = [t["id"] for t in q_terms("iwase")]
        c = db.connect("amiya")
        try:
            n = workspace.compute(c, "amiya", "buri_xl", dict(buy_term_id=None, buy_term_ids=alt, allocations=[]), f"{D}T06:30:00+09:00")
        finally:
            c.close()
        self.assertIn("1つの案に、同じ相手の条件は1つだけ入れられます（同じ返事の条件はどちらか一方）", n["errors"])

    def test_authority_is_per_counterparty(self):
        self.consult()
        tm.ex("amiya", "UPDATE authority SET max_trade_yen = 120000 WHERE role = 'staff'")   # 合計 141,800円 ＞ 120,000円でも、相手ごとなら内
        self.assertFalse(mate.option_needs_president("amiya", self.case, self.opt)["needs"])
        self.assertEqual(self.approve()["result"], "registered")

    def test_one_row_over_the_limit_sends_the_whole_option_to_the_president(self):
        self.consult()
        tm.ex("amiya", "UPDATE authority SET max_trade_yen = 100000 WHERE role = 'staff'")   # 岩瀬漁協の行 105,000円が上を超える
        pv = mate.option_needs_president("amiya", self.case, self.opt)
        self.assertEqual((pv["needs"], pv["over_limit"]), (True, True))
        r = self.approve()
        self.assertEqual(r["result"], "needs_president")
        self.assertEqual([x["seller"] for x in r["authority"]["rows"] if not x["allowed"]], ["iwase"])
        self.assertEqual(self.proposals(), [])                               # まだ登録しない
        self.assertEqual(self.approve("p1", actor="president")["result"], "registered")

    def test_a_combination_against_the_directive_goes_to_the_president(self):
        self.consult()
        orders = dict(q("amiya", "SELECT c.name, o.id FROM order_book o JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id "
                                 "WHERE i.code = 'buri_xl'"))
        payload = dict(item_code="buri_xl", summary="s", headline="h", short_question="どう分けますか", ask_human="a", cards=[], recommended="A",
                       options=[dict(code="A", label="順風満帆を先に", short_label="順風満帆を先に", buy_term_id=None, buy_term_ids=list(self.terms.values()),
                                     allocations=[dict(kind="order", ref_id=orders["料亭"], customer=None, quantity=6),
                                                  dict(kind="order", ref_id=orders["スーパー"], customer=None, quantity=2)],
                                     reason="r", risk="魚廣が欠ける", differs_from_decision=False, vault_refs=[])])
        c = db.connect("amiya")
        try:
            workspace.build(c, "amiya", "buri_xl", D, payload, 1, f"{D}T06:30:00+09:00")
        finally:
            c.close()
        v = mate.view("amiya", "buri_xl")
        a = v["actions"][0]
        self.assertTrue(a["president"])
        self.assertEqual(a["label"], f"社長に確かめる：岩瀬漁協 6本＋B社 2本：{RYOTEI} 6本・{SUPER} 2本")
        time.sleep(1.05)
        self.assertEqual(self.approve("d1", option=a)["result"], "needs_president")


class RegisterTest(MultiBase):
    def test_both_rows_are_registered_after_one_approval(self):
        self.consult()
        r = self.approve()
        self.assertEqual((r["result"], r["failed"]), ("registered", []))
        self.assertEqual([p[:3] for p in self.proposals()], [("iwase", 6, "registered"), ("buyer_b", 2, "registered")])
        for tid in r["trades"]:
            self.assertEqual(market.trade(tid)["status"], "registered")
        stock = self.amiya_stock()
        self.assertEqual(sorted((o, n, cost) for o, n, cost, _ in stock), [(1, 4, 17500), (3, 2, 17500), (3, 2, 18400)])
        self.assertEqual(q("buyer_b", "SELECT status FROM stock WHERE lot_no = 'B-buri_xl-1'"), [("sold",)])
        self.assertEqual(q("amiya", "SELECT status FROM deal_case WHERE id = ?", (self.case,)), [("registered",)])
        self.assertEqual(self.approve("m2")["result"], "stale")               # もう一度押しても二重に登録しない（条件はもう確定済み）
        self.assertEqual(len(self.amiya_stock()), 3)

    def check_partial(self, r, reason_part, short_reason):
        self.assertEqual(r["result"], "partial")
        p = self.proposals()
        self.assertEqual([x[:3] for x in p], [("iwase", 6, "registered"), ("buyer_b", 2, "failed")])
        self.assertIn(reason_part, p[1][4])
        self.assertEqual(q("amiya", "SELECT status FROM deal_case WHERE id = ?", (self.case,)), [("partial",)])
        self.assertEqual(sorted((o, n) for o, n, _, _ in self.amiya_stock()), [(1, 4), (3, 2)])   # 登録できた行の分だけ在庫に充てる
        self.assertEqual(q("market", "SELECT status FROM offer_term WHERE id = ?", (self.terms["buyer_b"],)), [("released",)])
        ev = [json.loads(x[0]) for x in q("amiya", "SELECT payload_json FROM biz_event WHERE kind = 'trade_registered' ORDER BY id")]
        self.assertEqual([(f["seller"], f["quantity"]) for f in ev[-1]["failed"]], [("buyer_b", 2)])   # AI が起き、残りの不足で相談し直す
        prompt, _ = agents.situation("amiya", dict(kind="trade_registered", payload=ev[-1]), D)
        self.assertIn("登録できなかった行：B社 2", prompt)
        v = mate.view("amiya", "buri_xl")
        self.assertEqual(v["receipt"]["text"], f"受け取りました。岩瀬漁協 6本は登録しました。B社 2本は{short_reason}ので登録していません。"
                                               f"残りの {RYOTEI} 4本を、私が相談し直します。")
        again = self.approve("m9")                                            # 同じ案の承認をもう一度：二重に登録しない
        self.assertEqual((again["result"], again.get("repeated")), ("partial", True))
        self.assertEqual(deal.resume("amiya", self.case)["result"], "partial")
        self.assertEqual(len(self.amiya_stock()), 2)

    def test_a_row_whose_hold_expired_is_left_and_the_rest_is_registered(self):
        self.consult()
        self.b_hold("expired")                                               # B社の側で押さえの期限が切れた（B社の決まりで再検証）
        self.check_partial(self.approve(), "仮押さえが無い（expired）", "期限が切れた")

    def test_a_row_the_seller_cannot_fill_is_left(self):
        self.consult()
        tm.ex("buyer_b", "UPDATE stock SET status = 'sold' WHERE lot_no = 'B-buri_xl-1'")   # B社がほかへ売った（相手の断り）
        self.check_partial(self.approve(), "在庫が足りない", "相手の在庫が足りなくなった")

    def test_a_row_the_ledger_refuses_is_left(self):
        self.consult()
        self.check_partial(self.approve(fail_at="ledger:buyer_b"), "帳簿に登録できませんでした", "帳簿に登録できなかった")

    def test_a_stop_in_the_middle_resumes_from_where_it_stopped(self):
        self.consult()
        r = self.approve(fail_at="stop:buyer_b")
        self.assertEqual((r["result"], r["done"]), ("incomplete", ["row:iwase"]))
        self.assertEqual([x[:3] for x in self.proposals()], [("iwase", 6, "registered"), ("buyer_b", 2, "approved")])
        self.assertEqual(len(self.amiya_stock()), 2)
        r = deal.resume("amiya", self.case)                                   # 照合して続きから：岩瀬漁協の行はもう一度登録しない
        self.assertEqual((r["result"], r["done"]), ("registered", ["row:iwase", "row:buyer_b"]))
        self.assertEqual(len(self.amiya_stock()), 3)
        self.assertEqual(len(q("market", "SELECT id FROM trade WHERE trade_id LIKE ?", (f"case{self.case}-buy-%",))), 2)

    def test_when_every_row_fails_the_option_is_stale(self):
        self.consult()
        self.b_hold("expired")
        h = network.term(self.terms["iwase"])["hold_ref"]
        tm.ex("iwase", "UPDATE stock_hold SET status = 'expired' WHERE id = ?", (h,))
        r = self.approve()
        self.assertEqual(r["result"], "stale")
        self.assertEqual(self.proposals(), [])


def q_terms(seller):
    m = db.connect(db.MARKET)
    try:
        return [dict(r) for r in m.execute("SELECT id, quantity FROM offer_term WHERE seller = ? AND status = 'offered' ORDER BY id", (seller,))]
    finally:
        m.close()
