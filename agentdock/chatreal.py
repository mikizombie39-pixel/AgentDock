"""本物の Claude で、仲間へのチャットを通す（費用がかかる・試験では呼ばない）。

  python -m agentdock.chatreal "なぜ魚廣を優先するの？"     # 1往復
  python -m agentdock.chatreal --set sal2                   # SAL の追加検証（同じ会話で続ける・途中で注文が増える）

- 業務の場面（料亭＝順風満帆の追加注文で、AI が相談を出し、担当者の判断待ち）は、試験と同じ手順で模擬の作業役が作る。
  本物の Claude を呼ぶのはチャットだけ（chat.ask・道具は読むだけ・チャットからの実行は止めたまま）
- 置き場は AgentDock-local の takes の「日時-chat-real」（リポジトリの外）。種の DB はそこに新しく作る
- 各問の前と後で、全部の DB の行（チャットの記録と費用の記録を除く）の SHA-256 と、画面の態度・実行の状態・数を記録し、比べる。
  途中の出来事（注文の追加）は、業務の状態が変わる段として別に記録する
- 記録：record.json（問ごとの質問・答え・根拠・捨てた物・道具の呼び出し・取得した記録・渡した文脈・前後の比較・トークンと費用）と summary.md。
  認証情報は書かない（鍵はファイルから読み、記録には入れない）
"""
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

TAKES = Path(r"C:\Users\mikiz\AgentDock-local\takes")
MAX_CALLS = 8
SETS = {
    "sal2": [
        ("ask", "なぜ魚廣を優先するの？"),
        ("ask", "この配分では何本足りない？"),
        ("event", "順風満帆の追加注文 2本"),
        ("ask", "順風満帆の注文が増えたみたい。今は何本足りない？"),
        ("ask", "では、その案で買っておいて"),
        ("ask", "代わりの魚ではだめ？"),
        ("ask", "なぜ浜見商店を優先するの？"),
        ("ask", "順風満帆の大将の好きな酒は？"),
    ],
}


