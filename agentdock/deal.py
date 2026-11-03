"""担当者の判断（承認・拒否・保留）と、正式な取引の確定。

  提案 → 条件付きの合意 → 担当者の承認 → 全ての組織の条件の再検証 → 正式な確定（両方の帳簿・在庫・監査の記録）

決まり：
- 表示した時の照合番号と、承認の時に計算し直した照合番号が違えば、古い案では確定しない（'stale'）
- 承認の時に、権限（1件 50万円）・値・在庫・期限・相手の組織の事前の承認を確かめ直す
- 売り先の注文を欠いてほかの売り先に回す案（顧客の間の優先）は、権限の表で許された役割（社長）だけが確定できる
- 漁協は、示した条件（期限の内・仮押さえ済み・担当者の権限の内）を事前の承認として扱う
- B社は、担当者が事前に承認した条件の内でした条件付きの合意を、B社の承認として扱う。超えていれば確定しない
- 同じ操作は idempotency_key で1回だけ。途中で止まったら 'incomplete' のまま残し、resume で残りを進める
- 拒否・期限切れの時は、仮押さえと条件付きの合意を取り消す
"""
import json

from agentdock import authority, consult, db, events, market, network, workspace
from agentdock.seed import scenario as sc

STEPS = ("buy_trade", "seller_stock", "terms", "sell_trades", "buyer_side", "own_books")


class DealError(Exception):
    pass


def _q1(con, sql, args=()):
    return con.execute(sql, args).fetchone()


def _audit(owner: str, actor: str, event: str, detail: dict) -> None:
    c = db.connect(owner)
    try:
        with c:
            c.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, ?, ?, ?)",
                      (db.now_iso(), actor, event, json.dumps(detail, ensure_ascii=False)))
    finally:
        c.close()


def expire(now: str) -> dict:
    """期限を過ぎた仮押さえ・条件・条件付きの合意を下ろす。"""
    m = db.connect(db.MARKET)
    try:
        with m:
            t = m.execute("UPDATE offer_term SET status = 'expired', updated_at = ? WHERE status = 'offered' AND valid_until <= ?", (db.now_iso(), now)).rowcount
            a = m.execute("UPDATE agreement SET status = 'expired', updated_at = ? WHERE status IN ('conditional', 'needs_staff') AND valid_until <= ?",
                          (db.now_iso(), now)).rowcount
    finally:
        m.close()
    h = 0
    for owner in db.OWNER_CODES:
        c = db.connect(owner)
        try:
            with c:
                h += c.execute("UPDATE stock_hold SET status = 'expired', updated_at = ? WHERE status = 'held' AND expires_at <= ?", (now, now)).rowcount
        finally:
            c.close()
    return dict(terms=t, agreements=a, holds=h)


# ── 承認の時の再検証 ────────────────────────────────────────────

def _verify_seller(term: dict, now: str) -> list[str]:
    """売り手（漁協）の事前の承認：仮押さえが期限の内で数量が足り、在庫があり、担当者の権限の内。"""
    errs = []
    c = db.connect(term["seller"])
    try:
        h = _q1(c, "SELECT * FROM stock_hold WHERE id = ?", (term["hold_ref"],))
        if h is None or h["status"] != "held":
            errs.append(f"{sc.OWNERS[term['seller']]['name']}の仮押さえが無い（{h['status'] if h else '無し'}）")
        elif h["expires_at"] <= now:
            errs.append(f"{sc.OWNERS[term['seller']]['name']}の仮押さえは期限切れ")
        elif h["quantity"] < term["quantity"] or h["buyer"] != term["buyer"]:
            errs.append("仮押さえの数量か相手が条件と合わない")
        if h is not None:
            s = _q1(c, "SELECT quantity, status FROM stock WHERE id = ?", (h["stock_id"],))
            other = _q1(c, "SELECT coalesce(sum(quantity), 0) FROM stock_hold WHERE stock_id = ? AND id != ? AND status = 'held' AND expires_at > ?",
                        (h["stock_id"], h["id"], now))[0]
            if s is None or s["status"] not in ("for_sale", "unallocated") or s["quantity"] - other < term["quantity"]:
                errs.append(f"{sc.OWNERS[term['seller']]['name']}の在庫が足りない")
        lim = authority.limit_for(c, "staff")
        if lim is not None and term["quantity"] * term["unit_price_yen"] > lim:
            errs.append(f"{sc.OWNERS[term['seller']]['name']}の担当者の権限を超える")
    finally:
        c.close()
    return errs


