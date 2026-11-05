-- 不足が残り、どこにも当たっていない時に、担当者が「もう一度当たってもらう」を頼んだ記録（2026-11-04 本物の Claude の通し・デイブ）
-- 見送りの後は AI が起きず、画面は「AI に任せてかまいません」と言ったまま行き止まりになっていた。
-- 頼んだ記録と、AI を起こす出来事（human_choice・source retry）は同じトランザクションで書く（agentdock/consult.py）
CREATE TABLE retry_request (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES item(id),
  trade_date TEXT NOT NULL,
  short_quantity INTEGER NOT NULL CHECK (short_quantity > 0),
  requested_by TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  event_id INTEGER REFERENCES biz_event(id),
  idempotency_key TEXT NOT NULL UNIQUE
) STRICT;
