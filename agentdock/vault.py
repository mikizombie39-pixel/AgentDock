"""会社ごとの Vault（知識・ルール・経験・社長の指示）：リポジトリの vault/<会社>/<分類>/<識別子>.md を読む。

- 知識はこのファイルの集まりが正本。チャットの文言・画面のコード・種のコードには書かない（2026-11-04 デイブ）
- 会社ごとに読む範囲を分ける：load(company) は vault/<company>/ だけを読む。ほかの会社の記録は返さない
- 読み込みで frontmatter を検証し、壊れた記録があれば VaultError で止める（黙って捨てない）
- 有効期間の判定は日付（実演の日 2026-11-02）で行う：今有効／期限切れ／これから有効／取り下げ
- 業務の数字（今朝の注文・在庫・値・相手の回答・期限）は Vault に持たない。業務 DB から読む
- 権限・金額の最終の判定は権限の表とサーバーの処理。ルールの文の金額は、種の権限の表と食い違わないことを確かめるだけ

置き場は環境変数 AGENTDOCK_VAULT で替えられる（試験用）。
"""
import datetime
import os
import re
import unicodedata
from pathlib import Path

from agentdock import db
from agentdock.seed import scenario as sc

DEFAULT_ROOT = db.REPO / "vault"
# 分類（並べる順）と、識別子の中の分類の文字
CATEGORIES = {"会社のルール": "R", "社長の指示": "D", "仕入れの知識": "S", "履歴・経験": "E"}
PREFIX = {"amiya": "AM", "buyer_b": "BB", "iwase": "IW", "buyer_c": "BC"}
REQUIRED = ("id", "company", "category", "title", "subject", "source", "recorded_on", "valid_from", "valid_to", "status", "fictional")
OPTIONAL = ("item", "customer", "directive_kind", "issued_by", "issued_from", "via", "received_at")
STATUSES = ("active", "expired", "withdrawn")
DIRECTIVE_KINDS = ("keep_customer",)
VALIDITY_LABEL = {"current": "今有効", "expired": "期限切れ", "future": "これから有効", "withdrawn": "取り下げ"}
# 記録の使い方：過去の経験（判断の材料）と、今有効な指示・ルール（従うもの）と、知識を分けて返す
USE_LABEL = {"directive": "今有効な社長の指示", "rule": "今有効な会社のルール", "knowledge": "仕入れの知識", "experience": "過去の経験（判断の材料）",
             "not_current": "今は効力のない記録"}


class VaultError(Exception):
    pass


def root() -> Path:
    return Path(os.environ.get("AGENTDOCK_VAULT") or DEFAULT_ROOT)


def _date(v, key, path):
    try:
        return datetime.date.fromisoformat(v).isoformat()
    except (TypeError, ValueError):
        raise VaultError(f"{path}: {key} が日付（YYYY-MM-DD）ではありません：{v!r}") from None


def parse(text: str, path) -> tuple[dict, str]:
    """frontmatter（key: value の行だけ）と本文。"""
    t = text.replace("\r\n", "\n")
    m = re.match(r"---\n(.*?)\n---\n(.*)\Z", t, re.S)
    if not m:
        raise VaultError(f"{path}: frontmatter（--- で囲んだ行）がありません")
    fm = {}
    for line in m.group(1).split("\n"):
        if not line.strip():
            continue
        k, sep, v = line.partition(":")
        k = k.strip()
        if not sep or not k:
            raise VaultError(f"{path}: frontmatter の行が読めません：{line!r}")
        if k in fm:
            raise VaultError(f"{path}: {k} が2回あります")
        fm[k] = v.strip().strip('"').strip("'") or None
    return fm, m.group(2).strip()


