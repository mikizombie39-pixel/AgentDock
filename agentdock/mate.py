"""仲間との対話の画面（mate.html）に渡す、ひとつの返り値（2026-11-04 デイブ・SAL）。

  GET /api/mate?owner=amiya&item=buri_xl      ← 対象の仕事・態度・実行の状態・語りかけ・人に求める行動・理由の1枚
  GET /api/morning?owner=amiya                 ← 今日の仕事の流れ（今日の記録からコードの決まりで並べる）
  POST /api/delegate                           ← 担当者が確かめて、任せる範囲を記録する

決まり：
- 態度と実行の状態を別々に持つ（SAL 2026-11-04）。
  態度＝状況への態度（強気・苦戦・慎重・残念・安堵・無ければ基本の姿）。AI の回答の確信度は使わない。業務の状態から決める
  実行の状態＝処理の状態（始める前・実行中・あなたの判断待ち・社長確認中・停止・登録済み・見張り中）
- 姿・言葉・ボタンは、この1つの返り値から描く（同じ状況を指すように）
- 語りかけの文には出所（code＝コードの決まった文／ai＝AI が書いた文・その記録の番号）を付ける
- 数字はコードが業務 DB から出す。AI の文の数字は使わない
- 態度は背景の更新1件ごとには変わらない：規則は「人への説明や進め方が変わる」業務の状態の区切りだけを見る

態度の規則（上から先に当てる。§1 の表）：
  慎重   注文や条件が変わった（AI が働き始めた後に注文が入り、人がまだその後に決めていない／人の答えで条件が変わり、AI が確かめ直している）
  残念   交渉が成立しなかった（最後に届いた返事が断りで、AI がまだ次の照会も相談もしていない）
  安堵   必要な数量と条件がそろった（足りない数が 0 で未登録の分がある／勧める案で全部の注文がそろい、担当者が決められる）
  苦戦   選択肢が少なく上限を超える（足りない数があり、当てのある相手が 1 つ以下で、届いた値が上限を超える）
  強気   別の仕入先がある（足りない数があり、当てのある相手が 2 つ以上）
"""
import datetime
import json
import re
import unicodedata

from agentdock import agents, authority, consult, criteria, db, market, results, work
from agentdock.seed import scenario as sc

STANCE_LABEL = {"bold": "強気", "struggle": "苦戦", "careful": "慎重", "sorry": "残念", "relief": "安堵"}
STANCE_RULE = {"bold": "別の仕入先がある", "struggle": "選択肢が少なく、上限を超える", "careful": "注文や条件が変わった",
               "sorry": "交渉が成立しなかった", "relief": "必要な数量と条件がそろった"}
EXEC_LABEL = {"before_start": "始める前", "running": "実行中", "your_turn": "あなたの判断待ち", "president": "社長確認中",
              "stopped": "停止", "registered": "登録済み", "idle": "見張り中"}
# 工程の帯（SAL 2026-11-04・デイブ 18:50）：品目ごとに、実際の処理に合わせた工程。処理につながっていない段は「未接続」と出す
BAND_STEPS = {"relative": ("注文の確認", "落札結果の照合", "不足分の手配", "振分け・承認", "結果の引継ぎ"),
              "tender": ("注文の確認", "入札", "落札結果の照合", "振分け", "結果の引継ぎ")}
# 今日の仕事の並べ方の「工程の順」（工程の名前を帯に合わせる前と同じ順位）
STEP_RANK = {"注文の確認": 0, "入札": 1, "落札結果の照合": 1, "不足分の手配": 1, "振分け・承認": 2, "振分け": 2, "結果の引継ぎ": 3}
STOP_OUTCOMES = ("error", "max_turns", "refusal")
PRESIDENT = "社長（網谷さん）"


class MateError(Exception):
    pass


def _t(s) -> float:
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return 0


def _short_name(name: str) -> str:
    return name.split("（")[0]


def _yen(v) -> str:
    return f"{v:,}円"


def _demo_now(on_date: str) -> str:
    return f"{on_date}T06:30:00+09:00"


# ── 業務の状態（記録からコードが集める）─────────────────────────────

def facts(owner: str, item: str, on_date: str = sc.DEMO_DATE, worker_error: str | None = None) -> dict:
    cur = work.current(owner, item, on_date)
    con = db.connect(owner)
    try:
        it = con.execute("SELECT id, code, name, unit FROM item WHERE code = ?", (item,)).fetchone()
        unit = sc.unit_label(it["unit"])
        info = {r["id"]: dict(r) for r in con.execute(
            "SELECT o.id, o.received_at, c.name AS customer, c.shop_name, o.sell_price_yen FROM order_book o JOIN customer c ON c.id = o.customer_id "
            "WHERE o.item_id = ? AND o.order_date = ?", (it["id"], on_date))}
        orders = []
        for l in cur["lines"]:
            o = info[l["order_id"]]
            try:
                ceiling = criteria.ceiling_yen(con, item, o["customer"])
            except LookupError:
                ceiling = None
            orders.append(dict(l, shop=o["shop_name"] or o["customer"], received_at=o["received_at"], ceiling_yen=ceiling,
                               sell_price_yen=o["sell_price_yen"]))
        dirs = agents.directives(con, it["id"], on_date)
        dele = con.execute("SELECT * FROM delegation WHERE trade_date = ? ORDER BY id DESC LIMIT 1", (on_date,)).fetchone()
        runs = []
        for r in con.execute("SELECT r.id, r.started_at, r.ended_at, r.outcome, r.summary, e.kind, e.payload_json FROM agent_run r "
                             "JOIN biz_event e ON e.id = r.event_id ORDER BY r.id"):
            p = json.loads(r["payload_json"])
            if agents.event_item(owner, dict(kind=r["kind"], payload=p)) == item:
                runs.append(dict(id=r["id"], started_at=r["started_at"], ended_at=r["ended_at"], outcome=r["outcome"], summary=r["summary"],
                                 kind=r["kind"], source=p.get("source")))
        any_run = con.execute("SELECT 1 FROM agent_run LIMIT 1").fetchone() is not None
        started = any_run or con.execute("SELECT 1 FROM biz_event WHERE kind = 'order_new' LIMIT 1").fetchone() is not None
        presents = [dict(r) for r in con.execute(
            "SELECT t.id, t.at, t.case_id, t.version, t.layout_json FROM workspace_turn t JOIN deal_case d ON d.id = t.case_id "
            "WHERE d.item_id = ? AND d.trade_date = ? AND t.source = 'present' ORDER BY t.id", (it["id"], on_date))]
        acts = []   # 人の判断の記録（相談の答え・承認・見送り・在庫の扱いの答え）
        for r in con.execute("SELECT h.decided_at AS at, h.decided_by AS who, 'decision' AS kind, h.id FROM human_decision h JOIN deal_case d ON d.id = h.case_id "
                             "WHERE d.item_id = ? AND d.trade_date = ?", (it["id"], on_date)):
            acts.append(dict(r))
        for r in con.execute("SELECT c.at, c.actor AS who, c.result AS kind, c.id, c.detail FROM case_decision c JOIN deal_case d ON d.id = c.case_id "
                             "WHERE d.item_id = ? AND d.trade_date = ? AND c.result NOT IN ('stale', 'not_executable')", (it["id"], on_date)):   # 通らなかった操作は判断ではない
            acts.append(dict(r))
        for r in con.execute("SELECT answered_at AS at, answered_by AS who, 'stock_answer' AS kind, id FROM stock_question "
                             "WHERE item_id = ? AND status = 'answered'", (it["id"],)):
            acts.append(dict(r))
        acts.sort(key=lambda a: (_t(a["at"]), a["id"]))
        vault = [dict(r) for r in con.execute("SELECT id, title, author, written_on, applies_when FROM vault_note WHERE item_id = ? OR item_id IS NULL ORDER BY id",
                                              (it["id"],))]
        auth = {r["role"]: dict(r) for r in con.execute("SELECT role, person_name, max_trade_yen, may_change_priority FROM authority")}
    finally:
        con.close()
    m = db.connect(db.MARKET)
    try:
        msgs = [dict(r) for r in m.execute("SELECT * FROM network_message WHERE (from_owner = ? OR to_owner = ?) AND item_code = ? AND trade_date = ? ORDER BY id",
                                           (owner, owner, item, on_date))]
        for x in msgs:
            x["terms"] = [dict(t) for t in m.execute("SELECT id, quantity, unit_price_yen, valid_until, status, hold_ref FROM offer_term WHERE message_id = ? ORDER BY id",
                                                     (x["id"],))]
    finally:
        m.close()
    posts = [p for p in market.requests_visible_to(owner, item, _demo_now(on_date)) if p["side"] == "sell"]
    holds = work._live_holds(owner, item, on_date)
    return dict(owner=owner, on_date=on_date, item=dict(code=it["code"], name=it["name"], short=_short_name(it["name"]), unit=unit),
                cur=cur, orders=orders, directives=dirs, delegation=dict(dele) if dele else None, runs=runs, started=started, presents=presents,
                acts=acts, messages=msgs, posts=posts, holds=holds, vault=vault, authority=auth, worker_error=worker_error,
                clock=clock(owner, on_date))


def _suppliers(f) -> dict:
    """当てのある相手（コード → 名前）：期限の内の条件をくれた相手・返事待ちの照会先・譲りたいの掲示の主。"""
    out = {}
    for h in f["holds"]:
        out[h["seller"]] = h["seller_name"]
    replied = {x["in_reply_to"] for x in f["messages"] if x["in_reply_to"]}
    for x in f["messages"]:
        if x["from_owner"] == f["owner"] and x["kind"] == "inquiry" and x["id"] not in replied:
            out[x["to_owner"]] = sc.OWNERS[x["to_owner"]]["name"]
    for p in f["posts"]:
        out[p["owner"]] = sc.OWNERS[p["owner"]]["name"]
    return out


def _running(f):
    """この品目で今動いている AI の起動か、順番を待つ出来事（無ければ None）。"""
    on = [r for r in f["runs"] if not r["ended_at"]]
    if on:
        return dict(kind=on[-1]["kind"], source=on[-1]["source"], queued=False)
    q = f["cur"].get("ai_queue") or []
    if q:
        return dict(kind=q[0]["kind"], source=q[0]["source"], queued=True)
    return None


def _first_run_at(f):
    return f["runs"][0]["started_at"] if f["runs"] else None


def _mid_orders(f) -> list[dict]:
    """AI が働き始めた後に入った注文で、まだ足りず、その後に人が決めていないもの。"""
    start = _first_run_at(f)
    if start is None:
        return []
    out = []
    for o in f["orders"]:
        if o["short"] <= 0 or _t(o["received_at"]) <= _t(start):
            continue
        if any(int(_t(a["at"])) >= int(_t(o["received_at"])) for a in f["acts"]):   # 人の判断の記録は秒まで
            continue
        out.append(o)
    return out


def _last_decline(f):
    """最後に届いた返事が断りで、その後に AI が照会も相談もしていなければ、その返事。"""
    incoming = [x for x in f["messages"] if x["to_owner"] == f["owner"] and x["kind"] in ("offer", "decline")]
    if not incoming or incoming[-1]["kind"] != "decline":
        return None
    d = incoming[-1]
    later = [x for x in f["messages"] if x["from_owner"] == f["owner"] and x["kind"] == "inquiry" and x["id"] > d["id"]]
    presented = [p for p in f["presents"] if _t(p["at"]) > _t(d["created_at"])]
    drafted = [x for x in f["cur"].get("drafts") or [] if _t(x["created_at"]) > _t(d["created_at"])]   # 売り先への連絡の下書きも、次の手
    return None if later or presented or drafted else d


def _round_declines(f):
    """人の最後の判断の後に届いた断り（この回の相談の前の、相手の返事）。人の判断がまだ無ければ空。"""
    if not f["acts"]:
        return []
    since = _t(f["acts"][-1]["at"])
    return [x for x in f["messages"] if x["to_owner"] == f["owner"] and x["kind"] == "decline" and _t(x["created_at"]) >= since]


