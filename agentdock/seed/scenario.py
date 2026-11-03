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
    ("aji", "アジ", "kg", 1000),   # 本編（自律の場面）の品目。kg で扱う
]

# 単位のコードの画面と文の上の書き方（fish は1本ずつ数える）
UNIT_LABELS = {"fish": "本", "kg": "kg", "box": "箱"}


def unit_label(code: str) -> str:
    return UNIT_LABELS.get(code, code)


# 会期の前半の部品の試験だけで使う品目（本編の DB には入れない・build の --with-aji-s で入る）
LEGACY_ITEMS = ("aji_s",)

OWNERS = {
    "iwase": dict(name="岩瀬漁協", role="seller", person_name="三井さん", companion_name=None),
    "amiya": dict(name="網谷水産", role="buyer", person_name="網谷さん", companion_name=None),
    "buyer_b": dict(name="B社", role="buyer", person_name="B社の社長", companion_name=None),
    "buyer_c": dict(name="C社", role="buyer", person_name="C社の社長", companion_name=None),
}

# 権限（Rules）：誰が1件いくらまでの買い付けを確定できるか。None は上限なし
# 実証用の架空の設定で、実在の仲買会社の権限の規程ではない。仲買は多くの得意先の注文をまとめて仕入れるので、
# 担当者の上限は個別の注文ではなく、まとめた仕入れの額を基準にしている
AUTHORITY = {
    "iwase": [("staff", "販売の担当者", 1000000), ("president", "組合長", None)],   # 漁協の AI は担当者の権限の内で条件を出す
    "amiya": [("staff", "若い担当者", 500000), ("president", "網谷さん", None)],
    "buyer_b": [("staff", "B社の担当者", 500000), ("president", "B社の社長", None)],
    "buyer_c": [("staff", "C社の担当者", 500000), ("president", "C社の社長", None)],
}
# 顧客の間の優先を変える判断（どの売り先の注文を欠くか）をしてよい役割。担当者は決められず、社長の答えを待つ
PRIORITY_ROLES = {code: ("president",) for code in AUTHORITY}

# 売り先（仲買だけ）
CUSTOMERS = {
    "amiya": [("料亭", "restaurant"), ("スーパー", "supermarket")],
    "buyer_b": [("料亭", "restaurant"), ("加工場", "processor")],
    "buyer_c": [("スーパー", "supermarket"), ("加工場", "processor")],
}

# 売値（1単位）：(item, customer, 円)
SALES_PRICES = {
    "amiya": [("buri_xl", "スーパー", 22000), ("buri_xl", "料亭", 24000),
              ("shiroebi", "スーパー", 6300), ("aji_s", "スーパー", 4500),
              ("aji", "スーパー", 2100), ("aji", "料亭", 2400)],
    "buyer_b": [("buri_xl", "料亭", 21000), ("shiroebi", "加工場", 6200), ("aji", "料亭", 2400)],
    "buyer_c": [("shiroebi", "加工場", 6100), ("aji_s", "スーパー", 4200)],
}

