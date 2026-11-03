"""Rules：誰が1件いくらまでの買い付けを確定できるか。業務 API の側で強制する。

- 上限の額はコードに書かず、持ち主の DB の authority 表から読む（組織と担当者ごと）
- 「1件」＝同じ取引の相手・同じ品目・同じ取引日の買い付けの合計（確定済みの分＋今回の分）
  いくつもの得意先の注文をまとめても、取引を小分けにしても、この合計で判定するので上限を迂回できない
- 上限を超えれば、上限の無い人（社長など）の承認が要る
- 顧客の間の優先を変える判断（どの売り先の注文を欠くか）も、表で許された役割（社長）だけが確定できる
- 採算や在庫の損の判断（判断の基準・費用の決まり）とは別に持つ。権限は「誰が決めてよいか」だけを見る
"""
from agentdock import db

APPROVED = ("approved",)


class AuthorityError(Exception):
    pass


def limit_for(con, role: str) -> int | None:
    """その役割の1件の上限（円）。None は上限なし。表に無い役割は止める。"""
    row = con.execute("SELECT max_trade_yen FROM authority WHERE role = ?", (role,)).fetchone()
    if row is None:
        raise AuthorityError(f"no authority for role: {role}")
    return row["max_trade_yen"] if hasattr(row, "keys") else row[0]


def may_change_priority(con, role: str) -> bool:
    """その役割が、顧客の間の優先（どの売り先の注文を欠くか）を変えてよいか。表に無い役割は止める。"""
    row = con.execute("SELECT may_change_priority FROM authority WHERE role = ?", (role,)).fetchone()
    if row is None:
        raise AuthorityError(f"no authority for role: {role}")
    return bool(row[0])


def case_total(con, counterparty: str, item_id: int, trade_date: str, exclude_proposal_id: int | None = None) -> int:
    """同じ相手・品目・取引日で、すでに確定した買い付けの合計（円）。"""
    row = con.execute(
        "SELECT COALESCE(SUM(quantity * unit_price_yen), 0) FROM proposal "
        "WHERE side = 'buy' AND counterparty = ? AND item_id = ? AND trade_date = ? "
        "AND status IN ('approved', 'registered', 'incomplete') AND id IS NOT ?",
        (counterparty, item_id, trade_date, exclude_proposal_id)).fetchone()
    return int(row[0])


def check(con, role: str, counterparty: str, item_id: int, trade_date: str, amount_yen: int,
          exclude_proposal_id: int | None = None) -> dict:
    """今回の額を足した「1件」の合計が、その役割の上限の中かを判定する。"""
    if amount_yen <= 0:
        raise AuthorityError("amount must be positive")
    limit = limit_for(con, role)
    already = case_total(con, counterparty, item_id, trade_date, exclude_proposal_id)
    total = already + amount_yen
    allowed = limit is None or total <= limit
    return dict(role=role, limit_yen=limit, already_yen=already, amount_yen=amount_yen, total_yen=total,
                allowed=allowed, needs="president" if not allowed else None, checked_at=db.now_iso())
