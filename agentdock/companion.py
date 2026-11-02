"""仲間（Claude）の処理の枠：道具の定義、道具を部品につなぐ処理、呼び出しの繰り返し。

仲間が使える道具は交渉の3つだけ（申し出る・受ける・打ち切る）。
数字の判定・開示の検査・範囲の判定・往復の数はすべて部品（コード）が行い、仲間は判断と説明だけをする。
範囲の外の申し出は実行されず、相談（escalate）として記録されて持ち主に回る。

呼び出し先は2つ：
- MockClient：決まった返事を返す模擬の仲間（ふだんの試験はこちら）
- ClaudeClient：本物の Claude。鍵はリポジトリの外のファイルから読み、api_key= で渡す（環境変数は使わない）
"""
import json
from dataclasses import dataclass, field
from pathlib import Path

from agentdock import consult, db, disclosure, evidence, market, runlog

MODEL = "claude-opus-5-5"
KEY_FILE = Path(r"C:\Users\mikiz\AgentDock-local\secrets\claude_api_key.txt")
MAX_TURNS = 8

_EVIDENCE = {
    "type": "array", "minItems": 1,
    "items": {"type": "object", "additionalProperties": False, "required": ["kind", "ref_id", "summary"],
              "properties": {
                  "kind": {"type": "string", "enum": ["price_history", "order", "sales_price", "cost_rule", "vault_case",
                                                       "broadcast", "auction_result", "landing"]},
                  "ref_id": {"type": ["integer", "null"]},
                  "summary": {"type": "string"}}},
}
TOOLS = [
    {"name": "make_offer", "strict": True,
     "description": "相手に値と数量を申し出る。message は相手に届く文。自社の上限・下限・原価・売り先の名前は書かない（書くと送られない）。",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["unit_price_yen", "quantity", "message", "rationale", "evidence"],
                      "properties": {"unit_price_yen": {"type": "integer"}, "quantity": {"type": "integer"},
                                     "message": {"type": "string"}, "rationale": {"type": "string"}, "evidence": _EVIDENCE}}},
    {"name": "accept", "strict": True,
     "description": "相手の申し出を受ける。双方の最終承認の後に登録される。",
     "input_schema": {"type": "object", "additionalProperties": False,
                      "required": ["unit_price_yen", "quantity", "rationale", "evidence"],
                      "properties": {"unit_price_yen": {"type": "integer"}, "quantity": {"type": "integer"},
                                     "rationale": {"type": "string"}, "evidence": _EVIDENCE}}},
    {"name": "end_negotiation", "strict": True,
     "description": "交渉を打ち切る（不成立）。",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["rationale"],
                      "properties": {"rationale": {"type": "string"}}}},
]


@dataclass
class Context:
    owner: str
    task_id: int
    item_code: str
    on_date: str
    negotiation_id: int
    version: int = 1
    outcome: str | None = None          # 'offered' / 'accepted' / 'no_deal' / 'needs_owner'
    log: list = field(default_factory=list)


def _evidence(inp):
    return [(e["kind"], e["ref_id"], e["summary"]) for e in inp["evidence"]]


def handle(con, ctx: Context, name: str, inp: dict) -> tuple[str, bool]:
    """道具を1回実行する。(仲間に返す文, エラーか) を返す。副作用はすべて部品が行う。"""
    if name == "make_offer":
        p, q = inp["unit_price_yen"], inp["quantity"]
        reasons = disclosure.check(con, ctx.item_code, p, q, inp["message"], ctx.on_date)
        if reasons:   # 送らない。仲間に書き直しを求める（記録は send の中で残す）
            try:
                disclosure.send(con, ctx.task_id, ctx.item_code, p, q, inp["message"], ctx.on_date)
            except disclosure.DisclosureError:
                pass
            return f"送れませんでした。文に出せない内容があります：{', '.join(reasons)}。書き直してください。", True
        try:
            evidence.record(con, ctx.task_id, "offer", p, q, inp["rationale"], _evidence(inp), ctx.on_date, model=MODEL)
        except evidence.OutsidePlan as e:
            evidence.record(con, ctx.task_id, "escalate", p, q, inp["rationale"], _evidence(inp), ctx.on_date, model=MODEL)
            ctx.outcome = "needs_owner"
            return f"承認済みの範囲の外です（{', '.join(e.reasons)}）。申し出は送らず、持ち主に相談します。ここで止めてください。", False
        try:
            ctx.version = market.exchange(ctx.negotiation_id, ctx.version)
        except market.StaleApproval:
            raise
        except market.MarketError:   # 往復の上限：市場が no_deal にした
            with con:
                con.execute("UPDATE task SET state = 'no_deal', updated_at = ? WHERE id = ?", (db.now_iso(), ctx.task_id))
            ctx.outcome = "no_deal"
            return f"往復の上限（{market.MAX_ROUNDS}回）に達しました。不成立です。", False
        disclosure.send(con, ctx.task_id, ctx.item_code, p, q, inp["message"], ctx.on_date)
        ctx.outcome = "offered"
        return f"送りました（{p}円 × {q}）。相手の返事を待ちます。", False
    if name == "accept":
        p, q = inp["unit_price_yen"], inp["quantity"]
        try:
            evidence.record(con, ctx.task_id, "accept", p, q, inp["rationale"], _evidence(inp), ctx.on_date, model=MODEL)
        except evidence.OutsidePlan as e:
            evidence.record(con, ctx.task_id, "escalate", p, q, inp["rationale"], _evidence(inp), ctx.on_date, model=MODEL)
            ctx.outcome = "needs_owner"
            return f"承認済みの範囲の外です（{', '.join(e.reasons)}）。受けずに持ち主に相談します。ここで止めてください。", False
        with con:
            con.execute("UPDATE task SET state = 'awaiting_final_approval', updated_at = ? WHERE id = ?", (db.now_iso(), ctx.task_id))
        ctx.outcome = "accepted"
        return "受けました。双方の最終承認を待ちます。", False
    if name == "end_negotiation":
        evidence.record(con, ctx.task_id, "decline", None, None, inp["rationale"], [("price_history", None, "交渉の打ち切り")], ctx.on_date, model=MODEL)
        market.no_deal(ctx.negotiation_id)
        with con:
            con.execute("UPDATE task SET state = 'no_deal', updated_at = ? WHERE id = ?", (db.now_iso(), ctx.task_id))
        ctx.outcome = "no_deal"
        return "打ち切りました（不成立）。", False
    return f"その道具はありません：{name}", True


