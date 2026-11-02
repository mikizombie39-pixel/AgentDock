"""模擬の条件（持ち主・品目・売り先・注文・決まり・水揚げ）。

登場する組織と、取引の条件・値はすべて模擬のもの。
デモの日の朝（DEMO_DATE）を基準にする。
"""
DEMO_DATE = "2026-11-02"
HISTORY_FROM = "2025-10-01"
HISTORY_TO = "2026-11-01"   # デモの日の前日まで

ITEMS = [
    # code, name, unit, unit_weight_g
    ("buri_xl", "ブリ（特大）", "fish", 9000),
    ("shiroebi", "シロエビ", "box", 1000),
    ("aji_s", "アジ（小）", "box", 10000),
]

OWNERS = {
    "iwase": dict(name="岩瀬漁協", role="seller", person_name="三井さん", companion_name=None),
    "amiya": dict(name="網谷水産", role="buyer", person_name="網谷さん", companion_name=None),
    "buyer_b": dict(name="B社", role="buyer", person_name="B社の社長", companion_name=None),
    "buyer_c": dict(name="C社", role="buyer", person_name="C社の社長", companion_name=None),
}

# 売り先（仲買だけ）
CUSTOMERS = {
    "amiya": [("料亭", "restaurant"), ("スーパー", "supermarket")],
    "buyer_b": [("料亭", "restaurant"), ("加工場", "processor")],
    "buyer_c": [("スーパー", "supermarket"), ("加工場", "processor")],
}

# 売値（1単位）：(item, customer, 円)
SALES_PRICES = {
    "amiya": [("buri_xl", "スーパー", 22000), ("buri_xl", "料亭", 24000),
              ("shiroebi", "スーパー", 6300), ("aji_s", "スーパー", 4500)],
    "buyer_b": [("buri_xl", "料亭", 21000), ("shiroebi", "加工場", 6200)],
    "buyer_c": [("shiroebi", "加工場", 6100), ("aji_s", "スーパー", 4200)],
}

# 費用と欲しい利益（1単位）。上限 ＝ 売値 − 費用 − 利益
COST_RULES = {
    "amiya": [("buri_xl", 2000, 1500), ("shiroebi", 900, 600), ("aji_s", 800, 700)],
    "buyer_b": [("buri_xl", 2000, 1500), ("shiroebi", 900, 600)],
    "buyer_c": [("shiroebi", 900, 600), ("aji_s", 800, 700)],
}

# 買い手の方針：安い日に注文より多めに押さえる数
BUYER_RULES = {
    "buyer_c": [("shiroebi", 2)],
}

# 売り手（漁協）の決まり：(item, 下限, 明日の見込みがこの数以上なら, 下限を上げる額)
# 本編：明日のブリの見込み 30本 → 16,500 + 500 ＝ 17,000円（「明日は多いので、今日の値は下げすぎない」）
# 不成立の場面（場面C）は hold を 2,500円にした別の種で 19,000円になる
SELLER_RULES = {
    "iwase": [("buri_xl", 16500, 20, 500)],
}
SCENE_C_HOLD_UP_YEN = 2500

# デモの日の朝の注文：(customer, item, quantity, sell_price)
ORDERS = {
    "amiya": [("スーパー", "buri_xl", 4, 22000), ("スーパー", "shiroebi", 5, 6300), ("スーパー", "aji_s", 3, 4500)],
    "buyer_b": [("料亭", "buri_xl", 2, 21000), ("加工場", "shiroebi", 6, 6200)],
    "buyer_c": [("加工場", "shiroebi", 9, 6100), ("スーパー", "aji_s", 2, 4200)],
}

# 漁協の今日の見込みの値（1単位）。アジ（小）は kg 250円 ＝ 1箱 2,500円
TODAY_EXPECTED_YEN = {"aji_s": 2500}

# 漁協が資料に添える一言（全社に同じ）
SELLER_NOTES = {
    "aji_s": "今日はアジが多く、値は直近より安めです。刺身・焼き・煮付け・揚げ物、どう使っても旨い魚です。",
    "buri_xl": "明日もブリの水揚げが多い見込みです。",
}

# デモの日の朝の入札（シロエビ 20箱）：(bidder, unit_price, quantity)。高い順に割り当てる
TODAY_TENDER = dict(lot_no="T-shiroebi-1", item="shiroebi", quantity=20,
                    bids=[("buyer_b", 4400, 6), ("buyer_c", 4350, 11), ("amiya", 4300, 5)])

# デモの日の朝のせり（人が行う）：(buyer, unit_price, quantity)。ブリ 12本のうち6本が売れ、6本が相対へ残る
# 'other' は Agent Dock を使っていない仲買。網谷水産の若いスタッフの落札は未決のため0本（HAL 案）
TODAY_AUCTION = dict(lot_no="A-buri_xl-1", item="buri_xl",
                     sales=[("buyer_b", 18200, 2), ("other", 17900, 4)])

# 入札の場面の画面に出す、網谷水産の承認済みの計画と仲間の入札（模擬の仲間の言葉・試作の画面の値を引き継いだ仮の値）
AMIYA_TENDER_PLAN = dict(max_unit_price_yen=4800, max_quantity=8)
AMIYA_TENDER_RATIONALE = "今朝の水揚げは少なめ。スーパーの注文の5箱を確実に取る"

# 漁協の水揚げ：(item, kind, quantity, size_note, caught_at, area, water_temp_c)
LANDINGS = {
    "iwase": [
        ("buri_xl", "actual", 12, "8kg 以上", "04:30", "定置網", 19.5),
        ("shiroebi", "actual", 20, "1箱 1kg", "03:00", "湾奥", 19.5),
        ("aji_s", "actual", 60, "小・1箱 10kg", "04:30", "定置網", 19.5),
        ("buri_xl", "forecast", 30, None, None, None, None),
        ("aji_s", "forecast", 60, None, None, None, None),
    ],
}
