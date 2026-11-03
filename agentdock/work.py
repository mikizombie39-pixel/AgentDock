"""作業の画面（dock.html）に出す仕事の一覧：業務 DB の事実からコードが組み立てる（画面のための文は作らない）。

  GET /api/work?owner=amiya&item=buri_xl

一覧の1件（item）：
  key      画面の上の同じ物を指す名前（例 "msg:3"）
  section  read（読み取ったこと）・done（AIが済ませたこと）・waiting（あなたを待つこと）・network（他の組織のAIとのやり取り）・left（残った仕事）
  kind     order・material・vault・stock・message・inquiry・reply・hold・trade・consultation・approval・order_left
  at       現れた時刻（記録の時刻）。AI が読んだ物は、読んだ時刻
  history  状態の移り変わり [{at, state}]（再生はこれを時刻の順にたどる）
  state    今の状態（history の最後）
  source   根拠の行 [{db, table, id}]（画面から記録を開く）
  data     画面に出す値（記録の値だけ）

決まり：
- 読むのは、その組織の DB と、市場の記録のうちその組織が送り手か受け手の行だけ（他社の DB は読まない）
- 品目で絞る（ほかの品目の文を混ぜない）
- 権限の線（案ア）：問い合わせ・返事・期限つきの仮押さえは AI が済ませたこと。登録・優先の変更・権限を超える承認は人を待つこと
"""
import datetime
import json

from agentdock import agents, db
from agentdock.seed import scenario as sc

SECTIONS = ("waiting", "read", "done", "network", "left")
CLOSED_CASE = ("registered", "rejected")


def _t(s) -> int:
    """並べる時刻（秒）。記録の時刻は秒までの物とマイクロ秒までの物が混ざるので、秒で比べ、同じ秒は記録の順を保つ。"""
    try:
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except (TypeError, ValueError):
        return 0


def _item(key, section, kind, at, data, source, history=None):
    history = sorted([h for h in (history or [dict(at=at, state="shown")]) if h["at"]], key=lambda h: _t(h["at"]))
    return dict(key=key, section=section, kind=kind, at=at, history=history, state=history[-1]["state"] if history else None,
                source=source, data=data)


def _steps(con, item: str) -> list[dict]:
    """この品目について、この組織の AI が呼んだ道具（成功したものだけ）。"""
    out = []
    for s in con.execute("SELECT id, run_id, seq, tool, input_json, at FROM agent_step WHERE is_error = 0 ORDER BY id"):
        try:
            inp = json.loads(s["input_json"] or "{}")
        except ValueError:
            continue
        if inp.get("item_code") == item:
            out.append(dict(s, input=inp))
    return out


def _first(steps, tool, after=None):
    return next((s for s in steps if s["tool"] == tool and (after is None or _t(s["at"]) >= _t(after))), None)


def view(owner: str, item: str, on_date: str = sc.DEMO_DATE) -> dict:
    if owner not in db.OWNER_CODES:
        raise ValueError(owner)
    con = db.connect(owner)
    try:
        it = con.execute("SELECT id, code, name, unit FROM item WHERE code = ?", (item,)).fetchone()
        if it is None:
            raise ValueError(item)
        steps = _steps(con, item)
        items = (_orders(con, it, on_date, steps) + _material(con, it, steps) + _vault(con, it, steps) + _stock(con, it, steps)
                 + _messages(owner, item, on_date) + _holds(con, owner, it, on_date) + _trades(con, it, on_date)
                 + _consultations(con, it, on_date) + _approvals(con, it, on_date) + _left(con, it, on_date))
        activity = _activity(con, owner, item)
    finally:
        con.close()
    rank = {s: i for i, s in enumerate(SECTIONS)}
    items.sort(key=lambda x: (_t(x["at"]), rank[x["section"]], x["key"]))
    return dict(owner=owner, owner_name=sc.OWNERS[owner]["name"], date=on_date,
                item=dict(code=it["code"], name=it["name"], unit=sc.unit_label(it["unit"])), items=items, activity=activity)


# ── 読み取ったこと（AI が道具で読んだ時刻に現れる）──────────────────────

