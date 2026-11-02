"""仲間の判断と根拠の記録、画面の「根拠の1枚」。

決まり：
- 範囲の中か外かはコードが判定する（plans.check）。仲間の言葉では決まらない
- 範囲の外の値・数量で、入札・申し出・受諾・購入を記録しようとしたら OutsidePlan で止める。範囲の外は相談（escalate）でしか残せない
- 根拠が1件も無い判断は記録しない
- 判断は仕事の版に結びつけて残す（後で古い判断かどうかが分かる）
"""
from agentdock import consult, db, plans

ACTS = ("bid", "offer", "counter", "accept", "buy")


class EvidenceError(Exception):
    pass


class OutsidePlan(EvidenceError):
    def __init__(self, reasons):
        super().__init__(", ".join(reasons))
        self.reasons = reasons


def record(con, task_id: int, action: str, unit_price_yen: int | None, quantity: int | None, rationale: str,
           evidence: list[tuple], on_date: str, criteria_id: int | None = None, model: str | None = None) -> int:
    """evidence は [(kind, ref_id, summary)]。decision の id を返す。"""
    if not evidence:
        raise EvidenceError("no evidence")
    if not rationale.strip():
        raise EvidenceError("no rationale")
    t = consult.task(con, task_id)
    item_code = con.execute("SELECT i.code FROM task t JOIN item i ON i.id = t.item_id WHERE t.id = ?", (task_id,)).fetchone()[0]
    if unit_price_yen is not None and quantity is not None:
        res = plans.check(con, item_code, t["kind"], on_date, unit_price_yen, quantity)
    else:
        res = dict(within=False, reasons=["no_terms"])
    if action in ACTS and not res["within"]:
        with con:
            con.execute("INSERT INTO audit_event (at, actor, event, task_id, detail) VALUES (?, 'system', 'outside_plan_blocked', ?, ?)",
                        (db.now_iso(), task_id, f"{action} {unit_price_yen} x {quantity}: {', '.join(res['reasons'])}"))
        raise OutsidePlan(res["reasons"])
    with con:
        did = con.execute(
            "INSERT INTO decision (task_id, task_version, action, unit_price_yen, quantity, criteria_id, within_plan, rationale, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, t["version"], action, unit_price_yen, quantity, criteria_id, 1 if res["within"] else 0, rationale, model, db.now_iso())).lastrowid
        con.executemany("INSERT INTO decision_evidence (decision_id, kind, ref_id, summary) VALUES (?, ?, ?, ?)",
                        [(did, k, ref, s) for k, ref, s in evidence])
        con.execute("INSERT INTO audit_event (at, actor, event, task_id, detail) VALUES (?, 'companion', 'decision', ?, ?)",
                    (db.now_iso(), task_id, f"{action} within={1 if res['within'] else 0}"))
    return did


def sheet(con, decision_id: int) -> dict:
    """根拠の1枚：判断、範囲の判定、参照した根拠。古い版の判断なら stale=True。"""
    d = con.execute("SELECT * FROM decision WHERE id = ?", (decision_id,)).fetchone()
    if d is None:
        raise KeyError(decision_id)
    t = consult.task(con, d["task_id"])
    item = con.execute("SELECT i.code, i.name FROM task t JOIN item i ON i.id = t.item_id WHERE t.id = ?", (d["task_id"],)).fetchone()
    p = con.execute("SELECT max_unit_price_yen, max_quantity FROM plan p JOIN task t ON t.item_id = p.item_id AND t.kind = p.kind "
                    "WHERE t.id = ? AND p.status = 'approved'", (d["task_id"],)).fetchone()
    return dict(
        decision_id=d["id"], task_id=d["task_id"], item=item["code"], item_name=item["name"], action=d["action"],
        unit_price_yen=d["unit_price_yen"], quantity=d["quantity"], within_plan=bool(d["within_plan"]),
        plan=dict(max_unit_price_yen=p[0], max_quantity=p[1]) if p else None,
        rationale=d["rationale"], version=d["task_version"], stale=d["task_version"] != t["version"],
        evidence=[dict(kind=r["kind"], ref_id=r["ref_id"], summary=r["summary"]) for r in con.execute(
            "SELECT kind, ref_id, summary FROM decision_evidence WHERE decision_id = ? ORDER BY id", (decision_id,))])
