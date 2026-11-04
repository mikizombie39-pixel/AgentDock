"""売り先の当てのない在庫を仕事にする試験（模擬の Claude で回す。本物の Claude は呼ばない）。

2026-11-04 デイブ：本物の実行（take 20261103-231116-live）で、担当者が「6本・スーパー 4本と料亭 2本」の案を登録した。
料亭には注文が無く、登録の後に起きた AI は「満たしていない注文は無い」として何もしなかった。網谷水産の在庫は
4本がスーパーに引当、2本が引当なし。ここでは同じ形を作り、次を確かめる。

  - B社が余りを譲る最低の値 ＝ せりの仕入れ値 18,200円 ＋ 運搬 200円 ＝ 18,400円（スーパー向けの上限 18,500円の内側）
  - 引当のない在庫が、AI に渡す状況と今の状態の行に出る
  - AI が扱いを人に問い、人の答えが出来事になって AI を起こす
  - 料亭の追加注文が後から入った時、AI は手元の 2本を先に充て、足りない 4本だけ問い合わせる（数量と可否はコードが確かめる）
  - 記録の再生：ある時刻の今の状態を記録から組み直す
"""
import datetime
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from agentdock import agents, db, deal, disclosure, events, network, scene, server, stage, stockwork, work, workspace
from agentdock.companion import MockClient
from agentdock.seed import build
from agentdock.seed import scenario as sc
from tests import test_buri as tb

D = sc.DEMO_DATE
NOW = tb.NOW
B = tb.B
tools, q, ex, settle = tb.tools, tb.q, tb.ex, tb.settle


def run_owner(owner, kind, script):
    """待ちの出来事のうち kind の最初の1件を、模擬の Claude で処理する。"""
    c = db.connect(owner)
    try:
        ev = events.pending(c, [kind])[0]
    finally:
        c.close()
    cl = MockClient(script)
    r = agents.run_event(cl, owner, ev, D)
    return r, cl