def _verify_buyer(ag: dict, now: str) -> list[str]:
    """買い手（B社）の事前の承認：合意が条件付きで期限の内・事前の承認の内・注文が残っている。"""
    errs = []
    name = sc.OWNERS[ag["buyer"]]["name"]
    if ag["status"] == "needs_staff":
        errs.append(f"{name}の担当者の承認待ち（事前の承認を超える合意）")
    elif ag["status"] != "conditional":
        errs.append(f"{name}との合意は {ag['status']}")
    if ag["valid_until"] <= now:
        errs.append(f"{name}との合意は期限切れ")
    c = db.connect(ag["buyer"])
    try:
        it = _q1(c, "SELECT id FROM item WHERE code = ?", (ag["item_code"],))
        pre = _q1(c, "SELECT * FROM preapproval WHERE item_id = ? AND side = 'buy' AND status = 'active'", (it[0],))
        if pre is None or pre["valid_until"] <= now or ag["quantity"] > pre["quantity"] or (
                pre["max_unit_price_yen"] is not None and ag["unit_price_yen"] > pre["max_unit_price_yen"]):
            errs.append(f"{name}の事前の承認の範囲の外")
        o = _q1(c, "SELECT coalesce(sum(quantity), 0) FROM order_book WHERE item_id = ? AND status IN ('open', 'partly_filled')", (it[0],))[0]
        if o < ag["quantity"]:
            errs.append(f"{name}の注文が残っていない")
    finally:
        c.close()
    return errs


# ── 判断の入口 ─────────────────────────────────────────────────

def decide(owner: str, case_id: int, option_id: int | None, action: str, shown_fingerprint: str | None,
           idempotency_key: str, actor: str = "staff", now: str | None = None, fail_at: str | None = None) -> dict:
    """担当者の判断を1回だけ処理する。結果の dict を返す（同じ key なら前の結果を返す）。"""
    now = now or f"{sc.DEMO_DATE}T06:30:00+09:00"
    if action not in ("approve", "reject", "hold"):
        raise DealError(f"unknown action: {action}")
    con = db.connect(owner)
    try:
        prev = _q1(con, "SELECT result, detail FROM case_decision WHERE idempotency_key = ?", (idempotency_key,))
        if prev is not None:
            out = json.loads(prev["detail"])
            out["repeated"] = True
            return out
        case = _q1(con, "SELECT * FROM deal_case WHERE id = ?", (case_id,))
        if case is None:
            raise DealError(f"no case: {case_id}")
        opt = _q1(con, "SELECT * FROM board_option WHERE id = ? AND case_id = ?", (option_id, case_id)) if option_id else None
        if action == "approve" and opt is None:
            raise DealError("approve needs an option")
    finally:
        con.close()
    expire(now)
    if action == "hold":
        out = dict(result="held", case_id=case_id)
        _set_case(owner, case_id, "held")
    elif action == "reject":
        out = dict(result="rejected", case_id=case_id, released=_release_all(owner, case_id, "担当者が見送りました"))
        _set_case(owner, case_id, "rejected")
    else:
        out = _approve(owner, case, opt, shown_fingerprint, actor, now, fail_at)
    _record(owner, case_id, option_id, action, actor, out, idempotency_key)
    return out


def _record(owner, case_id, option_id, action, actor, out, key) -> None:
    c = db.connect(owner)
    try:
        with c:
            c.execute("INSERT INTO case_decision (case_id, option_id, action, actor, at, result, detail, idempotency_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (case_id, option_id, action, actor, db.now_iso(), out["result"], json.dumps(out, ensure_ascii=False), key))
            c.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'owner', ?, ?)",
                      (db.now_iso(), f"case_{action}", json.dumps(dict(case_id=case_id, option_id=option_id, result=out["result"]), ensure_ascii=False)))
    finally:
        c.close()


