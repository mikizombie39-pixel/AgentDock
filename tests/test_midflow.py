"""途中の注文と、登録の後も続く仕事（2026-11-03 デイブの本物の答え：料亭に 6本を登録した後、スーパーの 4本が残ったまま AI が動かなかった）。

  - 登録は出来事になり、網谷水産の AI が残った注文を読んで続ける。案の承認も人の決定として記録し、相談は閉じる
  - 作業の途中で料亭の追加の注文が入る入口（POST /api/order）
  - B社はせりで注文より多く落札した（模擬）。自社の在庫と原価の最低の値で答える
  - Vault の経験を案の根拠に引ける（番号はコードが確かめる）。売り先への連絡は AI が下書きするだけで、送るかは人が決める
"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from agentdock import agents, consult, db, deal, events, network, results, scene, server, work, workspace
from agentdock.companion import MockClient
from agentdock.seed import scenario as sc
from tests import test_buri as tb

B = tb.B
D = tb.D


def run_amiya_on(kind, payload, script):
    c = db.connect("amiya")
    try:
        eid = events.emit(c, kind, payload)
        ev = events.get(c, eid)
    finally:
        c.close()
    cl = MockClient(script)
    agents.run_event(cl, "amiya", ev, D)
    return cl


def approve_split(t):
    tb.no_directive()   # 社長の指示が無い朝：スーパー 4本・料亭 2本の分け方は、社長の確認が要る
    a = next(o for o in tb.view()["options"] if o["code"] == "A")                              # スーパー 4本・料亭 2本
    t.assertEqual(deal.decide("amiya", t.case, a["id"], "approve", a["fingerprint"], "s1")["result"], "needs_president")
    t.assertEqual(deal.decide("amiya", t.case, a["id"], "approve", a["fingerprint"], "p1", actor="president")["result"], "registered")
    return a


class AfterRegistrationTest(tb.Base):
    def test_registration_wakes_the_ai_records_the_approval_and_closes_the_consultation(self):
        a = approve_split(self)
        owner, ev = scene.next_event()
        self.assertEqual((owner, ev["kind"], ev["payload"]["item"], ev["payload"]["case_id"]), ("amiya", "trade_registered", "buri_xl", self.case))
        prompt, item = agents.situation(owner, ev, D)
        self.assertEqual(item, "buri_xl")
        self.assertIn(f"料亭 6本（注文 {self.rest}）のうち 4本", prompt)                       # 残った注文をコードが書く
        rows = tb.q("amiya", "SELECT source, option_id, version, quantity, allocations_json, decided_by FROM human_decision")
        self.assertEqual([r[:4] + (r[5],) for r in rows], [("approval", a["id"], 1, 6, "president")])
        self.assertEqual(json.loads(rows[0][4]), [dict(order_id=self.sup, customer="スーパー", quantity=4), dict(order_id=self.rest, customer="料亭", quantity=2)])
        self.assertEqual(tb.q("amiya", "SELECT status FROM consultation WHERE case_id = ?", (self.case,)), [("closed",)])
        self.assertIsNone(work.current("amiya", "buri_xl")["consultation"] if work.current("amiya", "buri_xl").get("consultation") else None)

    def test_a_board_after_registration_opens_a_new_case_for_what_remains(self):
        approve_split(self)
        q2 = network.send("amiya", "buyer_b", "inquiry", "buri_xl", "ブリを 2本、1本 18,300円でお譲りいただけますか。", D, 2, 18300, side="buy")
        c = db.connect("buyer_b")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([tb.tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=2, unit_price_yen=18400)],
                                                            message="2本なら 1本 18,400円でお譲りします。", reason="せりで多めに仕入れた分です")))]), "buyer_b", ev, D)
        t = network.terms_for_message(q2 + 1)[0]["id"]
        c = db.connect("amiya")
        try:
            r = workspace.build(c, "amiya", "buri_xl", D, dict(item_code="buri_xl", summary="s", headline="h", short_question="B社の 2本を料亭に回しますか",
                                                                ask_human="a", cards=[], recommended="A", options=[
                dict(code="A", label="B社から 2本", short_label="B社から 2本", reason="r", risk="r", buy_term_id=t, differs_from_decision=False, vault_refs=[],
                     allocations=[dict(kind="order", ref_id=self.rest, customer=None, quantity=2)])]), 5, tb.NOW)
        finally:
            c.close()
        self.assertNotEqual(r["case_id"], self.case)                                              # 登録した案件は使い回さない
        self.assertEqual(tb.q("amiya", "SELECT id, status FROM deal_case ORDER BY id"), [(self.case, "registered"), (r["case_id"], "presented")])
        a = next(o for o in tb.view()["options"] if o["code"] == "A")
        self.assertEqual(deal.decide("amiya", r["case_id"], a["id"], "approve", a["fingerprint"], "s2")["result"], "registered")
        self.assertEqual(tb.q("market", "SELECT l.seller, l.seller_kind, t.quantity, t.unit_price_yen FROM trade t JOIN lot l ON l.id = t.lot_id ORDER BY t.id"),
                         [("iwase", "coop", 6, 18000), ("buyer_b", "buyer", 2, 18400)])                 # 仲買どうしの相対
        self.assertEqual(tb.q("buyer_b", "SELECT s.quantity, s.status FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'buri_xl' ORDER BY s.id"),
                         [(2, "sold")])
        self.assertEqual(tb.q("amiya", "SELECT status FROM order_book WHERE id = ?", (self.rest,)), [("partly_filled",)])   # 2本 ＋ 2本（6本の注文）


class MidFlowOrderTest(tb.Base):
    def setUp(self):
        super().setUp()
        self.srv = server.make_server(0)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(timeout=5)
        super().tearDown()

    def post(self, path, obj):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8"), method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_an_additional_order_mid_flow_is_a_business_event(self):
        body = dict(owner="amiya", customer="料亭", item="buri_xl", quantity=3, idempotency_key="demo-1")
        s, r = self.post("/api/order", body)
        self.assertEqual(s, 200)
        self.assertEqual(tb.q("amiya", "SELECT quantity, sell_price_yen FROM order_book WHERE id = ?", (r["order_id"],)), [(3, 24000)])   # 売値は売り先の表から
        self.assertEqual(self.post("/api/order", body)[1]["order_id"], r["order_id"])                    # 押し直しは同じ注文
        evs = tb.q("amiya", "SELECT kind, status, payload_json FROM biz_event WHERE kind = 'order_new' AND status = 'pending'")
        self.assertEqual(len(evs), 1)
        self.assertEqual(json.loads(evs[0][2])["key"], "demo-1")
        owner, ev = scene.next_event()
        prompt, _ = agents.situation(owner, ev, D)
        self.assertIn("今日のこの品目の注文", prompt)                                               # ほかの注文と合わせて足りるかを見る材料
        self.assertIn("スーパー 4本", prompt)
        for bad in (dict(body, customer="誰か", idempotency_key="b1"), dict(body, quantity=0, idempotency_key="b2"), dict(body, item="nori", idempotency_key="b3")):
            self.assertEqual(self.post("/api/order", bad)[0], 400)


class SurplusTest(tb.Base):
    def test_auction_gives_b_more_than_its_order(self):
        rows = [r for r in results.today_results() if r["item"] == "buri_xl"]
        self.assertEqual(sum(r["quantity"] for r in rows), 6)                                         # 相対に残るのは 6本のまま
        self.assertEqual({r["buyer"]: r["quantity"] for r in rows}, {"buyer_b": 4, "other": 2})
        self.assertEqual(tb.q("buyer_b", "SELECT s.quantity, s.status FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'buri_xl'"), [(2, "unallocated")])
        self.assertEqual(tb.q("buyer_c", "SELECT count(*) FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'buri_xl'"), [(0,)])

    def test_b_answers_from_its_stock_above_its_cost(self):
        q2 = network.send("amiya", "buyer_b", "inquiry", "buri_xl", "ブリを 3本、1本 18,000円でお譲りいただけますか。", D, 3, 18000, side="buy")
        c = db.connect("buyer_b")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([
            tb.tools(("read_stock", B)),
            tb.tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=2, unit_price_yen=18000)], message="2本なら 1本 18,000円です。", reason=None))),
            tb.tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=3, unit_price_yen=18400)], message="3本なら 1本 18,400円です。", reason=None))),
            tb.tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=2, unit_price_yen=18400)], message="当社の料亭向けは 21,000円ですが、2本なら 18,400円です。", reason=None))),
            tb.tools(("reply", dict(in_reply_to=q2, kind="offer", terms=[dict(quantity=2, unit_price_yen=18400)], message="2本なら 1本 18,400円でお譲りします。",
                                    reason="せりで多めに仕入れた分です")))]), "buyer_b", ev, D)
        steps = tb.q("buyer_b", "SELECT tool, is_error, result_text FROM agent_step ORDER BY id")
        self.assertEqual([(s[0], s[1]) for s in steps], [("read_stock", 0), ("reply", 1), ("reply", 1), ("reply", 1), ("reply", 0)])
        self.assertIn("最低の値を下回ります", steps[1][2])                                           # 仕入れ値 18,200円を割らない
        self.assertIn("出せるのは 2本", steps[2][2])
        self.assertIn("文に出せない内容", steps[3][2])                                               # 自社の売値は出せない
        self.assertEqual(tb.q("buyer_b", "SELECT quantity, unit_price_yen, buyer, status FROM stock_hold"), [(2, 18400, "amiya", "held")])


class VaultAndDraftTest(tb.Base):
    NOTE = "欠品になりそうな時は、早めにスーパーに伝え、代わりの品を提案する"

    def note_id(self):
        return tb.q("amiya", "SELECT id FROM vault_note WHERE title = ?", (self.NOTE,))[0][0]

    def test_the_practice_note_is_for_the_supermarket_and_not_attributed_to_a_person(self):
        (row,) = tb.q("amiya", "SELECT v.item_id, c.name, v.author, v.source FROM vault_note v LEFT JOIN customer c ON c.id = v.customer_id WHERE v.title = ?", (self.NOTE,))
        self.assertEqual(row, (None, "スーパー", "仲買の慣行（聞き取り・模擬）", "Vault AM-E-006"))   # 出所は Vault の記録の source・識別子は索引の source

    def test_options_cite_vault_notes_by_checked_id(self):
        def present(refs):
            return tb.tools(("present_options", dict(item_code="buri_xl", summary="s", headline="h", short_question="スーパーに早めに伝えますか", ask_human="a", cards=[],
                                                     recommended="A", options=[dict(code="A", label="料亭に 6本", short_label="料亭に 6本", reason="r", risk="r",
                                                                                    buy_term_id=self.t6, differs_from_decision=False, vault_refs=refs,
                                                                                    allocations=[dict(kind="order", ref_id=self.rest, customer=None, quantity=6)])])))
        run_amiya_on("trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]), [present([99999]), present([self.note_id()])])
        self.assertEqual(tb.q("amiya", "SELECT is_error FROM agent_step WHERE tool = 'present_options' ORDER BY id"), [(1,), (0,)])
        a = next(o for o in work.current("amiya", "buri_xl")["options"] if o["code"] == "A")
        self.assertEqual([v["title"] for v in a["grounds"]["vault"]], [self.NOTE])
        self.assertEqual(a["grounds"]["vault"][0]["author"], "仲買の慣行（聞き取り・模擬）")

    def test_a_message_to_a_customer_is_only_drafted_and_the_person_decides(self):
        def draft(sub):
            return tb.tools(("draft_to_customer", dict(customer="スーパー", item_code="buri_xl", substitute_item_code=sub,
                                                       message="本日のブリは 4本そろわない見込みです。代わりにアジはいかがでしょうか。", reason="欠品の連絡は早めに")))
        c = db.connect("amiya")
        try:
            with c:   # 代わりの品のアジを手元の在庫に置く（在庫の裏付けのある代わりの品だけ書ける）
                c.execute("INSERT INTO stock (item_id, quantity, status, stock_date, updated_at) SELECT id, 3, 'for_sale', ?, ? FROM item WHERE code = 'aji'",
                          (D, db.now_iso()))
        finally:
            c.close()
        run_amiya_on("trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]), [draft("nori"), draft("aji"), tb.tools(("wait_for_replies", dict(reason="r")))])
        self.assertEqual(tb.q("amiya", "SELECT is_error FROM agent_step WHERE tool = 'draft_to_customer' ORDER BY id"), [(1,), (0,)])
        (d,) = tb.q("amiya", "SELECT c.name, i.code, s.code, d.status FROM customer_draft d JOIN customer c ON c.id = d.customer_id JOIN item i ON i.id = d.item_id "
                             "LEFT JOIN item s ON s.id = d.substitute_item_id")
        self.assertEqual(d, ("スーパー", "buri_xl", "aji", "draft"))
        self.assertEqual(tb.q("market", "SELECT count(*) FROM network_message WHERE body LIKE '%アジはいかが%'"), [(0,)])   # 何も送らない
        cur = work.current("amiya", "buri_xl")
        (dr,) = cur["drafts"]
        self.assertEqual((dr["customer"], dr["substitute"], dr["status"]), ("スーパー", "アジ", "draft"))
        c = db.connect("amiya")
        try:
            self.assertEqual(consult.decide_draft(c, dr["draft_id"], "approve", "staff")["status"], "approved")
            self.assertEqual(consult.decide_draft(c, dr["draft_id"], "approve", "staff")["repeated"], True)
        finally:
            c.close()

    def test_a_substitute_without_stock_cannot_be_offered(self):
        msg = "本日のブリは 4本そろわない見込みです。代わりの魚をお探ししてよろしいでしょうか。"
        run_amiya_on("trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]), [
            tb.tools(("draft_to_customer", dict(customer="スーパー", item_code="buri_xl", substitute_item_code="aji", message="代わりにアジをご用意できます。", reason="r"))),
            tb.tools(("draft_to_customer", dict(customer="スーパー", item_code="buri_xl", substitute_item_code=None, message=msg, reason="r"))),
            tb.tools(("wait_for_replies", dict(reason="r")))])
        steps = tb.q("amiya", "SELECT is_error, result_text FROM agent_step WHERE tool = 'draft_to_customer' ORDER BY id")
        self.assertEqual([s[0] for s in steps], [1, 0])
        self.assertIn("手元の在庫にありません", steps[0][1])
        self.assertEqual(tb.q("amiya", "SELECT substitute_item_id, body FROM customer_draft"), [(None, msg)])


class InquiringLineTest(tb.Base):
    def test_a_short_order_with_an_open_inquiry_says_so(self):
        approve_split(self)
        network.send("amiya", "buyer_b", "inquiry", "buri_xl", "ブリを 4本、お譲りいただけますか。", D, 4, 18300, side="buy")
        lines = [l["text"] for l in work.current("amiya", "buri_xl")["lines"]]
        self.assertEqual(lines, ["魚廣の注文 4本を確保しています（登録済み 4本）",
                                 "順風満帆の注文 6本のうち 2本を確保しています（登録済み 2本）。4本 足りません（B社に問い合わせ中）"])


class NoNameTest(unittest.TestCase):
    def test_the_source_of_the_practice_is_not_named(self):
        from pathlib import Path
        bad = chr(0x5E83) + chr(0x5CF6)   # 書いてはいけない名前（コードにも、コンパイルした定数にも文字で残さない）
        hits = [str(p) for p in Path(db.REPO).rglob("*") if p.is_file() and ".git" not in p.parts and p.suffix in (".py", ".js", ".html", ".css", ".sql", ".md", ".txt", ".json")
                and bad in p.read_text(encoding="utf-8", errors="ignore")]
        self.assertEqual(hits, [])
