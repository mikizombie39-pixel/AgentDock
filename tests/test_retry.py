"""見送りの後の行き止まり（2026-11-04 本物の Claude の通し）：不足が残り、どこにも当たっていないのに「AI に任せてかまいません」と出た。

  - 足りない数が残り、返事を待つ照会も受付中の自社の掲示も無い時は、そのとおりに言い、人に当たり直しを決めてもらう
  - 「もう一度、仕入れ先を当たってもらう」は、頼んだ記録と AI を起こす出来事を同じトランザクションで書く
  - 当たっている最中・足りない数が無い時は、サーバーが受け付けない（画面が古くても）
"""
import time
import unittest

from agentdock import agents, db, mate, server
from tests import test_mate as tm

D = tm.D


class DeadEndTest(tm.Base):
    def rejected(self):
        """朝の注文（スーパー 4本）で案が出た後、担当者が見送る。"""
        self.start()
        self.worker.step()
        self.run_all()
        rej = next(a for a in self.v()["actions"] if a.get("choice") == "reject")
        self.decide(rej, f"r{time.time_ns()}")
        return self.v()

    def retry(self, key="rt1"):
        return server.dispatch("amiya", "/api/retry", dict(item="buri_xl", role="staff"), key)

    def test_after_a_decline_with_shortage_the_screen_says_nobody_is_being_asked(self):
        v = self.rejected()
        self.assertEqual(v["exec"], "idle")
        self.assertEqual(v["headline"]["text"], "あと 4本足りないままです。いまは、どこにも当たっていません。")
        self.assertNotEqual(v["ask"]["text"], "今は、AI に任せてかまいません。")
        self.assertEqual(v["ask"]["text"], "足りない 4本を、もう一度当たってもらいますか。")
        (a,) = v["actions"]
        self.assertEqual((a["id"], a["label"], a["api"]["path"]), ("retry", "もう一度、仕入れ先を当たってもらう", "/api/retry"))
        self.assertEqual(mate.dead_end("amiya", "buri_xl"), 4)
        # 札と帯も見出しと同じ事実を言う（「見張り中」と言わない・2026-11-05 デイブ）
        self.assertEqual((v["exec_label"], v["who"]), ("あなたの判断待ち", "you"))
        st = {x["name"]: x for x in v["band"]["steps"]}
        self.assertEqual(st["不足分の手配"]["note"], "あと 4本不足・当たり直しの判断待ち")
        self.assertNotIn("見張り中", v["band"]["summary"])

    def test_after_the_retry_a_new_buy_post_is_what_the_screen_says(self):
        """本物の Claude は当たり直しで、ほかの仲買への買いの掲示を出した（2026-11-05）。見出しは古い断りでなく、いま出した掲示を言う。"""
        from agentdock import network
        self.rejected()
        time.sleep(1.05)
        network.post("amiya", "buy", "buri_xl", 4, 1, 18400, "ブリ（特大）を 4本探しています。", D, f"{D}T10:00:00+09:00")
        v = self.v()
        self.assertEqual(v["headline"]["text"], "ほかの仲買に、買いの掲示を出しました（4本・1本 18,400円）。")
        self.assertIn("掲示は 10:00 まで受け付けます。応じる仲買の返事が届いたら、声をかけます。", [x["text"] for x in v["sub"]])
        self.assertTrue(v["why"]["lead"].startswith("当たった相手から確保できなかったため、ほかの仲買に 4本の買いの掲示を出しました。"))
        self.assertIsNone(mate.dead_end("amiya", "buri_xl"))                           # 掲示が受付中：当たっている先がある

    def test_the_reason_for_splitting_says_what_is_split(self):
        """「どう分けるか」は分かりにくい（2026-11-05 デイブ）：注文・確保の見込み・足りない数で、何を何本に分けるかを言う。"""
        f = mate.facts("amiya", "buri_xl")
        tl = dict(total=10, registered=0, held_total=6, left=4, held=[dict(name="岩瀬漁協", quantity=6)])
        f = dict(f, orders=[dict(shop="魚廣", quantity=4), dict(shop="順風満帆", quantity=6)])
        self.assertEqual(mate._split_lead(f, tl), "注文は 10本（魚廣 4本・順風満帆 6本）で、確保の見込みは 6本（岩瀬漁協の仮押さえ 6本）です。"
                                                  "4本足りないので、どの売り先に何本回すかを、あなたが決めます。")

    def test_a_wrong_item_code_is_answered_with_the_codes_to_use(self):
        """本物の Claude が buri_xl を buri_l と書いた（2026-11-04・05 の通しで3回）。誤りの返事に、自社の品目のコードを並べる。"""
        from agentdock.autonomy import ToolError
        c = db.connect("amiya")
        try:
            with self.assertRaises(ToolError) as e:
                agents._item(c, "buri_l")
        finally:
            c.close()
        self.assertIn("品目がありません：buri_l。自社の品目のコードは buri_xl・", str(e.exception))
        self.assertIn("略したり", agents.ITEM["description"])

    def test_an_instruction_outside_the_options_wakes_the_ai_with_it(self):
        """選択肢にない判断ができないのは困る（2026-11-05 デイブ）：C「ほかの指示を出す」は担当者の質問の入口に kind=instruction で届き、AI が指示として読む。"""
        from agentdock import events
        self.start()
        self.worker.step()
        self.run_all()
        v = self.v()
        other = v["actions"][-1]
        self.assertEqual((other["id"], other["letter"], other["api"]["path"], other["api"]["body"]["kind"]), ("other", "B" if len(v["actions"]) == 2 else other["letter"], "/api/ask", "instruction"))
        c = db.connect("amiya")
        try:
            eid = events.emit(c, "staff_question", dict(case_id=other["api"]["body"]["case_id"], text="B社に 1本 18,000円まで下げられないか聞いて", key="k1", kind="instruction"))
            ev = events.get(c, eid)
        finally:
            c.close()
        prompt, _ = agents.situation("amiya", ev, D)
        self.assertIn("担当者から選択肢にない進め方の指示が届きました", prompt)
        self.assertIn("できないことは、その理由と代わりの案を answer_staff で答えてください", prompt)
        v = self.v()                                                                  # AI の順番を待つ間：指示を受けたことを言う
        self.assertEqual(v["headline"]["text"], "あなたの指示を受けて、進め方を組み直しています。")
        self.assertIn("指示：「B社に 1本 18,000円まで下げられないか聞いて」", [x["text"] for x in v["sub"]])
        from agentdock import chat
        self.assertNotIn("other", [a["id"] for a in chat.work_state("amiya", "buri_xl")["actions"]])   # チャットの依頼では選ばない

    def test_sentences_shown_end_with_a_period(self):
        self.assertEqual(mate.end_sentence("順風満帆の追加で足りなくなりました。どう分けますか"), "順風満帆の追加で足りなくなりました。どう分けますか。")
        for t in ("どう分けますか。", "確かめますか？", "社長の理由：「先に確保して」", "（模擬）", ""):
            self.assertEqual(mate.end_sentence(t), t)

    def test_the_retry_wakes_the_ai_and_the_ai_asks_again(self):
        self.rejected()
        status, r = self.retry()
        self.assertEqual((status, r["result"], r["repeated"]), (200, "requested", False))
        c = db.connect("amiya")
        try:
            (rr,) = [tuple(x) for x in c.execute("SELECT short_quantity, requested_by, event_id FROM retry_request")]
            ev = c.execute("SELECT kind, payload_json FROM biz_event WHERE id = ?", (rr[2],)).fetchone()
        finally:
            c.close()
        self.assertEqual(rr[:2], (4, "staff"))
        self.assertEqual(ev["kind"], "human_choice")
        v = self.v()
        self.assertEqual(v["receipt"]["text"], "受け取りました。足りない 4本の仕入れ先を、もう一度当たります。")
        self.assertNotEqual(v["headline"]["text"], "あと 4本足りないままです。いまは、どこにも当たっていません。")   # AI の順番を待っている
        self.run_all()
        c = db.connect(db.MARKET)
        try:
            asked = c.execute("SELECT count(*) FROM network_message WHERE from_owner = 'amiya' AND kind = 'inquiry' AND body LIKE 'もう一度%'").fetchone()[0]
        finally:
            c.close()
        self.assertEqual(asked, 1)                                                    # AI が問い合わせ直した

    def test_the_prompt_for_the_retry_names_what_is_left(self):
        self.rejected()
        _, r = self.retry()
        c = db.connect("amiya")
        try:
            from agentdock import events
            ev = events.get(c, r["event_id"])
        finally:
            c.close()
        prompt, item = agents.situation("amiya", ev, D)
        self.assertEqual(item, "buri_xl")
        self.assertIn("もう一度当たるよう頼みました", prompt)
        self.assertIn("のうち 4本", prompt)                                           # 満たしていない注文の数はコードが書く

    def test_the_same_key_is_not_requested_twice(self):
        self.rejected()
        _, a = self.retry("same")
        _, b = self.retry("same")
        self.assertEqual((b["retry_id"], b["repeated"]), (a["retry_id"], True))
        c = db.connect("amiya")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM retry_request").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT count(*) FROM biz_event WHERE payload_json LIKE '%\"retry\"%'").fetchone()[0], 1)
        finally:
            c.close()

    def test_it_is_refused_while_the_ai_is_asking(self):
        self.rejected()
        self.retry("first")                                                           # 出来事が待っている（AI の順番待ち）
        with self.assertRaises(server.Rejected):
            self.retry("second")
        self.worker.step()                                                            # AI が漁協に問い合わせ直し、返事を待つ間も受け付けない
        self.assertEqual(self.v()["exec"], "running")
        with self.assertRaises(server.Rejected):
            self.retry("third")

    def test_it_is_not_offered_when_nothing_is_short(self):
        self.start()
        self.worker.step()
        self.run_all()
        self.assertIsNone(mate.dead_end("amiya", "buri_xl"))                           # 案が出ている（あなたの判断待ち）：行き止まりではない
        self.assertNotIn("retry", [a["id"] for a in self.v()["actions"]])


if __name__ == "__main__":
    unittest.main()
