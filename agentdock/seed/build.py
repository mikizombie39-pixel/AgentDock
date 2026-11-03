"""模擬の DB を作り直す。

  python -m agentdock.seed.build            # 本編の種
  python -m agentdock.seed.build --scene-c  # 不成立の場面の種（漁協の下限が 19,000円）
  python -m agentdock.seed.build --with-aji-s  # 会期の前半の部品の試験用：アジ（小）も入れる

本編の DB にはアジ（小）を入れない（本編のアジ 1kg 1,500円と並ぶと値の差が不自然なため・2026-11-03 デイブ）。
アジ（小）の行も作ってから外すので、他の品目の模擬の値は入れる時と同じになる。

安全装置：消すのは置き場の中の既知の5ファイルだけ。置き場が OneDrive の中なら止める。
帳面（vault）の置き場は消さず、「vault-退避-日時」の名前で脇へ移す（DB の索引と帳面のファイルを常にそろえるため）。
"""
import argparse
import datetime
import sys

from agentdock import db, market
from agentdock.seed import prices, sales, scenario as sc


def _ids(con, table, key="code"):
    return {r[key]: r["id"] for r in con.execute(f"SELECT id, {key} FROM {table}")}


def build_owner(code: str, public_rows, scene_c: bool = False, with_aji_s: bool = False, extra_public=()) -> None:
    con = db.connect(code)
    try:
        _fill_owner(con, code, public_rows, scene_c, with_aji_s, extra_public)
    finally:
        con.close()


def _fill_owner(con, code: str, public_rows, scene_c: bool, with_aji_s: bool = False, extra_public=()) -> None:
    """extra_public は本編のアジ（kg）の公開の相場。自社の取引と売れ方の乱数には混ぜない（他の品目の値を動かさない）。"""
    db.migrate(con, "owner")
    o = sc.OWNERS[code]
    now = db.now_iso()
    skip = set() if with_aji_s else set(sc.LEGACY_ITEMS)

    def keep(rows, pos):
        return [r for r in rows if r[pos] not in skip]
    with con:
        con.execute("INSERT INTO owner (id, code, name, role, person_name, companion_name) VALUES (1, ?, ?, ?, ?, ?)",
                    (code, o["name"], o["role"], o["person_name"], o["companion_name"]))
        con.executemany("INSERT INTO item (code, name, unit, unit_weight_g) VALUES (?, ?, ?, ?)", keep(sc.ITEMS, 0))
        item = _ids(con, "item")
        con.executemany("INSERT INTO customer (name, kind) VALUES (?, ?)", sc.CUSTOMERS.get(code, []))
        cust = _ids(con, "customer", "name")
        con.executemany("INSERT INTO authority (role, person_name, max_trade_yen) VALUES (?, ?, ?)", sc.AUTHORITY.get(code, []))

        if o["role"] == "buyer":
            con.executemany(
                "INSERT INTO price_history (item_id, trade_date, kind, unit_price_yen, quantity, source, lot_no) VALUES (?, ?, ?, ?, ?, 'public_result', ?)",
                [(item[c], d, k, p, q, n) for c, d, k, p, q, n in [*keep(public_rows, 0), *extra_public]])
            handled = {i for _, i, *_ in sc.ORDERS.get(code, [])}
            mine = prices.own_trades(code, handled, public_rows)
        else:
            mine = [*public_rows, *extra_public]   # 漁協の販売の記録＝自分の売った結果
        con.executemany(
            "INSERT INTO price_history (item_id, trade_date, kind, unit_price_yen, quantity, source, lot_no) VALUES (?, ?, ?, ?, ?, 'own_trade', ?)",
            [(item[c], d, k, p, q, n) for c, d, k, p, q, n in keep(mine, 0)])

        con.executemany("INSERT INTO sales_price (item_id, customer_id, unit_price_yen, valid_from) VALUES (?, ?, ?, ?)",
                        [(item[i], cust[c], p, sc.HISTORY_FROM) for i, c, p in keep(sc.SALES_PRICES.get(code, []), 0)])
        con.executemany("INSERT INTO cost_rule (item_id, process_cost_yen, target_profit_yen, valid_from) VALUES (?, ?, ?, ?)",
                        [(item[i], c, p, sc.HISTORY_FROM) for i, c, p in keep(sc.COST_RULES.get(code, []), 0)])
        con.executemany("INSERT INTO buyer_rule (item_id, extra_qty_on_cheap_day, valid_from) VALUES (?, ?, ?)",
                        [(item[i], n, sc.HISTORY_FROM) for i, n in sc.BUYER_RULES.get(code, [])])
        rules = sc.SELLER_RULES.get(code, [])
        con.executemany("INSERT INTO seller_rule (item_id, base_floor_yen, high_landing_qty, hold_up_yen, valid_from) VALUES (?, ?, ?, ?, ?)",
                        [(item[i], f, q, sc.SCENE_C_HOLD_UP_YEN if scene_c else h, sc.HISTORY_FROM) for i, f, q, h in rules])
        sp = {(i, c): p for i, c, p in sc.SALES_PRICES.get(code, [])}
        con.executemany(
            "INSERT INTO sales_history (customer_id, item_id, sale_date, ordered_qty, sold_qty, unit_price_yen) VALUES (?, ?, ?, ?, ?, ?)",
            [(cust[c], item[i], d, o, s_, p) for c, i, d, o, s_, p in keep(sales.history(code, public_rows, sp), 1)])
        con.executemany(
            "INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(cust[c], item[i], q, p, sc.DEMO_DATE, f"{sc.DEMO_DATE}T05:00:00+09:00") for c, i, q, p in keep(sc.ORDERS.get(code, []), 1)])
        tomorrow = sc.DEMO_DATE  # 見込みの行も「デモの日に作った明日の見込み」として同じ日付で持つ
        con.executemany(
            "INSERT INTO landing (item_id, landing_date, kind, quantity, size_note, caught_at, area, water_temp_c) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(item[i], tomorrow, k, q, s, c, a, w) for i, k, q, s, c, a, w in keep(sc.LANDINGS.get(code, []), 0)])
        _fill_autonomy(con, code, item, cust, now)
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'system', 'seed', ?)",
                    (now, "scene_c" if scene_c else "main"))


