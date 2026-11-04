"""会社ごとの Vault（リポジトリの vault/<会社>/<分類>/<識別子>.md）の試験（本物の Claude は呼ばない）。

- 読み込みで frontmatter を検証し、壊れた記録は読み込みで止める
- 有効期間の判定（実演の日 2026-11-02）：今有効／期限切れ／これから有効。過去の経験と今有効な指示・ルールを分けて返す
- 会社ごとの範囲：網谷水産の AI は vault/amiya だけ。相手の AI（漁協・B社）に網谷水産の記録が渡らない
- 社長の指示：Vault の AM-D-20261102-01 から業務 DB の directive を作る。期限切れの AM-D-20261020-01 は入れない
- ルールの文の金額と、種の権限の表が食い違わない（最終の判定は権限の表）
"""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from agentdock import agents, autonomy, db, events, live, mate, mockdock, scene, vault
from agentdock.seed import build
from agentdock.seed import scenario as sc

D = sc.DEMO_DATE


class LoadTest(unittest.TestCase):
    def test_every_company_loads_and_the_counts(self):
        got = {c: [r["id"] for r in vault.load(c)] for c in db.OWNER_CODES}
        self.assertEqual(len(got["amiya"]), 24)
        self.assertEqual(sorted(got["buyer_b"]), ["BB-E-001", "BB-R-001"])
        self.assertEqual(got["iwase"], ["IW-R-001"])
        self.assertEqual(got["buyer_c"], [])
        cats = {}
        for r in vault.load("amiya"):
            cats[r["category"]] = cats.get(r["category"], 0) + 1
        self.assertEqual(cats, {"会社のルール": 6, "社長の指示": 2, "仕入れの知識": 8, "履歴・経験": 8})
        for c in db.OWNER_CODES:
            for r in vault.load(c):
                self.assertTrue(r["id"].startswith(vault.PREFIX[c]))
                self.assertIn("架空", r["fictional"])

    def test_the_vault_numbers_agree_with_the_seed(self):
        """Vault の文の数（規格・単位・ふだんの注文）は種の値と同じ。"""
        k = {r["id"]: r for r in vault.load("amiya")}
        buri = next(x for x in sc.LANDINGS["iwase"] if x[0] == "buri_xl" and x[1] == "actual")
        self.assertIn(buri[3], k["AM-S-001"]["body"])                                  # 特大は 8kg 以上
        ebi = next(x for x in sc.LANDINGS["iwase"] if x[0] == "shiroebi")
        self.assertIn(ebi[3], k["AM-S-002"]["body"])                                   # 1箱 1kg
        q = next(x[2] for x in sc.ORDERS["amiya"] if x[0] == "スーパー" and x[1] == "buri_xl")
        self.assertIn(f"{q}本前後", k["AM-S-010"]["body"])                             # 魚廣の毎朝の注文
        self.assertIn(sc.shop("amiya", "スーパー"), k["AM-D-20261102-01"]["body"])
        self.assertIn("05:40", k["AM-D-20261102-01"]["body"])
        self.assertEqual(k["AM-D-20261102-01"]["received_at"], "05:40")


