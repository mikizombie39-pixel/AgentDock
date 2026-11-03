-- 担当者の答えで条件を変え、交渉し直す（docs/autonomy.md §11）
-- 1. 磁石ボードの版ごとに相談を1件開く（担当者が数量・配分を変える入口）。相談は案件に結びつき、変えた中身（change_json）と答えた人の役割を持つ
--    顧客の間の優先を変える答えは、担当者だけでは確定しない（needs_president で社長の答えを待つ）
-- 2. 選択肢の記号に「数量・配分を変える」（change）を足す
-- 3. 案件に仕事（task）を結びつける（相談の版の検査は仕事の版で行う）
-- 4. 権限の表に「顧客の間の優先を変えてよいか」を足す（金額と同じく、コードに書かず表に置く）
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE consultation_new (
  id INTEGER PRIMARY KEY,
  group_id INTEGER REFERENCES consultation_group(id),
  task_id INTEGER NOT NULL REFERENCES task(id),
  task_version INTEGER NOT NULL,
  question TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'answered', 'stale', 'needs_president')),
  chosen_option_id INTEGER,
  reason_text TEXT,
  answered_at TEXT,
  idempotency_key TEXT UNIQUE,
  case_id INTEGER REFERENCES deal_case(id),
  change_json TEXT,
  answered_by TEXT,
  UNIQUE (task_id, task_version)
) STRICT;
INSERT INTO consultation_new (id, group_id, task_id, task_version, question, status, chosen_option_id, reason_text, answered_at, idempotency_key)
  SELECT id, group_id, task_id, task_version, question, status, chosen_option_id, reason_text, answered_at, idempotency_key FROM consultation;
DROP TABLE consultation;
ALTER TABLE consultation_new RENAME TO consultation;

CREATE TABLE consultation_option_new (
  id INTEGER PRIMARY KEY,
  consultation_id INTEGER NOT NULL REFERENCES consultation(id),
  code TEXT NOT NULL CHECK (code IN (
    'prefer_restaurant', 'prefer_supermarket', 'split', 'pass', 'hold', 'buy_extra', 'order_only', 'change')),
  label TEXT NOT NULL,
  valid INTEGER NOT NULL CHECK (valid IN (0, 1)),
  UNIQUE (consultation_id, code)
) STRICT;
INSERT INTO consultation_option_new (id, consultation_id, code, label, valid)
  SELECT id, consultation_id, code, label, valid FROM consultation_option;
DROP TABLE consultation_option;
ALTER TABLE consultation_option_new RENAME TO consultation_option;

ALTER TABLE deal_case ADD COLUMN task_id INTEGER REFERENCES task(id);
ALTER TABLE authority ADD COLUMN may_change_priority INTEGER NOT NULL DEFAULT 0 CHECK (may_change_priority IN (0, 1));
COMMIT;
PRAGMA foreign_keys = ON;
