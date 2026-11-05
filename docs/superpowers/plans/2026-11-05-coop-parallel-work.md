# 漁協の並行の仕事と3者の流れの画面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 漁協の照合・請求・支払の集計をコードが1件ずつ実処理し、その記録で漁協の画面（仲間の型・紺）と3者の流れの画面を動かす。

**Architecture:** 漁協の DB に集計の表を足し（移行 022）、`agentdock/settle.py` が `begin` と `step` で1件ずつ処理する。サーバーの裏の糸が2秒ごとに `step` を呼ぶ。`coop.py` は集計の表から仕事のカードを組み、`overview.py` は3者の状態と今の連絡を組む。画面は `web/coop.html`（作り直し）と `web/overview.html`（新規）。

**Tech Stack:** Python 3.12・SQLite（STRICT）・素の JS・pytest

## Global Constraints

- 実証用ルール：漁協の手数料 5%・手数料の消費税 10%・仲買への請求の消費税 8%・円未満は切り捨て
- 対象：確定したせりの結果と、市場の帳簿に登録した漁協の売りの相対の取引。仮押さえは入れない
- 同じ元の取引を同じ種類で2回数えない（`UNIQUE (kind, source_kind, source_id)`）
- 照合の不一致は mismatch として記録し、請求と支払を始めない
- 初回の対象時点の後に成立した取引は「追加集計待ち → 反映済み」。待ちが残る間は完了と出さない
- 画面の文「実証用データを実処理しています。進行を見せるため、処理間隔を2秒に調整しています。」
- 語は「仲買への請求」「荷主への支払」。「仕切り伝票」は使わない
- 社長に確認を送った段階は「社長の判断待ち」
- 画面に縞を使わない。円の金額は「円」付き・右揃え

---

### Task 1: 集計の表と種（移行 022・荷主・ルール）

**Files:**
- Create: `db/migrations/owner/022_coop_settlement.sql`
- Modify: `agentdock/seed/scenario.py`（`CONSIGNORS`・`LOT_CONSIGNOR`・`SETTLE_RULE`）
- Modify: `agentdock/seed/build.py`（漁協に荷主・ロットの荷主・ルールを入れる）
- Test: `tests/test_settle.py`

- [ ] 表の定義（spec §1）を書く → 種を入れる → 試験：漁協の DB に荷主 3・ルール 1・ロットの荷主 4 が入り、ほかの組織には入らない

### Task 2: settle.py（begin・step・計算・再開・追加の集計・照合の不一致）

**Files:**
- Create: `agentdock/settle.py`
- Modify: `agentdock/scene.py`（`start` の後に `settle.begin`）
- Test: `tests/test_settle.py`

**Interfaces:**
- Produces: `settle.begin(on_date, cutoff_at) -> int`（照合の run の番号）・`settle.step(now=None) -> list[dict]`（処理した行）・`settle.status(on_date) -> dict`（`jobs`: kind→dict(status,total,done,additional_waiting)・`complete`: bool）・`settle.compute(kind, gross_yen, rule) -> dict`

- [ ] 試験：手で計算した正解（18,200円 × 4本＝72,800円 → 請求の税 5,824円・請求額 78,624円／手数料 3,640円・その税 364円・支払額 68,796円）
- [ ] 試験：`step` を何度呼んでも行の数と合計が変わらない。新しい接続で再開しても同じ
- [ ] 試験：初回の後に登録した取引が追加集計待ち → 反映済み。待ちの間は `complete` が False
- [ ] 試験：受け取った内容を書き換えると mismatch になり、請求と支払が running にならない
- [ ] 試験：仮押さえは入らない

### Task 3: サーバーの裏の糸（2秒ごとの step）

**Files:**
- Modify: `agentdock/server.py`（`main` で糸を起動・`SETTLE_INTERVAL = 2.0`）

- [ ] 糸は `settle.step()` を2秒ごとに呼び、例外は記録して止まらない。試験は `step` を直接呼ぶ（糸は試験しない）

### Task 4: 漁協の画面（仲間の型・紺・仕事のカード）

**Files:**
- Modify: `agentdock/coop.py`（`view` に `jobs` のカード・見出し・表情を足す）
- Modify: `web/coop.html`（作り直し：顔・見出し・カード・これまでの記録）
- Test: `tests/test_coop.py`

- [ ] 試験：カードの件数と状態が表の行と一致する。照会への回答のカードは Claude の起動の記録から（途中は「対応中」）。追加集計待ちの間は「取引と追加集計」が待ちと出る

### Task 5: 3者の流れの画面

**Files:**
- Create: `agentdock/overview.py`・`web/overview.html`
- Modify: `agentdock/server.py`（`GET /api/overview`）
- Test: `tests/test_overview.py`

**Interfaces:**
- Produces: `overview.view(on_date) -> dict`（`lanes`: 網谷水産・社長・岩瀬漁協の `state`（working・waiting_decision・idle）と `doing`・`focus`: 今の連絡 1本 `{from, to, kind, text, at}`）

- [ ] 試験：社長に確認を送った後は社長の列が waiting_decision（working ではない）。照会を送った直後の focus は 網谷水産 → 岩瀬漁協

### Task 6: 録画の道具と、全件の試験とコミット

**Files:**
- Modify: `AgentDock-local/scratch/record_real.mjs`（照会の後に 3列 2秒 → 漁協の画面、社長の確認の時に 3列、登録の後に漁協の画面で追加集計）

- [ ] 模擬のサーバーで道具を通す → 全件の試験 → コミット
