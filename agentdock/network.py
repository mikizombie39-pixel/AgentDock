"""Agent Network：組織の間で、掲示・問い合わせ・返事を運ぶ。

- 送る文は、送る側の DB で開示の検査を通す（自社の上限・下限・原価・売り先の名前を止める）
- 運んだ先の DB に出来事を書き、受け手の Claude AI パートナーを起こす
  掲示 → 'info_received'（全ての他の組織）／問い合わせ → 'quote_request'／返事 → 'quote_received'
- 運ぶのは市場の DB（network_message・deal_request）。各組織は自社の DB と、自社に届いたものだけを読む
"""
import json

from agentdock import db, disclosure, events, market


class NetworkError(Exception):
    pass


def _check(owner: str, item_code: str, unit_price_yen, quantity, text: str, on_date: str, terms=()) -> None:
    oc = db.connect(owner)
    try:
        allowed = {v for q, p in terms for v in (p, p * q)}
        reasons = disclosure.check(oc, item_code, unit_price_yen or 0, quantity or 0, text, on_date, also_allowed=allowed)
    finally:
        oc.close()
    if reasons:
        raise disclosure.DisclosureError(reasons)


def _emit(owner: str, kind: str, payload: dict) -> int:
    oc = db.connect(owner)
    try:
        return events.emit(oc, kind, payload)
    finally:
        oc.close()


def post(owner: str, side: str, item_code: str, quantity: int, min_qty: int, unit_price_yen: int,
         message: str, on_date: str, valid_until: str) -> int:
    """掲示を出し、他の全ての組織に「情報が入って来た」を届ける。掲示の番号を返す。"""
    oc = db.connect(owner)
    try:
        rid = market.post_request(oc, owner, side, item_code, quantity, min_qty, unit_price_yen, message, on_date, valid_until)
    finally:
        oc.close()
    for other in db.OWNER_CODES:
        if other != owner:
            _emit(other, "info_received", dict(source="deal_request", request_id=rid, from_owner=owner))
    return rid


def announce_material(broadcast_id: int, sender: str = "iwase") -> None:
    """漁協の資料が届いたことを、受け取った各組織に知らせる。"""
    for other in db.OWNER_CODES:
        if other != sender:
            _emit(other, "info_received", dict(source="broadcast", broadcast_id=broadcast_id, from_owner=sender))


def terms_of(m: dict) -> list[tuple[int, int]]:
    """文の条件の組（数量・値）。条件が1つの文は quantity・unit_price_yen の列だけで持つ。"""
    if m.get("terms_json"):
        return [tuple(t) for t in json.loads(m["terms_json"])]
    if m.get("quantity") and m.get("unit_price_yen"):
        return [(m["quantity"], m["unit_price_yen"])]
    return []


def send(from_owner: str, to_owner: str, kind: str, item_code: str, body: str, on_date: str,
         quantity: int | None = None, unit_price_yen: int | None = None,
         in_reply_to: int | None = None, request_id: int | None = None, terms=None,
         side: str | None = None, valid_until: str | None = None) -> int:
    """問い合わせ（kind='inquiry'）か返事（'offer'・'decline'）を送る。番号を返す。
    terms は条件の組 [(数量, 値), ...]。渡すと最初の組を quantity・unit_price_yen の列にも書く。"""
    terms = [tuple(t) for t in terms] if terms else []
    if not terms and kind == "offer" and quantity and unit_price_yen:
        terms = [(quantity, unit_price_yen)]   # 条件が1つの返事
    if terms:
        quantity, unit_price_yen = terms[0]
    if from_owner not in db.OWNER_CODES or to_owner not in db.OWNER_CODES or from_owner == to_owner:
        raise NetworkError(f"bad parties: {from_owner} -> {to_owner}")
    if kind not in ("inquiry", "offer", "decline"):
        raise NetworkError(f"unknown kind: {kind}")
    if (kind == "inquiry") != (in_reply_to is None):
        raise NetworkError("an inquiry starts a thread; a reply needs in_reply_to")
    q = None
    if in_reply_to is not None:
        q = message(in_reply_to)
        if q["to_owner"] != from_owner or q["from_owner"] != to_owner:
            raise NetworkError(f"message {in_reply_to} was not sent from {to_owner} to {from_owner}")
    if kind == "inquiry" and side not in ("buy", "sell"):
        raise NetworkError("an inquiry needs side 'buy' or 'sell'")
    if kind == "offer" and (not terms or not valid_until):
        raise NetworkError("an offer needs terms and valid_until")
    _check(from_owner, item_code, unit_price_yen, quantity, body, on_date, terms)
    m = db.connect(db.MARKET)
    try:
        with m:
            mid = m.execute(
                "INSERT INTO network_message (from_owner, to_owner, kind, item_code, quantity, unit_price_yen, body, in_reply_to, request_id, trade_date, created_at, terms_json, side) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (from_owner, to_owner, kind, item_code, quantity, unit_price_yen, body, in_reply_to, request_id, on_date, db.now_iso(),
                 json.dumps(terms) if len(terms) > 1 else None, side if kind == "inquiry" else None)).lastrowid
            if kind == "offer":
                # 条件の売り手と買い手は、問い合わせの向きで決まる（買いたいへの返事なら返事をした側が売り手）
                seller, buyer = (from_owner, to_owner) if q["side"] == "buy" else (to_owner, from_owner)
                now = db.now_iso()
                m.executemany(
                    "INSERT INTO offer_term (message_id, seller, buyer, item_code, quantity, unit_price_yen, valid_until, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 'offered', ?, ?)",
                    [(mid, seller, buyer, item_code, tq, tp, valid_until, now, now) for tq, tp in terms])
    finally:
        m.close()
    _emit(to_owner, "quote_request" if kind == "inquiry" else "quote_received",
          dict(message_id=mid, from_owner=from_owner, kind=kind))
    return mid


