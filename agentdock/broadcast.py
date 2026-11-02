"""漁協が仲買の全員に同じ資料を届ける（公平の検査つき）。

決まり：資料は1通だけ作り、全員に同じ時刻・同じ内容で届ける。仲買ごとに変えない。
工夫は中身でする。違いは、受け取った仲買の仲間の判断から生まれる。

公平の検査（コード）：
- 宛先は登録された仲買の全員でなければならない（1社だけ・一部だけには送らない）
- 届けた全員の照合番号が、作った資料の照合番号と同じでなければならない
どちらかが崩れたら FairnessError で止め、何も記録しない。
"""
import hashlib
import json

from agentdock import criteria, db
from agentdock.seed import scenario as sc

BUYERS = ("amiya", "buyer_b", "buyer_c")


class FairnessError(Exception):
    pass


def fingerprint(body: dict) -> str:
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def compose(con, on_date: str) -> dict:
    """漁協の DB から今日の資料を組み立てる。数字はすべてコードで出す。"""
    items = []
    rows = con.execute(
        "SELECT i.code, i.name, i.unit, l.quantity, l.size_note, l.caught_at, l.area, l.water_temp_c "
        "FROM landing l JOIN item i ON i.id = l.item_id WHERE l.landing_date = ? AND l.kind = 'actual' ORDER BY i.id",
        (on_date,)).fetchall()
    for r in rows:
        fc = con.execute(
            "SELECT l.quantity FROM landing l JOIN item i ON i.id = l.item_id WHERE i.code = ? AND l.landing_date = ? AND l.kind = 'forecast'",
            (r["code"], on_date)).fetchone()
        recent = criteria.market_stats(con, r["code"], on_date)
        last_year = criteria.last_year_stats(con, r["code"], on_date)
        entry = dict(
            item=r["code"], name=r["name"], unit=r["unit"], quantity=r["quantity"],
            size=r["size_note"], caught_at=r["caught_at"], area=r["area"], water_temp_c=r["water_temp_c"],
            tomorrow_forecast=fc[0] if fc else None,
            recent_median_yen=recent["median"] if recent else None,
            last_year_median_yen=last_year["median"] if last_year else None,
        )
        expected = sc.TODAY_EXPECTED_YEN.get(r["code"])
        if expected is not None:
            entry["today_expected_yen"] = expected
            if recent:
                entry["vs_recent_pct"] = round((expected - recent["median"]) * 100 / recent["median"])
        note = sc.SELLER_NOTES.get(r["code"])
        if note:
            entry["note"] = note
        items.append(entry)
    return dict(sender="iwase", date=on_date, title=f"{on_date} の水揚げと見通し", items=items)


def publish(body: dict, recipients=BUYERS, tamper=None) -> int:
    """資料を全員へ届ける。tamper は試験用（特定の宛先の中身を書き換えて検査が止めるかを見る）。"""
    if tuple(sorted(recipients)) != tuple(sorted(BUYERS)):
        raise FairnessError(f"recipients must be all buyers: {sorted(recipients)}")
    fp = fingerprint(body)
    copies = {}
    for who in recipients:
        b = json.loads(json.dumps(body))
        if tamper and who in tamper:
            tamper[who](b)
        copies[who] = (b, fingerprint(b))
    bad = [w for w, (_, f) in copies.items() if f != fp]
    if bad:
        raise FairnessError(f"fingerprint differs for: {bad}")

    now = db.now_iso()
    con = db.connect("iwase")
    try:
        with con:
            cur = con.execute("INSERT INTO broadcast (created_at, title, body_json, fingerprint) VALUES (?, ?, ?, ?)",
                              (now, body["title"], json.dumps(body, ensure_ascii=False, sort_keys=True), fp))
            bid = cur.lastrowid
            for who, (b, f) in copies.items():
                con.execute("INSERT INTO broadcast_delivery (broadcast_id, recipient, delivered_at, fingerprint) VALUES (?, ?, ?, ?)",
                            (bid, who, now, f))
    finally:
        con.close()
    for who, (b, f) in copies.items():
        c = db.connect(who)
        try:
            with c:
                c.execute("INSERT INTO inbox_broadcast (broadcast_id, sender, received_at, fingerprint, body_json) VALUES (?, 'iwase', ?, ?, ?)",
                          (bid, now, f, json.dumps(b, ensure_ascii=False, sort_keys=True)))
        finally:
            c.close()
    return bid


def verify(broadcast_id: int) -> bool:
    """届けた記録と、各社が受け取った中身の照合番号が全部そろっているか。"""
    con = db.connect("iwase")
    try:
        fp = con.execute("SELECT fingerprint FROM broadcast WHERE id = ?", (broadcast_id,)).fetchone()[0]
        sent = {r[0]: r[1] for r in con.execute(
            "SELECT recipient, fingerprint FROM broadcast_delivery WHERE broadcast_id = ?", (broadcast_id,))}
    finally:
        con.close()
    if set(sent) != set(BUYERS) or any(f != fp for f in sent.values()):
        return False
    for who in BUYERS:
        c = db.connect(who)
        try:
            row = c.execute("SELECT fingerprint, body_json FROM inbox_broadcast WHERE broadcast_id = ?", (broadcast_id,)).fetchone()
        finally:
            c.close()
        if row is None or row[0] != fp or fingerprint(json.loads(row[1])) != fp:
            return False
    return True
