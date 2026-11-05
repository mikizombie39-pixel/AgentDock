"""一つの取引の証拠のたどり（技術の検証の画面の1節・2026-11-05 SAL の方針）の試験。模擬の AI で撮影の分岐を通す（本物の Claude は呼ばない）。
取引 → 案件の決定 → 案と根拠 → Vault のファイル → Claude の起動 → 相手とのメッセージ → 市場の帳簿 → 在庫と注文、の順に、記録の行で並ぶこと。
"""
import hashlib
import json
import time

from agentdock import db, stage, trace, vault, workspace
from tests.test_mate import RYOTEI, SUPER, Base


class TraceTest(Base):
    def started(self):
        c = db.connect("iwase")
        try:
            return c.execute("SELECT count(*) FROM broadcast").fetchone()[0] > 0
        finally:
            c.close()

    def register_after_return(self):
        if not self.started():
            self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{RYOTEI} 6本")
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "t1")["result"], "needs_president")
        p = self.v()["president"]
        time.sleep(1.05)
        from agentdock import server
        server.dispatch("amiya", "/api/decide", dict(case_id=p["case_id"], option_id=p["option_id"], action="return",
                                                    fingerprint=p["fingerprint"], actor="president", reason="魚廣の 4本を先に確保して"), "t2")
        self.assertIn("社長が差し戻しました", [i["text"] for i in stage.flow()["items"]])   # 差し戻しの後も流れの口が落ちない（2026-11-05 の不具合）
        self.run_all()
        rec = next(x for x in self.v()["actions"] if str(x.get("id", "")).startswith("opt") and x.get("recommended"))
        time.sleep(1.05)
        self.assertEqual(self.decide(rec, "t3")["result"], "registered")
        self.run_all()   # 登録の後の起動（照会し直し・断り・下書き）は、たどりに入れない
        return p["case_id"]

    def test_no_registered_trade_yet(self):
        self.assertEqual(trace.trace("amiya")["rows"], [])

    def test_one_line_from_the_trade_to_the_stock(self):
        case = self.register_after_return()
        t = trace.trace("amiya")
        self.assertEqual(t["case_id"], case)
        steps = [r["step"] for r in t["rows"]]
        self.assertEqual(steps, sorted(steps))                                          # 1〜8 の順に並ぶ
        self.assertEqual(sorted(set(steps)), [1, 2, 3, 4, 5, 6, 7, 8])
        rows = {s: [r for r in t["rows"] if r["step"] == s] for s in range(1, 9)}
        # 1 取引：岩瀬漁協 6本 × 17,500円・B社 2本 × 18,400円、合計 141,800円はコードの計算
        self.assertEqual([r["title"] for r in rows[1]], ["岩瀬漁協から 6本 を仕入れ（1本 17,500円）", "B社から 2本 を仕入れ（1本 18,400円）",
                                                       "仕入れの合計 141,800円（8本）"])
        self.assertEqual([(r["kind"], r["origin"]) for r in rows[1]], [("fact", "record"), ("fact", "record"), ("calc", "code")])
        # 2 案件の決定：担当者の承認（社長の確認へ）→ 社長の差し戻し（理由）→ 担当者の承認（登録）
        self.assertEqual([r["title"] for r in rows[2]], ["担当者が承認", "社長（網谷さん）が差し戻し", "担当者が承認"])
        self.assertIn("理由：魚廣の 4本を先に確保して", rows[2][1]["lines"])
        self.assertTrue(all((r["kind"], r["origin"]) == ("human", "person") for r in rows[2]))
        # 3 案：AI が書いた文（模擬の AI）と、コードが計算した数（売り先は店の名前）
        self.assertEqual((rows[3][0]["kind"], rows[3][0]["origin"]), ("ai", "mock"))
        self.assertIn(f"売り先：{SUPER} 4本・{RYOTEI} 4本", rows[3][1]["lines"])
        # 4 模擬の AI は根拠の Vault の番号を付けない
        self.assertEqual([r["title"] for r in rows[4]], ["案は、根拠の Vault の番号を付けていません"])
        # 5 起動は登録の出来事より前だけ
        self.assertTrue(rows[5] and all(r["origin"] == "mock" for r in rows[5]))
        self.assertNotIn("trade_registered", " ".join(r["title"] for r in rows[5]))
        self.assertTrue(all(r["steps"] for r in rows[5]))
        # 6 相手とのやり取り：問い合わせと、条件の返事（返事を書いた相手の AI の起動の種類）
        self.assertEqual([r["title"] for r in rows[6]], ["網谷水産 → 岩瀬漁協：問い合わせ（4本）", "岩瀬漁協 → 網谷水産：条件の返事",
                                                       "網谷水産 → B社：問い合わせ（2本）", "B社 → 網谷水産：条件の返事"])
        self.assertTrue(all(r["origin"] == "mock" for r in rows[6]))
        # 7 市場の帳簿：両方の帳簿にある
        self.assertEqual([r["booked"] for r in rows[7]], [True, True])
        # 8 在庫と注文：魚廣 4本（充足）・順風満帆 2本＋2本（一部充足）・売り手の仮押さえは取引で確定
        self.assertEqual([r["title"] for r in rows[8]], [f"在庫 4本 を {SUPER}の注文（4本）に引当", f"在庫 2本 を {RYOTEI}の注文（6本）に引当",
                                                       f"在庫 2本 を {RYOTEI}の注文（6本）に引当", "岩瀬漁協の仮押さえ 6本：取引で確定",
                                                       "B社の仮押さえ 2本：取引で確定"])
        self.assertEqual(t["ai_origins"], ["模擬の AI"])
        # 文の中に区分の名前（スーパー・料亭）を出さない
        text = json.dumps([(r["title"], r["lines"]) for r in t["rows"]], ensure_ascii=False)
        self.assertNotIn("スーパー", text)
        self.assertNotIn("料亭", text)

    def test_vault_refs_show_the_file_and_its_fingerprint(self):
        case = self.register_after_return()
        c = db.connect("amiya")
        try:
            vid, vault_id, cat = c.execute("SELECT id, vault_id, category FROM vault_note WHERE vault_id = 'AM-E-006'").fetchone()
            opt = c.execute("SELECT option_id FROM case_decision WHERE case_id = ? AND result = 'registered'", (case,)).fetchone()[0]
            code, ver = c.execute("SELECT code, version FROM board_option WHERE id = ?", (opt,)).fetchone()
            tid, layout = c.execute("SELECT id, layout_json FROM workspace_turn WHERE case_id = ? AND version = ? AND source = 'present' ORDER BY id DESC LIMIT 1",
                                    (case, ver)).fetchone()
            lay = json.loads(layout)
            lay["vault_refs"] = {code: [vid]}   # 本物の Claude は案に根拠の Vault の番号を付ける：その形を記録に置く
            with c:
                c.execute("UPDATE workspace_turn SET layout_json = ? WHERE id = ?", (json.dumps(lay, ensure_ascii=False), tid))
        finally:
            c.close()
        rows = [r for r in trace.trace("amiya")["rows"] if r["step"] in (3, 4)]
        self.assertIn(f"根拠の Vault：{vid}（番号はコードが Vault にあることを確かめた）", rows[0]["lines"])
        (v,) = [r for r in rows if r["step"] == 4]
        digest = hashlib.sha256((vault.root() / "amiya" / cat / f"{vault_id}.md").read_bytes()).hexdigest()[:12]
        self.assertEqual(v["title"], "AM-E-006「欠品になりそうな時は、早めに魚廣に伝え、代わりの品を提案する」")
        self.assertIn(f"照合番号（SHA-256 の先頭 12桁）：{digest}", v["lines"])
        self.assertEqual((v["kind"], v["origin"]), ("experience", "record"))