def run(client, con, ctx: Context, system: str, prompt: str, run_kind: str = "mock", max_turns: int = MAX_TURNS) -> Context:
    """仲間を呼び、道具の呼び出しが終わるか、持ち主への相談・不成立・受諾になるまで繰り返す。"""
    if not consult.can_proceed(con, ctx.task_id):
        raise consult.ConsultError(f"task {ctx.task_id} cannot proceed: {consult.task(con, ctx.task_id)['state']}")
    messages: list[dict] = [{"role": "user", "content": prompt}]
    for _ in range(max_turns):
        resp = client.create(system=system, tools=TOOLS, messages=messages)
        runlog.record_usage(con, MODEL, resp.usage, run_kind)
        if resp.stop_reason == "refusal":
            ctx.log.append(("refusal", None))
            ctx.outcome = ctx.outcome or "needs_owner"
            return ctx
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if _get(b, "type") == "tool_use"]
        if not uses:
            return ctx
        results = []
        for b in uses:
            text, err = handle(con, ctx, _get(b, "name"), _get(b, "input"))
            ctx.log.append((_get(b, "name"), text))
            r = {"type": "tool_result", "tool_use_id": _get(b, "id"), "content": text}
            if err:
                r["is_error"] = True
            results.append(r)
        messages.append({"role": "user", "content": results})   # 結果はまとめて1通で返す
        if ctx.outcome in ("needs_owner", "no_deal", "accepted"):
            return ctx
    ctx.log.append(("max_turns", None))
    return ctx


def _get(block, key):
    return block[key] if isinstance(block, dict) else getattr(block, key)


# 呼び出し先 ------------------------------------------------------------------

@dataclass
class Reply:
    content: list
    stop_reason: str = "tool_use"
    usage: dict = field(default_factory=lambda: dict(input_tokens=0, output_tokens=0))


class MockClient:
    """決まった返事を順に返す模擬の仲間。受け取った要求も残す（試験で中身を確かめるため）。"""

    def __init__(self, replies: list[Reply]):
        self.replies = list(replies)
        self.requests = []

    def create(self, **params):
        self.requests.append(json.loads(json.dumps(params, ensure_ascii=False, default=str)))
        if not self.replies:
            return Reply(content=[{"type": "text", "text": "以上です。"}], stop_reason="end_turn")
        return self.replies.pop(0)


def read_key(path: Path = KEY_FILE) -> str:
    """鍵はリポジトリの外のファイルから読む。環境変数は見ない。無い・空なら止める。"""
    if not path.exists():
        raise FileNotFoundError(f"key file not found: {path}")
    key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise ValueError(f"key file is empty: {path}")
    return key


class ClaudeClient:
    """本物の Claude を呼ぶ。作るだけでは呼ばない（create を呼んだ時だけ通信する）。"""

    def __init__(self, effort: str = "high", key_path: Path = KEY_FILE):
        import anthropic   # 模擬の試験では読み込まない
        self._client = anthropic.Anthropic(api_key=read_key(key_path))
        self.effort = effort

    def create(self, system, tools, messages):
        r = self._client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=system, tools=tools, messages=messages,
            thinking={"type": "adaptive"}, output_config={"effort": self.effort},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        u = r.usage
        return Reply(content=r.content, stop_reason=r.stop_reason,
                     usage=dict(input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                                cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
                                cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0))
