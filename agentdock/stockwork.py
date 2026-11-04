"""売り先の当てのない在庫（引当のない在庫）を仕事として扱う：数える・譲る時の最低の値・注文に充てる・人に扱いを問う。

2026-11-04 デイブ：本物の実行で、担当者が「料亭 2本（注文なし）」を含む案を登録した後、AI は「満たしていない注文は無い」として
何もしなかった。売り先の当てのない数量は、AI が引き受ける残りの仕事にする。

数字はコードが業務 DB から出す。Claude は判断と説明をする。
- 引当のない在庫：自社の在庫のうち status = 'unallocated' の行（どの注文にも結びついていない数量）
- 譲る時の最低の値 ＝ 在庫の仕入れ値（stock.unit_cost_yen）＋ 譲る時の費用（resale_cost の項目の合計）
- 注文に充てる：注文の残り（注文 − 引当済み）と、引当のない数（仮押さえを引いた数）の内だけ。
  人が承認した案で「この売り先に回す」と決めた在庫（intended_customer_id）は、その売り先の注文にだけ充てる（人の決定を AI の制約にする）
- 扱いを問う：選択肢の記号と名前はコードが決める（売り先に注文を確かめる・ほかの仲買に譲る・そのまま持つ）。答えは出来事になり、AI を起こす
"""
import datetime
import json

from agentdock import authority, db, events

CHOICES = ("confirm_customer", "offer_to_buyers", "keep")
MAX_QUESTION = 40
MAX_REASON = 120


class StockError(Exception):
    pass


def _t(s) -> int:
    try:
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except (TypeError, ValueError):
        return 0


def _held(con, stock_id: int, now: str | None) -> int:
    """在庫の行に掛かっている期限の内の仮押さえ（ほかの仲買に譲る返事をした分）。"""
    if now is None:
        return 0
    return con.execute("SELECT coalesce(sum(quantity), 0) FROM stock_hold WHERE stock_id = ? AND status = 'held' AND expires_at > ?",
                       (stock_id, now)).fetchone()[0]


def unallocated(con, item_id: int, now: str | None = None) -> list[dict]:
    """引当のない在庫の行（古い順）。free は仮押さえを引いた数。"""
    rows = [dict(r) for r in con.execute(
        "SELECT s.id, s.quantity, s.unit_cost_yen, s.intended_customer_id, c.name AS intended_customer, s.lot_no, s.trade_id, "
        "coalesce(s.unallocated_at, s.updated_at) AS since FROM stock s LEFT JOIN customer c ON c.id = s.intended_customer_id "
        "WHERE s.item_id = ? AND s.status = 'unallocated' ORDER BY s.id", (item_id,))]
    for r in rows:
        r["free"] = r["quantity"] - _held(con, r["id"], now)
    return rows


def unallocated_at(con, item_id: int, at: str) -> list[dict]:
    """ある時刻の引当のない在庫（記録の再生）：その時に引当がなく、後で AI が注文に充てた行も、その時刻の前なら数える。"""
    out = []
    for r in con.execute(
            "SELECT s.id, s.quantity, s.status, s.updated_at, s.unit_cost_yen, s.intended_customer_id, c.name AS intended_customer, "
            "coalesce(s.unallocated_at, s.updated_at) AS since FROM stock s LEFT JOIN customer c ON c.id = s.intended_customer_id "
            "WHERE s.item_id = ? AND (s.status = 'unallocated' OR (s.status = 'allocated' AND s.unallocated_at IS NOT NULL)) ORDER BY s.id", (item_id,)):
        if _t(r["since"]) > _t(at):
            continue
        if r["status"] != "unallocated" and _t(r["updated_at"]) <= _t(at):
            continue
        out.append(dict(r, free=r["quantity"]))
    return out


def resale_floor(con, item_id: int) -> dict | None:
    """引当のない在庫をほかの仲買に譲る時の最低の値（1単位）と内訳。仕入れ値の分からない在庫しか無ければ None。"""
    costs = [r["unit_cost_yen"] for r in unallocated(con, item_id) if r["unit_cost_yen"]]
    if not costs:
        return None
    purchase = max(costs)   # 仕入れ値の違う行が混ざる時は、高い方を割らない
    items = [dict(name=r[0], yen=r[1]) for r in con.execute(
        "SELECT name, cost_yen_per_unit FROM resale_cost WHERE item_id = ? ORDER BY id", (item_id,))]
    return dict(floor_yen=purchase + sum(i["yen"] for i in items), purchase_unit_yen=purchase, costs=items)


def _order(con, order_id, item_id: int, on_date: str):
    o = con.execute("SELECT o.id, o.quantity, o.status, o.order_date, c.id AS customer_id, c.name AS customer FROM order_book o "
                    "JOIN customer c ON c.id = o.customer_id WHERE o.id = ? AND o.item_id = ?", (order_id, item_id)).fetchone()
    if o is None:
        raise StockError(f"この品目の注文 {order_id} がありません")
    if o["status"] not in ("open", "partly_filled") or o["order_date"] != on_date:
        raise StockError(f"注文 {order_id} は今日の開いている注文ではありません（{o['status']}）")
    return o


