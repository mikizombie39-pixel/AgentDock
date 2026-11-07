"""本編の場面を動かす：漁協の資料・注文を入れ、出来事の起きた順に各組織の Claude AI パートナーを起こす。

  起きる順は出来事の時刻（マイクロ秒）で決まる。どの組織が何の出来事で起きるかは agents.WAKE。
  安全装置：起動の数の上限（max_runs）と、場面全体の呼び出しの上限（max_total_calls）。
"""
from agentdock import agents, broadcast, db, events, network, results, settle
from agentdock.seed import scenario as sc

MAX_RUNS = 16
MAX_TOTAL_CALLS = 60


VARIANTS = {
    "normal": "通常",
    "price_up": "漁協の数量ごとの最低の値が上がった（15kg 以上 1,640円・10kg 1,700円）",
    "b_declines": "B社の料亭の注文が取り消された（B社の掲示は朝から出ているが、注文と事前の承認は無い）",
    "vault_changed": "料亭は改装で休業中（売り先の特徴の書き付けを今日の日付で更新）",
    "buri_floor_up": "漁協のブリの最低の値が上がった（明日の見込みで上げる額 2,500円・17,000円 → 19,000円）",
    "buri_stock_4": "漁協のブリの相対の残りが 4本（6本 → 4本）",
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
        ex("amiya", "UPDATE vault_note SET title = ?, body = ?, written_on = ?, applies_when = ? WHERE vault_id = 'AM-S-006'",
           ("料亭は改装のため休業中", "料亭は 2026-10-30 から 11 月中旬まで改装で休業している。この間は仕入れの案内をしない。",
            "2026-11-01", "料亭にアジを案内する時"))
    elif name == "b_declines":
        ex("buyer_b", "UPDATE preapproval SET status = 'cancelled'")
    elif name == "buri_floor_up":
        ex("iwase", "UPDATE seller_rule SET hold_up_yen = ? WHERE item_id = (SELECT id FROM item WHERE code = 'buri_xl')", (sc.SCENE_C_HOLD_UP_YEN,))
    elif name == "buri_stock_4":
        ex("iwase", "UPDATE stock SET quantity = 4 WHERE lot_no = 'S-buri_xl-1'")
    elif name != "normal":
        raise ValueError(name)


def start(on_date: str = sc.DEMO_DATE, variant: str = "normal", item: str = "aji") -> dict:
    """順0：朝のせりの結果を漁協が全員へ届ける（各社の仲間が注文と突き合わせる）。順1：漁協の資料を3社に届ける。
    順2：品目の注文が入る（アジ：網谷水産と B社／ブリ：網谷水産に料亭の追加の注文）。
    入札の結果は届けない：AI が入札する道具が無い（mate.TENDER_TOOL）ので、網谷水産の入札は記録に無い"""
    if item not in sc.SCENE_ORDERS:
        raise ValueError(item)
    out_results = results.publish([r for r in results.today_results(on_date) if r["method"] == "auction"])
    iw = db.connect("iwase")
    try:
        bid = broadcast.publish(broadcast.compose(iw, on_date))
    finally:
        iw.close()
    network.announce_material(bid)
    out = dict(broadcast_id=bid, results_fingerprint=out_results)
    out["settle_run"] = settle.begin(on_date)   # 漁協の集計案（照合 → 請求・支払）を、この時点を対象時点として始める（進めるのは settle.step）
    orders = sc.SCENE_ORDERS[item]
    if variant == "b_declines":   # B社の掲示は朝から出ている。料亭の注文はその後に取り消された
        out["b_post"] = network.post("buyer_b", "buy", "aji", 5, 5, 1650, "アジを 5kg 買いたいです。今朝のうちに受け渡しできれば助かります。",
                                     on_date, f"{on_date}T10:00:00+09:00")
        orders = orders[:1]
    for key, o in orders:
        c = db.connect(o["owner"])
        try:
            if o.get("existing"):   # 朝に入っていた注文：業務を始めた時に、その注文の出来事を書く（注文は増やさない）
                out[key] = events.order_noticed(c, o["customer"], o["item"], on_date)
            else:
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
#   python -m agentdock.scene --real --change-qty 10   # ボードが出たら、担当者が相談に答えて数量を変え、続きを通す
#   python -m agentdock.scene --real --item buri_xl --change-qty 6 --change-alloc 料亭=6 --president --ask "…" --approve --max-calls 30
#       # ブリ：料亭の追加の注文で始め、担当者が配分を変え（社長待ちなら社長が答え）、根拠を聞き、/api/decide で承認する
#   python -m agentdock.scene                   # 模擬の Claude は台本が要るので、この入口では本物だけを扱う
# 置き場は保存先の根（db.LOCAL）の takes\<日時>（OneDrive の外・本番の data とは別）。記録は take.json に書く

TAKES = str(db.LOCAL / "takes")


def _dump(folder) -> dict:
    out = {}
    for owner in db.OWNER_CODES:
        c = db.connect(owner)
        try:
            out[owner] = dict(
                runs=[dict(r) for r in c.execute("SELECT * FROM agent_run ORDER BY id")],
                steps=[dict(r) for r in c.execute("SELECT * FROM agent_step ORDER BY run_id, seq")],
                usage=[dict(r) for r in c.execute("SELECT * FROM api_usage ORDER BY id")],
                events=[dict(r) for r in c.execute("SELECT * FROM biz_event ORDER BY id")],
                holds=[dict(r) for r in c.execute("SELECT * FROM stock_hold ORDER BY id")],
                consultations=[dict(r) for r in c.execute("SELECT * FROM consultation ORDER BY id")])
        finally:
            c.close()
    m = db.connect(db.MARKET)
    try:
        out["market"] = dict(messages=[dict(r) for r in m.execute("SELECT * FROM network_message ORDER BY id")],
                             requests=[dict(r) for r in m.execute("SELECT * FROM deal_request ORDER BY id")],
                             terms=[dict(r) for r in m.execute("SELECT * FROM offer_term ORDER BY id")],
                             agreements=[dict(r) for r in m.execute("SELECT * FROM agreement ORDER BY id")])
    finally:
        m.close()
    return out


def staff_change(quantity: int, alloc: dict | None = None) -> dict:
    """撮影の途中の担当者の操作：最新のボードの相談に、数量を変えて答える（画面の /api/answer と同じ consult.answer）。
    alloc は {売り先の名前: 数量}。その売り先の今日の注文があれば注文に充て、無ければ未打診の売り先にする。
    alloc が無く、数量が案件の注文の内なら、その注文に全量を充てる配分を付ける。"""
    from agentdock import consult
    c = db.connect("amiya")
    try:
        row = c.execute("SELECT c.id, d.order_id, d.item_id, d.trade_date, o.quantity FROM consultation c JOIN deal_case d ON d.id = c.case_id "
                        "JOIN order_book o ON o.id = d.order_id WHERE c.status = 'open' ORDER BY c.id DESC LIMIT 1").fetchone()
        if row is None:
            return dict(result="no_open_consultation")
        change = dict(quantity=quantity)
        if alloc:
            allocs = []
            for customer, n in alloc.items():
                o = c.execute("SELECT o.id FROM order_book o JOIN customer cu ON cu.id = o.customer_id WHERE cu.name = ? AND o.item_id = ? "
                              "AND o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id LIMIT 1",
                              (customer, row["item_id"], row["trade_date"])).fetchone()
                allocs.append(dict(order_id=o["id"], quantity=n) if o else dict(customer=customer, quantity=n))
            change["allocations"] = allocs
        elif quantity <= row["quantity"]:
            change["allocations"] = [dict(order_id=row["order_id"], quantity=quantity)]
        return dict(consult.answer(c, row["id"], "change", f"take-change-{row['id']}", change=change, role="staff"), change=change)
    finally:
        c.close()


def president_answer() -> dict:
    """社長（網谷さん）の答え：担当者の答えが社長待ち（needs_president）の相談に、同じ変更で答えて確定する（/api/answer と同じ道）。"""
    import json
    from agentdock import consult
    c = db.connect("amiya")
    try:
        row = c.execute("SELECT id, change_json FROM consultation WHERE status = 'needs_president' ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return dict(result="no_consultation_waiting_for_president")
        change = json.loads(row["change_json"])
        return dict(consult.answer(c, row["id"], "change", f"take-president-{row['id']}", change=change, role="president"), change=change)
    finally:
        c.close()


def _http(base: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """撮影の途中の人の操作を、画面と同じ API で送る（この PC の中のサーバーだけ）。"""
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8") if body is not None else None,
                                 method="POST" if body is not None else "GET", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


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
    ap.add_argument("--item", default="aji", choices=list(sc.SCENE_ORDERS), help="通す品目（aji・buri_xl）")
    ap.add_argument("--variant", default="normal", choices=list(VARIANTS))
    ap.add_argument("--change-qty", type=int, help="ボードが出た後に、担当者が相談に答えて変える仕入れの数量")
    ap.add_argument("--change-alloc", help="担当者が変える配分（売り先=数量 をカンマで並べる。例：料亭=6）")
    ap.add_argument("--president", action="store_true", help="担当者の答えが社長待ちになったら、社長（網谷さん）が同じ変更で答える")
    ap.add_argument("--ask", help="変更の後のボードに、担当者が聞く質問（/api/ask で出来事にする）")
    ap.add_argument("--approve", action="store_true", help="最後のボードの勧める案を /api/decide で承認する（社長待ちなら社長が承認する）")
    ap.add_argument("--max-calls", type=int, default=MAX_TOTAL_CALLS, help="撮影の1回の呼び出しの合計の上限")
    a = ap.parse_args(argv)
    if not a.real:
        print("use --real (the mock run needs a script; see tests/test_scene.py)")
        return 2
    tag = ("" if a.item == "aji" else f"-{a.item}") + ("" if a.variant == "normal" else f"-{a.variant}")
    folder = Path(TAKES) / (datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + tag)
    folder.mkdir(parents=True)
    os.environ["AGENTDOCK_DATA"] = str(folder)
    if build.main([]) != 0:
        return 2
    apply_variant(a.variant)
    started = start(variant=a.variant, item=a.item)
    clients = {o: companion.ClaudeClient(effort=agents.autonomy.EFFORT) for o in db.OWNER_CODES}

    def used(lg):
        return sum(x.get("calls") or 0 for x in lg)

    def more(lg, phase):
        left = a.max_calls - used(lg)
        if left > 0:
            lg += [dict(x, phase=phase) for x in run(clients, run_kind="real", max_total_calls=left)]

    log = run(clients, run_kind="real", max_total_calls=a.max_calls)
    change = president = asked = None
    decided = []
    if a.change_qty:
        alloc = {k.strip(): int(v) for k, v in (p.split("=") for p in a.change_alloc.split(","))} if a.change_alloc else None
        change = staff_change(a.change_qty, alloc)
        print(f"staff change: {change}")
        if change.get("result") == "needs_president" and a.president:
            president = president_answer()
            print(f"president: {president}")
        if (president or change).get("result") == "answered":
            more(log, "after_change")
    if a.ask or a.approve:
        from agentdock import server
        import threading
        srv = server.make_server(0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            if a.ask:
                _, b = _http(base, "/api/board?owner=amiya")
                st, r = _http(base, "/api/ask", dict(owner="amiya", case_id=(b.get("case") or {}).get("id"), text=a.ask, idempotency_key="take-ask-1"))
                asked = dict(status=st, response=r, text=a.ask)
                print(f"ask: {asked}")
                if st == 200:
                    more(log, "after_ask")
            if a.approve:
                _, b = _http(base, "/api/board?owner=amiya")
                L = b.get("layout") or {}
                opt = next((o for o in b.get("options") or [] if o["code"] == L.get("primary")), None)
                for actor in ("staff", "president"):
                    if opt is None:
                        break
                    st, r = _http(base, "/api/decide", dict(owner="amiya", case_id=b["case"]["id"], option_id=opt["id"], action="approve",
                                                            fingerprint=opt["fingerprint"], idempotency_key=f"take-approve-{actor}", actor=actor))
                    decided.append(dict(actor=actor, option=opt["code"], status=st, response=r))
                    print(f"decide ({actor}): {st} {r.get('result')}")
                    if r.get("result") != "needs_president":
                        break
        finally:
            srv.shutdown()
            srv.server_close()
    data = _dump(folder)
    data["change"] = change
    data["president"] = president
    data["ask"] = asked
    data["decide"] = decided
    data["log"] = log
    data["started"] = started
    data["item"] = a.item
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
