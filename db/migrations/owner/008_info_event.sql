-- 出来事の種類に「情報が入って来た」（info_received：漁協の資料・他の仲買の掲示）を足す
-- SQLite の表の作り直しの手順（007 と同じ）：参照の検査を外して作り直し、戻す
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE biz_event_new (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('order_new', 'order_changed', 'quote_request', 'quote_received',
                                      'human_choice', 'trade_registered', 'handoff', 'info_received')),
  payload_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'handled')),
  handled_at TEXT,
  run_id INTEGER
) STRICT;
INSERT INTO biz_event_new (id, at, kind, payload_json, status, handled_at, run_id)
  SELECT id, at, kind, payload_json, status, handled_at, run_id FROM biz_event;
DROP TABLE biz_event;
ALTER TABLE biz_event_new RENAME TO biz_event;
COMMIT;
PRAGMA foreign_keys = ON;