def _check(fm: dict, body: str, path: Path, company: str, category: str) -> dict:
    missing = [k for k in REQUIRED if k not in fm]
    if missing:
        raise VaultError(f"{path}: 欠けている項目：{', '.join(missing)}")
    unknown = [k for k in fm if k not in REQUIRED + OPTIONAL]
    if unknown:
        raise VaultError(f"{path}: 知らない項目：{', '.join(unknown)}")
    for k in ("id", "company", "category", "title", "subject", "source", "recorded_on", "valid_from", "status", "fictional"):
        if not fm[k]:
            raise VaultError(f"{path}: {k} が空です")
    if fm["company"] != company:
        raise VaultError(f"{path}: company {fm['company']} が置き場の会社 {company} と違います")
    if fm["category"] != category or category not in CATEGORIES:
        raise VaultError(f"{path}: category {fm['category']} が置き場の分類 {category} と違うか、知らない分類です")
    if fm["id"] != path.stem:
        raise VaultError(f"{path}: id {fm['id']} がファイルの名前と違います")
    pat = rf"{PREFIX[company]}-D-\d{{8}}-\d{{2}}" if category == "社長の指示" else rf"{PREFIX[company]}-{CATEGORIES[category]}-\d{{3}}"
    if not re.fullmatch(pat, fm["id"]):
        raise VaultError(f"{path}: id {fm['id']} が識別子の形（{pat}）と違います")
    rec = dict(fm)
    rec["recorded_on"] = _date(fm["recorded_on"], "recorded_on", path)
    rec["valid_from"] = _date(fm["valid_from"], "valid_from", path)
    rec["valid_to"] = _date(fm["valid_to"], "valid_to", path) if fm["valid_to"] else None
    if rec["valid_to"] and rec["valid_to"] < rec["valid_from"]:
        raise VaultError(f"{path}: valid_to が valid_from より前です")
    if fm["status"] not in STATUSES:
        raise VaultError(f"{path}: status は {', '.join(STATUSES)} のどれかです：{fm['status']}")
    if "架空" not in fm["fictional"]:
        raise VaultError(f"{path}: fictional に実証用の架空設定の表示がありません")
    if not body:
        raise VaultError(f"{path}: 本文が空です")
    if fm.get("item") and fm["item"] not in {c for c, *_ in sc.ITEMS}:
        raise VaultError(f"{path}: item {fm['item']} は品目にありません")
    if fm.get("customer") and fm["customer"] not in {n for n, _ in sc.CUSTOMERS.get(company, [])}:
        raise VaultError(f"{path}: customer {fm['customer']} は {company} の売り先の区分にありません")
    if category == "社長の指示":
        if fm.get("directive_kind") not in DIRECTIVE_KINDS:
            raise VaultError(f"{path}: directive_kind は {', '.join(DIRECTIVE_KINDS)} のどれかです")
        if not fm.get("customer") or not fm.get("issued_by") or not fm.get("via") or not fm.get("issued_from"):
            raise VaultError(f"{path}: 社長の指示には customer・issued_by・issued_from・via が要ります")
        if fm["issued_by"] not in {r for r, *_ in sc.AUTHORITY.get(company, [])}:
            raise VaultError(f"{path}: issued_by {fm['issued_by']} は権限の表の役割にありません")
        if fm.get("received_at") and not re.fullmatch(r"\d{2}:\d{2}", fm["received_at"]):
            raise VaultError(f"{path}: received_at は HH:MM です")
        if not words(body):
            raise VaultError(f"{path}: 社長の指示の言葉（「」で囲んだ文）が本文にありません")
    elif any(fm.get(k) for k in ("directive_kind", "issued_by", "issued_from", "via", "received_at")):
        raise VaultError(f"{path}: 指示の項目は社長の指示の記録だけに書けます")
    rec["body"] = body
    rec["path"] = str(path)
    return rec


def words(body: str) -> str | None:
    """社長の指示の言葉（本文の最初の「」の中）。"""
    m = re.search(r"「([^」]+)」", body)
    return m.group(1) if m else None


