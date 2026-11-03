"""作業の画面の一覧（/api/work）の試験：業務 DB の事実から、項目・時刻・根拠の行を組み立てる（模擬の Claude だけ・費用 0）。"""
import json
import threading
import unittest
import urllib.error
import urllib.request

from agentdock import db, deal, server, work
from tests import test_buri as tb

B = tb.B


def keys(v, section=None, kind=None):
    return [x["key"] for x in v["items"] if (section is None or x["section"] == section) and (kind is None or x["kind"] == kind)]


def get(v, key):
    return next(x for x in v["items"] if x["key"] == key)


class WorkTest(tb.Base):
    def renegotiate(self):
        """担当者が 10本に変える → 網谷水産の AI が読み直して問い合わせ直す → 漁協が 6本で答え直す → 版 2 のボード。"""
        tb.answer(self.cons, "staff-1", self.change10())
        self.run_all(self.reinquiry(10, 18000) + [self.present(self.t6 + 1, [(self.sup, 4), (self.rest, 2)])],
                     [tb.tools(("read_stock", B)), self.offer(6, 18000, "今朝の相対の残りは 6本です")])

    def test_items_carry_time_section_and_source(self):
        self.renegotiate()
        v = work.view("amiya", "buri_xl")
        self.assertEqual(v["item"], dict(code="buri_xl", name="ブリ（特大）", unit="本"))
        for x in v["items"]:
            self.assertIn(x["section"], work.SECTIONS)
            self.assertTrue(x["at"] and x["source"] and x["history"], x["key"])
            self.assertEqual(x["state"], x["history"][-1]["state"])
        ts = [work._t(x["at"]) for x in v["items"]]                                        # 秒で比べる
        self.assertEqual(ts, sorted(ts))                                                      # 時刻の順
        # 読み取ったこと：注文は AI が read_orders で読んだ時刻に現れ、根拠に道具の呼び出しが付く
        orders = [get(v, k) for k in keys(v, "read", "order")]
        self.assertEqual([(o["data"]["customer"], o["data"]["quantity"], o["data"]["sell_price_yen"]) for o in orders],
                         [("スーパー", 4, 22000), ("料亭", 6, 24000)])
        step = next(s for s in orders[1]["source"] if s["table"] == "agent_step")
        self.assertEqual(tb.q("amiya", "SELECT tool FROM agent_step WHERE id = ?", (step["id"],)), [("read_orders",)])
        self.assertGreaterEqual(work._t(orders[1]["at"]), work._t(orders[1]["data"]["received_at"]))
        # やり取り：市場の文の全部（自社が当事者）。自社の AI が送った問い合わせは「AIが済ませたこと」にも出る
        msgs = [get(v, k)["data"] for k in keys(v, "network")]
        self.assertEqual([(m["frm"], m["msg_kind"], m["terms"][0]["quantity"], m["terms"][0]["unit_price_yen"], m["reason"]) for m in msgs], [
            ("網谷水産", "inquiry", 6, 17500, None), ("岩瀬漁協", "offer", 6, 18000, "今朝の相対の残りは 6本です"),
            ("網谷水産", "inquiry", 10, 18000, "注文の数が変わったため"), ("岩瀬漁協", "offer", 6, 18000, "今朝の相対の残りは 6本です")])
        self.assertEqual(keys(v, "done", "inquiry"), [f"sent:{self.q1}", f"sent:{self.q1 + 2}"])
        # 仮押さえ：前の押さえは下ろし、新しい押さえを持つ（時刻つきの移り変わり）
        holds = [get(v, k) for k in keys(v, "done", "hold")]
        self.assertEqual([tuple(h["state"] for h in x["history"]) for x in holds], [("held", "released"), ("held",)])
        self.assertEqual([(x["data"]["quantity"], x["data"]["unit_price_yen"], x["data"]["valid_until"]) for x in holds],
                         [(6, 18000, "2026-11-02T07:00:00+09:00")] * 2)
        # あなたを待つこと：版 1 は答え済み、版 2 が開いている。案の数字はコードの計算
        cons = [get(v, k) for k in keys(v, "waiting", "consultation")]
        self.assertEqual([(c["data"]["version"], c["state"]) for c in cons], [(1, "answered"), (2, "open")])
        a = next(o for o in cons[1]["data"]["options"] if o["code"] == "A")
        self.assertEqual((a["purchase"]["amount_yen"], a["gross_profit_yen"], a["executable"]), (108000, 16000, True))
        self.assertEqual(keys(v, "left"), [])                                                # 案件は閉じていない
        self.assertTrue(any(r["event_kind"] == "human_choice" for r in v["activity"]))
        text = json.dumps(v, ensure_ascii=False)
        for bad in ("aji", "アジ"):
            self.assertNotIn(bad, text)

    def test_approval_waits_for_the_president_then_registered_and_leftover(self):
        self.renegotiate()
        a = next(o for o in tb.view()["options"] if o["code"] == "A")                       # 版 2：スーパー 4本・料亭 2本
        deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "s1")
        v = work.view("amiya", "buri_xl")
        (ap,) = [get(v, k) for k in keys(v, "waiting", "approval")]
        self.assertEqual((ap["state"], ap["data"]["priority_change"], ap["data"]["asked_by"], ap["data"]["option_code"]), ("needs_president", True, "staff", "A"))
        deal.decide("amiya", self.case, a["id"], "approve", a["fingerprint"], "p1", actor="president")
        v = work.view("amiya", "buri_xl")
        ap = get(v, ap["key"])
        self.assertEqual([h["state"] for h in ap["history"]], ["needs_president", "registered"])
        (tr,) = [get(v, k)["data"] for k in keys(v, "done", "trade")]
        self.assertEqual((tr["counterparty"], tr["quantity"], tr["unit_price_yen"], tr["decided_by"]), ("岩瀬漁協", 6, 18000, "president"))
        self.assertEqual([get(v, k)["state"] for k in keys(v, "waiting", "consultation")], ["answered", "closed"])   # 登録で閉じた案件の相談は待たない
        left = [get(v, k)["data"] for k in keys(v, "left")]
        self.assertEqual([(x["customer"], x["quantity"], x["remaining"]) for x in left], [("料亭", 6, 4)])
        self.assertEqual([h["state"] for h in get(v, f"order:{self.rest}")["history"]], ["open", "partly_filled"])

    def test_the_seller_sees_only_its_own_and_disclosed_values(self):
        self.renegotiate()
        v = work.view("iwase", "buri_xl")
        self.assertEqual(keys(v, "read", "order"), [])
        self.assertTrue(keys(v, "done", "reply") and keys(v, "done", "hold") and keys(v, "read", "stock"))
        self.assertEqual([get(v, k)["data"]["side"] for k in keys(v, "done", "hold")], ["sell", "sell"])
        text = json.dumps(v, ensure_ascii=False)
        for secret in ("22000", "24000", "18500", "20500", "22,000", "24,000", "18,500", "20,500", "料亭", "スーパー", "aji"):
            self.assertNotIn(secret, text)
        self.assertEqual(work.view("buyer_b", "buri_xl")["items"], [])                      # AI が読んでいない・当事者でない組織には何も出ない

    def test_source_rows_are_scoped_to_the_owner(self):
        self.renegotiate()
        r = work.source("amiya", "market", "network_message", self.q1 + 2)
        self.assertEqual((r["row"]["quantity"], r["step"]["tool"]), (10, "send_inquiry"))   # 自社の AI がこの文を送った呼び出し
        self.assertNotIn("step", work.source("amiya", "market", "network_message", self.q1))  # 試験の手で送った文には呼び出しが無い
        with self.assertRaises(KeyError):
            work.source("buyer_b", "market", "network_message", self.q1)
        with self.assertRaises(ValueError):
            work.source("amiya", "owner", "authority", 1)
        self.assertEqual(work.source("amiya", "owner", "order_book", self.rest)["row"]["quantity"], 6)


