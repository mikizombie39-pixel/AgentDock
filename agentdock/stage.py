"""Adaptive Workspace の段階と経過：業務 DB の事実から、コードが決める（演出の台本は使わない）。

- 段階（待機・注文・情報収集・条件整理・判断・正式処理・完了・例外）は、出来事・Claude の起動・案件の状態から決める
- 経過（画面に現れるカードと、組織の間を動く情報）は、実際に記録された行だけから作る
- 網谷水産が見てよい事実だけを使う（他社どうしのやり取り・他社の DB は読まない）
"""
import datetime
import json

from agentdock import db, network, runlog
from agentdock.seed import scenario as sc

STAGES = ("idle", "order", "gathering", "organizing", "decision", "processing", "done", "exception")
STAGE_LABEL = {"idle": "待機", "order": "注文", "gathering": "情報収集", "organizing": "条件整理", "decision": "判断",
               "processing": "正式処理", "done": "完了", "exception": "例外"}
STEP_LABEL = {"buy_trade": "仕入れの取引を市場の取引帳簿に登録", "seller_stock": "漁協の在庫と仮押さえを確定",
              "terms": "選ばなかった条件と仮押さえを取り消し", "sell_trades": "B社への売りを市場の取引帳簿に登録",
              "buyer_side": "B社の注文と在庫に反映", "own_books": "自社の帳簿（在庫・注文）に反映"}
LOOK = {"read_orders": "注文と仕入れ値の上限を確かめました", "read_market": "相場を確かめました", "read_material": "漁協の今日の資料を読みました",
        "read_customers": "売り先の記録を確かめました", "read_posts": "仲買の掲示を確かめました", "read_thread": "今日のやり取りを確かめました"}
DOING = {"read_orders": "注文を確かめています", "read_market": "相場を確かめています", "read_material": "漁協の資料を読んでいます",
         "read_vault": "社長の経験を参照しています", "read_customers": "売り先の記録を確かめています", "read_posts": "仲買の掲示を確かめています",
         "read_thread": "やり取りを確かめています", "send_inquiry": "問い合わせを送りました", "compare_options": "案ごとの金額をコードで比べています",
         "present_options": "判断の材料をまとめています", "answer_staff": "答えをまとめています", "reply": "返事を送りました"}
WAKE_TEXT = {"order_new": "注文を受けて、Claude が仕事を始めました", "quote_received": "返事を受けて、Claude が続きを進めます",
             "info_received": "新しい情報を受けて、Claude が確かめています", "quote_request": "問い合わせを受けて、Claude が確かめています",
             "staff_question": "質問を受けて、Claude が業務の状態を確かめ直しています",
             "human_choice": "条件が変わったので、Claude が調べ直しています"}
OWNER = "amiya"


def _name(code: str) -> str:
    return sc.OWNERS.get(code, {}).get("name", code)


def _unit(con, item_code: str) -> str:
    r = con.execute("SELECT unit FROM item WHERE code = ?", (item_code,)).fetchone()
    return sc.unit_label(r[0]) if r else ""