def _set_case(owner: str, case_id: int, status: str) -> None:
    c = db.connect(owner)
    try:
        with c:
            c.execute("UPDATE deal_case SET status = ?, updated_at = ? WHERE id = ?", (status, db.now_iso(), case_id))
    finally:
        c.close()


def _case_terms(owner: str, case: dict) -> tuple[list[dict], list[dict]]:
    """この案件の品目で、owner が買い手の条件と、owner が売り手の合意。"""
    c = db.connect(owner)
    try:
        item = _q1(c, "SELECT code FROM item WHERE id = ?", (case["item_id"],))[0]
    finally:
        c.close()
    m = db.connect(db.MARKET)
    try:
        terms = [dict(r) for r in m.execute("SELECT t.* FROM offer_term t JOIN network_message n ON n.id = t.message_id "
                                            "WHERE t.buyer = ? AND t.item_code = ? AND n.trade_date = ?", (owner, item, case["trade_date"]))]
        ags = [dict(r) for r in m.execute("SELECT a.* FROM agreement a JOIN offer_term t ON t.id = a.term_id JOIN network_message n ON n.id = t.message_id "
                                          "WHERE a.seller = ? AND a.item_code = ? AND n.trade_date = ?", (owner, item, case["trade_date"]))]
    finally:
        m.close()
    return terms, ags


def _release_all(owner: str, case_id: int, reason: str, keep_term: int | None = None, keep_agreements=()) -> dict:
    """選ばれなかった条件と仮押さえ・合意を取り消し、合意の相手には取り消しを知らせる。"""
    c = db.connect(owner)
    try:
        case = dict(_q1(c, "SELECT * FROM deal_case WHERE id = ?", (case_id,)))
    finally:
        c.close()
    terms, ags = _case_terms(owner, case)
    released_terms, released_holds, cancelled = [], set(), []
    m = db.connect(db.MARKET)
    try:
        with m:
            for t in terms:
                if t["id"] != keep_term and t["status"] == "offered":
                    m.execute("UPDATE offer_term SET status = 'released', updated_at = ? WHERE id = ?", (db.now_iso(), t["id"]))
                    released_terms.append(t["id"])
            for a in ags:
                if a["id"] not in keep_agreements and a["status"] in ("conditional", "needs_staff"):
                    m.execute("UPDATE agreement SET status = 'cancelled', updated_at = ? WHERE id = ?", (db.now_iso(), a["id"]))
                    cancelled.append(a)
    finally:
        m.close()
    keep_hold = next((t["hold_ref"] for t in terms if t["id"] == keep_term), None)
    for t in terms:
        if t["hold_ref"] and t["hold_ref"] != keep_hold and t["hold_ref"] not in released_holds:
            sc_ = db.connect(t["seller"])
            try:
                with sc_:
                    sc_.execute("UPDATE stock_hold SET status = 'released', updated_at = ? WHERE id = ? AND status = 'held'", (db.now_iso(), t["hold_ref"]))
            finally:
                sc_.close()
            released_holds.add(t["hold_ref"])
    for a in cancelled:   # 合意の相手に、取り消しを文で知らせる（コードが書く定型の文）
        msg = _q1_market("SELECT message_id FROM offer_term WHERE id = ?", (a["term_id"],))[0]
        try:
            network.send(owner, a["buyer"], "decline", a["item_code"], f"今回はお取引を見送ります。条件付きの合意（{a['quantity']}）を取り消します。{reason}。",
                         case["trade_date"], in_reply_to=msg)
        except network.NetworkError:
            pass
    return dict(terms=released_terms, holds=sorted(released_holds), agreements=[a["id"] for a in cancelled])


def _q1_market(sql, args):
    m = db.connect(db.MARKET)
    try:
        return m.execute(sql, args).fetchone()
    finally:
        m.close()


