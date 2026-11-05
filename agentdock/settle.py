"""漁協の集計案：せりの後の照合・仲買への請求・荷主への支払を、コードが1件ずつ実処理して漁協の DB に保存する（2026-11-05 デイブ・SAL）。

範囲：集計案まで。請求書の発行・支払の確定・送金は扱わない。

決まり：
- 対象は確定したせりの結果（市場の auction_result）と、市場の帳簿に登録した漁協の売りの相対の取引（trade・registered・seller=漁協）。仮押さえは入れない
- 金額は実証用ルール（settle_rule）でコードが計算する。円未満は切り捨て
  仲買への請求：取引額 ＝ 単価 × 数量・請求の消費税 ＝ 取引額 × 8%・請求額 ＝ 取引額 ＋ 消費税
  荷主への支払：手数料 ＝ 取引額 × 5%・手数料の消費税 ＝ 手数料 × 10%・支払額 ＝ 取引額 − 手数料 − 手数料の消費税
- 同じ元の取引を同じ種類で2回数えない（settle_line の一意の制約）。止まった後に step を呼べば続きから進む
- 照合：せりの結果の1行ごと・組織ごと（漁協・各仲買）に、市場の記録の公開の部分と受け取った行の照合番号を比べる。食い違いは mismatch で、請求と支払は始めない
- 初回の対象時点（cutoff_at）の後に成立した取引は、追加の集計（additional=1）で反映する。反映されていない取引が残る間は complete=False
"""
import datetime

from agentdock import db, results
from agentdock.seed import scenario as sc

OWNER = results.SELLER
CHECK_ORDER = (results.SELLER, *results.BUYERS)          # 照合する相手（漁協・網谷水産・B社・C社）
KINDS = ("reconcile", "invoice", "payout")
NOTE = "実証用データを実処理しています。進行を見せるため、処理間隔を2秒に調整しています。"


def _sec(s) -> int:
    return int(datetime.datetime.fromisoformat(s).timestamp())


def _rule(con):
    return con.execute("SELECT * FROM settle_rule ORDER BY valid_from DESC, id DESC LIMIT 1").fetchone()


def compute(kind: str, gross_yen: int, rule) -> dict:
    """1行の金額（コードの計算・円未満は切り捨て）。"""
    if kind == "invoice":
        tax = gross_yen * rule["sale_tax_permille"] // 1000
        return dict(fee_yen=0, tax_yen=tax, amount_yen=gross_yen + tax)
    fee = gross_yen * rule["fee_permille"] // 1000
    tax = fee * rule["fee_tax_permille"] // 1000
    return dict(fee_yen=fee, tax_yen=tax, amount_yen=gross_yen - fee - tax)


def sources(on_date: str, con=None) -> list[dict]:
    """集計の対象（確定した取引だけ・時刻の順）。せりの結果は漁協が受け取った時刻、相対の取引は市場の帳簿に登録した時刻。"""
    own = con or db.connect(OWNER)
    m = db.connect(db.MARKET)
    try:
        got = {r[0]: r[1] for r in own.execute("SELECT result_id, received_at FROM auction_result_in WHERE result_id LIKE ?", (f"{on_date}-%",))}
        out = [dict(source_kind="auction_result", source_id=r["result_id"], lot_no=r["lot_no"], item_code=r["item_code"], buyer=r["buyer"],
                    quantity=r["quantity"], unit_price_yen=r["unit_price_yen"], at=got.get(r["result_id"]))
               for r in m.execute("SELECT * FROM auction_result WHERE trade_date = ? ORDER BY id", (on_date,)) if got.get(r["result_id"])]
        lots = {r[0]: r[1] for r in own.execute("SELECT trade_id, lot_no FROM stock WHERE trade_id IS NOT NULL")}
        out += [dict(source_kind="trade", source_id=r["trade_id"], lot_no=lots.get(r["trade_id"], r["lot_no"]), item_code=r["item_code"], buyer=r["buyer"],
                     quantity=r["quantity"], unit_price_yen=r["unit_price_yen"], at=r["created_at"])
                for r in m.execute("SELECT t.*, l.lot_no, l.item_code FROM trade t JOIN lot l ON l.id = t.lot_id "
                                   "WHERE t.seller = ? AND t.status = 'registered' AND l.trade_date = ? ORDER BY t.id", (OWNER, on_date))]
    finally:
        m.close()
        if con is None:
            own.close()
    return sorted(out, key=lambda s: (_sec(s["at"]), s["source_kind"], s["source_id"]))