def _fill_autonomy(con, code: str, item: dict, cust: dict, now: str) -> None:
    """本編（自律の場面）の種：数量ごとの最低の値・在庫・案内してよい売り先・売れ方・Vault。"""
    con.executemany("INSERT INTO quote_rule (item_id, min_qty, floor_yen, valid_from) VALUES (?, ?, ?, ?)",
                    [(item[i], q, f, sc.HISTORY_FROM) for i, q, f in sc.QUOTE_RULES.get(code, [])])
    con.executemany("INSERT INTO stock (item_id, quantity, status, lot_no, stock_date, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    [(item[i], q, s, n, sc.DEMO_DATE, now) for i, q, s, n in sc.STOCK.get(code, [])])
    con.executemany("UPDATE customer SET allow_contact = 1 WHERE name = ?", [(c,) for c in sc.CONTACT_OK.get(code, [])])
    con.executemany("INSERT INTO process_step (item_id, name, cost_yen_per_unit, valid_from) VALUES (?, ?, ?, ?)",
                    [(item[i], n, c, sc.HISTORY_FROM) for i, n, c in sc.PROCESS_STEPS.get(code, [])])
    steps = {(r[0], r[1]): r[2] for r in con.execute("SELECT item_id, name, id FROM process_step")}
    con.executemany("INSERT INTO route_process (item_id, destination, destination_kind, process_step_id, status, note) VALUES (?, ?, ?, ?, ?, ?)",
                    [(item[i], d, k, steps[(item[i], s)] if s else None, st, nt) for i, d, k, s, st, nt in sc.ROUTE_PROCESS.get(code, [])])
    con.executemany(
        "INSERT INTO preapproval (item_id, side, quantity, max_unit_price_yen, min_unit_price_yen, spec, deliver_by, approved_by, approved_at, valid_until, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')",
        [(item[i], sd, q, mx, mn, sp, dl, by, now, sc.PREAPPROVAL_VALID_UNTIL) for i, sd, q, mx, mn, sp, dl, by in sc.PREAPPROVALS.get(code, [])])
    con.executemany(
        "INSERT INTO sales_history (customer_id, item_id, sale_date, ordered_qty, sold_qty, unit_price_yen) VALUES (?, ?, ?, ?, ?, ?)",
        [(cust[c], item[i], d, o, s_, p) for c, i, d, o, s_, p in sc.AJI_SALES_HISTORY.get(code, [])])
    con.executemany(
        "INSERT INTO vault_note (kind, title, body, item_id, customer_id, author, source, written_on, applies_when) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(k, t, b, item[i] if i else None, cust[c] if c else None, a, s, w, aw)
         for k, t, b, i, c, a, s, w, aw in sc.VAULT_NOTES.get(code, [])])


def archive_vault(folder):
    """帳面の置き場があれば、消さずに日時つきの名前で脇へ移す。移した先を返す（無ければ None）。"""
    src = folder / "vault"
    if not src.exists():
        return None
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = folder / f"vault-退避-{stamp}"
    n = 2
    while dst.exists():
        dst = folder / f"vault-退避-{stamp}-{n}"
        n += 1
    src.rename(dst)
    return dst


def build_market() -> None:
    con = db.connect(db.MARKET)
    db.migrate(con, "market")
    con.close()
    for owner, side, item, qty, min_qty, price, msg in sc.DEAL_REQUESTS:
        oc = db.connect(owner)
        try:
            market.post_request(oc, owner, side, item, qty, min_qty, price, msg, sc.DEMO_DATE, sc.DEAL_REQUEST_VALID_UNTIL)
        finally:
            oc.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-c", action="store_true")
    ap.add_argument("--with-aji-s", action="store_true")
    a = ap.parse_args(argv)

    folder = db.data_dir()
    if "onedrive" in str(folder).lower():
        print(f"stop: data dir is inside OneDrive: {folder}", file=sys.stderr)
        return 2
    folder.mkdir(parents=True, exist_ok=True)
    for code in (*db.OWNER_CODES, db.MARKET):
        p = db.db_path(code)
        for suffix in ("", "-wal", "-shm", "-journal"):
            q = p.with_name(p.name + suffix)
            if q.exists():
                q.unlink()
    archived = archive_vault(folder)
    if archived:
        print(f"moved old vault to {archived}")

    rows = prices.public_results(sc.HISTORY_FROM, sc.HISTORY_TO)
    aji_kg = prices.public_results_aji_kg(sc.HISTORY_FROM, sc.HISTORY_TO)
    for code in db.OWNER_CODES:
        build_owner(code, rows, scene_c=a.scene_c, with_aji_s=a.with_aji_s, extra_public=aji_kg)
    build_market()
    print(f"built {len(db.OWNER_CODES) + 1} dbs in {folder} ({len(rows)} public results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
