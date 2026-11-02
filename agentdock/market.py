"""参照用の市場：出品、相対の場、取引の双方の承認と登録、帳簿の照合。

安全の条件をコードで守る：
- 古い判断：承認は条件の指紋に結びつく。条件が変わると前の承認は効かない
- 二重の回答：同じ当事者の同じ承認は一度しか記録しない。登録も二度しない
- 片側の失敗：売り手の帳簿と買い手の帳簿を別々に書く。片方だけ書けたら 'incomplete' にし、complete() で残りを書く
- 不成立と未完了の区別：交渉が合わなければ negotiation を 'no_deal'、登録の途中で止まれば trade を 'incomplete'
- 売り過ぎ：1つの出品で登録・承認待ちの数量の合計が出品の数を超えない
"""
import hashlib
import json

from agentdock import db

MAX_ROUNDS = 6


class MarketError(Exception):
    pass


class StaleApproval(MarketError):
    pass


def _con():
    return db.connect(db.MARKET)


def open_lot(lot_no: str, item_code: str, quantity: int, seller: str, seller_kind: str, method: str, trade_date: str) -> int:
    m = _con()
    try:
        with m:
            return m.execute(
                "INSERT INTO lot (lot_no, item_code, quantity, seller, seller_kind, method, trade_date, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (lot_no, item_code, quantity, seller, seller_kind, method, trade_date, db.now_iso())).lastrowid
    finally:
        m.close()


# 相対 ------------------------------------------------------------------------

def open_negotiation(lot_no: str, buyer: str) -> int:
    m = _con()
    try:
        lot = m.execute("SELECT id, seller FROM lot WHERE lot_no = ?", (lot_no,)).fetchone()
        if lot is None:
            raise KeyError(lot_no)
        if lot["seller"] == buyer:
            raise MarketError("seller cannot buy own lot")
        with m:
            return m.execute("INSERT INTO negotiation (lot_id, buyer, status, updated_at) VALUES (?, ?, 'open', ?)",
                             (lot["id"], buyer, db.now_iso())).lastrowid
    finally:
        m.close()


def negotiation(nid: int) -> dict:
    m = _con()
    try:
        r = m.execute("SELECT id, lot_id, buyer, version, rounds, status FROM negotiation WHERE id = ?", (nid,)).fetchone()
        if r is None:
            raise KeyError(nid)
        return dict(r)
    finally:
        m.close()


def exchange(nid: int, version: int) -> int:
    """申し出か返事を1往復と数える。版が違えば古い申し出として止める。上限を越えたら no_deal。新しい版を返す。"""
    m = _con()
    try:
        r = m.execute("SELECT version, rounds, status FROM negotiation WHERE id = ?", (nid,)).fetchone()
        if r is None:
            raise KeyError(nid)
        if r["status"] != "open":
            raise MarketError(f"negotiation {nid} is {r['status']}")
        if r["version"] != version:
            raise StaleApproval(f"negotiation {nid} is v{r['version']}, got v{version}")
        if r["rounds"] >= MAX_ROUNDS:
            with m:
                m.execute("UPDATE negotiation SET status = 'no_deal', updated_at = ? WHERE id = ?", (db.now_iso(), nid))
            raise MarketError(f"negotiation {nid} reached {MAX_ROUNDS} rounds: no_deal")
        with m:
            m.execute("UPDATE negotiation SET version = version + 1, rounds = rounds + 1, updated_at = ? WHERE id = ?", (db.now_iso(), nid))
        return r["version"] + 1
    finally:
        m.close()


def no_deal(nid: int) -> None:
    """交渉が合わなかった。登録の途中で止まった取引（incomplete）とは別の状態。"""
    m = _con()
    try:
        with m:
            n = m.execute("UPDATE negotiation SET status = 'no_deal', updated_at = ? WHERE id = ? AND status = 'open'",
                          (db.now_iso(), nid)).rowcount
        if n != 1:
            raise MarketError(f"negotiation {nid} is not open")
    finally:
        m.close()


# 取引 ------------------------------------------------------------------------

def terms_fingerprint(lot_no: str, seller: str, buyer: str, unit_price_yen: int, quantity: int, version: int) -> str:
    canon = json.dumps([lot_no, seller, buyer, unit_price_yen, quantity, version], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def propose(trade_id: str, lot_no: str, buyer: str, unit_price_yen: int, quantity: int, version: int = 1) -> str:
    """条件をそろえ、双方の承認を待つ取引を作る。指紋を返す。"""
    m = _con()
    try:
        lot = m.execute("SELECT id, seller, quantity FROM lot WHERE lot_no = ?", (lot_no,)).fetchone()
        if lot is None:
            raise KeyError(lot_no)
        if lot["seller"] == buyer:
            raise MarketError("seller cannot buy own lot")
        used = m.execute("SELECT coalesce(sum(quantity), 0) FROM trade WHERE lot_id = ? AND status IN ('awaiting_approval', 'registered', 'incomplete')",
                         (lot["id"],)).fetchone()[0]
        if used + quantity > lot["quantity"]:
            raise MarketError(f"lot {lot_no}: {used} + {quantity} > {lot['quantity']}")
        fp = terms_fingerprint(lot_no, lot["seller"], buyer, unit_price_yen, quantity, version)
        with m:
            m.execute("INSERT INTO trade (trade_id, lot_id, seller, buyer, unit_price_yen, quantity, version, terms_fingerprint, status, created_at) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_approval', ?)",
                      (trade_id, lot["id"], lot["seller"], buyer, unit_price_yen, quantity, version, fp, db.now_iso()))
        return fp
    finally:
        m.close()


def revise_terms(trade_id: str, unit_price_yen: int, quantity: int) -> str:
    """承認待ちの取引の条件を改める。版を上げ、前の条件への承認を消す（古い承認で登録しない）。新しい指紋を返す。"""
    m = _con()
    try:
        t = m.execute("SELECT t.id, t.lot_id, t.seller, t.buyer, t.version, t.status, t.quantity, l.lot_no, l.quantity AS lot_qty "
                      "FROM trade t JOIN lot l ON l.id = t.lot_id WHERE t.trade_id = ?", (trade_id,)).fetchone()
        if t is None:
            raise KeyError(trade_id)
        if t["status"] != "awaiting_approval":
            raise MarketError(f"{trade_id} is {t['status']}")
        used = m.execute("SELECT coalesce(sum(quantity), 0) FROM trade WHERE lot_id = ? AND id != ? AND status IN ('awaiting_approval', 'registered', 'incomplete')",
                         (t["lot_id"], t["id"])).fetchone()[0]
        if used + quantity > t["lot_qty"]:
            raise MarketError(f"lot {t['lot_no']}: {used} + {quantity} > {t['lot_qty']}")
        v = t["version"] + 1
        fp = terms_fingerprint(t["lot_no"], t["seller"], t["buyer"], unit_price_yen, quantity, v)
        with m:
            m.execute("UPDATE trade SET unit_price_yen = ?, quantity = ?, version = ?, terms_fingerprint = ? WHERE id = ?",
                      (unit_price_yen, quantity, v, fp, t["id"]))
            m.execute("DELETE FROM trade_approval WHERE trade_id = ?", (trade_id,))
        return fp
    finally:
        m.close()


def trade(trade_id: str) -> dict:
    m = _con()
    try:
        t = m.execute("SELECT * FROM trade WHERE trade_id = ?", (trade_id,)).fetchone()
        if t is None:
            raise KeyError(trade_id)
        out = dict(t)
        out["approvals"] = sorted(r[0] for r in m.execute("SELECT party FROM trade_approval WHERE trade_id = ?", (trade_id,)))
        out["seller_booked"] = m.execute("SELECT 1 FROM seller_ledger WHERE trade_id = ?", (trade_id,)).fetchone() is not None
        out["buyer_booked"] = m.execute("SELECT 1 FROM buyer_ledger WHERE trade_id = ?", (trade_id,)).fetchone() is not None
        return out
    finally:
        m.close()


def approve(trade_id: str, party: str, terms_fp: str, fail_buyer_side: bool = False) -> str:
    """当事者の最終承認。双方がそろったら登録する。取引の状態を返す。
    fail_buyer_side は試験用（売り手の帳簿を書いた後に、買い手の帳簿の書き込みが失敗した形）。"""
    m = _con()
    try:
        t = m.execute("SELECT trade_id, seller, buyer, terms_fingerprint, status FROM trade WHERE trade_id = ?", (trade_id,)).fetchone()
        if t is None:
            raise KeyError(trade_id)
        if party not in (t["seller"], t["buyer"]):
            raise MarketError(f"{party} is not a party of {trade_id}")
        if terms_fp != t["terms_fingerprint"]:
            raise StaleApproval(f"{trade_id}: approval is for other terms")
        if t["status"] != "awaiting_approval":
            return t["status"]
        with m:
            m.execute("INSERT OR IGNORE INTO trade_approval (trade_id, party, terms_fingerprint, approved_at) VALUES (?, ?, ?, ?)",
                      (trade_id, party, terms_fp, db.now_iso()))
        parties = {r[0] for r in m.execute("SELECT party FROM trade_approval WHERE trade_id = ? AND terms_fingerprint = ?", (trade_id, terms_fp))}
    finally:
        m.close()
    if parties != {t["seller"], t["buyer"]}:
        return "awaiting_approval"
    return _register(trade_id, fail_buyer_side)


def _register(trade_id: str, fail_buyer_side: bool = False) -> str:
    m = _con()
    try:
        t = m.execute("SELECT * FROM trade WHERE trade_id = ?", (trade_id,)).fetchone()
        now = db.now_iso()
        with m:   # 売り手の帳簿（1つ目の書き込み）。状態は先に incomplete にしておく
            m.execute("INSERT OR IGNORE INTO seller_ledger (trade_id, seller, unit_price_yen, quantity, recorded_at) VALUES (?, ?, ?, ?, ?)",
                      (trade_id, t["seller"], t["unit_price_yen"], t["quantity"], now))
            m.execute("UPDATE trade SET status = 'incomplete' WHERE trade_id = ?", (trade_id,))
        if fail_buyer_side:
            raise MarketError(f"{trade_id}: buyer ledger write failed (test)")
        with m:   # 買い手の帳簿（2つ目の書き込み）
            m.execute("INSERT OR IGNORE INTO buyer_ledger (trade_id, buyer, unit_price_yen, quantity, recorded_at) VALUES (?, ?, ?, ?, ?)",
                      (trade_id, t["buyer"], t["unit_price_yen"], t["quantity"], now))
            m.execute("UPDATE trade SET status = 'registered' WHERE trade_id = ?", (trade_id,))
        return "registered"
    finally:
        m.close()


def complete(trade_id: str) -> str:
    """incomplete の取引の残りの帳簿を書く。何度呼んでも二重には書かない。"""
    t = trade(trade_id)
    if t["status"] == "registered":
        return "registered"
    if t["status"] != "incomplete":
        raise MarketError(f"{trade_id} is {t['status']}")
    return _register(trade_id)


# 持ち主の側の照合 -----------------------------------------------------------

def reconcile_owner(code: str) -> list[dict]:
    """市場の取引のうち持ち主が当事者のものを、持ち主の DB の帳簿と照合の記録に写す。
    両方の帳簿がそろえば match、片方なら pending（未完了・要確認）、中身が違えば mismatch。"""
    m = _con()
    try:
        trades = [dict(r) for r in m.execute(
            "SELECT t.trade_id, t.seller, t.buyer, t.unit_price_yen, t.quantity, t.status, l.item_code FROM trade t JOIN lot l ON l.id = t.lot_id "
            "WHERE (t.seller = ? OR t.buyer = ?) AND t.status IN ('registered', 'incomplete') ORDER BY t.id", (code, code))]
        for t in trades:
            s = m.execute("SELECT unit_price_yen, quantity FROM seller_ledger WHERE trade_id = ?", (t["trade_id"],)).fetchone()
            b = m.execute("SELECT unit_price_yen, quantity FROM buyer_ledger WHERE trade_id = ?", (t["trade_id"],)).fetchone()
            terms = (t["unit_price_yen"], t["quantity"])
            if s is None or b is None:
                t["result"] = "pending"
            elif tuple(s) != terms or tuple(b) != terms:
                t["result"] = "mismatch"
            else:
                t["result"] = "match"
    finally:
        m.close()
    c = db.connect(code)
    try:
        item = {r["code"]: r["id"] for r in c.execute("SELECT id, code FROM item")}
        now = db.now_iso()
        with c:
            for t in trades:
                c.execute("INSERT INTO reconciliation (connector, external_trade_id, result, checked_at, detail) VALUES ('reference_market', ?, ?, ?, ?)",
                          (t["trade_id"], t["result"], now, t["status"]))
                if t["result"] == "match":
                    c.execute("INSERT OR IGNORE INTO ledger_entry (connector, external_trade_id, item_id, unit_price_yen, quantity, side, registered_at) "
                              "VALUES ('reference_market', ?, ?, ?, ?, ?, ?)",
                              (t["trade_id"], item[t["item_code"]], t["unit_price_yen"], t["quantity"], "sell" if t["seller"] == code else "buy", now))
    finally:
        c.close()
    return [dict(trade_id=t["trade_id"], result=t["result"]) for t in trades]