def begin(on_date: str = sc.DEMO_DATE, cutoff_at: str | None = None) -> int:
    """初回の集計を作る（照合は running・請求と支払は照合の後に始めるので waiting）。もう作ってあれば、その照合の番号を返す。"""
    con = db.connect(OWNER)
    try:
        r = con.execute("SELECT id FROM settle_run WHERE kind = 'reconcile' AND trade_date = ? AND additional = 0", (on_date,)).fetchone()
        if r:
            return r[0]
        now = db.now_iso()
        cutoff = cutoff_at or now
        rule = _rule(con)
        n = len([s for s in sources(on_date, con) if _sec(s["at"]) <= _sec(cutoff)])
        rows = len(_market(on_date))
        with con:
            rid = con.execute("INSERT INTO settle_run (kind, trade_date, cutoff_at, status, total_items, rule_id, created_at, started_at) "
                              "VALUES ('reconcile', ?, ?, 'running', ?, ?, ?, ?)", (on_date, cutoff, len(CHECK_ORDER) * rows, rule["id"], now, now)).lastrowid
            for k in ("invoice", "payout"):
                con.execute("INSERT INTO settle_run (kind, trade_date, cutoff_at, status, total_items, rule_id, created_at) VALUES (?, ?, ?, 'waiting', ?, ?, ?)",
                            (k, on_date, cutoff, n, rule["id"], now))
        return rid
    finally:
        con.close()


def _market(on_date: str) -> list[dict]:
    m = db.connect(db.MARKET)
    try:
        return [dict(r) for r in m.execute("SELECT result_id, lot_no, item_code AS item, unit_price_yen, quantity, trade_date FROM auction_result "
                                           "WHERE trade_date = ? ORDER BY id", (on_date,))]
    finally:
        m.close()


def _received_fp(who: str, on_date: str, result_id: str) -> str | None:
    c = db.connect(who)
    try:
        got = [r for r in results._received(c, on_date) if r["result_id"] == result_id]
    finally:
        c.close()
    return results.fingerprint([results._public(dict(r, trade_date=on_date)) for r in got]) if got else None


def _step_reconcile(con, run, now) -> dict | None:
    done = {(r[0], r[1]) for r in con.execute("SELECT who, result_id FROM settle_check WHERE run_id = ?", (run["id"],))}
    rows = _market(run["trade_date"])
    nxt = next(((w, r) for w in CHECK_ORDER for r in rows if (w, r["result_id"]) not in done), None)
    if nxt is None:
        return None
    who, row = nxt
    expected = results.fingerprint([results._public(row)])
    got = _received_fp(who, run["trade_date"], row["result_id"])
    with con:
        con.execute("INSERT OR IGNORE INTO settle_check (run_id, who, result_id, expected, got, ok, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run["id"], who, row["result_id"], expected, got, int(got == expected), now))
    return dict(kind="reconcile", who=who, result_id=row["result_id"], ok=got == expected)


def _step_line(con, run, now) -> dict | None:
    rule = con.execute("SELECT * FROM settle_rule WHERE id = ?", (run["rule_id"],)).fetchone()
    have = {r[0] for r in con.execute("SELECT source_kind || ':' || source_id FROM settle_line WHERE kind = ?", (run["kind"],))}
    src = next((s for s in sources(run["trade_date"], con) if _sec(s["at"]) <= _sec(run["cutoff_at"]) and f"{s['source_kind']}:{s['source_id']}" not in have), None)
    if src is None:
        return None
    gross = src["quantity"] * src["unit_price_yen"]
    if run["kind"] == "invoice":
        party = src["buyer"]
    else:
        r = con.execute("SELECT consignor_id FROM lot_consignor WHERE lot_no = ?", (src["lot_no"],)).fetchone()
        party = str(r[0]) if r else "unknown"
    amt = compute(run["kind"], gross, rule)
    with con:
        con.execute("INSERT OR IGNORE INTO settle_line (run_id, kind, source_kind, source_id, lot_no, item_code, party, quantity, unit_price_yen, gross_yen, "
                    "fee_yen, tax_yen, amount_yen, rule_id, source_at, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run["id"], run["kind"], src["source_kind"], src["source_id"], src["lot_no"], src["item_code"], party, src["quantity"],
                     src["unit_price_yen"], gross, amt["fee_yen"], amt["tax_yen"], amt["amount_yen"], rule["id"], src["at"], now))
    return dict(kind=run["kind"], source=f"{src['source_kind']}:{src['source_id']}", party=party, gross_yen=gross, **amt)


def _refresh(con, run, now) -> None:
    """件数と状態を、保存した行から数え直す（再開しても同じになる）。"""
    if run["kind"] == "reconcile":
        rows = con.execute("SELECT ok FROM settle_check WHERE run_id = ?", (run["id"],)).fetchall()
        done = len(rows)
        status = run["status"] if done < run["total_items"] else ("done" if all(r[0] for r in rows) else "mismatch")
    else:
        done = con.execute("SELECT count(*) FROM settle_line WHERE run_id = ?", (run["id"],)).fetchone()[0]
        status = "done" if done >= run["total_items"] else run["status"]
    with con:
        con.execute("UPDATE settle_run SET done_items = ?, status = ?, finished_at = CASE WHEN ? IN ('done', 'mismatch') THEN coalesce(finished_at, ?) ELSE finished_at END "
                    "WHERE id = ?", (done, status, status, now, run["id"]))