def _orders(con, it, on_date, steps):
    out = []
    for o in con.execute("SELECT o.id, c.name, o.quantity, o.sell_price_yen, o.received_at FROM order_book o "
                         "JOIN customer c ON c.id = o.customer_id WHERE o.item_id = ? AND o.order_date = ? ORDER BY o.id", (it["id"], on_date)):
        filled, filled_at = con.execute("SELECT coalesce(sum(quantity), 0), max(updated_at) FROM stock WHERE order_id = ? AND status = 'allocated'",
                                        (o["id"],)).fetchone()
        r = _first(steps, "read_orders", after=o["received_at"])   # 注文が入った後に AI が注文を読んだ最初の時刻
        if r is None:   # AI がまだ読んでいない注文は出さない（読み取ったことだけを出す。満たしていない注文は残った仕事に出る）
            continue
        at = r["at"]
        hist = [dict(at=at, state="open")]
        if filled:
            hist.append(dict(at=filled_at, state="filled" if filled >= o["quantity"] else "partly_filled"))
        src = [dict(db="owner", table="order_book", id=o["id"]), dict(db="owner", table="agent_step", id=r["id"])]
        out.append(_item(f"order:{o['id']}", "read", "order", at,
                         dict(order_id=o["id"], customer=o["name"], quantity=o["quantity"], sell_price_yen=o["sell_price_yen"],
                              filled=filled, received_at=o["received_at"]), src, hist))
    return out


