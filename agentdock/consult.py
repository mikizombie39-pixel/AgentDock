"""持ち主への相談と、相談の束（スマホの1枚）。

安全の条件をコードで守る：
- 古い判断：相談は仕事の版に結びつく。条件が変わって版が上がると、前の版の相談は stale になり、答えても通らない
- 二重の回答：同じ idempotency_key の回答は一度しか効かない。同じ回答の繰り返しは同じ結果を返す
- 無回答：答えが無いうちは仕事は awaiting_owner のままで、先へ進めない（時間が来ても勝手に決めない）
- 選べない選択肢：コードが確かめて valid=0 にした選択肢は選べない
- 条件を変える答え（change）：磁石ボードの版ごとの相談で、数量・配分を変える。答えを記録するのと同じトランザクションで
  出来事 human_choice を書き、Claude を起こす（二重の回答・古い版の回答では書かない）
- 顧客の間の優先を変える答えは、権限の表で許された役割（社長）だけが確定できる。担当者の答えは needs_president で止まる
"""
import json

from agentdock import authority, db, events

STATES_WAITING = ("awaiting_owner",)
CASE_OPTIONS = [("change", "数量・配分を変えて交渉し直す", True)]
CASE_OPEN = ("presented", "held", "stale", "needs_president")   # 条件を変えてよい案件の状態（確定・見送りの後は変えない）


class ConsultError(Exception):
    pass


class StaleError(ConsultError):
    pass


def _audit(con, actor: str, event: str, task_id=None, detail=None):
    con.execute("INSERT INTO audit_event (at, actor, event, task_id, detail) VALUES (?, ?, ?, ?, ?)",
                (db.now_iso(), actor, event, task_id, detail))


def open_task(con, kind: str, item_code: str) -> int:
    iid = con.execute("SELECT id FROM item WHERE code = ?", (item_code,)).fetchone()
    if iid is None:
        raise KeyError(item_code)
    with con:
        cur = con.execute("INSERT INTO task (kind, item_id, state, version, updated_at) VALUES (?, ?, 'running', 1, ?)",
                          (kind, iid[0], db.now_iso()))
        _audit(con, "companion", "task_opened", cur.lastrowid, f"{kind} {item_code}")
    return cur.lastrowid


