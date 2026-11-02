"""仲間の処理の枠の試験（模擬の仲間で回す。Claude は呼ばない）。"""
import os
import tempfile
import unittest
from pathlib import Path

from agentdock import companion, consult, db, market, plans
from agentdock.companion import Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
EV = [{"kind": "price_history", "ref_id": None, "summary": "直近30日の中央値 17,800円"}]
U = dict(input_tokens=2000, output_tokens=300)


def offer(price, qty=4, msg=None, i="t1"):
    return Reply(content=[{"type": "tool_use", "id": i, "name": "make_offer",
                           "input": dict(unit_price_yen=price, quantity=qty, message=msg or f"1本 {price:,}円で{qty}本、いかがでしょうか。",
                                         rationale="相場の中央値の少し下", evidence=EV)}], usage=U)


class CompanionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        market.open_lot("N-buri_xl-1", "buri_xl", 6, "iwase", "coop", "negotiation", D)
        self.con = db.connect("amiya")
        plans.approve(self.con, "buri_xl", "negotiation", D, 18500, 4)
        self.task = consult.open_task(self.con, "negotiation", "buri_xl")
        self.nid = market.open_negotiation("N-buri_xl-1", "amiya")

    def tearDown(self):
        self.con.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def ctx(self):
        return companion.Context(owner="amiya", task_id=self.task, item_code="buri_xl", on_date=D, negotiation_id=self.nid)

    def n(self, sql):
        return self.con.execute(sql).fetchone()[0]

    def go(self, *replies):
        client = companion.MockClient(list(replies))
        return companion.run(client, self.con, self.ctx(), "system", "ブリの相対を始めてください"), client

    def test_offer_within_plan_is_sent(self):
        ctx, client = self.go(offer(17500))
        self.assertEqual(ctx.outcome, "offered")
        self.assertEqual(self.n("SELECT count(*) FROM message WHERE passed_disclosure_check = 1"), 1)
        self.assertEqual(market.negotiation(self.nid)["rounds"], 1)
        self.assertEqual(self.n("SELECT count(*) FROM api_usage"), 2)            # 申し出と、最後の返事
        self.assertEqual(self.n("SELECT within_plan FROM decision"), 1)

    def test_leak_is_blocked_then_rewritten(self):
        ctx, client = self.go(offer(17500, msg="18,500円までなら出せます。"), offer(17500, i="t2"))
        self.assertEqual(ctx.outcome, "offered")
        self.assertEqual(self.n("SELECT count(*) FROM message"), 1)
        self.assertEqual(self.n("SELECT count(*) FROM audit_event WHERE event = 'disclosure_blocked'"), 1)
        first_result = client.requests[1]["messages"][-1]["content"][0]
        self.assertTrue(first_result["is_error"])                                  # 仲間に書き直しを求めた
        self.assertEqual(market.negotiation(self.nid)["rounds"], 1)                # 漏れた文は往復に数えない

    def test_outside_plan_goes_to_owner(self):
        ctx, client = self.go(offer(19000), offer(17500, i="t2"))
        self.assertEqual(ctx.outcome, "needs_owner")
        self.assertEqual(len(client.requests), 1)                                  # そこで止まる
        self.assertEqual(self.n("SELECT count(*) FROM message"), 0)
        self.assertEqual(self.n("SELECT action FROM decision"), "escalate")
        self.assertEqual(market.negotiation(self.nid)["rounds"], 0)

    def test_end_is_no_deal_and_cannot_resume(self):
        ctx, _ = self.go(Reply(content=[{"type": "tool_use", "id": "t1", "name": "end_negotiation", "input": {"rationale": "下限が合わない"}}], usage=U))
        self.assertEqual(ctx.outcome, "no_deal")
        self.assertEqual(market.negotiation(self.nid)["status"], "no_deal")
        with self.assertRaises(consult.ConsultError):
            self.go(offer(17500))

    def test_accept_within_plan(self):
        ctx, _ = self.go(Reply(content=[{"type": "tool_use", "id": "t1", "name": "accept",
                                         "input": dict(unit_price_yen=18000, quantity=4, rationale="上限の中", evidence=EV)}], usage=U))
        self.assertEqual(ctx.outcome, "accepted")
        self.assertEqual(consult.task(self.con, self.task)["state"], "awaiting_final_approval")

    def test_unknown_tool_and_refusal(self):
        ctx, client = self.go(Reply(content=[{"type": "tool_use", "id": "t1", "name": "drop_table", "input": {}}], usage=U))
        self.assertTrue(client.requests[1]["messages"][-1]["content"][0]["is_error"])
        ctx, _ = self.go(Reply(content=[], stop_reason="refusal", usage=U))
        self.assertEqual(ctx.outcome, "needs_owner")

    def test_round_limit_ends_in_no_deal(self):
        replies = [offer(17000 + k * 100, i=f"t{k}") for k in range(market.MAX_ROUNDS + 1)]
        client = companion.MockClient(replies)
        ctx = self.ctx()
        for _ in range(market.MAX_ROUNDS + 1):
            ctx.outcome = None
            companion.run(client, self.con, ctx, "s", "続けてください", max_turns=1)
            if ctx.outcome == "no_deal":
                break
        self.assertEqual(ctx.outcome, "no_deal")
        self.assertEqual(self.n("SELECT count(*) FROM message"), market.MAX_ROUNDS)

    def test_waiting_task_cannot_run(self):
        consult.bundle(self.con, "x", [dict(task_id=self.task, question="q", options=[("pass", "見送る", True)])])
        with self.assertRaises(consult.ConsultError):
            self.go(offer(17500))


class ToolsAndKeyTest(unittest.TestCase):
    def test_tools_are_strict(self):
        self.assertEqual([t["name"] for t in companion.TOOLS], ["make_offer", "accept", "end_negotiation"])
        for t in companion.TOOLS:
            s = t["input_schema"]
            self.assertTrue(t["strict"])
            self.assertFalse(s["additionalProperties"])
            self.assertEqual(sorted(s["required"]), sorted(s["properties"]))

    def test_key_is_read_only_from_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "k.txt"
            os.environ["ANTHROPIC_API_KEY"] = "from-env-should-not-be-used"
            try:
                with self.assertRaises(FileNotFoundError):
                    companion.read_key(p)
                p.write_text("  \n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    companion.read_key(p)
                p.write_text("sk-test-123\n", encoding="utf-8")
                self.assertEqual(companion.read_key(p), "sk-test-123")
            finally:
                os.environ.pop("ANTHROPIC_API_KEY", None)
        src = Path(companion.__file__).read_text(encoding="utf-8")
        self.assertNotIn("environ", src)
        self.assertNotIn("ANTHROPIC_API_KEY", src)
        self.assertFalse(str(companion.KEY_FILE).lower().startswith(str(Path(companion.__file__).parents[1]).lower()))


if __name__ == "__main__":
    unittest.main()