def terms_for_message(mid: int) -> list[dict]:
    m = db.connect(db.MARKET)
    try:
        return [dict(r) for r in m.execute("SELECT * FROM offer_term WHERE message_id = ? ORDER BY id", (mid,))]
    finally:
        m.close()


def term(term_id: int) -> dict:
    m = db.connect(db.MARKET)
    try:
        r = m.execute("SELECT * FROM offer_term WHERE id = ?", (term_id,)).fetchone()
    finally:
        m.close()
    if r is None:
        raise NetworkError(f"no term: {term_id}")
    return dict(r)


def attach_hold(mid: int, hold_id: int) -> None:
    """売り手の仮押さえの番号を、その返事の条件の全てに結びつける（条件は二者択一で、押さえは1件）。"""
    m = db.connect(db.MARKET)
    try:
        with m:
            m.execute("UPDATE offer_term SET hold_ref = ?, updated_at = ? WHERE message_id = ?", (hold_id, db.now_iso(), mid))
    finally:
        m.close()


def make_agreement(term_id: int, condition: str, status: str, valid_until: str) -> int:
    """AI どうしの条件付きの合意を記録する（正式な売買ではない）。"""
    t = term(term_id)
    m = db.connect(db.MARKET)
    try:
        with m:
            now = db.now_iso()
            return m.execute(
                "INSERT INTO agreement (term_id, seller, buyer, item_code, quantity, unit_price_yen, condition, status, valid_until, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (term_id, t["seller"], t["buyer"], t["item_code"], t["quantity"], t["unit_price_yen"], condition, status, valid_until, now, now)).lastrowid
    finally:
        m.close()


def agreement(aid: int) -> dict:
    m = db.connect(db.MARKET)
    try:
        r = m.execute("SELECT * FROM agreement WHERE id = ?", (aid,)).fetchone()
    finally:
        m.close()
    if r is None:
        raise NetworkError(f"no agreement: {aid}")
    return dict(r)


def message(mid: int) -> dict:
    m = db.connect(db.MARKET)
    try:
        r = m.execute("SELECT * FROM network_message WHERE id = ?", (mid,)).fetchone()
    finally:
        m.close()
    if r is None:
        raise NetworkError(f"no message: {mid}")
    return dict(r)


def message_for(viewer: str, mid: int) -> dict:
    """viewer が送ったか受け取った文だけを読める。"""
    r = message(mid)
    if viewer not in (r["from_owner"], r["to_owner"]):
        raise NetworkError(f"{viewer} cannot read message {mid}")
    return r


def request_for(viewer: str, rid: int) -> dict:
    """掲示は全ての組織が読める。"""
    m = db.connect(db.MARKET)
    try:
        r = m.execute("SELECT * FROM deal_request WHERE id = ?", (rid,)).fetchone()
    finally:
        m.close()
    if r is None:
        raise NetworkError(f"no request: {rid}")
    return dict(r)
