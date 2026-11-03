"""業務の出来事（Hooks）：仲間を起こす契機を、持ち主の DB の biz_event 表に書く・取る・済みにする。

決まり：
- 出来事は業務の記録と同じトランザクションで書く（注文だけ入って出来事が無い、を起こさない）
- 1つの出来事を引き受けられるのは1回の起動だけ（claim は待ちの行にだけ効く）
- 済みにできるのは引き受けた起動だけ。起動が途中で止まったら release で待ちに戻す
"""
import datetime
import json

from agentdock import db

KINDS = ('order_new', 'order_changed', 'quote_request', 'quote_received',
         'human_choice', 'trade_registered', 'handoff', 'info_received',
         'staff_question')   # info_received は移行 008・staff_question は移行 010 で追加


class EventError(Exception):
    pass


def _now() -> str:
    """出来事の時刻はマイクロ秒まで持つ（組織をまたいで起きた順に並べるため）。"""
    return datetime.datetime.now().astimezone().isoformat(timespec="microseconds")


def _insert(con, kind: str, payload: dict, at: str) -> int:
    if kind not in KINDS:
        raise EventError(f"unknown event kind: {kind}")
    return con.execute("INSERT INTO biz_event (at, kind, payload_json) VALUES (?, ?, ?)",
                       (at, kind, json.dumps(payload, ensure_ascii=False, sort_keys=True))).lastrowid


def emit(con, kind: str, payload: dict, at: str | None = None) -> int:
    """出来事を1件書く。番号を返す。"""
    with con:
        return _insert(con, kind, payload, at or _now())


def order_received(con, customer: str, item_code: str, quantity: int, sell_price_yen: int,
                   order_date: str, at: str | None = None, key: str | None = None) -> tuple[int, int]:
    """注文を受けた：注文の行と出来事 order_new を同じトランザクションで書く。(注文の番号, 出来事の番号) を返す。"""
    at = at or _now()
    with con:
        cust = con.execute("SELECT id FROM customer WHERE name = ?", (customer,)).fetchone()
        item = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()
        if cust is None or item is None:
            raise EventError(f"unknown customer or item: {customer} / {item_code}")
        oid = con.execute(
            "INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            (cust[0], item[0], quantity, sell_price_yen, order_date, at)).lastrowid
        payload = dict(order_id=oid, customer=customer, item=item_code, quantity=quantity, order_date=order_date)
        if key:
            payload["key"] = key   # 画面の操作の押し直しを見分ける
        eid = _insert(con, "order_new", payload, at)
    return oid, eid


def order_noticed(con, customer: str, item_code: str, order_date: str, at: str | None = None) -> tuple[int, int]:
    """すでに入っている注文（朝の注文）の出来事 order_new を書く。注文の行は増やさない。(注文の番号, 出来事の番号) を返す。"""
    with con:
        o = con.execute("SELECT o.id, o.quantity FROM order_book o JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id "
                        "WHERE c.name = ? AND i.code = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id LIMIT 1",
                        (customer, item_code, order_date)).fetchone()
        if o is None:
            raise EventError(f"no open order: {customer} / {item_code}")
        eid = _insert(con, "order_new", dict(order_id=o[0], customer=customer, item=item_code, quantity=o[1], order_date=order_date), at or _now())
    return o[0], eid


def _row(r) -> dict:
    d = dict(r)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


def get(con, event_id: int) -> dict:
    r = con.execute("SELECT * FROM biz_event WHERE id = ?", (event_id,)).fetchone()
    if r is None:
        raise EventError(f"no event: {event_id}")
    return _row(r)


def pending(con, kinds=None) -> list[dict]:
    """まだ誰も引き受けていない待ちの出来事を、古い順に返す。"""
    q = "SELECT * FROM biz_event WHERE status = 'pending' AND run_id IS NULL"
    args = []
    if kinds:
        q += f" AND kind IN ({', '.join('?' * len(kinds))})"
        args += list(kinds)
    return [_row(r) for r in con.execute(q + " ORDER BY id", args)]


def claim(con, event_id: int, run_id: int) -> bool:
    """出来事を起動 run_id が引き受ける。すでに誰かが引き受けたか済みなら False。"""
    with con:
        n = con.execute("UPDATE biz_event SET run_id = ? WHERE id = ? AND status = 'pending' AND run_id IS NULL",
                        (run_id, event_id)).rowcount
    return n == 1


def done(con, event_id: int, run_id: int) -> None:
    """引き受けた起動だけが済みにできる。"""
    with con:
        n = con.execute("UPDATE biz_event SET status = 'handled', handled_at = ? WHERE id = ? AND status = 'pending' AND run_id = ?",
                        (db.now_iso(), event_id, run_id)).rowcount
    if n != 1:
        raise EventError(f"event {event_id} is not claimed by run {run_id}")


def release(con, event_id: int, run_id: int) -> None:
    """起動が途中で止まった時、引き受けを外して待ちに戻す（済みの出来事は戻さない）。"""
    with con:
        n = con.execute("UPDATE biz_event SET run_id = NULL WHERE id = ? AND status = 'pending' AND run_id = ?",
                        (event_id, run_id)).rowcount
    if n != 1:
        raise EventError(f"event {event_id} is not claimed by run {run_id}")