def _approve(owner: str, case, opt, shown_fp: str, actor: str, now: str, fail_at: str | None) -> dict:
    case, opt = dict(case), dict(opt)
    if opt["status"] != "offered" or opt["version"] != case["version"]:
        return dict(result="stale", case_id=case["id"], reasons=["この案は最新の版ではありません"])
    if not opt["executable"]:   # 表示の時に実行できない案（決まりの上限を超える等）は、承認しても確定しない
        return dict(result="not_executable", case_id=case["id"], reasons=json.loads(opt["numbers_json"])["errors"])
    c = db.connect(owner)
    try:
        item_code = _q1(c, "SELECT code FROM item WHERE id = ?", (case["item_id"],))[0]
        plan = json.loads(opt["plan_json"])
        numbers = workspace.compute(c, owner, item_code, plan, now)
    finally:
        c.close()
    fp = workspace.fingerprint(plan, numbers)
    reasons = list(numbers["errors"])
    if shown_fp != opt["fingerprint"]:
        reasons.append("画面の案の照合番号が、表示した時のものと違う")
    if fp != opt["fingerprint"]:
        reasons.append("表示した時から条件が変わりました（値・数量・状態）")
    purchase = numbers["purchase"]
    term = network.term(purchase["term_id"]) if purchase else None
    ags = [network.agreement(l["ref_id"]) for l in numbers["lines"] if l["kind"] == "agreement"]
    if term:
        reasons += _verify_seller(term, now)
    for ag in ags:
        reasons += _verify_buyer(ag, now)
    if reasons:
        _set_case(owner, case["id"], "stale")
        _wake_for_new_board(owner, case["id"], reasons)
        return dict(result="stale", case_id=case["id"], reasons=reasons)
    if not purchase:   # 仕入れない案（見送る）は、拒否と同じに扱う
        rel = _release_all(owner, case["id"], "担当者が見送りました")
        _set_case(owner, case["id"], "rejected")
        return dict(result="rejected", case_id=case["id"], released=rel)
    c = db.connect(owner)
    try:
        auth = authority.check(c, actor, term["seller"], case["item_id"], case["trade_date"], purchase["amount_yen"])
        # 顧客の間の優先（どの売り先の注文を欠くか）を決める案は、表で許された役割だけが確定できる（相談の答えと同じ判定）
        allocs = [dict(order_id=l["ref_id"] if l["kind"] == "order" else None, quantity=l["quantity"]) for l in numbers["lines"]]
        priority = consult.changes_priority(c, case, dict(quantity=purchase["quantity"], allocations=allocs))
        may = authority.may_change_priority(c, actor)
    finally:
        c.close()
    if not auth["allowed"] or (priority and not may):
        _set_case(owner, case["id"], "needs_president")
        return dict(result="needs_president", case_id=case["id"], authority=auth, priority_change=priority)
    _book_proposals(owner, case, opt, numbers, actor)
    _set_case(owner, case["id"], "incomplete")
    return _run_steps(owner, case["id"], fail_at)


def _wake_for_new_board(owner: str, case_id: int, reasons: list[str]) -> None:
    """古い案では確定しない。仲間を起こして、新しい条件でボードを出し直してもらう。"""
    c = db.connect(owner)
    try:
        events.emit(c, "human_choice", dict(case_id=case_id, result="stale", reasons=reasons))
    finally:
        c.close()


def _book_proposals(owner: str, case, opt, numbers, actor) -> None:
    c = db.connect(owner)
    try:
        with c:
            if c.execute("SELECT 1 FROM proposal WHERE case_id = ?", (case["id"],)).fetchone():
                return
            p = numbers["purchase"]
            c.execute("INSERT INTO proposal (case_id, option_id, side, item_id, counterparty, trade_date, quantity, unit_price_yen, trade_id, status, decided_by, decided_at) "
                      "VALUES (?, ?, 'buy', ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
                      (case["id"], opt["id"], case["item_id"], p["seller"], case["trade_date"], p["quantity"], p["unit_price_yen"],
                       f"case{case['id']}-buy", actor, db.now_iso()))
            for l in numbers["lines"]:
                if l["kind"] == "agreement":
                    ag = network.agreement(l["ref_id"])
                    c.execute("INSERT INTO proposal (case_id, option_id, side, item_id, counterparty, trade_date, quantity, unit_price_yen, trade_id, status, decided_by, decided_at) "
                              "VALUES (?, ?, 'sell', ?, ?, ?, ?, ?, ?, 'approved', ?, ?)",
                              (case["id"], opt["id"], case["item_id"], ag["buyer"], case["trade_date"], l["quantity"], l["unit_price_yen"],
                               f"case{case['id']}-sell-{ag['id']}", actor, db.now_iso()))
    finally:
        c.close()


