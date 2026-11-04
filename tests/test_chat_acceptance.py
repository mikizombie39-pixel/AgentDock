"""段2（読み取り専用のチャット）の検収（2026-11-04 SAL の検収項目・模擬の Claude）。

1. 「なぜ魚廣を優先するの？」→ 今有効な社長の指示を参照し、根拠の識別子を返す
   （旧店名「浜見商店」で聞かれたら、記録に無い店として答え、魚廣に読み替えない）
2. 「この配分では何本足りない？」→ 今の業務の状態から答え、過去の履歴（経験の記録）と混同しない
3. Vault に答えの無い質問 → 「記録にはありません」と答え、事実を作らない
4. 網谷水産の利用者は、漁協や B社の非公開の情報を取得できない
5. 質問しても取引の状態は変わらない
本物の Claude の一往復は tools/chat_real_once.py（費用がかかるので試験では呼ばない）。
"""
import json

from agentdock import chat, db, events, vault
from agentdock.seed import scenario as sc
from tests.test_chat import ChatBase, snapshot
from tests.test_mate import RYOTEI, SUPER

D = sc.DEMO_DATE
Q1, Q1_OLD, Q2, Q3 = "なぜ魚廣を優先するの？", "なぜ浜見商店を優先するの？", "この配分では何本足りない？", "順風満帆の大将の好きな酒は？"


def turn_text(turn_id) -> str:
    """その回に Claude へ渡した文脈・道具の結果・取得した記録・答え（全部の文字）。"""
    t = chat.turn("amiya", turn_id)
    return json.dumps(dict(context=t["context"], steps=t["steps"], fetched=t["fetched"], answer=t["answer"]), ensure_ascii=False)


class AcceptanceTest(ChatBase):
    def test_1_why_uohiro_returns_the_current_directive_and_its_id(self):
        self.consult()
        a = self.ask(Q1)["answer"]
        self.assertEqual(a["conclusion"], f"社長から「{SUPER}の分は落とすなよ」と指示されています。今日は{SUPER}の 4本を確保する方針です。")   # 最初は短く
        self.assertEqual(a["next_action"], f"案1なら、{SUPER} 4本を守り、{RYOTEI}にも 4本を回せます。{RYOTEI}は 2本不足します。配分を確認しますか？")
        self.assertEqual(a["deadline"], "仮押さえの期限：B社 07:00・岩瀬漁協 07:00")                        # 期限はコードが別に添える
        self.assertEqual(a["kind"], "question")                                                               # 下見・質問のまま（実行しない）
        refs = [e["ref"] for e in a["evidence"]]
        self.assertEqual(refs[:3], ["directive:1", "AM-R-005", "calc:compare"])                               # 指示 → ルール → 計算 → 注文
        self.assertTrue(all(r.startswith("order_book:") for r in refs[4:]))
        d = a["evidence"][0]
        self.assertEqual(d["refs"], ["directive:1", "AM-D-20261102-01"])                                      # 同じ指示は1つ・識別子は両方
        self.assertEqual(d["validity"], "今有効")
        self.assertNotIn("AM-D-20261102-01", refs)
        self.assertNotIn("AM-D-20261020-01", refs)                                                            # 期限切れの指示を今の根拠にしない
        self.assertEqual(a["interpretation"], "案1は、担当者が選べる案の中で、計算上、不足が最も少ない案です。")

    def test_1c_a_directive_that_differs_from_the_vault_is_not_merged(self):
        self.consult()
        c = db.connect("amiya")
        try:
            with c:
                c.execute("UPDATE directive SET body = '順風満帆の分は落とすなよ' WHERE id = 1")
        finally:
            c.close()
        a = self.ask(Q1)["answer"]
        refs = [e["ref"] for e in a["evidence"]]
        self.assertIn("AM-D-20261102-01", refs)                                                               # 分けたまま
        d = next(e for e in a["evidence"] if e["ref"] == "directive:1")
        self.assertEqual(d["conflict"], "AM-D-20261102-01")
        self.assertNotIn("merged", d)
        self.assertTrue(a.get("conflict"))
        self.assertEqual(a["next_action"], "社長の指示の記録が食い違っています。どちらが正しいか、社長に確かめてください。")

    def test_1b_the_old_shop_name_is_not_mapped_to_uohiro(self):
        self.consult()
        r = self.ask(Q1_OLD)
        a = r["answer"]
        self.assertEqual(a["conclusion"], chat.UNKNOWN)
        self.assertNotIn("directive:1", [e["ref"] for e in a["evidence"]])
        self.assertIn("「浜見商店」は記録にありません", a["detail"])

    def test_2_short_comes_from_the_current_state_not_from_history(self):
        v = self.consult()
        r = self.ask(Q2)
        a = r["answer"]
        self.assertEqual({e["type_label"] for e in a["evidence"]}, {"コードの計算"})        # 今の計算だけ
        self.assertEqual(a["detail"], v["tally"]["line"])                                    # 画面と同じ数
        t = chat.turn("amiya", r["turn_id"])
        self.assertFalse([k for k in t["fetched"] if k.startswith("AM-E-")])                 # 過去の経験の記録は読んでいない
        c = db.connect("amiya")
        try:
            events.order_received(c, "料亭", "buri_xl", 2, 24000, D, key="more2")            # 業務の状態が変わる
        finally:
            c.close()
        self.run_all()
        a2 = self.ask(Q2)["answer"]
        self.assertEqual(a2["detail"], self.v()["tally"]["line"])
        self.assertNotEqual(a2["detail"], a["detail"])                                       # 同じ質問でも、今の状態が変われば答えが変わる
        o1 = next(o for o in self.v()["tally"]["options"] if o["no"] == 1)
        self.assertEqual(o1["note"], f"この案では足りない：{RYOTEI} 4本")                     # 同じ店の注文が2つでも、店ごとに合計する
        self.assertEqual(a2["conclusion"], f"案1の配分だと、{RYOTEI} 4本足りません。")

    def test_3_a_question_with_no_record_is_not_answered_with_made_up_facts(self):
        self.consult()
        for q in (Q3, "代わりの魚ではだめ？"):
            a = self.ask(q)["answer"]
            self.assertEqual(a["conclusion"], chat.UNKNOWN, q)
            self.assertFalse([e for e in a["evidence"] if e["type"] in ("directive", "rule") and q == Q3])

    def test_4_other_companies_private_records_are_not_fetched(self):
        self.consult()
        ids = [self.ask(q)["turn_id"] for q in (Q1, Q2, Q3, "いま漁協との話はどうなっている？", "社長は何と言っていた？")]
        private = [vault.get(o, r["id"])["body"] for o in ("buyer_b", "iwase") for r in vault.index(o, D)]
        self.assertTrue(private)
        m = db.connect(db.MARKET)
        try:
            for tid in ids:
                txt = turn_text(tid)
                for body in private:
                    self.assertNotIn(body, txt)                                    # B社・漁協の Vault の本文
                for n in ("18,200", "18200", "17,900", "17900"):
                    self.assertNotIn(n, txt)                                       # 漁協の売り上げ（B社の仕入れ値・ほかの仲買の値）
                for ref in chat.turn("amiya", tid)["fetched"]:
                    if ref.startswith("network_message:"):
                        f, to = m.execute("SELECT from_owner, to_owner FROM network_message WHERE id = ?", (int(ref.split(":")[1]),)).fetchone()
                        self.assertIn("amiya", (f, to))                            # 自社が当事者のやり取りだけ
                    if ref.startswith("offer_term:"):
                        s, b = m.execute("SELECT seller, buyer FROM offer_term WHERE id = ?", (int(ref.split(":")[1]),)).fetchone()
                        self.assertIn("amiya", (s, b))
        finally:
            m.close()

    def test_5_questions_do_not_change_the_trade(self):
        v0 = self.consult()
        before = snapshot()
        for q in (Q1, Q1_OLD, Q2, Q3):
            self.ask(q)
        self.assertEqual(snapshot(), before)
        v1 = self.v()
        self.assertEqual((v1["stance"], v1["exec"], v1["tally"]["line"]), (v0["stance"], v0["exec"], v0["tally"]["line"]))


    def test_6_a_request_in_step_2_is_not_run_and_says_so(self):
        self.consult()
        before = snapshot()
        a = self.ask("では、その案で買っておいて")["answer"]
        self.assertEqual((a["kind"], a["kind_label"], a["request"]), ("unsupported", "未対応", None))
        self.assertEqual(a["conclusion"], chat.RUN_OFF_CONCLUSION)
        self.assertTrue(a["next_action"].startswith("進める時は、画面の［"))
        self.assertEqual(snapshot(), before)


