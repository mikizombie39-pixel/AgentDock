"""本物の Claude の実行記録（take）を8項目で点検する。読むだけ（DB を書き換えない）。
2026-11-05 の5回の通しの点検に使った道具と同じ（冒頭の説明だけ直した）。
使い方：python -X utf8 evidence/check_take.py evidence/takes/20261105-203748-live
出力：各項目の合否と根拠の数字。最後に合格した項目の数。
"""
import datetime
import json
import os
import sqlite3
import sys

take = sys.argv[1]
base = os.path.join(take, "data") if os.path.exists(os.path.join(take, "data", "amiya.sqlite")) else take


def con(name):
    c = sqlite3.connect(f"file:{os.path.join(base, name + '.sqlite')}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def sec(s):
    return int(datetime.datetime.fromisoformat(s).timestamp()) if s else None


a, m, i = con("amiya"), con("market"), con("iwase")
out = []
HAS_SETTLE = bool(i.execute("SELECT 1 FROM sqlite_master WHERE name = 'settle_line'").fetchone())   # 移行 022 より前の記録には無い


def check(name, ok, detail):
    out.append((name, bool(ok), detail))


dec = [dict(r) for r in a.execute("SELECT * FROM case_decision ORDER BY id")]
runs = {r["id"]: dict(r) for r in a.execute("SELECT r.*, b.kind AS ev FROM agent_run r LEFT JOIN biz_event b ON b.id = r.event_id")}
turns = [dict(r) for r in a.execute("SELECT * FROM workspace_turn WHERE source = 'present' ORDER BY id")]
ret = next((d for d in dec if d["action"] == "return"), None)
reg = next((d for d in dec if d["result"] in ("registered", "partial")), None)

# 1 差し戻しの前に始まった起動が、決定の後に案を出していない
stale = [t for t in turns if any(sec(d["at"]) > sec(runs[t["run_id"]]["started_at"]) and sec(d["at"]) <= sec(t["at"]) for d in dec)]
check("1 古い判断の案を出していない", not stale, f"決定の後に出た古い起動の案 {len(stale)}件")

# 2 差し戻しの理由を受けた起動の案から、承認・登録へ
ok2, why2 = False, "差し戻しか登録が無い"
if ret and reg:
    opt = a.execute("SELECT case_id, version FROM board_option WHERE id = ?", (reg["option_id"],)).fetchone()
    t = next((t for t in turns if t["case_id"] == opt["case_id"] and t["version"] == opt["version"]), None)
    r = runs.get(t["run_id"]) if t else None
    ok2 = bool(r and sec(r["started_at"]) >= sec(ret["at"]) and r["ev"] == "human_choice")
    why2 = f"登録した案の版 {opt['version']}・案を出した起動 {t['run_id'] if t else None}（{r['ev'] if r else None}・開始 {r['started_at'][11:19] if r else None}）・差し戻し {ret['at'][11:19]}"
check("2 差し戻しを受けた案から登録", ok2, why2)

# 3 成立した取引が追加の集計に入った（追加集計待ちが残っていない）
trades = [dict(r) for r in m.execute("SELECT * FROM trade WHERE seller = 'iwase' AND status = 'registered'")]
if not HAS_SETTLE:
    i = sqlite3.connect(":memory:")
    i.row_factory = sqlite3.Row
    i.executescript("CREATE TABLE settle_line (kind, source_kind, source_id); CREATE TABLE settle_run (additional, status, started_at, finished_at);")
lines = {(r["kind"], r["source_kind"], r["source_id"]) for r in i.execute("SELECT kind, source_kind, source_id FROM settle_line")}
miss = [t["trade_id"] for t in trades if ("invoice", "trade", t["trade_id"]) not in lines or ("payout", "trade", t["trade_id"]) not in lines]
add = i.execute("SELECT count(*) FROM settle_run WHERE additional = 1 AND status = 'done'").fetchone()[0]
check("3 取引が追加の集計に反映" + ("" if HAS_SETTLE else "（集計の表の無い記録）"), HAS_SETTLE and trades and not miss, f"漁協の売りの取引 {len(trades)}件・未反映 {miss}・追加の集計の完了 {add}件")

# 4 二重計上が無く、配分・金額・不足が記録と一致
dup = i.execute("SELECT count(*) FROM (SELECT kind, source_kind, source_id, count(*) n FROM settle_line GROUP BY 1, 2, 3 HAVING n > 1)").fetchone()[0]
props = [dict(r) for r in a.execute("SELECT * FROM proposal WHERE status = 'registered'")]
bought = sum(p["quantity"] for p in props if p["side"] == "buy")
total = sum(p["quantity"] * p["unit_price_yen"] for p in props if p["side"] == "buy")
tids = {p["trade_id"] for p in props}
alloc = {r[0]: r[1] for r in a.execute("SELECT c.shop_name, sum(s.quantity) FROM stock s JOIN order_book o ON o.id = s.order_id JOIN customer c ON c.id = o.customer_id "
                                     "WHERE s.status = 'allocated' GROUP BY c.shop_name")}
orders = {r[0]: r[1] for r in a.execute("SELECT c.shop_name, sum(o.quantity) FROM order_book o JOIN customer c ON c.id = o.customer_id JOIN item i ON i.id = o.item_id "
                                      "WHERE i.code = 'buri_xl' GROUP BY c.shop_name")}
mkt = sum(r[0] * r[1] for r in m.execute("SELECT quantity, unit_price_yen FROM trade WHERE status = 'registered' AND buyer = 'amiya'"))
short = {k: orders[k] - alloc.get(k, 0) for k in orders}
ok4 = dup == 0 and bought == sum(alloc.values()) and total == mkt
check("4 二重計上なし・配分と金額が一致", ok4, f"重複 {dup}・仕入れ {bought}本 {total:,}円（市場の帳簿 {mkt:,}円）・配分 {alloc}・注文 {orders}・不足 {short}")

# 見せ場：魚廣 4本を確保
check("5 魚廣 4本を確保（社長の指示）", alloc.get("魚廣") == 4, f"魚廣 {alloc.get('魚廣')}本")

# 見せ場：漁協の集計の途中に照会が届いた（並行）
inq = [r["created_at"] for r in m.execute("SELECT created_at FROM network_message WHERE from_owner = 'amiya' AND to_owner = 'iwase' AND kind = 'inquiry' ORDER BY id")]
rs = [dict(r) for r in i.execute("SELECT started_at, finished_at FROM settle_run WHERE additional = 0")]
busy = [x for x in inq if any(r["started_at"] and sec(r["started_at"]) <= sec(x) <= sec(r["finished_at"] or "2100-01-01T00:00:00+09:00") for r in rs)]
check("6 集計の途中に照会が届いた" + ("" if HAS_SETTLE else "（集計の表の無い記録）"), busy, f"照会 {len(inq)}件のうち集計の途中 {len(busy)}件")

# 見せ場：登録の後に売り先への下書き（不足を伝える）が出た
drafts = [dict(r) for r in a.execute("SELECT created_at, status FROM customer_draft")]
after = [d for d in drafts if reg and sec(d["created_at"]) >= sec(reg["at"]) and d["status"] == "draft"]
check("7 登録の後に下書き", after, f"下書き {len(drafts)}件・登録の後でまだ決めていない {len(after)}件")

# 見せ場：起動の誤りや上限の到達が無い
bad = [r for r in runs.values() if r["outcome"] in ("error", "max_turns", "refusal")]
check("8 起動の誤りなし", not bad, f"誤りの起動 {[(r['id'], r['outcome']) for r in bad]}")

for n, ok, d in out:
    print(("○ " if ok else "× ") + n + "：" + d)
print(f"合格 {sum(1 for _, ok, _ in out if ok)} / {len(out)}")