def _round_story(f, tl) -> tuple[str, str | None]:
    """相手の返事を受けて、AI が次の手を用意して戻ってきた時の語りかけ（SAL 2026-11-04）。数と名前はすべて記録とコードの計算から。
    (見出し＝何が起きたか, 次の手＝勧める案と残りの不足)"""
    u = f["item"]["unit"]
    extra = "追加分を" if tl["registered"] else ""
    parts = [f"{sc.OWNERS[x['from_owner']]['name']}では{extra}確保できませんでした。" for x in _round_declines(f)]
    if tl["held"]:
        parts.append("・".join(f"{h['name']}からは {h['quantity']}{u}" for h in tl["held"]) + "を仮押さえできています。")
    rec = next((o for o in tl["options"] if o["option_id"] == next((x["option_id"] for x in f["cur"].get("options") or [] if x["recommended"]), None)), None)
    nxt = None
    if rec:
        title, _, _ = _choice_words(f, rec, "approve" if rec["buys"] else "reject", False)
        left = sum(x["quantity"] for x in rec["short_after"])
        nxt = f"{title}案ですが、残り {left}{u}は不足します。" if left else f"{title}案で、全部の注文がそろいます。"
    return "".join(parts), nxt


def _return_ctx(f) -> dict | None:
    """最後の人の判断が社長の差し戻し（理由つき）なら、その中身。redone＝AI が差し戻しの後に案を出し直した（2026-11-04 デイブ・SAL）。"""
    if not f["acts"] or f["acts"][-1]["kind"] != "returned":
        return None
    a = f["acts"][-1]
    d = json.loads(a.get("detail") or "{}")
    return dict(id=a["id"], at=a["at"], label=d.get("label") or "元の", reason=d.get("reason") or "",
                redone=any(_t(p["at"]) >= _t(a["at"]) for p in f["presents"]))


def _holds_line(f, tl) -> str:
    u = f["item"]["unit"]
    if tl["held"]:
        return "仮押さえ（" + "・".join(f"{h['name']} {h['quantity']}{u}" for h in tl["held"]) + "）は、見直した案で使えるよう残しています。"
    return "仮押さえは残っていません（期限の切れたものは解放済みです）。"


def _short_total(f) -> int:
    return sum(o["short"] for o in f["orders"] if o["state"] != "secured")


def option_needs_president(owner: str, case_id: int, option_id: int) -> dict:
    """この案を担当者が承認すると社長の確認が要るか（承認の時と同じ判定：権限の額・顧客の間の優先）。表示の時の見込み。"""
    con = db.connect(owner)
    try:
        case = dict(con.execute("SELECT * FROM deal_case WHERE id = ?", (case_id,)).fetchone())
        o = con.execute("SELECT numbers_json FROM board_option WHERE id = ?", (option_id,)).fetchone()
        n = json.loads(o["numbers_json"])
        pu = n["purchase"]
        if not pu:
            return dict(needs=False, priority_change=False, over_limit=False)
        allocs = [dict(order_id=l["ref_id"] if l["kind"] == "order" else None, quantity=l["quantity"]) for l in n["lines"]]
        priority = consult.changes_priority(con, case, dict(quantity=pu["quantity"], allocations=allocs))
        # 権限は相手ごと（同じ相手・同じ品目・同じ取引日の合計）。どれか1つでも上を超えれば社長の確認（承認の時と同じ）
        rows = n.get("purchases") or [pu]
        checks = [authority.check(con, "staff", r["seller"], case["item_id"], case["trade_date"], r["amount_yen"]) for r in rows]
        a = next((x for x in checks if not x["allowed"]), checks[0])
        decision = consult.latest_decision(con, case_id)
        settled = decision is not None and authority.may_change_priority(con, decision["decided_by"]) and not consult.against_decision(
            con, decision, json.loads(con.execute("SELECT plan_json FROM board_option WHERE id = ?", (option_id,)).fetchone()[0])["allocations"])
        settled = settled or consult.directive_settles(con, case, dict(quantity=pu["quantity"], allocations=allocs))
    finally:
        con.close()
    priority = priority and not settled and not authority_staff_may_prioritize(owner)
    return dict(needs=priority or not a["allowed"], priority_change=priority, over_limit=not a["allowed"], amount_yen=pu["amount_yen"], limit_yen=a["limit_yen"])


def authority_staff_may_prioritize(owner: str) -> bool:
    con = db.connect(owner)
    try:
        return authority.may_change_priority(con, "staff")
    finally:
        con.close()


# ── 態度と実行の状態 ─────────────────────────────────────────────

def stance(f) -> tuple[str | None, str | None, dict]:
    """(態度, 当てた規則, 根拠の参照)。態度が無ければ (None, None, {})（基本の姿）。"""
    cur = f["cur"]
    short = _short_total(f)
    run = _running(f)
    if cur.get("waiting_for") == "president":
        return None, None, {}
    mid = _mid_orders(f)
    # 慎重の説明は、いまの理由を言う（SAL 2026-11-04：一律に「注文や条件が変わった」としない）
    if _return_ctx(f):
        return "careful", "社長が見直しを求めた", dict(returned=_return_ctx(f)["id"])
    if mid:
        return "careful", "追加の注文が入った", dict(orders=[o["order_id"] for o in mid])
    if run and run["kind"] == "human_choice" and run["source"] == "consultation":
        return "careful", "あなたの答えで条件が変わった", dict(changed_by="consultation")
    d = _last_decline(f)
    if d and short > 0:
        return "sorry", STANCE_RULE["sorry"], dict(message=d["id"])
    dec = _round_declines(f)
    if dec and short > 0 and cur.get("waiting_for") == "staff":
        rec = next((o for o in cur.get("options") or [] if o["recommended"]), None)
        if not rec or any(a["short_after"] > 0 for a in rec["after"]):
            return "careful", ("追加分が不足している" if any(o["registered"] for o in f["orders"]) else "注文の分が不足している"), dict(declined=[x["id"] for x in dec])
    if f["orders"] and short == 0 and any(o["registered"] < o["quantity"] for o in f["orders"]):
        return "relief", STANCE_RULE["relief"], dict(orders=[o["order_id"] for o in f["orders"]])
    if cur.get("waiting_for") == "staff" and short > 0:
        rec = next((o for o in cur.get("options") or [] if o["recommended"]), None)
        if rec and rec["executable"] and rec["action"] == "approve" and all(a["short_after"] == 0 for a in rec["after"]) \
                and not option_needs_president(f["owner"], cur["case"]["id"], rec["option_id"])["needs"]:
            return "relief", STANCE_RULE["relief"], dict(option=rec["option_id"])
    if short > 0:
        sup = _suppliers(f)
        ceil = max([o["ceiling_yen"] for o in f["orders"] if o["short"] > 0 and o["ceiling_yen"]] or [0])
        offered = [t["unit_price_yen"] for x in f["messages"] if x["to_owner"] == f["owner"] and x["kind"] == "offer"
                   for t in x["terms"] if t["status"] == "offered"]
        over = bool(offered) and ceil and min(offered) > ceil
        opts = [o for o in cur.get("options") or [] if o["action"] == "approve"]
        if opts and not any(o["executable"] for o in opts) and any("上限" in e for o in opts for e in o["numbers"]["errors"]):
            over = True
        if over and len(sup) <= 1:
            return "struggle", STANCE_RULE["struggle"], dict(price=min(offered) if offered else None, ceiling=ceil)
        if len(sup) >= 2:
            return "bold", STANCE_RULE["bold"], dict(suppliers=sorted(sup))
    return None, None, {}


def exec_state(f) -> tuple[str, str | None]:
    """(実行の状態, 停止の理由)。"""
    cur = f["cur"]
    if not f["started"] and f["delegation"] is None:
        return "before_start", None
    if f["worker_error"]:
        return "stopped", f["worker_error"]
    last = f["runs"][-1] if f["runs"] else None
    if last and last["ended_at"] and last["outcome"] in STOP_OUTCOMES and not _running(f):
        return "stopped", {"error": "AI の処理が失敗しました", "max_turns": "AI の呼び出しの上限に達しました",
                           "refusal": "AI が処理を断りました"}[last["outcome"]] + (f"：{last['summary']}" if last["summary"] else "")
    if cur.get("waiting_for") == "president":
        return "president", None
    if cur.get("waiting_for") == "staff" or cur.get("stock_question") or any(d["status"] == "draft" for d in cur.get("drafts") or []):
        return "your_turn", None
    if _running(f):
        return "running", None
    replied = {x["in_reply_to"] for x in f["messages"] if x["in_reply_to"]}
    if any(x["from_owner"] == f["owner"] and x["kind"] == "inquiry" and x["id"] not in replied for x in f["messages"]):
        return "running", None
    if f["orders"] and all(o["registered"] >= o["quantity"] for o in f["orders"]):
        return "registered", None
    return "idle", None


# ── 語りかけ・行動・理由（ひとつの返り値）──────────────────────────────

def _said(text, origin="code", ref=None):
    return dict(text=text, origin=origin, ref=ref)


def _orders_phrase(f, orders=None) -> str:
    u = f["item"]["unit"]
    return "・".join(f"{o['shop']} {o['quantity']}{u}" for o in (orders if orders is not None else f["orders"]))


# AI の文の数の照合（2026-11-04 デイブ・案イ）：AI の文に出てくる数を、同じ場面のコードの値と比べる。
# 全部合えば AI の文を出し（出所「AI が書いた文（照合済み）」）、1つでも合わない・照合できない数があればコードの文に替える。
#   ・全角の数字と記号は NFKC で半角にしてから読む。桁の区切りの「,」は外す
#   ・「HH:MM」は時刻：記録の時刻（条件の期限・注文と指示の時刻・模擬の時計で見せる時刻）と比べる
#   ・数の後の単位で比べる先を決める：品目の単位（本・箱・kg）＝数量／「円」＝値と金額。単位の無い数・ほかの単位の数・品目と違う単位は照合できない
#   ・数量の直後（5字以内）に「足りない」「足りません」が続く数は、足りない数（あと何本・案ごとの足りない数・注文ごとの足りない数）とだけ比べる
#   ・「1本 17,500円」の「1本」は単価の言い方として読み飛ばす。「案1」は画面の案の続き番号と比べる
NUM = re.compile(r"(\d{1,2}):(\d{2})|(\d[\d,]*)\s*(本|箱|kg|円)?")
UNITS_QTY = ("本", "箱", "kg")


def _allowed(f, tl) -> dict:
    """この場面でコードが持っている値（数量・足りない数・値と金額・時刻）。"""
    qty, short, yen, times = set(), set(), set(), set()
    for k in ("total", "registered", "held_total", "left"):
        qty.add(tl[k])
    short.add(tl["left"])
    for o in f["orders"]:
        qty.update((o["quantity"], o["registered"], o["secured"], o["short"]))
        short.add(o["short"])
        yen.update(x for x in (o["ceiling_yen"], o["sell_price_yen"]) if x)
        times.add(o["received_at"][11:16])
    for h in tl["held"] + tl["reply_only"]:
        qty.add(h["quantity"])
        yen.add(h["unit_price_yen"])
    for x in f["messages"]:
        for t in x["terms"]:
            qty.add(t["quantity"])
            yen.add(t["unit_price_yen"])
            if t["valid_until"]:
                times.add(t["valid_until"][11:16])
    for o in f["cur"].get("options") or []:
        n = o["numbers"]
        if n["purchase"]:
            qty.add(n["purchase"]["quantity"])
            yen.update(x for x in (n["purchase"]["unit_price_yen"], n["purchase_yen"]) if x)
        for r in n.get("rows") or []:
            qty.add(r["quantity"])
            yen.update((r["unit_price_yen"], r["amount_yen"]))
        qty.update(l["quantity"] for l in n["lines"])
        for a in o["after"]:
            qty.update((a["short_after"], a["secured_after"]))
            short.add(a["short_after"])
    for s in f["cur"].get("stock") or []:
        qty.add(s["quantity"])
    if f["cur"].get("stock_question"):
        qty.add(f["cur"]["stock_question"]["quantity"])
    for d in f["directives"]:
        times.add(d["received_at"][11:16])
    times.add(f["clock"]["now_shown"])
    return dict(qty=qty, short=short, yen=yen, time=times, unit=f["item"]["unit"], option_no={o["no"] for o in tl["options"]})