class FakeClock:
    """記録の時刻を1回ごとに1秒進める（記録の再生は秒で比べるので、試験の中の出来事を別々の秒に置く）。"""

    def setUp(self):
        n = [0]

        def tick(spec):
            n[0] += 1
            return (datetime.datetime(2026, 11, 4, 9, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
                    + datetime.timedelta(seconds=n[0])).isoformat(timespec=spec)
        self._patches = [mock.patch.object(db, "now_iso", lambda: tick("seconds")), mock.patch.object(events, "_now", lambda: tick("microseconds"))]
        for p in self._patches:
            p.start()
        super().setUp()

    def tearDown(self):
        super().tearDown()
        for p in self._patches:
            p.stop()


def steps(owner):
    return q(owner, "SELECT tool, is_error, result_text FROM agent_step WHERE run_id = (SELECT max(id) FROM agent_run) ORDER BY seq")


class ResaleFloorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_floor_is_purchase_plus_named_costs_inside_the_ceiling(self):
        self.assertEqual(q("buyer_b", "SELECT r.name, r.cost_yen_per_unit FROM resale_cost r JOIN item i ON i.id = r.item_id WHERE i.code = 'buri_xl'"),
                         [("運搬（受け渡し先まで）", 200)])
        self.assertEqual(q("buyer_b", "SELECT s.quantity, s.status, s.unit_cost_yen FROM stock s JOIN item i ON i.id = s.item_id WHERE i.code = 'buri_xl'"),
                         [(2, "unallocated", 18200)])                                   # 仕入れ値はせりの落札値
        self.assertEqual(q("buyer_b", "SELECT count(*) FROM quote_rule"), [(0,)])       # 固定の値ではなく、仕入れ値と費用から計算する
        c = db.connect("buyer_b")
        try:
            it = c.execute("SELECT id FROM item WHERE code = 'buri_xl'").fetchone()[0]
            rf = stockwork.resale_floor(c, it)
            self.assertEqual(rf, dict(floor_yen=18400, purchase_unit_yen=18200, costs=[dict(name="運搬（受け渡し先まで）", yen=200)]))
            self.assertIn("amount 18400 (resale_floor)", disclosure.check(c, "buri_xl", 18500, 2, "最低でも 18,400円です。", D))
        finally:
            c.close()
        a = db.connect("amiya")
        try:
            ceiling = min(agents.criteria.ceiling_yen(a, "buri_xl", n) for n in ("スーパー", "料亭"))
        finally:
            a.close()
        self.assertEqual(ceiling, 18500)
        self.assertLessEqual(rf["floor_yen"], ceiling)                                  # 網谷水産が買える値

    def test_b_cannot_reply_below_purchase_plus_cost(self):
        scene.start(item="buri_xl")
        settle()
        qid = network.send("amiya", "buyer_b", "inquiry", "buri_xl", "ブリを 2本、1本 18,300円でお譲りいただけますか。", D, 2, 18300, side="buy")
        run_owner("buyer_b", "quote_request", [
            tools(("read_stock", B)),
            tools(("reply", dict(in_reply_to=qid, kind="offer", terms=[dict(quantity=2, unit_price_yen=18300)], message="2本なら 1本 18,300円です。", reason=None))),
            tools(("reply", dict(in_reply_to=qid, kind="offer", terms=[dict(quantity=2, unit_price_yen=18400)], message="2本なら 1本 18,400円です。", reason=None)))])
        s = steps("buyer_b")
        self.assertEqual([(x[0], x[1]) for x in s], [("read_stock", 0), ("reply", 1), ("reply", 0)])
        self.assertEqual(json.loads(s[0][2])["resale_floor"]["costs"], [dict(name="運搬（受け渡し先まで）", yen=200)])
        self.assertIn("最低は 18400円", s[1][2])                                         # 18,200円（仕入れ値だけ）ではない


class SpareBase(unittest.TestCase):
    """本物の 2回目の実行と同じ形：始まりはスーパー 4本だけ。漁協が 6本 17,500円で押さえ、担当者がスーパー 4本・料亭 2本（注文なし）で登録する。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.sup = scene.start(item="buri_xl")["amiya_order"][0]
        settle()
        q1 = network.send("amiya", "iwase", "inquiry", "buri_xl", "ブリを 6本、1本 17,500円でいかがでしょうか。", D, 6, 17500, side="buy")
        run_owner("iwase", "quote_request", [tools(("reply", dict(in_reply_to=q1, kind="offer", terms=[dict(quantity=6, unit_price_yen=17500)],
                                                                  message="6本なら 1本 17,500円でお出しします。", reason="相対の残りは 6本です")))])
        self.t6 = network.terms_for_message(q1 + 1)[0]["id"]
        settle()
        c = db.connect("amiya")
        try:
            self.ryotei = c.execute("SELECT id FROM customer WHERE name = '料亭'").fetchone()[0]
            r = workspace.build(c, "amiya", "buri_xl", D, dict(
                item_code="buri_xl", summary="s", headline="h", short_question="6本仕入れますか", ask_human="a", cards=[], recommended="B", options=[
                    dict(code="B", label="6本・スーパー 4本と料亭 2本", short_label="スーパー4・料亭2", reason="r", risk="料亭には注文が無い",
                         buy_term_id=self.t6, allocations=[dict(kind="order", ref_id=self.sup, customer=None, quantity=4),
                                                           dict(kind="customer_lead", ref_id=None, customer="料亭", quantity=2)])]), 1, NOW)
        finally:
            c.close()
        self.case = r["case_id"]
        b = next(o for o in tb.view()["options"] if o["code"] == "B")
        self.assertEqual(deal.decide("amiya", self.case, b["id"], "approve", b["fingerprint"], "s1")["result"], "registered")   # 担当者だけで登録
        self.registered_at = q("amiya", "SELECT max(at) FROM case_decision")[0][0]

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def stock(self):
        return q("amiya", "SELECT s.quantity, s.status, s.order_id, c.name, s.unit_cost_yen, s.allocated_by_run IS NOT NULL FROM stock s "
                          "LEFT JOIN customer c ON c.id = s.intended_customer_id ORDER BY s.id")

    def ask_script(self, choices=("confirm_customer", "offer_to_buyers", "keep")):
        return [tools(("read_orders", B)),
                tools(("ask_about_stock", dict(item_code="buri_xl", question="料亭の 2本をどうしますか", choices=list(choices),
                                               recommended="confirm_customer", customer=None, reason="料亭にはまだ注文がありません")))]

    def add_ryotei_order(self, qty=6):
        c = db.connect("amiya")
        try:
            return events.order_received(c, "料亭", "buri_xl", qty, 24000, D)[0]
        finally:
            c.close()


class SpareAfterRegistrationTest(SpareBase):
    def test_registration_keeps_the_intended_customer_and_cost(self):
        self.assertEqual(self.stock(), [(4, "allocated", self.sup, None, 17500, 0), (2, "unallocated", None, "料亭", 17500, 0)])
        owner, ev = scene.next_event()
        prompt, _ = agents.situation(owner, ev, D)
        self.assertIn("満たしていない注文はありません", prompt)
        self.assertIn("引当のない在庫（売り先の当てのない数量）：2本（人が承認した案で料亭に回すと決めた分・料亭の注文はまだ無い）", prompt)
        self.assertIn("ask_about_stock", prompt)
        cur = work.current("amiya", "buri_xl")
        self.assertEqual([s["text"] for s in cur["stock"]], ["引当のない在庫 2本：料亭に回すつもりで仕入れました（料亭の注文はまだありません）"])
        self.assertEqual([l["text"] for l in cur["lines"]], ["スーパーの注文 4本を確保しています（登録済み 4本）"])

    def test_the_ai_asks_and_the_answer_wakes_it(self):
        r, _ = run_owner("amiya", "trade_registered", self.ask_script())
        self.assertEqual(r["outcome"], "waiting")
        sq = work.current("amiya", "buri_xl")["stock_question"]
        self.assertEqual((sq["text"], sq["quantity"], sq["recommended"]), ("料亭の 2本をどうしますか", 2, "confirm_customer"))
        self.assertEqual([c["label"] for c in sq["choices"]], ["料亭に注文を確かめる", "ほかの仲買に譲る", "そのまま持つ"])   # 名前はコードが書く
        c = db.connect("amiya")
        try:
            with self.assertRaises(stockwork.StockError):
                stockwork.answer(c, sq["question_id"], "sell_all", "k0")                 # 問いに無い選択肢
            a = stockwork.answer(c, sq["question_id"], "confirm_customer", "k1")
            self.assertEqual(stockwork.answer(c, sq["question_id"], "confirm_customer", "k1")["repeated"], True)
            with self.assertRaises(stockwork.StockError):
                stockwork.answer(c, sq["question_id"], "keep", "k2")                     # 答えた問いには答え直せない
        finally:
            c.close()
        cur = work.current("amiya", "buri_xl")
        self.assertIsNone(cur["stock_question"])
        self.assertEqual(cur["stock_decision"]["text"], "担当者の決定：引当のない在庫は「料亭に注文を確かめる」")
        self.assertEqual([(x["kind"], x["source"]) for x in cur["ai_queue"]], [("human_choice", "stock_question")])   # 画面の「AIの番です」の文に使う
        owner, ev = scene.next_event()
        self.assertEqual((owner, ev["id"], ev["kind"], ev["payload"]["source"]), ("amiya", a["event_id"], "human_choice", "stock_question"))
        prompt, item = agents.situation(owner, ev, D)
        self.assertEqual(item, "buri_xl")
        self.assertIn("担当者が引当のない在庫の扱いを答えました", prompt)
        self.assertIn("料亭への連絡（注文があるかを確かめる文）を draft_to_customer で下書き", prompt)
        self.assertIn("担当者が引当のない在庫の扱いを答えました（料亭に注文を確かめる）", [i["text"] for i in stage.flow("amiya")["items"]])

    def test_offering_to_other_buyers_needs_the_human_answer(self):
        def post(price):
            return tools(("post_request", dict(side="sell", item_code="buri_xl", quantity=2, min_qty=2, unit_price_yen=price, message="ブリを 2本お譲りします。")))
        run_owner("amiya", "trade_registered", [post(18000), *self.ask_script()])
        s = steps("amiya")
        self.assertEqual([(x[0], x[1]) for x in s], [("post_request", 1), ("read_orders", 0), ("ask_about_stock", 0)])
        self.assertIn("担当者が決めます", s[0][2])                                         # 人が答える前は譲れない
        qid = work.current("amiya", "buri_xl")["stock_question"]["question_id"]
        c = db.connect("amiya")
        try:
            stockwork.answer(c, qid, "offer_to_buyers", "k1")
        finally:
            c.close()
        run_owner("amiya", "human_choice", [post(17400), post(17500)])
        s = steps("amiya")
        self.assertEqual([(x[0], x[1]) for x in s], [("post_request", 1), ("post_request", 0)])
        self.assertIn("最低は 17500円", s[0][2])                                          # 仕入れ値を割って譲らない
        self.assertEqual(q("market", "SELECT owner, side, quantity, unit_price_yen FROM deal_request WHERE owner = 'amiya'"), [("amiya", "sell", 2, 17500)])


class SpareAllocationTest(SpareBase):
    def test_a_later_order_takes_the_spare_first_and_asks_only_for_the_rest(self):
        run_owner("amiya", "trade_registered", self.ask_script())                      # 扱いの問いが開いている時に注文が入る
        rest = self.add_ryotei_order(6)
        owner, ev = scene.next_event()
        prompt, _ = agents.situation(owner, ev, D)
        self.assertIn("この注文には、問い合わせる前に allocate_stock で手元の在庫を充て", prompt)
        r, cl = run_owner("amiya", "order_new", [
            tools(("read_orders", B)),
            tools(("allocate_stock", dict(item_code="buri_xl", order_id=rest, quantity=6, reason="料亭の注文"))),
            tools(("allocate_stock", dict(item_code="buri_xl", order_id=self.sup, quantity=2, reason=None))),
            tools(("allocate_stock", dict(item_code="buri_xl", order_id=rest, quantity=2, reason="料亭に回すと決めた 2本"))),
            tools(("send_inquiry", dict(to_owner="buyer_b", side="buy", item_code="buri_xl", quantity=4, unit_price_yen=18400, request_id=None,
                                        message="ブリを 4本、1本 18,400円でお譲りいただけますか。", reason="注文の残りの分"))),
            tools(("wait_for_replies", dict(reason="B社の返事を待つ")))])
        s = steps("amiya")
        self.assertEqual([(x[0], x[1]) for x in s], [("read_orders", 0), ("allocate_stock", 1), ("allocate_stock", 1), ("allocate_stock", 0),
                                                     ("send_inquiry", 0), ("wait_for_replies", 0)], s)
        ro = json.loads(s[0][2])
        self.assertEqual(ro["unallocated_stock"], [dict(quantity=2, unit="本", intended_customer="料亭", unit_cost_yen=17500)])
        self.assertIn("充てられる引当のない在庫は 2 です", s[1][2])                         # 6本は充てられない
        self.assertIn("今日の開いている注文ではありません", s[2][2])                       # スーパーは満たし済み
        self.assertIn("この注文の残りは 4本", s[3][2])
        self.assertEqual(self.stock(), [(4, "allocated", self.sup, None, 17500, 0), (2, "allocated", rest, "料亭", 17500, 1)])
        self.assertEqual(q("amiya", "SELECT status FROM order_book WHERE id = ?", (rest,)), [("partly_filled",)])
        self.assertEqual(q("market", "SELECT to_owner, quantity FROM network_message WHERE kind = 'inquiry' ORDER BY id DESC LIMIT 1"), [("buyer_b", 4)])
        self.assertEqual(q("amiya", "SELECT status FROM stock_question"), [("closed",)])    # 問いは状況が変わって閉じる
        cur = work.current("amiya", "buri_xl")
        self.assertEqual(cur["stock"], [])
        self.assertEqual([l["text"] for l in cur["lines"]],
                         ["スーパーの注文 4本を確保しています（登録済み 4本）",
                          "料亭の注文 6本のうち 2本を確保しています（在庫から 2本）。4本 足りません（B社に問い合わせ中）"])
        kinds = {i["kind"] for i in work.view("amiya", "buri_xl")["items"]}
        self.assertTrue({"stock_left", "stock_question", "stock_alloc"} <= kinds)

    def test_the_board_counts_what_the_spare_already_filled(self):
        rest = self.add_ryotei_order(6)
        settle()
        c = db.connect("amiya")
        try:
            stockwork.allocate(c, "buri_xl", rest, 2, None, D, NOW)
            n6 = workspace.compute(c, "amiya", "buri_xl", dict(buy_term_id=None, allocations=[dict(kind="order", ref_id=rest, quantity=6)]), NOW)
            n4 = workspace.compute(c, "amiya", "buri_xl", dict(buy_term_id=None, allocations=[dict(kind="order", ref_id=rest, quantity=4)]), NOW)
        finally:
            c.close()
        self.assertIn(f"注文 {rest} の残り 4（2 は充当済み）を超えています", n6["errors"])
        self.assertFalse(any("残り" in e for e in n4["errors"]))

    def test_stock_for_one_customer_is_not_moved_to_another_by_the_ai(self):
        c = db.connect("amiya")
        try:
            sup2 = events.order_received(c, "スーパー", "buri_xl", 2, 22000, D)[0]
            with self.assertRaises(stockwork.StockError) as e:
                stockwork.allocate(c, "buri_xl", sup2, 2, None, D, NOW)
        finally:
            c.close()
        self.assertIn("料亭に回すと決めた在庫 2 は、料亭の注文にだけ充てられます", str(e.exception))


class ReplayTest(FakeClock, SpareBase):
    def test_current_can_be_rebuilt_for_a_past_time(self):
        rest = self.add_ryotei_order(6)
        settle()
        c = db.connect("amiya")
        try:
            ex("amiya", "UPDATE stock SET unallocated_at = '2026-11-04T10:00:00+09:00', updated_at = '2026-11-04T10:00:00+09:00' WHERE status = 'unallocated'")
            ex("amiya", "UPDATE order_book SET received_at = '2026-11-04T10:05:00+09:00' WHERE id = ?", (rest,))
            stockwork.allocate(c, "buri_xl", rest, 2, None, D, NOW)
            ex("amiya", "UPDATE stock SET updated_at = '2026-11-04T10:10:00+09:00' WHERE order_id = ?", (rest,))
        finally:
            c.close()
        before = work.current("amiya", "buri_xl", at="2026-11-04T10:06:00+09:00")
        self.assertEqual([s["text"] for s in before["stock"]], ["引当のない在庫 2本：料亭に回すつもりで仕入れました（料亭の注文に、まだ充てていません）"])
        self.assertEqual([l["text"] for l in before["lines"]][1], "料亭の 6本は、まだそろっていません")
        after = work.current("amiya", "buri_xl", at="2026-11-04T10:11:00+09:00")
        self.assertEqual(after["stock"], [])
        self.assertIn("在庫から 2本", after["lines"][1]["text"])
        now = work.current("amiya", "buri_xl")
        self.assertEqual((after["lines"], after["stock"]), (now["lines"], now["stock"]))

    def test_timeline_ends_at_the_current_state(self):
        tl = work.timeline("amiya", "buri_xl")["snapshots"]
        self.assertGreater(len(tl), 2)
        now = work.current("amiya", "buri_xl")
        for k in ("lines", "stock", "waiting_for", "case", "decision", "drafts"):
            self.assertEqual(tl[-1]["current"][k], now[k], k)
        first_case = next(s["current"]["case"] for s in tl if s["current"]["case"])
        self.assertEqual(first_case["status"], "presented")                                  # 登録の前は判断を待っていた
        self.assertTrue(any(s["current"]["waiting_for"] == "staff" for s in tl))


class ReplayOfARenegotiationTest(FakeClock, tb.Base):
    def test_the_president_wait_is_seen_in_the_replay(self):
        r = tb.answer(self.cons, "staff-1", self.rest6())                                     # 担当者：料亭 6本（優先の変更）→ 社長待ち
        self.assertEqual(r["result"], "needs_president")
        mid = q("amiya", "SELECT max(at) FROM audit_event")[0][0]
        tb.answer(self.cons, "president-1", self.rest6(), role="president")
        tl = work.timeline("amiya", "buri_xl")["snapshots"]
        states = [(s["current"]["waiting_for"], (s["current"].get("pending") or {}).get("kind")) for s in tl]
        self.assertIn(("president", "consultation"), states)
        self.assertEqual(work.current("amiya", "buri_xl", at=mid)["waiting_for"], "president")
        self.assertEqual(tl[-1]["current"]["waiting_for"], work.current("amiya", "buri_xl")["waiting_for"])


class ReplayCopyTest(unittest.TestCase):
    def test_the_take_is_copied_and_only_the_copy_is_migrated(self):
        import hashlib
        import sqlite3
        from pathlib import Path
        from agentdock import replay
        with tempfile.TemporaryDirectory() as take, tempfile.TemporaryDirectory() as base:
            for code in (*db.OWNER_CODES, db.MARKET):                  # 移行 014 の前の撮影の形（013 までの表）
                kind = "market" if code == db.MARKET else "owner"
                con = sqlite3.connect(Path(take) / f"{code}.sqlite")
                con.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT")
                for f in sorted((db.MIGRATIONS / kind).glob("*.sql")):
                    v = int(f.name.split("_", 1)[0])
                    if kind == "owner" and v >= 14:
                        continue
                    con.executescript(f.read_text(encoding="utf-8"))
                    con.execute("INSERT INTO schema_version VALUES (?, 'x')", (v,))
                con.commit()
                con.close()
            digest = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(take).glob("*.sqlite")}
            dst = replay.copy_take(Path(take), Path(base))
            self.assertEqual({f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in Path(take).glob("*.sqlite")}, digest)   # 記録は書き換えない
            con = sqlite3.connect(dst / "amiya.sqlite")
            self.assertEqual(con.execute("SELECT count(*) FROM stock_question").fetchone()[0], 0)
            con.close()
            with self.assertRaises(ValueError):
                replay.copy_take(Path(take), Path(base) / "OneDrive")


class StockAnswerHttpTest(FakeClock, SpareBase):
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

    def call(self, path, obj=None):
        req = urllib.request.Request(self.base + path, data=json.dumps(obj).encode("utf-8") if obj is not None else None,
                                     method="POST" if obj is not None else "GET", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_answer_and_timeline_over_http(self):
        run_owner("amiya", "trade_registered", self.ask_script(("confirm_customer", "keep")))
        s, cur = self.call("/api/current?owner=amiya&item=buri_xl")
        qid = cur["stock_question"]["question_id"]
        body = dict(owner="amiya", question_id=qid, choice="keep", idempotency_key="h1", role="staff")
        self.assertEqual(self.call("/api/stock_answer", dict(body, choice="offer_to_buyers", idempotency_key="h0"))[0], 409)   # 出していない選択肢
        self.assertEqual(self.call("/api/stock_answer", dict(body, role="nobody", idempotency_key="h9"))[0], 409)
        s, r = self.call("/api/stock_answer", body)
        self.assertEqual((s, r["result"], r["repeated"]), (200, "answered", False))
        self.assertEqual(self.call("/api/stock_answer", body)[1]["repeated"], True)
        s, tl = self.call("/api/current_timeline?owner=amiya&item=buri_xl")
        self.assertEqual(s, 200)
        self.assertEqual(tl["snapshots"][-1]["current"]["stock_decision"]["label"], "そのまま持つ")
        self.assertTrue(any(x["current"]["stock_question"] for x in tl["snapshots"]))


if __name__ == "__main__":
    unittest.main()
