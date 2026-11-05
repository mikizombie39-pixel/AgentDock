"""全体の流れ（3者）の画面：網谷水産（若い担当者と AI）・社長・岩瀬漁協（AI とコード）の、いま誰が作業しているかと、いま注目する連絡1本。

決まり（2026-11-05 SAL）：
- 状態は記録から決める。担当者と社長の状態は、仲間の画面と同じ計算（mate.view）から引く
- 社長に確認を送った段階は「社長の判断待ち」（waiting_decision）。「作業中」とは出さない
- 連絡の線は累積させず、記録の中で一番新しい1本だけを focus にする
"""
import datetime
import json

from agentdock import db, mate, settle
from agentdock.seed import scenario as sc

ITEM = "buri_xl"
LANES = ("amiya", "president", "iwase")
STATE_LABEL = {"working": "作業中", "your_turn": "判断の番", "waiting_decision": "社長の判断待ち", "decided": "返事済み", "idle": "待機"}
EV_LABEL = {"order_new": "注文を読み、照会を組んでいます", "quote_received": "届いた返事を読み、案を組んでいます", "human_choice": "人の答えを受けて、組み直しています",
            "trade_registered": "登録を受けて、残りを確かめています", "quote_request": "照会に、在庫と販売の条件を見て答えています", "staff_question": "質問に答えています"}


def _sec(s) -> int:
    try:
        return int(datetime.datetime.fromisoformat(s).timestamp())
    except (TypeError, ValueError):
        return 0


def _running(owner: str):
    c = db.connect(owner)
    try:
        return c.execute("SELECT r.id, b.kind FROM agent_run r LEFT JOIN biz_event b ON b.id = r.event_id WHERE r.ended_at IS NULL ORDER BY r.id DESC LIMIT 1").fetchone()
    finally:
        c.close()


def _member(who, kind, state, doing):
    return dict(who=who, kind=kind, state=state, state_label=STATE_LABEL[state], doing=doing)


def _focus(on_date: str):
    """記録の中で一番新しい連絡1本（照会・返事・社長への確認・差し戻し・登録）。"""
    ev = []
    m = db.connect(db.MARKET)
    try:
        for r in m.execute("SELECT * FROM network_message WHERE ((from_owner = 'amiya' AND to_owner = 'iwase') OR (from_owner = 'iwase' AND to_owner = 'amiya')) "
                           "AND trade_date = ? ORDER BY id", (on_date,)):
            what = {"inquiry": f"照会：ブリ {r['quantity']}本", "offer": "条件の返事", "decline": "断りの返事"}.get(r["kind"], r["kind"])
            terms = [t for t in m.execute("SELECT quantity, unit_price_yen FROM offer_term WHERE message_id = ? ORDER BY quantity DESC LIMIT 1", (r["id"],))]
            if terms:
                what += f"（{terms[0][0]}本 × {terms[0][1]:,}円・仮押さえ）"
            ev.append(dict(at=r["created_at"], order=(0, r["id"]), frm=r["from_owner"], to=r["to_owner"], text=what))
    finally:
        m.close()
    c = db.connect("amiya")
    try:
        for d in c.execute("SELECT * FROM case_decision ORDER BY id"):
            det = json.loads(d["detail"] or "{}")
            if d["result"] == "needs_president":
                ev.append(dict(at=d["at"], order=(1, d["id"]), frm="amiya", to="president", text="社長に確認を送りました"))
            elif d["action"] == "return":
                ev.append(dict(at=d["at"], order=(1, d["id"]), frm="president", to="amiya", text=f"差し戻し：「{det.get('reason') or ''}」"))
            elif d["result"] in ("registered", "partial"):
                ev.append(dict(at=d["at"], order=(1, d["id"]), frm="amiya", to="iwase", text="取引を市場の帳簿に登録しました"))
            elif d["actor"] == "president" and d["action"] == "approve":
                ev.append(dict(at=d["at"], order=(1, d["id"]), frm="president", to="amiya", text="社長が承認しました"))
    finally:
        c.close()
    if not ev:
        return None
    f = max(ev, key=lambda e: (_sec(e["at"]), e["order"]))
    return dict(frm=f["frm"], to=f["to"], text=f["text"], at=f["at"])


def view(on_date: str = sc.DEMO_DATE) -> dict:
    v = mate.view("amiya", ITEM, on_date)
    ex = v["exec"]
    pres = v.get("president")
    am_run, iw_run = _running("amiya"), _running("iwase")
    st = settle.status(on_date)
    busy_jobs = [k for k, j in (st.get("jobs") or {}).items() if j["status"] == "running"]
    job_name = {"reconcile": "せりの結果の照合", "invoice": "仲買への請求の集計", "payout": "荷主への支払の集計"}
    staff = _member("若い担当者", "person", "your_turn" if ex == "your_turn" else "idle",
                    "案を選んでいます" if ex == "your_turn" else ("社長の返事を待っています" if pres else "AI に任せています"))
    ai = _member("網谷水産の AI", "ai", "working" if am_run else "idle", EV_LABEL.get(am_run["kind"], "出来事を確かめています") if am_run else "次の出来事を見張っています")
    # 社長：確認を送られた段階は「判断待ち」。作業中とは出さない
    returned = any(d["action"] == "return" for d in _decisions())
    president = _member("社長（網谷さん）", "person", "waiting_decision" if pres else ("decided" if returned else "idle"),
                        "担当者からの確認が届いています" if pres else ("理由を添えて差し戻しました" if returned else "朝の指示を出しました"))
    coop_ai = _member("岩瀬漁協の AI", "ai", "working" if iw_run else "idle", EV_LABEL["quote_request"] if iw_run else "照会を待っています")
    code = _member("集計（コード）", "code", "working" if busy_jobs else "idle",
                   "・".join(job_name[k] for k in busy_jobs) if busy_jobs else ("今日の集計案がそろいました" if st.get("complete") else "待機"))
    lanes = [dict(key="amiya", title="網谷水産", members=[staff, ai]), dict(key="president", title="社長", members=[president]),
             dict(key="iwase", title="岩瀬漁協", members=[coop_ai, code])]
    f = _focus(on_date)
    if f:
        f["at_shown"] = mate.shown_time(f["at"], v["clock"])
    return dict(date=on_date, lanes=lanes, focus=f, clock=v["clock"], headline=(v.get("headline") or {}).get("text"))


def _decisions():
    c = db.connect("amiya")
    try:
        return [dict(r) for r in c.execute("SELECT action, actor, result, at FROM case_decision ORDER BY id")]
    finally:
        c.close()
