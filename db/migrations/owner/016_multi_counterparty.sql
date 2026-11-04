-- 2つ以上の相手の条件を1つの案にまとめる（2026-11-04 デイブ・案B）
-- 1. 案件の状態に partial（一部登録）を足す：登録できた行は登録のまま、登録できなかった行は理由を残し、残りは新しい相談で続ける
-- 2. 仕入れの行（proposal）に、元の条件の番号と、登録できなかった理由を持たせる。状態に failed（登録できなかった）を足す
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE deal_case_new (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES order_book(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  trade_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'presented', 'registered', 'incomplete', 'partial', 'rejected', 'held', 'stale', 'needs_president')),
  version INTEGER NOT NULL DEFAULT 0,
  last_run_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  task_id INTEGER REFERENCES task(id)
) STRICT;
INSERT INTO deal_case_new (id, order_id, item_id, trade_date, status, version, last_run_id, created_at, updated_at, task_id)
  SELECT id, order_id, item_id, trade_date, status, version, last_run_id, created_at, updated_at, task_id FROM deal_case;
DROP TABLE deal_case;
ALTER TABLE deal_case_new RENAME TO deal_case;
CREATE INDEX deal_case_order ON deal_case (order_id, id);

CREATE TABLE proposal_new (
  id INTEGER PRIMARY KEY,
  case_id INTEGER REFERENCES deal_case(id),
  option_id INTEGER REFERENCES board_option(id),
  side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
  item_id INTEGER NOT NULL REFERENCES item(id),
  counterparty TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price_yen INTEGER NOT NULL CHECK (unit_price_yen > 0),
  trade_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('approved', 'registered', 'incomplete', 'cancelled', 'failed')),
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL,
  term_id INTEGER,          -- 仕入れの行の元の条件（市場の offer_term）。1つの相手だけの案は NULL のまま（案の buy_term_id を使う）
  fail_reason TEXT          -- 登録できなかった理由（コードが書く）
) STRICT;
INSERT INTO proposal_new (id, case_id, option_id, side, item_id, counterparty, trade_date, quantity, unit_price_yen, trade_id, status, decided_by, decided_at)
  SELECT id, case_id, option_id, side, item_id, counterparty, trade_date, quantity, unit_price_yen, trade_id, status, decided_by, decided_at FROM proposal;
DROP TABLE proposal;
ALTER TABLE proposal_new RENAME TO proposal;
COMMIT;
PRAGMA foreign_keys = ON;
