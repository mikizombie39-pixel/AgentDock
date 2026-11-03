"""Adaptive Workspace（磁石ボード）：Claude が選んだ情報と案を、決められたカードの種類で組み、金額はコードが計算する。

Claude が決めるもの：何が重要か・どのカードを出すか・どの案を出すか・なぜ勧めるか・経験をどう使うか
コードが保証するもの：金額と数量（業務 DB と条件の番号から計算）・参照してよい情報か・案が実行できるか・表示した時の状態（照合番号）

Claude の文の中の金額は使わない。文に書かれた金額が計算と合わなければ、ボードに出さずに書き直してもらう。
"""
import hashlib
import json

from agentdock import authority, consult, criteria, db, disclosure, market, network
from agentdock.seed import scenario as sc

CARD_TYPES = ("order", "quote", "agreement", "vault", "customer", "post", "market", "risk", "note")
ALLOC_KINDS = ("order", "agreement", "customer_lead", "unallocated")
# 画面の組み方：Claude が選べるのは、決められた焦点・操作の候補・カードの種類だけ（HTML は作らせない）
FOCUS = ("recommend", "compare", "partner", "vault", "risk")
ACTIONS = ("approve", "compare", "check_partner", "open_detail", "show_others", "ask", "hold", "reject")
DECIDING = ("presented", "held")          # 担当者が判断できる案件の状態
MAX_LAYOUT_CARDS = 4
MAX_SHORT_QUESTION = 40   # 現在地の画面の問い
MAX_SHORT_LABEL = 16      # 現在地の画面のボタン（案の名前）
CERTAINTY = {"order": "注文あり", "conditional": "条件付きの合意", "needs_staff": "相手の担当者の承認待ち", "lead": "未打診", "none": "売り先なし"}


class BoardError(Exception):
    def __init__(self, reasons):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _process_cost(con, item_id: int, destination: str) -> dict:
    """売り先の工程の費用（1単位）。工程が未確認・未登録なら確定値として扱わない。"""
    r = con.execute(
        "SELECT r.status, r.note, p.name, p.cost_yen_per_unit FROM route_process r LEFT JOIN process_step p ON p.id = r.process_step_id "
        "WHERE r.item_id = ? AND r.destination = ?", (item_id, destination)).fetchone()
    if r is None:
        return dict(status="unknown", per_unit=None, step=None, note="売り先の工程が登録されていない")
    return dict(status=r["status"], per_unit=r["cost_yen_per_unit"], step=r["name"], note=r["note"])


def _term_state(term: dict, owner: str, now: str) -> list[str]:
    errs = []
    if term["buyer"] != owner:
        errs.append(f"term {term['id']} is not offered to {owner}")
    if term["status"] != "offered":
        errs.append(f"条件 {term['id']} は {term['status']}")
    if term["valid_until"] <= now:
        errs.append(f"条件 {term['id']} は期限切れ（{term['valid_until']}）")
    return errs


