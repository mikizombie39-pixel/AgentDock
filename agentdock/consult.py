"""持ち主への相談と、相談の束（スマホの1枚）。

安全の条件をコードで守る：
- 古い判断：相談は仕事の版に結びつく。条件が変わって版が上がると、前の版の相談は stale になり、答えても通らない
- 二重の回答：同じ idempotency_key の回答は一度しか効かない。同じ回答の繰り返しは同じ結果を返す
- 無回答：答えが無いうちは仕事は awaiting_owner のままで、先へ進めない（時間が来ても勝手に決めない）
- 選べない選択肢：コードが確かめて valid=0 にした選択肢は選べない
"""
from agentdock import db

STATES_WAITING = ("awaiting_owner",)


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


def answer(con, consultation_id: int, option_code: str, idempotency_key: str, reason_text: str | None = None) -> dict:
    """持ち主の回答。結果の dict を返す（duplicate=True は同じ回答の繰り返し）。"""
    prev = con.execute("SELECT id, chosen_option_id FROM consultation WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    if prev is not None:
        code = con.execute("SELECT code FROM consultation_option WHERE id = ?", (prev["chosen_option_id"],)).fetchone()[0]
        if prev["id"] != consultation_id or code != option_code:
            raise ConsultError("idempotency_key reused for a different answer")
        return dict(consultation_id=consultation_id, option=code, duplicate=True)

    c = con.execute("SELECT id, group_id, task_id, task_version, status FROM consultation WHERE id = ?", (consultation_id,)).fetchone()
    if c is None:
        raise KeyError(consultation_id)
    t = task(con, c["task_id"])
    if c["status"] == "stale" or t["version"] != c["task_version"]:
        with con:
            con.execute("UPDATE consultation SET status = 'stale' WHERE id = ?", (c["id"],))
            _refresh_group(con, c["group_id"])
            _audit(con, "system", "stale_answer_rejected", t["id"], f"consultation {c['id']} v{c['task_version']} != v{t['version']}")
        raise StaleError(f"consultation {c['id']} is for v{c['task_version']}, task is v{t['version']}")
    if c["status"] != "open":
        raise ConsultError(f"consultation {c['id']} is {c['status']}")
    opt = con.execute("SELECT id, valid FROM consultation_option WHERE consultation_id = ? AND code = ?",
                      (c["id"], option_code)).fetchone()
    if opt is None:
        raise ConsultError(f"no option {option_code}")
    if not opt["valid"]:
        raise ConsultError(f"option {option_code} is not allowed")
    now = db.now_iso()
    with con:
        con.execute("UPDATE consultation SET status = 'answered', chosen_option_id = ?, reason_text = ?, answered_at = ?, idempotency_key = ? WHERE id = ?",
                    (opt["id"], reason_text, now, idempotency_key, c["id"]))
        con.execute("UPDATE task SET state = 'resumed', updated_at = ? WHERE id = ?", (now, t["id"]))
        _refresh_group(con, c["group_id"])
        _audit(con, "owner", "consultation_answered", t["id"], f"consultation {c['id']} {option_code}")
    return dict(consultation_id=c["id"], option=option_code, duplicate=False)


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
