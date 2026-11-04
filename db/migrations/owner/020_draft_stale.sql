-- 売り先への連絡の下書きが古くなる（2026-11-04 本物の Claude の通し・デイブ）：下書きは作った時の状態を前提に書かれている。
-- 案件が決まった（登録・一部登録・見送り・社長の差し戻し）後は、まだ決めていない下書きを stale にし、承認できないようにする（agentdock/deal.py）
-- 状態に stale を足し、古くなった理由（stale_reason）を残す
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE customer_draft_new (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  item_id INTEGER REFERENCES item(id),
  substitute_item_id INTEGER REFERENCES item(id),
  body TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'discarded', 'stale')),
  run_id INTEGER,
  created_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT,
  stale_reason TEXT
) STRICT;
INSERT INTO customer_draft_new (id, customer_id, item_id, substitute_item_id, body, reason, status, run_id, created_at, decided_by, decided_at)
  SELECT id, customer_id, item_id, substitute_item_id, body, reason, status, run_id, created_at, decided_by, decided_at FROM customer_draft;
DROP TABLE customer_draft;
ALTER TABLE customer_draft_new RENAME TO customer_draft;
COMMIT;
PRAGMA foreign_keys = ON;
