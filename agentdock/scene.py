"""本編の場面を動かす：漁協の資料・注文を入れ、出来事の起きた順に各組織の Claude AI パートナーを起こす。

  起きる順は出来事の時刻（マイクロ秒）で決まる。どの組織が何の出来事で起きるかは agents.WAKE。
  安全装置：起動の数の上限（max_runs）と、場面全体の呼び出しの上限（max_total_calls）。
"""
from agentdock import agents, broadcast, db, events, network
from agentdock.seed import scenario as sc

MAX_RUNS = 16
MAX_TOTAL_CALLS = 60


VARIANTS = {
    "normal": "通常",
    "price_up": "漁協の数量ごとの最低の値が上がった（15kg 以上 1,640円・10kg 1,700円）",
    "b_declines": "B社の料亭の注文が取り消された（B社の掲示は朝から出ているが、注文と事前の承認は無い）",
    "vault_changed": "料亭は改装で休業中（売り先の特徴の書き付けを今日の日付で更新）",
}


def apply_variant(name: str) -> None:
    """種の DB を、条件を変えた通しの形にする。Claude には何も教えない（業務 DB の事実だけを変える）。"""
    def ex(owner, sql, args=()):
        c = db.connect(owner)
        try:
            with c:
                c.execute(sql, args)
        finally:
            c.close()
    if name == "price_up":
        ex("iwase", "UPDATE quote_rule SET floor_yen = 1640 WHERE min_qty = 15")
    elif name == "vault_changed":
        ex("amiya", "UPDATE vault_note SET title = ?, body = ?, written_on = ?, applies_when = ? WHERE kind = 'customer'",
           ("料亭は改装のため休業中", "料亭は 2026-10-30 から 11 月中旬まで改装で休業している。この間は仕入れの案内をしない。",
            "2026-11-01", "料亭にアジを案内する時"))
    elif name == "b_declines":
        ex("buyer_b", "UPDATE preapproval SET status = 'cancelled'")
    elif name != "normal":
        raise ValueError(name)


def start(on_date: str = sc.DEMO_DATE, variant: str = "normal") -> dict:
    """順1：漁協の資料を3社に届ける。順2：網谷水産に注文が入る。B社にも料亭の注文が入る。"""
    iw = db.connect("iwase")
    try:
        bid = broadcast.publish(broadcast.compose(iw, on_date))
    finally:
        iw.close()
    network.announce_material(bid)
    out = dict(broadcast_id=bid)
    orders = (("amiya_order", sc.AJI_ORDER), ("b_order", sc.B_AJI_ORDER))
    if variant == "b_declines":   # B社の掲示は朝から出ている。料亭の注文はその後に取り消された
        out["b_post"] = network.post("buyer_b", "buy", "aji", 5, 5, 1650, "アジを 5kg 買いたいです。今朝のうちに受け渡しできれば助かります。",
                                     on_date, f"{on_date}T10:00:00+09:00")
        orders = orders[:1]
    for key, o in orders:
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
        if r["outcome"] == "error":   # 失敗した出来事は待ちに戻るので、ここで止める（同じ出来事を繰り返し呼ばない）
            break
    return log


# ── 撮影の1回（take）：種を作り直した専用の置き場で、本物の Claude で通す ──
#   python -m agentdock.scene --real            # 本物の Claude（claude-opus-5-5・effort medium）
#   python -m agentdock.scene                   # 模擬の Claude は台本が要るので、この入口では本物だけを扱う
# 置き場は AgentDock-local\takes\<日時>（OneDrive の外・本番の data とは別）。記録は take.json に書く

TAKES = r"C:\Users\mikiz\AgentDock-local\takes"


def _dump(folder) -> dict:
    out = {}
    for owner in db.OWNER_CODES:
        c = db.connect(owner)
        try:
            out[owner] = dict(
                runs=[dict(r) for r in c.execute("SELECT * FROM agent_run ORDER BY id")],
                steps=[dict(r) for r in c.execute("SELECT * FROM agent_step ORDER BY run_id, seq")],
                usage=[dict(r) for r in c.execute("SELECT * FROM api_usage ORDER BY id")])
        finally:
            c.close()
    m = db.connect(db.MARKET)
    try:
        out["market"] = dict(messages=[dict(r) for r in m.execute("SELECT * FROM network_message ORDER BY id")],
                             requests=[dict(r) for r in m.execute("SELECT * FROM deal_request ORDER BY id")])
    finally:
        m.close()
    return out


def main(argv=None) -> int:
    import argparse
    import datetime
    import json
    import os
    from pathlib import Path

    from agentdock import companion, runlog
    from agentdock.seed import build

    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--variant", default="normal", choices=list(VARIANTS))
    a = ap.parse_args(argv)
    if not a.real:
        print("use --real (the mock run needs a script; see tests/test_scene.py)")
        return 2
    folder = Path(TAKES) / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ("" if a.variant == "normal" else f"-{a.variant}"))
    folder.mkdir(parents=True)
    os.environ["AGENTDOCK_DATA"] = str(folder)
    if build.main([]) != 0:
        return 2
    apply_variant(a.variant)
    started = start(variant=a.variant)
    clients = {o: companion.ClaudeClient(effort=agents.autonomy.EFFORT) for o in db.OWNER_CODES}
    log = run(clients, run_kind="real")
    data = _dump(folder)
    data["log"] = log
    data["started"] = started
    data["variant"] = dict(name=a.variant, description=VARIANTS[a.variant])
    spent = runlog.spent_micro_usd()
    data["spent_usd"] = spent / 1_000_000
    (folder / "take.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for x in log:
        print(f"{x['owner']:8} {x.get('event_kind', ''):15} {x.get('outcome')!s:10} calls={x.get('calls')} steps={x.get('steps')}  {(x.get('summary') or '')[:80]}")
    print(f"take: {folder}")
    print(f"spent: {spent / 1_000_000:.3f} USD, calls: {sum(x.get('calls') or 0 for x in log)}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
