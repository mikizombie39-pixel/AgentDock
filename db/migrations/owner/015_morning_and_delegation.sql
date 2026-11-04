-- 若い担当者の朝（2026-11-04 デイブ・SAL）
-- 1. 売り先の店の名前（架空）：区分の名前（スーパー・料亭）は業務の判定に使い、画面と AI への文は店の名前で呼ぶ
-- 2. 社長の指示（業務の記録・出所＝社長・別の浜から）：AI はこれを根拠に相談する。命令の実行は権限の表とコードが決める
-- 3. 委任：担当者が今朝の注文と社長の指示を確かめ、AI に任せる範囲を記録する（照会・条件内の入札・再交渉）
ALTER TABLE customer ADD COLUMN shop_name TEXT;

CREATE TABLE directive (
  id INTEGER PRIMARY KEY,
  issued_by TEXT NOT NULL,                 -- 役割（president）
  issued_from TEXT NOT NULL,               -- どこから（別の浜）
  via TEXT NOT NULL,                       -- どの手段で（電話など）
  body TEXT NOT NULL,                      -- 指示の言葉そのもの
  kind TEXT NOT NULL CHECK (kind IN ('keep_customer')),   -- keep_customer：その売り先の注文を欠かさない
  customer_id INTEGER REFERENCES customer(id),
  item_id INTEGER REFERENCES item(id),     -- NULL は全品目
  trade_date TEXT NOT NULL,
  received_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('active', 'withdrawn'))
) STRICT;

CREATE TABLE delegation (
  id INTEGER PRIMARY KEY,
  trade_date TEXT NOT NULL,
  scope_json TEXT NOT NULL,                -- ["inquiry", "bid_within_plan", "renegotiate"]（記号はコードが決める）
  checked_json TEXT NOT NULL,              -- 担当者が確かめた注文と指示の番号 {"orders": [...], "directives": [...]}
  granted_by TEXT NOT NULL,                -- 役割（staff）
  granted_at TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE
) STRICT;
