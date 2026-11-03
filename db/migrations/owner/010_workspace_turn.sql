-- Adaptive Workspace の組み直し（2026-11-03 SAL の再設計の指示）
-- 1. 出来事の種類に「担当者の質問」（staff_question）を足す：担当者が Claude に聞くと、出来事として記録し Claude を起こす
-- 2. 画面の組み方（workspace_turn）：Claude が選んだ見出し・勧める案・比べる案・根拠のカード・操作の候補・担当者への問いかけ
--    文と選択は Claude、数字はコードが業務 DB から計算する。1行が1回の組み直し（質問への答えを含む）
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE biz_event_new (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('order_new', 'order_changed', 'quote_request', 'quote_received',
                                      'human_choice', 'trade_registered', 'handoff', 'info_received', 'staff_question')),
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'handled')),
  handled_at TEXT,
  run_id INTEGER
) STRICT;
INSERT INTO biz_event_new (id, at, kind, payload_json, status, handled_at, run_id)
  SELECT id, at, kind, payload_json, status, handled_at, run_id FROM biz_event;
DROP TABLE biz_event;
ALTER TABLE biz_event_new RENAME TO biz_event;

CREATE TABLE workspace_turn (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  version INTEGER NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('present', 'answer')),
  question_event_id INTEGER,
  question TEXT,
  answer TEXT,
  layout_json TEXT NOT NULL,
  run_id INTEGER NOT NULL,
  at TEXT NOT NULL
) STRICT;
CREATE INDEX workspace_turn_case ON workspace_turn (case_id, id);
COMMIT;
PRAGMA foreign_keys = ON;
