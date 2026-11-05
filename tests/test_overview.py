"""全体の流れ（3者）の画面の試験（模擬の AI）：状態は記録から・社長に送った段階は「社長の判断待ち」・連絡は一番新しい1本だけ。"""
import time

from agentdock import overview
from tests import test_trace


class OverviewTest(test_trace.TraceTest):
    test_no_registered_trade_yet = None
    test_one_line_from_the_trade_to_the_stock = None
    test_vault_refs_show_the_file_and_its_fingerprint = None

    def lane(self, v, key):
        return {m["who"]: m for l in v["lanes"] if l["key"] == key for m in l["members"]}

    def test_states_and_the_one_focus(self):
        self.assertIsNone(overview.view()["focus"])
        self.start()
        self.worker.step()                                                          # 網谷水産の AI が照会を送る
        v = overview.view()
        self.assertEqual((v["focus"]["frm"], v["focus"]["to"]), ("amiya", "iwase"))
        self.assertTrue(v["focus"]["text"].startswith("照会：ブリ"))
        self.assertEqual(self.lane(v, "iwase")["集計（コード）"]["state"], "working")   # 照合が動いている
        self.add_ryotei()
        self.run_all()
        a = self.option(self.v(), f"：{test_trace.RYOTEI} 6本")
        time.sleep(1.05)
        self.decide(a, "o1")                                                         # 社長に確認を送る
        v = overview.view()
        pres = self.lane(v, "president")["社長（網谷さん）"]
        self.assertEqual((pres["state"], pres["state_label"]), ("waiting_decision", "社長の判断待ち"))   # 作業中とは出さない
        self.assertEqual((v["focus"]["frm"], v["focus"]["to"], v["focus"]["text"]), ("amiya", "president", "社長に確認を送りました"))
        p = self.v()["president"]
        time.sleep(1.05)
        from agentdock import server
        server.dispatch("amiya", "/api/decide", dict(case_id=p["case_id"], option_id=p["option_id"], action="return", fingerprint=p["fingerprint"],
                                                    actor="president", reason="魚廣の 4本を先に確保して"), "o2")
        v = overview.view()
        self.assertEqual((v["focus"]["frm"], v["focus"]["to"]), ("president", "amiya"))
        self.assertEqual(v["focus"]["text"], "差し戻し：「魚廣の 4本を先に確保して」")
        self.assertEqual(self.lane(v, "president")["社長（網谷さん）"]["state"], "decided")
        self.run_all()
        rec = next(x for x in self.v()["actions"] if str(x.get("id", "")).startswith("opt") and x.get("recommended"))
        time.sleep(1.05)
        self.decide(rec, "o3")
        v = overview.view()
        self.assertEqual((v["focus"]["frm"], v["focus"]["to"], v["focus"]["text"]), ("amiya", "iwase", "取引を市場の帳簿に登録しました"))