def filled(con, order_id: int) -> int:
    return con.execute("SELECT coalesce(sum(quantity), 0) FROM stock WHERE order_id = ? AND status = 'allocated'", (order_id,)).fetchone()[0]


def set_order_status(con, order_id: int) -> None:
    """注文の充足を、引当の合計から決め直す（呼ぶ側のトランザクションの中で）。"""
    q = con.execute("SELECT quantity FROM order_book WHERE id = ?", (order_id,)).fetchone()[0]
    got = filled(con, order_id)
    con.execute("UPDATE order_book SET status = ? WHERE id = ?", ("filled" if got >= q else "partly_filled" if got else "open", order_id))


def allocate(con, item_code: str, order_id: int, quantity: int, run_id: int | None, on_date: str, now: str) -> dict:
    """引当のない在庫を、今日の開いている注文に充てる。数量と充ててよいかはコードが確かめる。"""
    it = con.execute("SELECT id, unit FROM item WHERE code = ?", (item_code,)).fetchone()
    if it is None:
        raise StockError(f"品目がありません：{item_code}")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise StockError("quantity は 1 以上の整数で示してください")
    o = _order(con, order_id, it["id"], on_date)
    remaining = o["quantity"] - filled(con, o["id"])
    if quantity > remaining:
        raise StockError(f"注文 {o['id']}（{o['customer']}）の残りは {remaining} です。それより多くは充てられません")
    rows = unallocated(con, it["id"], now)
    ok = sorted([r for r in rows if r["intended_customer_id"] in (None, o["customer_id"])],
                key=lambda r: (r["intended_customer_id"] != o["customer_id"], r["id"]))
    other = [r for r in rows if r["intended_customer_id"] not in (None, o["customer_id"])]
    free = sum(max(0, r["free"]) for r in ok)
    if free < quantity:
        msg = f"充てられる引当のない在庫は {free} です"
        if other:
            names = "・".join(dict.fromkeys(r["intended_customer"] for r in other))
            msg += (f"（人が承認した案で{names}に回すと決めた在庫 {sum(r['free'] for r in other)} は、{names}の注文にだけ充てられます。"
                    "ほかの売り先に充てるかは ask_about_stock で担当者に問うてください）")
        raise StockError(msg)
    at = db.now_iso()
    used, left = [], quantity
    with con:
        for r in ok:
            take = min(left, max(0, r["free"]))
            if take <= 0:
                continue
            if take == r["quantity"]:   # 行の全部：その行を注文に結びつける（引当のなかった時刻は残す）
                con.execute("UPDATE stock SET status = 'allocated', order_id = ?, allocated_by_run = ?, unallocated_at = ?, updated_at = ? WHERE id = ?",
                            (o["id"], run_id, r["since"], at, r["id"]))
                used.append(dict(stock_id=r["id"], quantity=take))
            else:                       # 行の一部：分けて、分けた行を注文に結びつける
                con.execute("UPDATE stock SET quantity = quantity - ?, unallocated_at = ?, updated_at = ? WHERE id = ?", (take, r["since"], at, r["id"]))
                src = con.execute("SELECT * FROM stock WHERE id = ?", (r["id"],)).fetchone()
                nid = con.execute(
                    "INSERT INTO stock (item_id, quantity, status, order_id, trade_id, lot_no, stock_date, updated_at, unit_cost_yen, intended_customer_id, "
                    "unallocated_at, allocated_by_run) VALUES (?, ?, 'allocated', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (it["id"], take, o["id"], src["trade_id"], src["lot_no"], src["stock_date"], at, src["unit_cost_yen"], src["intended_customer_id"],
                     r["since"], run_id)).lastrowid
                used.append(dict(stock_id=nid, quantity=take, split_from=r["id"]))
            left -= take
            if left == 0:
                break
        set_order_status(con, o["id"])
        con.execute("UPDATE stock_question SET status = 'closed', closed_at = ? WHERE item_id = ? AND status = 'open'", (at, it["id"]))
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'companion', 'stock_allocated', ?)",
                    (at, json.dumps(dict(order_id=o["id"], quantity=quantity, rows=used, run_id=run_id), ensure_ascii=False)))
    left_stock = sum(r["quantity"] for r in unallocated(con, it["id"]))
    return dict(order_id=o["id"], customer=o["customer"], quantity=quantity, order_remaining=remaining - quantity, unallocated_left=left_stock)


def _labels(con, item_id: int, choices, customer: str | None) -> list[dict]:
    out = []
    for c in choices:
        if c == "confirm_customer":
            if not customer:
                raise StockError("confirm_customer には customer（注文を確かめる売り先の名前）が要ります")
            ok = con.execute("SELECT 1 FROM customer c JOIN sales_price s ON s.customer_id = c.id AND s.item_id = ? WHERE c.name = ?",
                             (item_id, customer)).fetchone()
            if ok is None:
                raise StockError(f"売り先 {customer} にはこの品目の売値がありません")
            out.append(dict(code=c, label=f"{customer}に注文を確かめる", customer=customer))
        elif c == "offer_to_buyers":
            out.append(dict(code=c, label="ほかの仲買に譲る"))
        elif c == "keep":
            out.append(dict(code=c, label="そのまま持つ"))
        else:
            raise StockError(f"選択肢 {c} は使えません（使えるのは {', '.join(CHOICES)}）")
    return out


