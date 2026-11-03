"""本編の場面を動かす：漁協の資料・注文を入れ、出来事の起きた順に各組織の Claude AI パートナーを起こす。

  起きる順は出来事の時刻（マイクロ秒）で決まる。どの組織が何の出来事で起きるかは agents.WAKE。
  安全装置：起動の数の上限（max_runs）と、場面全体の呼び出しの上限（max_total_calls）。
"""
from agentdock import agents, broadcast, db, events, network
from agentdock.seed import scenario as sc

MAX_RUNS = 16
MAX_TOTAL_CALLS = 60


def start(on_date: str = sc.DEMO_DATE) -> dict:
    """順1：漁協の資料を3社に届ける。順2：網谷水産に注文が入る。B社にも料亭の注文が入る。"""
    iw = db.connect("iwase")
    try:
        bid = broadcast.publish(broadcast.compose(iw, on_date))
    finally:
        iw.close()
    network.announce_material(bid)
    out = dict(broadcast_id=bid)
    for key, o in (("amiya_order", sc.AJI_ORDER), ("b_order", sc.B_AJI_ORDER)):
        c = db.connect(o["owner"])
        try:
            out[key] = events.order_received(c, o["customer"], o["item"], o["quantity"], o["sell_price_yen"], on_date)
        finally:
            c.close()
    return out


def next_event():
    """起こすべき出来事のうち、最も早いもの（owner, event）。無ければ None。"""
    found = []
    for owner in db.OWNER_CODES:
        c = db.connect(owner)
        try:
            found += [(e["at"], e["id"], owner, e) for e in events.pending(c) if agents.wakes(owner, e)]
        finally:
            c.close()
    if not found:
        return None
    found.sort(key=lambda x: (x[0], x[1]))
    return found[0][2], found[0][3]


def run(clients: dict, on_date: str = sc.DEMO_DATE, run_kind: str = "mock",
        max_runs: int = MAX_RUNS, max_total_calls: int = MAX_TOTAL_CALLS) -> list[dict]:
    """出来事が尽きるか、上限に達するまで起こし続ける。clients は組織ごとの呼び出し先。"""
    log, total = [], 0
    for _ in range(max_runs):
        nxt = next_event()
        if nxt is None:
            break
        owner, ev = nxt
        left = max_total_calls - total
        if left <= 0:
            log.append(dict(owner=owner, event=ev["id"], outcome="budget_exhausted"))
            break
        r = agents.run_event(clients[owner], owner, ev, on_date, run_kind=run_kind, max_calls=min(agents.autonomy.MAX_CALLS, left))
        total += r["calls"]
        log.append(dict(owner=owner, event_kind=ev["kind"], **r))
    return log
