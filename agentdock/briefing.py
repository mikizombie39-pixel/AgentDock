"""仲間に渡す材料の組み立て。

持ち主の DB だけを読み、今日の判断に要る材料を1つにまとめる。数字はすべてコードで出す。
仲間はこの材料を読んで判断し、言葉で説明する（数字を作らない）。
材料には照合番号をつけ、どの材料で判断したかを後から確かめられるようにする。
"""
import hashlib
import json
from typing import Any

from agentdock import criteria, plans, results, vaultbook


def _fp(obj) -> str:
    canon = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build(con, item_code: str, kind: str, on_date: str) -> dict:
    """item_code の今日の材料。kind は 'tender'・'negotiation'・'purchase'。"""
    owner = dict(con.execute("SELECT code, name, role, person_name FROM owner").fetchone())
    item = dict(con.execute("SELECT code, name, unit, unit_weight_g FROM item WHERE code = ?", (item_code,)).fetchone())
    m: dict[str, Any] = dict(owner=owner, item=item, kind=kind, date=on_date)
    m["market_recent"] = criteria.market_stats(con, item_code, on_date)
    m["market_last_year"] = criteria.last_year_stats(con, item_code, on_date)
    p = con.execute(
        "SELECT p.max_unit_price_yen, p.max_quantity, p.min_unit_price_yen FROM plan p JOIN item i ON i.id = p.item_id "
        "WHERE i.code = ? AND p.kind = ? AND p.trade_date = ? AND p.status = 'approved'", (item_code, kind, on_date)).fetchone()
    m["approved_plan"] = (dict(max_unit_price_yen=p[0], max_quantity=p[1]) | ({"min_unit_price_yen": p[2]} if p[2] else {})) if p else None

    if owner["role"] == "buyer":
        orders = []
        for o in con.execute(
                "SELECT c.name, o.quantity, o.sell_price_yen, o.received_at FROM order_book o JOIN item i ON i.id = o.item_id "
                "JOIN customer c ON c.id = o.customer_id WHERE i.code = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id",
                (item_code, on_date)):
            orders.append(dict(customer=o[0], quantity=o[1], sell_price_yen=o[2], received_at=o[3],
                               ceiling_yen=criteria.ceiling_yen(con, item_code, o[0]),
                               sell_ratio=criteria.sell_ratio(con, item_code, o[0], on_date)))
        m["orders"] = orders
        rec = results.reconcile(owner["code"], on_date)
        m["results"] = next((x for x in rec["lines"] if x["item"] == item_code), None)
        m["cheap_offer"] = next((x for x in rec["offers"] if x["item"] == item_code), None)
        ib = con.execute("SELECT body_json FROM inbox_broadcast ORDER BY broadcast_id DESC LIMIT 1").fetchone()
        body = json.loads(ib[0]) if ib else None
        m["coop_item"] = next((e for e in body["items"] if e["item"] == item_code), None) if body and body.get("date") == on_date else None
    else:
        landed = con.execute(
            "SELECT l.quantity, l.size_note, l.caught_at, l.area FROM landing l JOIN item i ON i.id = l.item_id "
            "WHERE i.code = ? AND l.landing_date = ? AND l.kind = 'actual'", (item_code, on_date)).fetchone()
        fc = con.execute("SELECT l.quantity FROM landing l JOIN item i ON i.id = l.item_id WHERE i.code = ? AND l.landing_date = ? AND l.kind = 'forecast'",
                         (item_code, on_date)).fetchone()
        m["landing"] = dict(landing=dict(landed) if landed else None, tomorrow_forecast=fc[0] if fc else None)
        has_rule = con.execute("SELECT 1 FROM seller_rule s JOIN item i ON i.id = s.item_id WHERE i.code = ?", (item_code,)).fetchone()
        if has_rule:
            floor, held = criteria.floor_yen(con, item_code, on_date)
            m["floor"] = dict(floor_yen=floor, held_up_for_tomorrow=held)
        rec = results.reconcile(owner["code"], on_date)
        m["results"] = next((x for x in rec["lines"] if x["item"] == item_code), None)
    m["vault_cases"] = vaultbook.find(con, item_code)
    m["fingerprint"] = _fp(m)
    return m


