"""判断の基準をコードで計算する（仲間は計算された基準を読んで判断する）。

数字はすべてここで出す。中央値と幅は Python で計算する（SQLite だけの関数を使わない）。
"""
import datetime
import statistics


def _item_id(con, item_code: str) -> int:
    row = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()
    if row is None:
        raise KeyError(item_code)
    return row["id"]


def ceiling_yen(con, item_code: str, customer_name: str) -> int:
    """買い手の上限 ＝ 売値 − 費用 − 欲しい利益（1単位）。"""
    iid = _item_id(con, item_code)
    sp = con.execute(
        "SELECT s.unit_price_yen FROM sales_price s JOIN customer c ON c.id = s.customer_id "
        "WHERE s.item_id = ? AND c.name = ? ORDER BY s.valid_from DESC LIMIT 1", (iid, customer_name)).fetchone()
    cr = con.execute(
        "SELECT process_cost_yen, target_profit_yen FROM cost_rule WHERE item_id = ? ORDER BY valid_from DESC LIMIT 1",
        (iid,)).fetchone()
    if sp is None or cr is None:
        raise LookupError(f"no sales price or cost rule: {item_code} / {customer_name}")
    return sp[0] - cr[0] - cr[1]


def floor_yen(con, item_code: str, on_date: str) -> tuple[int, bool]:
    """売り手の下限。明日の見込みが多い日は下限を上げる。(下限, 上げたか) を返す。"""
    iid = _item_id(con, item_code)
    rule = con.execute(
        "SELECT base_floor_yen, high_landing_qty, hold_up_yen FROM seller_rule WHERE item_id = ? ORDER BY valid_from DESC LIMIT 1",
        (iid,)).fetchone()
    if rule is None:
        raise LookupError(f"no seller rule: {item_code}")
    fc = con.execute(
        "SELECT quantity FROM landing WHERE item_id = ? AND landing_date = ? AND kind = 'forecast'", (iid, on_date)).fetchone()
    held = fc is not None and fc[0] >= rule[1]
    return rule[0] + (rule[2] if held else 0), held


def _stats(values):
    if not values:
        return None
    if len(values) == 1:
        v = values[0]
        return dict(median=v, low=v, high=v, count=1)
    q = statistics.quantiles(values, n=4, method="inclusive")
    return dict(median=int(round(statistics.median(values))), low=int(round(q[0])), high=int(round(q[2])), count=len(values))


def market_stats(con, item_code: str, on_date: str, days: int = 30, source: str | None = None):
    """on_date の前日までの days 日の相場：中央値と、安い方から4分の1・4分の3の値。"""
    iid = _item_id(con, item_code)
    if source is None:
        role = con.execute("SELECT role FROM owner").fetchone()[0]
        source = "own_trade" if role == "seller" else "public_result"
    end = datetime.date.fromisoformat(on_date) - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days - 1)
    vals = [r[0] for r in con.execute(
        "SELECT unit_price_yen FROM price_history WHERE item_id = ? AND source = ? AND trade_date BETWEEN ? AND ?",
        (iid, source, start.isoformat(), end.isoformat()))]
    return _stats(vals)


def last_year_stats(con, item_code: str, on_date: str, window: int = 15, source: str | None = None):
    """去年の同じ日の前後 window 日の相場。"""
    d = datetime.date.fromisoformat(on_date)
    center = d.replace(year=d.year - 1)
    iid = _item_id(con, item_code)
    if source is None:
        role = con.execute("SELECT role FROM owner").fetchone()[0]
        source = "own_trade" if role == "seller" else "public_result"
    start = (center - datetime.timedelta(days=window)).isoformat()
    end = (center + datetime.timedelta(days=window)).isoformat()
    vals = [r[0] for r in con.execute(
        "SELECT unit_price_yen FROM price_history WHERE item_id = ? AND source = ? AND trade_date BETWEEN ? AND ?",
        (iid, source, start, end))]
    return _stats(vals)


def spare_quantity(won: int, ordered: int) -> int:
    """買えた数のうち、注文を越えて余る数（仲買どうしの相対で譲れる数）。"""
    return max(0, won - ordered)


CHEAP_RATIO = 0.85


def cheapness_by_day(rows, days: int = 30):
    """[(date_iso, price)] から、日ごとに「その日の平均 ÷ 直近 days 日の日ごとの平均の中央値」を返す。
    直近の記録が無い日は None。"""
    per_day = {}
    for day, price in rows:
        per_day.setdefault(day, []).append(price)
    means = {d: sum(v) / len(v) for d, v in per_day.items()}
    ordered = sorted(means)
    out = {}
    for d in ordered:
        start = (datetime.date.fromisoformat(d) - datetime.timedelta(days=days)).isoformat()
        prev = [means[x] for x in ordered if start <= x < d]
        out[d] = round(means[d] / statistics.median(prev), 3) if prev else None
    return out


def sell_ratio(con, item_code: str, customer_name: str, on_date: str, days: int = 365):
    """安い日とふだんの日に、注文の何倍売れたか（中央値）。仲間が「多めに買う」根拠に使う。"""
    iid = _item_id(con, item_code)
    start = (datetime.date.fromisoformat(on_date) - datetime.timedelta(days=days)).isoformat()
    prices = [(r[0], r[1]) for r in con.execute(
        "SELECT trade_date, unit_price_yen FROM price_history WHERE item_id = ? AND source = 'public_result' AND trade_date < ?",
        (iid, on_date))]
    cheap = cheapness_by_day(prices)
    rows = con.execute(
        "SELECT s.sale_date, s.ordered_qty, s.sold_qty FROM sales_history s JOIN customer c ON c.id = s.customer_id "
        "WHERE s.item_id = ? AND c.name = ? AND s.sale_date >= ? AND s.sale_date < ?", (iid, customer_name, start, on_date))
    on_cheap, on_normal = [], []
    for day, ordered, sold in rows:
        r = cheap.get(day)
        if r is None:
            continue
        (on_cheap if r <= CHEAP_RATIO else on_normal).append(sold / ordered)
    def med(v):
        return round(statistics.median(v), 2) if v else None
    return dict(cheap_days=len(on_cheap), cheap_ratio=med(on_cheap), normal_days=len(on_normal), normal_ratio=med(on_normal))
