"""持ち主の帳面（Vault）：社長の選択と理由を事例として残し、次の判断で引く。

- 事例は答えの出た相談からだけ作る（仲間が勝手に事例を書かない）
- 本文は Markdown のファイル、DB の vault_case は引くための索引
- ファイルは持ち主ごとの置き場（データの置き場の中）にだけ書く
"""
from pathlib import Path

from agentdock import db


class VaultError(Exception):
    pass


def folder(owner_code: str) -> Path:
    if owner_code not in db.OWNER_CODES:
        raise ValueError(owner_code)
    return db.data_dir() / "vault" / owner_code


def record_from_consultation(con, consultation_id: int) -> Path:
    c = con.execute(
        "SELECT c.id, c.task_id, c.question, c.status, c.reason_text, c.answered_at, o.code AS option_code, o.label AS option_label, "
        "t.item_id, t.kind, i.code AS item_code, i.name AS item_name "
        "FROM consultation c JOIN task t ON t.id = c.task_id JOIN item i ON i.id = t.item_id "
        "LEFT JOIN consultation_option o ON o.id = c.chosen_option_id WHERE c.id = ?", (consultation_id,)).fetchone()
    if c is None:
        raise KeyError(consultation_id)
    if c["status"] != "answered":
        raise VaultError(f"consultation {consultation_id} is {c['status']}")
    if con.execute("SELECT 1 FROM vault_case WHERE consultation_id = ?", (consultation_id,)).fetchone():
        raise VaultError(f"consultation {consultation_id} is already recorded")
    owner = con.execute("SELECT code, person_name FROM owner").fetchone()
    base = folder(owner["code"]).resolve()
    day = c["answered_at"][:10]
    path = (base / f"{day}-{c['item_code']}-{c['kind']}-c{c['id']}.md").resolve()
    if base not in path.parents:
        raise VaultError(f"path outside the vault: {path}")
    reason = (c["reason_text"] or "").strip()
    body = "\n".join([
        "---",
        f"date: {day}",
        f"item: {c['item_code']}",
        f"kind: {c['kind']}",
        f"choice: {c['option_code']}",
        "---",
        f"# {c['item_name']}：{c['question']}",
        "",
        f"- {owner['person_name']}の選択：{c['option_label']}",
        f"- 理由：{reason or '（記録なし）'}",
        "",
    ])
    base.mkdir(parents=True, exist_ok=True)
    with con:
        con.execute("INSERT INTO vault_case (file_path, task_id, situation, chosen_option_code, created_at, item_id, consultation_id, reason_text) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(path), c["task_id"], c["question"], c["option_code"], db.now_iso(), c["item_id"], c["id"], reason or None))
        path.write_text(body, encoding="utf-8")   # 書けなければ索引の行も戻る
    return path


def find(con, item_code: str, limit: int = 3) -> list[dict]:
    """品目の事例を新しい順に。ファイルが消えていれば missing=True で返す（黙って捨てない）。"""
    out = []
    for r in con.execute(
            "SELECT v.id, v.file_path, v.situation, v.chosen_option_code, v.reason_text, v.created_at FROM vault_case v "
            "JOIN item i ON i.id = v.item_id WHERE i.code = ? ORDER BY v.created_at DESC, v.id DESC LIMIT ?", (item_code, limit)):
        out.append(dict(id=r["id"], date=r["created_at"][:10], situation=r["situation"], choice=r["chosen_option_code"],
                        reason=r["reason_text"], missing=not Path(r["file_path"]).exists()))
    return out