def load(company: str) -> list[dict]:
    """会社の Vault の記録を全部読む（vault/<company>/ だけ）。壊れた記録があれば VaultError。"""
    if company not in db.OWNER_CODES:
        raise VaultError(f"知らない会社です：{company}")
    base = (root() / company).resolve()
    if not base.exists():
        return []
    out, seen = [], set()
    for p in sorted(base.rglob("*")):
        if p.is_dir():
            continue
        rel = p.resolve().relative_to(base)
        if p.suffix != ".md" or len(rel.parts) != 2:
            raise VaultError(f"{p}: Vault には <分類>/<識別子>.md だけを置きます")
        fm, body = parse(p.read_bytes().decode("utf-8"), p)
        rec = _check(fm, body, p, company, rel.parts[0])
        if rec["id"] in seen:
            raise VaultError(f"{p}: 識別子 {rec['id']} が重なっています")
        seen.add(rec["id"])
        out.append(rec)
    order = list(CATEGORIES)
    out.sort(key=lambda r: (order.index(r["category"]), r["id"]))
    return out


def validity(rec: dict, on_date: str) -> str:
    """その日に有効か：current（今有効）／expired（期限切れ）／future（これから有効）／withdrawn（取り下げ）。"""
    if rec["status"] == "withdrawn":
        return "withdrawn"
    if rec["status"] == "expired" or (rec["valid_to"] and rec["valid_to"] < on_date):
        return "expired"
    if rec["valid_from"] > on_date:
        return "future"
    return "current"


def use(rec: dict, on_date: str) -> str:
    """記録の使い方。経験は日付に関わらず「過去の経験（判断の材料）」。指示とルールは今有効な時だけ従うもの。"""
    if rec["category"] == "履歴・経験":
        return "experience"
    if validity(rec, on_date) != "current":
        return "not_current"
    return {"社長の指示": "directive", "会社のルール": "rule", "仕入れの知識": "knowledge"}[rec["category"]]


def index(company: str, on_date: str) -> list[dict]:
    """索引（本文なし）：識別子・分類・題・対象・日付・有効か・使い方。"""
    return [dict(id=r["id"], category=r["category"], title=r["title"], subject=r["subject"], recorded_on=r["recorded_on"],
                 valid_from=r["valid_from"], valid_to=r["valid_to"], validity=validity(r, on_date), validity_label=VALIDITY_LABEL[validity(r, on_date)],
                 use=use(r, on_date), use_label=USE_LABEL[use(r, on_date)], item=r.get("item"), customer=r.get("customer"))
            for r in load(company)]


def get(company: str, rid: str) -> dict:
    """会社の範囲の記録を識別子で1件。ほかの会社の識別子・無い識別子は VaultError（中身は返さない）。"""
    for r in load(company):
        if r["id"] == rid:
            return r
    raise VaultError(f"{rid} は自社の Vault にありません")


def directives(company: str, on_date: str) -> list[dict]:
    """その日に有効な社長の指示（期限切れ・これから有効・取り下げは入れない）。"""
    return [r for r in load(company) if r["category"] == "社長の指示" and validity(r, on_date) == "current"]


def rule_amounts(rec: dict) -> set[int]:
    """ルールの文の金額（「円」の付いた数）。"""
    t = unicodedata.normalize("NFKC", rec["body"])
    return {int(m.group(1).replace(",", "")) for m in re.finditer(r"(\d[\d,]*)\s*円", t)}


def rule_amount_mismatches(company: str, records: list[dict] | None = None) -> list[str]:
    """会社のルールの文の金額が、種の権限の表（AUTHORITY）の額と食い違う所。空なら一致。最終の判定は権限の表で行う。"""
    allowed = {v for _, _, v in sc.AUTHORITY.get(company, []) if v}
    out = []
    for r in records if records is not None else load(company):
        if r["category"] != "会社のルール":
            continue
        for v in sorted(rule_amounts(r) - allowed):
            out.append(f"{r['id']} の {v:,}円 は権限の表（{'・'.join(f'{a:,}円' for a in sorted(allowed)) or 'なし'}）にありません")
    return out