class SuperlativeTest(ChatBase):
    """「一番」「最も」は、全部の案の比較（calc:compare）と照合する。"""

    def compare(self, best, low=2):
        return dict(rows=[], best=best, low=low)

    def test_without_the_comparison_the_sentence_is_dropped(self):
        txt, why = chat.check_superlative("案1が一番欠けが少ないです。魚廣は守れます。", False, self.compare([1]), "本")
        self.assertEqual((txt, len(why)), ("魚廣は守れます。", 1))

    def test_an_option_that_is_not_the_lowest_is_dropped(self):
        txt, why = chat.check_superlative("案3が最も不足が少ない。", True, self.compare([1]), "本")
        self.assertEqual((txt, len(why)), ("", 1))

    def test_a_tie_names_all_lowest_options(self):
        txt, _ = chat.check_superlative("案1が一番不足が少ない。", True, self.compare([1, 2]), "本")
        self.assertEqual(txt, "担当者が選べる案の中で、計算上、不足が最も少ないのは 案1・案2 です（同じ 2本）。")

    def test_the_unique_lowest_is_said_in_the_code_words(self):
        txt, _ = chat.check_superlative("これが一番いい案です。", True, self.compare([1]), "本")
        self.assertEqual(txt, "案1は、担当者が選べる案の中で、計算上、不足が最も少ない案です。")

    def test_the_comparison_counts_every_option(self):
        self.consult()
        s = chat.work_state("amiya", "buri_xl")
        v = self.v()
        self.assertEqual(len(s["compare"]["rows"]), len([o for o in v["tally"]["options"]]))   # 全部の案を計算に入れる
        staff = [r for r in s["compare"]["rows"] if r["staff"]]
        self.assertEqual(s["compare"]["best"], [r["no"] for r in staff if r["short"] == min(x["short"] for x in staff)])
