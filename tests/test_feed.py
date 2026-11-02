"""画面へ流す口（読み出しと HTTP の受け口）の試験。"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from agentdock import broadcast, consult, db, disclosure, evidence, feed, market, plans, results, server
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
OTHERS = ("buyer_b", "buyer_c", "B社", "C社", "other")
KEYS = {"item", "unit", "scale", "lastYear", "recent", "computedLimit", "plan", "choice", "book"}


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        iw = db.connect("iwase")
        try:
            broadcast.publish(broadcast.compose(iw, D))
        finally:
            iw.close()
        results.publish(results.today_results())
        a = db.connect("amiya")
        try:
            plans.approve(a, "shiroebi", "tender", D, 4800, 8)
            t = consult.open_task(a, "tender", "shiroebi")
            evidence.record(a, t, "bid", 4300, 5, "むき身の注文の5箱を確実に取る", [("order", 1, "スーパー 5箱")], D)
            self.buri = consult.open_task(a, "negotiation", "buri_xl")
            gid = consult.bundle(a, "ブリ", [dict(task_id=self.buri, question="料亭の分で上限を上げますか",
                                                 options=[("prefer_restaurant", "上げる", True), ("pass", "見送る", True)])])
            self.cid = consult.card(a, gid)["items"][0]["consultation_id"]
            with self.assertRaises(disclosure.DisclosureError):
                disclosure.send(a, t, "shiroebi", 4300, 5, "4,800円までなら", D)
        finally:
            a.close()
        market.open_lot("R-1", "shiroebi", 2, "buyer_c", "buyer", "negotiation", D)
        fp = market.propose("TR-1", "R-1", "amiya", 4500, 2)
        market.approve("TR-1", "buyer_c", fp)
        market.approve("TR-1", "amiya", fp)
        market.reconcile_owner("amiya")

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_state(self):
        s = feed.state("amiya")
        self.assertEqual(s["owner"]["person_name"], "網谷さん")
        self.assertEqual(len(s["open_cards"]), 1)
        self.assertEqual(s["decisions"][0]["action"], "bid")
        self.assertEqual([(x["external_trade_id"], x["side"]) for x in s["ledger"]], [("TR-1", "buy")])
        self.assertEqual(s["reconciliation"][0]["result"], "match")
        self.assertEqual(s["run"]["blocked"]["disclosure_blocked"], 1)

    def test_only_own_data(self):
        for payload in (feed.state("amiya"), feed.events("amiya"), feed.bid_view("amiya", "shiroebi", "tender", D)):
            text = json.dumps(payload, ensure_ascii=False)
            for name in OTHERS:
                self.assertNotIn(name, text)
        self.assertEqual(feed.state("buyer_b")["ledger"], [])
        with self.assertRaises(ValueError):
            feed.state("market")

    def test_events_cursor(self):
        first = feed.events("amiya")
        self.assertTrue(first["events"])
        self.assertNotIn("seed", {e["event"] for e in first["events"]})
        again = feed.events("amiya", since=first["cursor"])
        self.assertEqual(again["events"], [])
        a = db.connect("amiya")
        try:
            consult.answer(a, self.cid, "pass", "k1")
        finally:
            a.close()
        new = feed.events("amiya", since=first["cursor"])
        self.assertEqual([e["event"] for e in new["events"]], ["consultation_answered"])

    def test_bid_view_has_prototype_shape(self):
        v = feed.bid_view("amiya", "shiroebi", "tender", D)
        self.assertTrue(KEYS <= set(v))
        self.assertEqual(v["unit"], "1箱（1kg）")
        self.assertEqual(v["computedLimit"], dict(salePrice=6300, processing=900, profit=600, value=4800))
        self.assertEqual(v["plan"], dict(low=None, limit=4800, boxes=8))
        self.assertEqual((v["choice"]["price"], v["choice"]["boxes"], v["choice"]["withinPlan"]), (4300, 5, True))
        self.assertLess(v["scale"]["min"], v["recent"]["low"])
        self.assertGreater(v["scale"]["max"], v["computedLimit"]["value"])

    def test_tender_view_hides_others_before_close(self):
        market.open_lot("T-1", "shiroebi", 20, "iwase", "coop", "tender", D)
        market.open_tender("T-1", f"{D}T06:00:00+09:00", f"{D}T06:30:00+09:00")
        for who, p, q in sc.TODAY_TENDER["bids"]:
            market.submit_bid("T-1", who, p, q, now=f"{D}T06:10:00+09:00")
        self.assertEqual(feed.tender_view("T-1", "amiya")["bids"], [dict(bidder="amiya", unit_price_yen=4300, quantity=5)])


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        a = db.connect("amiya")
        try:
            self.task = consult.open_task(a, "negotiation", "buri_xl")
            gid = consult.bundle(a, "ブリ", [dict(task_id=self.task, question="q",
                                                 options=[("prefer_restaurant", "上げる", True), ("pass", "見送る", True)])])
            self.cid = consult.card(a, gid)["items"][0]["consultation_id"]
        finally:
            a.close()
        self.srv = server.make_server(0)
        self.th = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.th.start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.th.join(timeout=5)
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def post(self, obj, raw=None):
        data = raw if raw is not None else json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(self.base + "/api/answer", data=data, method="POST", headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_get_routes(self):
        self.assertEqual(self.get("/api/state?owner=amiya")[0], 200)
        self.assertEqual(self.get("/api/events?owner=amiya&since=0")[0], 200)
        self.assertEqual(self.get("/api/state?owner=market")[0], 400)
        self.assertEqual(self.get("/api/state")[0], 400)
        self.assertEqual(self.get("/nope")[0], 404)

    def test_answer_once_and_stale(self):
        body = dict(owner="amiya", consultation_id=self.cid, option="pass", idempotency_key="phone-1", reason="見送る")
        self.assertEqual(self.post(body), (200, dict(consultation_id=self.cid, option="pass", duplicate=False)))
        self.assertEqual(self.post(body)[1]["duplicate"], True)                         # 押し直し
        self.assertEqual(self.post(dict(body, option="prefer_restaurant"))[0], 409)     # 同じ鍵で別の答え
        a = db.connect("amiya")
        try:
            consult.revise(a, self.task, "新しい注文")
            gid = consult.bundle(a, "ブリ2", [dict(task_id=self.task, question="q2", options=[("pass", "見送る", True)])])
            new_cid = consult.card(a, gid)["items"][0]["consultation_id"]
            consult.revise(a, self.task, "さらに新しい注文")
        finally:
            a.close()
        s, r = self.post(dict(owner="amiya", consultation_id=new_cid, option="pass", idempotency_key="phone-2"))
        self.assertEqual((s, r["error"]), (409, "stale"))
        self.assertEqual(self.post(None, raw=b"{bad json")[0], 400)
        self.assertEqual(self.post(dict(body, owner="market", idempotency_key="x"))[0], 400)

    def test_talos_ports_refused(self):
        for p in (8772, 8773, 8774, 8775, 8777):
            with self.assertRaises(ValueError):
                server.make_server(p)
        self.assertEqual(server.main(["--port", "8772"]), 2)


if __name__ == "__main__":
    unittest.main()
