"""DB の置き場と開き方。

持ち主ごとに1つのファイルを開く（仲間は持ち主の DB しか開かない）。
置き場は OneDrive の外。環境変数 AGENTDOCK_DATA で上書きできる（試験用）。
"""
import datetime
import os
import sqlite3
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MIGRATIONS = REPO / "db" / "migrations"
DEFAULT_DATA = Path(r"C:\Users\mikiz\AgentDock-local\data")

OWNER_CODES = ("iwase", "amiya", "buyer_b", "buyer_c")
MARKET = "market"


def data_dir() -> Path:
    return Path(os.environ.get("AGENTDOCK_DATA") or DEFAULT_DATA)


def db_path(code: str) -> Path:
    if code not in OWNER_CODES and code != MARKET:
        raise ValueError(f"unknown db: {code}")
    return data_dir() / f"{code}.sqlite"


def connect(code: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path(code))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def now_iso() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def migrate(con: sqlite3.Connection, kind: str) -> list[int]:
    """kind は 'owner' か 'market'。未適用の番号つき SQL を順に当てる。"""
    folder = MIGRATIONS / kind
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL) STRICT")
    done = {r[0] for r in con.execute("SELECT version FROM schema_version")}
    applied = []
    for f in sorted(folder.glob("*.sql")):
        v = int(f.name.split("_", 1)[0])
        if v in done:
            continue
        with con:
            con.executescript(f.read_text(encoding="utf-8"))
            con.execute("INSERT INTO schema_version (version, applied_at) VALUES (?, ?)", (v, now_iso()))
        applied.append(v)
    return applied
