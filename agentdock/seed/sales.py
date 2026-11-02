"""1年分の売れ方の記録（売り先ごとの注文の数と、実際に売れた数）を作る。値はすべて架空。

安い日（その日の相場が直近30日の中央値の 85% 以下）には、売り先によって注文より多く売れる。
例：網谷水産のスーパーでは、安い日のアジが注文の約2倍売れる。
"""
import random

from agentdock import criteria

SEED = 20261102

# (売り先, 品目, 1回の注文の幅, 注文がある日の割合, 安い日に売れる倍率)
PATTERNS = {
    "amiya": [
        ("スーパー", "aji_s", (2, 4), 0.9, 2.0),
        ("スーパー", "buri_xl", (2, 5), 0.9, 1.0),
        ("スーパー", "shiroebi", (3, 6), 0.9, 1.0),
        ("料亭", "buri_xl", (2, 6), 0.35, 1.0),
    ],
    "buyer_b": [
        ("料亭", "buri_xl", (1, 3), 0.5, 1.0),
        ("加工場", "shiroebi", (4, 8), 0.9, 1.0),
    ],
    "buyer_c": [
        ("加工場", "shiroebi", (6, 10), 0.9, 1.0),
        ("スーパー", "aji_s", (2, 4), 0.9, 1.4),
    ],
}


def history(owner_code: str, public_rows, sales_prices: dict):
    """[(customer, item, date, ordered, sold, unit_price)] を返す。"""
    rng = random.Random(f"{SEED}-sales-{owner_code}")
    by_item = {}
    for code, day, _k, price, _q, _n in public_rows:
        by_item.setdefault(code, []).append((day, price))
    cheap = {code: criteria.cheapness_by_day(rows) for code, rows in by_item.items()}
    out = []
    for cust, item, (lo, hi), p_order, cheap_factor in PATTERNS.get(owner_code, []):
        price = sales_prices.get((item, cust))
        if price is None:
            continue
        for day, ratio in sorted(cheap.get(item, {}).items()):
            if rng.random() > p_order:
                continue
            ordered = rng.randint(lo, hi)
            if ratio is not None and ratio <= criteria.CHEAP_RATIO:
                sold = round(ordered * cheap_factor * rng.uniform(0.85, 1.15))
            else:
                sold = round(ordered * rng.uniform(0.9, 1.1))
            out.append((cust, item, day, ordered, max(0, sold), price))
    return out
