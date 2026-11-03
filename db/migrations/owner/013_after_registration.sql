-- 途中の注文と、登録の後も続く仕事（2026-11-03 デイブの本物の答えで分かったこと）
-- 1. 案件は注文ごとに1つではなくなる：料亭の分を登録した後も、スーパーの注文は新しい案件で続く（order_id の一意を外す）
-- 2. 相談は、案件が登録・見送りで閉じたら closed にする
-- 3. 人の決定は、相談の答えだけでなく、案の承認（だれが・どの版で・どの配分を）も記録する
-- 4. 売り先への連絡は AI が下書きするだけ。送るかは人が決める（下書きの表）
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE deal_case_new (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL REFERENCES order_book(id),
  item_id INTEGER NOT NULL REFERENCES item(id),
  trade_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'presented', 'registered', 'incomplete', 'rejected', 'held', 'stale', 'needs_president')),
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

CREATE TABLE consultation_new (
  id INTEGER PRIMARY KEY,
  group_id INTEGER REFERENCES consultation_group(id),
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'stale', 'needs_president', 'closed')),
  chosen_option_id INTEGER,
  reason_text TEXT,
  answered_at TEXT,
  idempotency_key TEXT UNIQUE,
  case_id INTEGER REFERENCES deal_case(id),
  change_json TEXT,
  answered_by TEXT,
  UNIQUE (task_id, task_version)
) STRICT;
INSERT INTO consultation_new SELECT id, group_id, task_id, task_version, question, status, chosen_option_id, reason_text, answered_at, idempotency_key,
  case_id, change_json, answered_by FROM consultation;
DROP TABLE consultation;
ALTER TABLE consultation_new RENAME TO consultation;

CREATE TABLE human_decision_new (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  source TEXT NOT NULL CHECK (source IN ('consultation', 'approval')),
  consultation_id INTEGER UNIQUE REFERENCES consultation(id),
  option_id INTEGER REFERENCES board_option(id),
  version INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  allocations_json TEXT,
  priority_change INTEGER NOT NULL CHECK (priority_change IN (0, 1)),
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
) STRICT;
INSERT INTO human_decision_new (id, case_id, source, consultation_id, option_id, version, quantity, allocations_json, priority_change, decided_by, decided_at)
  SELECT id, case_id, 'consultation', consultation_id, NULL, version, quantity, allocations_json, priority_change, decided_by, decided_at FROM human_decision;
DROP TABLE human_decision;
ALTER TABLE human_decision_new RENAME TO human_decision;
CREATE INDEX human_decision_case ON human_decision (case_id, id);

CREATE TABLE customer_draft (
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customer(id),
  item_id INTEGER REFERENCES item(id),
  substitute_item_id INTEGER REFERENCES item(id),
  body TEXT NOT NULL,
  reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('draft', 'approved', 'discarded')),
  run_id INTEGER,
  created_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT
) STRICT;
COMMIT;
PRAGMA foreign_keys = ON;