def _register_trade(trade_id: str, lot_no: str, item_code: str, seller: str, seller_kind: str, buyer: str, qty: int, price: int, date: str) -> list[str]:
    """市場に取引を登録する。何度呼んでも二重にしない（途中で止まった取引は残りの帳簿を書く）。呼んだ市場の API の名前を返す。"""
    calls = []
    try:
        t = market.trade(trade_id)
    except KeyError:
        try:
            market.open_lot(lot_no, item_code, qty, seller, seller_kind, "negotiation", date)
            calls.append("market.open_lot")
        except Exception:
            pass   # 前の試みで出品だけ作れていた
        market.propose(trade_id, lot_no, buyer, price, qty)
        calls.append("market.propose")
        t = market.trade(trade_id)
    if t["status"] == "incomplete":
        market.complete(trade_id)
        calls.append("market.complete")
        return calls
    if t["status"] == "awaiting_approval":
        for party in (seller, buyer):
            market.approve(trade_id, party, t["terms_fingerprint"])
            calls.append(f"market.approve({party})")
    return calls


def _api_audit(owner: str, case_id: int, trade_id: str, calls: list[str]) -> None:
    """市場の取引帳簿への登録を、呼んだ API と登録の後の状態（市場の帳簿を読み直した値）で記録する。"""
    t = market.trade(trade_id)
    _audit(owner, "system", "market_api", dict(case_id=case_id, trade_id=trade_id, calls=calls, status=t["status"],
                                                seller_booked=t["seller_booked"], buyer_booked=t["buyer_booked"]))


def _step_audit(owner: str, case_id: int, step: str) -> None:
    _audit(owner, "system", "case_step", dict(case_id=case_id, step=step))