def compute(con, owner: str, item_code: str, option: dict, now: str) -> dict:
    """1つの案の数字を、条件の番号と業務 DB から計算する。errors が空なら実行できる。"""
    item = con.execute("SELECT id, code, name, unit FROM item WHERE code = ?", (item_code,)).fetchone()
    errors, lines = [], []
    purchase = None
    tid = option.get("buy_term_id")
    if tid is not None:
        try:
            t = network.term(tid)
        except network.NetworkError:
            t = None
            errors.append(f"条件 {tid} がありません")
        if t:
            errors += _term_state(t, owner, now)
            purchase = dict(term_id=t["id"], seller=t["seller"], seller_name=sc.OWNERS[t["seller"]]["name"], quantity=t["quantity"],
                            unit_price_yen=t["unit_price_yen"], amount_yen=t["quantity"] * t["unit_price_yen"],
                            valid_until=t["valid_until"], hold_ref=t["hold_ref"])
            if t["hold_ref"] is None:
                errors.append(f"条件 {t['id']} に売り手の仮押さえがない")
    allocated = 0
    ceilings = []
    for a in option.get("allocations", []):
        kind, qty = a["kind"], a["quantity"]
        if kind not in ALLOC_KINDS or qty <= 0:
            errors.append(f"配分が正しくない：{a}")
            continue
        allocated += qty
        if kind == "order":
            o = con.execute("SELECT o.id, o.quantity, o.sell_price_yen, o.status, c.name FROM order_book o JOIN customer c ON c.id = o.customer_id "
                            "WHERE o.id = ? AND o.item_id = ?", (a.get("ref_id"), item["id"])).fetchone()
            if o is None:
                errors.append(f"注文 {a.get('ref_id')} がありません")
                continue
            if o["status"] not in ("open", "partly_filled"):
                errors.append(f"注文 {o['id']} は {o['status']}")
            if qty > o["quantity"]:
                errors.append(f"注文 {o['id']} の数量 {o['quantity']} を超えています")
            pc = _process_cost(con, item["id"], o["name"])
            lines.append(dict(kind="order", ref_id=o["id"], to=o["name"], quantity=qty, unit_price_yen=o["sell_price_yen"],
                              revenue_yen=qty * o["sell_price_yen"], certainty="order", process=pc))
            ceilings.append(_buy_ceiling(con, item, o["name"]))
        elif kind == "agreement":
            try:
                ag = network.agreement(a.get("ref_id"))
            except network.NetworkError:
                errors.append(f"合意 {a.get('ref_id')} がありません")
                continue
            if ag["seller"] != owner:
                errors.append(f"合意 {ag['id']} は {owner} が売る合意ではない")
            if ag["status"] not in ("conditional", "needs_staff"):
                errors.append(f"合意 {ag['id']} は {ag['status']}")
            if ag["valid_until"] <= now:
                errors.append(f"合意 {ag['id']} は期限切れ")
            if qty != ag["quantity"]:
                errors.append(f"合意 {ag['id']} の数量は {ag['quantity']}")
            pc = _process_cost(con, item["id"], ag["buyer"])
            lines.append(dict(kind="agreement", ref_id=ag["id"], to=sc.OWNERS[ag["buyer"]]["name"], quantity=qty,
                              unit_price_yen=ag["unit_price_yen"], revenue_yen=qty * ag["unit_price_yen"],
                              certainty=ag["status"], process=pc))
        elif kind == "customer_lead":
            c = con.execute("SELECT c.id, c.name, c.allow_contact, s.unit_price_yen FROM customer c JOIN sales_price s ON s.customer_id = c.id "
                            "AND s.item_id = ? WHERE c.name = ?", (item["id"], a.get("customer"))).fetchone()
            if c is None:
                errors.append("customer_lead には customer（売り先の名前）が要ります" if not a.get("customer")
                              else f"売り先 {a.get('customer')} の売値がありません")
                continue
            pc = _process_cost(con, item["id"], c["name"])
            lines.append(dict(kind="customer_lead", ref_id=c["id"], to=c["name"], quantity=qty, unit_price_yen=c["unit_price_yen"],
                              revenue_yen=qty * c["unit_price_yen"], certainty="lead", process=pc, may_contact=bool(c["allow_contact"])))
        else:
            lines.append(dict(kind="unallocated", ref_id=None, to="売り先なし", quantity=qty, unit_price_yen=0, revenue_yen=0,
                              certainty="none", process=dict(status="none", per_unit=0, step=None, note=None)))
    # 仕入れ値の上限は網谷水産の決まり（強制）：注文に充てる仕入れの値が上限を超える案は実行できない（2026-11-03 SAL・デイブ）
    if purchase:
        for lim in ceilings:
            if lim["ceiling_yen"] is not None and purchase["unit_price_yen"] > lim["ceiling_yen"]:
                errors.append(f"仕入れ値 {purchase['unit_price_yen']:,}円／{item['unit']} は、網谷水産の決まりの仕入れ値の上限 "
                              f"{lim['ceiling_yen']:,}円／{item['unit']}（{lim['customer']}の注文）を超えます。現在の条件では実行できません")
    if purchase and allocated != purchase["quantity"]:
        errors.append(f"配分の合計 {allocated} が仕入れの数量 {purchase['quantity']} と合わない")
    if not purchase and any(l["kind"] != "unallocated" for l in lines):
        errors.append("仕入れの条件が無いのに売りの配分がある")

    # 加工の費用：工程が確定している売り先だけを合計する。未確認があれば、粗利は確定値として出さない
    cost_confirmed = sum(l["quantity"] * (l["process"]["per_unit"] or 0) for l in lines if l["process"]["status"] == "confirmed")
    unconfirmed = [l for l in lines if l["process"]["status"] in ("unconfirmed", "unknown")]
    for l in lines:
        l["process_cost_yen"] = l["quantity"] * l["process"]["per_unit"] if l["process"]["per_unit"] is not None and l["process"]["status"] != "none" else (0 if l["process"]["status"] == "none" else None)
    revenue = sum(l["revenue_yen"] for l in lines)
    purchase_yen = purchase["amount_yen"] if purchase else 0
    profit = revenue - purchase_yen - cost_confirmed
    unsold = sum(l["quantity"] for l in lines if l["kind"] == "unallocated")
    uncertain = sum(l["quantity"] for l in lines if l["certainty"] in ("conditional", "needs_staff", "lead"))
    per_unit_buy = purchase["unit_price_yen"] if purchase else 0
    numbers = dict(
        purchase=purchase, lines=lines, revenue_yen=revenue, purchase_yen=purchase_yen,
        process_cost_yen=cost_confirmed, gross_profit_yen=profit if not unconfirmed else None,
        gross_profit_without_unconfirmed_yen=profit if unconfirmed else None,
        unconfirmed_process=[l["to"] for l in unconfirmed],
        uncounted_costs=list(sc.UNCOUNTED_COSTS),
        profit_kind="見込み",   # 正式な確定の前はすべて見込み
        uncertain_quantity=uncertain, unsold_quantity=unsold,
        unsold_loss_yen=unsold * (per_unit_buy + 0) if unsold else 0,
        limits=dict(buy_ceiling=ceilings, authority=_authority(con, purchase_yen)),
        errors=errors, executable=not errors)
    return numbers