def _ts(s: str) -> float:
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _items(con, m) -> list[dict]:
    out = []
    ev_kind = {}
    for e in con.execute("SELECT id, at, kind, payload_json FROM biz_event ORDER BY id"):
        p = json.loads(e["payload_json"])
        ev_kind[e["id"]] = e["kind"]
        k = f"ev:{e['id']}"
        if e["kind"] == "order_new":
            it = con.execute("SELECT name, unit FROM item WHERE code = ?", (p["item"],)).fetchone()
            out.append(dict(key=k, at=e["at"], kind="order", ref=p["order_id"], item=p["item"],
                            text=f"新しい注文を確認しました：{p['customer']}から {it['name'] if it else p['item']} {p['quantity']}{sc.unit_label(it['unit']) if it else ''}"))
        elif e["kind"] == "info_received" and p.get("source") == "broadcast":
            out.append(dict(key=k, at=e["at"], kind="packet", frm=p.get("from_owner", "iwase"), to=OWNER, text="岩瀬漁協から今日の資料が届きました"))
        elif e["kind"] == "info_received" and p.get("source") == "deal_request":
            try:
                r = network.request_for(OWNER, p["request_id"])
                side = "買いたい" if r["side"] == "buy" else "譲りたい"
                out.append(dict(key=k, at=e["at"], kind="packet", frm=r["owner"], to=OWNER,
                                text=f"{_name(r['owner'])}の掲示が届きました（{side}・{r['quantity']}{_unit(con, r['item_code'])}）"))
            except network.NetworkError:
                pass
        elif e["kind"] == "staff_question":
            out.append(dict(key=k, at=e["at"], kind="question", text=p["text"], ref=p.get("case_id")))
        elif e["kind"] == "human_choice" and p.get("source") == "consultation":
            who = "社長" if p.get("answered_by") == "president" else "担当者"
            out.append(dict(key=k, at=e["at"], kind="change", text=f"{who}が条件を変えました（仕入れの数量 {p['change']['quantity']}）"))
        elif e["kind"] == "human_choice" and p.get("source") == "stock_question":
            who = "社長" if p.get("answered_by") == "president" else "担当者"
            out.append(dict(key=k, at=e["at"], kind="change", text=f"{who}が引当のない在庫の扱いを答えました（{p['label']}）"))
        elif e["kind"] == "human_choice":
            out.append(dict(key=k, at=e["at"], kind="exception", text="承認の時の確かめ直しで確定できませんでした：" + "・".join(p.get("reasons", []))))
    for r in con.execute("SELECT id, event_id, started_at, ended_at, outcome FROM agent_run ORDER BY id"):
        kind = ev_kind.get(r["event_id"])
        if kind in WAKE_TEXT and r["outcome"] != "nothing":
            out.append(dict(key=f"run:{r['id']}", at=r["started_at"], kind="wake", text=WAKE_TEXT[kind], ref=r["id"]))
        if r["outcome"] in ("error", "max_turns", "refusal"):
            out.append(dict(key=f"runend:{r['id']}", at=r["ended_at"] or r["started_at"], kind="error",
                            text="Claude の処理が途中で止まりました（" + r["outcome"] + "）。出来事は待ちに戻しました"))
    for s in con.execute("SELECT run_id, seq, tool, input_json, result_text, is_error, at FROM agent_step ORDER BY run_id, seq"):
        k = f"step:{s['run_id']}:{s['seq']}"
        inp = json.loads(s["input_json"] or "{}")
        if s["is_error"]:
            out.append(dict(key=k, at=s["at"], kind="retry", text=f"決まりに合わない入力をコードが止め、Claude が選び直しました（{s['tool']}）"))
        elif s["tool"] == "read_vault":
            try:
                notes = [dict(title=n["title"], applies_when=n["applies_when"], author=n["author"])
                         for n in json.loads(s["result_text"])["notes"]][:3]
            except (ValueError, KeyError, TypeError):
                notes = []
            out.append(dict(key=k, at=s["at"], kind="vault", text="社長の過去の経験（Vault）を参照しました", notes=notes))
        elif s["tool"] in LOOK:
            out.append(dict(key=k, at=s["at"], kind="look", text=LOOK[s["tool"]]))
        elif s["tool"] == "compare_options":
            out.append(dict(key=k, at=s["at"], kind="compare", text=f"案ごとの金額をコードで計算して比べました（{len(inp.get('options') or [])}案）"))
        elif s["tool"] == "wait_for_replies":
            out.append(dict(key=k, at=s["at"], kind="wait", text="相手の返事を待っています"))
        elif s["tool"] == "present_options":
            out.append(dict(key=k, at=s["at"], kind="present", text="判断の材料がそろいました"))
        elif s["tool"] == "answer_staff":
            out.append(dict(key=k, at=s["at"], kind="answer", text=inp.get("answer") or "", focus=inp.get("focus")))
    for msg in m.execute("SELECT * FROM network_message WHERE from_owner = ? OR to_owner = ? ORDER BY id", (OWNER, OWNER)):
        terms = network.terms_of(dict(msg))
        what = {"inquiry": "問い合わせ", "offer": "条件の返事", "decline": "断りの返事"}[msg["kind"]]
        qs = "・".join(f"{q}{_unit(con, msg['item_code'])}" for q, _ in terms) if terms else ""
        out.append(dict(key=f"msg:{msg['id']}", at=msg["created_at"], kind="packet", frm=msg["from_owner"], to=msg["to_owner"], msg_kind=msg["kind"],
                        text=f"{_name(msg['from_owner'])} → {_name(msg['to_owner'])}：{what}" + (f"（{qs}）" if qs else "")))
    for d in con.execute("SELECT id, action, at, result FROM case_decision ORDER BY id"):
        out.append(dict(key=f"dec:{d['id']}", at=d["at"], kind="decision", action=d["action"], result=d["result"],
                        text={"approve": "担当者が承認しました", "hold": "担当者が保留にしました", "reject": "担当者が見送りました"}[d["action"]]))
    for a in con.execute("SELECT id, at, event, detail FROM audit_event WHERE event IN ('case_step', 'market_api', 'case_registered', 'case_incomplete') ORDER BY id"):
        d = json.loads(a["detail"])
        if a["event"] == "case_step":
            out.append(dict(key=f"au:{a['id']}", at=a["at"], kind="step", step=d["step"], text=STEP_LABEL.get(d["step"], d["step"])))
        elif a["event"] == "market_api":
            both = d.get("seller_booked") and d.get("buyer_booked")
            out.append(dict(key=f"au:{a['id']}", at=a["at"], kind="api", text=f"市場の取引帳簿に登録しました（{d['trade_id']}・"
                            + ("売り手と買い手の両方の帳簿" if both else "帳簿は片側だけ") + "）", calls=d.get("calls")))
        elif a["event"] == "case_registered":
            out.append(dict(key=f"au:{a['id']}", at=a["at"], kind="done", text="取引が完了しました"))
        else:
            out.append(dict(key=f"au:{a['id']}", at=a["at"], kind="error", text="確定の途中で止まりました：" + str(d.get("error"))))
    out.sort(key=_order)
    return out


