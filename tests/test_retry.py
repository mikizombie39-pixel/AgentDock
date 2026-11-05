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