def _buy_ceiling(con, item, customer: str) -> dict:
    """仕入れ値の上限（決まり）と、その内訳（売値 − 加工 − 確保する利益）。"""
    try:
        ceiling = criteria.ceiling_yen(con, item["code"], customer)
    except LookupError:
        return dict(customer=customer, ceiling_yen=None)
    sp = con.execute("SELECT s.unit_price_yen FROM sales_price s JOIN customer c ON c.id = s.customer_id WHERE s.item_id = ? AND c.name = ? "
                     "ORDER BY s.valid_from DESC LIMIT 1", (item["id"], customer)).fetchone()
    cr = con.execute("SELECT process_cost_yen, target_profit_yen FROM cost_rule WHERE item_id = ? ORDER BY valid_from DESC LIMIT 1",
                     (item["id"],)).fetchone()
    return dict(customer=customer, ceiling_yen=ceiling, sell_price_yen=sp[0], process_cost_yen=cr[0], target_profit_yen=cr[1], enforced=True)


def _authority(con, amount_yen: int) -> dict:
    """担当者の承認の権限（1件の上限）。仕入れ値の上限とは別の決まり。確定の時は authority.check で合計を確かめ直す。"""
    try:
        limit = authority.limit_for(con, "staff")
    except Exception:
        return dict(limit_yen=None, amount_yen=amount_yen, within=None)
    return dict(limit_yen=limit, amount_yen=amount_yen, within=limit is None or amount_yen <= limit)


def fingerprint(plan: dict, numbers: dict) -> str:
    # 決まり（仕入れ値の上限・権限）は照合番号に入れない：その違反は errors と承認の時の権限の検査で止める
    keep = {k: v for k, v in numbers.items() if k not in ("errors", "executable", "limits")}
    canon = json.dumps(dict(plan=plan, numbers=keep), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]


def _amounts_allowed(con, owner: str, item_code: str, computed: list[dict], on_date: str) -> set[int]:
    out = set()
    for n in computed:
        for k in ("revenue_yen", "purchase_yen", "process_cost_yen", "gross_profit_yen", "gross_profit_without_unconfirmed_yen", "unsold_loss_yen"):
            if n.get(k):
                out.add(abs(n[k]))
        if n["purchase"]:
            out |= {n["purchase"]["unit_price_yen"], n["purchase"]["amount_yen"]}
        for l in n["lines"]:
            out |= {l["unit_price_yen"], l["revenue_yen"]}
            if l["process"]["per_unit"]:
                out.add(l["process"]["per_unit"])
            if l.get("process_cost_yen"):
                out.add(l["process_cost_yen"])
    # 業務 DB の値（相場・売値・上限・届いた条件と合意の値）は文に書いてよい
    r = criteria.market_stats(con, item_code, on_date)
    if r:
        out |= {r["median"], r["low"], r["high"]}
    for row in con.execute("SELECT s.unit_price_yen, c.name FROM sales_price s JOIN customer c ON c.id = s.customer_id "
                           "JOIN item i ON i.id = s.item_id WHERE i.code = ?", (item_code,)):
        out.add(row[0])
        try:
            out.add(criteria.ceiling_yen(con, item_code, row[1]))
        except LookupError:
            pass
    m = market._con()
    try:
        for row in m.execute("SELECT unit_price_yen, quantity FROM offer_term WHERE (buyer = ? OR seller = ?) AND item_code = ?", (owner, owner, item_code)):
            out |= {row[0], row[0] * row[1]}
        for row in m.execute("SELECT unit_price_yen FROM deal_request WHERE item_code = ?", (item_code,)):
            out.add(row[0])
        for row in m.execute("SELECT unit_price_yen, quantity FROM network_message WHERE (from_owner = ? OR to_owner = ?) AND item_code = ? "
                             "AND unit_price_yen IS NOT NULL", (owner, owner, item_code)):
            out |= {row[0], row[0] * (row[1] or 1)}
    finally:
        m.close()
    unit = {v for v in out if 100 <= v < 10000}   # 1単位の値どうしの差（例：上限より 40円高い）
    out |= {abs(a - b) for a in unit for b in unit if a != b}
    inbox = con.execute("SELECT body_json FROM inbox_broadcast").fetchall()
    for (b,) in inbox:
        for i in json.loads(b)["items"]:
            if i["item"] == item_code:
                out |= {v for v in (i.get("today_expected_yen"), i.get("recent_median_yen"), i.get("last_year_median_yen")) if v}
    return out