def ask(con, item_code: str, question: str, choices: list, recommended: str | None, reason: str | None, customer: str | None,
        run_id: int | None) -> dict:
    """引当のない在庫の扱いを人に問う。前の開いた問いは古い問いになる。"""
    it = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()
    if it is None:
        raise StockError(f"品目がありません：{item_code}")
    rows = unallocated(con, it["id"])
    n = sum(r["quantity"] for r in rows)
    if n == 0:
        raise StockError("この品目に引当のない在庫はありません")
    q = (question or "").strip()
    if not 0 < len(q) <= MAX_QUESTION:
        raise StockError(f"question は 1〜{MAX_QUESTION}字で書いてください")
    if not choices or len(set(choices)) != len(choices):
        raise StockError("choices は重ならない 1〜3件で示してください")
    if recommended is not None and recommended not in choices:
        raise StockError("recommended は choices の中から選んでください")
    r = (reason or "").strip() or None
    if r and len(r) > MAX_REASON:
        raise StockError(f"reason は {MAX_REASON}字までで書いてください")
    if customer is None:   # 人が承認した案の行き先が1つなら、その売り先
        named = {x["intended_customer"] for x in rows if x["intended_customer"]}
        customer = named.pop() if len(named) == 1 else None
    labels = _labels(con, it["id"], choices, customer)
    at = db.now_iso()
    with con:
        con.execute("UPDATE stock_question SET status = 'stale', closed_at = ? WHERE item_id = ? AND status = 'open'", (at, it["id"]))
        qid = con.execute("INSERT INTO stock_question (item_id, quantity, question, choices_json, recommended, reason, status, run_id, created_at) "
                          "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                          (it["id"], n, q, json.dumps(labels, ensure_ascii=False), recommended, r, run_id, at)).lastrowid
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'companion', 'stock_question_opened', ?)",
                    (at, json.dumps(dict(question_id=qid, quantity=n, choices=list(choices)), ensure_ascii=False)))
    return dict(question_id=qid, quantity=n, choices=labels)


def answer(con, question_id: int, choice: str, key: str, role: str = "staff") -> dict:
    """人の答え。答えを記録するのと同じトランザクションで出来事 human_choice を書き、AI を起こす。同じ key の繰り返しは前の結果。"""
    prev = con.execute("SELECT id, answer FROM stock_question WHERE idempotency_key = ?", (key,)).fetchone()
    if prev is not None:
        if prev["id"] != question_id or prev["answer"] != choice:
            raise StockError("idempotency_key reused for a different answer")
        return dict(result="answered", question_id=question_id, choice=choice, repeated=True)
    try:
        authority.limit_for(con, role)   # 表に無い役割は止める
    except authority.AuthorityError as e:
        raise StockError(str(e)) from None
    q = con.execute("SELECT q.*, i.code AS item_code FROM stock_question q JOIN item i ON i.id = q.item_id WHERE q.id = ?", (question_id,)).fetchone()
    if q is None:
        raise KeyError(question_id)
    if q["status"] != "open":
        raise StockError(f"この問いは {q['status']} です（状況が変わりました）")
    labels = {c["code"]: c for c in json.loads(q["choices_json"])}
    if choice not in labels:
        raise StockError(f"選択肢 {choice} はこの問いにありません")
    n = sum(r["quantity"] for r in unallocated(con, q["item_id"]))
    at = db.now_iso()
    if n == 0:
        with con:
            con.execute("UPDATE stock_question SET status = 'closed', closed_at = ? WHERE id = ?", (at, q["id"]))
        raise StockError("引当のない在庫はもうありません（注文に充てられました）")
    c = labels[choice]
    with con:
        con.execute("UPDATE stock_question SET status = 'answered', answer = ?, answered_by = ?, answered_at = ?, idempotency_key = ? WHERE id = ?",
                    (choice, role, at, key, q["id"]))
        eid = events._insert(con, "human_choice", dict(source="stock_question", question_id=q["id"], item=q["item_code"], choice=choice,
                                                       label=c["label"], customer=c.get("customer"), quantity=n, answered_by=role), events._now())
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'owner', 'stock_question_answered', ?)",
                    (at, json.dumps(dict(question_id=q["id"], choice=choice, role=role, event_id=eid), ensure_ascii=False)))
    return dict(result="answered", question_id=q["id"], choice=choice, event_id=eid, repeated=False)


def latest_answer(con, item_id: int) -> str | None:
    r = con.execute("SELECT answer FROM stock_question WHERE item_id = ? AND status = 'answered' ORDER BY id DESC LIMIT 1", (item_id,)).fetchone()
    return r[0] if r else None