class BrokenRecordTest(unittest.TestCase):
    GOOD = {"id": "AM-S-099", "company": "amiya", "category": "仕入れの知識", "title": "t", "subject": "s", "source": "模擬",
            "recorded_on": "2026-10-01", "valid_from": "2026-10-01", "valid_to": "", "status": "active", "fictional": "実証用の架空設定"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_VAULT"] = self.tmp.name

    def tearDown(self):
        os.environ.pop("AGENTDOCK_VAULT", None)
        self.tmp.cleanup()

    def put(self, fm=None, body="本文", rel="amiya/仕入れの知識/AM-S-099.md", drop=(), raw=None):
        p = Path(self.tmp.name) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            f = dict(self.GOOD, **(fm or {}))
            raw = "---\n" + "".join(f"{k}: {v}\n" for k, v in f.items() if k not in drop) + "---\n\n" + body + "\n"
        p.write_bytes(raw.encode("utf-8"))

    def broken(self, msg, **kw):
        self.put(**kw)
        with self.assertRaises(vault.VaultError) as e:
            vault.load("amiya")
        self.assertIn(msg, str(e.exception))

    def test_a_good_record_loads(self):
        self.put()
        (r,) = vault.load("amiya")
        self.assertEqual((r["id"], r["valid_to"], r["body"]), ("AM-S-099", None, "本文"))

    def test_broken_records_stop_the_load(self):
        cases = [
            ("欠けている項目", dict(drop=("source",))),
            ("知らない項目", dict(fm={"author": "誰か"})),
            ("ファイルの名前と違います", dict(fm={"id": "AM-S-098"})),
            ("置き場の会社", dict(fm={"company": "buyer_b"})),
            ("置き場の分類", dict(fm={"category": "会社のルール"})),
            ("識別子の形", dict(fm={"id": "AM-X-099"}, rel="amiya/仕入れの知識/AM-X-099.md")),
            ("日付", dict(fm={"valid_from": "11月2日"})),
            ("valid_to が valid_from より前", dict(fm={"valid_to": "2026-09-01"})),
            ("status", dict(fm={"status": "maybe"})),
            ("架空設定の表示", dict(fm={"fictional": "実在"})),
            ("本文が空", dict(body="")),
            ("品目にありません", dict(fm={"item": "maguro"})),
            ("売り先の区分にありません", dict(fm={"customer": "居酒屋"})),
            ("frontmatter", dict(raw="題だけ\n")),
            ("2回あります", dict(raw="---\nid: AM-S-099\nid: AM-S-099\n---\n本文\n")),
            ("指示の項目は社長の指示の記録だけ", dict(fm={"via": "電話"})),
            ("<分類>/<識別子>.md だけ", dict(rel="amiya/AM-S-099.md")),
        ]
        for msg, kw in cases:
            with self.subTest(msg):
                shutil.rmtree(self.tmp.name)
                os.makedirs(self.tmp.name)
                self.broken(msg, **kw)

    def test_a_directive_needs_its_words_and_fields(self):
        d = dict(id="AM-D-20261102-09", category="社長の指示", customer="スーパー", directive_kind="keep_customer", issued_by="president",
                 issued_from="別の浜", via="電話")
        self.broken("「」で囲んだ文", fm=d, body="言葉がない", rel="amiya/社長の指示/AM-D-20261102-09.md")
        shutil.rmtree(self.tmp.name)
        self.broken("権限の表の役割", fm=dict(d, issued_by="clerk"), body="「x」", rel="amiya/社長の指示/AM-D-20261102-09.md")

    def test_a_broken_record_stops_the_seed_build(self):
        with tempfile.TemporaryDirectory() as data:
            os.environ["AGENTDOCK_DATA"] = data
            try:
                shutil.copytree(vault.DEFAULT_ROOT, self.tmp.name, dirs_exist_ok=True)
                self.put(fm={"valid_from": "x"})
                with self.assertRaises(vault.VaultError):
                    build.main([])
            finally:
                os.environ.pop("AGENTDOCK_DATA", None)


class ValidityTest(unittest.TestCase):
    def rec(self, rid):
        return vault.get("amiya", rid)

    def test_today_expired_and_future(self):
        today = self.rec("AM-D-20261102-01")
        self.assertEqual(vault.validity(today, D), "current")
        self.assertEqual(vault.validity(today, "2026-11-03"), "expired")
        self.assertEqual(vault.validity(today, "2026-11-01"), "future")
        old = self.rec("AM-D-20261020-01")
        self.assertEqual(vault.validity(old, D), "expired")
        self.assertEqual(vault.validity(dict(old, status="active"), "2026-10-22"), "current")   # 期間の内なら有効（状態が active の時）

    def test_past_experience_and_current_rules_are_told_apart(self):
        ix = {r["id"]: r for r in vault.index("amiya", D)}
        self.assertEqual(ix["AM-D-20261102-01"]["use"], "directive")
        self.assertEqual(ix["AM-D-20261020-01"]["use"], "not_current")
        self.assertEqual(ix["AM-D-20261020-01"]["validity_label"], "期限切れ")
        self.assertEqual(ix["AM-R-003"]["use"], "rule")
        self.assertEqual(ix["AM-S-011"]["use"], "knowledge")
        self.assertEqual({ix[i]["use"] for i in ix if i.startswith("AM-E-")}, {"experience"})
        self.assertNotIn("body", ix["AM-R-003"])                                          # 索引は本文を持たない
        self.assertEqual([r["id"] for r in vault.directives("amiya", D)], ["AM-D-20261102-01"])
        self.assertEqual([r["id"] for r in vault.directives("amiya", "2026-10-21")], [])   # 状態が expired の記録は期間の内でも入れない

    def test_rules_valid_on_the_demo_day(self):
        """実演の日（2026-11-02）に、会社のルールと仕入れの知識は全部有効（下書きで後の日付だった記録を直した）。"""
        for r in vault.load("amiya") + vault.load("buyer_b") + vault.load("iwase"):
            if r["category"] in ("会社のルール", "仕入れの知識"):
                self.assertEqual(vault.validity(r, D), "current", r["id"])
            self.assertLessEqual(r["recorded_on"], D, r["id"])


class SeedFromVaultTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        os.environ.pop("AGENTDOCK_VAULT", None)
        self.tmp.cleanup()

    def q(self, owner, sql, args=()):
        c = db.connect(owner)
        try:
            return [tuple(r) for r in c.execute(sql, args)]
        finally:
            c.close()

    def test_the_directive_comes_from_the_vault_and_the_expired_one_is_not_active(self):
        rows = self.q("amiya", "SELECT d.vault_id, d.body, c.name, d.item_id, d.issued_from, d.via, d.received_at, d.status FROM directive d "
                               "JOIN customer c ON c.id = d.customer_id")
        self.assertEqual(rows, [("AM-D-20261102-01", "魚廣の分は落とすなよ", "スーパー", None, "別の浜", "電話（模擬）", "2026-11-02T05:40:00+09:00", "active")])
        self.assertEqual(self.q("amiya", "SELECT count(*) FROM directive WHERE vault_id = 'AM-D-20261020-01'"), [(0,)])
        self.assertEqual(self.q("amiya", "SELECT count(*) FROM vault_note WHERE category = '社長の指示'"), [(0,)])
        for o in ("iwase", "buyer_b", "buyer_c"):
            self.assertEqual(self.q(o, "SELECT count(*) FROM directive"), [(0,)])
        self.assertFalse(hasattr(sc, "DIRECTIVES") or hasattr(sc, "VAULT_NOTES"))   # 種のコードに知識を書かない

    def test_each_company_index_has_only_its_own_records(self):
        for o in db.OWNER_CODES:
            ids = [r[0] for r in self.q(o, "SELECT vault_id FROM vault_note")]
            self.assertTrue(all(i.startswith(vault.PREFIX[o]) for i in ids), (o, ids))
            self.assertEqual(set(ids), {r["id"] for r in vault.load(o) if r["category"] != "社長の指示"})

    def test_rule_amounts_agree_with_the_authority_table(self):
        for o in db.OWNER_CODES:
            self.assertEqual(vault.rule_amount_mismatches(o), [], o)
        self.assertIn(500000, vault.rule_amounts(vault.get("amiya", "AM-R-003")))
        self.assertEqual(self.q("amiya", "SELECT max_trade_yen FROM authority WHERE role = 'staff'"), [(500000,)])
        # 食い違えば build が止まる
        with tempfile.TemporaryDirectory() as vroot:
            shutil.copytree(vault.DEFAULT_ROOT, vroot, dirs_exist_ok=True)
            f = next(Path(vroot).rglob("AM-R-003.md"))
            f.write_bytes(f.read_bytes().decode("utf-8").replace("500,000円", "600,000円").encode("utf-8"))
            os.environ["AGENTDOCK_VAULT"] = vroot
            self.assertEqual(vault.rule_amount_mismatches("amiya"), ["AM-R-003 の 600,000円 は権限の表（500,000円）にありません"])
            with self.assertRaises(vault.VaultError):
                build.main([])

    def test_read_vault_tool_is_scoped_and_marks_the_use(self):
        for o, item in (("amiya", "buri_xl"), ("buyer_b", "aji")):
            c = db.connect(o)
            try:
                out = json.loads(agents.read_vault(autonomy.Ctx(owner=o, con=c, run_id=0, event={}, on_date=D), {"item_code": item}))
            finally:
                c.close()
            ids = [n["vault_id"] for n in out["notes"]]
            self.assertTrue(ids and all(i.startswith(vault.PREFIX[o]) for i in ids), ids)
            if o == "amiya":
                uses = {n["vault_id"]: n["use"] for n in out["notes"]}
                self.assertEqual(uses["AM-E-001"], "過去の経験（判断の材料）")
                self.assertEqual(uses["AM-R-003"], "今有効な会社のルール")
        with self.assertRaises(vault.VaultError):
            vault.get("buyer_b", "AM-R-003")
        with self.assertRaises(vault.VaultError):
            vault.get("amiya", "BB-R-001")
        with self.assertRaises(vault.VaultError):
            vault.load("../amiya")


class Recording(mockdock.Policy):
    """模擬の仲間の方針に、受け取った要求（指示の文・状況の報告・道具の結果）を残す。"""

    def __init__(self, owner):
        super().__init__(owner, 0)
        self.seen = []

    def create(self, **params):
        self.seen.append(json.dumps(params, ensure_ascii=False, default=str))
        return super().create(**params)


class CounterpartScopeTest(unittest.TestCase):
    """相手の AI（漁協・B社）に、網谷水産の Vault の記録が渡らない（ブリの通しを模擬の仲間で回して確かめる）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["AGENTDOCK_DATA"] = self.tmp.name
        self.assertEqual(build.main([]), 0)

    def tearDown(self):
        os.environ.pop("AGENTDOCK_DATA", None)
        self.tmp.cleanup()

    def test_no_amiya_record_reaches_the_other_ais(self):
        clients = {o: Recording(o) for o in db.OWNER_CODES}
        w = live.Worker(clients, run_kind="mock")
        c = db.connect("amiya")
        try:
            cs = mate.check_sheet("amiya")
            mate.delegate(c, D, list(sc.DELEGATION_SCOPES), dict(orders=[o["order_id"] for o in cs["orders"]],
                                                                  directives=[d["directive_id"] for d in cs["directives"]]), "k1")
        finally:
            c.close()
        scene.start(item="buri_xl")
        while w.step():
            pass
        c = db.connect("amiya")
        try:
            events.order_received(c, "料亭", "buri_xl", 6, 24000, D)
        finally:
            c.close()
        while w.step():
            pass
        self.assertIsNone(w.error)
        self.assertTrue(clients["iwase"].seen and clients["buyer_b"].seen)
        amiya = vault.load("amiya")
        for o in ("iwase", "buyer_b"):
            text = "\n".join(clients[o].seen)
            self.assertNotIn("AM-", text, o)
            for r in amiya:
                self.assertNotIn(r["body"][:20], text, (o, r["id"]))
                self.assertNotIn(r["title"], text, (o, r["id"]))
            self.assertNotIn(sc.shop("amiya", "スーパー"), text, o)   # 網谷水産の売り先の店の名前も渡らない
        self.assertTrue(any("AM-" in s for s in clients["amiya"].seen))   # 網谷水産の AI は自社の記録を読む


if __name__ == "__main__":
    unittest.main()