def check_texts(texts: list[str], allowed: set[int]) -> list[str]:
    bad = []
    for t in texts:
        for v in sorted(disclosure.amounts(t or "") - allowed):
            bad.append(f"{v:,}円")
    return sorted(set(bad))


def _card_data(con, owner: str, card: dict) -> dict:
    t, rid = card["card_type"], card.get("ref_id")
    if t not in CARD_TYPES:
        raise BoardError([f"カードの種類 {t} は使えません"])
    if t == "order":
        r = con.execute("SELECT o.id, c.name AS customer, i.name AS item, i.unit, o.quantity, o.sell_price_yen FROM order_book o "
                        "JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id WHERE o.id = ?", (rid,)).fetchone()
        if r is None:
            raise BoardError([f"注文 {rid} がありません"])
        return dict(r)
    if t == "quote":
        tm = network.term(rid)
        if owner not in (tm["buyer"], tm["seller"]):
            raise BoardError([f"条件 {rid} は自社の条件ではない"])
        return dict(term_id=tm["id"], seller=sc.OWNERS[tm["seller"]]["name"], quantity=tm["quantity"], unit_price_yen=tm["unit_price_yen"],
                    valid_until=tm["valid_until"], status=tm["status"])
    if t == "agreement":
        ag = network.agreement(rid)
        if owner not in (ag["buyer"], ag["seller"]):
            raise BoardError([f"合意 {rid} は自社の合意ではない"])
        return dict(agreement_id=ag["id"], buyer=sc.OWNERS[ag["buyer"]]["name"], quantity=ag["quantity"], unit_price_yen=ag["unit_price_yen"],
                    condition=ag["condition"], status=ag["status"], status_label=CERTAINTY.get(ag["status"], ag["status"]), valid_until=ag["valid_until"])
    if t == "vault":
        r = con.execute("SELECT id, kind, title, body, author, source, written_on, applies_when FROM vault_note WHERE id = ?", (rid,)).fetchone()
        if r is None:
            raise BoardError([f"Vault {rid} がありません"])
        return dict(r)
    if t == "customer":
        r = con.execute("SELECT id, name, allow_contact FROM customer WHERE id = ?", (rid,)).fetchone()
        if r is None:
            raise BoardError(["売り先のカードの ref_id には、read_customers の customer_id を入れてください" if rid is None else f"売り先 {rid} がありません"])
        h = con.execute("SELECT count(*), max(sale_date), min(sold_qty), max(sold_qty) FROM sales_history WHERE customer_id = ?", (rid,)).fetchone()
        return dict(id=r[0], name=r[1], may_contact=bool(r[2]), times=h[0], last=h[1], min_sold=h[2], max_sold=h[3])
    if t == "post":
        p = network.request_for(owner, rid)
        return dict(request_id=p["id"], owner=sc.OWNERS[p["owner"]]["name"], side=p["side"], quantity=p["quantity"], min_qty=p["min_qty"],
                    unit_price_yen=p["unit_price_yen"], message=p["message"], valid_until=p["valid_until"])
    if t == "market":
        return {}
    return {}


