"""仲間との対話の画面の処理の試験（模擬の Claude・本物の Claude は呼ばない）。

態度は業務の状態からコードが決める（2026-11-04 デイブ・SAL・正本 §1 の表）：
  強気＝別の仕入先がある／苦戦＝選択肢が少なく上限を超える／慎重＝注文や条件が変わった／残念＝交渉が成立しなかった／安堵＝必要な数量と条件がそろった
態度と実行の状態（実行中・あなたの判断待ち・社長確認中・停止・登録済み）は別に持つ。
社長の朝の指示（魚廣の分は落とすなよ）は業務の記録で、指示どおりの優先は社長が決めた扱い、指示と違う案は社長の確認へ進む。
"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
import urllib.error
import urllib.request

from agentdock import agents, consult, db, deal, disclosure, events, live, mate, mockdock, network, results, scene, server
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
U = dict(input_tokens=3000, output_tokens=400)
SUPER = sc.shop("amiya", "スーパー")
RYOTEI = sc.shop("amiya", "料亭")


def tools(*calls):
    return Reply(content=[{"type": "tool_use", "id": f"m{i}{time.time_ns()}", "name": n, "input": x} for i, (n, x) in enumerate(calls)], usage=U)


def ex(owner, sql, args=()):
    c = db.connect(owner)
    try:
        with c:
            c.execute(sql, args)
    finally:
        c.close()


class Base(unittest.TestCase):
    variant = "normal"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        scene.apply_variant(self.variant)
        self.worker = live.Worker({o: mockdock.Policy(o, 0) for o in db.OWNER_CODES}, run_kind="mock")

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def delegate(self, key="d1"):
        c = db.connect("amiya")
        try:
            cs = mate.check_sheet("amiya")
            return mate.delegate(c, D, list(sc.DELEGATION_SCOPES), dict(orders=[o["order_id"] for o in cs["orders"]],
                                                                         directives=[d["directive_id"] for d in cs["directives"]]), key)
        finally:
            c.close()

    def start(self):
        self.delegate()
        scene.start(item="buri_xl")

    def run_all(self):
        while self.worker.step():
            pass
        self.assertIsNone(self.worker.error)

    def add_ryotei(self):
        c = db.connect("amiya")
        try:
            return events.order_received(c, "料亭", "buri_xl", 6, 24000, D)[0]
        finally:
            c.close()

    def v(self):
        return mate.view("amiya", "buri_xl")

    def option(self, v, needle):
        return next(a for a in v["actions"] if needle in a["label"])

    def decide(self, a, key, actor="staff"):
        b = a["api"]["body"]
        return deal.decide("amiya", b["case_id"], b["option_id"], b["action"], b["fingerprint"], key, actor=actor)


class StanceTest(Base):
    def test_bold_when_there_is_another_supplier(self):
        self.start()
        self.worker.step()                                                   # 網谷水産の AI が漁協と B社に照会する
        v = self.v()
        self.assertEqual((v["stance"], v["rule"], v["exec"]), ("bold", "別の仕入先がある", "running"))
        self.assertEqual(v["refs"]["suppliers"], ["buyer_b", "iwase"])
        self.assertEqual(v["headline"], dict(text="この条件で押してみます。", origin="code", ref=None))
        self.assertEqual(v["who"], "ai")

    def test_careful_when_an_order_arrives_while_the_ai_works_then_settled_by_a_human_decision(self):
        self.start()
        self.worker.step()
        self.add_ryotei()                                                    # 順風満帆の追加注文（AI が働き始めた後）
        v = self.v()
        self.assertEqual((v["stance"], v["rule"]), ("careful", "追加の注文が入った"))   # 慎重の説明は、いまの理由
        self.assertEqual(v["headline"]["text"], "待ってください。確認し直します。")
        self.assertIn(f"{RYOTEI}から追加の注文 6本が入りました。", [s["text"] for s in v["sub"]])
        self.run_all()
        v = self.v()                                                         # 相談：慎重のまま・語りかけは AI の文（記録の番号つき）
        self.assertEqual((v["stance"], v["exec"], v["who"]), ("careful", "your_turn", "you"))
        self.assertEqual((v["headline"]["origin"], v["headline"]["ref"]["table"]), ("ai", "workspace_turn"))
        self.assertEqual(v["why"]["lead"], f"{SUPER}を優先するのは、社長の朝の指示があるためです。{RYOTEI}を優先するには、社長への確認が必要です。")
        self.assertEqual([s["title"] for s in v["why"]["sections"]][:3], ["足りない数と注文", "社長の指示と権限", "相手の回答と値"])
        a = self.option(v, f"岩瀬漁協 6本：{SUPER}")                           # 指示どおり（漁協の分だけ）：魚廣 4本・順風満帆 2本 → 担当者が決められる
        self.assertFalse(a["president"])
        time.sleep(1.05)                                                     # 人の判断の記録は秒まで
        self.assertEqual(self.decide(a, "s1")["result"], "registered")
        v = self.v()
        self.assertNotEqual(v["stance"], "careful")
        self.assertIn(f"{SUPER} 4本・{RYOTEI} 2本で登録しました", v["receipt"]["text"])
        self.assertIn(f"残りの{RYOTEI} 4本は、私が別の仕入先を当たります。", v["receipt"]["text"])

    def test_sorry_when_the_last_reply_is_a_decline_until_the_ai_turns_to_another(self):
        self.start()
        ex("iwase", "UPDATE stock SET status = 'sold' WHERE lot_no = 'S-buri_xl-1'")
        self.worker.step()                                                   # 網谷水産が照会
        self.worker.step()                                                   # 岩瀬漁協：断り
        v = self.v()
        self.assertEqual((v["stance"], v["rule"]), ("sorry", "交渉が成立しなかった"))
        self.assertEqual(v["headline"]["text"], "難しかったです。別の浜を当たります。")
        self.assertEqual([s["origin"] for s in v["sub"]], ["code", "reply"])   # 相手の理由は「相手から届いた回答」
        network.send("amiya", "buyer_c", "inquiry", "buri_xl", "ブリを 2本お願いできますか。", D, 2, 17500, side="buy")
        self.assertNotEqual(self.v()["stance"], "sorry")                     # 次の照会へ向き直った

    def test_relief_when_the_recommended_option_fills_every_order(self):
        self.start()
        self.run_all()                                                       # 料亭の注文なし：漁協 4本で魚廣 4本がそろう
        v = self.v()
        self.assertEqual((v["stance"], v["rule"], v["exec"]), ("relief", "必要な数量と条件がそろった", "your_turn"))
        self.assertTrue(v["actions"][0]["recommended"])

    def test_no_stance_and_a_president_state_when_the_staff_picks_an_option_against_the_directive(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{RYOTEI} 6本")                            # 料亭 6本・魚廣 0本 → 社長に確かめる
        self.assertTrue(a["president"])
        self.assertTrue(a["label"].startswith("社長に確かめる："))
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "s2")["result"], "needs_president")
        v = self.v()
        self.assertEqual((v["exec"], v["stance"], v["who"], v["actions"]), ("president", None, "president", []))
        self.assertIn("社長（網谷さん）に確認を送りました", v["receipt"]["text"])
        p = v["president"]
        self.assertEqual((p["kind"], p["priority_change"]), ("approval", True))
        r = deal.decide("amiya", p["case_id"], p["option_id"], "approve", p["fingerprint"], "p1", actor="president")
        self.assertEqual(r["result"], "registered")                          # 社長の画面から承認できる

    def test_stopped_is_a_state_apart_from_the_stance(self):
        self.start()
        self.worker.step()
        v = mate.view("amiya", "buri_xl", worker_error="呼び出しの上限に達しました")
        self.assertEqual((v["exec"], v["stop"], v["stance"]), ("stopped", "呼び出しの上限に達しました", "bold"))


class StruggleTest(Base):
    variant = "buri_floor_up"   # 漁協の最低の値 19,000円 ＞ 魚廣向けの上限 18,500円

    def test_struggle_when_the_only_supplier_is_over_the_ceiling(self):
        self.start()
        q = network.send("amiya", "iwase", "inquiry", "buri_xl", "ブリを 4本、1本 17,500円でお願いできますか。", D, 4, 17500, side="buy")
        c = db.connect("iwase")
        try:
            ev = events.pending(c, ["quote_request"])[0]
        finally:
            c.close()
        agents.run_event(MockClient([tools(("reply", dict(in_reply_to=q, kind="offer", terms=[dict(quantity=4, unit_price_yen=19000)],
                                                         message="4本なら 1本 19,000円です。", reason=None)))]), "iwase", ev, D)
        v = self.v()
        self.assertEqual((v["stance"], v["rule"]), ("struggle", "選択肢が少なく、上限を超える"))
        self.assertEqual((v["refs"]["price"], v["refs"]["ceiling"]), (19000, 18500))
        self.assertIn(f"届いた値 19,000円は、{SUPER}向けの上限 18,500円を超えます。", [s["text"] for s in v["sub"]])
        self.assertEqual(v["why"]["sections"][0]["title"], "値と上限（コードの計算）")


class NumbersTest(Base):
    """相談の画面の数（見出し・下の行・案のボタン・理由の1枚）は、全部コードの同じ計算（mate.tally）から出る。
    AI の文に食い違う数があっても、画面にはコードの数が出る（取引の整合性：金額と数量はコードが業務 DB から出す）。"""

    def consult(self, lie=False):
        orig = mockdock.Policy._present
        if lie:   # 模擬の AI が、業務 DB と合わない数を文と案の名前に書く
            def lying(policy, item, orders, terms):
                name, inp = orig(policy, item, orders, terms)
                inp = json.loads(json.dumps(inp, ensure_ascii=False))
                inp["short_question"] = inp["ask_human"] = f"{RYOTEI}の追加で 3本足りません。どう分けますか"
                for o in inp["options"]:
                    if o["code"] != "E":
                        o["short_label"], o["label"] = f"{SUPER}9・{RYOTEI}9", "漁協から 9本"
                return name, inp
            mockdock.Policy._present = lying
        try:
            self.start()
            self.worker.step()
            self.add_ryotei()
            self.run_all()
        finally:
            mockdock.Policy._present = orig
        v = self.v()
        self.assertEqual((v["stance"], v["exec"]), ("careful", "your_turn"))
        return v

    def check_one_calculation(self, v):
        t = v["tally"]
        held = "・".join(f"{h['name']} {h['quantity']}本" for h in t["held"])
        self.assertEqual((t["total"], t["registered"], t["held_total"], t["left"]), (10, 0, 8, 2))   # 漁協 6本（返事の2つの条件のうち押さえは1件）＋ B社 2本
        self.assertEqual(held, "岩瀬漁協 6本・B社 2本")
        line = f"10本の注文に対し、8本を仮押さえ（{held}）。あと 2本不足。どの案でも、どこかの店が欠けます。"
        self.assertEqual(t["line"], line)
        self.assertIn(line, [s["text"] for s in v["sub"]])                                   # 下の行
        rows = [r["text"] for s in v["why"]["sections"] for r in s["rows"]]
        self.assertIn(line, rows)                                                            # 理由の1枚
        self.assertIn(f"仮押さえ（相手が期限つきで押さえた分・どの注文に回すかは未定）：{held}", rows)
        a = next(x for x in v["actions"] if x.get("recommended"))
        combo = f"岩瀬漁協 6本＋B社 2本：{SUPER} 4本・{RYOTEI} 4本"                         # 2つの相手の条件をまとめた案（案B）
        self.assertEqual(a["label"], combo)                                                   # 案のボタン（案の配分から）
        self.assertEqual(a["short_after"], [dict(shop=RYOTEI, quantity=2)])
        self.assertIn(f"この案では足りない：{RYOTEI} 2本", a["note"])
        self.assertTrue(any(r.startswith(f"案1：{combo}（仕入れ 141,800円）") and f"この案では足りない：{RYOTEI} 2本" in r for r in rows))
        self.assertIn("案1の行：岩瀬漁協 6本 × 17,500円（期限 07:00・条件 2）", rows)          # 行ごとの値・期限・出所
        self.assertIn("案1の行：B社 2本 × 18,400円（期限 07:00・条件 3）", rows)
        opts = [r for r in next(s for s in v["why"]["sections"] if s["title"] == "案（コードの計算）")["rows"] if "の行：" not in r["text"]]
        self.assertEqual([r["text"][:2] for r in opts], ["案1", "案2", "案3", "案4"])          # 画面では続き番号（AI の記号 A・B・E は出さない）
        self.assertIsNone(t["unused_text"])                                                   # 仮押さえは全部、案1に入っている
        self.assertNotIn("どの案にも入っていない仮押さえ", [s["title"] for s in v["why"]["sections"]])
        b = next(x for x in v["actions"] if x.get("president"))
        self.assertEqual(b["label"], f"社長に確かめる：岩瀬漁協 6本：{RYOTEI} 6本")
        texts = [v["headline"]["text"]] + [s["text"] for s in v["sub"]] + [x["label"] for x in v["actions"]] + [x.get("note") or "" for x in v["actions"]] + rows
        for x in texts:
            self.assertNotIn("9本", x)
            self.assertNotIn(f"{SUPER}9", x)
        return a

    def test_the_numbers_on_the_consult_screen_come_from_one_calculation(self):
        v = self.consult()
        self.check_one_calculation(v)
        self.assertEqual(v["headline"]["origin"], "ai")                                      # 数字の無い AI の文は使う（出所つき）
        self.assertIsNone(v["why"]["speech_origin"]["replaced"])

    def test_when_the_ai_leaves_a_hold_out_the_code_records_it_and_tells_the_ai(self):
        orig = mockdock.Policy._present

        def no_combo(policy, item, orders, terms):   # 模擬の AI が、組み合わせの案を出さない
            name, inp = orig(policy, item, orders, terms)
            inp = dict(inp, options=[o for o in inp["options"] if not o.get("buy_term_ids")])
            inp["recommended"], inp["alternative"] = inp["options"][0]["code"], inp["options"][1]["code"]
            return name, inp
        mockdock.Policy._present = no_combo
        try:
            self.start()
            self.worker.step()
            self.add_ryotei()
            self.run_all()
        finally:
            mockdock.Policy._present = orig
        v = self.v()
        self.assertEqual(len(v["actions"]), 3)                                                # コードは案を足さない
        unused = "B社の 2本（仮押さえ）は、どの案にも入っていません。仮押さえを全部使う案が無いことを、AI に伝えました。"
        self.assertEqual(v["tally"]["unused_text"], unused)
        self.assertIn(unused, [s["text"] for s in v["sub"]])
        c = db.connect("amiya")
        try:
            told = [r[0] for r in c.execute("SELECT result_text FROM agent_step WHERE tool = 'present_options' ORDER BY id")]
        finally:
            c.close()
        self.assertIn("全部使う案がありません", told[-1])                                     # AI への返事にも書く

    def test_an_ai_text_with_numbers_that_do_not_match_is_replaced_by_the_code(self):
        v = self.consult(lie=True)
        self.check_one_calculation(v)
        self.assertEqual(v["headline"], dict(text=f"{RYOTEI}の追加注文で、魚が足りません。どう分けますか。", origin="code", ref=None))
        r = v["why"]["speech_origin"]["replaced"]                                            # 使わなかった AI の文と、その記録の番号を残す
        self.assertEqual((r["text"], r["ref"]["table"]), (f"{RYOTEI}の追加で 3本足りません。どう分けますか", "workspace_turn"))
        self.assertNotIn("3本", " ".join([v["headline"]["text"]] + [s["text"] for s in v["sub"]]))


class NumberCheckTest(NumbersTest):
    """AI の文の数の照合（案イ）：合う文は残る（照合済み）／合わない文は替わる／照合できない数を含む文は替わる。"""

    def allowed(self):
        self.consult()
        f = mate.facts("amiya", "buri_xl")
        return f, mate.tally(f), mate._allowed(f, mate.tally(f))

    def test_the_rules_of_the_check(self):
        f, tl, al = self.allowed()                                      # 注文 10本・登録 0本・仮押さえ 8本・あと 2本
        ok = [f"{RYOTEI}の追加で 2本足りません。どう分けますか",          # 足りない数 2 ＝ あと 2本
              f"{RYOTEI}の追加で ２本足りません",                          # 全角の数字
              f"案1なら{RYOTEI}が 4本足りません",                          # 案ごとの足りない数 4
              "岩瀬漁協の 1本 17,500円の条件で進めますか",                  # 「1本 17,500円」は単価の言い方・17,500円は届いた条件の値
              "期限は 07:00 です",                                          # 条件の期限
              "10本のうち 8本を押さえています"]
        for t in ok:
            self.assertEqual(mate.check_numbers(t, al), [], t)
        bad = {f"{RYOTEI}の追加で 3本足りません": "足りない数 3本",
               "岩瀬漁協は 1本 17,400円です": "17,400円",
               "期限は 08:15 です": "時刻 08:15",
               "あと 2つ足りません": "単位が無く",                           # 単位の無い数は照合できない
               "2箱 足りません": "品目の単位",                              # ブリは本で数える
               "全部で 12本です": "12本"}
        for t, why in bad.items():
            r = mate.check_numbers(t, al)
            self.assertTrue(r and any(why in x for x in r), (t, r))
        said, rep = mate._ai_text(f, tl, ok[0], dict(db="owner", table="workspace_turn", id=1))
        self.assertEqual((said["origin"], rep), ("ai_checked", None))
        said, rep = mate._ai_text(f, tl, "あと 2つ足りません", dict(db="owner", table="workspace_turn", id=1))
        self.assertIsNone(said)
        self.assertTrue(rep["reason"].startswith("AI の文の数を照合し、合わない数があったのでコードの文に替えました"))

    def test_a_matching_ai_text_stays_with_its_origin(self):
        orig = mockdock.Policy._present

        def honest(policy, item, orders, terms):
            name, inp = orig(policy, item, orders, terms)
            inp = dict(inp, short_question=f"{RYOTEI}の追加で 2本足りません。どう分けますか")
            return name, inp
        mockdock.Policy._present = honest
        try:
            self.start()
            self.worker.step()
            self.add_ryotei()
            self.run_all()
        finally:
            mockdock.Policy._present = orig
        v = self.v()
        self.assertEqual(v["headline"]["text"], f"{RYOTEI}の追加で 2本足りません。どう分けますか。")   # 句点の無い AI の文は、画面に出す前に句点を足す（2026-11-05 デイブ）
        self.assertEqual((v["headline"]["origin"], v["why"]["speech_origin"]["label"]), ("ai_checked", "AI が書いた文（照合済み）"))
        self.check_one_calculation(v)


class ClockTest(Base):
    def test_the_screen_shows_the_demo_morning_clock(self):
        k = mate.clock("amiya")
        self.assertIsNone(k["anchor"])
        self.assertEqual(k["now_shown"], sc.DEMO_CLOCK_START)            # 始める前は朝の開始の時刻
        self.start()
        k = mate.clock("amiya")
        c = db.connect("amiya")
        try:
            granted = c.execute("SELECT granted_at FROM delegation").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(k["anchor"], granted)                           # 業務の開始＝委任の記録の時刻
        import datetime as dt
        later = (dt.datetime.fromisoformat(granted) + dt.timedelta(minutes=12, seconds=30)).isoformat()
        self.assertEqual(mate.shown_time(granted, k), "05:45")
        self.assertEqual(mate.shown_time(later, k), "05:57")              # 経過した分だけ進む
        self.assertEqual(mate.shown_time(f"{D}T05:40:00+09:00", k), "05:40")   # 実演の朝の時刻の記録はそのまま
        self.worker.step()
        v = self.v()
        self.assertEqual(v["receipt"]["at_shown"], "05:45")
        rows = [r for s in v["why"]["sections"] + v["why"]["more"] for r in s["rows"] if r["at"]]
        self.assertTrue(rows and all(r["at_shown"] == mate.shown_time(r["at"], k) for r in rows))
        self.assertIn("実際に記録した時刻とは違います", v["clock"]["note"])


class MorningTest(Base):
    def test_before_start_greets_and_lists_todays_work_by_the_rule(self):
        v = self.v()
        self.assertEqual((v["exec"], v["stance"], v["who"]), ("before_start", None, "you"))
        # 品目ごとに比べる（本と箱を足さない）：ブリ 4本 ＞ 3.6本・シロエビ 5箱 ＞ 4.6箱（直近 30日の平均）
        self.assertEqual(v["headline"]["text"], "おはようございます。今日はブリとシロエビの注文が、ふだんより多めです。")
        self.assertEqual(v["sub"][0]["text"], "今朝はブリ 4本・シロエビ 5箱です。直近 30日の平均は、ブリ 3.6本・シロエビ 4.6箱です。")
        # できる範囲だけを約束する（シロエビの入札は未接続）
        self.assertEqual(v["sub"][1]["text"], "まず注文と社長の指示を確認しましょう。その後、ブリの照会は私が進めます。配分と購入の判断は、必要な時に相談します。")
        vol = mate.order_volume("amiya", D)
        self.assertEqual([(x["item"], x["today"], x["usual"], x["more"]) for x in vol["items"]], [("ブリ", 4, 3.6, True), ("シロエビ", 5, 4.6, True)])
        m = mate.morning("amiya")
        self.assertEqual([j["key"] for j in m["jobs"]], ["check", "buy:buri_xl", "tender:shiroebi"])   # 一緒に確認 → AI に任せてよい
        self.assertTrue(all(j["count"] > 0 for j in m["jobs"]))
        self.assertEqual((m["jobs"][0]["who"], m["jobs"][0]["who_label"]), ("together", "一緒に確認"))
        t = next(j for j in m["jobs"] if j["key"] == "tender:shiroebi")
        self.assertFalse(t["connected"])
        self.assertEqual([j["step"] for j in m["jobs"]], ["注文の確認", "不足分の手配", "入札"])   # 工程の名前は帯と同じ

    def test_an_item_without_sales_history_is_not_called_more(self):
        # 売れ方の記録の無い売り先がある品目は、ふだんを 0 と数えて「多い」と言わない
        c = db.connect("amiya")
        try:
            with c:
                c.execute("DELETE FROM sales_history WHERE item_id = (SELECT id FROM item WHERE code = 'shiroebi')")
        finally:
            c.close()
        vol = mate.order_volume("amiya", D)
        self.assertEqual([(x["item"], x["known"], x["more"]) for x in vol["items"]], [("ブリ", True, True), ("シロエビ", False, False)])
        v = self.v()
        self.assertEqual(v["headline"]["text"], "おはようございます。今日はブリの注文が、ふだんより多めです。")
        self.assertEqual(v["sub"][0]["text"], "今朝はブリ 4本・シロエビ 5箱です。直近 30日の平均は、ブリ 3.6本です。")

    def test_president_wait_goes_below_ai_work_and_your_turn_goes_first(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        keys = [j["key"] for j in mate.morning("amiya")["jobs"]]
        self.assertEqual(keys[0], "ask:buri_xl")
        a = self.option(self.v(), f"：{RYOTEI} 6本")
        time.sleep(1.05)
        self.decide(a, "s3")
        jobs = [j for j in mate.morning("amiya")["jobs"] if not j["done"]]
        self.assertEqual(jobs[-1]["key"], "pres:buri_xl")                     # 社長の返事待ちは、若い担当者が今答える仕事ではない

    def test_delegate_records_the_scope_and_stops_when_the_orders_changed(self):
        c = db.connect("amiya")
        try:
            with self.assertRaises(mate.MateError):
                mate.delegate(c, D, ["inquiry"], dict(orders=[1], directives=[1]), "x")   # 確かめた注文が足りない
            with self.assertRaises(mate.MateError):
                mate.delegate(c, D, ["sell_everything"], dict(orders=[1, 2], directives=[1]), "y")
        finally:
            c.close()
        r = self.delegate("d9")
        self.assertFalse(r["repeated"])
        self.assertTrue(self.delegate("d9")["repeated"])
        v = self.v()
        self.assertEqual(v["receipt"]["text"], "受け取りました。照会・条件内の入札・再交渉を引き受けます。")
        scene.start(item="buri_xl")
        prompt, _ = agents.situation("amiya", dict(kind="order_new", payload=dict(item="buri_xl", customer="スーパー", quantity=4, order_id=1)), D)
        self.assertIn(f"社長の今日の指示（指示 1・別の浜から・電話（模擬））：「{SUPER}の分は落とすなよ」", prompt)
        self.assertIn("担当者が任せた範囲：照会・条件内の入札・再交渉", prompt)

    def test_tender_shows_what_is_not_connected(self):
        t = mate.tender_status("amiya", "shiroebi")
        self.assertFalse(t["connected"])
        rows = {r["label"]: r for r in t["rows"]}
        self.assertFalse(rows["市場の入札の受付"]["connected"])
        self.assertFalse(rows["AI が入札を出す道具"]["connected"])
        self.assertTrue(rows["注文"]["connected"])
        v = mate.tender_view("amiya", "shiroebi")
        self.assertEqual((v["exec"], v["exec_label"]), ("unconnected", "未接続"))


class BandTest(Base):
    """工程の帯（SAL 2026-11-04・デイブ 18:50）：品目ごとの工程・記録が変わった時だけ進む・未接続の段は明示する。"""

    def b(self, code="buri_xl"):
        x = mate.band("amiya", code)
        return x, {s["name"]: (s["state"], s["note"]) for s in x["steps"]}

    def test_buri_and_shiroebi_have_their_own_steps(self):
        x, _ = self.b()
        self.assertEqual([s["name"] for s in x["steps"]], ["注文の確認", "落札結果の照合", "不足分の手配", "振分け・承認", "結果の引継ぎ"])
        y, _ = self.b("shiroebi")
        self.assertEqual([s["name"] for s in y["steps"]], ["注文の確認", "入札", "落札結果の照合", "振分け", "結果の引継ぎ"])
        self.assertEqual([s["name"] for s in y["steps"] if not s["connected"]], ["入札", "振分け", "結果の引継ぎ"])   # 未接続を明示する
        self.assertEqual([s["name"] for s in x["steps"] if not s["connected"]], ["結果の引継ぎ"])
        self.assertEqual((x["mode"], x["current"]), ("before", 0))

    def test_the_buri_band_follows_the_records(self):
        self.start()                                                          # 委任・朝のせりの結果が届く・注文の出来事
        x, s = self.b()
        self.assertEqual(s["注文の確認"][0], "done")
        self.assertEqual(s["落札結果の照合"], ("done", "自社の落札なし。不足分の手配へ"))   # せり 12本のうち、網谷水産の落札は 0本（未着ではない・照合済みと確保を分ける）
        self.worker.step()                                                    # 漁協と B社に照会
        x, s = self.b()
        self.assertEqual((x["mode"], s["不足分の手配"][0]), ("working", "current"))
        self.assertIn("照会中", s["不足分の手配"][1])
        self.add_ryotei()
        self.run_all()                                                        # 相談（あなたの判断待ち）
        x, s = self.b()
        self.assertEqual((x["mode"], x["current"]), ("you", 3))
        self.assertEqual(s["振分け・承認"], ("current", "配分を相談中・あなたの判断待ち"))
        self.assertEqual(s["不足分の手配"], ("partial", "仮押さえ 8本・あと 2本不足"))   # 不足が残る間は済みにしない（SAL 2026-11-04）
        self.assertEqual(x["summary"], "ブリ：振分け・承認（配分を相談中・あなたの判断待ち）")
        a = self.option(self.v(), f"：{RYOTEI} 6本")
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "s9")["result"], "needs_president")
        x, s = self.b()
        self.assertEqual((x["mode"], s["振分け・承認"]), ("president", ("current", "社長確認中")))

    def test_registered_then_the_handover_is_not_connected(self):
        self.start()
        self.run_all()                                                        # 料亭の注文なし：漁協 4本で魚廣 4本
        a = self.v()["actions"][0]
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "r1")["result"], "registered")
        self.run_all()
        x, s = self.b()
        self.assertEqual((x["mode"], x["current"]), ("done", None))
        self.assertEqual(s["振分け・承認"], ("done", "登録済み"))
        self.assertEqual(s["結果の引継ぎ"][0], "todo")
        self.assertEqual(x["summary"], "ブリ：登録済み（結果の引継ぎは未接続）")

    def test_stopped_shows_on_the_band(self):
        self.start()
        self.worker.step()
        x = mate.view("amiya", "buri_xl", worker_error="呼び出しの上限に達しました")["band"]
        self.assertEqual((x["mode"], x["steps"][2]["state"], x["steps"][2]["note"]), ("stopped", "current", "停止"))

    def test_shiroebi_stays_at_the_unconnected_tender(self):
        self.start()
        y, s = self.b("shiroebi")
        self.assertEqual((y["mode"], s["入札"], s["落札結果の照合"][0]), ("unconnected", ("current", "未接続"), "todo"))   # 入札の結果は届けていない
        self.assertEqual(y["summary"], "シロエビ：入札（未接続）")

    def test_the_mate_view_and_the_morning_carry_the_bands(self):
        v = self.v()
        self.assertEqual(v["band"]["item"], "buri_xl")
        self.assertEqual([o["summary"] for o in v["band_others"]], ["シロエビ：注文の確認（担当者の確認待ち）"])
        m = mate.morning("amiya")
        self.assertEqual([b["item"] for b in m["bands"]], ["buri_xl", "shiroebi"])
        self.assertEqual(mate.tender_view("amiya", "shiroebi")["band"]["item"], "shiroebi")

    def test_publishing_the_morning_auction_twice_adds_nothing(self):
        self.start()
        results.publish([r for r in results.today_results() if r["method"] == "auction"])   # 同じ結果をもう一度送る
        c = db.connect("amiya")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM auction_result_in").fetchone()[0], 2)   # B社 4本・ほかの仲買 2本
        finally:
            c.close()


class DecideBlockTest(Base):
    """判断のブロック（SAL §2・§3）：案の名前は人の返事の言葉・店ごとの確保と不足・承認のボタンの文。選ぶ（下見）は画面だけで、承認のボタンが既存の処理を通る。"""

    def test_the_choices_say_what_happens_in_plain_words(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        acts = {a["title"]: a for a in self.v()["actions"]}
        self.assertEqual(list(acts), [f"{SUPER} 4本を確保し、{RYOTEI}へ 4本を回す", f"{SUPER} 4本を確保し、{RYOTEI}へ 2本を回す",
                                      f"{RYOTEI}へ 6本を回す（{SUPER}は 0本）", "今回は仕入れを見送る"])
        rec = acts[f"{SUPER} 4本を確保し、{RYOTEI}へ 4本を回す"]
        self.assertTrue(rec["recommended"])
        self.assertEqual((rec["choice"], rec["confirm"]), ("approve", "この配分で購入を承認する"))
        self.assertEqual(rec["buys"], [dict(seller="岩瀬漁協", quantity=6, unit_price_yen=17500, amount_yen=105000),
                                       dict(seller="B社", quantity=2, unit_price_yen=18400, amount_yen=36800)])
        self.assertEqual(rec["purchase_yen"], 141800)                                        # 相手ごとの金額の合計と同じ（理由の1枚の「仕入れ 141,800円」）
        self.assertEqual(rec["alloc"], [dict(shop=SUPER, ordered=4, secured=4, short=0, added=4), dict(shop=RYOTEI, ordered=6, secured=4, short=2, added=4)])
        self.assertEqual(rec["effect"], "岩瀬漁協 6本・B社 2本（合計 141,800円）の買い付けを登録します（購入の確定）。")
        pres = acts[f"{RYOTEI}へ 6本を回す（{SUPER}は 0本）"]
        self.assertEqual((pres["choice"], pres["confirm"], pres["president"]), ("president", "この案を社長に確かめる", True))
        self.assertIn("社長が承認するまで、何も登録しません", pres["effect"])
        rej = acts["今回は仕入れを見送る"]
        self.assertEqual((rej["choice"], rej["effect"]), ("reject", "仕入れずに、この相談を閉じます。仮押さえの 8本は手放します。"))

    def test_the_approve_button_goes_through_the_existing_check(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        pres = next(a for a in self.v()["actions"] if a.get("choice") == "president")
        time.sleep(1.05)
        self.assertEqual(self.decide(pres, "dp1")["result"], "needs_president")   # 承認のボタンでも、指示と違う案は社長の確認へ
        self.assertEqual(self.v()["exec"], "president")


class RoundStoryTest(Base):
    """相手の返事を受け、AI が次の手を用意して戻ってきた場面（SAL 2026-11-04）：見出し＝何が起きたか・次の手と残りの不足・登録済みの行き先・態度は慎重。"""

    def test_after_a_decline_the_companion_tells_what_happened_and_the_next_move(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{RYOTEI} 6本")                       # 順風満帆 6本（魚廣 0本）→ 社長の確認
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "rs1")["result"], "needs_president")
        p = self.v()["president"]
        time.sleep(1.05)
        self.assertEqual(deal.decide("amiya", p["case_id"], p["option_id"], "approve", p["fingerprint"], "rs2", actor="president")["result"], "registered")
        self.run_all()                                                      # 漁協は残りなしで断り・B社は 2本 → 次の相談
        v = self.v()
        self.assertEqual((v["exec"], v["stance"], v["rule"]), ("your_turn", "careful", "追加分が不足している"))   # 笑顔の基本の姿にしない・説明はいまの理由
        self.assertEqual(v["headline"], dict(text="岩瀬漁協では追加分を確保できませんでした。B社からは 2本を仮押さえできています。", origin="calc", ref=None))
        subs = [s["text"] for s in v["sub"]]
        self.assertEqual(subs[0], f"おすすめは「{SUPER} 2本を確保する」です。この案でも、あと 2本足りません。")
        self.assertEqual(subs[1], f"10本の注文に対し、6本を登録済み（{RYOTEI} 6本）・2本を仮押さえ（B社 2本）。あと 2本不足。")   # 買う案が1つなので「どの案でも」は言わない
        self.assertEqual(v["why"]["lead"], "岩瀬漁協から断りの返事が届き、追加分があと 2本不足しているためです。足りない分をどう手配するか、あなたと相談しています。")
        st = next(x for x in v["band"]["steps"] if x["name"] == "不足分の手配")   # 不足が残る間は「済み」にしない（SAL 2026-11-04）
        self.assertEqual((st["state"], st["note"]), ("partial", "仮押さえ 2本・あと 2本不足"))
        said = json.dumps(dict(h=v["headline"], s=v["sub"], a=v["ask"], w=v["why"], b=v["band"]), ensure_ascii=False)
        for word in ("そろいました", "終わりました", "全部の注文がそろいます"):
            self.assertNotIn(word, said)

    def test_to_the_end_the_shortage_is_never_called_done(self):
        """通しの確認（SAL 2026-11-04）：断り → 次の相談 → 登録 → 下書き → 終了。不足が残る間は、完了の言葉も「別の浜を当たる」約束も出さない。"""
        self.test_after_a_decline_the_companion_tells_what_happened_and_the_next_move()
        v = self.v()
        rec = next(a for a in v["actions"] if str(a.get("id", "")).startswith("opt") and a.get("recommended"))
        time.sleep(1.05)
        self.assertEqual(self.decide(rec, "rs3")["result"], "registered")
        self.run_all()                                                      # B社も断り → 売り先への連絡の下書き
        v = self.v()
        self.assertEqual((v["exec"], v["stance"]), ("your_turn", "sorry"))
        self.assertEqual(v["headline"]["text"], f"{SUPER}に、足りない見込みを伝えますか。")   # 「別の浜を当たります」と言わない
        st = {x["name"]: x for x in v["band"]["steps"]}
        self.assertEqual(st["振分け・承認"]["note"], "売り先への連絡を相談中・あなたの判断待ち")
        self.assertIsNone(v["receipt"])                                     # 前の受領（「別の仕入先を当たります」）を下書きの横に残さない
        dr = next(a for a in v["actions"] if a.get("kind") == "primary")
        c = db.connect("amiya")
        try:
            consult.decide_draft(c, int(dr["api"]["body"]["draft_id"]), "approve", "staff")
        finally:
            c.close()
        self.run_all()
        v = self.v()
        self.assertEqual(v["exec"], "idle")
        # 返事を待つ照会も自社の掲示も無い：行き止まり。任せてよいと言わず、当たり直しを人に決めてもらう（2026-11-05 残りの 5）
        self.assertEqual(v["headline"]["text"], "あと 2本足りないままです。いまは、どこにも当たっていません。")
        self.assertIn(f"{SUPER}への連絡は、あなたから送ってください。Agent Dock は売り先に送りません。", [x["text"] for x in v["sub"]])
        self.assertEqual([a["id"] for a in v["actions"]], ["retry"])
        self.assertEqual(v["receipt"]["text"], f"受け取りました。{SUPER}への連絡の下書きを使うと決めました。")
        self.assertEqual(v["receipt"]["at_shown"], mate.shown_time(v["receipt"]["at"], mate.facts("amiya", "buri_xl", D)["clock"]))   # 模擬の時計で見せる
        self.assertEqual({x["name"]: x["note"] for x in v["band"]["steps"]}["不足分の手配"], "あと 2本不足・当たり直しの判断待ち")   # 行き止まり：見出しと同じ事実（2026-11-05 デイブ）
        said = json.dumps(dict(h=v["headline"], s=v["sub"], a=v["ask"], w=v["why"], r=v["receipt"], b=v["band"]), ensure_ascii=False)
        for word in ("そろいました", "終わりました", "全部の注文がそろ", "別の浜を当たります", "別の仕入先を当たります", "ほかに当たれる先を探します"):
            self.assertNotIn(word, said)


class PresidentReturnTest(Base):
    """社長の差し戻し（2026-11-04 デイブ・SAL）：取引の取り消しとは別。理由を添えて戻し、元の案は登録できなくなり、AI が理由に沿って案を組み直す。
    登録済みの取引と、期限の内の仮押さえは残す。"""

    def ask_president(self):
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{RYOTEI} 6本")                            # 順風満帆 6本・魚廣 0本 → 社長に確かめる
        time.sleep(1.05)
        self.assertEqual(self.decide(a, "rt1")["result"], "needs_president")
        return self.v()["president"]

    def send_back(self, p, key="rt3", reason="魚廣の 4本を先に確保して"):
        time.sleep(1.05)
        return server.dispatch("amiya", "/api/decide", dict(case_id=p["case_id"], option_id=p["option_id"], action="return",
                                                            fingerprint=p["fingerprint"], actor="president", reason=reason), key)   # 画面のボタンと同じ道

    def test_only_the_president_returns_and_a_reason_is_required(self):
        p = self.ask_president()
        with self.assertRaises(deal.DealError):                               # 担当者は差し戻せない
            deal.decide("amiya", p["case_id"], p["option_id"], "return", p["fingerprint"], "rt2", actor="staff", reason="r")
        with self.assertRaises(server.Rejected):                              # 理由のない差し戻しは通らない
            self.send_back(p, "rt2b", reason="  ")

    def test_the_return_withdraws_the_option_and_the_ai_rebuilds_the_board(self):
        p = self.ask_president()
        held = self.v()["tally"]["held_total"]
        r = self.send_back(p)
        self.assertEqual((r[0], r[1]["result"], r[1]["reason"]), (200, "returned", "魚廣の 4本を先に確保して"))
        again = deal.decide("amiya", p["case_id"], p["option_id"], "approve", p["fingerprint"], "rt4", actor="president")
        self.assertEqual(again["result"], "stale")                            # 元の案は承認待ちを解いた：そのままでは登録できない
        self.assertEqual(self.v()["tally"]["registered"], 0)
        v = self.v()                                                          # AI が組み直す前
        self.assertEqual((v["exec"], v["stance"], v["rule"]), ("running", "careful", "社長が見直しを求めた"))
        self.assertEqual(v["headline"]["text"], "社長から、この点を見直すよう返事がありました。")
        subs = [s["text"] for s in v["sub"]]
        self.assertEqual(subs[0], "社長の理由：「魚廣の 4本を先に確保して」")
        self.assertTrue(subs[1].startswith("元の案「") and subs[1].endswith("」は承認待ちを解除しました。この案のままでは登録できません。"))
        self.assertEqual(subs[2], "仮押さえ（岩瀬漁協 6本・B社 2本）は、見直した案で使えるよう残しています。")
        self.assertEqual(v["tally"]["held_total"], held)                      # 取り消しではない：仮押さえは手放さない
        self.assertIn("理由：「魚廣の 4本を先に確保して」", v["receipt"]["text"])
        self.assertEqual({x["name"]: x["note"] for x in v["band"]["steps"]}["不足分の手配"], "社長の差し戻しを受けて組み直し中")
        self.run_all()                                                        # AI が理由に沿って案を出し直す
        v = self.v()
        self.assertEqual((v["exec"], v["stance"], v["rule"]), ("your_turn", "careful", "社長が見直しを求めた"))
        self.assertEqual(v["headline"]["text"], "社長から、この点を見直すよう返事がありました。案を組み直しました。")
        self.assertEqual(v["sub"][0]["text"], "社長の理由：「魚廣の 4本を先に確保して」")
        opts = [a for a in v["actions"] if str(a.get("id", "")).startswith("opt")]
        self.assertNotIn(f"opt{p['option_id']}", [a["id"] for a in opts])     # 元の案は出てこない
        self.assertFalse([a for a in opts if a.get("president")])             # 理由に沿う案だけ（指示の売り先を欠く案は出し直さない）
        self.assertEqual({x["name"]: x["note"] for x in v["band"]["steps"]}["振分け・承認"], "見直した案を相談中・あなたの判断待ち")
        rec = next(a for a in opts if a.get("recommended"))
        time.sleep(1.05)
        self.assertEqual(self.decide(rec, "rt5")["result"], "registered")    # 見直した案は、いつもの道で登録まで進む


class ShopNameTest(unittest.TestCase):
    """店の名前は種の1か所（SHOP_NAMES）。社長の指示の言葉の正本は Vault の記録なので、店の名前を変える時は Vault の指示の言葉も直す。
    片方だけ変えると build が止まる（食い違ったまま画面と AI の文に出さない・2026-11-04）。"""

    def test_one_place_changes_the_screen_and_the_ai_text(self):
        import shutil
        from agentdock import vault
        old = sc.SHOP_NAMES["amiya"]["スーパー"]
        sc.SHOP_NAMES["amiya"]["スーパー"] = "試しの商店"
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as vroot:
            os.environ["AGENTDOCK_DATA"] = tmp
            try:
                with self.assertRaises(vault.VaultError) as e:   # Vault の指示の言葉は「魚廣」のまま
                    build.main([])
                self.assertIn("試しの商店", str(e.exception))
                shutil.rmtree(tmp)
                os.makedirs(tmp)
                shutil.copytree(vault.DEFAULT_ROOT, vroot, dirs_exist_ok=True)
                f = next(Path(vroot).rglob("AM-D-20261102-01.md"))
                f.write_bytes(f.read_bytes().decode("utf-8").replace(old, "試しの商店").encode("utf-8"))
                os.environ["AGENTDOCK_VAULT"] = vroot
                self.assertEqual(build.main([]), 0)
                cs = mate.check_sheet("amiya")
                self.assertEqual(cs["directives"][0]["body"], "試しの商店の分は落とすなよ")
                self.assertEqual({o["shop"] for o in cs["orders"]}, {"試しの商店"})
                c = db.connect("amiya")
                try:
                    self.assertIn("customer 試しの商店", disclosure.check(c, "buri_xl", 18000, 4, "試しの商店さん向けです", D))
                finally:
                    c.close()
            finally:
                sc.SHOP_NAMES["amiya"]["スーパー"] = old
                os.environ.pop("AGENTDOCK_DATA", None)
                os.environ.pop("AGENTDOCK_VAULT", None)


class HttpTest(Base):
    def test_mate_and_delegate_over_http(self):
        srv = server.make_server(0)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            m = json.loads(urllib.request.urlopen(f"{base}/api/morning?owner=amiya").read())
            body = dict(owner="amiya", scope=list(sc.DELEGATION_SCOPES), idempotency_key="h1", item="buri_xl",
                        checked=dict(orders=[o["order_id"] for o in m["check"]["orders"]], directives=[d["directive_id"] for d in m["check"]["directives"]]))
            r = json.loads(urllib.request.urlopen(urllib.request.Request(f"{base}/api/delegate", json.dumps(body).encode(), {"Content-Type": "application/json"})).read())
            self.assertEqual((r["result"], r["started"]), ("delegated", "buri_xl"))   # 任せると業務が始まる（注文の出来事で AI が起きる）
            v = json.loads(urllib.request.urlopen(f"{base}/api/mate?owner=amiya&item=buri_xl").read())
            self.assertEqual(v["exec"], "running")
            t = json.loads(urllib.request.urlopen(f"{base}/api/mate?owner=amiya&item=shiroebi").read())
            self.assertEqual(t["exec"], "unconnected")
            bad = dict(body, idempotency_key="h2", checked=dict(orders=[], directives=[]))
            with self.assertRaises(urllib.error.HTTPError) as e:
                urllib.request.urlopen(urllib.request.Request(f"{base}/api/delegate", json.dumps(bad).encode(), {"Content-Type": "application/json"}))
            self.assertEqual(e.exception.code, 409)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()