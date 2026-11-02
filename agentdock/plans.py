"""承認済みの計画と、範囲の中か外かの判定（コードが判定する）。

範囲の中は仲間が自分で決め、範囲の外は持ち主に相談する。
その線引きは仲間の言葉ではなく、この判定の結果で決まる。
"""
from agentdock import db


def _item_id(con, item_code: str) -> int:
    row = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()
    if row is None:
        raise KeyError(item_code)
    return row["id"]


def approve(con, item_code: str, kind: str, trade_date: str, max_unit_price_yen: int, max_quantity: int,
            criteria_id: int | None = None) -> int:
    """持ち主が承認した計画を記録する。同じ品目・種類・日の前の承認は superseded にする。"""
    iid = _item_id(con, item_code)
    now = db.now_iso()
    with con:
        con.execute("UPDATE plan SET status = 'superseded' WHERE item_id = ? AND kind = ? AND trade_date = ? AND status = 'approved'",
                    (iid, kind, trade_date))
        cur = con.execute(
            "INSERT INTO plan (item_id, kind, trade_date, max_unit_price_yen, max_quantity, criteria_id, status, approved_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'approved', ?)",
            (iid, kind, trade_date, max_unit_price_yen, max_quantity, criteria_id, now))
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'owner', 'plan_approved', ?)",
                    (now, f"{item_code} {kind} {trade_date} <= {max_unit_price_yen} x {max_quantity}"))
    return cur.lastrowid


def check(con, item_code: str, kind: str, trade_date: str, unit_price_yen: int, quantity: int) -> dict:
    """範囲の中か外か。外なら理由を全部挙げる（値・数量・計画なし）。"""
    iid = _item_id(con, item_code)
    p = con.execute(
        "SELECT id, max_unit_price_yen, max_quantity FROM plan WHERE item_id = ? AND kind = ? AND trade_date = ? AND status = 'approved'",
        (iid, kind, trade_date)).fetchone()
    if p is None:
        return dict(within=False, plan_id=None, reasons=["no_approved_plan"])
    reasons = []
    if unit_price_yen > p["max_unit_price_yen"]:
        reasons.append("price_over")
    if quantity > p["max_quantity"]:
        reasons.append("quantity_over")
    return dict(within=not reasons, plan_id=p["id"], reasons=reasons,
                max_unit_price_yen=p["max_unit_price_yen"], max_quantity=p["max_quantity"])
