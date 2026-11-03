"""送る前の開示の検査（仲間が相手に送る文を、送る前にコードで確かめる）。

止めるもの：
- 自社の内情の金額：売値・原価・欲しい利益・上限（買い手）・下限（売り手）・数量ごとの最低の値・承認済みの計画の上限の値
  （申し出の値そのものと、その合計は送ってよい）
- 内情を示す語：上限・下限・原価・利益・採算・予算・承認済み・帳面
- 自社の売り先の名前
数字は全角、カンマ、「1万8500」の形も同じ値として読む。
通らなければ何も送らず、記録には止めた理由だけを残す。
"""
import re
import unicodedata

from agentdock import criteria, db

WORDS = ("上限", "下限", "原価", "利益", "採算", "予算", "承認済み", "帳面")
MIN_YEN = 100   # これより小さい数（箱数など）は金額として見ない


class DisclosureError(Exception):
    def __init__(self, reasons):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


def amounts(text: str) -> set[int]:
    t = unicodedata.normalize("NFKC", text).replace("，", ",")
    found = set()
    for m in re.finditer(r"(\d[\d,]*)\s*万\s*(\d[\d,]*)?", t):
        found.add(int(m.group(1).replace(",", "")) * 10000 + int((m.group(2) or "0").replace(",", "")))
    t = re.sub(r"\d[\d,]*\s*万\s*(\d[\d,]*)?", " ", t)
    for m in re.finditer(r"\d[\d,]*", t):
        found.add(int(m.group(0).replace(",", "")))
    return {v for v in found if v >= MIN_YEN}


def secrets(con, item_code: str, on_date: str) -> dict[int, str]:
    """この品目について相手に出してはいけない金額と、その名前。"""
    out = {}
    iid = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()[0]
    for r in con.execute("SELECT c.name, s.unit_price_yen FROM sales_price s JOIN customer c ON c.id = s.customer_id WHERE s.item_id = ?", (iid,)):
        out[r[1]] = f"sales_price:{r[0]}"
        out[criteria.ceiling_yen(con, item_code, r[0])] = f"ceiling:{r[0]}"
    for r in con.execute("SELECT process_cost_yen, target_profit_yen FROM cost_rule WHERE item_id = ?", (iid,)):
        if r[0]:
            out[r[0]] = "process_cost"
        if r[1]:
            out[r[1]] = "target_profit"
    if con.execute("SELECT 1 FROM seller_rule WHERE item_id = ?", (iid,)).fetchone():
        out[criteria.floor_yen(con, item_code, on_date)[0]] = "floor"
    for r in con.execute("SELECT floor_yen FROM quote_rule WHERE item_id = ?", (iid,)):
        out[r[0]] = "quote_floor"
    for r in con.execute("SELECT max_unit_price_yen, min_unit_price_yen FROM plan WHERE item_id = ? AND status = 'approved'", (iid,)):
        if r[0]:
            out[r[0]] = "plan_max"
        if r[1]:
            out[r[1]] = "plan_min"
    return {v: k for v, k in out.items() if v >= MIN_YEN}


def check(con, item_code: str, unit_price_yen: int, quantity: int, text: str, on_date: str) -> list[str]:
    """止める理由のリスト。空なら送ってよい。"""
    reasons = []
    allowed = {unit_price_yen, unit_price_yen * quantity}
    sec = secrets(con, item_code, on_date)
    for v in sorted(amounts(text) - allowed):
        if v in sec:
            reasons.append(f"amount {v} ({sec[v]})")
    norm = unicodedata.normalize("NFKC", text)
    reasons += [f"word {w}" for w in WORDS if w in norm]
    reasons += [f"customer {r[0]}" for r in con.execute("SELECT name FROM customer ORDER BY id") if r[0] in norm]
    return reasons


def send(con, task_id: int, item_code: str, unit_price_yen: int, quantity: int, text: str, on_date: str) -> int:
    """検査を通った文だけを送った記録にする。通らなければ DisclosureError（記録は止めた理由だけ）。"""
    reasons = check(con, item_code, unit_price_yen, quantity, text, on_date)
    now = db.now_iso()
    if reasons:
        with con:
            con.execute("INSERT INTO audit_event (at, actor, event, task_id, detail) VALUES (?, 'hook', 'disclosure_blocked', ?, ?)",
                        (now, task_id, "; ".join(reasons)))
        raise DisclosureError(reasons)
    with con:
        seq = con.execute("SELECT coalesce(max(seq), 0) + 1 FROM message WHERE task_id = ?", (task_id,)).fetchone()[0]
        con.execute("INSERT INTO message (task_id, seq, direction, unit_price_yen, quantity, sent_at, passed_disclosure_check, body) "
                    "VALUES (?, ?, 'out', ?, ?, ?, 1, ?)", (task_id, seq, unit_price_yen, quantity, now, text))
    return seq
