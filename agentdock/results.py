"""漁協がせりと入札の結果を全員へ送り、各社の仲間が自社の注文と突き合わせる。

決まり：
- 結果の公開の部分（ロット・品目・値・数量）は、全員に同じものを送る。他社の名前は送らない
- 自社の落札だけ is_own=1 の印で区別する（名前では区別しない）
- 公開の部分の指紋が1社でも違えば ResultError で止め、どの持ち主の DB にも何も記録しない
- 同じ結果を二度送っても行は増えない（result_id で一意）
- 突き合わせでは、結果が届いていない品目を「不足」と書かない（未着と不足を分ける）
"""
import hashlib
import json

from agentdock import criteria, db
from agentdock.seed import scenario as sc

CONNECTOR = "reference_market"
BUYERS = ("amiya", "buyer_b", "buyer_c")
SELLER = "iwase"


class ResultError(Exception):
    pass


def allocate(bids, quantity: int) -> dict:
    """入札の割り当て：高い値の順に、出品の数が尽きるまで。同じ値は先に並んだ方から。"""
    left, won = quantity, {}
    for who, _price, q in sorted(bids, key=lambda b: -b[1]):
        n = min(q, left)
        if n > 0:
            won[who] = n
        left -= n
    return won


def today_results(on_date: str = sc.DEMO_DATE) -> list[dict]:
    """デモの日の結果（入札とせり）。入札の数はコードで割り当てる。"""
    rows = []
    t = sc.TODAY_TENDER
    prices = {who: p for who, p, _ in t["bids"]}
    for who, q in allocate(t["bids"], t["quantity"]).items():
        rows.append(dict(lot_no=t["lot_no"], item=t["item"], method="tender", buyer=who, unit_price_yen=prices[who], quantity=q))
    a = sc.TODAY_AUCTION
    for who, p, q in a["sales"]:
        rows.append(dict(lot_no=a["lot_no"], item=a["item"], method="auction", buyer=who, unit_price_yen=p, quantity=q))
    for n, r in enumerate(rows, 1):
        r["result_id"] = f"{on_date}-{r['lot_no']}-{n}"
        r["trade_date"] = on_date
    return rows


def _public(row: dict) -> dict:
    """全員に同じに見せる部分（買い手の名前を除く）。"""
    return {k: row[k] for k in ("result_id", "lot_no", "item", "unit_price_yen", "quantity", "trade_date")}


def fingerprint(public_rows) -> str:
    canon = json.dumps(sorted(public_rows, key=lambda r: r["result_id"]), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def publish(rows: list[dict], tamper=None) -> str:
    """結果を市場の DB に記録し、漁協と仲買の全員へ送る。指紋を返す。
    tamper は試験用（特定の宛先の公開の部分を書き換えて、検査が止めるかを見る）。"""
    if len({r["result_id"] for r in rows}) != len(rows):
        raise ResultError("duplicate result_id")
    fp = fingerprint([_public(r) for r in rows])
    copies = {}
    for who in (SELLER, *BUYERS):
        pub = [_public(r) for r in rows]
        if tamper and who in tamper:
            tamper[who](pub)
        copies[who] = pub
    bad = [w for w, pub in copies.items() if fingerprint(pub) != fp]
    if bad:
        raise ResultError(f"public part differs for: {bad}")
    s = db.connect(SELLER)
    try:
        landed = {r["code"]: r["quantity"] for r in s.execute(
            "SELECT i.code, l.quantity FROM landing l JOIN item i ON i.id = l.item_id WHERE l.kind = 'actual' AND l.landing_date = ?",
            (rows[0]["trade_date"],))} if rows else {}
    finally:
        s.close()
    sold = {}
    for r in rows:
        sold[r["item"]] = sold.get(r["item"], 0) + r["quantity"]
    over = [i for i, q in sold.items() if q > landed.get(i, 0)]
    if over:
        raise ResultError(f"sold more than landed: {over}")

    m = db.connect(db.MARKET)
    try:
        with m:
            m.executemany(
                "INSERT OR IGNORE INTO auction_result (result_id, lot_no, item_code, buyer, unit_price_yen, quantity, trade_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["result_id"], r["lot_no"], r["item"], r["buyer"], r["unit_price_yen"], r["quantity"], r["trade_date"]) for r in rows])
    finally:
        m.close()

    _deliver(rows, copies)
    return fp


def _deliver(rows: list[dict], copies: dict) -> None:
    """各社の DB へ書く。既に届いた行は増やさない（INSERT OR IGNORE）。"""
    now = db.now_iso()
    buyer_of = {r["result_id"]: r["buyer"] for r in rows}
    for who, pub in copies.items():
        c = db.connect(who)
        try:
            item = {r["code"]: r["id"] for r in c.execute("SELECT id, code FROM item")}
            with c:
                c.executemany(
                    "INSERT OR IGNORE INTO auction_result_in (connector, result_id, item_id, lot_no, unit_price_yen, quantity, is_own, received_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(CONNECTOR, p["result_id"], item[p["item"]], p["lot_no"], p["unit_price_yen"], p["quantity"],
                      1 if who == SELLER or buyer_of[p["result_id"]] == who else 0, now) for p in pub])
        finally:
            c.close()