class WorkApiTest(tb.Base):
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

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_work_and_source_endpoints(self):
        s, v = self.get("/api/work?owner=amiya&item=buri_xl")
        self.assertEqual((s, v["item"]["unit"]), (200, "本"))
        self.assertEqual(v, json.loads(json.dumps(work.view("amiya", "buri_xl"), ensure_ascii=False)))
        self.assertEqual(self.get("/api/work?owner=market&item=buri_xl")[0], 400)
        s, cur = self.get("/api/current?owner=amiya&item=buri_xl")
        self.assertEqual((s, cur["waiting_for"], len(cur["lines"])), (200, "staff", 2))
        self.assertEqual(self.get("/api/work?owner=amiya&item=nori")[0], 400)
        s, r = self.get(f"/api/source?owner=amiya&db=market&table=network_message&id={self.q1}")
        self.assertEqual((s, r["row"]["from_owner"]), (200, "amiya"))
        self.assertEqual(self.get(f"/api/source?owner=buyer_c&db=market&table=network_message&id={self.q1}")[0], 404)
        self.assertEqual(self.get("/api/source?owner=amiya&db=owner&table=authority&id=1")[0], 400)
        for name in ("dock.html", "dock.js", "dock.css", "dock-icons.js"):                 # 作業の画面のファイル（web/ の直下だけ）
            with urllib.request.urlopen(f"{self.base}/{name}", timeout=5) as r:
                self.assertEqual(r.status, 200, name)
                body = r.read().decode("utf-8")
            self.assertNotIn("repeating-linear-gradient", body, name)
            self.assertNotIn("proto/", body, name)


