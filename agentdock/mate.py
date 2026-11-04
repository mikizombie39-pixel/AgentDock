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

from agentdock import agents, authority, consult, criteria, db, market, work
from agentdock.seed import scenario as sc

STANCE_LABEL = {"bold": "強気", "struggle": "苦戦", "careful": "慎重", "sorry": "残念", "relief": "安堵"}
STANCE_RULE = {"bold": "別の仕入先がある", "struggle": "選択肢が少なく、上限を超える", "careful": "注文や条件が変わった",
               "sorry": "交渉が成立しなかった", "relief": "必要な数量と条件がそろった"}
EXEC_LABEL = {"before_start": "始める前", "running": "実行中", "your_turn": "あなたの判断待ち", "president": "社長確認中",
              "stopped": "停止", "registered": "登録済み", "idle": "見張り中"}
STEPS = ("注文を確認", "買い付け", "振分け", "出荷")
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
                acts=acts, messages=msgs, posts=posts, holds=holds, vault=vault, authority=auth, worker_error=worker_error)


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
    return None if later or presented else d


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
        a = authority.check(con, "staff", pu["seller"], case["item_id"], case["trade_date"], pu["amount_yen"])
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
    if mid:
        return "careful", STANCE_RULE["careful"], dict(orders=[o["order_id"] for o in mid])
    if run and run["kind"] == "human_choice" and run["source"] == "consultation":
        return "careful", STANCE_RULE["careful"], dict(changed_by="consultation")
    d = _last_decline(f)
    if d and short > 0:
        return "sorry", STANCE_RULE["sorry"], dict(message=d["id"])
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


DIGITS = re.compile(r"[0-9０-９]")


