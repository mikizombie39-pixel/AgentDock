"""本物の Claude で、仲間へのチャットを1往復だけ通す（費用がかかる・試験では呼ばない）。

  python -m agentdock.chatreal "なぜ魚廣を優先するの？"

- 業務の場面（料亭＝順風満帆の追加注文で、AI が相談を出し、担当者の判断待ち）は、試験と同じ手順で模擬の作業役が作る。
  本物の Claude を呼ぶのはチャットの1回だけ（chat.ask・道具は読むだけ）
- 置き場は AgentDock-local の takes の「日時-chat-real」（リポジトリの外）。種の DB はそこに新しく作る
- 処理の前と後で、全部の DB の行（チャットの記録と費用の記録を除く）の SHA-256 と、画面の態度・実行の状態・数を記録し、比べる
- 記録：record.json（質問・答え・根拠・捨てた物・道具の呼び出し・取得した記録・渡した文脈・前後の比較・トークンと費用）と summary.md
"""
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

TAKES = Path(r"C:\Users\mikiz\AgentDock-local\takes")
MAX_CALLS = 8


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
    return dict(stance=v["stance"], exec=v["exec"], headline=v["headline"], tally=v["tally"]["line"])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or not argv[0].strip():
        print('usage: python -m agentdock.chatreal "質問"')
        return 2
    question = argv[0].strip()
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = TAKES / f"{stamp}-chat-real"
    out_dir.mkdir(parents=True, exist_ok=False)
    os.environ["AGENTDOCK_DATA"] = str(out_dir / "data")

    from agentdock import chat, db, events, live, mate, mockdock, scene
    from agentdock.companion import ClaudeClient
    from agentdock.seed import build
    from agentdock.seed import scenario as sc

    # 業務の場面（試験の consult と同じ手順・模擬の作業役）
    assert build.main([]) == 0
    scene.apply_variant("normal")
    worker = live.Worker({o: mockdock.Policy(o, 0) for o in db.OWNER_CODES}, run_kind="mock")
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

    snap0, view0 = _snapshot(), _view()
    client = ClaudeClient(effort=chat.EFFORT)
    started = datetime.datetime.now().isoformat(timespec="seconds")
    try:
        r = chat.ask(client, "amiya", "buri_xl", question, f"real-{stamp}", sc.DEMO_DATE, run_kind="real", max_calls=MAX_CALLS)
        err = None
    except chat.ChatError as e:
        r, err = None, f"{type(e).__name__}: {e}"
    finished = datetime.datetime.now().isoformat(timespec="seconds")
    snap1, view1 = _snapshot(), _view()

    c = db.connect("amiya")
    try:
        usage = [dict(row) for row in c.execute("SELECT at, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_micro_usd, run_kind FROM api_usage "
                                               "WHERE run_kind = 'full_run' ORDER BY id")]
        tid = c.execute("SELECT max(id) FROM chat_turn").fetchone()[0]
    finally:
        c.close()
    t = chat.turn("amiya", tid) if tid else None
    changed = sorted(k for k in set(snap0) | set(snap1) if snap0.get(k) != snap1.get(k))
    rec = dict(question=question, started=started, finished=finished, error=err, model=chat.MODEL, effort=chat.EFFORT,
               before=dict(sha256=_digest(snap0), view=view0, rows=sum(len(v) for v in snap0.values())),
               after=dict(sha256=_digest(snap1), view=view1, rows=sum(len(v) for v in snap1.values())),
               changed_tables=changed, usage=usage,
               cost_usd=round(sum(u["cost_micro_usd"] for u in usage) / 1_000_000, 4),
               turn=t)
    (out_dir / "record.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    a = (t or {}).get("answer") or {}
    lines = [f"# 本物の Claude の一往復（{started}）", "", f"- 質問：{question}", f"- モデル：{chat.MODEL}（effort {chat.EFFORT}）",
             f"- 呼び出し：{len(usage)}回・入力 {sum(u['input_tokens'] for u in usage):,}・出力 {sum(u['output_tokens'] for u in usage):,} トークン・費用 {rec['cost_usd']} ドル（runlog の単価）",
             f"- 結果：{(t or {}).get('status')}" + (f"・{err}" if err else ""), "", "## 答え（サーバーの検査の後）", "",
             f"- 種類：{a.get('kind_label')}", f"- 結論：{a.get('conclusion')}", f"- 次の行動：{a.get('next_action')}",
             f"- 詳しい説明：{a.get('detail')}", f"- AI の解釈：{a.get('interpretation')}", f"- サーバーの検査：{'／'.join(a.get('notes') or []) or 'なし'}", "",
             "## 根拠（この回に道具で取得した記録だけ）", ""]
    for e in a.get("evidence") or []:
        lines.append(f"- `{e['ref']}`（{e['type_label']}・{e['origin_label']}" + (f"・{e['validity']}" if e.get("validity") else "") + f"）引用（{e['quote_from']}）：{e['quote']}")
    lines += ["", "## 道具の呼び出し", ""]
    for s in (t or {}).get("steps") or []:
        lines.append(f"- {s['tool']} {json.dumps(s['input'], ensure_ascii=False)}" + ("（エラー）" if s["is_error"] else ""))
    lines += ["", "## 捨てた物", "", "```json", json.dumps((t or {}).get("dropped"), ensure_ascii=False, indent=1), "```", "",
              "## 業務の状態の前後", "", f"- 前：SHA-256 {rec['before']['sha256']}（{rec['before']['rows']}行）・{view0}",
              f"- 後：SHA-256 {rec['after']['sha256']}（{rec['after']['rows']}行）・{view1}",
              f"- 変わった表：{changed or 'なし'}（chat_turn と api_usage は比較の外）"]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out_dir)
    print("\n".join(lines))
    return 0 if r and not changed else 1


if __name__ == "__main__":
    raise SystemExit(main())
