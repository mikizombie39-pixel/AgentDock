"""実行の記録の集計（画面の隅の「実行中・会期中に作成」の表示と、費用の報告の元）。

- 呼び出しの費用は、モデルごとの単価の表から整数（100万分の1ドル）で出す。表に無いモデルは推測せず止める
- 費用の合計が報告の刻み（既定 50ドル）を越えるたびに、越えた刻みを返す
- 止めた件数（開示の検査・範囲の外・古い回答）を数える。作った会話の再生では出ない数字
"""
from agentdock import db

# 100万トークンあたりの単価（100万分の1ドル）。claude-api の参照（2026-10-06 時点）から
PRICES = {
    "claude-opus-5-5": dict(input=4_000_000, output=20_000_000, cache_read=200_000, cache_write=5_000_000),
}
BLOCK_EVENTS = ("disclosure_blocked", "outside_plan_blocked", "stale_answer_rejected")


class UnknownModel(Exception):
    pass


def cost_micro_usd(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0) -> int:
    p = PRICES.get(model)
    if p is None:
        raise UnknownModel(model)
    total = input_tokens * p["input"] + output_tokens * p["output"] + cache_read * p["cache_read"] + cache_write * p["cache_write"]
    return -(-total // 1_000_000)   # 切り上げ


def record_usage(con, model: str, usage: dict, run_kind: str) -> int:
    """usage は input_tokens・output_tokens・cache_read_input_tokens・cache_creation_input_tokens を持つ dict。費用を返す。"""
    i, o = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    r, w = usage.get("cache_read_input_tokens", 0) or 0, usage.get("cache_creation_input_tokens", 0) or 0
    cost = cost_micro_usd(model, i, o, r, w)
    with con:
        con.execute("INSERT INTO api_usage (at, model, input_tokens, output_tokens, cost_micro_usd, run_kind, cache_read_tokens, cache_write_tokens) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (db.now_iso(), model, i, o, cost, run_kind, r, w))
    return cost


def spent_micro_usd(codes=db.OWNER_CODES) -> int:
    total = 0
    for code in codes:
        c = db.connect(code)
        try:
            total += c.execute("SELECT coalesce(sum(cost_micro_usd), 0) FROM api_usage").fetchone()[0]
        finally:
            c.close()
    return total


def crossed(before_micro: int, after_micro: int, step_usd: int = 50) -> list[int]:
    """before から after へ増えた間に越えた報告の刻み（ドル）。"""
    step = step_usd * 1_000_000
    return [k * step_usd for k in range(before_micro // step + 1, after_micro // step + 1)]


def summary(codes=db.OWNER_CODES) -> dict:
    out = dict(owners={}, blocked={e: 0 for e in BLOCK_EVENTS}, calls=0, cost_micro_usd=0, last_event_at=None)
    for code in codes:
        c = db.connect(code)
        try:
            events = {r[0]: r[1] for r in c.execute("SELECT event, count(*) FROM audit_event GROUP BY event")}
            last = c.execute("SELECT max(at) FROM audit_event WHERE event != 'seed'").fetchone()[0]
            calls, cost = c.execute("SELECT count(*), coalesce(sum(cost_micro_usd), 0) FROM api_usage").fetchone()
            by_kind = {r[0]: r[1] for r in c.execute("SELECT run_kind, sum(cost_micro_usd) FROM api_usage GROUP BY run_kind")}
        finally:
            c.close()
        out["owners"][code] = dict(events=events, last_event_at=last, calls=calls, cost_micro_usd=cost, cost_by_kind=by_kind)
        for e in BLOCK_EVENTS:
            out["blocked"][e] += events.get(e, 0)
        out["calls"] += calls
        out["cost_micro_usd"] += cost
        if last and (out["last_event_at"] is None or last > out["last_event_at"]):
            out["last_event_at"] = last
    return out
