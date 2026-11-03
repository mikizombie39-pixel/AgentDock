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
         in_reply_to: int | None = None, request_id: int | None = None, terms=None) -> int:
    """問い合わせ（kind='inquiry'）か返事（'offer'・'decline'）を送る。番号を返す。
    terms は条件の組 [(数量, 値), ...]。渡すと最初の組を quantity・unit_price_yen の列にも書く。"""
    terms = [tuple(t) for t in terms] if terms else []
    if terms:
        quantity, unit_price_yen = terms[0]
    if from_owner not in db.OWNER_CODES or to_owner not in db.OWNER_CODES or from_owner == to_owner:
        raise NetworkError(f"bad parties: {from_owner} -> {to_owner}")
    if kind not in ("inquiry", "offer", "decline"):
        raise NetworkError(f"unknown kind: {kind}")
    if (kind == "inquiry") != (in_reply_to is None):
        raise NetworkError("an inquiry starts a thread; a reply needs in_reply_to")
    if in_reply_to is not None:
        q = message(in_reply_to)
        if q["to_owner"] != from_owner or q["from_owner"] != to_owner:
            raise NetworkError(f"message {in_reply_to} was not sent from {to_owner} to {from_owner}")
    _check(from_owner, item_code, unit_price_yen, quantity, body, on_date, terms)
    m = db.connect(db.MARKET)
    try:
        with m:
            mid = m.execute(
                "INSERT INTO network_message (from_owner, to_owner, kind, item_code, quantity, unit_price_yen, body, in_reply_to, request_id, trade_date, created_at, terms_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (from_owner, to_owner, kind, item_code, quantity, unit_price_yen, body, in_reply_to, request_id, on_date, db.now_iso(),
                 json.dumps(terms) if len(terms) > 1 else None)).lastrowid
    finally:
        m.close()
    _emit(to_owner, "quote_request" if kind == "inquiry" else "quote_received",
          dict(message_id=mid, from_owner=from_owner, kind=kind))
    return mid


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