def build(con, owner: str, item_code: str, on_date: str, payload: dict, run_id: int, now: str) -> dict:
    """Claude の present_options の入力から磁石ボードを作る。作れなければ BoardError（Claude に理由を返す）。"""
    orders = [a["ref_id"] for o in payload["options"] for a in o["allocations"] if a["kind"] == "order"]
    oid = orders[0] if orders else None
    if oid is None:
        r = con.execute("SELECT o.id FROM order_book o JOIN item i ON i.id = o.item_id WHERE i.code = ? AND o.order_date = ? "
                        "AND o.status IN ('open', 'partly_filled') ORDER BY o.id LIMIT 1", (item_code, on_date)).fetchone()
        if r is None:
            raise BoardError(["この品目の注文がありません"])
        oid = r[0]
    item_id = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()[0]
    case = _case_for(con, orders, oid, item_id, on_date)
    # 現在地の画面に出す短い問いと案の名前（長い文は詳しい内容として残す）
    sq = (payload.get("short_question") or "").strip() or None
    errs = [] if sq is None or len(sq) <= MAX_SHORT_QUESTION else [f"short_question は {MAX_SHORT_QUESTION}字まで"]
    errs += [f"案 {o['code']} の short_label は 1〜{MAX_SHORT_LABEL}字" for o in payload["options"]
             if o.get("short_label") is not None and not 0 < len(o["short_label"].strip()) <= MAX_SHORT_LABEL]
    # 根拠に引いた Vault の番号は、自社の Vault にある番号だけ
    known = {r[0] for r in con.execute("SELECT id FROM vault_note")}
    errs += [f"案 {o['code']} の vault_refs {v} は Vault にありません（read_vault の id を使ってください）"
             for o in payload["options"] for v in o.get("vault_refs") or [] if v not in known]
    # 人の決定と照合する：決定と違う案は、違うと明示した時だけ出せる（その案は決定した人か網谷さんだけが承認できる）
    decision = consult.latest_decision(con, case[0]) if case else None
    unit = sc.unit_label(con.execute("SELECT unit FROM item WHERE id = ?", (item_id,)).fetchone()[0])
    computed = []
    for o in payload["options"]:
        plan = dict(buy_term_id=o["buy_term_id"], allocations=o["allocations"])
        diff = consult.against_decision(con, decision, o["allocations"]) if o["buy_term_id"] is not None else []
        if diff and not o.get("differs_from_decision"):
            errs.append(f"案 {o['code']} は人の決定と違います（{'・'.join(diff)}）。{consult.decision_text(decision, unit)}。"
                        "決定に沿った案にするか、決定と違う案だと differs_from_decision: true で示してください"
                        "（その案は、決定した人か網谷さんだけが承認できます）")
        elif diff:
            plan["differs_from_decision"] = decision["id"]
        n = compute(con, owner, item_code, plan, now)
        computed.append((o, plan, n))
    if errs:
        raise BoardError(errs)
    allowed = _amounts_allowed(con, owner, item_code, [n for _, _, n in computed], on_date)
    bad = check_texts([payload["summary"]] + [x for o, _, _ in computed for x in (o["label"], o["reason"], o["risk"])] +
                      [c.get("note") or "" for c in payload["cards"]], allowed)
    if bad:
        raise BoardError([f"文の中の金額 {', '.join(bad)} は、業務 DB の値とも計算とも合いません。金額は書かないか、正しい値にしてください"
                          "（磁石ボードの金額はコードが計算して出します）"])
    cards = [(c, _card_data(con, owner, c)) for c in payload["cards"]]
    layout = check_layout(con, owner, dict(
        headline=payload.get("headline") or payload["summary"], focus="recommend", primary=payload["recommended"],
        alternative=payload.get("alternative"), cards=payload["cards"][:MAX_LAYOUT_CARDS],
        actions=payload.get("actions") or ["approve", "compare", "show_others", "ask"],
        suggested_questions=payload.get("suggested_questions") or [], ask_human=payload.get("ask_human")),
        {o["code"]: n["executable"] for o, _, n in computed}, allowed)
    layout["short_question"] = sq
    layout["short_labels"] = {o["code"]: o["short_label"].strip() for o in payload["options"] if o.get("short_label")}
    layout["vault_refs"] = {o["code"]: list(dict.fromkeys(o.get("vault_refs") or [])) for o in payload["options"] if o.get("vault_refs")}
    now_iso = db.now_iso()
    with con:
        if case is None:
            cid = con.execute("INSERT INTO deal_case (order_id, item_id, trade_date, status, version, created_at, updated_at) VALUES (?, ?, ?, 'open', 0, ?, ?)",
                              (oid, item_id, on_date, now_iso, now_iso)).lastrowid
            version = 1
        else:
            cid, version = case[0], case[1] + 1
        con.execute("UPDATE board_option SET status = 'withdrawn' WHERE case_id = ? AND status = 'offered'", (cid,))
        con.execute("UPDATE deal_case SET status = 'presented', version = ?, last_run_id = ?, updated_at = ? WHERE id = ?", (version, run_id, now_iso, cid))
        for pos, (c, data) in enumerate(cards):
            con.execute("INSERT INTO board_card (case_id, version, position, card_type, ref_id, note, data_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (cid, version, pos, c["card_type"], c.get("ref_id"), c.get("note"), json.dumps(data, ensure_ascii=False)))
        for o, plan, n in computed:
            con.execute("INSERT INTO board_option (case_id, version, code, label, reason, risk, plan_json, numbers_json, recommended, executable, fingerprint, status) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'offered')",
                        (cid, version, o["code"], o["label"], o["reason"], o["risk"], json.dumps(plan, ensure_ascii=False),
                         json.dumps(n, ensure_ascii=False), int(o["code"] == payload["recommended"]), int(n["executable"]), fingerprint(plan, n)))
        con.execute("INSERT INTO workspace_turn (case_id, version, source, layout_json, run_id, at) VALUES (?, ?, 'present', ?, ?, ?)",
                    (cid, version, json.dumps(layout, ensure_ascii=False), run_id, now_iso))
        # 版ごとの相談：担当者が数量・配分を変える入口（前の版の相談は stale になる）
        item_id = con.execute("SELECT item_id FROM deal_case WHERE id = ?", (cid,)).fetchone()[0]
        consult_id = consult.open_for_case(con, cid, item_id, version, layout["ask_human"] or layout["headline"])
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'companion', 'board_presented', ?)",
                    (now_iso, json.dumps(dict(case_id=cid, version=version, run_id=run_id, summary=payload["summary"]), ensure_ascii=False)))
    return dict(case_id=cid, version=version, consultation_id=consult_id,
                options=[(o["code"], n["executable"], n["errors"]) for o, _, n in computed])