if __name__ == "__main__":
    unittest.main()


class CurrentTest(tb.Base):
    """現在地（作業の画面の既定の表示）：注文ごとの状態の行はコードが業務 DB から作る。問いと案は AI が選び、数字はコード。"""

    def lines(self):
        return [l["text"] for l in work.current("amiya", "buri_xl")["lines"]]

    def test_before_any_decision_the_hold_is_not_assigned(self):
        cur = work.current("amiya", "buri_xl")
        self.assertEqual([l["text"] for l in cur["lines"]], ["スーパーの 4本は、まだそろっていません", "料亭の 6本は、まだそろっていません"])
        self.assertEqual([h["text"] for h in cur["holds"]], ["岩瀬漁協が 6本を 1本 18,000円で押さえています（期限 07:00）。どの注文に回すかは未定です"])
        self.assertEqual((cur["waiting_for"], cur["question"]["text"]), ("staff", "この配分で進めますか"))   # 短い問いが無い版は ask_human
        a, e = cur["options"]
        self.assertEqual((a["code"], a["label"], a["action"], a["recommended"]), ("A", "漁協から 6本、スーパーに 4本・料亭に 2本", "approve", True))
        self.assertEqual((e["code"], e["action"]), ("E", "reject"))                              # 仕入れない案は「見送る」
        self.assertEqual([(x["customer"], x["secured_after"], x["short_after"]) for x in a["after"]], [("スーパー", 4, 0), ("料亭", 2, 4)])
        self.assertEqual((a["numbers"]["purchase_yen"], a["numbers"]["gross_profit_yen"], a["gross_profit_diff_yen"]), (108000, 16000, 0))
        self.assertEqual(e["gross_profit_diff_yen"], -16000)
        self.assertEqual(a["grounds"]["reason"], "朝の注文を先に満たす")

    def test_after_approval_waiting_for_the_president_then_registered(self):
        a = next(o for o in work.current("amiya", "buri_xl")["options"] if o["code"] == "A")
        deal.decide("amiya", self.case, a["option_id"], "approve", a["fingerprint"], "s1")
        cur = work.current("amiya", "buri_xl")
        self.assertEqual(cur["waiting_for"], "president")                                       # 担当者の承認の操作は出さない
        self.assertEqual((cur["pending"]["kind"], cur["pending"]["option_id"], cur["pending"]["priority_change"]), ("approval", a["option_id"], True))
        deal.decide("amiya", self.case, a["option_id"], "approve", a["fingerprint"], "p1", actor="president")
        cur = work.current("amiya", "buri_xl")
        self.assertEqual([l["text"] for l in cur["lines"]], ["スーパーの注文 4本を確保しています（登録済み 4本）",
                                                             "料亭の注文 6本のうち 2本を確保しています（登録済み 2本）。4本 足りません"])
        self.assertEqual((cur["waiting_for"], cur["options"], cur["holds"]), (None, [], []))

    def test_a_decision_assigns_the_held_quantity_to_orders(self):
        tb.answer(self.cons, "p", dict(quantity=6, allocations=[dict(order_id=self.rest, quantity=6)]), role="president")
        cur = work.current("amiya", "buri_xl")
        self.assertEqual(cur["decision"]["text"], "社長（網谷さん）の決定（版 1）：仕入れ 6本（料亭 6本）")
        self.assertEqual(self.lines(), ["スーパーの 4本は、まだそろっていません", "料亭の注文 6本を確保しています（岩瀬漁協が押さえ中 6本）"])
        self.assertEqual(cur["holds"], [])
        self.assertEqual(cur["waiting_for"], None)                                              # 答えた後は AI の番（交渉し直し）
        self.assertEqual([e["kind"] for e in cur["ai_queue"]], ["human_choice"])