def _run_steps(owner: str, case_id: int, fail_at: str | None = None) -> dict:
    """確定の手順を順に進める。各手順は何度呼んでも結果が同じ（途中で止まっても resume で続けられる）。"""
    c = db.connect(owner)
    try:
        case = dict(_q1(c, "SELECT * FROM deal_case WHERE id = ?", (case_id,)))
        props = [dict(r) for r in c.execute("SELECT * FROM proposal WHERE case_id = ? ORDER BY id", (case_id,))]
        item_code = _q1(c, "SELECT code FROM item WHERE id = ?", (case["item_id"],))[0]
        opt = dict(_q1(c, "SELECT * FROM board_option WHERE id = ?", (props[0]["option_id"],)))
    finally:
        c.close()
    plan = json.loads(opt["plan_json"])
    numbers = json.loads(opt["numbers_json"])
    buy = next(p for p in props if p["side"] == "buy")
    sells = [p for p in props if p["side"] == "sell"]
    term = network.term(plan["buy_term_id"])
    done = []
    try:
        # 1. 仕入れの取引（漁協 → 網谷水産）：両方の帳簿
        if fail_at == "buy_trade":
            raise DealError("test: stopped before the buy trade")
        calls = _register_trade(buy["trade_id"], f"T{term['id']}", item_code, term["seller"], "coop", owner, buy["quantity"], buy["unit_price_yen"], case["trade_date"])
        _api_audit(owner, case_id, buy["trade_id"], calls)
        done.append("buy_trade")
        _step_audit(owner, case_id, "buy_trade")
        # 2. 売り手の在庫：仮押さえを確定し、在庫を減らす
        _seller_stock(term, buy)
        done.append("seller_stock")
        _step_audit(owner, case_id, "seller_stock")
        # 3. 条件：選んだ条件を確定、他の条件と仮押さえは取り消す
        m = db.connect(db.MARKET)
        try:
            with m:
                m.execute("UPDATE offer_term SET status = 'confirmed', updated_at = ? WHERE id = ? AND status IN ('offered', 'confirmed')", (db.now_iso(), term["id"]))
        finally:
            m.close()
        _release_all(owner, case_id, "別の条件で確定しました", keep_term=term["id"], keep_agreements={int(p["trade_id"].rsplit("-", 1)[1]) for p in sells})
        done.append("terms")
        _step_audit(owner, case_id, "terms")
        # 4. 売りの取引（網谷水産 → B社）：両方の帳簿
        if fail_at == "sell_trades":
            raise DealError("test: stopped before the sell trades")
        for p in sells:
            aid = int(p["trade_id"].rsplit("-", 1)[1])
            calls = _register_trade(p["trade_id"], f"A{aid}", item_code, owner, "buyer", p["counterparty"], p["quantity"], p["unit_price_yen"], case["trade_date"])
            _api_audit(owner, case_id, p["trade_id"], calls)
            m = db.connect(db.MARKET)
            try:
                with m:
                    m.execute("UPDATE agreement SET status = 'confirmed', updated_at = ? WHERE id = ?", (db.now_iso(), aid))
            finally:
                m.close()
        done.append("sell_trades")
        _step_audit(owner, case_id, "sell_trades")
        # 5. 買い手（B社）の側：事前の承認を使い済みに、注文に引当、在庫
        for p in sells:
            _buyer_side(p, item_code, case["trade_date"])
        done.append("buyer_side")
        _step_audit(owner, case_id, "buyer_side")
        # 6. 自社の帳面：在庫の引当・注文の充足・提案の登録済み
        _own_books(owner, case, numbers, props)
        done.append("own_books")
        _step_audit(owner, case_id, "own_books")
    except Exception as e:
        _audit(owner, "system", "case_incomplete", dict(case_id=case_id, done=done, error=str(e)))
        return dict(result="incomplete", case_id=case_id, done=done, error=str(e))
    _set_case(owner, case_id, "registered")
    _audit(owner, "system", "case_registered", dict(case_id=case_id, trades=[p["trade_id"] for p in props]))
    return dict(result="registered", case_id=case_id, trades=[p["trade_id"] for p in props], done=done,
                gross_profit_expected_yen=numbers["gross_profit_yen"])


def _seller_stock(term: dict, buy: dict) -> None:
    c = db.connect(term["seller"])
    try:
        h = dict(_q1(c, "SELECT * FROM stock_hold WHERE id = ?", (term["hold_ref"],)))
        if h["status"] == "confirmed":
            return
        s = dict(_q1(c, "SELECT * FROM stock WHERE id = ?", (h["stock_id"],)))
        now = db.now_iso()
        with c:
            c.execute("UPDATE stock_hold SET status = 'confirmed', quantity = ?, unit_price_yen = ?, updated_at = ? WHERE id = ?",
                      (buy["quantity"], buy["unit_price_yen"], now, h["id"]))
            if s["quantity"] == buy["quantity"]:
                c.execute("UPDATE stock SET status = 'sold', trade_id = ?, updated_at = ? WHERE id = ?", (buy["trade_id"], now, s["id"]))
            else:
                c.execute("UPDATE stock SET quantity = quantity - ?, updated_at = ? WHERE id = ?", (buy["quantity"], now, s["id"]))
                c.execute("INSERT INTO stock (item_id, quantity, status, trade_id, lot_no, stock_date, updated_at) VALUES (?, ?, 'sold', ?, ?, ?, ?)",
                          (s["item_id"], buy["quantity"], buy["trade_id"], s["lot_no"], s["stock_date"], now))
            c.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'system', 'sold_by_preapproved_term', ?)",
                      (now, json.dumps(dict(trade_id=buy["trade_id"], term_id=term["id"], quantity=buy["quantity"]), ensure_ascii=False)))
    finally:
        c.close()