def _case_for(con, orders: list, oid: int, item_id: int, on_date: str):
    """案件の行（id, version）。案の注文の並びが変わっても（例：料亭だけに回す案）、同じ品目の開いた案件を使う。無ければ None。"""
    case = None
    for o in orders or [oid]:   # 登録・見送りで閉じた案件は使わない（残った注文は新しい案件で続ける）
        case = case or con.execute("SELECT id, version FROM deal_case WHERE order_id = ? AND status NOT IN ('registered', 'rejected') "
                                   "ORDER BY id DESC LIMIT 1", (o,)).fetchone()
    if case is None:
        case = con.execute("SELECT id, version FROM deal_case WHERE item_id = ? AND trade_date = ? AND status IN (?, ?, ?, ?, ?) "
                           "ORDER BY id DESC LIMIT 1", (item_id, on_date, "open", *consult.CASE_OPEN)).fetchone()
    return case


def check_layout(con, owner: str, layout: dict, codes: dict, allowed: set) -> dict:
    """Claude が選んだ画面の組み方を確かめ、カードの中身を業務 DB から埋めて返す。合わなければ BoardError。

    codes は {案の記号: 実行できるか}。文の中の金額は、業務 DB の値か計算の結果だけを許す。
    """
    errs = []
    head = (layout.get("headline") or "").strip()
    if not head or len(head) > 120:
        errs.append("headline は 1〜120字で書いてください")
    if layout.get("focus") not in FOCUS:
        errs.append(f"focus は {', '.join(FOCUS)} のどれか")
    prim, alt = layout.get("primary"), layout.get("alternative")
    if prim is not None and prim not in codes:
        errs.append(f"primary の案 {prim} がボードにありません")
    if alt is not None and (alt not in codes or alt == prim):
        errs.append(f"alternative の案 {alt} がボードに無いか、primary と同じです")
    cards = layout.get("cards") or []
    if len(cards) > MAX_LAYOUT_CARDS:
        errs.append(f"cards は {MAX_LAYOUT_CARDS} 枚まで（担当者が判断に要るものだけ）")
    acts = []
    for a in layout.get("actions") or []:
        if a not in ACTIONS:
            errs.append(f"操作 {a} は使えません（使えるのは {', '.join(ACTIONS)}）")
        elif a not in acts:
            acts.append(a)
    sq = [q.strip() for q in (layout.get("suggested_questions") or []) if q and q.strip()]
    if len(sq) > 3 or any(len(q) > 40 for q in sq):
        errs.append("suggested_questions は 3件まで・各 40字まで")
    ask = (layout.get("ask_human") or "").strip() or None
    ans = (layout.get("answer") or "").strip() or None
    if ans and len(ans) > 500:
        errs.append("answer は 500字まで")
    bad = check_texts([head, ask or "", ans or "", *sq] + [c.get("note") or "" for c in cards], allowed)
    if bad:
        errs.append(f"文の中の金額 {', '.join(bad)} は、業務 DB の値とも計算とも合いません。金額は書かないか、正しい値にしてください")
    if errs:
        raise BoardError(errs)
    filled = [dict(card_type=c["card_type"], ref_id=c.get("ref_id"), note=c.get("note"), data=_card_data(con, owner, c)) for c in cards]
    return dict(headline=head, focus=layout["focus"], primary=prim, alternative=alt, cards=filled, actions=acts,
                suggested_questions=sq, ask_human=ask, answer=ans)


