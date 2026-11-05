"""漁協の集計案（照合・仲買への請求・荷主への支払）の試験（2026-11-05 デイブ・SAL の条件）。模擬の AI（本物の Claude は呼ばない）。
- 金額は実証用ルール（手数料 5%・手数料の消費税 10%・請求の消費税 8%・円未満は切り捨て）でコードが計算する
- 同じ取引を2回数えない（再実行・止まった後の再開）
- 初回の対象時点の後に成立した取引は、追加集計待ち → 反映済み。待ちが残る間は完了にしない
- 照合の不一致は記録し、請求と支払を始めない
- 仮押さえは入れない
"""
import time

from agentdock import db, settle
from tests import test_trace


def drain(limit=60):
    n = 0
    while settle.step():
        n += 1
        assert n < limit
    return n


def q(sql, args=()):
    c = db.connect("iwase")
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


class SettleTest(test_trace.TraceTest):
    test_no_registered_trade_yet = None
    test_one_line_from_the_trade_to_the_stock = None
    test_vault_refs_show_the_file_and_its_fingerprint = None

    def test_the_rule_and_the_amounts(self):
        rule = q("SELECT * FROM settle_rule")[0]
        self.assertEqual((rule["fee_permille"], rule["fee_tax_permille"], rule["sale_tax_permille"], rule["rounding"]), (50, 100, 80, "floor"))
        # 18,200円 × 4本 = 72,800円：請求の税 5,824円・請求額 78,624円／手数料 3,640円・その税 364円・支払額 68,796円
        self.assertEqual(settle.compute("invoice", 72800, rule), dict(fee_yen=0, tax_yen=5824, amount_yen=78624))
        self.assertEqual(settle.compute("payout", 72800, rule), dict(fee_yen=3640, tax_yen=364, amount_yen=68796))
        # 円未満は切り捨て：35,800円（17,900円 × 2本）の手数料 1,790円・その税 179円
        self.assertEqual(settle.compute("payout", 35800, rule), dict(fee_yen=1790, tax_yen=179, amount_yen=33831))
        self.assertEqual(settle.compute("invoice", 35805, rule)["tax_yen"], 2864)             # 2,864.4円 → 2,864円
        for who in ("amiya", "buyer_b", "buyer_c"):                                            # 仲買の DB には入れない
            c = db.connect(who)
            try:
                self.assertEqual(c.execute("SELECT count(*) FROM settle_rule").fetchone()[0], 0)
            finally:
                c.close()

    def test_reconcile_then_invoices_and_payouts_once_each(self):
        self.start()
        st = settle.status()
        self.assertEqual({k: j["status"] for k, j in st["jobs"].items()}, {"reconcile": "running", "invoice": "waiting", "payout": "waiting"})
        self.assertFalse(st["complete"])
        drain()
        st = settle.status()
        self.assertEqual({k: (j["status"], j["done"], j["total"]) for k, j in st["jobs"].items()},
                         {"reconcile": ("done", 8, 8), "invoice": ("done", 2, 2), "payout": ("done", 2, 2)})
        self.assertTrue(st["complete"])
        inv = {r["party"]: r["amount_yen"] for r in q("SELECT party, amount_yen FROM settle_line WHERE kind = 'invoice'")}
        self.assertEqual(inv, {"buyer_b": 78624, "other": 35800 + 2864})
        pay = q("SELECT c.name, sum(l.amount_yen) AS amt FROM settle_line l JOIN consignor c ON c.id = CAST(l.party AS INTEGER) WHERE l.kind = 'payout' GROUP BY c.name")
        self.assertEqual([(r["name"], r["amt"]) for r in pay], [("第一岩瀬丸", 68796 + 33831)])
        before = q("SELECT count(*), sum(amount_yen) FROM settle_line")[0]
        for _ in range(5):                                                                     # 何度呼んでも増えない
            self.assertEqual(settle.step(), [])
        self.assertEqual(tuple(q("SELECT count(*), sum(amount_yen) FROM settle_line")[0]), tuple(before))
        self.assertEqual(settle.begin(), q("SELECT id FROM settle_run WHERE kind = 'reconcile'")[0][0])   # もう一度始めても作り直さない

    def test_resume_after_a_stop_does_not_count_twice(self):
        self.start()
        for _ in range(9):   # 照合 8件（4組織 × せりの結果 2行）→ 請求・支払を始めて 1件ずつ
            settle.step()
        c = db.connect("iwase")
        try:
            with c:   # 途中で止まった：状態だけ running のまま・件数の数え直しはまだ
                c.execute("UPDATE settle_run SET done_items = 0 WHERE kind IN ('invoice', 'payout')")
        finally:
            c.close()
        drain()
        self.assertEqual([tuple(r) for r in q("SELECT kind, count(*) FROM settle_line GROUP BY kind ORDER BY kind")], [("invoice", 2), ("payout", 2)])
        self.assertEqual([tuple(r) for r in q("SELECT kind, done_items, total_items, status FROM settle_run ORDER BY id")],
                         [("reconcile", 8, 8, "done"), ("invoice", 2, 2, "done"), ("payout", 2, 2, "done")])

    def test_a_trade_after_the_cutoff_waits_then_is_reflected(self):
        self.start()
        drain()
        time.sleep(1.05)
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{test_trace.RYOTEI} 6本")
        time.sleep(1.05)
        self.decide(a, "s1")   # 社長の確認へ
        p = self.v()["president"]
        time.sleep(1.05)
        from agentdock import server
        server.dispatch("amiya", "/api/decide", dict(case_id=p["case_id"], option_id=p["option_id"], action="return", fingerprint=p["fingerprint"],
                                                    actor="president", reason="魚廣の 4本を先に確保して"), "s2")
        self.run_all()
        rec = next(x for x in self.v()["actions"] if str(x.get("id", "")).startswith("opt") and x.get("recommended"))
        time.sleep(1.05)
        self.assertEqual(self.decide(rec, "s3")["result"], "registered")        # 岩瀬漁協の 6本の相対の取引が成立（初回の対象時点の後）
        st = settle.status()
        self.assertEqual([w["source_kind"] for w in st["waiting"]], ["trade"])   # 追加集計待ち
        self.assertFalse(st["complete"])                                          # 未反映が残る間は完了にしない
        drain()
        st = settle.status()
        self.assertEqual(st["waiting"], [])
        self.assertTrue(st["complete"])
        add = q("SELECT l.kind, l.source_kind, l.gross_yen, l.amount_yen FROM settle_line l JOIN settle_run r ON r.id = l.run_id WHERE r.additional = 1 ORDER BY l.kind")
        self.assertEqual([(r[0], r[1]) for r in add], [("invoice", "trade"), ("payout", "trade")])
        self.assertEqual(add[0][2], 6 * 17500)
        self.assertEqual(q("SELECT party FROM settle_line WHERE kind = 'invoice' AND source_kind = 'trade'")[0][0], "amiya")
        self.assertEqual(q("SELECT c.name FROM settle_line l JOIN consignor c ON c.id = CAST(l.party AS INTEGER) WHERE l.kind = 'payout' AND l.source_kind = 'trade'")[0][0],
                         "第一岩瀬丸")                                                  # 相対の取引も元のロット（S-buri_xl-1）の荷主へ

    def test_a_mismatch_is_recorded_and_stops_invoices_and_payouts(self):
        self.start()
        c = db.connect("buyer_c")
        try:
            with c:   # C社が受け取った結果の値が食い違う
                c.execute("UPDATE auction_result_in SET unit_price_yen = unit_price_yen + 100 WHERE id = (SELECT min(id) FROM auction_result_in)")
        finally:
            c.close()
        drain()
        st = settle.status()
        self.assertEqual({k: j["status"] for k, j in st["jobs"].items()}, {"reconcile": "mismatch", "invoice": "waiting", "payout": "waiting"})
        self.assertFalse(st["complete"])
        self.assertEqual([(r["who"], r["ok"]) for r in q("SELECT who, min(ok) AS ok FROM settle_check GROUP BY who ORDER BY min(id)")],
                         [("iwase", 1), ("amiya", 1), ("buyer_b", 1), ("buyer_c", 0)])   # C社の1行だけ食い違う
        self.assertEqual(q("SELECT count(*) FROM settle_check WHERE ok = 0")[0][0], 1)
        self.assertEqual(q("SELECT count(*) FROM settle_line")[0][0], 0)

    def test_holds_are_not_counted(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()   # 仮押さえ（stock_hold・held）はできている
        self.assertTrue(q("SELECT count(*) FROM stock_hold WHERE status = 'held'")[0][0] > 0)
        drain()
        self.assertEqual(q("SELECT count(*) FROM settle_line WHERE source_kind = 'trade'")[0][0], 0)
        self.assertTrue(settle.status()["complete"])
