"""1年分の架空の相場（公開の結果）を作る。

季節の形：富山湾の漁獲期と相場の傾向（高い時期・安い時期）を参考にした。値はすべて架空。
日ごとの上下は乱数で、種を固定する（何度作っても同じ値）。
市の休み：日曜日と年末年始（12/31〜1/4）は行を作らない。
"""
import datetime
import random

SEED = 20261102

# 月ごとの中心の値（1単位の円）。旬の外の月は None（行を作らない）
# ブリ（特大・1本 約 9kg）：12月中旬〜1月中旬が高値、5月中旬〜6月中旬が安値、2月はやや安値
# シロエビ（1箱 1kg）：4月〜11月。漁期の初めが高く、夏は落ち着く
# アジ（小・1箱 10kg）：7月〜9月上旬が高値、9月中旬〜1月上旬と4〜6月が安値
MONTHLY_CENTER = {
    "buri_xl": {1: 34000, 2: 22500, 3: 20500, 4: 19000, 5: 16500, 6: 14500, 7: 16000,
                8: 17000, 9: 17500, 10: 17700, 11: 18000, 12: 32000},
    "shiroebi": {1: None, 2: None, 3: None, 4: 4800, 5: 4650, 6: 4450, 7: 4300,
                 8: 4300, 9: 4400, 10: 4450, 11: 4500, 12: None},
    "aji_s": {1: 3600, 2: 4200, 3: 5500, 4: 3200, 5: 3000, 6: 3000, 7: 7000,
              8: 7000, 9: 5000, 10: 3500, 11: 3400, 12: 3300},
}
# 日ごとの上下の幅（中心に対する割合）と、値の刻み
SPREAD = {"buri_xl": 0.05, "shiroebi": 0.05, "aji_s": 0.12}
TICK = {"buri_xl": 100, "shiroebi": 50, "aji_s": 100}
# 1日の公開の結果の件数（ロットの数）と1件の数量
LOTS_PER_DAY = {"buri_xl": (2, 4), "shiroebi": (2, 3), "aji_s": (2, 4)}
QTY_PER_LOT = {"buri_xl": (2, 6), "shiroebi": (3, 10), "aji_s": (5, 20)}
KIND = {"buri_xl": "auction", "shiroebi": "tender", "aji_s": "auction"}
# 豊漁で安い日：その日の中心の値に掛ける倍率と、起きる割合（アジだけ）
GLUT = {"aji_s": (0.72, 0.12)}


def market_days(start: str, end: str):
    d = datetime.date.fromisoformat(start)
    last = datetime.date.fromisoformat(end)
    while d <= last:
        closed = d.weekday() == 6 or (d.month == 12 and d.day == 31) or (d.month == 1 and d.day <= 4)
        if not closed:
            yield d
        d += datetime.timedelta(days=1)


def _round(v: float, tick: int) -> int:
    return max(tick, int(round(v / tick)) * tick)


def public_results(start: str, end: str):
    """[(item_code, date_iso, kind, unit_price_yen, quantity, lot_no)] を返す。"""
    rng = random.Random(SEED)
    rows = []
    for d in market_days(start, end):
        for code, months in MONTHLY_CENTER.items():
            center = months[d.month]
            if center is None:
                continue
            if code in GLUT and rng.random() < GLUT[code][1]:
                center = center * GLUT[code][0]
            lo, hi = LOTS_PER_DAY[code]
            for n in range(rng.randint(lo, hi)):
                price = _round(center * (1 + rng.uniform(-SPREAD[code], SPREAD[code])), TICK[code])
                q_lo, q_hi = QTY_PER_LOT[code]
                lot_no = f"{d:%Y%m%d}-{code}-{n + 1}"
                rows.append((code, d.isoformat(), KIND[code], price, rng.randint(q_lo, q_hi), lot_no))
    return rows


# アジ（kg・本編の品目）：公開の相場は別の乱数で作る（他の品目の値を動かさないため）。直近30日の中央値は 1kg 1,680円前後
AJI_KG_CENTER = {1: 1900, 2: 1950, 3: 1850, 4: 1750, 5: 1600, 6: 1550, 7: 1500,
                 8: 1550, 9: 1650, 10: 1680, 11: 1700, 12: 1850}


def public_results_aji_kg(start: str, end: str):
    """本編のアジ（kg）の公開の結果。形は public_results と同じ。"""
    rng = random.Random(f"{SEED}-aji-kg")
    rows = []
    for d in market_days(start, end):
        center = AJI_KG_CENTER[d.month]
        for n in range(rng.randint(1, 2)):
            price = _round(center * (1 + rng.uniform(-0.06, 0.06)), 10)
            rows.append(("aji", d.isoformat(), "auction", price, rng.randint(10, 40), f"{d:%Y%m%d}-aji-{n + 1}"))
    return rows


def own_trades(owner_code: str, items, public_rows, every_n_days: int = 3):
    """自社の過去の取引：公開の結果のうち、自社が扱う品目から数日おきに1件を自社の取引とする。"""
    rng = random.Random(f"{SEED}-{owner_code}")
    by_day = {}
    for row in public_rows:
        if row[0] in items:
            by_day.setdefault((row[0], row[1]), []).append(row)
    out = []
    for i, key in enumerate(sorted(by_day)):
        if i % every_n_days:
            continue
        code, day, kind, price, qty, lot_no = rng.choice(by_day[key])
        out.append((code, day, kind, price, max(1, qty // 2), lot_no))
    return out
