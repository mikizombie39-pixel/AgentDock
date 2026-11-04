-- 社長の差し戻し（2026-11-04 デイブ）：担当者が送った確認を、社長が担当者へ戻す。見送り（reject）とは別の操作
-- case_decision の操作に return を足す。仮押さえは残し、案件は担当者の判断待ち（presented）に戻る（agentdock/deal.py）
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE case_decision_new (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  option_id INTEGER REFERENCES board_option(id),
  action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'hold', 'return')),
  actor TEXT NOT NULL,
  at TEXT NOT NULL,
  result TEXT NOT NULL,
  detail TEXT,
  idempotency_key TEXT NOT NULL UNIQUE
) STRICT;
INSERT INTO case_decision_new (id, case_id, option_id, action, actor, at, result, detail, idempotency_key)
  SELECT id, case_id, option_id, action, actor, at, result, detail, idempotency_key FROM case_decision;
DROP TABLE case_decision;
ALTER TABLE case_decision_new RENAME TO case_decision;
COMMIT;
PRAGMA foreign_keys = ON;
