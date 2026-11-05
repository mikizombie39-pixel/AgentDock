"""同じ時に届いた返事は1回の起動でまとめて読み、同じ売り先への下書きは2通にしない（2026-11-04 本物の Claude の通し）。

  - B社と C社の断りが別々の出来事になり、AI が2回起きた。2回目は新しい事実が無いのに案と下書きを出し直した（約 0.5 ドル）
  - 料亭への欠品の下書きが2通（番号 1・2）並び、担当者が両方を承認した
"""
import unittest

from agentdock import agents, consult, db, events, network, scene
from agentdock.companion import MockClient
from tests import test_buri as tb

D = tb.D


def declines():
    """網谷水産が B社と C社に問い合わせ、2社が続けて断る（どちらもまだ誰も読んでいない）。"""
    ids = []
    for who in ("buyer_b", "buyer_c"):
        q = network.send("amiya", who, "inquiry", "buri_xl", "ブリを 4本お譲りいただけますか。", D, 4, 18000, side="buy")
        c = db.connect(who)
        try:
            for e in events.pending(c, ["quote_request"]):   # 相手の側の問い合わせは、この試験では起こさない
                events.claim(c, e["id"], 0)
        finally:
            c.close()
        ids.append(network.send(who, "amiya", "decline", "buri_xl", "本日は余りがありません。", D, in_reply_to=q))
    c = db.connect("amiya")
    try:
        evs = [e for e in events.pending(c, ["quote_received"]) if e["payload"]["message_id"] in ids]
    finally:
        c.close()
    return ids, evs


class OneWakeForRepliesTest(tb.Base):
    def test_replies_that_arrived_together_are_read_in_one_run(self):
        ids, evs = declines()
        self.assertEqual(len(evs), 2)
        cl = MockClient([tb.tools(("wait_for_replies", dict(reason="2社とも断り")))])
        r = agents.run_event(cl, "amiya", evs[0], D)
        rows = tb.q("amiya", f"SELECT status, run_id FROM biz_event WHERE id IN ({evs[0]['id']}, {evs[1]['id']}) ORDER BY id")
        self.assertEqual(rows, [("handled", r["run_id"]), ("handled", r["run_id"])])   # 2件とも、この1回の起動が済みにした
        nxt = scene.next_event()
        self.assertFalse(nxt and nxt[0] == "amiya" and nxt[1]["id"] == evs[1]["id"])     # 2件目で起こし直さない
        self.assertIn(f"C社からも返事が届いています（番号 {ids[1]}・decline）", cl.requests[0]["messages"][0]["content"])   # 2件目の中身も最初の文に入る

    def test_the_prompt_names_the_other_reply(self):
        ids, evs = declines()
        more = agents.same_replies("amiya", evs[0])
        self.assertEqual([e["id"] for e in more], [evs[1]["id"]])
        self.assertIn(f"C社からも返事が届いています（番号 {ids[1]}・decline）", agents._more_lines("amiya", more, D))

    def test_a_failed_run_returns_both_replies_to_waiting(self):
        _, evs = declines()

        class Broken:
            def create(self, **kw):
                raise RuntimeError("通信の失敗")
        r = agents.run_event(Broken(), "amiya", evs[0], D)
        self.assertEqual(r["outcome"], "error")
        rows = tb.q("amiya", f"SELECT status, run_id FROM biz_event WHERE id IN ({evs[0]['id']}, {evs[1]['id']}) ORDER BY id")
        self.assertEqual(rows, [("pending", None), ("pending", None)])

    def test_other_kinds_are_not_folded(self):
        _, evs = declines()
        c = db.connect("amiya")
        try:
            order = events.get(c, events.order_received(c, "料亭", "buri_xl", 1, 24000, D)[1])
        finally:
            c.close()
        self.assertEqual(agents.same_replies("amiya", order), [])


class OneDraftPerCustomerTest(tb.Base):
    def draft(self, customer, msg):
        return tb.tools(("draft_to_customer", dict(customer=customer, item_code="buri_xl", substitute_item_code=None, message=msg, reason="欠品の連絡")))

    def test_a_new_draft_to_the_same_customer_replaces_the_undecided_one(self):
        c = db.connect("amiya")
        try:
            eid = events.emit(c, "trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]))
            ev = events.get(c, eid)
        finally:
            c.close()
        agents.run_event(MockClient([self.draft("料亭", "4本足りない見込みです。"), self.draft("料亭", "4本足りない見込みです（書き直し）。"),
                                     self.draft("スーパー", "そろう見込みです。"), tb.tools(("wait_for_replies", dict(reason="r")))]), "amiya", ev, D)
        rows = tb.q("amiya", "SELECT d.id, c.name, d.status, d.stale_reason FROM customer_draft d JOIN customer c ON c.id = d.customer_id ORDER BY d.id")
        self.assertEqual([(r[1], r[2]) for r in rows], [("料亭", "stale"), ("料亭", "draft"), ("スーパー", "draft")])   # 別の売り先の下書きは残る
        self.assertEqual(rows[0][3], f"新しい下書き（番号 {rows[1][0]}）に書き直したため")
        self.assertIn(f"前の下書き（番号 {rows[0][0]}）は、この下書きに置き換えました",
                      tb.q("amiya", "SELECT result_text FROM agent_step WHERE tool = 'draft_to_customer' ORDER BY id")[1][0])
        c = db.connect("amiya")
        try:
            with self.assertRaises(consult.ConsultError):   # 置き換えた下書きは承認できない
                consult.decide_draft(c, rows[0][0], "approve", "staff")
        finally:
            c.close()

    def test_a_decided_draft_is_left_as_it_is(self):
        c = db.connect("amiya")
        try:
            eid = events.emit(c, "trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]))
            ev = events.get(c, eid)
        finally:
            c.close()
        agents.run_event(MockClient([self.draft("料亭", "一通目"), tb.tools(("wait_for_replies", dict(reason="r")))]), "amiya", ev, D)
        (first,) = tb.q("amiya", "SELECT id FROM customer_draft")
        c = db.connect("amiya")
        try:
            consult.decide_draft(c, first[0], "approve", "staff")
            eid = events.emit(c, "trade_registered", dict(case_id=self.case, item="buri_xl", trades=[]))
            ev = events.get(c, eid)
        finally:
            c.close()
        agents.run_event(MockClient([self.draft("料亭", "二通目"), tb.tools(("wait_for_replies", dict(reason="r")))]), "amiya", ev, D)
        self.assertEqual(tb.q("amiya", "SELECT status FROM customer_draft ORDER BY id"), [("approved",), ("draft",)])   # 伝えると決めた下書きは書き換えない


if __name__ == "__main__":
    unittest.main()