def answer(con, owner: str, item_code: str, on_date: str, case_id: int, layout: dict, question_event_id: int,
           question: str, run_id: int) -> dict:
    """担当者の質問への答えと、組み直した画面を記録する（最新の版の案を使う）。"""
    case = con.execute("SELECT * FROM deal_case WHERE id = ?", (case_id,)).fetchone()
    if case is None:
        raise BoardError([f"案件 {case_id} がありません"])
    if case["status"] not in DECIDING:
        raise BoardError([f"案件 {case_id} は判断の時ではありません（{case['status']}）"])
    rows = con.execute("SELECT code, executable, numbers_json FROM board_option WHERE case_id = ? AND version = ?",
                       (case_id, case["version"])).fetchall()
    allowed = _amounts_allowed(con, owner, item_code, [json.loads(r["numbers_json"]) for r in rows], on_date)
    lay = check_layout(con, owner, layout, {r["code"]: bool(r["executable"]) for r in rows}, allowed)
    with con:
        tid = con.execute("INSERT INTO workspace_turn (case_id, version, source, question_event_id, question, answer, layout_json, run_id, at) "
                          "VALUES (?, ?, 'answer', ?, ?, ?, ?, ?, ?)",
                          (case_id, case["version"], question_event_id, question, lay["answer"], json.dumps(lay, ensure_ascii=False),
                           run_id, db.now_iso())).lastrowid
    return dict(turn_id=tid, case_id=case_id, version=case["version"])


def _gate_actions(layout: dict, case_status: str, opts: list[dict]) -> dict:
    """操作の候補は、案件の状態と案の実行の可否でコードが絞る（Claude が選んでも、今できない操作は出さない）。"""
    by = {o["code"]: o for o in opts}
    acts = list(layout.get("actions") or [])
    if case_status not in DECIDING:
        acts = []
    prim = by.get(layout.get("primary"))
    if prim is None or not prim["executable"] or prim["status"] != "offered":
        acts = [a for a in acts if a != "approve"]
    if layout.get("alternative") not in by:
        acts = [a for a in acts if a != "compare"]
    out = dict(layout, actions=acts)
    if case_status not in DECIDING:
        out["suggested_questions"] = []
        out["ask_human"] = None
    return out


def records(con, case_id: int) -> list[dict]:
    """確定した案件の、相手ごとの正式な記録（市場の取引帳簿と自社の帳簿から読む）。売れていない数量は売れたことにしない。"""
    from agentdock import deal
    props = [dict(r) for r in con.execute("SELECT * FROM proposal WHERE case_id = ? ORDER BY id", (case_id,))]
    if not props:
        return []
    rec = {r["trade_id"]: r for r in deal.reconcile([p["trade_id"] for p in props])}
    out = []
    for p in props:
        r = rec.get(p["trade_id"], {})
        name = sc.OWNERS.get(p["counterparty"], {}).get("name", p["counterparty"])
        out.append(dict(kind="market_trade", side=p["side"], counterparty=name, quantity=p["quantity"], unit_price_yen=p["unit_price_yen"],
                        trade_id=p["trade_id"], market_status=r.get("status"), seller_booked=r.get("seller_booked"),
                        buyer_booked=r.get("buyer_booked"), proposal_status=p["status"]))
    buy_tid = next((p["trade_id"] for p in props if p["side"] == "buy"), None)
    for s in con.execute("SELECT s.quantity, s.status, s.order_id, o.status AS order_status, c.name FROM stock s "
                         "LEFT JOIN order_book o ON o.id = s.order_id LEFT JOIN customer c ON c.id = o.customer_id "
                         "WHERE s.trade_id = ? ORDER BY s.id", (buy_tid,)):
        if s["status"] == "allocated":
            out.append(dict(kind="order_fill", counterparty=s["name"], quantity=s["quantity"], order_id=s["order_id"],
                            order_status=s["order_status"]))
        elif s["status"] == "unallocated":
            out.append(dict(kind="unallocated", counterparty=None, quantity=s["quantity"]))
    return out


