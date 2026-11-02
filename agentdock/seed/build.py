"""模擬の DB を作り直す。

  python -m agentdock.seed.build            # 本編の種
  python -m agentdock.seed.build --scene-c  # 不成立の場面の種（漁協の下限が 19,000円）

安全装置：消すのは置き場の中の既知の5ファイルだけ。置き場が OneDrive の中なら止める。
帳面（vault）の置き場は消さず、「vault-退避-日時」の名前で脇へ移す（DB の索引と帳面のファイルを常にそろえるため）。
"""
import argparse
import datetime
import sys

from agentdock import db
from agentdock.seed import prices, sales, scenario as sc


def _ids(con, table, key="code"):
    return {r[key]: r["id"] for r in con.execute(f"SELECT id, {key} FROM {table}")}


def build_owner(code: str, public_rows, scene_c: bool = False) -> None:
    con = db.connect(code)
    try:
        _fill_owner(con, code, public_rows, scene_c)
    finally:
        con.close()


def _fill_owner(con, code: str, public_rows, scene_c: bool) -> None:
    db.migrate(con, "owner")
    o = sc.OWNERS[code]
    now = db.now_iso()
    with con:
        con.execute("INSERT INTO owner (id, code, name, role, person_name, companion_name) VALUES (1, ?, ?, ?, ?, ?)",
                    (code, o["name"], o["role"], o["person_name"], o["companion_name"]))
        con.executemany("INSERT INTO item (code, name, unit, unit_weight_g) VALUES (?, ?, ?, ?)", sc.ITEMS)
        item = _ids(con, "item")
        con.executemany("INSERT INTO customer (name, kind) VALUES (?, ?)", sc.CUSTOMERS.get(code, []))
        cust = _ids(con, "customer", "name")

        if o["role"] == "buyer":
            con.executemany(
                "INSERT INTO price_history (item_id, trade_date, kind, unit_price_yen, quantity, source, lot_no) VALUES (?, ?, ?, ?, ?, 'public_result', ?)",
                [(item[c], d, k, p, q, n) for c, d, k, p, q, n in public_rows])
            handled = {i for _, i, *_ in sc.ORDERS.get(code, [])}
            mine = prices.own_trades(code, handled, public_rows)
        else:
            mine = public_rows   # 漁協の販売の記録＝自分の売った結果
        con.executemany(
            "INSERT INTO price_history (item_id, trade_date, kind, unit_price_yen, quantity, source, lot_no) VALUES (?, ?, ?, ?, ?, 'own_trade', ?)",
            [(item[c], d, k, p, q, n) for c, d, k, p, q, n in mine])

        con.executemany("INSERT INTO sales_price (item_id, customer_id, unit_price_yen, valid_from) VALUES (?, ?, ?, ?)",
                        [(item[i], cust[c], p, sc.HISTORY_FROM) for i, c, p in sc.SALES_PRICES.get(code, [])])
        con.executemany("INSERT INTO cost_rule (item_id, process_cost_yen, target_profit_yen, valid_from) VALUES (?, ?, ?, ?)",
                        [(item[i], c, p, sc.HISTORY_FROM) for i, c, p in sc.COST_RULES.get(code, [])])
        con.executemany("INSERT INTO buyer_rule (item_id, extra_qty_on_cheap_day, valid_from) VALUES (?, ?, ?)",
                        [(item[i], n, sc.HISTORY_FROM) for i, n in sc.BUYER_RULES.get(code, [])])
        rules = sc.SELLER_RULES.get(code, [])
        con.executemany("INSERT INTO seller_rule (item_id, base_floor_yen, high_landing_qty, hold_up_yen, valid_from) VALUES (?, ?, ?, ?, ?)",
                        [(item[i], f, q, sc.SCENE_C_HOLD_UP_YEN if scene_c else h, sc.HISTORY_FROM) for i, f, q, h in rules])
        sp = {(i, c): p for i, c, p in sc.SALES_PRICES.get(code, [])}
        con.executemany(
            "INSERT INTO sales_history (customer_id, item_id, sale_date, ordered_qty, sold_qty, unit_price_yen) VALUES (?, ?, ?, ?, ?, ?)",
            [(cust[c], item[i], d, o, s_, p) for c, i, d, o, s_, p in sales.history(code, public_rows, sp)])
        con.executemany(
            "INSERT INTO order_book (customer_id, item_id, quantity, sell_price_yen, order_date, received_at) VALUES (?, ?, ?, ?, ?, ?)",
            [(cust[c], item[i], q, p, sc.DEMO_DATE, f"{sc.DEMO_DATE}T05:00:00+09:00") for c, i, q, p in sc.ORDERS.get(code, [])])
        tomorrow = sc.DEMO_DATE  # 見込みの行も「デモの日に作った明日の見込み」として同じ日付で持つ
        con.executemany(
            "INSERT INTO landing (item_id, landing_date, kind, quantity, size_note, caught_at, area, water_temp_c) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(item[i], tomorrow, k, q, s, c, a, w) for i, k, q, s, c, a, w in sc.LANDINGS.get(code, [])])
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'system', 'seed', ?)",
                    (now, "scene_c" if scene_c else "main"))


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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene-c", action="store_true")
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
    for code in db.OWNER_CODES:
        build_owner(code, rows, scene_c=a.scene_c)
    build_market()
    print(f"built {len(db.OWNER_CODES) + 1} dbs in {folder} ({len(rows)} public results)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