def step(now: str | None = None, on_date: str = sc.DEMO_DATE) -> list[dict]:
    """動いている集計ごとに1件だけ処理して保存する（並行）。照合が終われば請求と支払を始め、対象時点の後の取引には追加の集計を作る。"""
    now = now or db.now_iso()
    con = db.connect(OWNER)
    try:
        done = []
        for _ in range(2):   # 処理の前に、始められる集計を始める（照合の後の請求・支払と、追加の集計）。1度目で何も処理しなければ、もう1度だけ見る
            rec = con.execute("SELECT status FROM settle_run WHERE kind = 'reconcile' AND trade_date = ? AND additional = 0", (on_date,)).fetchone()
            if rec and rec[0] == "done":
                with con:
                    con.execute("UPDATE settle_run SET status = 'running', started_at = ? WHERE trade_date = ? AND status = 'waiting' AND kind IN ('invoice', 'payout')",
                                (now, on_date))
                _open_additional(con, on_date, now)
            for run in con.execute("SELECT * FROM settle_run WHERE trade_date = ? AND status = 'running' ORDER BY id", (on_date,)).fetchall():
                r = _step_reconcile(con, run, now) if run["kind"] == "reconcile" else _step_line(con, run, now)
                if r:
                    done.append(r)
                _refresh(con, con.execute("SELECT * FROM settle_run WHERE id = ?", (run["id"],)).fetchone(), now)
            if done:
                break
        return done
    finally:
        con.close()


def _open_additional(con, on_date: str, now: str) -> None:
    """初回の後に成立して、まだ行の無い取引があれば、種類ごとに追加の集計を作る（同じ種類で動いている集計がある間は作らない）。"""
    rule = _rule(con)
    src = sources(on_date, con)
    for k in ("invoice", "payout"):
        if con.execute("SELECT 1 FROM settle_run WHERE trade_date = ? AND kind = ? AND status IN ('waiting', 'running')", (on_date, k)).fetchone():
            continue
        have = {r[0] for r in con.execute("SELECT source_kind || ':' || source_id FROM settle_line WHERE kind = ?", (k,))}
        missing = [s for s in src if f"{s['source_kind']}:{s['source_id']}" not in have]
        if missing:
            with con:
                con.execute("INSERT INTO settle_run (kind, trade_date, cutoff_at, additional, status, total_items, rule_id, created_at, started_at) "
                            "VALUES (?, ?, ?, 1, 'running', ?, ?, ?, ?)", (k, on_date, max(s["at"] for s in missing), len(missing), rule["id"], now, now))


def status(on_date: str = sc.DEMO_DATE) -> dict:
    """仕事ごとの状態・件数と、追加集計待ちの取引。complete は、照合が done・請求と支払が done・追加集計待ちが無い時だけ True。"""
    con = db.connect(OWNER)
    try:
        runs = [dict(r) for r in con.execute("SELECT * FROM settle_run WHERE trade_date = ? ORDER BY id", (on_date,))]
        if not runs:
            return dict(started=False, jobs={}, waiting=[], complete=False, note=NOTE)
        src = sources(on_date, con)
        have = {k: {r[0] for r in con.execute("SELECT source_kind || ':' || source_id FROM settle_line WHERE kind = ?", (k,))} for k in ("invoice", "payout")}
        cutoff = next(r["cutoff_at"] for r in runs if r["kind"] == "reconcile" and not r["additional"])
        waiting = [s for s in src if _sec(s["at"]) > _sec(cutoff) and any(f"{s['source_kind']}:{s['source_id']}" not in have[k] for k in have)]
        jobs = {}
        for k in KINDS:
            mine = [r for r in runs if r["kind"] == k]
            st = "mismatch" if any(r["status"] == "mismatch" for r in mine) else "running" if any(r["status"] == "running" for r in mine) else \
                 "waiting" if any(r["status"] == "waiting" for r in mine) else "done"
            jobs[k] = dict(status=st, total=sum(r["total_items"] for r in mine), done=sum(r["done_items"] for r in mine),
                           runs=[r["id"] for r in mine], additional=any(r["additional"] for r in mine))
        complete = all(j["status"] == "done" for j in jobs.values()) and not waiting
        return dict(started=True, cutoff_at=cutoff, jobs=jobs, waiting=waiting, complete=complete, note=NOTE)
    finally:
        con.close()
