-- 人の決定：担当者・社長が相談に答えて確定した数量と配分（誰が・どの版で）。
-- AI の次の案（present_options）と承認（/api/decide）は、最新の決定と照合してコードが確かめる
CREATE TABLE human_decision (
  id INTEGER PRIMARY KEY,
  case_id INTEGER NOT NULL REFERENCES deal_case(id),
  consultation_id INTEGER NOT NULL UNIQUE REFERENCES consultation(id),
  version INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  allocations_json TEXT,
  priority_change INTEGER NOT NULL CHECK (priority_change IN (0, 1)),
  decided_by TEXT NOT NULL,
  decided_at TEXT NOT NULL
) STRICT;
CREATE INDEX human_decision_case ON human_decision (case_id, id);
