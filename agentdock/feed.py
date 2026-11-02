"""画面への読み出し。

決まり：
- 持ち主の画面には、その持ち主の DB の中身だけを返す（他社の名前・値・帳面は返さない）
- 市場の入札は、締めの前は見る人の自社の入札だけ（market.bids_visible_to と同じ判定）
- 数字は部品が出した値をそのまま返す（ここで計算し直さない）
- 出来事は監査の記録の id で順に返し、画面は続きの id から読む
"""
from agentdock import briefing, consult, db, evidence, market, runlog

BLOCK_EVENTS = runlog.BLOCK_EVENTS


def _owner(code: str) -> str:
    if code not in db.OWNER_CODES:
        raise ValueError(f"unknown owner: {code}")
    return code


def events(owner: str, since: int = 0, limit: int = 100) -> dict:
    c = db.connect(_owner(owner))
    try:
        rows = [dict(r) for r in c.execute(
            "SELECT id, at, actor, event, task_id, detail FROM audit_event WHERE id > ? AND event != 'seed' ORDER BY id LIMIT ?",
            (since, max(1, min(limit, 500))))]
    finally:
        c.close()
    return dict(owner=owner, events=rows, cursor=rows[-1]["id"] if rows else since)


def state(owner: str) -> dict:
    c = db.connect(_owner(owner))
    try:
        o = dict(c.execute("SELECT code, name, role, person_name, companion_name FROM owner").fetchone())
        tasks = [dict(r) for r in c.execute(
            "SELECT t.id, t.kind, i.code AS item, t.state, t.version, t.updated_at FROM task t JOIN item i ON i.id = t.item_id ORDER BY t.id")]
        cards = [consult.card(c, r[0]) for r in c.execute("SELECT id FROM consultation_group WHERE status != 'answered' ORDER BY id")]
        decisions = [evidence.sheet(c, r[0]) for r in c.execute("SELECT id FROM decision ORDER BY id DESC LIMIT 20")]
        messages = [dict(r) for r in c.execute(
            "SELECT task_id, seq, unit_price_yen, quantity, sent_at, body FROM message ORDER BY id DESC LIMIT 20")]
        ledger = [dict(r) for r in c.execute(
            "SELECT l.external_trade_id, i.code AS item, l.unit_price_yen, l.quantity, l.side, l.registered_at "
            "FROM ledger_entry l JOIN item i ON i.id = l.item_id ORDER BY l.id")]
        recon = [dict(r) for r in c.execute(
            "SELECT external_trade_id, result, checked_at FROM reconciliation r WHERE id = "
            "(SELECT max(id) FROM reconciliation WHERE external_trade_id = r.external_trade_id) ORDER BY id")]
    finally:
        c.close()
    run = runlog.summary((owner,))
    mine = run["owners"][owner]
    return dict(owner=o, tasks=tasks, open_cards=cards, decisions=decisions, messages=messages,
                ledger=ledger, reconciliation=recon,
                run=dict(calls=mine["calls"], cost_micro_usd=mine["cost_micro_usd"], last_event_at=mine["last_event_at"],
                         blocked={e: mine["events"].get(e, 0) for e in BLOCK_EVENTS}))


def bid_view(owner: str, item_code: str, kind: str, on_date: str) -> dict:
    """試作の画面（web/mock-bid.js の MOCK_BID）と同じ形で、DB の値を返す。"""
    c = db.connect(_owner(owner))
    try:
        m = briefing.build(c, item_code, kind, on_date)
        last = c.execute(
            "SELECT d.id FROM decision d JOIN task t ON t.id = d.task_id JOIN item i ON i.id = t.item_id "
            "WHERE i.code = ? AND t.kind = ? ORDER BY d.id DESC LIMIT 1", (item_code, kind)).fetchone()
        sheet = evidence.sheet(c, last[0]) if last else None
        cost = c.execute("SELECT process_cost_yen, target_profit_yen FROM cost_rule r JOIN item i ON i.id = r.item_id "
                         "WHERE i.code = ? ORDER BY valid_from DESC LIMIT 1", (item_code,)).fetchone()
    finally:
        c.close()
    unit = "1本" if m["item"]["unit"] == "fish" else f"1箱（{m['item']['unit_weight_g'] // 1000}kg）"
    r, ly, p = m["market_recent"], m["market_last_year"], m["approved_plan"]
    first = (m.get("orders") or [None])[0]
    values = [v for v in (r and r["low"], r and r["high"], ly and ly["low"], ly and ly["high"],
                          first and first["ceiling_yen"], p and p.get("max_unit_price_yen"), p and p.get("min_unit_price_yen")) if v]
    scale = dict(min=int(min(values) * 0.9) // 100 * 100, max=-(-int(max(values) * 1.1) // 100) * 100) if values else None
    u = "本" if m["item"]["unit"] == "fish" else "箱"
    yen = lambda v: f"{v:,}円"   # noqa: E731
    own = []
    if first and cost:
        own.append(dict(tag="決まり", text="上限は「売値 − 加工 − 利益」で決める"))
    if p and p.get("max_unit_price_yen"):
        own.append(dict(tag="決まり", text=f"計画の外は社長に聞く（上限 {yen(p['max_unit_price_yen'])}・{p['max_quantity']}{u}）"))
    own += [dict(tag="これまでの例", text=f"{v['date']} {v['situation']} → {v['choice']}" + (f"（{v['reason']}）" if v["reason"] else ""))
            for v in m["vault_cases"]]
    data = []
    if r:
        data.append(dict(tag="相場", to="recent", text=f"直近30日の中央値 {yen(r['median'])}（幅 {yen(r['low'])}〜{yen(r['high'])}）"))
    if ly:
        data.append(dict(tag="相場", to="lastYear", text=f"去年の同じ時期の中央値 {yen(ly['median'])}（幅 {yen(ly['low'])}〜{yen(ly['high'])}）"))
    for o in m.get("orders", []):
        data.append(dict(tag="注文", to="mark", text=f"{o['customer']}の注文 {o['quantity']}{u}"))
    res, coop = m.get("results"), m.get("coop_item")
    if res and "won" in res:
        data.append(dict(tag="状況", to="mark", text=f"今日の結果：落札 {res['won']}{u}・不足 {res['short']}{u}"))
    elif coop:
        size = f"（{coop['size']}）" if coop.get("size") else ""
        data.append(dict(tag="状況", to="mark", text=f"漁協の資料：今朝の水揚げ {coop['quantity']}{u}{size}"))
    return dict(
        item=m["item"]["name"], unit=unit, scale=scale,
        lastYear=ly and dict(low=ly["low"], high=ly["high"], median=ly["median"]),
        recent=r and dict(low=r["low"], high=r["high"], median=r["median"]),
        computedLimit=first and cost and dict(salePrice=first["sell_price_yen"], processing=cost[0], profit=cost[1], value=first["ceiling_yen"]),
        plan=p and dict(low=p.get("min_unit_price_yen"), limit=p.get("max_unit_price_yen"), boxes=p["max_quantity"],
                        approvedBy=m["owner"]["person_name"]),
        choice=sheet and dict(price=sheet["unit_price_yen"], boxes=sheet["quantity"], reason=sheet["rationale"],
                              action=sheet["action"], withinPlan=sheet["within_plan"], stale=sheet["stale"]),
        book=dict(own=own, data=data),
        evidence=sheet["evidence"] if sheet else [],
        briefingFingerprint=m["fingerprint"])


def tender_view(lot_no: str, viewer: str) -> dict:
    return dict(lot=lot_no, viewer=_owner(viewer), bids=market.bids_visible_to(lot_no, viewer))