class StaleRunTest(TraceTest):
    """起動が始まった後に人が決めた時は、その起動は案を出せない（2026-11-05 本物の通しの撮り直し：差し戻しを知らない起動の案が
    「組み直した案」として出て、担当者がそれを承認した）。決定を受けた起動が出し直す。"""
    test_no_registered_trade_yet = None
    test_one_line_from_the_trade_to_the_stock = None
    test_vault_refs_show_the_file_and_its_fingerprint = None

    def test_a_run_that_started_before_a_decision_cannot_present(self):
        self.register_after_return()
        c = db.connect("amiya")
        try:
            with c:
                rid = c.execute("INSERT INTO agent_run (role, event_id, started_at, outcome, model, run_kind) VALUES ('buyer', NULL, ?, NULL, 'm', 'mock')",
                                ("2026-11-05T00:00:00+09:00",)).lastrowid
            with self.assertRaises(workspace.BoardError) as e:
                workspace.build(c, "amiya", "buri_xl", "2026-11-02", dict(options=[]), rid, "2026-11-02T06:00:00+09:00")
        finally:
            c.close()
        msg = " ".join(e.exception.args[0]) if isinstance(e.exception.args[0], list) else str(e.exception)
        self.assertIn("この起動が始まった後に", msg)
        self.assertIn("社長の差し戻し", msg)
        self.assertIn("do_nothing", msg)


class SupersededTermTest(TraceTest):
    """相手の新しい返事で前の仮押さえが置き換わったら、古い版の案は選べない（2026-11-05 本物の通しの3回目：担当者が古い条件の案を承認し、stale になった）。"""
    test_no_registered_trade_yet = None
    test_one_line_from_the_trade_to_the_stock = None
    test_vault_refs_show_the_file_and_its_fingerprint = None

    def test_an_option_on_a_replaced_term_is_not_executable(self):
        from agentdock import work
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        opts = work.current("amiya", "buri_xl")["options"]
        a = next(o for o in opts if o["action"] == "approve" and o["executable"])
        c = db.connect("amiya")
        try:
            plan = json.loads(c.execute("SELECT plan_json FROM board_option WHERE id = ?", (a["option_id"],)).fetchone()[0])
        finally:
            c.close()
        tid = (plan.get("buy_term_ids") or [plan["buy_term_id"]])[0]
        m = db.connect(db.MARKET)
        try:
            with m:   # 相手の新しい返事が、前の条件に代わった
                m.execute("UPDATE offer_term SET status = 'released' WHERE id = ?", (tid,))
        finally:
            m.close()
        b = next(o for o in work.current("amiya", "buri_xl")["options"] if o["option_id"] == a["option_id"])
        self.assertFalse(b["executable"])
        self.assertTrue(any(f"条件 {tid} は相手の新しい返事に置き換わりました" in e for e in b["numbers"]["errors"]))
        act = next(x for x in self.v()["actions"] if x.get("id") == f"opt{a['option_id']}")
        self.assertTrue(act.get("disabled"))                                       # 仲間の画面でも押せない