def _buyer_side(p: dict, item_code: str, date: str) -> None:
    c = db.connect(p["counterparty"])
    try:
        if _q1(c, "SELECT 1 FROM stock WHERE trade_id = ?", (p["trade_id"],)):
            return
        it = _q1(c, "SELECT id FROM item WHERE code = ?", (item_code,))[0]
        o = _q1(c, "SELECT id, quantity FROM order_book WHERE item_id = ? AND status IN ('open', 'partly_filled') ORDER BY id LIMIT 1", (it,))
        now = db.now_iso()
        with c:
            c.execute("INSERT INTO stock (item_id, quantity, status, order_id, trade_id, stock_date, updated_at) VALUES (?, ?, 'allocated', ?, ?, ?, ?)",
                      (it, p["quantity"], o["id"] if o else None, p["trade_id"], date, now))
            if o:
                c.execute("UPDATE order_book SET status = ? WHERE id = ?", ("filled" if p["quantity"] >= o["quantity"] else "partly_filled", o["id"]))
            c.execute("UPDATE preapproval SET status = 'used' WHERE item_id = ? AND side = 'buy' AND status = 'active'", (it,))
            c.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'system', 'bought_by_preapproval', ?)",
                      (now, json.dumps(dict(trade_id=p["trade_id"], quantity=p["quantity"], unit_price_yen=p["unit_price_yen"]), ensure_ascii=False)))
    finally:
        c.close()


def _own_books(owner: str, case: dict, numbers: dict, props: list[dict]) -> None:
    c = db.connect(owner)
    try:
        if _q1(c, "SELECT 1 FROM stock WHERE trade_id = ?", (props[0]["trade_id"],)):
            return
        now = db.now_iso()
        buy = next(p for p in props if p["side"] == "buy")
        with c:
            for l in numbers["lines"]:
                if l["kind"] == "order":
                    c.execute("INSERT INTO stock (item_id, quantity, status, order_id, trade_id, stock_date, updated_at) VALUES (?, ?, 'allocated', ?, ?, ?, ?)",
                              (case["item_id"], l["quantity"], l["ref_id"], buy["trade_id"], case["trade_date"], now))
                    oq = _q1(c, "SELECT quantity FROM order_book WHERE id = ?", (l["ref_id"],))[0]
                    c.execute("UPDATE order_book SET status = ? WHERE id = ?", ("filled" if l["quantity"] >= oq else "partly_filled", l["ref_id"]))
                elif l["kind"] == "agreement":
                    tid = f"case{case['id']}-sell-{l['ref_id']}"
                    c.execute("INSERT INTO stock (item_id, quantity, status, trade_id, stock_date, updated_at) VALUES (?, ?, 'sold', ?, ?, ?)",
                              (case["item_id"], l["quantity"], tid, case["trade_date"], now))
                else:   # 未打診の売り先・売り先なし：引当のない在庫として持つ（売れたことにしない）
                    c.execute("INSERT INTO stock (item_id, quantity, status, trade_id, stock_date, updated_at) VALUES (?, ?, 'unallocated', ?, ?, ?)",
                              (case["item_id"], l["quantity"], buy["trade_id"], case["trade_date"], now))
            c.execute("UPDATE proposal SET status = 'registered' WHERE case_id = ?", (case["id"],))
    finally:
        c.close()


def resume(owner: str, case_id: int) -> dict:
    """途中で止まった確定（'incomplete'）の残りを進める。済んだ手順は二重にしない。"""
    c = db.connect(owner)
    try:
        st = _q1(c, "SELECT status FROM deal_case WHERE id = ?", (case_id,))[0]
    finally:
        c.close()
    if st == "registered":
        return dict(result="registered", case_id=case_id, repeated=True)
    if st != "incomplete":
        raise DealError(f"case {case_id} is {st}")
    return _run_steps(owner, case_id)


def reconcile(case_trades: list[str]) -> list[dict]:
    """市場の取引と、両方の帳簿の照合（片側だけの帳簿を見つける）。"""
    out = []
    for tid in case_trades:
        try:
            t = market.trade(tid)
        except KeyError:
            out.append(dict(trade_id=tid, status="missing"))
            continue
        out.append(dict(trade_id=tid, status=t["status"], seller_booked=t["seller_booked"], buyer_booked=t["buyer_booked"]))
    return out