def task(con, task_id: int):
    row = con.execute("SELECT id, kind, state, version FROM task WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError(task_id)
    return row


def bundle(con, title: str, asks: list[dict]) -> int:
    """複数の相談を1枚にまとめて出す。asks は [{task_id, question, options: [(code, label, valid)]}]。
    1件でも出せなければ、どれも出さない。"""
    if not asks:
        raise ConsultError("empty bundle")
    now = db.now_iso()
    with con:
        gid = con.execute("INSERT INTO consultation_group (created_at, title, status) VALUES (?, ?, 'open')",
                          (now, title)).lastrowid
        for a in asks:
            t = task(con, a["task_id"])
            if t["state"] in ("done", "no_deal", "incomplete"):
                raise ConsultError(f"task {t['id']} is closed: {t['state']}")
            if not any(v for _, _, v in a["options"]):
                raise ConsultError(f"task {t['id']} has no valid option")
            cid = con.execute(
                "INSERT INTO consultation (group_id, task_id, task_version, question, status) VALUES (?, ?, ?, ?, 'open')",
                (gid, t["id"], t["version"], a["question"])).lastrowid
            con.executemany("INSERT INTO consultation_option (consultation_id, code, label, valid) VALUES (?, ?, ?, ?)",
                            [(cid, c, label, 1 if v else 0) for c, label, v in a["options"]])
            con.execute("UPDATE task SET state = 'awaiting_owner', updated_at = ? WHERE id = ?", (now, t["id"]))
            _audit(con, "companion", "consultation_opened", t["id"], f"group {gid} v{t['version']}")
    return gid


def _refresh_group(con, gid):
    if gid is None:
        return
    st = [r[0] for r in con.execute("SELECT status FROM consultation WHERE group_id = ?", (gid,))]
    status = "answered" if all(s == "answered" for s in st) else ("stale" if "stale" in st and "open" not in st else "open")
    con.execute("UPDATE consultation_group SET status = ? WHERE id = ?", (status, gid))


def answer(con, consultation_id: int, option_code: str, idempotency_key: str, reason_text: str | None = None,
           change: dict | None = None, role: str = "staff") -> dict:
    """持ち主の回答。結果の dict を返す（duplicate=True は同じ回答の繰り返し）。
    option_code が 'change' の時は change（数量・配分）を渡す。role は答えた人の役割（権限の表の role）。"""
    prev = con.execute("SELECT id, chosen_option_id, status FROM consultation WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if prev is not None:
        code = con.execute("SELECT code FROM consultation_option WHERE id = ?", (prev["chosen_option_id"],)).fetchone()[0]
        if prev["id"] != consultation_id or code != option_code:
            raise ConsultError("idempotency_key reused for a different answer")
        out = dict(consultation_id=consultation_id, option=code, duplicate=True)
        if code == "change":
            out["result"] = prev["status"]
        return out

    c = con.execute("SELECT id, group_id, task_id, task_version, status, case_id FROM consultation WHERE id = ?", (consultation_id,)).fetchone()
    if c is None:
        raise KeyError(consultation_id)
    t = task(con, c["task_id"])
    if c["status"] == "stale" or t["version"] != c["task_version"]:
        with con:
            con.execute("UPDATE consultation SET status = 'stale' WHERE id = ?", (c["id"],))
            _refresh_group(con, c["group_id"])
            _audit(con, "system", "stale_answer_rejected", t["id"], f"consultation {c['id']} v{c['task_version']} != v{t['version']}")
        raise StaleError(f"consultation {c['id']} is for v{c['task_version']}, task is v{t['version']}")
    if c["status"] != "open" and not (c["status"] == "needs_president" and option_code == "change"):
        raise ConsultError(f"consultation {c['id']} is {c['status']}")
    opt = con.execute("SELECT id, valid FROM consultation_option WHERE consultation_id = ? AND code = ?",
                      (c["id"], option_code)).fetchone()
    if opt is None:
        raise ConsultError(f"no option {option_code}")
    if not opt["valid"]:
        raise ConsultError(f"option {option_code} is not allowed")
    if option_code == "change":
        return _answer_change(con, c, opt["id"], idempotency_key, reason_text, change, role)
    now = db.now_iso()
    with con:
        con.execute("UPDATE consultation SET status = 'answered', chosen_option_id = ?, reason_text = ?, answered_at = ?, idempotency_key = ? WHERE id = ?",
                    (opt["id"], reason_text, now, idempotency_key, c["id"]))
        con.execute("UPDATE task SET state = 'resumed', updated_at = ? WHERE id = ?", (now, t["id"]))
        _refresh_group(con, c["group_id"])
        _audit(con, "owner", "consultation_answered", t["id"], f"consultation {c['id']} {option_code}")
    return dict(consultation_id=c["id"], option=option_code, duplicate=False)


def open_for_case(con, case_id: int, item_id: int, version: int, question: str) -> int:
    """磁石ボードの版ごとの相談を開く（担当者が数量・配分を変える入口）。呼ぶ側のトランザクションの中で書く。
    案件の仕事の版をボードの版にそろえ、前の版の相談は stale にする。相談の番号を返す。"""
    now = db.now_iso()
    tid = con.execute("SELECT task_id FROM deal_case WHERE id = ?", (case_id,)).fetchone()[0]
    if tid is None:
        tid = con.execute("INSERT INTO task (kind, item_id, state, version, updated_at) VALUES ('purchase', ?, 'awaiting_owner', ?, ?)",
                          (item_id, version, now)).lastrowid
        con.execute("UPDATE deal_case SET task_id = ? WHERE id = ?", (tid, case_id))
    else:
        con.execute("UPDATE task SET version = ?, state = 'awaiting_owner', updated_at = ? WHERE id = ?", (version, now, tid))
        con.execute("UPDATE consultation SET status = 'stale' WHERE task_id = ? AND status IN ('open', 'needs_president')", (tid,))
    cid = con.execute("INSERT INTO consultation (task_id, task_version, question, status, case_id) VALUES (?, ?, ?, 'open', ?)",
                      (tid, version, question, case_id)).lastrowid
    con.executemany("INSERT INTO consultation_option (consultation_id, code, label, valid) VALUES (?, ?, ?, ?)",
                    [(cid, code, label, 1 if v else 0) for code, label, v in CASE_OPTIONS])
    _audit(con, "companion", "consultation_opened", tid, f"case {case_id} v{version}")
    return cid


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def check_change(con, case, change) -> dict:
    """担当者が変えた数量・配分を、自社の DB で確かめて整える（画面の操作の値だけを読む・自然文は読まない）。"""
    if not isinstance(change, dict) or not _int(change.get("quantity")):
        raise ConsultError("change needs a positive integer quantity")
    out = dict(quantity=change["quantity"])
    allocs = change.get("allocations")
    if allocs is None:
        return out
    if not isinstance(allocs, list) or not allocs:
        raise ConsultError("allocations must be a non-empty list")
    norm = []
    for a in allocs:
        if not isinstance(a, dict) or not _int(a.get("quantity")):
            raise ConsultError("each allocation needs a positive integer quantity")
        if a.get("order_id") is not None:
            o = con.execute("SELECT o.id, c.name FROM order_book o JOIN customer c ON c.id = o.customer_id WHERE o.id = ? AND o.item_id = ? "
                            "AND o.status IN ('open', 'partly_filled')", (a["order_id"], case["item_id"])).fetchone()
            if o is None:
                raise ConsultError(f"no open order {a['order_id']} for this item")
            norm.append(dict(order_id=o["id"], customer=o["name"], quantity=a["quantity"]))
        elif a.get("customer"):
            cu = con.execute("SELECT name FROM customer WHERE name = ?", (a["customer"],)).fetchone()
            if cu is None:
                raise ConsultError(f"no customer {a['customer']}")
            norm.append(dict(order_id=None, customer=cu["name"], quantity=a["quantity"]))
        else:
            raise ConsultError("each allocation needs order_id or customer")
    if sum(a["quantity"] for a in norm) != out["quantity"]:
        raise ConsultError("allocations must add up to the quantity")
    out["allocations"] = norm
    return out


def changes_priority(con, case, change: dict) -> bool:
    """顧客の間の優先を変える答えか：ある売り先の注文を欠き、ほかの売り先に回す（配分を決めずに注文の合計より減らすのも同じ）。"""
    orders = [dict(r) for r in con.execute(
        "SELECT o.id, o.quantity, c.name AS customer FROM order_book o JOIN customer c ON c.id = o.customer_id "
        "WHERE o.item_id = ? AND o.order_date = ? AND o.status IN ('open', 'partly_filled')", (case["item_id"], case["trade_date"]))]
    allocs = change.get("allocations")
    if allocs is None:
        return len({o["customer"] for o in orders}) >= 2 and change["quantity"] < sum(o["quantity"] for o in orders)
    got = {}
    for a in allocs:
        if a["order_id"] is not None:
            got[a["order_id"]] = got.get(a["order_id"], 0) + a["quantity"]
    short = {o["id"] for o in orders if got.get(o["id"], 0) < o["quantity"]}
    # 欠ける注文のどれかについて、ほかの売り先（ほかの注文・注文の無い売り先）に回す数があれば優先の変更
    # （回す先の注文も欠けていても同じ。2026-11-03 撮影で、料亭 4本・スーパー 0本の案が担当者だけで確定した）
    return any(sum(a["quantity"] for a in allocs if a["order_id"] != s) > 0 for s in short)


def directive_settles(con, case, change: dict) -> bool:
    """社長の今日の指示（この売り先の分は落とすな）が、この優先をすでに決めているか（2026-11-04 デイブ・SAL）：
    指示の売り先の注文を全部満たし、欠けるのは指示の外の売り先だけなら、社長の決めた優先どおり。指示と違う案は社長の確認へ進む。"""
    allocs = change.get("allocations")
    if allocs is None:
        return False
    keep = {r[0] for r in con.execute(
        "SELECT customer_id FROM directive WHERE kind = 'keep_customer' AND status = 'active' AND trade_date = ? AND customer_id IS NOT NULL "
        "AND (item_id IS NULL OR item_id = ?)", (case["trade_date"], case["item_id"]))}
    if not keep:
        return False
    kept = [dict(r) for r in con.execute(
        "SELECT id, quantity FROM order_book WHERE item_id = ? AND order_date = ? AND status IN ('open', 'partly_filled') AND customer_id IN (%s)"
        % ",".join("?" * len(keep)), (case["item_id"], case["trade_date"], *keep))]
    if not kept:
        return False
    got = {}
    for a in allocs:
        if a["order_id"] is not None:
            got[a["order_id"]] = got.get(a["order_id"], 0) + a["quantity"]
    return all(got.get(o["id"], 0) >= o["quantity"] for o in kept)


def _answer_change(con, c, option_id: int, key: str, reason_text, change, role: str) -> dict:
    """条件を変える答え。確定できれば、記録と同じトランザクションで出来事 human_choice を書く。"""
    if c["case_id"] is None:
        raise ConsultError("change is only for a board consultation")
    case = con.execute("SELECT id, item_id, trade_date, status FROM deal_case WHERE id = ?", (c["case_id"],)).fetchone()
    if case["status"] not in CASE_OPEN:
        raise ConsultError(f"case {case['id']} is {case['status']}")
    change = check_change(con, case, change)
    try:
        may = authority.may_change_priority(con, role)
    except authority.AuthorityError as e:
        raise ConsultError(str(e)) from None
    if c["status"] == "needs_president" and not may:
        raise ConsultError(f"consultation {c['id']} waits for the president")
    priority = changes_priority(con, case, change)
    now = db.now_iso()
    cj = json.dumps(change, ensure_ascii=False, sort_keys=True)
    base = dict(consultation_id=c["id"], option="change", duplicate=False, priority_change=priority)
    if priority and not may and not directive_settles(con, case, change):
        with con:
            con.execute("UPDATE consultation SET status = 'needs_president', chosen_option_id = ?, reason_text = ?, answered_at = ?, idempotency_key = ?, "
                        "change_json = ?, answered_by = ? WHERE id = ?", (option_id, reason_text, now, key, cj, role, c["id"]))
            _audit(con, "owner", "consultation_needs_president", c["task_id"], f"consultation {c['id']} by {role}")
        return dict(base, result="needs_president")
    with con:
        con.execute("UPDATE consultation SET status = 'answered', chosen_option_id = ?, reason_text = ?, answered_at = ?, idempotency_key = ?, "
                    "change_json = ?, answered_by = ? WHERE id = ?", (option_id, reason_text, now, key, cj, role, c["id"]))
        con.execute("UPDATE task SET state = 'resumed', updated_at = ? WHERE id = ?", (now, c["task_id"]))
        # 前の版の案では承認できないようにする（交渉し直した結果で、ボードを出し直す）
        con.execute("UPDATE board_option SET status = 'withdrawn' WHERE case_id = ? AND status = 'offered'", (case["id"],))
        con.execute("UPDATE deal_case SET status = 'open', updated_at = ? WHERE id = ?", (now, case["id"]))
        eid = events._insert(con, "human_choice", dict(source="consultation", consultation_id=c["id"], case_id=case["id"], version=c["task_version"],
                                                       option="change", change=change, answered_by=role, priority_change=priority), events._now())
        # 確定した人の決定を記録する（AI の次の案と承認は、この決定と照合される）
        con.execute("INSERT INTO human_decision (case_id, source, consultation_id, version, quantity, allocations_json, priority_change, decided_by, decided_at) "
                    "VALUES (?, 'consultation', ?, ?, ?, ?, ?, ?, ?)", (case["id"], c["id"], c["task_version"], change["quantity"],
                                                       json.dumps(change["allocations"], ensure_ascii=False) if change.get("allocations") else None,
                                                       int(priority), role, now))
        _refresh_group(con, c["group_id"])
        _audit(con, "owner", "consultation_answered", c["task_id"], f"consultation {c['id']} change by {role} event {eid}")
    return dict(base, result="answered", event_id=eid)


WHO = {"staff": "担当者", "president": "社長（網谷さん）"}


def latest_decision(con, case_id: int):
    """案件の最新の人の決定（無ければ None）。"""
    r = con.execute("SELECT * FROM human_decision WHERE case_id = ? ORDER BY id DESC LIMIT 1", (case_id,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["allocations"] = json.loads(d["allocations_json"]) if d["allocations_json"] else None
    return d


def decision_text(d: dict, unit: str = "") -> str:
    """決定の中身（AI と画面に返す）：誰が・どの版で・数量と配分。"""
    parts = "・".join(f"{a['customer']} {a['quantity']}{unit}" for a in d["allocations"] or [])
    return f"{WHO.get(d['decided_by'], d['decided_by'])}の決定（版 {d['version']}）：仕入れ {d['quantity']}{unit}" + (f"（{parts}）" if parts else "")


def against_decision(con, d: dict | None, allocations: list[dict]) -> list[str]:
    """案の配分が人の決定と違う点（空なら決定に沿う）。決定が名指しした行き先へ、決定の数量までだけ回すのが「沿う」。
    行き先が少ないのは、相手の在庫が足りない時などに起きるので沿うとみなす。仕入れない案は沿う。"""
    if d is None or not allocations:
        return []
    def label(kind, ref):
        if kind == "order":
            r = con.execute("SELECT c.name FROM order_book o JOIN customer c ON c.id = o.customer_id WHERE o.id = ?", (ref,)).fetchone()
            return f"注文 {ref}（{r[0] if r else '?'}）"
        return {"customer": f"{ref}（注文なし）", "agreement": f"合意 {ref}", "unallocated": "売り先なし"}[kind]
    got = {}
    for a in allocations:
        k = ("order", a.get("ref_id")) if a["kind"] == "order" else ("customer", a.get("customer")) if a["kind"] == "customer_lead" else (
            ("agreement", a.get("ref_id")) if a["kind"] == "agreement" else ("unallocated", None))
        got[k] = got.get(k, 0) + a["quantity"]
    if not d["allocations"]:
        total = sum(got.values())
        return [f"仕入れ {total} は決定の {d['quantity']} を超えます"] if total > d["quantity"] else []
    want = {}
    for a in d["allocations"]:
        k = ("order", a["order_id"]) if a.get("order_id") is not None else ("customer", a["customer"])
        want[k] = want.get(k, 0) + a["quantity"]
    out = []
    for k, q in got.items():
        if k not in want:
            out.append(f"{label(*k)}に {q} を回しています（決定に無い行き先）")
        elif q > want[k]:
            out.append(f"{label(*k)}に {q} を回しています（決定は {want[k]}）")
    return out


def decide_draft(con, draft_id: int, action: str, role: str) -> dict:
    """売り先への連絡の下書きを、人が使うか使わないか決める（Agent Dock は売り先に送らない。人が決めた記録を残す）。
    同じ決定の繰り返しは repeated=True。決めた後に別の決定はできない。"""
    if action not in ("approve", "discard"):
        raise ConsultError(f"unknown action: {action}")
    authority.limit_for(con, role)   # 表に無い役割は止める
    d = con.execute("SELECT id, status, decided_by, stale_reason FROM customer_draft WHERE id = ?", (draft_id,)).fetchone()
    if d is None:
        raise KeyError(draft_id)
    if d["status"] == "stale":   # 案件が決まった後の下書き（deal.DRAFT_STALE）：前提が変わったので使えない
        raise ConsultError(f"この下書きは古くなったため、使えません（{d['stale_reason']}）")
    want = "approved" if action == "approve" else "discarded"
    if d["status"] != "draft":
        if d["status"] == want:
            return dict(draft_id=d["id"], status=d["status"], repeated=True)
        raise ConsultError(f"draft {draft_id} is {d['status']}")
    now = db.now_iso()
    with con:
        con.execute("UPDATE customer_draft SET status = ?, decided_by = ?, decided_at = ? WHERE id = ? AND status = 'draft'", (want, role, now, d["id"]))
        _audit(con, "owner", f"customer_draft_{want}", None, f"draft {d['id']} by {role}")
    return dict(draft_id=d["id"], status=want, repeated=False)


def revise(con, task_id: int, reason: str) -> int:
    """条件が変わった（新しい注文が入った等）。版を上げ、前の版の未回答の相談を stale にする。新しい版を返す。"""
    t = task(con, task_id)
    if t["state"] in ("done", "no_deal", "incomplete"):
        raise ConsultError(f"task {task_id} is closed: {t['state']}")
    now = db.now_iso()
    with con:
        con.execute("UPDATE task SET version = version + 1, state = 'revalidating', updated_at = ? WHERE id = ?", (now, task_id))
        gids = [r[0] for r in con.execute("SELECT DISTINCT group_id FROM consultation WHERE task_id = ? AND status = 'open'", (task_id,))]
        con.execute("UPDATE consultation SET status = 'stale' WHERE task_id = ? AND status = 'open'", (task_id,))
        for g in gids:
            _refresh_group(con, g)
        _audit(con, "system", "task_revised", task_id, reason)
    return t["version"] + 1


def can_proceed(con, task_id: int) -> bool:
    """答えを待っている間・見直し中は先へ進めない。"""
    return task(con, task_id)["state"] not in (*STATES_WAITING, "revalidating", "done", "no_deal", "incomplete")


def card(con, group_id: int) -> dict:
    """スマホの1枚に出す中身（読み取り専用）。"""
    g = con.execute("SELECT id, title, status FROM consultation_group WHERE id = ?", (group_id,)).fetchone()
    if g is None:
        raise KeyError(group_id)
    items = []
    for c in con.execute("SELECT id, task_id, task_version, question, status FROM consultation WHERE group_id = ? ORDER BY id", (group_id,)):
        opts = [dict(code=o["code"], label=o["label"], valid=bool(o["valid"])) for o in con.execute(
            "SELECT code, label, valid FROM consultation_option WHERE consultation_id = ? ORDER BY id", (c["id"],))]
        items.append(dict(consultation_id=c["id"], task_id=c["task_id"], version=c["task_version"],
                          question=c["question"], status=c["status"], options=opts))
    return dict(group_id=g["id"], title=g["title"], status=g["status"], items=items)


def revalidated(con, task_id: int, briefing_fingerprint: str) -> None:
    """見直しを終えた（新しい材料を読み直した）。見直し中の仕事だけを動かせる状態に戻す。読み直した材料の照合番号を残す。"""
    t = task(con, task_id)
    if t["state"] != "revalidating":
        raise ConsultError(f"task {task_id} is {t['state']}, not revalidating")
    with con:
        con.execute("UPDATE task SET state = 'running', updated_at = ? WHERE id = ?", (db.now_iso(), task_id))
        _audit(con, "companion", "task_revalidated", task_id, f"v{t['version']} briefing {briefing_fingerprint[:12]}")