def _market_rows(on_date: str) -> list[dict]:
    m = db.connect(db.MARKET)
    try:
        return [dict(r) for r in m.execute(
            "SELECT result_id, lot_no, item_code AS item, buyer, unit_price_yen, quantity, trade_date FROM auction_result WHERE trade_date = ? ORDER BY result_id",
            (on_date,))]
    finally:
        m.close()


def resend(on_date: str = sc.DEMO_DATE) -> list[str]:
    """途中の社で書き込みが失敗した時の送り直し。市場の記録を正として、届いていない行だけを足す。
    足した宛先を返す。届いた後に書き換えられた行は直さない（verify が False のまま残り、人が見る）。"""
    rows = _market_rows(on_date)
    if not rows:
        raise ResultError(f"no market results: {on_date}")
    want = {r["result_id"] for r in rows}
    missing = []
    for who in (SELLER, *BUYERS):
        c = db.connect(who)
        try:
            have = {r["result_id"] for r in _received(c, on_date)}
        finally:
            c.close()
        if want - have:
            missing.append(who)
    _deliver(rows, {who: [_public(r) for r in rows] for who in missing})
    return missing


def _received(con, on_date: str) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT a.result_id, i.code AS item, a.lot_no, a.unit_price_yen, a.quantity, a.is_own "
        "FROM auction_result_in a JOIN item i ON i.id = a.item_id WHERE a.connector = ? AND a.result_id LIKE ? ORDER BY a.result_id",
        (CONNECTOR, f"{on_date}-%"))]


def verify(on_date: str = sc.DEMO_DATE) -> bool:
    """市場の記録と、全員が受け取った公開の部分の指紋がそろっているか。自社の印が正しいか。"""
    market = _market_rows(on_date)
    if not market:
        return False
    fp = fingerprint([_public(r) for r in market])
    buyer_of = {r["result_id"]: r["buyer"] for r in market}
    for who in (SELLER, *BUYERS):
        c = db.connect(who)
        try:
            got = _received(c, on_date)
        finally:
            c.close()
        pub = [dict(r, trade_date=on_date) for r in got]
        if fingerprint([_public(r) for r in pub]) != fp:
            return False
        for r in got:
            expect = 1 if who == SELLER or buyer_of[r["result_id"]] == who else 0
            if r["is_own"] != expect:
                return False
    return True


def reconcile(code: str, on_date: str = sc.DEMO_DATE) -> dict:
    """持ち主の DB だけを読み、今日の結果を突き合わせる。数字はすべてコードで出す。"""
    con = db.connect(code)
    try:
        role = con.execute("SELECT role FROM owner").fetchone()[0]
        got = _received(con, on_date)
        return _reconcile_seller(con, got, on_date) if role == "seller" else _reconcile_buyer(con, got, on_date)
    finally:
        con.close()