def _ai_question(f):
    """今の版の AI の短い問い（AI が書いた文・記録の番号つき）。
    数字を含む AI の文は画面の語りかけに使わない（取引の整合性：金額と数量はコードが業務 DB から出す）。
    使わなかった時は (None, 使わなかった文と記録の番号) を返し、理由の1枚に出所を残す。"""
    cur = f["cur"]
    if cur.get("waiting_for") != "staff" or not f["presents"]:
        return None, None
    t = f["presents"][-1]
    lay = json.loads(t["layout_json"] or "{}")
    q = lay.get("short_question") or lay.get("ask_human")
    if not q:
        return None, None
    ref = dict(db="owner", table="workspace_turn", id=t["id"])
    if DIGITS.search(q):
        return None, dict(text=q, ref=ref, reason="数字を含む AI の文は使わず、コードの計算の文に替えました")
    return _said(q, "ai", ref), None


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
        used_terms = {json.loads(r[0]).get("buy_term_id") for r in con.execute(
            f"SELECT plan_json FROM board_option WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else set()
    finally:
        con.close()
    # どの案にも入っていない仮押さえ：1つの案で仕入れる条件は1つだけの作り（present_options の buy_term_id は1件・登録も1件）
    unused = [h for h in held if h["term_id"] not in used_terms] if f["cur"].get("options") else []
    for no, o in enumerate(f["cur"].get("options") or [], 1):
        n = o["numbers"]
        if n["purchase"]:
            p = n["purchase"]
            label = f"{p['seller']} {p['quantity']}{u}：" + "・".join(f"{shops.get(l['to'], l['to'])} {l['quantity']}{u}" for l in n["lines"])
        else:
            label = "見送る（仕入れない）"
        short_after = [dict(shop=shops.get(a["customer"], a["customer"]), quantity=a["short_after"]) for a in o["after"] if a["short_after"] > 0]
        options.append(dict(option_id=o["option_id"], code=o["code"], no=no, label=label, short_after=short_after,
                            note=("この案では足りない：" + "・".join(f"{s['shop']} {s['quantity']}{u}" for s in short_after)) if short_after else "全部の注文がそろう"))
    held_text = "・".join(f"{h['name']} {h['quantity']}{u}" for h in held)
    line = (f"注文は合わせて {total}{u}。登録済み {registered}{u}・仮押さえ {held_total}{u}" + (f"（{held_text}）" if held else "") +
            (f"で、あと {left}{u}足りません。" if left else "で、数はそろっています。"))
    unused_text = ("・".join(f"{h['name']}の {h['quantity']}{u}" for h in unused) + "（仮押さえ）は、どの案にも入っていません。"
                   "今の作りでは、1つの案で仕入れる相手は1つだけです。") if unused else None
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
    aiq, replaced = _ai_question(f)
    mid = _mid_orders(f)
    sup = _suppliers(f)
    tl = tally(f)

    if ex == "before_start":
        vol = order_volume(owner, on_date)
        head = _said("おはようございます。" + ("今日は注文が増えています。" if vol["more"] else "今日の注文を確かめました。"))
        sub = [_said("まず注文と社長の指示を確認しましょう。その後の入札と照会は私が進めます。配分と購入の判断は、必要な時に相談します。")]
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
                orgs=list(orgs.values()), net=net, op=_op(f, ex), president=pres, busy=_running(f) is not None)


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
            acts.append(dict(id=f"opt{o['option_id']}", label=("社長に確かめる：" + label) if pv["needs"] else label,
                             kind="primary" if o["recommended"] else "secondary", recommended=o["recommended"], short_after=c["short_after"],
                             note="・".join(tags),
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
        return acts, _said(q["text"], "ai", dict(db="owner", table="stock_question", id=q["question_id"]))
    d = next(d for d in cur.get("drafts") or [] if d["status"] == "draft")
    acts = [dict(id=f"dr{d['draft_id']}a", label="この文で伝える", kind="primary", api=dict(path="/api/draft", body=dict(draft_id=d["draft_id"], action="approve", role="staff"))),
            dict(id=f"dr{d['draft_id']}d", label="使わない", kind="secondary", api=dict(path="/api/draft", body=dict(draft_id=d["draft_id"], action="discard", role="staff")))]
    return acts, _said(f"{d['customer']}への連絡の下書きです（AI が作成・まだ送っていません）：{d['body']}", "ai", dict(db="owner", table="customer_draft", id=d["draft_id"]))


# ── 理由の1枚（仲間に触れる・「なぜ？」で開く。判断に要る順）────────────────

def _row(text, origin, at=None, ref=None):
    return dict(text=text, origin=origin, at=at, ref=ref)


ORIGIN_LABEL = {"reply": "相手から届いた回答", "calc": "コードの計算", "vault": "Vault の記録", "candidate": "まだ確認していない候補",
                "directive": "社長の指示（記録）", "record": "業務の記録", "ai": "AI が書いた文", "code": "コードの決まった文"}


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
        opts.append(_row(f"案{c['no']}：{c['label']}" + (f"（仕入れ {_yen(n['purchase_yen'])}）" if n["purchase"] else "") + f"・{c['note']}" +
                         ("" if o["executable"] else "（実行できません）"), "calc"))
    vault = [_row(f"{v['title']}（{v['author']}・{v['written_on']}）", "vault", None, dict(db="owner", table="vault_note", id=v["id"])) for v in f["vault"]]
    history = asked + [r for r in replies]

    # 先頭の一文（今の判断の理由）
    lead = None
    others_short = [o for o in f["orders"] if o["short"] > 0 and o["shop"] != ds]
    if ex == "before_start":
        lead = "今朝の注文と社長の指示を確かめてから、照会と入札を私が進めます。"
        vol = order_volume(f["owner"], f["on_date"])
        orders = orders + [_row(f"今朝の注文の数量 {vol['today']}（同じ売り先・品目のふだん {vol['usual']}・直近 30日の平均）", "calc")]
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
        if ds and others_short and ds_short:
            lead = (f"{ds}を優先するのは、社長の朝の指示があるためです。"
                    f"{'・'.join(o['shop'] for o in others_short)}を優先するには、社長への確認が必要です。")
        elif st == "careful":
            lead = "注文や条件が変わったので、足りない数と相手の回答を確かめ直しています。"
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
        lead = "相手から断りの返事が届いたためです。ほかに当たれる先を探します。"
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
    keep = lambda xs: [dict(title=t, rows=r) for t, r in xs if r]   # noqa: E731
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
        elif k == "needs_president":
            text = f"受け取りました。{PRESIDENT}に確認を送りました。答えが来たら続けます。"
        elif k == "rejected":
            text = "受け取りました。この案は見送りました。" + rest
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
        out = dict(at=last["at"], text=text, ref=dict(db="owner", table="case_decision" if k in ("registered", "needs_president", "rejected", "stale") else
                                                       ("human_decision" if k == "decision" else "stock_question"), id=last["id"]))
    after = [r for r in f["runs"] if r["ended_at"] and _t(r["started_at"]) >= _t(out["at"]) and r["summary"] and r["outcome"] not in STOP_OUTCOMES]
    if after:
        out["ai"] = _said(after[-1]["summary"], "ai", dict(db="owner", table="agent_run", id=after[-1]["id"]))
    return out


def _fresh_receipt(f):
    """受け取った内容は、その後に AI が新しい相談を出すまで見せる（古い受領を次の判断の横に残さない）。"""
    r = receipt(f)
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
                receipt=None, taken=[], others=[])


# ── 今日の仕事の流れ（今日の記録から、コードの決まりで並べる）──────────────────

def order_volume(owner, on_date) -> dict:
    """今日の注文の数量と、同じ売り先・品目のふだんの数量（直近 30日に注文のあった日の平均・売れ方の記録から）。"""
    con = db.connect(owner)
    try:
        start = (datetime.date.fromisoformat(on_date) - datetime.timedelta(days=30)).isoformat()
        today, usual = 0, 0.0
        for r in con.execute("SELECT customer_id, item_id, sum(quantity) FROM order_book WHERE order_date = ? GROUP BY 1, 2", (on_date,)):
            today += r[2]
            a = con.execute("SELECT avg(ordered_qty) FROM sales_history WHERE customer_id = ? AND item_id = ? AND sale_date >= ? AND sale_date < ?",
                            (r[0], r[1], start, on_date)).fetchone()[0]
            usual += a or 0
    finally:
        con.close()
    return dict(today=today, usual=round(usual, 1), more=today > usual)


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

    def add(key, step, title, count, reason, who, dest, deadline=None, directive=False, connected=True, done=False):
        if count <= 0:
            return
        jobs.append(dict(key=key, step=step, title=title, count=count, reason=reason, who=who,
                         who_label={"you": "あなたが決める", "ai": "AI に任せてよい", "president": "社長を待つ"}[who],
                         dest=dest, deadline=deadline, directive=directive, connected=connected, done=done))
    add("check", "注文を確認", "今朝の注文と社長の指示を確かめる", n_orders + len(dirs), f"注文 {n_orders}件・社長の指示 {len(dirs)}件", "you", "check",
        done=dele is not None)
    for it in items:
        name, u = _short_name(it["name"]), sc.unit_label(it["unit"])
        if tender_item(owner, it["code"]):
            t = tender_status(owner, it["code"], on_date)
            q = sum(o["quantity"] for o in t["orders"])
            add(f"tender:{it['code']}", "買い付け", f"{name} {q}{u}の入札", q,
                ("条件内で入札する" + ("" if t["connected"] else "（未接続）")), "ai", it["code"], deadline=t["closes_at"],
                directive=any(o["customer"] in dir_customers for o in t["orders"]), connected=t["connected"])
            continue
        f = facts(owner, it["code"], on_date)
        ex, _ = exec_state(f)
        short = _short_total(f)
        shops = "・".join(f"{o['shop']} {o['quantity']}{u}" for o in f["orders"])
        directive = any(o["customer"] in dir_customers for o in f["orders"])
        if ex == "your_turn":
            add(f"ask:{it['code']}", "買い付け" if f["cur"].get("waiting_for") else "振分け", f"{name}：AI の相談に答える", 1,
                "AI があなたの判断を待っています", "you", it["code"], directive=directive)
        if ex == "president":
            add(f"pres:{it['code']}", "買い付け", f"{name}：社長の確認", 1, "社長の答えを待っています", "president", it["code"], directive=directive)
        add(f"buy:{it['code']}", "買い付け", f"{name} {short}{u}の買い付け（照会）", short, f"{shops}・あと {short}{u}", "ai", it["code"], directive=directive)
        spare = sum(s["quantity"] for s in f["cur"].get("stock") or [])
        add(f"spare:{it['code']}", "振分け", f"{name}：引当のない在庫 {spare}{u}の扱い", spare, "売り先の当てのない在庫です",
            "you" if f["cur"].get("stock_question") else "ai", it["code"])
        reg = sum(o["registered"] for o in f["orders"])
        add(f"sort:{it['code']}", "振分け", f"{name} {reg}{u}を注文ごとに分ける", reg, "登録した仕入れ（振分けの処理は未接続）", "ai", it["code"], connected=False)
    cls = {"you": 0, "ai": 1, "president": 2}

    def key(j):
        c = cls[j["who"]]
        if j["deadline"] and c == 1:
            c = 0
        return (j["done"], c, _t(j["deadline"]) if j["deadline"] else 10 ** 12, not j["directive"], STEPS.index(j["step"]))
    jobs.sort(key=key)
    return dict(owner=owner, date=on_date, steps=list(STEPS), jobs=jobs, delegated=dele is not None,
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
