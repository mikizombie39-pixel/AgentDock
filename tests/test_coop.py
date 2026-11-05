"""岩瀬漁協の AI の画面（読むだけ・2026-11-05 SAL の方針）の試験。模擬の AI で撮影の分岐を通す（本物の Claude は呼ばない）。
行はすべて漁協の DB と市場の記録から作る：せりの結果の公開と照合番号の検査・資料の配信と照合番号の検査・入札（未接続）・照会への回答・取引と在庫。
"""
import json

from agentdock import coop
from tests import test_trace   # モジュールで読む（TraceTest の試験を、ここでもう一度流さない）


class CoopViewTest(test_trace.TraceTest):
    test_no_registered_trade_yet = None
    test_one_line_from_the_trade_to_the_stock = None
    test_vault_refs_show_the_file_and_its_fingerprint = None

    def test_before_the_day_starts_there_is_nothing_to_show(self):
        v = coop.view()
        self.assertEqual(v["rows"], [])
        self.assertEqual([(x["item"], x["quantity"], x["unit"]) for x in v["landing"]], [("ブリ（特大）", 12, "本"), ("シロエビ", 20, "箱"), ("アジ", 15, "kg")])

    def test_the_coops_work_in_time_order_from_the_records(self):
        self.register_after_return()
        v = coop.view()
        works = [r["work"] for r in v["rows"]]
        self.assertEqual(works[:5], ["せりの結果の公開", "せりの結果の公開", "資料の配信", "資料の配信", "入札"])
        checks = [r for r in v["rows"] if r["kind"] == "check"]
        self.assertEqual([r["ok"] for r in checks], [True, True])                         # 全員に同じ結果・同じ資料（照合番号の検査）
        self.assertEqual(len({l.split("：")[1] for l in checks[0]["lines"]}), 1)           # 5か所の照合番号が同じ
        (t,) = [r for r in v["rows"] if r["work"] == "入札"]
        self.assertEqual((t["title"], t["connected"]), ("シロエビの入札：入札の受付は未接続です", False))
        quote = [r["title"] for r in v["rows"] if r["work"] == "照会への回答"]
        self.assertIn("網谷水産から問い合わせ：ブリ（特大） 4本 × 17,500円", quote)
        self.assertIn("網谷水産へ条件の返事", quote)
        self.assertIn("網谷水産へ断りの返事", quote)
        self.assertIn("仮押さえ 1：網谷水産に ブリ（特大） 6本 × 17,500円 → 取引で確定", quote)
        runs = [r for r in v["rows"] if r.get("steps")]
        self.assertTrue(runs and all(r["origin"] == "mock" for r in runs))
        self.assertIn("在庫を読んだ結果：出せる数 6・自社の最低の値 17,000円（漁協の画面にだけ表示）", runs[0]["lines"])
        trade = [r["title"] for r in v["rows"] if r["work"] == "取引と在庫"]
        self.assertEqual(trade, ["取引 case1-buy-2：網谷水産へ ブリ（特大） 6本 × 17,500円", "在庫 ブリ（特大） 6本：売れました（取引 case1-buy-2）"])
        self.assertEqual(v["for_sale"], [dict(item="アジ", quantity=15, unit="kg")])
        self.assertTrue(all(r["at_shown"] for r in v["rows"]))                            # 時刻は仲間の画面と同じ模擬の時計
        self.assertEqual(v["ai_origins"], ["模擬の AI"])

    def test_the_coops_floor_stays_off_the_buyers_screens(self):
        self.register_after_return()
        from agentdock import mate, work
        for data in (mate.view("amiya", "buri_xl"), work.view("amiya", "buri_xl"), work.current("amiya", "buri_xl")):
            self.assertNotIn("17,000円", json.dumps(data, ensure_ascii=False))           # 漁協の最低の値は、仲買の画面に出さない
            self.assertNotIn('"floor_yen": 17000', json.dumps(data, ensure_ascii=False))