_RANK = {"ev": 0, "run": 1, "step": 2, "msg": 3, "runend": 4, "dec": 5, "au": 6}


def _order(x: dict):
    """時刻の順。同じ秒の中は、出来事 → 起動 → 道具 → 文 → 判断 → 確定の手順の順で、番号は数として比べる。"""
    prefix, *nums = x["key"].split(":")
    return (int(_ts(x["at"])), _RANK.get(prefix, 9), [int(n) for n in nums], _ts(x["at"]))


def _stage(con, items: list[dict]) -> tuple[str, dict | None]:
    run = con.execute("SELECT r.id, e.kind FROM agent_run r LEFT JOIN biz_event e ON e.id = r.event_id "
                      "WHERE r.ended_at IS NULL ORDER BY r.id DESC LIMIT 1").fetchone()
    busy = None
    if run is not None:
        last = con.execute("SELECT tool FROM agent_step WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run["id"],)).fetchone()
        busy = dict(run_id=run["id"], event=run["kind"],
                    text="Claude が" + (DOING.get(last["tool"], "考えています") if last else "状況を読んでいます"))
    case = con.execute("SELECT status FROM deal_case ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()
    if case is not None:
        st = case["status"]
        if st in ("registered", "rejected"):
            return "done", busy
        if st in ("incomplete", "needs_president"):
            return "exception", busy
        if st == "stale":
            return ("organizing" if busy else "exception"), busy
        if st in ("presented", "held"):
            return "decision", busy
        return "organizing", busy
    if not any(i["kind"] == "order" for i in items):
        return "idle", busy
    last_err = con.execute("SELECT outcome FROM agent_run WHERE ended_at IS NOT NULL ORDER BY id DESC LIMIT 1").fetchone()
    if busy is None and last_err is not None and last_err["outcome"] in ("error", "max_turns", "refusal"):
        return "exception", busy
    if not con.execute("SELECT 1 FROM agent_run LIMIT 1").fetchone():
        return "order", busy
    if any(i["kind"] == "packet" and i.get("to") == OWNER and i.get("msg_kind") == "offer" for i in items):
        return "organizing", busy
    return "gathering", busy


def flow(owner: str = OWNER) -> dict:
    """画面の段階と経過。owner は網谷水産だけ（担当者の磁石ボード）。"""
    if owner != OWNER:
        raise ValueError("the workspace is for amiya")
    con = db.connect(owner)
    m = db.connect(db.MARKET)
    try:
        items = _items(con, m)
        stage, busy = _stage(con, items)
    finally:
        con.close()
        m.close()
    orgs = [dict(code=c, name=o["name"], own=c == owner) for c, o in sc.OWNERS.items()]
    return dict(stage=stage, stage_label=STAGE_LABEL[stage], busy=busy, items=items, orgs=orgs)


def verify(owner: str = OWNER) -> dict:
    """技術の検証の画面：Claude の起動と道具の呼び出し・市場の取引帳簿の API と登録の結果・帳簿の照合・費用。"""
    from agentdock import deal
    con = db.connect(owner)
    m = db.connect(db.MARKET)
    try:
        runs = [dict(r) for r in con.execute("SELECT r.*, e.kind AS event_kind FROM agent_run r LEFT JOIN biz_event e ON e.id = r.event_id ORDER BY r.id")]
        steps = [dict(r) for r in con.execute("SELECT run_id, seq, tool, input_json, substr(result_text, 1, 400) AS result_text, is_error, at "
                                              "FROM agent_step ORDER BY run_id, seq")]
        api = [dict(at=r["at"], **json.loads(r["detail"])) for r in con.execute("SELECT at, detail FROM audit_event WHERE event = 'market_api' ORDER BY id")]
        trades = [dict(r) for r in m.execute("SELECT trade_id, seller, buyer, quantity, unit_price_yen, status, created_at FROM trade "
                                             "WHERE seller = ? OR buyer = ? ORDER BY id", (owner, owner))]
        audit = [dict(r) for r in con.execute("SELECT at, actor, event, detail FROM audit_event ORDER BY id DESC LIMIT 60")]
    finally:
        con.close()
        m.close()
    return dict(data_dir=str(db.data_dir()), runs=runs, steps=steps, market_api=api, trades=trades,
                reconcile=deal.reconcile([t["trade_id"] for t in trades]), audit=audit,
                spent_usd=runlog.spent_micro_usd() / 1_000_000, now=db.now_iso())