def _snapshot():
    from agentdock import db
    out = {}
    for code in (*db.OWNER_CODES, db.MARKET):
        c = db.connect(code)
        try:
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT IN ('chat_turn', 'api_usage', 'sqlite_sequence') ORDER BY name"):
                out[f"{code}.{t}"] = [list(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY rowid")]
        finally:
            c.close()
    return out


def _digest(snap) -> str:
    return hashlib.sha256(json.dumps(snap, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _view():
    from agentdock import mate
    v = mate.view("amiya", "buri_xl")
    return dict(stance=v["stance"], exec=v["exec"], headline=(v["headline"] or {}).get("text"), tally=v["tally"]["line"])


def _usage_since(last_id):
    from agentdock import db
    c = db.connect("amiya")
    try:
        rows = [dict(r) for r in c.execute("SELECT id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_micro_usd FROM api_usage "
                                           "WHERE run_kind = 'full_run' AND id > ? ORDER BY id", (last_id,))]
    finally:
        c.close()
    return rows


def _scene(worker):
    from agentdock import db, events, mate, scene
    from agentdock.seed import scenario as sc
    c = db.connect("amiya")
    try:
        cs = mate.check_sheet("amiya")
        mate.delegate(c, sc.DEMO_DATE, list(sc.DELEGATION_SCOPES), dict(orders=[o["order_id"] for o in cs["orders"]],
                                                                         directives=[d["directive_id"] for d in cs["directives"]]), "d1")
    finally:
        c.close()
    scene.start(item="buri_xl")
    worker.step()
    c = db.connect("amiya")
    try:
        events.order_received(c, "料亭", "buri_xl", 6, 24000, sc.DEMO_DATE)
    finally:
        c.close()
    while worker.step():
        pass
    if worker.error:
        raise SystemExit(f"business scene failed: {worker.error}")


def _event(worker, label):
    from agentdock import db, events
    from agentdock.seed import scenario as sc
    c = db.connect("amiya")
    try:
        events.order_received(c, "料亭", "buri_xl", 2, 24000, sc.DEMO_DATE, key="chatreal-more")
    finally:
        c.close()
    while worker.step():
        pass
    if worker.error:
        raise SystemExit(f"event failed: {worker.error}")


def _md_step(i, s):
    a = (s.get("turn") or {}).get("answer") or {}
    L = [f"## {i}. 質問：{s['question']}", "",
         f"- 呼び出し {len(s['usage'])}回・入力 {sum(u['input_tokens'] for u in s['usage']):,}・出力 {sum(u['output_tokens'] for u in s['usage']):,} トークン・{s['cost_usd']} ドル",
         f"- 種類：{a.get('kind_label')}", f"- 結論：{a.get('conclusion')}", f"- 次の行動：{a.get('next_action')}",
         f"- 期限（コード）：{a.get('deadline')}", f"- 詳しい説明：{a.get('detail')}", f"- AI の解釈：{a.get('interpretation')}",
         f"- サーバーの検査：{'／'.join(a.get('notes') or []) or 'なし'}", f"- 押すボタンの案内：{a.get('suggested_action')}", "", "根拠（表示の順）："]
    for e in a.get("evidence") or []:
        L.append(f"- `{'・'.join(e.get('refs') or [e['ref']])}`（{e['type_label']}" + (f"・{e['validity']}" if e.get("validity") else "") +
                 ("・過去" if e.get("past") else "") + ("・食い違い" if e.get("conflict") else "") + f"）引用（{e['quote_from']}）：{e['quote']}")
    L += ["", "道具：" + " → ".join(f"{x['tool']}{json.dumps(x['input'], ensure_ascii=False) if x['input'] else ''}" for x in (s.get("turn") or {}).get("steps") or []),
          "", "捨てた物・替えた文：" + json.dumps((s.get("turn") or {}).get("dropped"), ensure_ascii=False),
          "", f"- 前：{s['before']['sha256'][:16]}…（{s['before']['rows']}行）{s['before']['view']}",
          f"- 後：{s['after']['sha256'][:16]}…（{s['after']['rows']}行）{s['after']['view']}",
          f"- 変わった表：{s['changed_tables'] or 'なし'}" + (f"・エラー：{s['error']}" if s.get("error") else ""), ""]
    return L


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--set" and argv[1] in SETS:
        steps, name = SETS[argv[1]], argv[1]
    elif len(argv) == 1 and argv[0].strip() and not argv[0].startswith("--"):
        steps, name = [("ask", argv[0].strip())], "one"
    else:
        print('usage: python -m agentdock.chatreal "質問"  |  python -m agentdock.chatreal --set sal2')
        return 2
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = TAKES / f"{stamp}-chat-real-{name}"
    out_dir.mkdir(parents=True, exist_ok=False)
    os.environ["AGENTDOCK_DATA"] = str(out_dir / "data")

    from agentdock import chat, db, live, mockdock, scene
    from agentdock.companion import ClaudeClient
    from agentdock.seed import build
    from agentdock.seed import scenario as sc

    assert build.main([]) == 0
    scene.apply_variant("normal")
    worker = live.Worker({o: mockdock.Policy(o, 0) for o in db.OWNER_CODES}, run_kind="mock")
    _scene(worker)
    client = ClaudeClient(effort=chat.EFFORT)

    rec = dict(set=name, model=chat.MODEL, effort=chat.EFFORT, run_enabled=False, started=datetime.datetime.now().isoformat(timespec="seconds"), steps=[])
    last_usage = 0
    for i, (kind, text) in enumerate(steps, 1):
        snap0, view0 = _snapshot(), _view()
        if kind == "event":
            _event(worker, text)
            snap1, view1 = _snapshot(), _view()
            rec["steps"].append(dict(no=i, kind="event", label=text, before=dict(sha256=_digest(snap0), view=view0), after=dict(sha256=_digest(snap1), view=view1),
                                     changed_tables=sorted(k for k in set(snap0) | set(snap1) if snap0.get(k) != snap1.get(k))))
            continue
        err, tid = None, None
        try:
            r = chat.ask(client, "amiya", "buri_xl", text, f"real-{stamp}-{i}", sc.DEMO_DATE, run_kind="real", max_calls=MAX_CALLS, run_enabled=False)
            tid = r["turn_id"]
        except chat.ChatError as e:
            err = f"{type(e).__name__}: {e}"
            c = db.connect("amiya")
            try:
                tid = c.execute("SELECT id FROM chat_turn WHERE idempotency_key = ?", (f"real-{stamp}-{i}",)).fetchone()[0]
            finally:
                c.close()
        snap1, view1 = _snapshot(), _view()
        usage = _usage_since(last_usage)
        last_usage = max([u["id"] for u in usage] or [last_usage])
        rec["steps"].append(dict(no=i, kind="ask", question=text, error=err, usage=usage, cost_usd=round(sum(u["cost_micro_usd"] for u in usage) / 1_000_000, 4),
                                 before=dict(sha256=_digest(snap0), view=view0, rows=sum(len(v) for v in snap0.values())),
                                 after=dict(sha256=_digest(snap1), view=view1, rows=sum(len(v) for v in snap1.values())),
                                 changed_tables=sorted(k for k in set(snap0) | set(snap1) if snap0.get(k) != snap1.get(k)),
                                 turn=chat.turn("amiya", tid) if tid else None))
        print(f"step {i} done: {text} ({err or 'answered'})", flush=True)
    rec["finished"] = datetime.datetime.now().isoformat(timespec="seconds")
    asks = [s for s in rec["steps"] if s["kind"] == "ask"]
    rec["cost_usd"] = round(sum(s["cost_usd"] for s in asks), 4)
    (out_dir / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    L = [f"# 本物の Claude の検証（{rec['started']}・{name}）", "", f"- モデル：{chat.MODEL}（effort {chat.EFFORT}）・チャットからの実行：止めたまま",
         f"- 合計：{rec['cost_usd']} ドル（runlog の単価）・問 {len(asks)}件", ""]
    for s in rec["steps"]:
        if s["kind"] == "event":
            L += [f"## {s['no']}. 出来事：{s['label']}（業務の状態が変わる段）", "", f"- 前：{s['before']['sha256'][:16]}… {s['before']['view']}",
                  f"- 後：{s['after']['sha256'][:16]}… {s['after']['view']}", f"- 変わった表：{s['changed_tables']}", ""]
        else:
            L += _md_step(s["no"], s)
    (out_dir / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print(out_dir)
    bad = [s["no"] for s in asks if s["changed_tables"] or s["error"]]
    print("asks with a change or an error:", bad or "none")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