# 費用と欲しい利益（1単位）。上限 ＝ 売値 − 費用 − 利益
COST_RULES = {
    "amiya": [("buri_xl", 2000, 1500), ("shiroebi", 900, 600), ("aji_s", 800, 700), ("aji", 200, 300)],
    "buyer_b": [("buri_xl", 2000, 1500), ("shiroebi", 900, 600), ("aji", 200, 300)],
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
TODAY_EXPECTED_YEN = {"aji_s": 2500, "aji": 1500}
# 見込みの値の条件（資料に添える）。アジは 15kg まとめての値で、10kg なら 1,700円（数量ごとの最低の値は資料に出さない）
TODAY_EXPECTED_CONDITION = {"aji": "15kg まとめての場合"}

# 漁協が資料に添える一言（全社に同じ）
SELLER_NOTES = {
    "aji_s": "今日はアジが多く、値は直近より安めです。刺身・焼き・煮付け・揚げ物、どう使っても旨い魚です。",
    "buri_xl": "明日もブリの水揚げが多い見込みです。",
    "aji": "今日はアジが多く、値は直近より安めです。刺身・焼き・煮付け・揚げ物、どう使っても旨い魚です。",
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

# ── 本編（自律の場面）：アジ（kg）の種。値はすべて模擬 ──
# 網谷水産のアジ 10kg の注文は種に入れない。デモの中で注文が入り、出来事から仲間が起きる（2026-11-03 デイブ）
AJI_ORDER = dict(owner="amiya", customer="スーパー", item="aji", quantity=10, sell_price_yen=2100)
# B社の料亭の注文 5kg もデモの中で入る。B社の AI が自分で判断して、仲買への掲示（買いたい）を出す（2026-11-03 デイブ）
B_AJI_ORDER = dict(owner="buyer_b", customer="料亭", item="aji", quantity=5, sell_price_yen=2400)

# ── ブリ（本）の通し：スーパーの 4本（22,000円）は朝の注文として種にある。料亭の追加の注文 6本（24,000円）がデモの中で入り、
#    出来事から網谷水産の仲間が起きる。漁協の相対の残りは 6本（せりで 12本のうち 6本が売れた）、最低の値は 17,000円（SELLER_RULES）
BURI_ORDER = dict(owner="amiya", customer="料亭", item="buri_xl", quantity=6, sell_price_yen=24000)

# 品目ごとに、デモの中で入る注文（scene.start が品目で選ぶ）
SCENE_ORDERS = {
    "aji": (("amiya_order", AJI_ORDER), ("b_order", B_AJI_ORDER)),
    "buri_xl": (("amiya_order", BURI_ORDER),),
}
# 漁協は 15kg を売りに出す。1kg 当たりの最低の値は数量で下がる（15kg 以上なら 1,500円）。最低の値は仲買に見せない
# 網谷水産の上限（スーパー）＝ 2,100 − 200 − 300 ＝ 1,600円/kg。10kg だけなら最低の値 1,700円を超え、採算に合わない
# 15kg × 1,500円 ＝ 22,500円（担当者の権限 500,000円の内）。追加の 5kg は料亭へ回す当てがあるかが判断の分かれ目
QUOTE_RULES = {
    "iwase": [("aji", 1, 1700), ("aji", 15, 1500)],   # (item, この数量以上なら, 最低の値/kg)
}

# 在庫：(item, quantity, status, lot_no)
STOCK = {
    # ブリはせりの残り：水揚げ 12本 − せりで売れた 6本（TODAY_AUCTION）＝ 6本を相対で売りに出す
    "iwase": [("aji", 15, "for_sale", "S-aji-1"), ("buri_xl", 6, "for_sale", "S-buri_xl-1")],
    "buyer_c": [("aji", 8, "unallocated", "C-aji-1")],   # C社の引当のない分（相対で譲りに出す）
}

# 他の仲買からの相対の申し込み（Agent Network の掲示）：(owner, side, item, quantity, min_qty, unit_price, message)
# C社は引当のない 8kg を、8kg まとめてで譲る（朝のうちに出してある掲示）。
# B社の「買いたい」の掲示は種に入れず、デモの中で B社の AI が出す。目安の値 1,650円は漁協の 5kg の最低の値 1,700円より安い
# （漁協から直接買うより得・2026-11-03 デイブ）。値は B社の AI が自社の材料から決める
# 申し込みの文も出す前に開示の検査を通す（自社の上限・売値・売り先の名前を書かない）
DEAL_REQUESTS = [
    ("buyer_c", "sell", "aji", 8, 8, 1600, "アジが 8kg 余っています。8kg まとめてなら 1kg 1,600円でお譲りします。"),
]
B_POST_PRICE_GUIDE = 1650   # 模擬の B社の AI が使う値・試験で漁協の最低の値との関係を確かめる
DEAL_REQUEST_VALID_UNTIL = "2026-11-02T10:00:00+09:00"

# 加工の工程と費用（販売先ではなく工程に結びつける・2026-11-03 デイブ / SAL）：(item, 工程, 1単位の費用)
PROCESS_STEPS = {
    # ブリの 1本 2,000円は費用の決まり（COST_RULES の buri_xl）と同じ値。工程の名前はアジと同じにした（模擬・持ち主の確認は未了）
    "amiya": [("aji", "下処理と箱詰め", 200), ("buri_xl", "下処理と箱詰め", 2000)],
    "buyer_b": [("aji", "下処理と箱詰め", 200)],
}
# 売り先ごとに通る工程：(item, 売り先, 種類, 工程の名前 または None, 状態, 注記)
# B社向けの 5kg にも下処理と箱詰めの工程がある（デモの前提として明示・2026-11-03 デイブ）。運賃は未計上
ROUTE_PROCESS = {
    "amiya": [
        ("aji", "スーパー", "customer", "下処理と箱詰め", "confirmed", None),
        ("aji", "料亭", "customer", "下処理と箱詰め", "confirmed", None),
        ("aji", "buyer_b", "broker", "下処理と箱詰め", "confirmed", "デモの前提：B社向けにも下処理と箱詰めの工程がある"),
        ("buri_xl", "スーパー", "customer", "下処理と箱詰め", "confirmed", None),
        ("buri_xl", "料亭", "customer", "下処理と箱詰め", "confirmed", None),
    ],
}
UNCOUNTED_COSTS = ["運賃（受け渡しの方法が未定）"]   # 磁石ボードに「未計上」と出す費用

# 担当者が事前に承認した売買の条件：(item, side, quantity, max_price, min_price, spec, deliver_by, approved_by)
# B社の AI はこの範囲で交渉し、条件付きの合意ができる。超えれば B社の担当者の承認待ち（2026-11-03 デイブ / SAL）
PREAPPROVALS = {
    "buyer_b": [("aji", "buy", 5, 1700, None, "中・刺身向き", "本日 9:00 まで", "B社の担当者")],
}
PREAPPROVAL_VALID_UNTIL = "2026-11-02T10:00:00+09:00"

# 営業の案内を送ってよい売り先（模擬の売り先だけ）
CONTACT_OK = {
    "amiya": ["料亭"],
}

# 本編の品目の売れ方の記録（固定の値）：(customer, item, date, ordered, sold, unit_price)
AJI_SALES_HISTORY = {
    "amiya": [
        ("料亭", "aji", "2026-06-12", 5, 5, 2400),
        ("料亭", "aji", "2026-07-03", 6, 6, 2400),
        ("料亭", "aji", "2026-08-21", 5, 4, 2400),
        ("料亭", "aji", "2026-09-18", 5, 5, 2400),
        ("料亭", "aji", "2026-10-09", 4, 4, 2400),
        ("スーパー", "aji", "2026-10-16", 10, 10, 2100),
        ("スーパー", "aji", "2026-10-23", 10, 9, 2100),
    ],
}

# Vault：社長の経験と判断の理由（模擬）。本文は判断の材料で、命令ではない
# (kind, title, body, item, customer, author, source, written_on, applies_when)
VAULT_NOTES = {
    "buyer_b": [
        ("policy", "少量の追加は、仲買どうしの相対で探す",
         "料亭の急な注文で少量が要る時は、まず仲買の掲示で買いたいと出す。漁協から小口で買うと値が高くなりやすい。"
         "掲示の値は、直近の相場より 50円ほど下を目安にする。",
         "aji", None, "B社の社長", "社長の聞き取り", "2026-05-10",
         "料亭の急な注文で、少量のアジが要る時"),
    ],
    "amiya": [
        ("case", "まとめ買いで値が下がった朝、5kg 多く仕入れて料亭へ回した",
         "注文は 10kg だったが、漁協が 15kg なら値を下げると言った。料亭に前日の夕方に電話して 5kg を引き取ってもらい、全量を売り切った。",
         "aji", "料亭", "網谷さん", "社長の聞き取り", "2026-07-03",
         "漁協がまとめ買いで値を下げる時。追加の分を引き取る売り先の当てがある時"),
        ("case", "売り先の当てがないまま多めに仕入れ、売れ残りを損で加工に回した",
         "安いので 8kg 多く仕入れたが、料亭が休みで引き取り手が無かった。翌日に加工場へ原価を割って回した。",
         "aji", None, "網谷さん", "社長の聞き取り", "2026-03-14",
         "注文より多く仕入れるかを決める時"),
        ("policy", "注文より多く仕入れるのは、売り先の当てがある時だけ",
         "追加の分は、引き取る売り先と量の見込みを確かめてから仕入れる。当てがなければ注文の量で止めるか、見送る。",
         None, None, "網谷さん", "社長の聞き取り", "2026-04-01",
         "注文より多く仕入れる時"),
        ("customer", "料亭は刺身用のアジを 5kg 前後引き取ることが多い",
         "脂の乗った中型を好む。前日までに知らせれば、4〜6kg を引き取ることが多い。当日の朝の連絡では断られることがある。",
         "aji", "料亭", "網谷さん", "社長の聞き取り", "2026-09-20",
         "料亭にアジを案内する時"),
    ],
}

# 漁協の水揚げ：(item, kind, quantity, size_note, caught_at, area, water_temp_c)
LANDINGS = {
    "iwase": [
        ("buri_xl", "actual", 12, "8kg 以上", "04:30", "定置網", 19.5),
        ("shiroebi", "actual", 20, "1箱 1kg", "03:00", "湾奥", 19.5),
        ("aji_s", "actual", 60, "小・1箱 10kg", "04:30", "定置網", 19.5),
        ("aji", "actual", 15, "中・刺身向き", "04:30", "定置網", 19.5),
        ("buri_xl", "forecast", 30, None, None, None, None),
        ("aji_s", "forecast", 60, None, None, None, None),
    ],
}