def check_numbers(text: str, allowed: dict) -> list[str]:
    """AI の文の数のうち、照合できない・合わない数の理由（空なら全部合った）。"""
    t = unicodedata.normalize("NFKC", text)
    out = []
    for m in NUM.finditer(t):
        if m.group(1):
            hhmm = f"{int(m.group(1)):02d}:{m.group(2)}"
            if hhmm not in allowed["time"]:
                out.append(f"時刻 {hhmm} は記録にありません")
            continue
        v, unit = int(m.group(3).replace(",", "")), m.group(4)
        if m.start() > 0 and t[m.start() - 1] == "案" and unit is None:   # 「案1」は画面の案の続き番号
            if v not in allowed["option_no"]:
                out.append(f"案{v} は画面の案にありません")
            continue
        if v == 1 and unit == allowed["unit"] and re.match(r"\s*(あたり)?\s*[\d,]+\s*円", t[m.end():]):
            continue   # 「1本 17,500円」の「1本」は単価の言い方（数量ではない）
        if unit is None:
            out.append(f"数 {v} は単位が無く、照合できません")
        elif unit == "円":
            if v not in allowed["yen"]:
                out.append(f"{v:,}円 はコードの値にありません")
        elif unit != allowed["unit"]:
            out.append(f"{v}{unit} は品目の単位（{allowed['unit']}）と違い、照合できません")
        elif re.search(r"足り(な|ま)", t[m.end():m.end() + 5]):
            if v not in allowed["short"]:
                out.append(f"足りない数 {v}{unit} はコードの計算（{'・'.join(str(x) for x in sorted(allowed['short']) if x)}{unit}）と合いません")
        elif v not in allowed["qty"]:
            out.append(f"{v}{unit} はコードの数量にありません")
    return out


def _ai_text(f, tl, text, ref):
    """AI の文を照合して出す。数が無ければ AI の文、全部合えば「照合済み」、合わなければ (None, 使わなかった文と理由)。"""
    if not text:
        return None, None
    if not re.search(r"[0-9０-９]", text):
        return _said(text, "ai", ref), None
    bad = check_numbers(text, _allowed(f, tl))
    if bad:
        return None, dict(text=text, ref=ref, reason="AI の文の数を照合し、合わない数があったのでコードの文に替えました：" + "／".join(bad))
    return _said(text, "ai_checked", ref), None


def _ai_question(f, tl):
    """今の版の AI の短い問い（AI が書いた文・記録の番号つき）を、照合して返す。(語りかけ, 使わなかった時の記録)。"""
    cur = f["cur"]
    if cur.get("waiting_for") != "staff" or not f["presents"]:
        return None, None
    t = f["presents"][-1]
    lay = json.loads(t["layout_json"] or "{}")
    return _ai_text(f, tl, lay.get("short_question") or lay.get("ask_human"), dict(db="owner", table="workspace_turn", id=t["id"]))


# ── 模擬の時計（2026-11-04 デイブ・案ア）：記録の時刻は実際の時計のまま DB に残し、画面に出す時だけ実演の朝の時計に写す ──
#   見せる時刻 ＝ 実演の朝の開始（scenario.DEMO_CLOCK_START）＋（記録の時刻 − 業務の開始の実際の時刻）
#   業務の開始の実際の時刻＝委任の記録の時刻（無ければ、実演の日でない最初の出来事の時刻・その次に最初の AI の起動の時刻）
#   実演の日の日付で書かれた記録（朝の注文 05:00・社長の指示 05:40・条件の期限 07:00 など）は、もともと実演の時計なのでそのまま出す

def clock(owner: str, on_date: str = sc.DEMO_DATE) -> dict:
    con = db.connect(owner)
    try:
        r = con.execute("SELECT granted_at FROM delegation WHERE trade_date = ? ORDER BY id LIMIT 1", (on_date,)).fetchone()
        anchor = r[0] if r else None
        if anchor is None:
            r = con.execute("SELECT min(at) FROM biz_event WHERE at NOT LIKE ?", (on_date + "%",)).fetchone()
            anchor = r[0]
        if anchor is None:
            r = con.execute("SELECT min(started_at) FROM agent_run").fetchone()
            anchor = r[0]
    finally:
        con.close()
    start = f"{on_date}T{sc.DEMO_CLOCK_START}:00+09:00"
    now = db.now_iso()
    out = dict(anchor=anchor, start=start, on_date=on_date)
    out["now_shown"] = shown_time(now, out) if anchor else sc.DEMO_CLOCK_START
    out["now_real"] = now
    out["note"] = (f"模擬の時計です。実際に記録した時刻とは違います。業務の開始（委任）を朝 {sc.DEMO_CLOCK_START} に合わせ、"
                   "そこから経過した分だけ進めています。")
    return out


def shown_time(ts: str | None, ck: dict) -> str | None:
    """記録の時刻を、画面に出す模擬の時計の「HH:MM」に写す。"""
    if not ts:
        return None
    if ts.startswith(ck["on_date"]) or not ck["anchor"]:
        return ts[11:16]
    d = datetime.datetime.fromisoformat(ck["start"]) + (datetime.datetime.fromisoformat(ts) - datetime.datetime.fromisoformat(ck["anchor"]))
    return d.strftime("%H:%M")