def _reconcile_buyer(con, got, on_date):
    arrived = {r["item"] for r in got}
    lines = []
    orders = con.execute(
        "SELECT i.code AS item, c.name AS customer, o.quantity FROM order_book o JOIN item i ON i.id = o.item_id "
        "JOIN customer c ON c.id = o.customer_id WHERE o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY i.id, c.id",
        (on_date,)).fetchall()
    by_item = {}
    for o in orders:
        by_item.setdefault(o["item"], []).append((o["customer"], o["quantity"]))
    for item, custs in by_item.items():
        ordered = sum(q for _, q in custs)
        own = [r for r in got if r["item"] == item and r["is_own"]]
        public = [r["unit_price_yen"] for r in got if r["item"] == item]
        won = sum(r["quantity"] for r in own)
        line: dict[str, object] = {"item": item, "orders": [dict(customer=c, quantity=q) for c, q in custs], "ordered": ordered}
        if item not in arrived:
            line.update(status="awaiting_result")
        else:
            short = max(0, ordered - won)
            spare = criteria.spare_quantity(won=won, ordered=ordered)
            line.update(
                won=won, short=short, spare=spare,
                own_avg_yen=round(sum(r["unit_price_yen"] * r["quantity"] for r in own) / won) if won else None,
                public_low_yen=min(public), public_high_yen=max(public),
                status="short" if short else ("spare" if spare else "filled"))
        lines.append(line)
    return dict(owner=con.execute("SELECT code FROM owner").fetchone()[0], date=on_date, lines=lines,
                offers=_cheap_offers(con, by_item, on_date))


def _cheap_offers(con, by_item, on_date):
    """届いた資料のうち、今日の見込みの値が直近より安い品目と、その品目を注文した売り先の売れ方。"""
    row = con.execute("SELECT broadcast_id, body_json FROM inbox_broadcast ORDER BY broadcast_id DESC LIMIT 1").fetchone()
    if row is None:
        return []
    body = json.loads(row["body_json"])
    if body.get("date") != on_date:
        return []
    out = []
    for e in body["items"]:
        exp, med = e.get("today_expected_yen"), e.get("recent_median_yen")
        if exp is None or not med or exp / med > criteria.CHEAP_RATIO:
            continue
        sells = [dict(customer=c, ordered=q, **criteria.sell_ratio(con, e["item"], c, on_date)) for c, q in by_item.get(e["item"], [])]
        out.append(dict(item=e["item"], broadcast_id=row["broadcast_id"], expected_yen=exp, recent_median_yen=med,
                        ratio_to_recent=round(exp / med, 3), quantity=e["quantity"], customers=sells))
    return out


def _reconcile_seller(con, got, on_date):
    lines = []
    for r in con.execute(
            "SELECT i.code AS item, l.quantity FROM landing l JOIN item i ON i.id = l.item_id "
            "WHERE l.landing_date = ? AND l.kind = 'actual' ORDER BY i.id", (on_date,)):
        item, landed = r["item"], r["quantity"]
        rows = [x for x in got if x["item"] == item]
        line: dict[str, object] = {"item": item, "landed": landed}
        if not rows:
            line.update(status="not_sold_yet")
        else:
            sold = sum(x["quantity"] for x in rows)
            if sold > landed:
                raise ResultError(f"sold more than landed: {item} {sold} > {landed}")
            line.update(sold=sold, remaining=landed - sold, status="remaining" if landed > sold else "sold_out")
            if landed > sold and con.execute(
                    "SELECT 1 FROM seller_rule s JOIN item i ON i.id = s.item_id WHERE i.code = ?", (item,)).fetchone():
                floor, held = criteria.floor_yen(con, item, on_date)
                line.update(floor_yen=floor, floor_held_up=held)
        lines.append(line)
    return dict(owner=SELLER, date=on_date, lines=lines)