def view(con, case_id: int | None = None) -> dict:
    """画面に出すボード（最新の版）。"""
    case = (con.execute("SELECT * FROM deal_case WHERE id = ?", (case_id,)) if case_id else
            con.execute("SELECT * FROM deal_case ORDER BY updated_at DESC, id DESC LIMIT 1")).fetchone()
    if case is None:
        return dict(case=None)
    v = case["version"]
    cards = [dict(r, data=json.loads(r["data_json"])) for r in con.execute(
        "SELECT id, position, card_type, ref_id, note, data_json FROM board_card WHERE case_id = ? AND version = ? ORDER BY position", (case["id"], v))]
    opts = [dict(r, numbers=json.loads(r["numbers_json"]), plan=json.loads(r["plan_json"])) for r in con.execute(
        "SELECT id, code, label, reason, risk, plan_json, numbers_json, recommended, executable, fingerprint, status FROM board_option "
        "WHERE case_id = ? AND version = ? ORDER BY code", (case["id"], v))]
    for o in opts:
        o.pop("numbers_json")
        o.pop("plan_json")
    owner = con.execute("SELECT code FROM owner").fetchone()[0]
    if case["status"] in DECIDING:   # 表示の時に今の条件で計算し直す。変わった案は承認の操作を出さない（承認の時も確かめ直す）
        item_code = con.execute("SELECT code FROM item WHERE id = ?", (case["item_id"],)).fetchone()[0]
        now = f"{case['trade_date']}T06:30:00+09:00"
        for o in opts:
            if o["status"] != "offered":
                continue
            n = compute(con, owner, item_code, o["plan"], now)
            if fingerprint(o["plan"], n) != o["fingerprint"]:
                o.update(changed=True, numbers=n, executable=0)
    for c in cards:
        c.pop("data_json")
        if c["card_type"] in ("quote", "agreement"):   # 状態は変わるので、表示の時に読み直す
            try:
                c["data"] = _card_data(con, owner, c)
            except (BoardError, network.NetworkError):
                pass
    done = con.execute("SELECT option_id FROM case_decision WHERE case_id = ? AND result = 'registered' ORDER BY id DESC LIMIT 1", (case["id"],)).fetchone()
    if done is None and case["status"] == "registered":
        done = con.execute("SELECT option_id FROM proposal WHERE case_id = ? LIMIT 1", (case["id"],)).fetchone()
    for o in opts:
        if done is not None and o["id"] == done[0] and case["status"] == "registered":
            o["numbers"]["profit_kind"] = "確定"
            o["chosen"] = True
    summary = con.execute("SELECT detail FROM audit_event WHERE event = 'board_presented' ORDER BY id DESC LIMIT 1").fetchone()
    decisions = [dict(r) for r in con.execute("SELECT option_id, action, actor, at, result, detail FROM case_decision WHERE case_id = ? ORDER BY id", (case["id"],))]
    t = con.execute("SELECT * FROM workspace_turn WHERE case_id = ? AND version = ? ORDER BY id DESC LIMIT 1", (case["id"], v)).fetchone()
    layout = _gate_actions(json.loads(t["layout_json"]), case["status"], opts) if t else None
    if layout:
        for c in layout["cards"]:   # 条件と合意の状態は変わるので、表示の時に読み直す
            if c["card_type"] in ("quote", "agreement"):
                try:
                    c["data"] = _card_data(con, owner, c)
                except (BoardError, network.NetworkError):
                    pass
    turns = [dict(id=r["id"], version=r["version"], question=r["question"], answer=r["answer"], at=r["at"],
                  focus=json.loads(r["layout_json"])["focus"]) for r in con.execute(
        "SELECT * FROM workspace_turn WHERE case_id = ? AND source = 'answer' ORDER BY id", (case["id"],))]
    cons = con.execute("SELECT id, task_version, status, question, change_json, answered_by FROM consultation WHERE case_id = ? ORDER BY id DESC LIMIT 1",
                       (case["id"],)).fetchone()
    consultation = None
    if cons is not None:
        consultation = dict(consultation_id=cons["id"], version=cons["task_version"], status=cons["status"], question=cons["question"],
                            change=json.loads(cons["change_json"]) if cons["change_json"] else None, answered_by=cons["answered_by"],
                            options=[dict(code=o["code"], label=o["label"], valid=bool(o["valid"])) for o in con.execute(
                                "SELECT code, label, valid FROM consultation_option WHERE consultation_id = ? ORDER BY id", (cons["id"],))])
    it = con.execute("SELECT code, name, unit FROM item WHERE id = ?", (case["item_id"],)).fetchone()
    # 数量・配分を変える操作の行：この品目の今日の注文（開いているもの）と、注文の無い売り先（売値のある先）
    orders = [dict(order_id=r["id"], customer=r["name"], quantity=r["quantity"], status=r["status"]) for r in con.execute(
        "SELECT o.id, c.name, o.quantity, o.status FROM order_book o JOIN customer c ON c.id = o.customer_id "
        "WHERE o.item_id = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id", (case["item_id"], case["trade_date"]))]
    leads = [r[0] for r in con.execute("SELECT c.name FROM customer c JOIN sales_price s ON s.customer_id = c.id AND s.item_id = ? ORDER BY c.id",
                                       (case["item_id"],)) if r[0] not in {o["customer"] for o in orders}]
    return dict(case=dict(case), cards=cards, options=opts, summary=json.loads(summary[0])["summary"] if summary else None, decisions=decisions,
                layout=layout, layout_turn=t["id"] if t else None, turns=turns, consultation=consultation,
                item=dict(code=it["code"], name=it["name"], unit=sc.unit_label(it["unit"])), orders=orders, leads=leads,
                records=records(con, case["id"]) if case["status"] == "registered" else [])