def tally(f) -> dict:
    """画面に出す数の元。見出し・下の行・案のボタン・理由の1枚は、全部これから出す（同じ計算）。
    確保済み＝登録した在庫の引当／仮押さえ＝相手が期限つきで押さえた条件（返事ごとに1件・どの注文に回すかは人の決定まで未定）／
    返事だけ＝条件の返事はあるが押さえの無い条件"""
    u = f["item"]["unit"]
    total = sum(o["quantity"] for o in f["orders"])
    registered = sum(o["registered"] for o in f["orders"])
    held = [dict(seller=h["seller"], name=h["seller_name"], quantity=h["quantity"], unit_price_yen=h["unit_price_yen"], term_id=h["term_id"])
            for h in f["holds"]]
    held_ids = {h["term_id"] for h in held}
    held_msgs = {x["id"] for x in f["messages"] for t in x["terms"] if t["id"] in held_ids}
    reply_only = [dict(name=sc.OWNERS[x["from_owner"]]["name"], quantity=t["quantity"], unit_price_yen=t["unit_price_yen"])
                  for x in f["messages"] if x["to_owner"] == f["owner"] and x["kind"] == "offer" and x["id"] not in held_msgs
                  for t in x["terms"] if t["status"] == "offered" and not t["hold_ref"]]
    held_total = sum(h["quantity"] for h in held)
    left = max(0, total - registered - held_total)
    shops = {o["customer"]: o["shop"] for o in f["orders"]}
    options = []
    ids = [o["option_id"] for o in f["cur"].get("options") or []]
    con = db.connect(f["owner"])
    try:   # 案が仕入れる条件の番号（案の計画の記録から）
        plans = [json.loads(r[0]) for r in con.execute(f"SELECT plan_json FROM board_option WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    finally:
        con.close()
    used_terms = {t for pl in plans for t in (pl.get("buy_term_ids") or [pl.get("buy_term_id")])}
    # どの案にも入っていない仮押さえ（AI が組み合わせの案を出さなかった時。コードは案を足さず、理由の1枚と AI への返事に記録する）
    unused = [h for h in held if h["term_id"] not in used_terms] if f["cur"].get("options") else []
    for no, o in enumerate(f["cur"].get("options") or [], 1):
        n = o["numbers"]
        to = "・".join(f"{shops.get(l['to'], l['to'])} {l['quantity']}{u}" for l in n["lines"])
        if n.get("rows"):   # 2つ以上の相手の案：相手ごとの行を「＋」でつなぐ
            label = "＋".join(f"{r['seller']} {r['quantity']}{u}" for r in n["rows"]) + "：" + to
        elif n["purchase"]:
            p = n["purchase"]
            label = f"{p['seller']} {p['quantity']}{u}：" + to
        else:
            label = "見送る（仕入れない）"
        short_after = []   # 同じ店の注文が2つ以上ある時は、店ごとに合計する（「順風満帆 2本・順風満帆 2本」と並べない）
        for a in o["after"]:
            if a["short_after"] > 0:
                shop = shops.get(a["customer"], a["customer"])
                hit = next((x for x in short_after if x["shop"] == shop), None)
                if hit:
                    hit["quantity"] += a["short_after"]
                else:
                    short_after.append(dict(shop=shop, quantity=a["short_after"]))
        alloc = []   # 店ごとに：残りの注文と、この案で確保できる数（下見に出す・コードの計算）
        for a in o["after"]:
            shop = shops.get(a["customer"], a["customer"])
            hit = next((x for x in alloc if x["shop"] == shop), None)
            if not hit:
                hit = dict(shop=shop, ordered=0, secured=0, short=0)
                alloc.append(hit)
            hit["ordered"] += a["quantity"]
            hit["secured"] += a["secured_after"]
            hit["short"] += a["short_after"]
        buys = [dict(seller=r["seller"], quantity=r["quantity"], unit_price_yen=r["unit_price_yen"], amount_yen=r["amount_yen"]) for r in n.get("rows") or []] or \
            ([dict(seller=n["purchase"]["seller"], quantity=n["purchase"]["quantity"], unit_price_yen=n["purchase"]["unit_price_yen"],
                   amount_yen=n["purchase"]["quantity"] * n["purchase"]["unit_price_yen"])] if n["purchase"] else [])
        options.append(dict(option_id=o["option_id"], code=o["code"], no=no, label=label, short_after=short_after, rows=n.get("rows") or [],
                            alloc=alloc, buys=buys, purchase_yen=n["purchase_yen"],
                            note=("この案では足りない：" + "・".join(f"{s['shop']} {s['quantity']}{u}" for s in short_after)) if short_after else "全部の注文がそろう"))
    held_text = "・".join(f"{h['name']} {h['quantity']}{u}" for h in held)
    reg_to = "・".join(f"{o['shop']} {o['registered']}{u}" for o in f["orders"] if o["registered"])
    have = "・".join(x for x in ((f"{registered}{u}を登録済み（{reg_to}）" if registered else ""),
                                  (f"{held_total}{u}を仮押さえ" + (f"（{held_text}）" if held else "") if held_total else "")) if x)
    line = f"{total}{u}の注文に対し、" + (have if have else "まだ確保はありません") + "。" + (f"あと {left}{u}不足。" if left else "数はそろっています。")
    buy_opts = [o for o in options if o["buys"]]
    if left and len(buy_opts) >= 2:   # 案との関係：全部の案の不足（コードの計算）から言う。買う案が1つなら、その案の語りかけが言う
        line += ("どの案でも、どこかの店が欠けます。" if all(o["short_after"] for o in buy_opts) else "全部そろう案もあります。")
    told = bool(f["presents"]) and bool(json.loads(f["presents"][-1]["layout_json"] or "{}").get("all_holds_missing"))
    unused_text = ("・".join(f"{h['name']}の {h['quantity']}{u}" for h in unused) + "（仮押さえ）は、どの案にも入っていません。" +
                   ("仮押さえを全部使う案が無いことを、AI に伝えました。" if told else "")) if unused else None
    return dict(total=total, registered=registered, held=held, held_total=held_total, reply_only=reply_only, left=left,
                options=options, line=line, unit=u, unused_holds=unused, unused_text=unused_text)


def _counterparts(f) -> list[str]:
    names = []
    for x in f["messages"]:
        o = x["to_owner"] if x["from_owner"] == f["owner"] else x["from_owner"]
        n = sc.OWNERS[o]["name"]
        if n not in names:
            names.append(n)
    return names


def subject(f) -> str:
    who = _counterparts(f)
    base = f"{f['item']['short']}の買い付け"
    return base + (f" · {'・'.join(who)}との交渉" if who else "")


def view(owner: str, item: str, on_date: str = sc.DEMO_DATE, worker_error: str | None = None) -> dict:
    f = facts(owner, item, on_date, worker_error)
    st, rule, refs = stance(f)
    ex, stop = exec_state(f)
    u, name = f["item"]["unit"], f["item"]["short"]
    cur = f["cur"]
    short = _short_total(f)
    head, sub, ask, actions = None, [], None, []
    mid = _mid_orders(f)
    sup = _suppliers(f)
    tl = tally(f)
    aiq, replaced = _ai_question(f, tl)
    part = partial(f)

    if ex == "before_start":
        vol = order_volume(owner, on_date)
        head = _said("おはようございます。" + (f"今日は{'と'.join(vol['more'])}の注文が、ふだんより多めです。" if vol["more"] else "今朝の注文が届いています。"))
        can = ai_can_proceed(owner, on_date)
        sub = [_said(_volume_words(vol)),
               _said("まず注文と社長の指示を確認しましょう。" + (f"その後、{'と'.join(can)}は私が進めます。" if can else "") + "配分と購入の判断は、必要な時に相談します。")]
        actions = [dict(id="check", label="一緒に確認する", kind="primary")]
        ask = _said("今朝の注文と社長の指示を、一緒に確かめます。")
    elif ex == "stopped":
        head = _said("止まっています。")
        sub = [_said(f"理由：{stop}")]
        ask = _said("止まった理由を見て、再開してよいかを決めてください。")
        actions = [dict(id="resume", label="再開する", kind="primary")]
    elif ex == "president":
        p = cur.get("pending") or {}
        why = [x for x in ((f"{_directive_shop(f) or 'ほかの売り先'}の注文を欠く案です" if p.get("priority_change") else ""),
                           (f"担当者の上限 {_yen(p['limit_yen'])}を超えます" if p.get("over_limit") and p.get("limit_yen") else "")) if x]
        head = _said(f"{PRESIDENT}に確認しています。")
        sub = [_said("・".join(why) + "。" if why else "社長の判断が要る案です。"), _said("答えが来たら続けます。待つ間、ほかの仕事を進めてかまいません。")]
        ask = _said("今は、あなたが答える仕事ではありません。")
    elif ex in ("running", "idle", "your_turn") and _return_ctx(f):   # 社長の差し戻し：理由を伝え、AI が案を組み直す（取引の取り消しとは別）
        rc = _return_ctx(f)
        ref = dict(db="owner", table="case_decision", id=rc["id"])
        if rc["redone"] and ex == "your_turn":
            head = _said("社長から、この点を見直すよう返事がありました。案を組み直しました。")
            sub = [_said(f"社長の理由：「{rc['reason']}」", "code", ref), _said(tl["line"], "calc"), _said(_holds_line(f, tl), "calc")]
        else:
            head = _said("社長から、この点を見直すよう返事がありました。")
            sub = [_said(f"社長の理由：「{rc['reason']}」", "code", ref),
                   _said(f"元の案「{rc['label']}」は承認待ちを解除しました。この案のままでは登録できません。"),
                   _said(_holds_line(f, tl), "calc")]
            if tl["registered"]:
                sub.append(_said(f"登録済みの {tl['registered']}{f['item']['unit']}はそのままです。", "calc"))
            sub.append(_said("社長の理由に沿って、案を組み直しています。"))
    elif ex == "idle" and tl["left"] and _closed_draft(f):   # 下書きに答えた後：不足は残ったまま、AI は見張りに戻る（「別の浜を当たる」と言わない・SAL 2026-11-04 通しの確認）
        cd = _closed_draft(f)
        shop = sc.shop(owner, cd["customer"])
        head = _said(f"{shop}に伝える文を決めました。" if cd["status"] == "approved" else f"{shop}への連絡の下書きは、使わないことにしました。")
        sub = [_said(tl["line"], "calc")]
        if cd["status"] == "approved":
            sub.append(_said("連絡はあなたから送ってください。Agent Dock は売り先に送りません。"))
        sub.append(_said("新しい返事や注文が届いたら、声をかけます。"))
    elif ex in ("running", "idle") and part:   # 一部登録の後（AI が次の相談を出すまで）：何が済んで何が残ったか（態度の文より先）
        head = _said(part["done"])
        sub = [_said(part["rest"])] if part["rest"] else []
        sub.append(_said("あなたは魚を見てきてください。判断が必要になったら声をかけます。"))
    elif st == "careful" and refs.get("declined") and ex == "your_turn":   # 相手の返事を受け、次の手を用意して戻ってきた
        story, nxt = _round_story(f, tl)
        head = _said(story, "calc")
        sub = ([_said(nxt, "calc")] if nxt else []) + [_said(tl["line"], "calc")]   # 問いは判断のブロックが出す（AI の問いの文は重ねない）
    elif st == "careful":
        if ex == "your_turn":   # 相談の見出しはコードの文。数字を含まない AI の文だけを使う
            head = aiq or _said(f"{'・'.join(o['shop'] for o in mid)}の追加注文で、魚が足りません。どう分けますか" if mid else "条件が変わりました。どう進めますか")
        else:
            head = _said("待ってください。確認し直します。")
        if mid:
            sub.append(_said("・".join(f"{o['shop']}から追加の注文 {o['quantity']}{u}" for o in mid) + "が入りました。"))
            sub.append(_said(tl["line"], "calc"))
            if ex == "your_turn" and tl["unused_text"]:
                sub.append(_said(tl["unused_text"], "calc"))
        elif refs.get("changed_by"):
            sub.append(_said("あなたの答えで条件が変わったので、相手に問い合わせ直します。"))
    elif st == "sorry":
        d = next(x for x in f["messages"] if x["id"] == refs["message"])
        dr = next((x for x in cur.get("drafts") or [] if x["status"] == "draft"), None) if ex == "your_turn" else None
        if dr:   # 断りの後に AI が売り先への連絡の下書きを出した：見出しは「別の浜を当たる」でなく、いま人に求めること（SAL 2026-11-04 通しの確認）
            head = _said(f"{sc.shop(owner, dr['customer'])}に、足りない見込みを伝えますか。")
            sub = [_said(f"{sc.OWNERS[d['from_owner']]['name']}から、断りの返事が届きました。"), _said(tl["line"], "calc"),
                   _said("下書きは AI が作りました。伝えるかどうかは、あなたが決めます。")]
        else:
            head = _said("難しかったです。別の浜を当たります。")
            sub = [_said(f"{sc.OWNERS[d['from_owner']]['name']}から、断りの返事が届きました。")]
        if d.get("reason"):
            sub.append(_said(f"相手の理由：{d['reason']}", "reply", dict(db="market", table="network_message", id=d["id"])))
    elif st == "relief":
        head = _said("そろいました。最後の確認をお願いします。")
        sub = [_said(f"{_orders_phrase(f)}の分がそろいます。")]
    elif st == "struggle":
        head = _said("いまの条件では、少し厳しいです。")
        sh = next((o for o in f["orders"] if o["short"] > 0), None)
        if refs.get("price") and sh:
            sub.append(_said(f"届いた値 {_yen(refs['price'])}は、{sh['shop']}向けの上限 {_yen(refs['ceiling'])}を超えます。"))
        sub.append(_said("代わりの魚も探しましょう。"))
    elif st == "bold":
        head = _said("この条件で押してみます。")
        sub = [_said(f"{'と'.join(sup.values())}に当たれます。希望の値で照会しています。")]
    elif ex == "registered":
        head = _said(f"{name}はそろいました。")
        sub = [_said(f"{_orders_phrase(f)}を登録しました。")]
    elif ex == "your_turn" and not cur.get("waiting_for") and not cur.get("stock_question"):   # 売り先への連絡の下書き
        d = next(d for d in cur.get("drafts") or [] if d["status"] == "draft")
        head = _said(f"{sc.shop(owner, d['customer'])}に、足りない見込みを伝えますか。")
        sub.append(_said(tl["line"], "calc"))
        sub.append(_said("下書きは AI が作りました。伝えるかどうかは、あなたが決めます。"))
    elif ex == "your_turn":
        head = aiq or _said("どう進めるか、決めてください。")
        if cur.get("waiting_for") == "staff":
            sub.append(_said(tl["line"], "calc"))
            if tl["unused_text"]:
                sub.append(_said(tl["unused_text"], "calc"))
    elif ex == "running":
        waiting = [n for c, n in sup.items() if any(x["to_owner"] == c and x["from_owner"] == owner for x in f["messages"])]
        head = _said("買い付けの照会は私が進めます。")
        sub = [_said("あなたは魚を見てきてください。判断が必要になったら声をかけます。")]
        if waiting:
            sub.append(_said(f"{'・'.join(waiting)}の返事を待っています。"))
    else:
        head = _said("見張っています。")
        sub = [_said("次の出来事があれば、声をかけます。")]

    # 人に求める行動（同じ返り値から）
    if ex == "your_turn":
        actions, ask = _choices(f, tl)
    elif st == "relief" and ex != "your_turn" and short == 0 and ex not in ("president", "stopped"):
        ask = _said("登録の前に、内容を確かめてください。")
    if ex in ("running", "idle") and not actions:
        ask = ask or _said("今は、AI に任せてかまいません。")
    if ex == "registered":
        ask = _said("この仕事は終わりました。")
    who = {"before_start": "you", "your_turn": "you", "stopped": "you", "president": "president"}.get(ex, "ai")
    # 相手の組織の印と、便りの動き（照会・返事）。画面は新しい便りの id だけを動かす
    orgs = {}
    for x in f["messages"]:
        o = x["to_owner"] if x["from_owner"] == owner else x["from_owner"]
        orgs[o] = dict(code=o, name=sc.OWNERS[o]["name"],
                       state="asked" if x["from_owner"] == owner and x["kind"] == "inquiry" else ("declined" if x["kind"] == "decline" else "replied"))
    for fr in (partial_rows(f) if f["acts"] and f["acts"][-1]["kind"] == "partial" else []):
        if fr["status"] != "failed" or fr["counterparty"] not in orgs:
            continue
        last = [x for x in f["messages"] if owner in (x["from_owner"], x["to_owner"]) and fr["counterparty"] in (x["from_owner"], x["to_owner"])][-1]
        if any(t["id"] == fr["term_id"] for t in last["terms"]):   # その相手との最後のやり取りが、登録できなかった条件
            orgs[fr["counterparty"]]["state"] = "expired" if fail_reason_short(fr["fail_reason"]) == "期限が切れた" else "failed"
    net = [dict(id=x["id"], frm=x["from_owner"], to=x["to_owner"], kind=x["kind"], at=x["created_at"]) for x in f["messages"]]
    pres = None   # 社長の画面（別の窓）で答える中身：担当者が送った確認と、その案
    if ex == "president":
        p = cur.get("pending") or {}
        o = next((x for x in cur.get("options") or [] if x["option_id"] == p.get("option_id")), None)
        ol = next((x for x in tl["options"] if x["option_id"] == p.get("option_id")), None)
        pres = dict(kind=p.get("kind"), case_id=cur["case"]["id"], option_id=p.get("option_id"), fingerprint=o["fingerprint"] if o else None,
                    label=ol["label"] if ol else None, note=ol["note"] if ol else None, consultation_id=p.get("consultation_id"), change=p.get("change"),
                    priority_change=p.get("priority_change"), over_limit=p.get("over_limit"), asked_by=p.get("asked_by"))
    return dict(owner=owner, item=f["item"], subject=subject(f), stance=st, stance_label=STANCE_LABEL.get(st), rule=rule, refs=refs,
                exec=ex, exec_label=EXEC_LABEL[ex], stop=stop, who=who, headline=head, sub=sub, ask=ask, actions=actions,
                why=why_card(f, st, ex, head, tl, replaced), receipt=_fresh_receipt(f), tally=tl, taken=taken(f, ex), others=others(owner, item, on_date),
                band=band(owner, item, on_date, worker_error, f), band_others=band_others(owner, item, on_date),
                orgs=list(orgs.values()), net=net, op=_op(f, ex), president=pres, busy=_running(f) is not None, clock=f["clock"])


def _op(f, ex):
    """実演の操作（料亭の追加注文）。業務の途中で、まだ入っていない時だけ出す。"""
    o = sc.BURI_ADDED_ORDER
    if f["item"]["code"] != o["item"] or ex == "before_start" or any(x["customer"] == o["customer"] for x in f["orders"]):
        return None
    return dict(label=f"（実演の操作）{sc.shop(f['owner'], o['customer'])}から追加注文が届く：{f['item']['short']} {o['quantity']}{f['item']['unit']}",
                body=dict(owner=f["owner"], customer=o["customer"], item=o["item"], quantity=o["quantity"], idempotency_key="demo-ryotei-buri-6"))


def _directive_shop(f):
    d = next((x for x in f["directives"] if x["customer"]), None)
    return (d["shop_name"] or d["customer"]) if d else None


def _choice_words(f, c, action, needs_president) -> tuple[str, str, str]:
    """案の名前（人の返事の言葉）・承認のボタンの文・押すと起きること。数はコードの計算（tally の alloc・buys）から書く。"""
    u = f["item"]["unit"]
    if action != "approve":
        held = sum(h["quantity"] for h in f["holds"])
        return ("今回は仕入れを見送る", "今回は見送る",
                "仕入れずに、この相談を閉じます。" + (f"仮押さえの {held}{u}は手放します。" if held else ""))
    ds = _directive_shop(f)
    got = [(a["shop"], a["secured"]) for a in c["alloc"]]
    mine = next((g for s_, g in got if s_ == ds), None)
    rest = "・".join(f"{s_}へ {g}{u}" for s_, g in got if s_ != ds and g > 0)
    if ds and mine:
        title = f"{ds} {mine}{u}を確保し、{rest}を回す" if rest else f"{ds} {mine}{u}を確保する"
    elif ds and mine == 0:
        title = f"{rest}を回す（{ds}は 0{u}）"
    else:
        title = f"{rest}に分ける"
    buys = "・".join(f"{b['seller']} {b['quantity']}{u}" for b in c["buys"])
    if needs_president:
        return title, "この案を社長に確かめる", "社長に確認を送ります。社長が承認するまで、何も登録しません。"
    return title, "この配分で購入を承認する", f"{buys}（合計 {_yen(c['purchase_yen'])}）の買い付けを登録します（購入の確定）。"


def _choices(f, tl):
    """あなたの判断待ちの時のボタン。案の名前と数はコードの計算（tally）から書く（AI の案の名前の数字は使わない）。
    社長の確認が要る案は「社長に確かめる」にする（新人に決めさせない）。"""
    cur = f["cur"]
    acts = []
    if cur.get("waiting_for") == "staff":
        by_id = {x["option_id"]: x for x in tl["options"]}
        for o in cur.get("options") or []:
            pv = option_needs_president(f["owner"], cur["case"]["id"], o["option_id"]) if o["action"] == "approve" else dict(needs=False)
            c = by_id[o["option_id"]]
            label = c["label"]
            if o["action"] == "approve" and not o["executable"]:
                acts.append(dict(id=f"opt{o['option_id']}", label=label, kind="off", disabled=True,
                                 note="実行できません：" + "・".join(o["numbers"]["errors"])))
                continue
            tags = [t for t in ("社長の確認が要る案" if pv["needs"] else "", "AI の勧め" if o["recommended"] and not pv["needs"] else "",
                                c["note"] if o["action"] == "approve" else "") if t]
            title, confirm, effect = _choice_words(f, c, o["action"], pv["needs"])
            acts.append(dict(id=f"opt{o['option_id']}", label=("社長に確かめる：" + label) if pv["needs"] else label,
                             kind="primary" if o["recommended"] else "secondary", recommended=o["recommended"], short_after=c["short_after"],
                             note="・".join(tags), title=title, confirm=confirm, effect=effect, alloc=c["alloc"], buys=c["buys"], purchase_yen=c["purchase_yen"],
                             choice="reject" if o["action"] != "approve" else ("president" if pv["needs"] else "approve"),
                             api=dict(path="/api/decide", body=dict(case_id=cur["case"]["id"], option_id=o["option_id"] if o["action"] == "approve" else None,
                                                                    action=o["action"], fingerprint=o["fingerprint"] if o["action"] == "approve" else None,
                                                                    actor="staff")),
                             president=pv["needs"]))
        return acts, _said("どれで進めるか、選んでください。" + ("社長の確認が要る案は、私が社長に確かめます。" if any(a.get("president") for a in acts) else ""))
    if cur.get("stock_question"):
        q = cur["stock_question"]
        for c in q["choices"]:
            acts.append(dict(id=f"sq{c['code']}", label=c["label"], kind="primary" if c["code"] == q["recommended"] else "secondary",
                             recommended=c["code"] == q["recommended"],
                             api=dict(path="/api/stock_answer", body=dict(question_id=q["question_id"], choice=c["code"], role="staff"))))
        said, _ = _ai_text(f, tl, q["text"], dict(db="owner", table="stock_question", id=q["question_id"]))
        return acts, said or _said(f"引当のない在庫 {q['quantity']}{f['item']['unit']}の扱いを決めてください。", "calc")
    d = next(d for d in cur.get("drafts") or [] if d["status"] == "draft")
    acts = [dict(id=f"dr{d['draft_id']}a", label="この文で伝える", kind="primary", api=dict(path="/api/draft", body=dict(draft_id=d["draft_id"], action="approve", role="staff"))),
            dict(id=f"dr{d['draft_id']}d", label="使わない", kind="secondary", api=dict(path="/api/draft", body=dict(draft_id=d["draft_id"], action="discard", role="staff")))]
    shop = sc.shop(f["owner"], d["customer"])
    said, _ = _ai_text(f, tl, d["body"], dict(db="owner", table="customer_draft", id=d["draft_id"]))   # 下書きの文の数も照合する
    if said:
        said = dict(said, text=f"{shop}への連絡の下書きです（AI が作成・まだ送っていません）：{d['body']}")
    return acts, said or _said(f"{shop}への連絡の下書きがあります（AI が作成・まだ送っていません）。数が合わないので、なぜ？で確かめてください。", "calc")


# ── 理由の1枚（仲間に触れる・「なぜ？」で開く。判断に要る順）────────────────

def _row(text, origin, at=None, ref=None):
    return dict(text=text, origin=origin, at=at, ref=ref)


ORIGIN_LABEL = {"reply": "相手から届いた回答", "calc": "コードの計算", "vault": "Vault の記録", "candidate": "まだ確認していない候補",
                "directive": "社長の指示（記録）", "record": "業務の記録", "ai": "AI が書いた文", "ai_checked": "AI が書いた文（照合済み）",
                "code": "コードの決まった文"}


def why_card(f, st, ex, head, tl=None, replaced=None) -> dict:
    u = f["item"]["unit"]
    cur = f["cur"]
    ds = _directive_shop(f)
    tl = tl or tally(f)
    # 数は全部 tally から（見出し・下の行・ボタンと同じ計算）
    orders = [_row(f"{o['shop']}の注文 {o['quantity']}{u}：登録済み {o['registered']}{u}" +
                   (f"・人の決定で回す仮押さえ {o['secured'] - o['registered']}{u}" if o["secured"] > o["registered"] else ""),
                   "record", o["received_at"], dict(db="owner", table="order_book", id=o["order_id"])) for o in f["orders"]]
    if tl["held"]:
        orders.append(_row("仮押さえ（相手が期限つきで押さえた分・どの注文に回すかは未定）：" + "・".join(f"{h['name']} {h['quantity']}{u}" for h in tl["held"]),
                           "reply", None, dict(db="market", table="offer_term", id=tl["held"][0]["term_id"])))
    if tl["reply_only"]:
        orders.append(_row("返事だけ（押さえていない）：" + "・".join(f"{r['name']} {r['quantity']}{u}" for r in tl["reply_only"]), "reply"))
    orders.append(_row(tl["line"], "calc"))
    dir_rows = [_row(f"「{d['body']}」（{d['issued_from']}から・{d['via']}）", "directive", d["received_at"],
                     dict(db="owner", table="directive", id=d["directive_id"])) for d in f["directives"]]
    staff = f["authority"].get("staff") or {}
    pres = f["authority"].get("president") or {}
    dir_rows.append(_row(f"若い担当者が決められるのは 1件 {_yen(staff['max_trade_yen'])}まで。得意先の間の優先を変えるのは{PRESIDENT}", "record", None,
                         dict(db="owner", table="authority", id=None)) if staff.get("max_trade_yen") else
                    _row(f"得意先の間の優先を変えるのは{PRESIDENT}", "record"))
    replies = []
    for x in f["messages"]:
        if x["to_owner"] != f["owner"] or x["kind"] not in ("offer", "decline"):
            continue
        who = sc.OWNERS[x["from_owner"]]["name"]
        if x["kind"] == "decline":
            replies.append(_row(f"{who}：断り" + (f"（{x['reason']}）" if x["reason"] else ""), "reply", x["created_at"],
                                dict(db="market", table="network_message", id=x["id"])))
        held_ids = {h["term_id"] for h in tl["held"]}
        for t in x["terms"]:
            if t["status"] == "offered":
                state = "仮押さえ" if t["id"] in held_ids else ("同じ返事の別の条件・どちらか一方" if t["hold_ref"] else "返事だけ・押さえなし")
            else:
                state = {"released": "下ろした", "confirmed": "確定", "expired": "期限切れ"}.get(t["status"], t["status"])
            replies.append(_row(f"{who}：{t['quantity']}{u} × {_yen(t['unit_price_yen'])}（{state}・期限 {t['valid_until'][11:16]}）", "reply",
                                x["created_at"], dict(db="market", table="offer_term", id=t["id"])))
    asked = [_row(f"{sc.OWNERS[x['to_owner']]['name']}に照会：{x['quantity'] or ''}{u if x['quantity'] else ''}" +
                  (f" × {_yen(x['unit_price_yen'])}" if x["unit_price_yen"] else ""), "record", x["created_at"],
                  dict(db="market", table="network_message", id=x["id"])) for x in f["messages"] if x["from_owner"] == f["owner"] and x["kind"] == "inquiry"]
    stock = [_row(s["text"], "record") for s in cur.get("stock") or []]
    cands = [_row(f"{sc.OWNERS[p['owner']]['name']}の掲示：{p['quantity']}{u}を譲りたい（{_yen(p['unit_price_yen'])}）", "candidate", p.get("created_at"),
                  dict(db="market", table="deal_request", id=p["id"])) for p in f["posts"]
             if not any(x["to_owner"] == p["owner"] for x in f["messages"] if x["from_owner"] == f["owner"])]
    calc = []
    for o in f["orders"]:
        if o["ceiling_yen"]:
            calc.append(_row(f"{o['shop']}向けの仕入れ値の上限 {_yen(o['ceiling_yen'])}（売値 − 加工 − 確保する利益）", "calc"))
    opts = []
    by_id = {x["option_id"]: x for x in tl["options"]}
    for o in cur.get("options") or []:
        n, c = o["numbers"], by_id[o["option_id"]]
        for r in c["rows"]:   # 2つ以上の相手の案：行ごとの値・期限・出所（相手から届いた条件）
            opts.append(_row(f"案{c['no']}の行：{r['seller']} {r['quantity']}{u} × {_yen(r['unit_price_yen'])}（期限 {r['valid_until'][11:16]}・条件 {r['term_id']}）",
                             "reply", None, dict(db="market", table="offer_term", id=r["term_id"])))
        opts.append(_row(f"案{c['no']}：{c['label']}" + (f"（仕入れ {_yen(n['purchase_yen'])}）" if n["purchase"] else "") + f"・{c['note']}" +
                         ("" if o["executable"] else "（実行できません）"), "calc"))
    vault = [_row(f"{v['title']}（{v['author']}・{v['written_on']}）", "vault", None, dict(db="owner", table="vault_note", id=v["id"])) for v in f["vault"]]
    history = asked + [r for r in replies]

    # 先頭の一文（今の判断の理由）
    lead = None
    others_short = [o for o in f["orders"] if o["short"] > 0 and o["shop"] != ds]
    if ex == "before_start":
        can = ai_can_proceed(f["owner"], f["on_date"])
        lead = "今朝の注文と社長の指示を確かめてから、" + ("・".join(can) + "を私が進めます。" if can else "配分と購入の判断を相談します。")
        vol = order_volume(f["owner"], f["on_date"])
        orders = orders + [_row(f"今朝の{v['item']} {v['today']}{v['unit']}（同じ売り先のふだん {v['usual']:g}{v['unit']}・直近 30日の平均）" if v["known"]
                                else f"今朝の{v['item']} {v['today']}{v['unit']}（売れ方の記録の無い売り先を含むため、ふだんとは比べていません）", "calc")
                           for v in vol["items"]]
        sections = [("今朝の注文", orders), ("社長の指示と権限", dir_rows), ("任せる範囲", [_row("AI に任せる：" + "・".join(sc.DELEGATION_SCOPES.values()), "code"),
                                                                                    _row(f"{PRESIDENT}に確かめる：得意先の優先・権限の外の承認", "code"),
                                                                                    _row("あなたが決める：購入の承認（権限の内）・配分", "code")])]
        more = [("Vault の記録", vault)]
    elif ex == "president":
        lead = (f"{ds}を欠く案なので、社長の朝の指示により{PRESIDENT}の確認が要ります。" if ds and (cur.get("pending") or {}).get("priority_change")
                else f"担当者の権限の外なので、{PRESIDENT}の確認が要ります。")
        sections = [("社長の指示と権限", dir_rows), ("足りない数と注文", orders), ("案の中身", opts)]
        more = [("相手の回答と値", replies), ("Vault の記録", vault)]
    elif st == "careful" or (st is None and ex == "your_turn"):
        ds_short = any(o["shop"] == ds and o["short"] > 0 for o in f["orders"])
        if _return_ctx(f):
            lead = f"社長から見直しの返事があったためです（理由：「{_return_ctx(f)['reason']}」）。登録済みの取引はそのままで、未確定の案だけを組み直します。"
        elif ds and others_short and ds_short:
            lead = (f"{ds}を優先するのは、社長の朝の指示があるためです。"
                    f"{'・'.join(o['shop'] for o in others_short)}を優先するには、社長への確認が必要です。")
        elif st == "careful" and _round_declines(f) and _short_total(f) > 0:
            names = "・".join(sorted({sc.OWNERS[x["from_owner"]]["name"] for x in _round_declines(f)}))
            lack = (f"{'追加分が' if tl['registered'] else ''}あと {tl['left']}{u}不足している" if tl["left"] else "条件が変わった")   # いまの理由（SAL 2026-11-04）
            lead = f"{names}から断りの返事が届き、{lack}ためです。足りない分をどう手配するか、あなたと相談しています。"
        elif st == "careful":
            lead = ("追加の注文が入ったので" if _mid_orders(f) else "あなたの答えで条件が変わったので") + "、足りない数と相手の回答を確かめ直しています。"
        else:
            lead = f"注文の合計 {tl['total']}{u}に対して、あと {tl['left']}{u}足りません。どう分けるか、あなたの判断が要ります。"
        sections = [("足りない数と注文", orders), ("社長の指示と権限", dir_rows), ("相手の回答と値", replies), ("在庫", stock), ("案（コードの計算）", opts)]
        if tl["unused_text"]:   # 案に入っていない仮押さえは、なぜ入っていないかを案の前に一文で
            sections.insert(4, ("どの案にも入っていない仮押さえ", [_row(tl["unused_text"], "calc")]))
        more = [("Vault の記録", vault), ("これまでのやり取り", asked)]
    elif st == "bold":
        lead = f"{'と'.join(_suppliers(f).values())}の複数に当たれるので、希望の値で押せます。"
        sections = [("照会先と回答", asked + replies), ("足りない数と注文", orders), ("社長の指示と権限", dir_rows)]
        more = [("まだ確認していない候補", cands), ("Vault の記録", vault)]
    elif st == "struggle":
        lead = "届いた値が上限を超え、ほかに当てのある相手がいないためです。"
        sections = [("値と上限（コードの計算）", calc), ("相手の回答と値", replies), ("まだ確認していない候補", cands), ("社長の指示と権限", dir_rows)]
        more = [("足りない数と注文", orders), ("Vault の記録", vault)]
    elif st == "sorry":
        lead = ("相手から断りの返事が届いたためです。足りない見込みを売り先に伝えるかどうか、あなたが決めます。"
                if ex == "your_turn" and any(x["status"] == "draft" for x in cur.get("drafts") or [])
                else f"相手から断りの返事が届いたためです。あと {tl['left']}{f['item']['unit']}不足したまま、売り先への連絡はあなたが決めました。" if ex == "idle" and tl["left"] and _closed_draft(f)
                else "相手から断りの返事が届いたためです。ほかに当たれる先を探します。")
        sections = [("相手の回答", replies), ("まだ確認していない候補", cands), ("足りない数と注文", orders)]
        more = [("社長の指示と権限", dir_rows), ("Vault の記録", vault)]
    elif st == "relief":
        lead = "必要な数量が、条件の内でそろったためです。"
        sections = [("そろう内訳", orders), ("社長の指示と権限", dir_rows), ("相手の回答と値", replies)]
        more = [("案（コードの計算）", opts), ("Vault の記録", vault)]
    else:
        lead = "任された範囲で、照会を進めています。"
        sections = [("照会先と回答", history), ("足りない数と注文", orders), ("社長の指示と権限", dir_rows)]
        more = [("在庫", stock), ("Vault の記録", vault)]
    ck = f["clock"]

    def keep(xs):   # 時刻は模擬の時計で見せる（実際の記録の時刻 at も残す）
        return [dict(title=t, rows=[dict(r, at_shown=shown_time(r["at"], ck)) for r in rows]) for t, rows in xs if rows]
    return dict(lead=lead, sections=keep(sections), more=keep(more),
                speech_origin=dict(origin=head["origin"], label=ORIGIN_LABEL[head["origin"]], ref=head["ref"], replaced=replaced) if head else None,
                origins=ORIGIN_LABEL)


# ── 受け取った内容（人の判断の後・サーバーの記録から）と、AI が引き受けたこと ──────────

def receipt(f) -> dict | None:
    """人の最後の判断を、コードが記録から言い直す。その後に AI が書いた文（終わった起動の要約）があれば添える（出所 ai）。"""
    u = f["item"]["unit"]
    last = f["acts"][-1] if f["acts"] else None
    dele = f["delegation"]
    if last is None and dele is None:
        return None
    if last is None or (dele and _t(dele["granted_at"]) > _t(last["at"])):
        scope = "・".join(sc.DELEGATION_SCOPES.get(s, s) for s in json.loads(dele["scope_json"]))
        out = dict(at=dele["granted_at"], text=f"受け取りました。{scope}を引き受けます。", ref=dict(db="owner", table="delegation", id=dele["id"]))
    else:
        short = [o for o in f["orders"] if o["short"] > 0]
        rest = ("残りの " + "・".join(f"{o['shop']} {o['short']}{u}" for o in short) + "は、私が別の仕入先を当たります。") if short else ""
        k = last["kind"]
        if k == "registered":
            got = [o for o in f["orders"] if o["registered"]]
            text = f"受け取りました。{_orders_phrase(f, [dict(o, quantity=o['registered']) for o in got])}で登録しました。" + rest
        elif k == "partial":   # 2つ以上の相手の案で、登録できた行とできなかった行がある（何が済んで何が残ったかを一文で）
            pt = _partial_text(f)
            text = f"受け取りました。{pt['done']}" + pt["rest"]
        elif k == "needs_president":
            text = f"受け取りました。{PRESIDENT}に確認を送りました。答えが来たら続けます。"
        elif k == "returned":
            d = json.loads(last.get("detail") or "{}")
            text = (f"受け取りました。「{d.get('label') or '元の'}」の案を差し戻しました。理由：「{d.get('reason') or ''}」。"
                    "元の案は登録できない状態にし、担当者と AI に見直しを伝えました。")
        elif k == "rejected":
            # 見送りでは AI は起きない（相談を閉じる）。「別の仕入先を当たります」と約束しない（SAL 2026-11-04 通しの確認）
            text = "受け取りました。この案は見送り、仮押さえは手放しました。" + (("・".join(f"{o['shop']} {o['short']}{u}" for o in short) + "は足りないままです。") if short else "")
        elif k == "decision":
            con = db.connect(f["owner"])
            try:
                d = dict(con.execute("SELECT * FROM human_decision WHERE id = ?", (last["id"],)).fetchone())
            finally:
                con.close()
            allocs = json.loads(d["allocations_json"]) if d["allocations_json"] else []
            shops = {o["customer"]: o["shop"] for o in f["orders"]}
            parts = "・".join(f"{shops.get(a['customer'], a['customer'])} {a['quantity']}{u}" for a in allocs)
            what = parts or f"仕入れ {d['quantity']}{u}"
            text = f"受け取りました。{what}の条件で、相手に問い合わせ直します。"
        elif k == "stock_answer":
            text = "受け取りました。答えの扱いで続けます。"
        else:
            text = "受け取りました。"
        out = dict(at=last["at"], text=text, ref=dict(db="owner", table="case_decision" if k in ("registered", "partial", "needs_president", "rejected", "stale", "returned") else
                                                       ("human_decision" if k == "decision" else "stock_question"), id=last["id"]))
    after = [r for r in f["runs"] if r["ended_at"] and _t(r["started_at"]) >= _t(out["at"]) and r["summary"] and r["outcome"] not in STOP_OUTCOMES]
    if after:
        out["ai"] = _said(after[-1]["summary"], "ai", dict(db="owner", table="agent_run", id=after[-1]["id"]))
    out["at_shown"] = shown_time(out["at"], f["clock"])
    return out


def partial_rows(f) -> list[dict]:
    """最後の人の判断が一部登録の時の、案件の仕入れの行（登録できた行・できなかった行と理由）。"""
    last = f["acts"][-1] if f["acts"] else None
    if not last or last["kind"] != "partial":
        return []
    cid = json.loads(last["detail"] or "{}").get("case_id")
    con = db.connect(f["owner"])
    try:
        return [dict(r) for r in con.execute("SELECT counterparty, quantity, status, fail_reason, term_id FROM proposal WHERE case_id = ? AND side = 'buy' ORDER BY id",
                                             (cid,))]
    finally:
        con.close()


def _partial_text(f) -> dict:
    u = f["item"]["unit"]
    rows = partial_rows(f)
    ok = "・".join(f"{sc.OWNERS[r['counterparty']]['name']} {r['quantity']}{u}" for r in rows if r["status"] == "registered")
    ng = "・".join(f"{sc.OWNERS[r['counterparty']]['name']} {r['quantity']}{u}は{fail_reason_short(r['fail_reason'])}ので登録していません"
                   for r in rows if r["status"] == "failed")
    short = [o for o in f["orders"] if o["short"] > 0]
    return dict(done=f"{ok}は登録しました。{ng}。",
                rest=("残りの " + "・".join(f"{o['shop']} {o['short']}{u}" for o in short) + "を、私が相談し直します。") if short else "")


def partial(f) -> dict | None:
    """一部登録の受け取った内容が今も新しい（AI がまだ次の相談を出していない）時の、見出しの文。"""
    r = _fresh_receipt(f)
    if not r or not f["acts"] or f["acts"][-1]["kind"] != "partial" or (f["delegation"] and _t(f["delegation"]["granted_at"]) > _t(f["acts"][-1]["at"])):
        return None
    return _partial_text(f)


def fail_reason_short(reason: str | None) -> str:
    """登録できなかった理由（コードが書いた長い文）を、画面の一文に入る短い言い方にする。"""
    r = reason or ""
    if "期限" in r or "expired" in r:
        return "期限が切れた"
    if "仮押さえが無い" in r:
        return "相手が押さえを下ろした"
    if "在庫" in r:
        return "相手の在庫が足りなくなった"
    if "権限" in r:
        return "相手の担当者の権限の外だった"
    if "帳簿" in r:
        return "帳簿に登録できなかった"
    return "相手の確認が通らなかった"


def _closed_draft(f):
    """人が最後に決めた売り先への連絡の下書き（答えを待つ下書きが無く、決めた後に AI が動いていない時だけ）。"""
    drafts = f["cur"].get("drafts") or []
    if any(d["status"] == "draft" for d in drafts) or _running(f):
        return None
    done = [d for d in drafts if d["status"] in ("approved", "discarded") and d.get("decided_at")]
    if not done:
        return None
    d = max(done, key=lambda x: _t(x["decided_at"]))
    last = [_t(x["at"]) for x in (f["acts"] or [])] + [_t(x["at"]) for x in (f["presents"] or [])]
    return d if not last or _t(d["decided_at"]) >= max(last) else None


def _fresh_receipt(f):
    """受け取った内容は、その後に AI が新しい相談を出すまで見せる（古い受領を次の判断の横に残さない）。"""
    r = receipt(f)
    cd = _closed_draft(f)
    if cd and (not r or _t(cd["decided_at"]) >= _t(r["at"])):   # 最後の判断が下書きの決定
        shop = sc.shop(f["owner"], cd["customer"])
        return dict(at=cd["decided_at"], text=f"受け取りました。{shop}への連絡の下書きを" + ("使うと決めました。" if cd["status"] == "approved" else "使わないと決めました。"),
                    ref=dict(db="owner", table="customer_draft", id=cd["draft_id"]), at_shown=shown_time(cd["decided_at"], f["clock"]))
    if r and any(d["status"] == "draft" and _t(d["created_at"]) >= _t(r["at"]) for d in f["cur"].get("drafts") or []):
        return None   # AI が後から売り先への連絡の下書きを出した：前の受領（「別の仕入先を当たります」）を下書きの問いの横に残さない
    if r and f["presents"] and _t(f["presents"][-1]["at"]) > _t(r["at"]) + 1:
        return None
    return r


def taken(f, ex) -> list[str]:
    """AI が引き受けていること（任せた範囲の記録と、今の動き）。"""
    d = f["delegation"]
    out = []
    if d:
        out.append("任された範囲：" + "・".join(sc.DELEGATION_SCOPES.get(s, s) for s in json.loads(d["scope_json"])))
    run = _running(f)
    if run:
        out.append({"order_new": "注文を読み、照会を進めています", "quote_received": "届いた返事を読み、案を組んでいます",
                    "human_choice": "あなたの答えを受けて、進め直しています", "trade_registered": "登録を受けて、残りを確かめています",
                    "quote_request": "問い合わせに答えています", "staff_question": "あなたの質問に答えています"}.get(run["kind"], "出来事を確かめています")
                   + ("（順番を待っています）" if run["queued"] else ""))
    return out


# ── ほかの仕事（短い状態の表示だけ）と、シロエビの入札の接続 ──────────────────

def others(owner: str, item: str, on_date: str) -> list[dict]:
    out = []
    con = db.connect(owner)
    try:
        items = [r["code"] for r in con.execute("SELECT DISTINCT i.code FROM order_book o JOIN item i ON i.id = o.item_id WHERE o.order_date = ? ORDER BY i.id",
                                                (on_date,))]
    finally:
        con.close()
    for code in items:
        if code == item:
            continue
        if tender_item(owner, code):
            t = tender_status(owner, code, on_date)
            out.append(dict(item=code, name=t["name"], status=t["status"], connected=t["connected"]))
        else:
            ex, _ = exec_state(facts(owner, code, on_date))
            out.append(dict(item=code, name=_item_name(owner, code), status=EXEC_LABEL[ex], connected=True))
    return out


def band_others(owner: str, item: str, on_date: str) -> list[dict]:
    """帯の下の一行（ほかの仕事）：今日の注文がある品目のうち、開いている品目の外。"""
    con = db.connect(owner)
    try:
        codes = [r["code"] for r in con.execute("SELECT DISTINCT i.code FROM order_book o JOIN item i ON i.id = o.item_id WHERE o.order_date = ? ORDER BY i.id",
                                                (on_date,))]
    finally:
        con.close()
    return [band(owner, c, on_date) for c in codes if c != item]


def _item_name(owner, code):
    con = db.connect(owner)
    try:
        return _short_name(con.execute("SELECT name FROM item WHERE code = ?", (code,)).fetchone()[0])
    finally:
        con.close()


def tender_item(owner: str, code: str) -> bool:
    """この品目は入札で買うか（自社の記録の相場の種類から）。"""
    con = db.connect(owner)
    try:
        r = con.execute("SELECT kind FROM price_history p JOIN item i ON i.id = p.item_id WHERE i.code = ? ORDER BY trade_date DESC LIMIT 1", (code,)).fetchone()
    finally:
        con.close()
    return bool(r) and r[0] == "tender"


TENDER_TOOL = None   # AI が入札を出す道具（agents.TOOLSETS）。2026-11-04 時点で無い


def tender_status(owner: str, code: str, on_date: str = sc.DEMO_DATE) -> dict:
    """入札の仕事の状態と、本物の処理への接続（記録から。台本の値は使わない）。"""
    con = db.connect(owner)
    try:
        it = con.execute("SELECT id, name, unit FROM item WHERE code = ?", (code,)).fetchone()
        u = sc.unit_label(it["unit"])
        orders = [dict(r) for r in con.execute("SELECT o.id, o.quantity, c.name AS customer, c.shop_name FROM order_book o JOIN customer c ON c.id = o.customer_id "
                                               "WHERE o.item_id = ? AND o.order_date = ? ORDER BY o.id", (it["id"], on_date))]
        plan = con.execute("SELECT max_unit_price_yen, max_quantity, approved_at FROM plan WHERE item_id = ? AND kind = 'tender' AND trade_date = ? AND status = 'approved'",
                           (it["id"], on_date)).fetchone()
        decision = con.execute("SELECT d.id, d.action, d.unit_price_yen, d.quantity, d.model FROM decision d JOIN task t ON t.id = d.task_id "
                               "WHERE t.item_id = ? AND t.kind = 'tender' ORDER BY d.id DESC LIMIT 1", (it["id"],)).fetchone()
    finally:
        con.close()
    m = db.connect(db.MARKET)
    try:
        t = m.execute("SELECT t.id, t.status, t.opens_at, t.closes_at, l.lot_no FROM tender t JOIN lot l ON l.id = t.lot_id "
                      "WHERE l.item_code = ? AND l.trade_date = ? ORDER BY t.id DESC LIMIT 1", (code, on_date)).fetchone()
        bid = m.execute("SELECT unit_price_yen, quantity, submitted_at FROM tender_bid WHERE tender_id = ? AND bidder = ?", (t["id"], owner)).fetchone() if t else None
    finally:
        m.close()
    tools = {x.name for x in agents.TOOLSETS.get(owner, [])}
    has_tool = any(n in tools for n in ("submit_bid", "place_bid"))
    rows = [dict(label="注文", text="・".join(f"{o['shop_name'] or o['customer']} {o['quantity']}{u}" for o in orders) or "なし", connected=True),
            dict(label="入札の計画", text=(f"上限 {_yen(plan['max_unit_price_yen'])}・{plan['max_quantity']}{u}（社長が承認）" if plan else "記録なし（社長が承認した計画がありません）"), connected=True),
            dict(label="市場の入札の受付", text=(f"{t['lot_no']}・締切 {t['closes_at'][11:16]}（{t['status']}）" if t else "未接続：今日の入札の受付が市場の DB にありません"),
                 connected=bool(t)),
            dict(label="AI が入札を出す道具", text="あり" if has_tool else "未接続：AI が入札を出す道具がまだありません", connected=has_tool),
            dict(label="自社の入札", text=(f"{bid['quantity']}{u} × {_yen(bid['unit_price_yen'])}（{bid['submitted_at'][11:16]}）" if bid else "なし"), connected=True),
            dict(label="入札の判断の記録", text=(f"{decision['quantity']}{u} × {_yen(decision['unit_price_yen'])}（{'模擬の記録' if decision['model'] == 'mock' else decision['model']}）"
                                                if decision else "なし"), connected=True)]
    connected = bool(t) and has_tool
    return dict(item=code, name=_short_name(it["name"]), unit=u, connected=connected, rows=rows,
                status="入札中" if bid else ("未接続" if not connected else "入札の前"), closes_at=t["closes_at"] if t else None,
                orders=orders)


def tender_view(owner: str, code: str, on_date: str = sc.DEMO_DATE) -> dict:
    """人がシロエビを開いた時の、仲間の説明（未接続を明示する）。"""
    t = tender_status(owner, code, on_date)
    u = t["unit"]
    q = sum(o["quantity"] for o in t["orders"])
    head = _said(f"{t['name']} {q}{u}の入札は、まだ本物の処理につながっていません。" if not t["connected"] else f"{t['name']}の入札を進めています。")
    sub = [_said("つながっていない部分は「未接続」と出しています。実装済みには見せません。")] if not t["connected"] else []
    return dict(owner=owner, item=dict(code=code, name=t["name"], short=t["name"], unit=u), subject=f"{t['name']}の入札", stance=None, stance_label=None,
                rule=None, refs={}, exec="unconnected" if not t["connected"] else "running", exec_label="未接続" if not t["connected"] else EXEC_LABEL["running"],
                stop="入札の受付と AI の入札の道具が、まだ本物の処理につながっていません" if not t["connected"] else None, who="ai", headline=head, sub=sub,
                ask=_said("今は、ブリの仕事を続けてください。"), actions=[], tender=t,
                why=dict(lead="入札の仕事が、本物の処理のどこまでつながっているかです。",
                         sections=[dict(title="接続の状態", rows=[_row(f"{r['label']}：{r['text']}", "record" if r["connected"] else "candidate") for r in t["rows"]])],
                         more=[], speech_origin=dict(origin="code", label=ORIGIN_LABEL["code"], ref=None), origins=ORIGIN_LABEL),
                receipt=None, taken=[], others=[], clock=clock(owner, on_date),
                band=band(owner, code, on_date), band_others=band_others(owner, code, on_date))


# ── 工程の帯（今日の記録から、コードが各段の状態を決める）──────────────────

def _results_for(owner: str, code: str, on_date: str) -> list[dict]:
    """漁協から届いた今日の結果（せり・入札）のうち、この品目の行。"""
    con = db.connect(owner)
    try:
        return [r for r in results._received(con, on_date) if r["item"] == code]
    finally:
        con.close()


def band(owner: str, code: str, on_date: str = sc.DEMO_DATE, worker_error: str | None = None, f: dict | None = None) -> dict:
    """品目の工程の帯。各段の状態＝done（済み）・current（いま）・todo（これから）・skipped（今日は無い）。
    connected=False の段は本物の処理につながっていない（未接続）。工程が進むのは記録が変わった時だけ（時間では進めない）。
    mode は現在地の印の動き方：before（始める前）・working（AI が働いている）・you（あなたの判断待ち）・president（社長確認中）・
    stopped（停止）・unconnected（未接続の段で止まっている）・watch（見張り中）・done（登録まで済んだ）"""
    tender = tender_item(owner, code)
    names = BAND_STEPS["tender" if tender else "relative"]
    steps = [dict(name=n, state="todo", connected=True, note=None) for n in names]
    S = {x["name"]: x for x in steps}
    S["結果の引継ぎ"]["connected"] = False
    got = _results_for(owner, code, on_date)
    ex = None
    if tender:
        t = tender_status(owner, code, on_date)
        u, name = t["unit"], t["name"]
        S["入札"]["connected"] = t["connected"]
        S["振分け"]["connected"] = False
        con = db.connect(owner)
        try:
            dele = con.execute("SELECT id FROM delegation WHERE trade_date = ? LIMIT 1", (on_date,)).fetchone()
        finally:
            con.close()
        S["注文の確認"]["note"] = f"注文 {len(t['orders'])}件"
        mode = "before"
        if dele is None:
            S["注文の確認"].update(state="current", note="担当者の確認待ち")
        else:
            S["注文の確認"]["state"] = "done"
            S["入札"].update(state="current", note="未接続" if not t["connected"] else t["status"])
            mode = "unconnected" if not t["connected"] else "working"
        if got:   # 入札の結果が届いていれば、照合は済み
            for n in ("注文の確認", "入札"):
                S[n]["state"] = "done"
            won = sum(r["quantity"] for r in got if r["is_own"])
            S["落札結果の照合"].update(state="done", note=f"落札 {won}{u}")
    else:
        f = f or facts(owner, code, on_date, worker_error)
        u, name = f["item"]["unit"], f["item"]["short"]
        ex, _ = exec_state(f)
        S["注文の確認"]["note"] = f"注文 {len(f['orders'])}件"
        mode = "before"
        if ex == "before_start":
            S["注文の確認"].update(state="current", note="担当者の確認待ち")
        else:
            S["注文の確認"]["state"] = "done"
            if got:
                won = sum(r["quantity"] for r in got if r["is_own"])
                kind = "せり" if all(str(r["lot_no"]).startswith("A-") for r in got) else "市場"
                # 照合が済んだことと、魚を確保できたかを分ける（SAL 2026-11-04：0本で「済み」は誤読される）
                S["落札結果の照合"].update(state="done", word="照合済み",
                                       note=f"{kind}で自社の落札 {won}{u}" if won else "自社の落札なし。不足分の手配へ")
            elif f["runs"]:   # 結果が届かないまま手配が始まった：今日の結果は無い（未着と不足を分ける）
                S["落札結果の照合"].update(state="skipped", note="今日の結果は届いていません")
            else:
                S["落札結果の照合"].update(state="current", note="漁協の結果を待っています")
                mode = "watch"
            if got or f["runs"]:
                tl = tally(f)
                cur = f["cur"]
                held = f"仮押さえ {tl['held_total']}{u}" if tl["held_total"] else None
                if ex == "registered":
                    S["不足分の手配"].update(state="done", note=held)
                    S["振分け・承認"].update(state="done", note="登録済み")
                    mode = "done"
                elif ex in ("your_turn", "president"):
                    if tl["left"]:   # 不足が残る：済みとせず、終えた範囲（照会・仮押さえ）と残りを示す（SAL 2026-11-04）
                        S["不足分の手配"].update(state="partial", note="・".join(x for x in (held, f"あと {tl['left']}{u}不足") if x))
                    else:
                        S["不足分の手配"].update(state="done", note=held)
                    what = "見直した案" if _return_ctx(f) else "配分" if cur.get("waiting_for") == "staff" else "売り先への連絡" if any(d["status"] == "draft" for d in cur.get("drafts") or [])                         else "在庫の扱い" if cur.get("stock_question") else "配分"
                    S["振分け・承認"].update(state="current", note=f"{what}を相談中・あなたの判断待ち" if ex == "your_turn" else "社長確認中")
                    mode = "you" if ex == "your_turn" else "president"
                else:
                    who = _counterparts(f)
                    note = {"stopped": "停止", "running": (f"{'・'.join(who)}に照会中" if who else "照会の準備中"), "idle": "見張り中"}.get(ex, EXEC_LABEL.get(ex))
                    if ex == "running" and _mid_orders(f):
                        note = "追加注文で照会し直し中"
                    if ex == "running" and _return_ctx(f):
                        note = "社長の差し戻しを受けて組み直し中"
                    if ex == "idle" and tl["left"]:   # 見張りに戻っても、不足が残ることを言う
                        note = f"あと {tl['left']}{u}不足・見張り中"
                    S["不足分の手配"].update(state="current", note=note)
                    mode = {"stopped": "stopped", "running": "working"}.get(ex, "watch")
    cur_i = next((i for i, x in enumerate(steps) if x["state"] == "current"), None)
    here = steps[cur_i] if cur_i is not None else None
    if here:
        summary = f"{name}：{here['name']}" + (f"（{here['note']}）" if here["note"] else "")
    elif mode == "done":
        summary = f"{name}：登録済み（結果の引継ぎは未接続）"
    else:
        summary = f"{name}：{EXEC_LABEL.get(ex, '') if ex else ''}"
    return dict(item=code, name=name, tender=tender, steps=steps, current=cur_i, mode=mode, summary=summary)

# ── 今日の仕事の流れ（今日の記録から、コードの決まりで並べる）──────────────────

def order_volume(owner, on_date) -> dict:
    """今日の注文の数量と、同じ売り先・品目のふだんの数量（直近 30日に注文のあった日の平均・売れ方の記録から）。
    品目ごとに比べる（本と箱を足さない）。売れ方の記録の無い売り先がある品目は比べない（ふだんを 0 と数えると、必ず「多い」になるため）。"""
    con = db.connect(owner)
    try:
        start = (datetime.date.fromisoformat(on_date) - datetime.timedelta(days=30)).isoformat()
        rows = {}
        for r in con.execute("SELECT o.customer_id, o.item_id, i.name, i.unit, sum(o.quantity) FROM order_book o JOIN item i ON i.id = o.item_id "
                             "WHERE o.order_date = ? GROUP BY 1, 2 ORDER BY o.item_id", (on_date,)):
            a = con.execute("SELECT avg(ordered_qty) FROM sales_history WHERE customer_id = ? AND item_id = ? AND sale_date >= ? AND sale_date < ?",
                            (r[0], r[1], start, on_date)).fetchone()[0]
            v = rows.setdefault(r[1], dict(item=_short_name(r[2]), unit=sc.unit_label(r[3]), today=0, usual=0.0, known=True))
            v["today"] += r[4]
            v["usual"] += a or 0
            v["known"] = v["known"] and a is not None
    finally:
        con.close()
    items = [dict(v, usual=round(v["usual"], 1), more=v["known"] and v["today"] > v["usual"]) for v in rows.values()]
    return dict(items=items, more=[v["item"] for v in items if v["more"]])


def _volume_words(vol) -> str:
    """「今朝はブリ 4本・シロエビ 5箱です。直近 30日の平均は、ブリ 3.6本・シロエビ 4.6箱です。」（比べられない品目は平均を言わない）"""
    s = "今朝は" + "・".join(f"{v['item']} {v['today']}{v['unit']}" for v in vol["items"]) + "です。"
    known = [v for v in vol["items"] if v["known"]]
    return s + ("直近 30日の平均は、" + "・".join(f"{v['item']} {v['usual']:g}{v['unit']}" for v in known) + "です。" if known else "")


def ai_can_proceed(owner, on_date) -> list:
    """朝に AI が進められる仕事（つながっている入札と、漁協への照会だけ・品目の順）。未接続の入札は言わない（SAL 2026-11-04）。"""
    out = []
    for o in check_sheet(owner, on_date)["orders"]:
        w = (f"{o['item']}の入札" if o.get("connected") else None) if o["tender"] else f"{o['item']}の照会"
        if w and w not in out:
            out.append(w)
    return out


def morning(owner: str, on_date: str = sc.DEMO_DATE) -> dict:
    """今日の仕事。並べ方（SAL 2026-11-04）：手の要る順に並べ、工程の名前を小さく添える。件数 0 の仕事は出さない。
    並べる鍵：(待つ相手の区分, 締切, 社長の指示に関わるか, 工程の順)
      待つ相手の区分 0＝今あなたが決める仕事か、締切のある仕事（締切の早い順）／1＝AI に任せてよい仕事／2＝社長の返事を待つ仕事（あなたが今答える仕事ではない）
    """
    con = db.connect(owner)
    try:
        items = [dict(r) for r in con.execute("SELECT DISTINCT i.id, i.code, i.name, i.unit FROM order_book o JOIN item i ON i.id = o.item_id "
                                              "WHERE o.order_date = ? ORDER BY i.id", (on_date,))]
        n_orders = con.execute("SELECT count(*) FROM order_book WHERE order_date = ?", (on_date,)).fetchone()[0]
        dirs = agents.directives(con, None, on_date)
        dele = con.execute("SELECT id FROM delegation WHERE trade_date = ? ORDER BY id DESC LIMIT 1", (on_date,)).fetchone()
        dir_customers = {d["customer"] for d in dirs}
    finally:
        con.close()
    jobs = []

    def add(key, step, title, count, reason, who, dest, deadline=None, directive=False, connected=True, done=False, rank=None):
        if count <= 0:
            return
        jobs.append(dict(key=key, step=step, rank=STEP_RANK[step] if rank is None else rank, title=title, count=count, reason=reason, who=who,
                         who_label={"together": "一緒に確認", "you": "あなたが決める", "ai": "AI に任せてよい", "president": "社長を待つ"}[who],
                         dest=dest, deadline=deadline, directive=directive, connected=connected, done=done))
    # 注文と指示の確認は「一緒に確認」（配分の判断＝あなたが決める、と分ける・SAL 2026-11-04）
    add("check", "注文の確認", "今朝の注文と社長の指示を確かめる", n_orders + len(dirs), f"注文 {n_orders}件・社長の指示 {len(dirs)}件", "together", "check",
        done=dele is not None)
    for it in items:
        name, u = _short_name(it["name"]), sc.unit_label(it["unit"])
        if tender_item(owner, it["code"]):
            t = tender_status(owner, it["code"], on_date)
            q = sum(o["quantity"] for o in t["orders"])
            add(f"tender:{it['code']}", "入札", f"{name} {q}{u}の入札", q,
                ("条件内で入札する" + ("" if t["connected"] else "（未接続）")), "ai", it["code"], deadline=t["closes_at"],
                directive=any(o["customer"] in dir_customers for o in t["orders"]), connected=t["connected"])
            continue
        f = facts(owner, it["code"], on_date)
        ex, _ = exec_state(f)
        short = _short_total(f)
        shops = "・".join(f"{o['shop']} {o['quantity']}{u}" for o in f["orders"])
        directive = any(o["customer"] in dir_customers for o in f["orders"])
        if ex == "your_turn":
            add(f"ask:{it['code']}", "振分け・承認", f"{name}：AI の相談に答える", 1,
                "AI があなたの判断を待っています", "you", it["code"], directive=directive, rank=1 if f["cur"].get("waiting_for") else 2)
        if ex == "president":
            add(f"pres:{it['code']}", "振分け・承認", f"{name}：社長の確認", 1, "社長の答えを待っています", "president", it["code"], directive=directive, rank=1)
        add(f"buy:{it['code']}", "不足分の手配", f"{name} {short}{u}の買い付け（照会）", short, f"{shops}・あと {short}{u}", "ai", it["code"], directive=directive)
        spare = sum(s["quantity"] for s in f["cur"].get("stock") or [])
        add(f"spare:{it['code']}", "振分け・承認", f"{name}：引当のない在庫 {spare}{u}の扱い", spare, "売り先の当てのない在庫です",
            "you" if f["cur"].get("stock_question") else "ai", it["code"])
        reg = sum(o["registered"] for o in f["orders"])
        add(f"sort:{it['code']}", "振分け・承認", f"{name} {reg}{u}を注文ごとに分ける", reg, "登録した仕入れ（振分けの処理は未接続）", "ai", it["code"], connected=False)
    cls = {"together": 0, "you": 0, "ai": 1, "president": 2}

    def key(j):
        c = cls[j["who"]]
        if j["deadline"] and c == 1:
            c = 0
        return (j["done"], c, _t(j["deadline"]) if j["deadline"] else 10 ** 12, not j["directive"], j["rank"])
    jobs.sort(key=key)
    return dict(owner=owner, date=on_date, bands=[band(owner, it["code"], on_date) for it in items], jobs=jobs, delegated=dele is not None, clock=clock(owner, on_date),
                rule="手の要る順（あなたが決める仕事と締切のある仕事 → AI に任せてよい仕事 → 社長を待つ仕事）。同じ区分は締切・社長の指示・工程の順")


def check_sheet(owner: str, on_date: str = sc.DEMO_DATE) -> dict:
    """「一緒に確認する」で出す今朝の注文と社長の指示（委任の時に、確かめた番号として送り返す）。"""
    con = db.connect(owner)
    try:
        orders = [dict(order_id=r["id"], shop=r["shop_name"] or r["customer"], item=_short_name(r["iname"]), quantity=r["quantity"],
                       unit=sc.unit_label(r["unit"]), tender=False, item_code=r["code"])
                  for r in con.execute("SELECT o.id, o.quantity, c.name AS customer, c.shop_name, i.name AS iname, i.unit, i.code FROM order_book o "
                                       "JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id "
                                       "WHERE o.order_date = ? AND o.status IN ('open', 'partly_filled') ORDER BY o.id", (on_date,))]
        dirs = agents.directives(con, None, on_date)
        staff = con.execute("SELECT max_trade_yen FROM authority WHERE role = 'staff'").fetchone()
    finally:
        con.close()
    for o in orders:
        if tender_item(owner, o["item_code"]):
            t = tender_status(owner, o["item_code"], on_date)
            o.update(tender=True, closes_at=t["closes_at"], connected=t["connected"])
    return dict(orders=orders, directives=dirs, scopes=[dict(code=k, label=v) for k, v in sc.DELEGATION_SCOPES.items()],
                ask_president=["得意先の優先", "権限の外の承認" + (f"（若い担当者は 1件 {_yen(staff[0])}まで）" if staff and staff[0] else "")])


def delegate(con, on_date: str, scope: list, checked: dict, key: str, role: str = "staff") -> dict:
    """担当者が確かめて、任せる範囲を記録する。確かめた注文と指示が今の記録と違えば止める（確かめた後に変わった）。"""
    prev = con.execute("SELECT id, scope_json FROM delegation WHERE idempotency_key = ?", (key,)).fetchone()
    if prev is not None:
        return dict(result="delegated", delegation_id=prev["id"], repeated=True)
    try:
        authority.limit_for(con, role)
    except authority.AuthorityError as e:
        raise MateError(str(e)) from None
    scope = list(dict.fromkeys(scope or []))
    if not scope or any(s not in sc.DELEGATION_SCOPES for s in scope):
        raise MateError(f"任せる範囲は {', '.join(sc.DELEGATION_SCOPES)} から選んでください")
    orders = {r[0] for r in con.execute("SELECT id FROM order_book WHERE order_date = ? AND status IN ('open', 'partly_filled')", (on_date,))}
    dirs = {r[0] for r in con.execute("SELECT id FROM directive WHERE trade_date = ? AND status = 'active'", (on_date,))}
    if set(checked.get("orders") or []) != orders or set(checked.get("directives") or []) != dirs:
        raise MateError("確かめた後に、注文か社長の指示が変わりました。もう一度確かめてください")
    at = db.now_iso()
    with con:
        did = con.execute("INSERT INTO delegation (trade_date, scope_json, checked_json, granted_by, granted_at, idempotency_key) VALUES (?, ?, ?, ?, ?, ?)",
                          (on_date, json.dumps(scope), json.dumps(dict(orders=sorted(orders), directives=sorted(dirs))), role, at, key)).lastrowid
        con.execute("INSERT INTO audit_event (at, actor, event, detail) VALUES (?, 'owner', 'delegated', ?)",
                    (at, json.dumps(dict(delegation_id=did, scope=scope, role=role), ensure_ascii=False)))
    return dict(result="delegated", delegation_id=did, repeated=False)
