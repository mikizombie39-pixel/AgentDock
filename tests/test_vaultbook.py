"""帳面の事例の記録と、次の判断の材料に出ることの試験。"""
import os
import tempfile
import unittest

from agentdock import briefing, consult, db, vaultbook
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE
OPTS = [("prefer_restaurant", "料亭の分を優先する", True), ("pass", "見送る", True)]


class VaultbookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)
        self.con = db.connect("amiya")
        self.task = consult.open_task(self.con, "negotiation", "buri_xl")
        gid = consult.bundle(self.con, "ブリ", [dict(task_id=self.task, question="料亭の分で上限を上げますか", options=OPTS)])
        self.cid = consult.card(self.con, gid)["items"][0]["consultation_id"]

    def tearDown(self):
        self.con.close()
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_unanswered_is_not_recorded(self):
        with self.assertRaises(vaultbook.VaultError):
            vaultbook.record_from_consultation(self.con, self.cid)
        self.assertEqual(self.con.execute("SELECT count(*) FROM vault_case").fetchone()[0], 0)

    def test_answer_becomes_a_case_and_appears_in_briefing(self):
        consult.answer(self.con, self.cid, "prefer_restaurant", "k1", "料亭は長い付き合い。年末の分を逃したくない")
        path = vaultbook.record_from_consultation(self.con, self.cid)
        self.assertTrue(path.exists())
        self.assertIn(str(vaultbook.folder("amiya").resolve()), str(path))
        text = path.read_text(encoding="utf-8")
        self.assertIn("網谷さんの選択：料亭の分を優先する", text)
        self.assertIn("理由：料亭は長い付き合い", text)
        with self.assertRaises(vaultbook.VaultError):
            vaultbook.record_from_consultation(self.con, self.cid)            # 同じ相談を二度は残さない
        cases = vaultbook.find(self.con, "buri_xl")
        self.assertEqual([(c["choice"], c["missing"]) for c in cases], [("prefer_restaurant", False)])
        m = briefing.build(self.con, "buri_xl", "negotiation", D)
        self.assertIn("帳面の事例", briefing.render(m))
        self.assertIn("prefer_restaurant（理由：料亭は長い付き合い", briefing.render(m))
        self.assertEqual(vaultbook.find(self.con, "aji_s"), [])

    def test_missing_file_is_reported(self):
        consult.answer(self.con, self.cid, "pass", "k1")
        path = vaultbook.record_from_consultation(self.con, self.cid)
        path.unlink()
        self.assertTrue(vaultbook.find(self.con, "buri_xl")[0]["missing"])
        self.assertIn("本文のファイルが見つからない", briefing.render(briefing.build(self.con, "buri_xl", "negotiation", D)))

    def test_rebuild_moves_the_vault_aside_and_deletes_nothing(self):
        consult.answer(self.con, self.cid, "pass", "k1", "今日は見送る")
        old = vaultbook.record_from_consultation(self.con, self.cid)
        self.con.close()
        self.assertEqual(build.main([]), 0)
        self.con = db.connect("amiya")
        self.assertFalse(old.exists())
        moved = list(db.data_dir().glob("vault-退避-*/amiya/*.md"))
        self.assertEqual([m.name for m in moved], [old.name])                 # 消えずに脇へ移った
        self.assertIn("今日は見送る", moved[0].read_text(encoding="utf-8"))
        self.assertFalse(vaultbook.folder("amiya").exists())                  # 新しい置き場は空から
        self.assertEqual(vaultbook.find(self.con, "buri_xl"), [])
        self.con.close()
        self.assertEqual(build.main([]), 0)                                    # 帳面が無い時の作り直しも通る
        self.con = db.connect("amiya")
        self.assertEqual(len(list(db.data_dir().glob("vault-退避-*"))), 1)

    def test_folder_is_per_owner(self):
        self.assertNotEqual(vaultbook.folder("amiya"), vaultbook.folder("buyer_c"))
        with self.assertRaises(ValueError):
            vaultbook.folder("../market")


if __name__ == "__main__":
    unittest.main()