def check_terms(con, item_code: str, kind: str, on_date: str, unit_price_yen: int, quantity: int) -> dict:
    """材料と同じ判定を、仲間が考えた値に当てる（画面の「範囲の中か外か」の表示用）。"""
    return plans.check(con, item_code, kind, on_date, unit_price_yen, quantity)


def _yen(v):
    return "—" if v is None else f"{v:,}円"


def render(m: dict) -> str:
    """材料を仲間に渡す文にする。数字は材料の値だけを使う。"""
    unit = {"fish": "本", "kg": "kg"}.get(m["item"]["unit"], "箱")
    lines = [f"# 今日の材料（{m['date']}・{m['owner']['name']}・{m['item']['name']}）", ""]
    r = m["market_recent"]
    if r:
        lines.append(f"- 直近30日の相場：中央値 {_yen(r['median'])}（安い方から4分の1 {_yen(r['low'])}・4分の3 {_yen(r['high'])}・{r['count']}件）")
    ly = m["market_last_year"]
    if ly:
        lines.append(f"- 去年の同じ時期：中央値 {_yen(ly['median'])}（{ly['count']}件）")
    p = m["approved_plan"]
    if p is None:
        lines.append("- 承認済みの計画：なし（値を出す前に持ち主の承認が要る）")
    elif p.get("min_unit_price_yen"):
        lines.append(f"- 承認済みの計画：1{unit} {_yen(p['min_unit_price_yen'])} 以上・{p['max_quantity']}{unit} まで")
    else:
        lines.append(f"- 承認済みの計画：1{unit} {_yen(p['max_unit_price_yen'])} まで・{p['max_quantity']}{unit} まで")
    for o in m.get("orders", []):
        sr = o["sell_ratio"]
        cheap = f"・安い日は注文の {sr['cheap_ratio']}倍売れた（{sr['cheap_days']}日）" if sr.get("cheap_ratio") else ""
        lines.append(f"- 注文：{o['customer']} {o['quantity']}{unit}・売値 {_yen(o['sell_price_yen'])}・上限 {_yen(o['ceiling_yen'])}{cheap}")
    res = m.get("results")
    if res:
        if res.get("status") in ("awaiting_result", "not_sold_yet"):
            lines.append("- 今日の結果：まだ届いていない")
        elif "won" in res:
            lines.append(f"- 今日の結果：落札 {res['won']}{unit}・不足 {res['short']}{unit}・余り {res['spare']}{unit}")
        else:
            lines.append(f"- 今日の結果：水揚げ {res['landed']}{unit}・売れた {res['sold']}{unit}・残り {res['remaining']}{unit}")
    c = m.get("cheap_offer")
    if c:
        lines.append(f"- 漁協の資料：今日の見込み {_yen(c['expected_yen'])}（直近の中央値の {round(c['ratio_to_recent'] * 100)}%）・{c['quantity']}{unit}")
    f = m.get("floor")
    if f:
        note = "（明日の水揚げが多いので引き上げ）" if f["held_up_for_tomorrow"] else ""
        lines.append(f"- 下限：{_yen(f['floor_yen'])}{note}")
    lnd = m.get("landing")
    if lnd and lnd["tomorrow_forecast"] is not None:
        lines.append(f"- 明日の水揚げの見込み：{lnd['tomorrow_forecast']}{unit}")
    for v in m.get("vault_cases", []):
        why = f"（理由：{v['reason']}）" if v["reason"] else ""
        lost = "・本文のファイルが見つからない" if v["missing"] else ""
        lines.append(f"- 帳面の事例 {v['date']}：{v['situation']} → {v['choice']}{why}{lost}")
    lines += ["", f"材料の照合番号：{m['fingerprint'][:12]}"]
    return "\n".join(lines)