def _material(con, it, steps):
    s = _first(steps, "read_material") or _first(steps, "read_my_material")
    if s is None:
        return []
    table = "inbox_broadcast" if s["tool"] == "read_material" else "broadcast"
    r = con.execute(f"SELECT id, body_json FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
    e = next((x for x in json.loads(r["body_json"])["items"] if x["item"] == it["code"]), None) if r else None
    if e is None:
        return []
    keep = {k: e.get(k) for k in ("quantity", "size", "caught_at", "area", "tomorrow_forecast", "recent_median_yen", "last_year_median_yen", "note")}
    return [_item(f"material:{r['id']}", "read", "material", s["at"], dict(keep, sender=sc.OWNERS["iwase"]["name"]),
                  [dict(db="owner", table=table, id=r["id"]), dict(db="owner", table="agent_step", id=s["id"])])]


def _vault(con, it, steps):
    s = _first(steps, "read_vault")
    if s is None:
        return []
    notes = [dict(r) for r in con.execute("SELECT id, title, author, written_on, applies_when FROM vault_note WHERE item_id = ? OR item_id IS NULL ORDER BY id",
                                          (it["id"],))]
    if not notes:
        return []
    return [_item(f"vault:{s['id']}", "read", "vault", s["at"], dict(notes=notes),
                  [dict(db="owner", table="vault_note", id=n["id"]) for n in notes] + [dict(db="owner", table="agent_step", id=s["id"])])]


def _stock(con, it, steps):
    """自社の在庫（売り手が売りに出している数）。AI が read_stock で読んだ時刻に現れる。"""
    s = _first(steps, "read_stock")
    if s is None:
        return []
    out = []
    for r in con.execute("SELECT id, quantity, status, lot_no, updated_at FROM stock WHERE item_id = ? ORDER BY id", (it["id"],)):
        hist = [dict(at=s["at"], state="for_sale")] + ([dict(at=r["updated_at"], state=r["status"])] if r["status"] != "for_sale" else [])
        out.append(_item(f"stock:{r['id']}", "read", "stock", s["at"], dict(quantity=r["quantity"], status=r["status"], lot_no=r["lot_no"]),
                         [dict(db="owner", table="stock", id=r["id"]), dict(db="owner", table="agent_step", id=s["id"])], hist))
    return out


# ── やり取り（市場の記録のうち、自社が送り手か受け手の行）────────────────

def _messages(owner, item, on_date):
    m = db.connect(db.MARKET)
    try:
        rows = [dict(r) for r in m.execute("SELECT * FROM network_message WHERE (from_owner = ? OR to_owner = ?) AND item_code = ? AND trade_date = ? ORDER BY id",
                                           (owner, owner, item, on_date))]
        terms = {r["id"]: [dict(t) for t in m.execute("SELECT id, quantity, unit_price_yen, valid_until FROM offer_term WHERE message_id = ? ORDER BY id",
                                                        (r["id"],))] for r in rows}
    finally:
        m.close()
    out = []
    for r in rows:
        ts = [dict(term_id=t["id"], quantity=t["quantity"], unit_price_yen=t["unit_price_yen"], valid_until=t["valid_until"]) for t in terms[r["id"]]] or (
            [dict(term_id=None, quantity=r["quantity"], unit_price_yen=r["unit_price_yen"], valid_until=None)] if r["quantity"] or r["unit_price_yen"] else [])
        data = dict(message_id=r["id"], from_owner=r["from_owner"], frm=sc.OWNERS[r["from_owner"]]["name"], to_owner=r["to_owner"],
                    to=sc.OWNERS[r["to_owner"]]["name"], msg_kind=r["kind"], terms=ts, reason=r["reason"], body=r["body"],
                    in_reply_to=r["in_reply_to"], outgoing=r["from_owner"] == owner)
        src = [dict(db="market", table="network_message", id=r["id"])]
        out.append(_item(f"msg:{r['id']}", "network", "message", r["created_at"], data, src))
        if r["from_owner"] == owner:   # 自社の AI が送った問い合わせ・返事 ＝ AI が済ませたこと
            out.append(_item(f"sent:{r['id']}", "done", "inquiry" if r["kind"] == "inquiry" else "reply", r["created_at"], data, src))
    return out


def _holds(con, owner, it, on_date):
    """仮押さえ。買い手は市場の条件（売り手の押さえの番号つき）から、売り手は自社の stock_hold から。"""
    out = []
    m = db.connect(db.MARKET)
    try:
        terms = [dict(r) for r in m.execute(
            "SELECT t.* FROM offer_term t JOIN network_message n ON n.id = t.message_id "
            "WHERE t.buyer = ? AND t.item_code = ? AND n.trade_date = ? AND t.hold_ref IS NOT NULL ORDER BY t.id", (owner, it["code"], on_date))]
    finally:
        m.close()
    for t in terms:
        hist = [dict(at=t["created_at"], state="held")]
        if t["status"] != "offered":
            hist.append(dict(at=t["updated_at"], state={"released": "released", "expired": "expired"}.get(t["status"], "confirmed")))
        out.append(_item(f"hold:t{t['id']}", "done", "hold", t["created_at"],
                         dict(counterparty=sc.OWNERS[t["seller"]]["name"], counterparty_code=t["seller"], side="buy", quantity=t["quantity"],
                              unit_price_yen=t["unit_price_yen"], valid_until=t["valid_until"], message_id=t["message_id"]),
                         [dict(db="market", table="offer_term", id=t["id"])], hist))
    m = db.connect(db.MARKET)
    try:   # 売り手の押さえの作った時刻は、デモの朝の時刻で書かれている。押さえを結びつけた返事の文の時刻を使う
        sent = {r["id"]: r["created_at"] for r in m.execute("SELECT id, created_at FROM network_message WHERE from_owner = ?", (owner,))}
    finally:
        m.close()
    for h in con.execute("SELECT h.* FROM stock_hold h JOIN stock s ON s.id = h.stock_id WHERE s.item_id = ? ORDER BY h.id", (it["id"],)):
        at = sent.get(h["negotiation_id"], h["created_at"])
        hist = [dict(at=at, state="held")] + ([dict(at=h["updated_at"], state=h["status"])] if h["status"] != "held" else [])
        out.append(_item(f"hold:h{h['id']}", "done", "hold", at,
                         dict(counterparty=sc.OWNERS.get(h["buyer"], {}).get("name", h["buyer"]), counterparty_code=h["buyer"], side="sell",
                              quantity=h["quantity"], unit_price_yen=h["unit_price_yen"], valid_until=h["expires_at"], message_id=h["negotiation_id"]),
                         [dict(db="owner", table="stock_hold", id=h["id"])], hist))
    return out


def _trades(con, it, on_date):
    return [_item(f"trade:{p['id']}", "done", "trade", p["decided_at"],
                  dict(side=p["side"], counterparty=sc.OWNERS.get(p["counterparty"], {}).get("name", p["counterparty"]), quantity=p["quantity"],
                       unit_price_yen=p["unit_price_yen"], trade_id=p["trade_id"], decided_by=p["decided_by"]),
                  [dict(db="owner", table="proposal", id=p["id"])], [dict(at=p["decided_at"], state=p["status"])])
            for p in con.execute("SELECT * FROM proposal WHERE item_id = ? AND trade_date = ? ORDER BY id", (it["id"], on_date))]


# ── あなたを待つこと ─────────────────────────────────────────────

def _audit_at(con, event: str, needle: str) -> list[str]:
    return [r["at"] for r in con.execute("SELECT at, detail FROM audit_event WHERE event = ? ORDER BY id", (event,)) if needle in (r["detail"] or "") + " "]


def _options(con, case_id: int, version: int) -> list[dict]:
    out = []
    for o in con.execute("SELECT * FROM board_option WHERE case_id = ? AND version = ? ORDER BY code", (case_id, version)):
        n, p = json.loads(o["numbers_json"]), json.loads(o["plan_json"])
        pu = n["purchase"]
        out.append(dict(option_id=o["id"], code=o["code"], label=o["label"], recommended=bool(o["recommended"]), executable=bool(o["executable"]),
                        status=o["status"], fingerprint=o["fingerprint"],
                        purchase=dict(seller=pu["seller_name"], quantity=pu["quantity"], unit_price_yen=pu["unit_price_yen"], amount_yen=pu["amount_yen"]) if pu else None,
                        lines=[dict(to=l["to"], quantity=l["quantity"], kind=l["kind"]) for l in n["lines"]],
                        gross_profit_yen=n["gross_profit_yen"] if n["gross_profit_yen"] is not None else n["gross_profit_without_unconfirmed_yen"],
                        profit_complete=n["gross_profit_yen"] is not None, errors=n["errors"],
                        allocations=[dict(kind=a["kind"], ref_id=a.get("ref_id"), quantity=a["quantity"]) for a in p["allocations"]]))
    return out


def _consultations(con, it, on_date):
    out = []
    for c in con.execute("SELECT c.*, d.status AS case_status, d.updated_at AS case_at FROM consultation c JOIN deal_case d ON d.id = c.case_id "
                         "WHERE d.item_id = ? AND d.trade_date = ? ORDER BY c.id", (it["id"], on_date)):
        t = con.execute("SELECT at FROM workspace_turn WHERE case_id = ? AND version = ? AND source = 'present' ORDER BY id LIMIT 1",
                        (c["case_id"], c["task_version"])).fetchone()
        opened = t["at"] if t else c["answered_at"]
        needle = f"consultation {c['id']} "
        hist = [dict(at=opened, state="open")]
        hist += [dict(at=a, state="needs_president") for a in _audit_at(con, "consultation_needs_president", needle)]
        hist += [dict(at=a, state="answered") for a in _audit_at(con, "consultation_answered", needle)]
        if c["status"] == "stale":   # 版が上がって古くなった（答えても通らない）
            nxt = con.execute("SELECT at FROM workspace_turn WHERE case_id = ? AND version > ? AND source = 'present' ORDER BY id LIMIT 1",
                              (c["case_id"], c["task_version"])).fetchone()
            hist.append(dict(at=nxt["at"] if nxt else c["case_at"], state="stale"))
        if c["status"] in ("open", "needs_president") and c["case_status"] in CLOSED_CASE:   # 案件が登録・見送りで閉じた
            hist.append(dict(at=c["case_at"], state="closed"))
        out.append(_item(f"cons:{c['id']}", "waiting", "consultation", opened,
                         dict(consultation_id=c["id"], case_id=c["case_id"], version=c["task_version"], question=c["question"],
                              change=json.loads(c["change_json"]) if c["change_json"] else None, answered_by=c["answered_by"],
                              options=_options(con, c["case_id"], c["task_version"])),
                         [dict(db="owner", table="consultation", id=c["id"]), dict(db="owner", table="deal_case", id=c["case_id"])], hist))
    return out


def _approvals(con, it, on_date):
    """承認が担当者の権限を超えた（金額・顧客の間の優先）：網谷さんを待つ。後の承認で登録されたら閉じる。"""
    rows = [dict(r) for r in con.execute("SELECT d.* FROM case_decision d JOIN deal_case c ON c.id = d.case_id WHERE c.item_id = ? AND c.trade_date = ? ORDER BY d.id",
                                         (it["id"], on_date))]
    out = []
    for d in rows:
        if d["result"] != "needs_president":
            continue
        detail = json.loads(d["detail"] or "{}")
        auth = detail.get("authority") or {}
        o = con.execute("SELECT code, label, fingerprint FROM board_option WHERE id = ?", (d["option_id"],)).fetchone()
        later = next((x for x in rows if x["id"] > d["id"] and x["case_id"] == d["case_id"] and x["result"] != "needs_president"), None)
        hist = [dict(at=d["at"], state="needs_president")] + ([dict(at=later["at"], state=later["result"])] if later else [])
        out.append(_item(f"appr:{d['id']}", "waiting", "approval", d["at"],
                         dict(case_id=d["case_id"], option_id=d["option_id"], option_code=o["code"] if o else None, option_label=o["label"] if o else None,
                              fingerprint=o["fingerprint"] if o else None, asked_by=d["actor"], priority_change=detail.get("priority_change"),
                              amount_yen=auth.get("amount_yen"), limit_yen=auth.get("limit_yen"), over_limit=auth.get("allowed") is False),
                         [dict(db="owner", table="case_decision", id=d["id"])] + ([dict(db="owner", table="case_decision", id=later["id"])] if later else []), hist))
    return out


# ── 残った仕事 ────────────────────────────────────────────────

def _left(con, it, on_date):
    """案件が閉じた（登録・見送り）後に、まだ満たしていない注文。"""
    closed = con.execute("SELECT max(updated_at) FROM deal_case WHERE item_id = ? AND trade_date = ? AND status IN ('registered', 'rejected')",
                         (it["id"], on_date)).fetchone()[0]
    if closed is None:
        return []
    out = []
    for o in con.execute("SELECT o.id, c.name, o.quantity FROM order_book o JOIN customer c ON c.id = o.customer_id "
                         "WHERE o.item_id = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id", (it["id"], on_date)):
        filled = con.execute("SELECT coalesce(sum(quantity), 0) FROM stock WHERE order_id = ? AND status = 'allocated'", (o["id"],)).fetchone()[0]
        out.append(_item(f"left:{o['id']}", "left", "order_left", closed,
                         dict(order_id=o["id"], customer=o["name"], quantity=o["quantity"], remaining=o["quantity"] - filled),
                         [dict(db="owner", table="order_book", id=o["id"])], [dict(at=closed, state="open")]))
    return out


# ── AI の起動（画面の「AI が動いている」に使う）───────────────────────

def _activity(con, owner, item):
    out = []
    for r in con.execute("SELECT r.id, r.started_at, r.ended_at, r.outcome, e.kind, e.payload_json FROM agent_run r JOIN biz_event e ON e.id = r.event_id ORDER BY r.id"):
        if agents.event_item(owner, dict(kind=r["kind"], payload=json.loads(r["payload_json"]))) != item:
            continue
        out.append(dict(run_id=r["id"], event_kind=r["kind"], started_at=r["started_at"], ended_at=r["ended_at"], outcome=r["outcome"],
                        source=dict(db="owner", table="agent_run", id=r["id"])))
    return out


# ── 根拠の行（画面の「記録」から開く）──────────────────────────────

OWNER_TABLES = ("order_book", "agent_step", "agent_run", "inbox_broadcast", "broadcast", "vault_note", "stock", "stock_hold", "proposal",
                "consultation", "deal_case", "case_decision")
MARKET_TABLES = ("network_message", "offer_term")


def source(owner: str, dbname: str, table: str, rid: int) -> dict:
    """根拠の行を読む。自社の DB の行と、市場の行のうち自社が当事者の行だけ。"""
    if owner not in db.OWNER_CODES:
        raise ValueError(owner)
    if dbname == "owner" and table in OWNER_TABLES:
        c = db.connect(owner)
        try:
            r = c.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
        finally:
            c.close()
        if r is None:
            raise KeyError(rid)
        return dict(db="owner", table=table, row=dict(r))
    if dbname == "market" and table in MARKET_TABLES:
        m = db.connect(db.MARKET)
        try:
            r = m.execute(f"SELECT * FROM {table} WHERE id = ?", (rid,)).fetchone()
        finally:
            m.close()
        r = dict(r) if r else None
        if r is None or owner not in ((r["from_owner"], r["to_owner"]) if table == "network_message" else (r["seller"], r["buyer"])):
            raise KeyError(rid)
        out = dict(db="market", table=table, row=r)
        if table == "network_message" and r["from_owner"] == owner:   # 自社の AI がこの文を送った道具の呼び出し
            c = db.connect(owner)
            try:
                s = c.execute("SELECT id, run_id, seq, tool, input_json, result_text, at FROM agent_step WHERE tool IN ('send_inquiry', 'reply') AND is_error = 0 "
                              "AND (result_text LIKE ? OR result_text LIKE ?) ORDER BY id LIMIT 1", (f"%番号 {rid}）%", f"%番号 {rid}・%")).fetchone()
            finally:
                c.close()
            if s:
                out["step"] = dict(s)
        return out
    raise ValueError(f"{dbname}.{table}")
