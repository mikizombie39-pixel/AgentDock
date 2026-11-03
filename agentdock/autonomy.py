"""自律の循環：出来事で Claude AI パートナーを起こし、状況を渡し、Claude が道具を選び、コードが実行する。

  起こす → 状況の報告 → Claude が道具を選ぶ → コードが実行（決まりの検査つき）→ 繰り返し → 終わりの道具で止まる

決まり：
- 1回の起動の呼び出しは MAX_CALLS 回まで（費用の上限・2026-11-03 デイブ）。超えたら 'max_turns' で止める
- 道具の呼び出しは全て agent_step に残す（本物の Claude が判断した証拠）
- 数字の判定・開示の検査・権限の検査はコードが行う。Claude は判断と説明をする
- 出来事は起動が引き受けてから処理し、終われば済みにする。途中の失敗（'error'）は待ちに戻す
"""
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from agentdock import db, events, runlog
from agentdock.companion import MODEL, _get

MAX_CALLS = 20
EFFORT = "medium"
OUTCOMES = ("presented", "waiting", "nothing", "done", "error", "max_turns", "refusal")
# 起動の種類（agent_run：mock・real）と、費用の記録の種類（api_usage：mock・scene_check・full_run・recording）の対応
USAGE_KIND = {"mock": "mock", "real": "full_run"}


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def normalize(v):
    """文字の中の \\uXXXX の書き方を文字に戻す（入れ子の dict・list も）。"""
    if isinstance(v, str):
        return _UESC.sub(lambda m: chr(int(m.group(1), 16)), v) if "\\u" in v else v
    if isinstance(v, list):
        return [normalize(x) for x in v]
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in v.items()}
    return v


class ToolError(Exception):
    """道具の入力が決まりに合わない（Claude に理由を返して、選び直してもらう）。"""


@dataclass
class Tool:
    name: str
    description: str
    properties: dict
    handler: Callable          # (ctx, inp) -> str
    ends: str | None = None    # 成功した時に起動を止める結果（'waiting'・'presented'・'done'・'nothing'）
    strict: bool = True        # 読むだけの道具は strict を外す（strict の道具が多いと文法が大きすぎて呼べない・2026-11-03 実測）

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "strict": self.strict,
                "input_schema": {"type": "object", "additionalProperties": False,
                                 "required": list(self.properties), "properties": self.properties}}


@dataclass
class Ctx:
    owner: str
    con: object
    run_id: int
    event: dict
    on_date: str
    outcome: str | None = None
    summary: str | None = None
    data: dict = field(default_factory=dict)


def _record_step(con, run_id: int, seq: int, tool: str, inp, text: str, is_error: bool) -> None:
    with con:
        con.execute("INSERT INTO agent_step (run_id, seq, tool, input_json, result_text, is_error, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, seq, tool, json.dumps(inp, ensure_ascii=False, sort_keys=True), text, int(is_error), db.now_iso()))


def run(client, owner: str, event_id: int, role: str, tools: list[Tool], system: str, prompt: str,
        on_date: str, run_kind: str = "mock", max_calls: int = MAX_CALLS) -> dict:
    """1回の起動。{run_id, outcome, summary, steps, calls} を返す。出来事が引き受け済みなら outcome は None。"""
    con = db.connect(owner)
    try:
        with con:
            run_id = con.execute("INSERT INTO agent_run (role, event_id, started_at, model, run_kind) VALUES (?, ?, ?, ?, ?)",
                                 (role, event_id, db.now_iso(), MODEL, run_kind)).lastrowid
        if not events.claim(con, event_id, run_id):
            _finish(con, run_id, "nothing", "この出来事は別の起動が引き受け済み")
            return dict(run_id=run_id, outcome=None, summary=None, steps=0, calls=0)
        ctx = Ctx(owner=owner, con=con, run_id=run_id, event=events.get(con, event_id), on_date=on_date)
        by_name = {t.name: t for t in tools}
        schemas = [t.schema() for t in tools]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        seq = calls = 0
        last_text = None
        try:
            while calls < max_calls:
                resp = client.create(system=system, tools=schemas, messages=messages)
                calls += 1
                runlog.record_usage(con, MODEL, resp.usage, USAGE_KIND[run_kind])
                if resp.stop_reason == "refusal":
                    ctx.outcome = "refusal"
                    break
                messages.append({"role": "assistant", "content": resp.content})
                texts = [_get(b, "text") for b in resp.content if _get(b, "type") == "text"]
                if texts:
                    last_text = texts[-1]
                uses = [b for b in resp.content if _get(b, "type") == "tool_use"]
                if not uses:
                    ctx.outcome = ctx.outcome or "nothing"
                    ctx.summary = ctx.summary or last_text
                    break
                results = []
                for b in uses:
                    name, inp = _get(b, "name"), normalize(_get(b, "input"))
                    seq += 1
                    tool = by_name.get(name)
                    if tool is None:
                        text, err = f"その道具はありません：{name}", True
                    elif ctx.outcome:
                        text, err = "この起動は終わりの道具を実行済みです。ほかの道具は実行しません。", True
                    else:
                        try:
                            text, err = tool.handler(ctx, inp), False
                            if tool.ends:
                                ctx.outcome = tool.ends
                        except ToolError as e:
                            text, err = f"実行しませんでした：{e}", True
                    _record_step(con, run_id, seq, name, inp, text, err)
                    r = {"type": "tool_result", "tool_use_id": _get(b, "id"), "content": text}
                    if err:
                        r["is_error"] = True
                    results.append(r)
                messages.append({"role": "user", "content": results})
                if ctx.outcome:
                    break
            else:
                ctx.outcome = "max_turns"
        except Exception as e:   # 部品の不具合・通信の失敗：記録して出来事を待ちに戻す
            seq += 1
            _record_step(con, run_id, seq, "(error)", {}, f"{type(e).__name__}: {e}", True)
            _finish(con, run_id, "error", str(e))
            events.release(con, event_id, run_id)
            return dict(run_id=run_id, outcome="error", summary=str(e), steps=seq, calls=calls)
        _finish(con, run_id, ctx.outcome, ctx.summary or last_text)
        events.done(con, event_id, run_id)
        return dict(run_id=run_id, outcome=ctx.outcome, summary=ctx.summary or last_text, steps=seq, calls=calls, data=ctx.data)
    finally:
        con.close()


def _finish(con, run_id: int, outcome: str, summary) -> None:
    with con:
        con.execute("UPDATE agent_run SET ended_at = ?, outcome = ?, summary = ? WHERE id = ?",
                    (db.now_iso(), outcome, summary, run_id))


def steps(owner: str, run_id: int) -> list[dict]:
    con = db.connect(owner)
    try:
        return [dict(r) for r in con.execute("SELECT * FROM agent_step WHERE run_id = ? ORDER BY seq", (run_id,))]
    finally:
        con.close()
