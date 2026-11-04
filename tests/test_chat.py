"""仲間へのチャット（聞く・頼む）の試験（模擬の Claude・本物の Claude は呼ばない）。デイブの §7 の検証を全部確かめる。

- 同じ質問でも、参照する指示や業務の状態が変われば、渡す文脈と根拠と答えが変わる
- Vault にない内容は「記録にはありません。確認が必要です」と答える（順風満帆の代わりの魚の好み）
- 質問と下見では、交渉・購入・登録が起きない（DB の前後を比べる）
- 担当者のチャットでは、社長の指示や権限を越えられない
- チャットからの実行も、既存のボタンと同じサーバーの検証を通る
- 相手の AI に社内の非公開情報が漏れない（チャットは何も送らない・道具は自社の Vault だけ）
- サーバーの検査：取得していない根拠は捨てる・記録に無い引用は記録の文に替える・合わない数字は出さない・過去の経験の数字を今の値にしない
- 通信の失敗：入力を残し、同じキーの再送で二重に処理しない
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agentdock import chat, chatmock, db, events, mate, server, vault
from agentdock.companion import MockClient, Reply
from agentdock.seed import build
from agentdock.seed import scenario as sc
from tests import test_mate as tm

D = sc.DEMO_DATE
SUPER, RYOTEI = tm.SUPER, tm.RYOTEI
U = dict(input_tokens=100, output_tokens=50)


def answer_reply(**kw):
    a = dict(kind="question", conclusion="c", next_action="n", detail=None, interpretation=None, evidence=[], request=None)
    a.update(kw)
    return Reply(content=[{"type": "tool_use", "id": f"a{time.time_ns()}", "name": "answer", "input": a}], usage=U)


def call(name, inp):
    return Reply(content=[{"type": "tool_use", "id": f"{name}{time.time_ns()}", "name": name, "input": inp}], usage=U)


def snapshot():
    """全部の DB の行（チャットの記録と費用の記録を除く）。"""
    out = {}
    for code in (*db.OWNER_CODES, db.MARKET):
        c = db.connect(code)
        try:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN ('chat_turn', 'api_usage', 'sqlite_sequence')"):
                out[f"{code}.{t}"] = [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        finally:
            c.close()
    return out


class ChatBase(tm.Base):
    def ask(self, text, key=None, client=None):
        self.client = client or chatmock.ChatPolicy()
        return chat.ask(self.client, "amiya", "buri_xl", text, key or f"k{time.time_ns()}")

    def consult(self):
        """料亭（順風満帆）の追加注文で、AI が相談を出した所（あなたの判断待ち）。"""
        self.start()
        self.worker.step()
        self.add_ryotei()
        self.run_all()
        v = self.v()
        self.assertEqual((v["stance"], v["exec"]), ("careful", "your_turn"))
        return v


class DaveQuestionsTest(ChatBase):
    """デイブの5つの質問（模擬の Claude）：根拠は、この回に取得した記録だけ。"""

    def test_the_five_questions(self):
        self.consult()
        a = self.ask("なぜ魚廣を優先するの？")["answer"]
        self.assertEqual(a["kind"], "question")
        self.assertEqual(a["conclusion"], f"{SUPER}を先にそろえるのは、社長の今朝の指示「{SUPER}の分は落とすなよ」があるためです。")
        self.assertEqual([(e["ref"], e["type_label"]) for e in a["evidence"]],
                         [("directive:1", "社長の指示"), ("AM-D-20261102-01", "社長の指示"), ("AM-R-005", "会社のルール")])
        self.assertEqual(a["evidence"][1]["validity"], "今有効")
        self.assertEqual(a["interpretation"], "指示どおりの分け方なら、あなたが決めてよい決まりです。")   # AI の解釈は事実と分ける

        a = self.ask("社長は何と言っていた？")["answer"]
        self.assertIn(f"「{SUPER}の分は落とすなよ」", a["conclusion"])
        self.assertIn("今週は順風満帆を優先（10月20日〜10月24日）は期限切れで、今は効力がありません。", a["detail"])   # 今の指示と過去の指示の違い
        old = next(e for e in a["evidence"] if e["ref"] == "AM-D-20261020-01")
        self.assertEqual((old["type_label"], old["validity"]), ("今は効力のない記録", "期限切れ"))
        self.assertTrue(any("期限の切れた記録" in n for n in a["notes"]))

        a = self.ask("この配分だと、どこが何本足りない？")["answer"]
        self.assertEqual(a["conclusion"], f"案1の配分だと、{RYOTEI} 2本足りません。")
        self.assertEqual([e["type_label"] for e in a["evidence"]], ["コードの計算", "コードの計算"])
        self.assertEqual(a["detail"], self.v()["tally"]["line"])                    # 数はコードの計算（画面と同じ）

        a = self.ask("代わりの魚ではだめ？")["answer"]
        self.assertEqual(a["conclusion"], chat.UNKNOWN)
        self.assertEqual([e["ref"] for e in a["evidence"]], ["AM-S-011", "AM-S-003"])
        self.assertEqual(a["evidence"][0]["quote"], "代わりの魚の好みは記録していない。")

        a = self.ask("いま漁協との話はどうなっている？")["answer"]
        self.assertEqual(a["conclusion"], "岩瀬漁協が 6本を 1本 17,500円で仮押さえ（期限 07:00）。")
        self.assertIn("相手の回答", [e["type_label"] for e in a["evidence"]])

    def test_the_evidence_carries_the_date_and_the_time_it_was_checked(self):
        self.consult()
        r = self.ask("社長は何と言っていた？")
        e = next(x for x in r["answer"]["evidence"] if x["ref"] == "AM-D-20261102-01")
        self.assertEqual(e["dates"], ["2026-11-02", "2026-11-02", "2026-11-02"])
        self.assertEqual(e["src"]["source"], "社長の電話（模擬）")
        self.assertIn("架空", e["src"]["fictional"])
        self.assertTrue(e["checked_at"].startswith(db.now_iso()[:10]))
        d = next(x for x in r["answer"]["evidence"] if x["ref"] == "directive:1")
        self.assertEqual((d["at"], d["src"]), ("2026-11-02T05:40:00+09:00", dict(db="owner", table="directive", id=1)))


class ContextChangesTest(ChatBase):
    """同じ質問でも、参照する指示や業務の状態が変われば、渡す文脈・根拠・答えが変わる。"""

    def tearDown(self):
        os.environ.pop("AGENTDOCK_VAULT", None)
        super().tearDown()

    def test_the_directive_changes_the_answer(self):
        self.start()
        self.worker.step()
        with_dir = self.ask("なぜ魚廣を優先するの？")
        p1 = self.client.requests[0]["messages"][0]["content"]
        # 社長の指示の記録の適用を前日までにした Vault で作り直す（今日の指示が無い朝）
        vroot = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, vroot, True)
        shutil.copytree(vault.DEFAULT_ROOT, vroot, dirs_exist_ok=True)
        f = next(Path(vroot).rglob("AM-D-20261102-01.md"))
        f.write_bytes(f.read_bytes().decode("utf-8").replace("valid_to: 2026-11-02", "valid_to: 2026-11-01")
                      .replace("valid_from: 2026-11-02", "valid_from: 2026-11-01").replace("recorded_on: 2026-11-02", "recorded_on: 2026-11-01").encode("utf-8"))
        os.environ["AGENTDOCK_VAULT"] = vroot
        self.assertEqual(build.main([]), 0)
        self.start()
        self.worker.step()
        without = self.ask("なぜ魚廣を優先するの？")
        p2 = self.client.requests[0]["messages"][0]["content"]
        self.assertIn("社長の今日の指示「魚廣の分は落とすなよ」", p1)
        self.assertNotIn("社長の今日の指示", p2)                                         # 渡す文脈が変わる
        a1, a2 = with_dir["answer"], without["answer"]
        self.assertNotEqual(a1["conclusion"], a2["conclusion"])                            # 答えが変わる
        self.assertEqual(a2["conclusion"], "今日は社長の指示の記録がありません。先に受けた注文から確保する決まりで進めています。")
        self.assertIn("AM-R-001", [e["ref"] for e in a2["evidence"]])                      # 根拠が変わる
        self.assertNotIn("directive:1", [e["ref"] for e in a2["evidence"]])

    def test_the_business_state_changes_the_answer(self):
        self.start()
        self.worker.step()
        self.run_all()
        before = self.ask("この配分だと、どこが何本足りない？")["answer"]
        self.add_ryotei()
        self.run_all()
        after = self.ask("この配分だと、どこが何本足りない？")["answer"]
        self.assertNotEqual(before["conclusion"], after["conclusion"])
        opt = next(a for a in self.v()["actions"] if a["id"].startswith("opt"))
        self.assertIn(opt["short_after"][0]["shop"], after["conclusion"])                   # 今の案（コードの計算）の足りない数から答える
        self.assertIn(f"{opt['short_after'][0]['quantity']}本足りません", after["conclusion"])

    def test_the_previous_turns_are_passed(self):
        self.consult()
        self.ask("なぜ魚廣を優先するの？")
        self.ask("社長は何と言っていた？")
        p = self.client.requests[0]["messages"][0]["content"]
        self.assertIn("直前の対話（古い順）：\n- 担当者：なぜ魚廣を優先するの？", p)


class NoSideEffectTest(ChatBase):
    def test_questions_and_previews_do_not_change_the_business(self):
        v0 = self.consult()
        before = snapshot()
        for q in ("なぜ魚廣を優先するの？", "社長は何と言っていた？", "この配分だと、どこが何本足りない？", "代わりの魚ではだめ？",
                  "いま漁協との話はどうなっている？", "順風満帆を優先にするとどうなる？", "その案で進めて", "B社に電話して"):
            self.ask(q)
        self.assertEqual(snapshot(), before)                                               # 交渉・購入・登録・態度の元の記録は変わらない
        v1 = self.v()
        self.assertEqual((v1["stance"], v1["exec"], v1["headline"]), (v0["stance"], v0["exec"], v0["headline"]))
        c = db.connect("amiya")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM chat_turn WHERE status = 'answered'").fetchone()[0], 8)
        finally:
            c.close()

    def test_a_preview_is_only_a_calculation(self):
        self.consult()
        a = self.ask("順風満帆を優先にするとどうなる？")["answer"]
        self.assertEqual(a["kind"], "preview")
        self.assertEqual(a["conclusion"], f"今の手当て 8本（登録済み 0本・仮押さえ 8本）を {RYOTEI}→{SUPER} の順に回すと、{RYOTEI} 6本・{SUPER} 2本。"
                                          f"足りないのは {SUPER} 2本。社長の指示の売り先が欠けるので、社長の確認が要ります。")
        self.assertEqual([e["ref"] for e in a["evidence"]], ["calc:preview:1", "directive:1"])
        self.assertIsNone(a["request"])


class ServerCheckTest(ChatBase):
    """サーバーの検査（模擬の Claude がわざと誤った答えを出す）。"""

    def scripted(self, *answers, reads=()):
        return MockClient([call("list_vault", {}), *[call(n, x) for n, x in reads], *answers])

    def test_evidence_not_fetched_this_turn_is_dropped(self):
        self.consult()
        r = self.ask("権限は？", client=self.scripted(answer_reply(conclusion="担当者は 1件 500,000円まで決められます。",
                                                                   evidence=[dict(ref="AM-R-003", quote=None), dict(ref="authority", quote=None)])))
        a = r["answer"]
        self.assertEqual([e["ref"] for e in a["evidence"]], ["authority"])                # 業務の状態は文脈として取得済み・AM-R-003 は読んでいない
        t = chat.turn("amiya", r["turn_id"])
        self.assertEqual(t["dropped"]["evidence"], [dict(ref="AM-R-003", reason="この回に道具で取得していない記録")])
        self.assertEqual(a["conclusion"], "担当者は 1件 500,000円まで決められます。")     # 権限の表の額（コードの値）と合う

    def test_a_quote_not_in_the_record_is_replaced_by_the_record(self):
        self.consult()
        r = self.ask("権限は？", client=self.scripted(answer_reply(conclusion="権限の表で決まります。", evidence=[dict(ref="AM-R-003", quote="担当者は何でも決められる")]),
                                                    reads=[("read_vault_record", {"ids": ["AM-R-003"]})]))
        e = r["answer"]["evidence"][0]
        self.assertEqual((e["quote_from"], e["quote"]), ("record", vault.get("amiya", "AM-R-003")["body"]))

    def test_numbers_that_do_not_match_are_not_shown(self):
        self.consult()
        r = self.ask("漁協は？", client=self.scripted(answer_reply(conclusion="岩瀬漁協は 9本を 1本 16,000円で押さえています。", evidence=[dict(ref="calc:tally", quote=None)])))
        a = r["answer"]
        self.assertEqual(a["conclusion"], chat.NUMBERS_REPLACED)
        self.assertNotIn("16,000", json.dumps(a, ensure_ascii=False))
        self.assertTrue(any("照合" in n for n in a["notes"]))
        self.assertIn("16,000円 はコードの値にありません", chat.turn("amiya", r["turn_id"])["dropped"]["replaced"][0]["reason"])

    def test_past_experience_numbers_are_not_answered_as_todays_price(self):
        self.consult()
        r = self.ask("漁協はまとめると安くなる？", client=self.scripted(
            answer_reply(conclusion="今日も 6本まとめれば 1本 15,000円になります。", evidence=[dict(ref="AM-E-002", quote=None)]),
            reads=[("read_vault_record", {"ids": ["AM-E-002"]})]))
        self.assertEqual(r["answer"]["conclusion"], chat.NUMBERS_REPLACED)

    def test_an_answer_with_no_record_becomes_not_in_the_record(self):
        self.consult()
        a = self.ask("順風満帆は鯛でもいい？", client=self.scripted(answer_reply(conclusion="順風満帆は鯛でも喜びます。", evidence=[dict(ref="AM-X-1", quote=None)])))["answer"]
        self.assertEqual((a["conclusion"], a["evidence"]), (chat.UNKNOWN, []))

    def test_other_company_records_cannot_be_read(self):
        self.consult()
        r = self.ask("B社の決まりは？", client=self.scripted(answer_reply(conclusion=chat.UNKNOWN, evidence=[dict(ref="BB-R-001", quote=None)]),
                                                            reads=[("read_vault_record", {"ids": ["BB-R-001", "IW-R-001"]})]))
        t = chat.turn("amiya", r["turn_id"])
        step = next(s for s in t["steps"] if s["tool"] == "read_vault_record")
        self.assertEqual(json.loads(step["result"])["records"], [])
        self.assertEqual(json.loads(step["result"])["not_in_own_vault"], ["BB-R-001", "IW-R-001"])
        self.assertEqual(r["answer"]["evidence"], [])


class RequestTest(ChatBase):
    def run_turn(self, turn_id, key):
        return self.http("POST", "/api/chat/run", dict(owner="amiya", turn_id=turn_id, idempotency_key=key))

    def setUp(self):
        super().setUp()
        self.srv = server.make_server(0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        server.CHAT, server.CHAT_KIND = chatmock.ChatPolicy(), "mock"
        server.CHAT_RUN = True          # 段4 の実行の経路を確かめる（既定は止めてある）

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        server.CHAT, server.CHAT_RUN = None, False
        super().tearDown()

    def http(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"}, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_a_request_is_confirmed_and_runs_only_when_pressed_with_the_button_checks(self):
        self.consult()
        st, r = self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text="その案で進めて", idempotency_key="c1"))
        self.assertEqual(st, 200)
        req = r["answer"]["request"]
        button = next(a for a in self.v()["actions"] if a.get("recommended"))
        self.assertEqual((req["action_id"], req["api"]), (button["id"], button["api"]))       # ボタンと同じ処理・同じ中身
        self.assertEqual(r["answer"]["kind"], "request")
        self.assertEqual(snapshot()["amiya.proposal"], [])                                    # 確認の文だけ。まだ何も起きない
        st, out = self.run_turn(r["turn_id"], "x1")
        self.assertEqual((st, out["result"], out["via"]), (200, "registered", "chat"))
        st, again = self.run_turn(r["turn_id"], "x1")                                         # 同じキーの再送：二重に実行しない
        self.assertEqual((st, again["repeated"]), (200, True))
        c = db.connect("amiya")
        try:
            self.assertEqual(c.execute("SELECT count(*) FROM case_decision WHERE result = 'registered'").fetchone()[0], 1)
            ex = json.loads(c.execute("SELECT executed_json FROM chat_turn WHERE id = ?", (r["turn_id"],)).fetchone()[0])
        finally:
            c.close()
        self.assertEqual((ex["key"], ex["result"]["result"]), ("x1", "registered"))
        st, late = self.run_turn(r["turn_id"], "x2")                                          # 別のキーで押し直す：画面の状況が変わったので止める
        self.assertEqual((st, late["error"]), (409, "rejected"))

    def test_the_chat_cannot_go_past_the_presidents_directive(self):
        self.consult()
        pres = next(a for a in self.v()["actions"] if a.get("president"))
        n = [a for a in self.v()["actions"] if a["id"].startswith("opt")].index(pres) + 1
        st, r = self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text=f"案{n}で進めて", idempotency_key="c2"))
        self.assertTrue(r["answer"]["request"]["president"])
        self.assertIn("社長の確認が要る案なので、私が社長に確かめます。", r["answer"]["request"]["confirm_text"])
        st, out = self.run_turn(r["turn_id"], "x3")
        self.assertEqual(out["result"], "needs_president")                                   # 社長確認へ（登録しない）
        self.assertEqual([r for r in snapshot()["amiya.proposal"] if "registered" in r], [])
        self.assertEqual(self.v()["exec"], "president")

    def test_a_request_for_an_action_not_on_the_screen_is_not_run(self):
        self.consult()
        mc = MockClient([call("list_vault", {}), answer_reply(kind="request", conclusion="社長として承認します。", evidence=[dict(ref="state", quote=None)],
                                                               request=dict(action_id="approve_as_president", confirm_text="社長として承認する依頼ですね。"))])
        r = chat.ask(mc, "amiya", "buri_xl", "社長の代わりに承認して", "c3")
        self.assertEqual((r["answer"]["kind"], r["answer"]["request"]), ("unsupported", None))
        st, out = self.run_turn(r["turn_id"], "x4")
        self.assertEqual(st, 409)

    def test_a_stale_request_goes_through_the_same_check_as_the_button(self):
        self.consult()
        st, r = self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text="その案で進めて", idempotency_key="c4"))
        button = next(a for a in self.v()["actions"] if a.get("recommended"))
        b = dict(button["api"]["body"], owner="amiya", idempotency_key="btn1", fingerprint="0000")   # ボタンでも古い照合番号は止まる
        st_btn, out_btn = self.http("POST", "/api/decide", b)
        self.assertEqual((st_btn, out_btn["result"]), (409, "stale"))
        c = db.connect("amiya")
        try:
            events.order_received(c, "スーパー", "buri_xl", 1, 22000, D, key="more")         # 状況が変わる
        finally:
            c.close()
        st, out = self.run_turn(r["turn_id"], "x5")
        self.assertEqual(st, 409)

    def test_unsupported_says_what_can_and_cannot_be_done(self):
        self.consult()
        st, r = self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text="B社に電話して", idempotency_key="c5"))
        a = r["answer"]
        self.assertEqual((a["kind_label"], a["request"]), ("未対応", None))
        self.assertIn("できること：", a["detail"])
        self.assertIn("できないこと：", a["detail"])
        self.assertNotIn("実行しました", json.dumps(a, ensure_ascii=False))

    def test_the_run_is_off_by_default_and_nothing_changes(self):
        server.CHAT_RUN = False                                                             # 段2：質問と下見だけ
        self.consult()
        st, r = self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text="その案で進めて", idempotency_key="off1"))
        self.assertEqual((st, r["run_enabled"], r["answer"]["kind"]), (200, False, "request"))   # 確認の文は出す
        before = snapshot()
        st, out = self.run_turn(r["turn_id"], "offx")
        self.assertEqual((st, out["error"]), (403, "not_enabled"))
        self.assertEqual(snapshot(), before)
        st, h = self.http("GET", "/api/chat?owner=amiya&item=buri_xl")
        self.assertFalse(h["run_enabled"])
        self.assertIsNone(chat.turn("amiya", r["turn_id"])["executed"])

    def test_history_and_turn_record_over_http(self):
        self.consult()
        self.http("POST", "/api/chat", dict(owner="amiya", item="buri_xl", text="社長は何と言っていた？", idempotency_key="c6"))
        st, h = self.http("GET", "/api/chat?owner=amiya&item=buri_xl")
        self.assertEqual((st, h["run_kind"], h["turns"][0]["question"]), (200, "mock", "社長は何と言っていた？"))
        st, t = self.http("GET", f"/api/chat/turn?owner=amiya&id={h['turns'][0]['id']}")
        self.assertIn("AM-D-20261102-01", t["fetched"])
        self.assertIn("担当者の言葉：社長は何と言っていた？", t["context"]["prompt"])
        self.assertEqual([s["tool"] for s in t["steps"]], ["list_vault", "read_work_state", "read_vault_record", "answer"])


class FailureTest(ChatBase):
    def test_a_failure_keeps_the_input_and_the_same_key_does_not_run_twice(self):
        self.consult()

        class Down:
            def create(self, **kw):
                raise ConnectionError("network down")
        with self.assertRaises(chat.ChatFailed):
            chat.ask(Down(), "amiya", "buri_xl", "社長は何と言っていた？", "same")
        c = db.connect("amiya")
        try:
            row = c.execute("SELECT question, status, error FROM chat_turn WHERE idempotency_key = 'same'").fetchone()
        finally:
            c.close()
        self.assertEqual((row[0], row[1]), ("社長は何と言っていた？", "failed"))
        self.assertIn("network down", row[2])
        ok = chatmock.ChatPolicy()
        r1 = chat.ask(ok, "amiya", "buri_xl", "社長は何と言っていた？", "same")              # 同じキーで送り直す
        n = len(ok.requests)
        r2 = chat.ask(ok, "amiya", "buri_xl", "社長は何と言っていた？", "same")              # 答えの後の再送：前の答え・AI を呼ばない
        self.assertEqual((r1["repeated"], r2["repeated"], len(ok.requests)), (False, True, n))
        self.assertEqual(r1["answer"], r2["answer"])
        with self.assertRaises(chat.ChatError):
            chat.ask(ok, "amiya", "buri_xl", "別の質問", "same")


class RealClaudeWiringTest(ChatBase):
    """本物の Claude に渡す中身（道具と構造化の出力）を、呼ばずに確かめる。"""

    def test_tools_and_the_answer_schema(self):
        names = [t["name"] for t in chat.schemas()]
        self.assertEqual(names, ["list_vault", "read_vault_record", "read_work_state", "preview_allocation", "answer"])
        for t in chat.schemas():
            self.assertEqual(t["input_schema"]["type"], "object")
            self.assertFalse(t["input_schema"]["additionalProperties"])
            self.assertEqual(sorted(t["input_schema"]["required"]), sorted(t["input_schema"]["properties"]))
        ans = next(t for t in chat.schemas() if t["name"] == "answer")["input_schema"]["properties"]
        self.assertEqual(ans["kind"]["enum"], ["question", "preview", "request", "unsupported"])
        self.assertEqual(sorted(ans["evidence"]["items"]["required"]), ["quote", "ref"])
        self.assertNotIn("send", " ".join(names))                                          # 送る・登録する道具は無い

    def test_the_request_to_claude(self):
        self.consult()
        p = chatmock.ChatPolicy()
        chat.ask(p, "amiya", "buri_xl", "社長は何と言っていた？", "w1")
        first, second = p.requests[0], p.requests[1]
        self.assertEqual(set(first), {"system", "tools", "messages"})                      # ClaudeClient.create(system, tools, messages) と同じ形
        self.assertIn("読むだけ", first["system"])
        self.assertIn("記録にはありません。確認が必要です。", first["system"])
        res = second["messages"][-1]
        self.assertEqual(res["role"], "user")
        self.assertEqual({b["type"] for b in res["content"]}, {"tool_result"})
        self.assertEqual(chat.EFFORT, "medium")


if __name__ == "__main__":
    unittest.main()
