-- 漁協の集計案（2026-11-05 デイブ・SAL）：せりの後の照合・仲買への請求・荷主への支払を、コードが1件ずつ実処理して保存する。
-- 請求書の発行・支払の確定・送金は扱わない（集計案まで）。行は漁協の DB にだけ入る（ほかの組織の表は空のまま）。
-- 対象は確定したせりの結果と、市場の帳簿に登録した漁協の売りの相対の取引。仮押さえは入れない（agentdock/settle.py）。

-- 実証用ルール（実在の料率ではない）：手数料・手数料の消費税・請求の消費税は千分率、端数は円未満の切り捨て
CREATE TABLE settle_rule (
  id INTEGER PRIMARY KEY,
  fee_permille INTEGER NOT NULL CHECK (fee_permille >= 0),
  fee_tax_permille INTEGER NOT NULL CHECK (fee_tax_permille >= 0),
  sale_tax_permille INTEGER NOT NULL CHECK (sale_tax_permille >= 0),
  rounding TEXT NOT NULL CHECK (rounding IN ('floor')),
  note TEXT NOT NULL,
  valid_from TEXT NOT NULL
) STRICT;

-- 荷主（架空）と、ロットの荷主
CREATE TABLE consignor (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE lot_consignor (
  lot_no TEXT PRIMARY KEY,
  consignor_id INTEGER NOT NULL REFERENCES consignor(id)
) STRICT;

-- 集計の実行：照合（reconcile）・請求（invoice）・支払（payout）。cutoff_at は対象時点（初回）。additional=1 は対象時点の後の取引の追加の集計
CREATE TABLE settle_run (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN ('reconcile', 'invoice', 'payout')),
  trade_date TEXT NOT NULL,
  cutoff_at TEXT NOT NULL,
  additional INTEGER NOT NULL DEFAULT 0 CHECK (additional IN (0, 1)),
  status TEXT NOT NULL CHECK (status IN ('waiting', 'running', 'done', 'mismatch', 'stopped')),
  total_items INTEGER NOT NULL CHECK (total_items >= 0),
  done_items INTEGER NOT NULL DEFAULT 0 CHECK (done_items >= 0),
  rule_id INTEGER REFERENCES settle_rule(id),
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
) STRICT;

-- 集計の行：元の取引ごとに1行（同じ取引を同じ種類で2回数えない）。party は仲買の組織の番号（請求）か荷主の番号（支払）
CREATE TABLE settle_line (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES settle_run(id),
  kind TEXT NOT NULL CHECK (kind IN ('invoice', 'payout')),
  source_kind TEXT NOT NULL CHECK (source_kind IN ('auction_result', 'trade')),
  source_id TEXT NOT NULL,
  lot_no TEXT NOT NULL,
  item_code TEXT NOT NULL,
  party TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  gross_yen INTEGER NOT NULL,
  fee_yen INTEGER NOT NULL,
  tax_yen INTEGER NOT NULL,
  amount_yen INTEGER NOT NULL,
  rule_id INTEGER NOT NULL REFERENCES settle_rule(id),
  source_at TEXT,
  at TEXT NOT NULL,
  UNIQUE (kind, source_kind, source_id)
) STRICT;

-- 照合の行：せりの結果の1行ごと・組織ごとに、市場の記録の公開の部分の照合番号と、その組織が受け取った行の照合番号を比べる
CREATE TABLE settle_check (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES settle_run(id),
  who TEXT NOT NULL,
  result_id TEXT NOT NULL,
  expected TEXT NOT NULL,
  got TEXT,
  ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
  at TEXT NOT NULL,
  UNIQUE (run_id, who, result_id)
) STRICT;
